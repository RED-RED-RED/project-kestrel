"""
tests/test_radio_status.py
==========================
Per-radio status from node heartbeats: parsing, the radio-down alert, and the
GPS jamming fields it now carries.
"""
from unittest.mock import patch

import pytest

from core import runtime_settings as rt
from core.radio_watch import RadioWatch
from models.schemas import NodeHeartbeat


@pytest.fixture(autouse=True)
def _clean():
    rt._overrides.clear()
    yield
    rt._overrides.clear()


def _hb(radio_status=None, **kw):
    return NodeHeartbeat(node_id="KN-01", status="online", ts=1.0,
                         gps={"lat": 41.5, "lon": -96.1, "alt": 318.0, "fix": True, "sats": 12},
                         system={}, radios=["wifi"], radio_status=radio_status, **kw)


def _gps(jam="ok", spoof="ok", survey=True):
    return {"id": "gps", "kind": "gps", "label": "GPS", "state": "running",
            "detail": {"jamming": jam, "spoofing": spoof, "survey": survey}}


def _radio(state, rid="wifi_5g", label="Wi-Fi 5 GHz", error=None):
    r = {"id": rid, "kind": "wifi", "label": label, "state": state}
    if error:
        r["error"] = error
    return r


# ── Schema ──────────────────────────────────────────────────────────────────

def test_old_heartbeat_still_parses():
    hb = _hb()
    assert hb.radio_status is None and hb.jamming_state is None


def test_gps_entry_fills_jamming_spoofing_survey():
    hb = _hb([_gps(jam="critical", spoof="spoofing", survey=True)])
    assert hb.jamming_state == "critical"
    assert hb.spoofing_state == "spoofing"
    assert hb.gps["survey_complete"] is True


# ── RadioWatch ──────────────────────────────────────────────────────────────

def test_down_must_persist_before_alerting():
    w = RadioWatch()
    down = [_radio("down", error="interface wlan2 not found")]
    assert w.evaluate("KN-01", down, 120, now=0) == []
    assert w.evaluate("KN-01", down, 120, now=60) == []
    due = w.evaluate("KN-01", down, 120, now=125)
    assert [r["id"] for r in due] == ["wifi_5g"]
    assert w.evaluate("KN-01", down, 120, now=300) == []        # one alert per outage


def test_blip_that_recovers_never_alerts_and_rearms():
    w = RadioWatch()
    w.evaluate("KN-01", [_radio("down")], 120, now=0)
    w.evaluate("KN-01", [_radio("running")], 120, now=30)       # recovered
    assert w.evaluate("KN-01", [_radio("down")], 120, now=140) == []   # clock restarted
    assert w.evaluate("KN-01", [_radio("down")], 120, now=265)  # new outage alerts


def test_degraded_and_off_never_alert():
    w = RadioWatch()
    for t in (0, 200, 400):
        assert w.evaluate("KN-01", [_radio("degraded"), _radio("off", rid="adsb")], 120, now=t) == []


def test_forget_node_and_per_node_isolation():
    w = RadioWatch()
    w.evaluate("KN-01", [_radio("down")], 120, now=0)
    w.evaluate("KN-02", [_radio("down")], 120, now=0)
    w.forget_node("KN-01")
    assert w.evaluate("KN-01", [_radio("down")], 120, now=130) == []   # restarted clock
    assert w.evaluate("KN-02", [_radio("down")], 120, now=130)         # unaffected


# ── Alert engine ────────────────────────────────────────────────────────────

def _engine():
    from mqtt.alert_engine import AlertEngine
    from core.config import Settings
    eng = AlertEngine(Settings(db_host="x", db_port=5432, db_name="t", db_user="t",
                               db_password="t", mqtt_host="x", mqtt_port=1883,
                               mqtt_user="t", mqtt_password="t"))
    seen = []
    async def fake_emit(a):
        seen.append(a)
    eng._emit = fake_emit
    return eng, seen


@pytest.mark.asyncio
async def test_radio_down_alert_and_toggle():
    eng, seen = _engine()
    r = _radio("down", rid="sdr_KESTREL", label="RTL-SDR KESTREL",
               error="No RTL-SDR with serial 'KESTREL'")
    await eng.radio_down("KN-01", r)
    assert seen[0]["category"] == "radio_down"
    assert seen[0]["title"] == "RTL-SDR KESTREL down on KN-01"
    assert "No RTL-SDR with serial" in seen[0]["description"]
    assert seen[0]["ref_id"] == "radio:KN-01:sdr_KESTREL"
    rt._overrides["alert_radio_down_enabled"] = False
    await eng.radio_down("KN-01", r)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_jamming_alert_only_on_real_indication():
    eng, seen = _engine()
    for state in (None, "ok", "unknown"):
        await eng.evaluate_heartbeat(_hb([_gps(jam=state)]))
    assert seen == []
    await eng.evaluate_heartbeat(_hb([_gps(jam="critical")]))
    assert seen and seen[0]["category"] == "gps_jamming"


def test_radio_down_settings():
    assert rt.get("radio_down_after_s") == 120
    assert rt._coerce("radio_down_after_s", "10") == 60          # floor
    assert rt.get("alert_radio_down_enabled") is True
