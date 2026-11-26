"""
tests/test_health.py
====================
Per-radio live health (scanner/health.py) and each scanner's status().
"""
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from scanner import health as h
from scanner.health import RadioHealth, flag_quiet, collect


# ── RadioHealth ─────────────────────────────────────────────────────────────

def test_counter_windows():
    r = RadioHealth("wifi_2g", "wifi", "Wi-Fi 2.4 GHz")
    t0 = 1_000_000.0
    with patch("scanner.health.time.time", return_value=t0 - 300):
        r.count("frames", 50)            # 5 min ago: in 10 min window only
    with patch("scanner.health.time.time", return_value=t0 - 5):
        r.count("frames", 7)             # recent
    with patch("scanner.health.time.time", return_value=t0):
        assert r.per_min("frames") == 7
        assert r.last_10m("frames") == 57
        assert r.per_min("missing") == 0


def test_snapshot_shape_and_none_dropped():
    r = RadioHealth("sdr_KESTREL", "sdr", "RTL-SDR KESTREL", serial="KESTREL", device_index=None)
    r.running()
    r.count("captures", 3)
    snap = r.snapshot({"captures_per_min": "captures", "links_10m": "links"})
    assert snap["state"] == "running" and "error" not in snap
    assert snap["detail"] == {"serial": "KESTREL", "captures_per_min": 3, "links_10m": 0}


def test_down_carries_reason_and_recovery_clears_it():
    r = RadioHealth("bt", "bt", "Bluetooth")
    r.down("hci1 not available")
    assert r.snapshot()["error"] == "hci1 not available"
    r.running()
    assert "error" not in r.snapshot()


def test_flag_quiet():
    base = {"state": "running", "detail": {"frames_per_min": 0}}
    fresh = dict(base, since=int(time.time()))
    old   = dict(base, since=int(time.time()) - 600)
    assert flag_quiet(fresh, "frames_per_min", "deaf")["state"] == "running"   # grace period
    q = flag_quiet(old, "frames_per_min", "deaf")
    assert q["state"] == "degraded" and q["error"] == "deaf"
    busy = dict(old, detail={"frames_per_min": 40})
    assert flag_quiet(busy, "frames_per_min", "deaf")["state"] == "running"


def test_collect_off_dead_thread_and_errors():
    dead = threading.Thread(target=lambda: None); dead.start(); dead.join()
    alive = MagicMock(); alive.is_alive.return_value = True
    ok = lambda: {"id": "a", "state": "running"}
    def boom():
        raise RuntimeError("x")
    gps = MagicMock(); gps.radio_status.return_value = {"id": "gps", "state": "running"}
    out = collect([(ok, alive), (ok, dead), h.off("adsb", "adsb", "ADS-B"), (boom, alive)], gps)
    assert [o["state"] for o in out] == ["running", "running", "down", "off", "down"]
    assert out[2]["error"] == "scanner stopped unexpectedly"


# ── Wi-Fi ───────────────────────────────────────────────────────────────────

def _wifi(band="2.4", iface="wlan1"):
    from scanner.wifi_nan import WiFiNANScanner
    return WiFiNANScanner(iface=iface, publisher=MagicMock(), gps=MagicMock(),
                          node_id="KN-01", stop_event=threading.Event(), band=band)


def test_wifi_missing_interface_is_down():
    s = _wifi(band="5", iface="wlan9")
    with patch("scanner.wifi_nan.subprocess.run", return_value=MagicMock(returncode=1)):
        s.run()
    st = s.status()
    assert st["id"] == "wifi_5g" and st["state"] == "down"
    assert "wlan9 not found" in st["error"]


def test_wifi_counts_frames():
    s = _wifi()
    s.health.running()
    for _ in range(4):
        s._handle_packet(MagicMock())
    st = s.status()
    assert st["detail"]["frames_per_min"] == 4
    assert st["detail"]["iface"] == "wlan1" and st["detail"]["channels"] == [1, 6, 11]


# ── Bluetooth ───────────────────────────────────────────────────────────────

def test_bt_status_basic():
    from scanner.bluetooth import BluetoothScanner
    b = BluetoothScanner(hci_index=1, publisher=MagicMock(), gps=MagicMock(),
                         node_id="KN-01", stop_event=threading.Event())
    b.health.set(backend="hci", long_range=True)
    b.health.running()
    b.health.count("adverts", 12)
    st = b.status()
    assert st["kind"] == "bt" and st["state"] == "running"
    assert st["detail"]["hci"] == "hci1" and st["detail"]["long_range"] is True
    assert st["detail"]["adverts_per_min"] == 12


# ── SDR ─────────────────────────────────────────────────────────────────────

def test_sdr_open_failure_is_down_with_reason():
    from scanner.sdr import SDRScanner
    from scanner.sdr_source import SDRSourceError
    src = MagicMock(); src.open.side_effect = SDRSourceError("No RTL-SDR with serial 'KESTREL'")
    s = SDRScanner(device_index=0, publisher=MagicMock(), gps=MagicMock(), node_id="KN-01",
                   stop_event=threading.Event(), source=src, serial="KESTREL")
    s.run()
    st = s.status()
    assert st["id"] == "sdr_KESTREL" and st["state"] == "down"
    assert "KESTREL" in st["error"]


def test_sdr_running_reports_bands_and_captures():
    from test_sdr_scanner import make_scanner, scene_generator
    s, pub, stop, src = make_scanner(scene_generator, bands=["900"])
    threading.Timer(0.5, stop.set).start()
    s.run()
    st = s.status()
    assert st["detail"]["bands"] == ["900"]
    assert st["detail"]["captures_per_min"] > 0


# ── ADS-B ───────────────────────────────────────────────────────────────────

def _adsb():
    from scanner.adsb import ADSBScanner
    return ADSBScanner(publisher=MagicMock(), gps=MagicMock(), node_id="KN-01",
                       stop_event=threading.Event())


def test_adsb_message_rate_from_counter_delta():
    a = _adsb()
    a._last_stats_poll = time.time()          # skip the stats.json fetch
    a._track({"messages": 1000}, 2)
    a._track({"messages": 1250}, 3)
    st = a.status()
    assert st["state"] == "running"
    assert st["detail"]["messages_per_min"] == 250
    assert st["detail"]["aircraft"] == 3


def test_adsb_unreachable_is_down():
    a = _adsb()
    a._fetch = MagicMock(side_effect=OSError("HTTP Error 404"))
    a.stop_event.wait = lambda t: a.stop_event.set()
    a.run()
    st = a.status()
    assert st["state"] == "down" and "404" in st["error"]


# ── GPS ─────────────────────────────────────────────────────────────────────

def _gps(tmp_path):
    from publisher.gps import GPSDaemon
    return GPSDaemon(port="/dev/null", survey_state_path=tmp_path / "s.json")


def test_gps_states(tmp_path):
    g = _gps(tmp_path)
    assert g.radio_status()["state"] == "down"                    # port never opened
    g._port_open = True
    assert g.radio_status()["error"] == "no fix"
    g._has_fix, g._satellites = True, 14
    st = g.radio_status()
    assert st["state"] == "running" and st["detail"]["sats"] == 14
    g._is_ublox, g._jamming_state = True, "critical"
    st = g.radio_status()
    assert st["state"] == "degraded" and "jamming critical" in st["error"]


# ── Heartbeat payload ───────────────────────────────────────────────────────

def _publisher():
    from test_mqtt_fallback import _make_publisher
    return _make_publisher()


def _realistic_status():
    now = int(time.time())
    return [
        {"id": "gps", "kind": "gps", "label": "GPS", "state": "running",
         "detail": {"port": "/dev/ttyGPS", "sats": 14, "fix": True, "ublox": True,
                    "jamming": "ok", "spoofing": "ok", "survey": True}},
        {"id": "wifi_2g", "kind": "wifi", "label": "Wi-Fi 2.4 GHz", "state": "running", "since": now,
         "detail": {"iface": "wlan1", "channels": [1, 6, 11], "frames_per_min": 2400,
                    "rid_10m": 3, "hop_errors_per_min": 0}},
        {"id": "wifi_5g", "kind": "wifi", "label": "Wi-Fi 5 GHz", "state": "running", "since": now,
         "detail": {"iface": "wlan2", "channels": [36, 40, 44, 48, 149, 153, 157, 161],
                    "frames_per_min": 310, "rid_10m": 0, "hop_errors_per_min": 0}},
        {"id": "bt", "kind": "bt", "label": "Bluetooth", "state": "running", "since": now,
         "detail": {"hci": "hci1", "long_range": True, "backend": "hci",
                    "adverts_per_min": 5400, "rid_10m": 2}},
        {"id": "sdr_KESTREL", "kind": "sdr", "label": "RTL-SDR KESTREL", "state": "running",
         "since": now, "detail": {"backend": "rtlsdr", "serial": "KESTREL",
                                  "bands": ["1g2", "433", "900"], "captures_per_min": 1400,
                                  "links_10m": 1}},
        {"id": "adsb", "kind": "adsb", "label": "ADS-B", "state": "running", "since": now,
         "detail": {"source": "http://127.0.0.1:8080/data/aircraft.json", "aircraft": 7,
                    "messages_per_min": 5200, "signal_db": -12.4, "noise_db": -35.1}},
    ]


def test_heartbeat_carries_radio_status_and_stays_small():
    pub = _publisher()
    sent = {}
    pub._enqueue = lambda topic, payload, qos=0: sent.update(topic=topic, payload=payload)
    gps = MagicMock(lat=41.54, lon=-96.13, alt=317.9, has_fix=True, satellites=14)
    with patch("psutil.cpu_percent", return_value=12.0):
        pub.send_heartbeat(gps, radios=["wifi", "bt", "sdr", "adsb"],
                           radio_status=_realistic_status())
    p = sent["payload"]
    assert p["radios"] == ["wifi", "bt", "sdr", "adsb"]        # kept for older servers
    assert [r["id"] for r in p["radio_status"]] == ["gps", "wifi_2g", "wifi_5g", "bt", "sdr_KESTREL", "adsb"]
    size = len(json.dumps(p, separators=(",", ":")))
    assert size < 2048, size


def test_heartbeat_without_status_unchanged():
    pub = _publisher()
    sent = {}
    pub._enqueue = lambda topic, payload, qos=0: sent.update(payload=payload)
    gps = MagicMock(lat=0, lon=0, alt=0, has_fix=False, satellites=0)
    pub.send_heartbeat(gps, radios=["wifi"])
    assert "radio_status" not in sent["payload"]


# ── Throttle: full status once a minute, or on any state change ─────────────

def test_status_throttle():
    from scanner.health import StatusThrottle
    t = StatusThrottle(60)
    a = [{"id": "wifi_5g", "state": "running"}]
    b = [{"id": "wifi_5g", "state": "down"}]
    assert t.due(a, now=0)           # first heartbeat
    assert not t.due(a, now=10)      # unchanged, within a minute
    assert t.due(b, now=20)          # state change goes out immediately
    assert not t.due(b, now=30)
    assert t.due(b, now=80)          # minute elapsed


def test_config_default_interval():
    from config.settings import NodeConfig
    assert NodeConfig().radio_status_interval_s == 60
