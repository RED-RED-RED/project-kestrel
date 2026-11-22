"""
core/node_watchdog.py
=====================
Marks a node offline when its heartbeats stop, even if the broker never
delivers its MQTT last-will (LWT).

The LWT covers the common case (the broker notices the dropped connection).
It misses some: the broker restarted while the node was down, or the node's
last-will went to a topic the server doesn't subscribe to. Without this the
node would read "online" indefinitely.

Nodes heartbeat every ~10 s. A node is only marked offline after
node_offline_after_s of silence (default 5 minutes, never less), so a brief
network blip doesn't flap it or page anyone. The next heartbeat brings it back
online on its own (the heartbeat handler sets status = 'online').
"""

from __future__ import annotations

import asyncio
import logging

from core import runtime_settings as rt
from db.database import get_pool

log = logging.getLogger("node-watchdog")

CHECK_EVERY_S = 30


async def sweep_once(alert_engine, broadcaster, timeout_s: float) -> list[str]:
    """Flip silent nodes online -> offline, alert once per outage. Returns ids."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # One atomic statement: only nodes still 'online' transition, so an
        # outage is reported once, and never on top of an LWT offline.
        rows = await conn.fetch("""
            UPDATE nodes SET status = 'offline'
            WHERE status = 'online'
              AND last_seen < NOW() - make_interval(secs => $1)
            RETURNING node_id,
                      EXTRACT(EPOCH FROM (NOW() - last_seen))::int AS silent_s
        """, float(timeout_s))

    offline = []
    for r in rows:
        node_id, silent_s = r["node_id"], r["silent_s"] or 0
        mins = max(1, round(silent_s / 60))
        log.warning(f"Node {node_id} marked offline: no heartbeat for {silent_s}s")
        await alert_engine.node_offline(
            node_id, detail=f"No heartbeat from {node_id} for {mins} min."
        )
        await broadcaster.broadcast_node_update(
            node_id, "offline", {"reason": "heartbeat_timeout", "silent_s": silent_s}
        )
        offline.append(node_id)
    return offline


async def run_watchdog(alert_engine, broadcaster, check_every_s: float = CHECK_EVERY_S):
    """Background task: sweep forever. Errors are logged, never fatal."""
    log.info("Node watchdog running")
    while True:
        try:
            await sweep_once(alert_engine, broadcaster, rt.get("node_offline_after_s"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"Node watchdog sweep failed: {e}")
        await asyncio.sleep(check_every_s)
