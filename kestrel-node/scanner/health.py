"""
scanner/health.py
=================
Live per-radio health, reported in every heartbeat.

Each scanner owns one RadioHealth and updates it as it works: running / down
(with the reason), a few details (interface, channel, serial, bands...) and
event counters (frames, adverts, captures, detections). The heartbeat sends a
compact snapshot of each, so the dashboard shows what is actually running
rather than what the config asked for.

Counters are bucketed (10 s buckets, 10 min window) so a radio that sees
thousands of frames a minute costs a few integers, not a list of timestamps.
Thread-safe: scanners write from their own threads, the heartbeat reads.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

BUCKET_S = 10
WINDOW_BUCKETS = 60          # 10 minutes

STARTING = "starting"
RUNNING  = "running"
DEGRADED = "degraded"        # running, but missing something (e.g. LR off, no GPS fix)
DOWN     = "down"
OFF      = "off"             # disabled in config


class _Counter:
    __slots__ = ("buckets",)

    def __init__(self):
        self.buckets: deque[list] = deque(maxlen=WINDOW_BUCKETS)   # [bucket_start, count]

    def add(self, n: int, now: float):
        start = int(now // BUCKET_S) * BUCKET_S
        if self.buckets and self.buckets[-1][0] == start:
            self.buckets[-1][1] += n
        else:
            self.buckets.append([start, n])

    def total(self, seconds: float, now: float) -> int:
        cutoff = now - seconds
        return sum(c for start, c in self.buckets if start + BUCKET_S > cutoff)


class RadioHealth:
    def __init__(self, radio_id: str, kind: str, label: str, **detail):
        self.id = radio_id
        self.kind = kind                 # wifi | bt | sdr | adsb | gps
        self.label = label
        self._lock = threading.Lock()
        self._state = STARTING
        self._error: Optional[str] = None
        self._since = time.time()
        self._detail: dict = dict(detail)
        self._counters: dict[str, _Counter] = {}

    # ── Writers (scanner threads) ──────────────────────────────────────────
    def set_state(self, state: str, error: Optional[str] = None):
        with self._lock:
            if state != self._state:
                self._since = time.time()
            self._state = state
            self._error = error if state in (DOWN, DEGRADED) else None

    def running(self):
        self.set_state(RUNNING)

    def degraded(self, reason: str):
        self.set_state(DEGRADED, reason)

    def down(self, reason: str):
        self.set_state(DOWN, reason)

    def set(self, **detail):
        with self._lock:
            self._detail.update(detail)

    def count(self, name: str, n: int = 1):
        now = time.time()
        with self._lock:
            c = self._counters.get(name)
            if c is None:
                c = self._counters[name] = _Counter()
            c.add(n, now)

    # ── Readers ────────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def per_min(self, name: str) -> int:
        now = time.time()
        with self._lock:
            c = self._counters.get(name)
            return c.total(60, now) if c else 0

    def last_10m(self, name: str) -> int:
        now = time.time()
        with self._lock:
            c = self._counters.get(name)
            return c.total(600, now) if c else 0

    def snapshot(self, rates: dict[str, str] | None = None) -> dict:
        """
        Compact dict for the heartbeat. `rates` maps output key -> counter
        name, with "_per_min" or "_10m" in the key choosing the window.
        Keys with None values are dropped to keep the payload small.
        """
        now = time.time()
        with self._lock:
            out = {
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "state": self._state,
                "since": int(self._since),
            }
            if self._error:
                out["error"] = self._error[:160]
            detail = {k: v for k, v in self._detail.items() if v is not None}
            for key, counter in (rates or {}).items():
                c = self._counters.get(counter)
                window = 600 if key.endswith("_10m") else 60
                detail[key] = c.total(window, now) if c else 0
            if detail:
                out["detail"] = detail
        return out


QUIET_GRACE_S = 90


def flag_quiet(snap: dict, rate_key: str, reason: str) -> dict:
    """
    Mark a "running" radio degraded when it has been up a while but its
    liveness counter is zero (an adapter that's open but deaf). Beacons and BLE
    adverts are everywhere, so zero for a full minute means something's wrong.
    """
    if (snap.get("state") == RUNNING
            and time.time() - snap.get("since", 0) >= QUIET_GRACE_S
            and snap.get("detail", {}).get(rate_key, 0) == 0):
        snap = dict(snap, state=DEGRADED, error=reason)
    return snap


def off(radio_id: str, kind: str, label: str) -> dict:
    """Snapshot for a radio that is disabled in config."""
    return {"id": radio_id, "kind": kind, "label": label, "state": OFF}


class StatusThrottle:
    """Decides when a heartbeat carries the full radio_status list."""

    def __init__(self, interval_s: float = 60):
        self.interval_s = interval_s
        self._last_sent = 0.0
        self._last_states: tuple = ()

    def due(self, status: list[dict], now: float | None = None) -> bool:
        now = time.time() if now is None else now
        states = tuple((r.get("id"), r.get("state")) for r in status)
        if states != self._last_states or now - self._last_sent >= self.interval_s:
            self._last_states, self._last_sent = states, now
            return True
        return False


def collect(sources: list, gps=None) -> list[dict]:
    """
    Build the heartbeat's radio_status list.

    `sources` holds (status_fn, thread) pairs for running scanners and plain
    dicts for radios that are off. A scanner whose thread has died without
    saying why is reported down rather than trusted.
    """
    out: list[dict] = []
    if gps is not None:
        try:
            out.append(gps.radio_status())
        except Exception as e:
            out.append({"id": "gps", "kind": "gps", "label": "GPS", "state": DOWN,
                        "error": f"status unavailable: {e}"})
    for src in sources:
        if isinstance(src, dict):
            out.append(src)
            continue
        status_fn, thread = src
        try:
            snap = status_fn()
        except Exception as e:
            snap = {"id": "unknown", "kind": "unknown", "label": "unknown",
                    "state": DOWN, "error": f"status unavailable: {e}"}
        if thread is not None and not thread.is_alive() and snap.get("state") not in (DOWN, OFF):
            snap = dict(snap, state=DOWN, error="scanner stopped unexpectedly")
        out.append(snap)
    return out
