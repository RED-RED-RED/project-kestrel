#!/usr/bin/env python3
"""
scripts/healthcheck.py
======================
Quick deployment verification. Run from anywhere on your network
to check that all components are reachable and functioning.

Usage:
    python healthcheck.py --server 192.168.1.100

Output is colour-coded: green ✓ passing, red ✗ failing.
"""

import argparse
import json
import os
import socket
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError


RESET  = "\033[0m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"


def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET} {msg}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")


def check_tcp(host, port, label, timeout=3):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        ok(f"{label} ({host}:{port})")
        return True
    except Exception as e:
        fail(f"{label} ({host}:{port}) — {e}")
        return False


API_KEY = None   # set from --api-key / KESTREL_API_KEY in main()


def check_http(url, label, expected_key=None, timeout=5):
    try:
        headers = {"Accept": "application/json"}
        if API_KEY:
            headers["X-Api-Key"] = API_KEY
        with urlopen(Request(url, headers=headers), timeout=timeout) as r:
            body = json.loads(r.read())
        if expected_key and expected_key not in body:
            fail(f"{label} — response missing '{expected_key}'")
            return False
        ok(f"{label}")
        return body
    except URLError as e:
        reason = getattr(e, "reason", e)
        if getattr(e, "code", None) == 401:
            reason = "401 Unauthorized: pass --api-key (or set KESTREL_API_KEY)"
        fail(f"{label} — {reason}")
        return False
    except (ValueError, OSError) as e:
        fail(f"{label} — {e}")
        return False


def _pct(v):
    return "—" if v is None else f"{v:.0f}%"


def radio_problems(node):
    """Down/degraded radios from a node's live per-radio status."""
    out = []
    for r in node.get("radio_status") or []:
        if r.get("state") in ("down", "degraded"):
            out.append((r["state"], r.get("label", r.get("id")), r.get("error", "")))
    return out


def check_websocket(host, port, label, timeout=4):
    """Basic TCP handshake check for WS port."""
    return check_tcp(host, port, label, timeout)


def main():
    parser = argparse.ArgumentParser(description="KESTREL health check")
    parser.add_argument("--server", default="localhost", help="Server IP/hostname (http:// optional)")
    parser.add_argument("--api-key", default=os.environ.get("KESTREL_API_KEY"),
                        help="API key, if KESTREL_API_KEY is set on the server")
    parser.add_argument("--api-port",   type=int, default=8000)
    parser.add_argument("--http-port",  type=int, default=80)
    parser.add_argument("--mqtt-port",  type=int, default=1883)
    parser.add_argument("--db-port",    type=int, default=5432)
    args = parser.parse_args()

    global API_KEY
    API_KEY = args.api_key
    # Accept "http://1.2.3.4" or "1.2.3.4:80" as well as a bare host
    h = args.server.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0] \
        if args.server.count(":") <= 1 or "://" in args.server else args.server
    local = h in ("localhost", "127.0.0.1", "::1")
    print(f"\n{BOLD}KESTREL Deployment Health Check{RESET}")
    print(f"Target server: {BOLD}{h}{RESET}")
    print("─" * 44)

    results = {}

    # ── Network connectivity ───────────────────────────────────────────────
    head("Network")
    results["mqtt"]  = check_tcp(h, args.mqtt_port,  "MQTT broker (Mosquitto)")
    results["api"]   = check_tcp(h, args.api_port,   "FastAPI backend")
    results["http"]  = check_tcp(h, args.http_port,  "Nginx / KESTREL dashboard")
    # TimescaleDB only listens on the server's localhost; from elsewhere the
    # API's /health reports whether it can reach the database.
    if local:
        results["db"] = check_tcp(h, args.db_port, "TimescaleDB")

    # ── API endpoints ──────────────────────────────────────────────────────
    head("API Endpoints")
    base = f"http://{h}:{args.api_port}"

    health = check_http(f"{base}/health", "/health", expected_key="status")
    if health:
        ok(f"  Server version: {health.get('version','—')}")
        if not local:
            results["db"] = health.get("db") == "ok"
            (ok if results["db"] else fail)(f"  Database: {health.get('db', '?')} (via /health)")

    nodes_resp = check_http(f"{base}/api/nodes", "GET /api/nodes")
    if isinstance(nodes_resp, list):
        online = [n for n in nodes_resp if n.get("status") == "online"]
        ok(f"  {len(online)}/{len(nodes_resp)} nodes online")
        for node in nodes_resp:
            status = node.get("status", "unknown")
            color = GREEN if status == "online" else RED
            print(f"    {color}{'●' if status=='online' else '○'}{RESET} "
                  f"{node['node_id']} — {status} "
                  f"(CPU {_pct(node.get('cpu_pct'))} MEM {_pct(node.get('mem_pct'))})")
            if status != "online":
                continue
            probs = radio_problems(node)
            if node.get("radio_status") and not probs:
                print(f"        {GREEN}all radios running{RESET}")
            for state, label, err in probs:
                c = RED if state == "down" else YELLOW
                print(f"        {c}{state:8}{RESET} {label}{': ' + err if err else ''}")

    drones_resp = check_http(f"{base}/api/detections/tracks", "GET /api/detections/tracks")
    if isinstance(drones_resp, list):
        no_rid = [d for d in drones_resp if not d.get("has_valid_rid")]
        ok(f"  {len(drones_resp)} active drones ({len(no_rid)} with no RID)")

    alerts_resp = check_http(f"{base}/api/alerts?acknowledged=false&limit=10",
                              "GET /api/alerts (unacknowledged)")
    if isinstance(alerts_resp, list) and len(alerts_resp) > 0:
        high = [a for a in alerts_resp if a.get("level") == "high"]
        if high:
            warn(f"  {len(high)} HIGH alerts outstanding:")
            for a in high[:3]:
                print(f"    {RED}!{RESET} {a['title']}")

    check_http(f"{base}/api/analysis/stats", "GET /api/analysis/stats")

    # ── KESTREL dashboard ──────────────────────────────────────────────────────────
    head("KESTREL dashboard")
    try:
        req = Request(f"http://{h}:{args.http_port}/",
                      headers={"Accept": "text/html"})
        with urlopen(req, timeout=5) as r:
            body = r.read().decode()
        if "RID" in body or "kestrel" in body.lower():
            ok("KESTREL dashboard HTML served correctly")
        else:
            warn("KESTREL dashboard reachable but RID content not detected")
    except Exception as e:
        fail(f"KESTREL dashboard — {e}")

    # ── Summary ────────────────────────────────────────────────────────────
    head("Summary")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    color  = GREEN if passed == total else RED
    print(f"  {color}{passed}/{total} core services reachable{RESET}\n")

    if passed < total:
        print(f"  {YELLOW}Tip: run 'make server-logs' to check for errors{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
