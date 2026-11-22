"""
core/aircraft_store.py
======================
In-memory store of the current crewed-aircraft picture (ADS-B).

Aircraft update ~1 Hz per node and carry no history worth keeping, so they live
in memory with a short TTL rather than in TimescaleDB. Keyed by ICAO hex; the
most recent report for an aircraft wins, tagged with the node that heard it.

Single asyncio event loop (MQTT ingest + API read on the same loop), so no lock.
"""

import time


class AircraftStore:
    def __init__(self, ttl_s: float = 60.0):
        self.ttl_s = ttl_s
        self._ac: dict[str, dict] = {}   # icao -> aircraft dict + {node_id, updated}

    def update(self, node_id: str, aircraft: list[dict]) -> None:
        now = time.time()
        for ac in aircraft:
            icao = ac.get("icao")
            if not icao:
                continue
            self._ac[icao] = {**ac, "node_id": node_id, "updated": now}

    def current(self) -> list[dict]:
        """Live aircraft, expired entries dropped."""
        now = time.time()
        self._ac = {k: v for k, v in self._ac.items() if now - v["updated"] <= self.ttl_s}
        return list(self._ac.values())

    def clear(self) -> None:
        self._ac.clear()


# Process-wide singleton.
aircraft_store = AircraftStore()
