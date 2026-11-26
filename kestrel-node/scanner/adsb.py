"""
scanner/adsb.py
===============
ADS-B ingest for crewed-aircraft awareness.

dump1090-fa does the real 1090 MHz Mode S / ADS-B decoding on its own dedicated
RTL-SDR and writes the current picture to aircraft.json (~1 Hz). This scanner
just reads that file over HTTP and republishes a compact aircraft list to
kestrel/<node>/aircraft, so the server can show crewed traffic alongside drones.

Aircraft are informational, not threats: the server keeps them out of the drone
threat/alert pipeline. The one exception (handled server-side) is a low, nearby
aircraft, which raises a deconfliction advisory since it shares the drone
operating band under 400 ft AGL. That is why the payload carries both
barometric and geometric altitude.
"""

import json
import logging
import threading
import urllib.request
import time

from scanner.health import RadioHealth, flag_quiet as _flag_quiet

log = logging.getLogger("adsb")


class ADSBScanner:
    def __init__(
        self,
        publisher,
        gps,
        node_id: str,
        stop_event: threading.Event,
        source_url: str = "http://127.0.0.1:8080/data/aircraft.json",
        publish_interval_s: float = 1.0,
        max_age_s: float = 30.0,
        http_timeout_s: float = 2.0,
    ):
        self.publisher = publisher
        self.gps = gps
        self.node_id = node_id
        self.stop_event = stop_event
        self.source_url = source_url
        self.publish_interval_s = publish_interval_s
        self.max_age_s = max_age_s
        self.http_timeout_s = http_timeout_s
        self._warned = False
        self._stats_url = source_url.rsplit("/", 1)[0] + "/stats.json"
        self._last_msgs: int | None = None
        self._last_stats_poll = 0.0
        self.health = RadioHealth("adsb", "adsb", "ADS-B", source=source_url)

    # ── Parsing ────────────────────────────────────────────────────────────
    @staticmethod
    def _parse(ac: dict):
        """One dump1090 aircraft entry -> compact dict, or None if it has no
        usable lat/lon (Mode S contacts without a position are skipped)."""
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None:
            return None
        alt_baro = ac.get("alt_baro")
        on_ground = (alt_baro == "ground")
        if not isinstance(alt_baro, (int, float)):
            alt_baro = None
        callsign = (ac.get("flight") or "").strip() or None
        return {
            "icao":      ac.get("hex"),
            "callsign":  callsign,
            "lat":       lat,
            "lon":       lon,
            "alt_baro":  alt_baro,                                   # ft, pressure
            "alt_geom":  ac.get("alt_geom"),                         # ft, geometric (GNSS)
            "gs":        ac.get("gs"),                               # kt
            "track":     ac.get("track"),                            # deg
            "squawk":    ac.get("squawk"),
            "vert_rate": ac.get("baro_rate", ac.get("geom_rate")),  # ft/min
            "on_ground": on_ground,
            "seen":      ac.get("seen"),                             # s since last msg
            "rssi":      ac.get("rssi"),                             # dBFS
        }

    def _collect(self, data: dict) -> list:
        out = []
        for ac in data.get("aircraft", []):
            seen = ac.get("seen")
            if seen is not None and seen > self.max_age_s:
                continue
            parsed = self._parse(ac)
            if parsed is not None:
                out.append(parsed)
        return out

    # ── HTTP ───────────────────────────────────────────────────────────────
    def _fetch(self) -> dict:
        with urllib.request.urlopen(self.source_url, timeout=self.http_timeout_s) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    # ── Health ─────────────────────────────────────────────────────────────
    def _track(self, data: dict, n_positioned: int):
        """Update health from a successful aircraft.json poll."""
        self.health.running()
        self.health.set(aircraft=n_positioned)
        msgs = data.get("messages")
        if isinstance(msgs, int):
            if self._last_msgs is not None and msgs >= self._last_msgs:
                self.health.count("messages", msgs - self._last_msgs)
            self._last_msgs = msgs
        # Receiver signal/noise from stats.json, every 30 s (cheap, best-effort)
        now = time.time()
        if now - self._last_stats_poll >= 30:
            self._last_stats_poll = now
            try:
                with urllib.request.urlopen(self._stats_url, timeout=self.http_timeout_s) as r:
                    local = json.loads(r.read()).get("last1min", {}).get("local", {})
                self.health.set(signal_db=local.get("signal"), noise_db=local.get("noise"))
            except Exception:
                pass

    def status(self) -> dict:
        """Live health snapshot for the heartbeat."""
        snap = self.health.snapshot({"messages_per_min": "messages"})
        return _flag_quiet(snap, "messages_per_min",
                           "dump1090 decoding nothing (antenna / line of sight?)")

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def run(self):
        log.info(f"ADS-B scanner starting — source {self.source_url}")
        while not self.stop_event.is_set():
            try:
                data = self._fetch()
                aircraft = self._collect(data)
                self.publisher.publish_aircraft(aircraft, self.gps)
                self._track(data, len(aircraft))
                if self._warned:
                    log.info("ADS-B source reachable again.")
                    self._warned = False
            except Exception as e:
                self.health.down(f"dump1090 unreachable: {e}")
                self._last_msgs = None
                if not self._warned:
                    log.warning(
                        f"ADS-B source unreachable ({self.source_url}): {e} — "
                        f"is dump1090 running?"
                    )
                    self._warned = True
            self.stop_event.wait(self.publish_interval_s)
        log.info("ADS-B scanner stopped.")
