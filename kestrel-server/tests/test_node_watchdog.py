"""
tests/test_node_watchdog.py
===========================
Heartbeat-timeout offline detection (core/node_watchdog.py).
"""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import node_watchdog as wd
from core import runtime_settings as rt


def _pool(rows):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = acquire
    return pool, conn


def _patch_pool(pool):
    async def _get_pool():
        return pool
    return patch("core.node_watchdog.get_pool", new=_get_pool)


@pytest.fixture(autouse=True)
def _clean():
    rt._overrides.clear()
    yield
    rt._overrides.clear()


@pytest.mark.asyncio
async def test_silent_node_alerts_and_broadcasts_once():
    pool, conn = _pool([{"node_id": "KN-01", "silent_s": 372}])
    ae, bc = AsyncMock(), AsyncMock()
    with _patch_pool(pool):
        out = await wd.sweep_once(ae, bc, 300)
    assert out == ["KN-01"]
    ae.node_offline.assert_awaited_once()
    assert ae.node_offline.await_args.args[0] == "KN-01"
    assert "6 min" in ae.node_offline.await_args.kwargs["detail"]
    bc.broadcast_node_update.assert_awaited_once()
    assert bc.broadcast_node_update.await_args.args[:2] == ("KN-01", "offline")


@pytest.mark.asyncio
async def test_nothing_silent_does_nothing():
    pool, _ = _pool([])
    ae, bc = AsyncMock(), AsyncMock()
    with _patch_pool(pool):
        assert await wd.sweep_once(ae, bc, 300) == []
    ae.node_offline.assert_not_awaited()
    bc.broadcast_node_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_online_nodes_transition_and_timeout_is_passed():
    pool, conn = _pool([])
    with _patch_pool(pool):
        await wd.sweep_once(AsyncMock(), AsyncMock(), 300)
    sql, timeout = conn.fetch.await_args.args
    assert "WHERE status = 'online'" in sql      # never re-alerts an LWT-offline node
    assert "RETURNING" in sql                     # atomic flip, one alert per outage
    assert timeout == 300.0


def test_default_is_five_minutes_and_floor_is_enforced():
    assert rt.get("node_offline_after_s") == 300
    assert rt._coerce("node_offline_after_s", "60") == 300      # can't go below 5 min
    assert rt._coerce("node_offline_after_s", "900") == 900


@pytest.mark.asyncio
async def test_watchdog_uses_live_setting_and_survives_errors():
    rt._overrides["node_offline_after_s"] = 600
    calls = []

    async def fake_sweep(ae, bc, timeout_s):
        calls.append(timeout_s)
        if len(calls) == 1:
            raise RuntimeError("db hiccup")          # must not kill the task
        if len(calls) >= 2:
            raise asyncio.CancelledError

    with patch("core.node_watchdog.sweep_once", new=fake_sweep):
        with pytest.raises(asyncio.CancelledError):
            await wd.run_watchdog(AsyncMock(), AsyncMock(), check_every_s=0)
    assert calls == [600, 600]


@pytest.mark.asyncio
async def test_lwt_offline_alert_wording_unchanged_path():
    from mqtt.alert_engine import AlertEngine
    from core.config import Settings
    eng = AlertEngine(Settings(db_host="x", db_port=5432, db_name="t", db_user="t",
                               db_password="t", mqtt_host="x", mqtt_port=1883,
                               mqtt_user="t", mqtt_password="t"))
    seen = []
    async def fake_emit(a):
        seen.append(a)
    eng._emit = fake_emit
    await eng.node_offline("KN-01")                               # LWT path
    await eng.node_offline("KN-01", detail="No heartbeat from KN-01 for 6 min.")
    assert "disconnected from the broker" in seen[0]["description"]
    assert seen[1]["description"].startswith("No heartbeat from KN-01 for 6 min.")


@pytest.mark.asyncio
async def test_offline_alert_can_be_switched_off_in_settings():
    from mqtt.alert_engine import AlertEngine
    from core.config import Settings
    eng = AlertEngine(Settings(db_host="x", db_port=5432, db_name="t", db_user="t",
                               db_password="t", mqtt_host="x", mqtt_port=1883,
                               mqtt_user="t", mqtt_password="t"))
    seen = []
    async def fake_emit(a):
        seen.append(a)
    eng._emit = fake_emit

    await eng.node_offline("KN-01")
    assert len(seen) == 1                                   # on by default
    rt._overrides["alert_node_offline_enabled"] = False     # GUI toggle off
    await eng.node_offline("KN-01")
    await eng.node_offline("KN-01", detail="No heartbeat from KN-01 for 6 min.")
    assert len(seen) == 1                                   # both paths suppressed


def test_nodes_group_in_settings_snapshot():
    snap = rt.snapshot()
    nodes = [e for e in snap["editable"] if e["group"] == "Nodes"]
    keys = [e["key"] for e in nodes]
    assert keys == ["alert_node_offline_enabled", "alert_radio_down_enabled",
                    "radio_down_after_s", "node_offline_after_s"]
    by = {e["key"]: e for e in nodes}
    assert by["alert_node_offline_enabled"]["type"] == "bool"
    assert by["alert_node_offline_enabled"]["value"] is True
    assert by["node_offline_after_s"]["value"] == 300 and by["node_offline_after_s"]["min"] == 300
