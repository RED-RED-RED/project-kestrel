"""
core/radio_watch.py
===================
Raises a "radio down" alert when one radio on a node that is otherwise online
(heartbeats still arriving) stays down: a Wi-Fi adapter dropping off USB, an
SDR that can't be opened, dump1090 stopped.

A radio has to be reported down for radio_down_after_s (default 120 s) before
it alerts, so a USB hiccup or a scanner restart doesn't page anyone. One alert
per outage; the next "running" report re-arms it. "degraded" (running but
missing something) and "off" (disabled in config) never alert.

In memory: after a server restart an ongoing outage is re-detected on the next
full status heartbeat and alerts once the grace period passes again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Outage:
    first_down: float
    alerted: bool = False


@dataclass
class RadioWatch:
    _outages: dict = field(default_factory=dict)     # (node_id, radio_id) -> _Outage

    def evaluate(self, node_id: str, radio_status: list[dict], after_s: float,
                 now: float | None = None) -> list[dict]:
        """Return the radio entries that just crossed the down threshold."""
        now = time.time() if now is None else now
        due = []
        seen = set()
        for r in radio_status or []:
            rid = r.get("id")
            if not rid:
                continue
            key = (node_id, rid)
            seen.add(key)
            if r.get("state") != "down":
                self._outages.pop(key, None)          # recovered or never down
                continue
            o = self._outages.setdefault(key, _Outage(first_down=now))
            if not o.alerted and now - o.first_down >= after_s:
                o.alerted = True
                due.append(r)
        # A radio that vanished from the list (config changed) is no longer tracked
        for key in [k for k in self._outages if k[0] == node_id and k not in seen]:
            del self._outages[key]
        return due

    def forget_node(self, node_id: str):
        """Node went offline: its node-offline alert covers everything."""
        for key in [k for k in self._outages if k[0] == node_id]:
            del self._outages[key]


radio_watch = RadioWatch()
