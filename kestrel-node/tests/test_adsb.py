"""ADS-B scanner: aircraft.json parsing and the publish loop. No hardware/net."""
import threading
from scanner.adsb import ADSBScanner


class FakePublisher:
    def __init__(self):
        self.calls = []

    def publish_aircraft(self, aircraft, gps):
        self.calls.append(aircraft)


class FakeGPS:
    lat = 41.44
    lon = -96.13
    alt = 350.0


SAMPLE = {
    "now": 1763800000.0,
    "aircraft": [
        {"hex": "a1b2c3", "flight": "UAL123 ", "lat": 41.5, "lon": -96.2,
         "alt_baro": 3500, "alt_geom": 3600, "gs": 220, "track": 90,
         "squawk": "1200", "baro_rate": -640, "seen": 1.2, "rssi": -12.3},
        {"hex": "ddeeff", "flight": "N45678", "lat": 41.45, "lon": -96.14,
         "alt_baro": "ground", "seen": 0.5},                       # on ground
        {"hex": "nopos0", "flight": "BLIND1", "alt_baro": 10000, "seen": 2.0},  # no position
        {"hex": "stale0", "lat": 42.0, "lon": -97.0, "alt_baro": 5000, "seen": 99.0},  # too old
    ],
}


def _scanner(pub=None):
    return ADSBScanner(pub or FakePublisher(), FakeGPS(), "KN-01", threading.Event())


def test_parse_keeps_only_positioned_fresh():
    out = _scanner()._collect(SAMPLE)
    assert {a["icao"] for a in out} == {"a1b2c3", "ddeeff"}   # no-position + stale dropped
    ua = next(a for a in out if a["icao"] == "a1b2c3")
    assert ua["callsign"] == "UAL123"                         # whitespace trimmed
    assert ua["alt_baro"] == 3500 and ua["alt_geom"] == 3600  # both altitudes carried
    assert ua["vert_rate"] == -640
    assert ua["on_ground"] is False


def test_on_ground_aircraft():
    g = next(a for a in _scanner()._collect(SAMPLE) if a["icao"] == "ddeeff")
    assert g["on_ground"] is True
    assert g["alt_baro"] is None                              # "ground" is not an altitude


def test_run_publishes_once_then_stops():
    pub = FakePublisher()
    ev = threading.Event()
    s = ADSBScanner(pub, FakeGPS(), "KN-01", ev, publish_interval_s=0.001)

    def fetch_once():
        ev.set()                                              # stop the loop after this cycle
        return SAMPLE
    s._fetch = fetch_once
    s.run()

    assert len(pub.calls) == 1
    assert {a["icao"] for a in pub.calls[0]} == {"a1b2c3", "ddeeff"}
