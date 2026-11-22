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
