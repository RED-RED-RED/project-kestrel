"""
tests/test_agl.py
=================
Height-above-ground resolution (analysis/agl.py) and every consumer that must
use it instead of MSL/barometric altitude.
"""
import pytest
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from analysis.agl import resolve_drone_agl, resolve_aircraft_agl_ft, FT_PER_M
from analysis.flights import detail
from analysis.threat_scoring import ThreatScorer
from core import runtime_settings as rt
from core.config import Settings
from models.schemas import DetectionEvent, DroneData, NodePosition

GROUND_M = 340.0   # roughly Blair, NE (HAE)


# ── resolver ────────────────────────────────────────────────────────────────

def test_drone_prefers_rid_height():
    assert resolve_drone_agl(55.0, GROUND_M + 200, GROUND_M) == (55.0, "rid")


def test_drone_estimates_from_geodetic_when_height_missing():
    # ODID "unknown" height decodes to 0.0 on the node
    assert resolve_drone_agl(0.0, GROUND_M + 80, GROUND_M) == (80.0, "est")
    assert resolve_drone_agl(None, GROUND_M + 80, GROUND_M) == (80.0, "est")


def test_drone_estimate_clamps_below_ground_to_zero():
    assert resolve_drone_agl(0.0, GROUND_M - 15, GROUND_M) == (0.0, "est")


def test_drone_nothing_usable():
    assert resolve_drone_agl(0.0, 0.0, GROUND_M) == (None, None)
    assert resolve_drone_agl(0.0, GROUND_M + 50, 0.0) == (None, None)   # node has no fix


def test_aircraft_prefers_geometric():
    ground_ft = GROUND_M * FT_PER_M
    agl, src = resolve_aircraft_agl_ft(ground_ft + 300, ground_ft + 9000, GROUND_M)
    assert src == "geo" and agl == 300


def test_aircraft_falls_back_to_baro():
    ground_ft = GROUND_M * FT_PER_M
    agl, src = resolve_aircraft_agl_ft(None, ground_ft + 1200, GROUND_M)
    assert src == "baro" and agl == 1200


def test_aircraft_no_altitude():
    assert resolve_aircraft_agl_ft(None, None, GROUND_M) == (None, None)


# ── alert engine: no more barometric (MSL) fallback ─────────────────────────

def _engine():
    from mqtt.alert_engine import AlertEngine
    eng = AlertEngine(Settings(
        db_host="localhost", db_port=5432, db_name="t", db_user="t", db_password="t",
        mqtt_host="localhost", mqtt_port=1883, mqtt_user="t", mqtt_password="t",
    ))
    emitted = []
    async def fake_emit(a):
        emitted.append(a)
    eng._emit = fake_emit
    return eng, emitted


def _event(height_agl, alt_baro, alt_geo, operator_id=None):
    return DetectionEvent(
        node_id="KN-01", transport="wifi_nan", rssi=-70,
        node_position=NodePosition(lat=41.54, lon=-96.13, alt=GROUND_M),
        drone=DroneData(id="DRN1", lat=41.545, lon=-96.13, status="ground",
                        alt_baro=alt_baro, alt_geo=alt_geo, height_agl=height_agl,
                        speed_h=0.0, operator_id=operator_id),
        ts=1.0,
    )


async def _evaluate(eng, event):
    from tests.test_alert_engine import build_mock_pool
    pool, _ = build_mock_pool()
    async def _get_pool():
        return pool
    with patch("mqtt.alert_engine.get_pool", new=_get_pool):
        await eng.evaluate(event)


@pytest.mark.asyncio
async def test_grounded_drone_with_only_baro_does_not_trip_400ft():
    # Sitting on the ground: no height field, baro ~ field elevation (MSL).
    # The old rule used alt_baro as AGL and would fire "Above 400ft AGL".
    eng, emitted = _engine()
    await _evaluate(eng, _event(height_agl=0.0, alt_baro=GROUND_M, alt_geo=0.0))
    assert not [a for a in emitted if a["category"] == "altitude"]


@pytest.mark.asyncio
async def test_high_drone_by_geodetic_estimate_trips_400ft():
    eng, emitted = _engine()
    await _evaluate(eng, _event(height_agl=0.0, alt_baro=None, alt_geo=GROUND_M + 150))
    assert [a for a in emitted if a["category"] == "altitude"]


# ── threat scoring ──────────────────────────────────────────────────────────

def test_threat_scoring_ignores_baro_msl():
    scorer = ThreatScorer()
    d = {"drone_id": "X", "operator_id": "", "lat": 41.5, "lon": -96.1,
         "agl_m": None, "height_agl": 0.0, "alt_baro": GROUND_M,
         "ua_type": "helicopter_or_mr", "detecting_nodes": ["KN-01"], "description": ""}
    assert scorer.score(d, None).high_altitude_no_rid == 0.0


# ── flight history: rows from before agl_m existed ──────────────────────────

def test_flight_path_resolves_agl_for_old_rows():
    from datetime import datetime, timezone
    rows = [{"t": datetime(2026, 11, 1, tzinfo=timezone.utc), "lat": 41.5, "lon": -96.1,
             "node_id": "KN-01", "alt_geo": GROUND_M + 60, "alt_baro": None,
             "height_agl": 0.0, "node_alt": GROUND_M, "speed_h": 4.0, "heading": 90.0,
             "rssi": -60, "drone_id": "D"}]
    assert detail(rows)["path"][0]["agl_m"] == 60.0


# ── live-editable low-aircraft thresholds ───────────────────────────────────

@pytest.fixture
def _clean_overrides():
    rt._overrides.clear()
    yield
    rt._overrides.clear()


def test_threshold_keys_are_editable_and_clamped(_clean_overrides):
    for k in ("alert_low_aircraft_agl_ft", "alert_low_aircraft_radius_km",
              "alert_low_aircraft_cooldown_s"):
        assert k in rt.EDITABLE
    assert rt._coerce("alert_low_aircraft_radius_km", "-3") == 0.5
    assert rt._coerce("alert_low_aircraft_agl_ft", "99999") == 5000
    ro = {e["key"] for e in rt.snapshot()["readonly"]}
    assert "alert_low_aircraft_agl_ft" not in ro


@pytest.mark.asyncio
async def test_aircraft_alert_uses_live_override(_clean_overrides):
    eng, emitted = _engine()
    ac = {"icao": "abc123", "callsign": "N1", "lat": 41.545, "lon": -96.13,
          "on_ground": False, "alt_geom": GROUND_M * FT_PER_M + 600, "alt_baro": None}
    await eng.evaluate_aircraft("KN-01", 41.54, -96.13, GROUND_M, [ac])
    assert emitted == []                      # 600 ft AGL, default threshold 400
    rt._overrides["alert_low_aircraft_agl_ft"] = 1000.0
    await eng.evaluate_aircraft("KN-01", 41.54, -96.13, GROUND_M, [ac])
    assert len(emitted) == 1                  # GUI raised it to 1000, now alerts


# ── Remote ID height reference (takeoff vs ground) ──────────────────────────

def test_height_type_labels_source():
    assert resolve_drone_agl(40.0, GROUND_M + 90, GROUND_M, "ground") == (40.0, "rid")
    assert resolve_drone_agl(40.0, GROUND_M + 90, GROUND_M, "takeoff") == (40.0, "takeoff")
    assert resolve_drone_agl(40.0, GROUND_M + 90, GROUND_M, None) == (40.0, "rid")   # older node


def test_takeoff_height_still_beats_node_estimate():
    # Measured on board, so preferred over a terrain-assuming estimate.
    agl, src = resolve_drone_agl(40.0, GROUND_M + 90, GROUND_M, "takeoff")
    assert (agl, src) == (40.0, "takeoff")


def test_height_type_accepted_on_detection_payload():
    ev = _event(height_agl=30.0, alt_baro=None, alt_geo=None)
    payload = ev.model_dump()
    payload["drone"]["height_type"] = "takeoff"
    assert DetectionEvent(**payload).drone.height_type == "takeoff"
