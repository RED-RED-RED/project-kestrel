"""
tests/test_low_aircraft.py
==========================
Low-aircraft deconfliction advisory (ADS-B) and the in-memory aircraft store.
_emit is mocked so no DB/WebSocket is needed.
"""
import time
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import Settings
from core.aircraft_store import AircraftStore

# node sits at ~350 m MSL ( ~1148 ft ) at (41.44, -96.13)
NODE = dict(node_id="KN-01", lat=41.44, lon=-96.13, alt_m=350.0)
NODE_FT = 350.0 * 3.28084


def _settings():
    return Settings(
        db_host="localhost", db_port=5432, db_name="t", db_user="t", db_password="t",
        mqtt_host="localhost", mqtt_port=1883, mqtt_user="t", mqtt_password="t",
        alert_low_aircraft_agl_ft=400.0,
        alert_low_aircraft_radius_km=8.0,
        alert_low_aircraft_cooldown_s=120.0,
    )


def _engine():
    from mqtt.alert_engine import AlertEngine
    eng = AlertEngine(_settings())
    emitted = []
    async def fake_emit(a):
        emitted.append(a)
    eng._emit = fake_emit
    return eng, emitted


def _ac(**kw):
    base = dict(icao="a1b2c3", callsign="N123", lat=41.45, lon=-96.14,
                on_ground=False, alt_geom=None, alt_baro=None)
    base.update(kw)
    return base


async def _run(eng, aircraft):
    await eng.evaluate_aircraft(NODE["node_id"], NODE["lat"], NODE["lon"],
                               NODE["alt_m"], aircraft)


@pytest.mark.asyncio
async def test_low_near_aircraft_alerts():
    eng, emitted = _engine()
    await _run(eng, [_ac(alt_geom=NODE_FT + 200)])      # ~200 ft AGL, ~1.4 km away
    assert len(emitted) == 1
    a = emitted[0]
    assert a["category"] == "low_aircraft"
    assert a["level"] == "medium"
    assert a["ref_id"] == "aircraft:a1b2c3"
    assert "AGL" in a["description"]


@pytest.mark.asyncio
async def test_high_aircraft_no_alert():
    eng, emitted = _engine()
    await _run(eng, [_ac(alt_geom=NODE_FT + 3000)])     # well above the band
    assert emitted == []


@pytest.mark.asyncio
async def test_far_low_aircraft_no_alert():
    eng, emitted = _engine()
    await _run(eng, [_ac(alt_geom=NODE_FT + 100, lat=42.5, lon=-97.5)])  # low but ~170 km
    assert emitted == []


@pytest.mark.asyncio
async def test_on_ground_no_alert():
    eng, emitted = _engine()
    await _run(eng, [_ac(alt_geom=NODE_FT + 50, on_ground=True)])
    assert emitted == []


@pytest.mark.asyncio
async def test_prefers_geometric_falls_back_to_baro():
    eng, emitted = _engine()
    await _run(eng, [_ac(alt_baro=NODE_FT + 150)])      # only baro present, still low
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeat():
    eng, emitted = _engine()
    ac = _ac(alt_geom=NODE_FT + 150)
    await _run(eng, [ac])
    await _run(eng, [ac])                               # within cooldown
    assert len(emitted) == 1


def test_aircraft_store_update_and_ttl():
    s = AircraftStore(ttl_s=0.05)
    s.update("KN-01", [{"icao": "x", "lat": 1.0, "lon": 2.0},
                       {"icao": "y", "lat": 3.0, "lon": 4.0},
                       {"lat": 5.0, "lon": 6.0}])        # no icao -> skipped
    cur = s.current()
    assert {a["icao"] for a in cur} == {"x", "y"}
    assert all(a["node_id"] == "KN-01" for a in cur)
    time.sleep(0.07)
    assert s.current() == []                             # expired
