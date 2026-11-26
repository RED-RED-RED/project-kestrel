# KESTREL Server

FastAPI backend for KESTREL. Consumes MQTT from KESTREL nodes (Remote ID
detections, SDR RF links, ADS-B aircraft, heartbeats with per-radio status),
stores them in TimescaleDB, runs the alert engine, trilateration and threat
scoring, and pushes live state to the dashboard over WebSocket.

## Deploy

From the repo root, on the server host:

```bash
sudo bash scripts/deploy-kestrel-server.sh
```

It installs Docker if needed, writes `docker/.env` with generated passwords,
builds the dashboard and starts TimescaleDB, Mosquitto, the API and Nginx. The
passwords also go into `DEPLOYMENT.md` in the repo root (gitignored). Back up
`docker/.env`.

Re-running it on an existing server keeps `docker/.env` as it is (passwords,
API key, notification settings) and saves a timestamped backup beside it.

By hand instead: `cp docker/.env.example docker/.env`, set the passwords, create
`docker/mosquitto_passwd` (`mosquitto_passwd -c -b docker/mosquitto_passwd rid <MQTT_PASSWORD>`),
build the dashboard (`cd ../kestrel-dashboard && npm install && npm run build`),
then `make server-up` from the repo root.

## Update

```bash
sudo bash scripts/update.sh server
```

Pulls, rebuilds the dashboard, and rebuilds and restarts the API container. New
tables and columns are added automatically at startup.

## Ports

| Port | Service |
|---|---|
| 80 | Nginx: dashboard, `/api`, `/ws` |
| 8000 | API directly (calibration tool, `healthcheck.py`) |
| 1883 | Mosquitto (authenticated) |
| 5432 | TimescaleDB, bound to `127.0.0.1` only |

## Tests

```bash
cd kestrel-server
pip install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

Use Python 3.12 (what the container and CI run); the pinned `asyncpg` doesn't
build on 3.13.
