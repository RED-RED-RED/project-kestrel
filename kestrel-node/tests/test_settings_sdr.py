"""resolve_sdrs(): the new multi-SDR list plus legacy single-block fallback."""
from config.settings import NodeConfig, resolve_sdrs


def test_multi_sdr_list():
    cfg = NodeConfig()
    cfg.sdrs = [
        {"enabled": True,  "backend": "rtlsdr", "device_index": 0, "bands": ["900", "1g2", "433"]},
        {"enabled": True,  "backend": "hackrf", "device_index": 0, "bands": ["2g4", "5g8"]},
        {"enabled": False, "backend": "hackrf", "device_index": 1},
    ]
    radios = resolve_sdrs(cfg)
    assert len(radios) == 2                       # the disabled entry is dropped
    assert radios[0]["backend"] == "rtlsdr"
    assert radios[0]["bands"] == ["900", "1g2", "433"]
    assert radios[1]["backend"] == "hackrf"
    assert radios[1]["bands"] == ["2g4", "5g8"]
    assert radios[1]["device_index"] == 0


def test_legacy_single_block_fallback():
    cfg = NodeConfig()
    cfg.sdrs = []
    cfg.sdr_enabled = True
    cfg.sdr_backend = "rtlsdr"
    cfg.sdr_device_index = 0
    radios = resolve_sdrs(cfg)
    assert len(radios) == 1
    assert radios[0]["backend"] == "rtlsdr"
    assert radios[0]["bands"] is None             # auto = source's reachable range


def test_disabled_returns_empty():
    cfg = NodeConfig()
    cfg.sdrs = []
    cfg.sdr_enabled = False
    assert resolve_sdrs(cfg) == []


def test_list_wins_over_legacy_block():
    cfg = NodeConfig()
    cfg.sdr_enabled = True                         # legacy block on
    cfg.sdrs = [{"enabled": True, "backend": "hackrf", "device_index": 0}]
    radios = resolve_sdrs(cfg)
    assert len(radios) == 1
    assert radios[0]["backend"] == "hackrf"        # the list takes precedence


# ── RTL-SDR selection by serial (stable across reboots) ─────────────────────

def test_serial_passes_through_and_defaults_to_none():
    cfg = NodeConfig()
    cfg.sdrs = [
        {"backend": "rtlsdr", "device_index": 0, "serial": "KESTREL", "bands": ["900"]},
        {"backend": "rtlsdr", "device_index": 1},
    ]
    radios = resolve_sdrs(cfg)
    assert radios[0]["serial"] == "KESTREL"
    assert radios[1]["serial"] is None


def _fake_rtlsdr(monkeypatch, serials):
    """Install a stand-in pyrtlsdr module; returns the list of opened indexes."""
    import sys, types
    opened = []

    class FakeRtl:
        @staticmethod
        def get_device_index_by_serial(serial):
            if serial not in serials:
                raise IOError("not found")
            return serials.index(serial)

        @staticmethod
        def get_device_serial_addresses():
            return list(serials)

        def __init__(self, index):
            opened.append(index)

    monkeypatch.setitem(sys.modules, "rtlsdr", types.SimpleNamespace(RtlSdr=FakeRtl))
    return opened


def test_rtlsdr_opens_by_serial(monkeypatch):
    from scanner.sdr_source import make_source
    opened = _fake_rtlsdr(monkeypatch, ["ADSB1090", "KESTREL"])
    src = make_source("rtlsdr", device_index=0, serial="KESTREL")
    src.open()
    assert opened == [1]                          # resolved by serial, not index 0


def test_rtlsdr_unknown_serial_is_a_clear_error(monkeypatch):
    import pytest
    from scanner.sdr_source import make_source, SDRSourceError
    _fake_rtlsdr(monkeypatch, ["ADSB1090"])
    src = make_source("rtlsdr", serial="KESTREL")
    with pytest.raises(SDRSourceError, match="rtl_eeprom") as exc:
        src.open()
    assert "Connected serials: ADSB1090" in str(exc.value)
