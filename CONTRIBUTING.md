# Contributing to KESTREL Platform

## Repository layout

```
project-kestrel/
├── kestrel-node/        KESTREL sensor node (Python, runs on each Pi)
├── kestrel-server/      KESTREL central server (FastAPI + TimescaleDB)
├── kestrel-dashboard/      KESTREL dashboard (React + Leaflet)
├── calibration/       RSSI path-loss calibration utility
├── scripts/           Deployment and maintenance scripts
└── docs/              Architecture docs and hardware guides
```

## Development setup

```bash
# Clone
git clone https://github.com/RED-RED-RED/project-kestrel
cd project-kestrel

# Python deps (Python 3.12, matching the server container and CI)
pip install -r kestrel-server/requirements-dev.txt
pip install -r kestrel-node/requirements.txt
pip install -r calibration/requirements.txt
pip install pytest pytest-asyncio

# UI deps
cd kestrel-dashboard && npm install && cd ..

# Run all tests
make test

# Start server stack locally (requires Docker)
make server-up

# Start UI dev server (proxies to localhost:8000)
make ui-dev
```

## Running tests

```bash
make test          # everything (each suite runs from its own folder, like CI)
make test-node     # node: RID parser, GPS, Wi-Fi NAN, Bluetooth, SDR, ADS-B, radio health
make test-server   # server: alerts, trilateration, threat scoring, RF, flights, settings
make test-cal      # calibration engine, collector, config writer
```

## Pull request checklist

- [ ] All tests pass (`make test`)
- [ ] UI builds without errors (`make ui-build`)
- [ ] New code has corresponding tests
- [ ] MQTT topic names use `kestrel/` prefix
- [ ] Node IDs use the `KN-NN` format
- [ ] No credentials committed (check `.gitignore`)
- [ ] Deployment scripts updated if install paths, packages or config keys changed
- [ ] Shell scripts pass `shellcheck -S warning` and are executable in git
      (`git update-index --chmod=+x path/to/script.sh`)

## Coding conventions

**Python:** PEP 8, type hints on public functions, docstrings on modules.
Logging via `logging.getLogger(__name__)` — no `print()` in production code.

**JavaScript/JSX:** Functional components, hooks only. State in Zustand store,
not component-local state unless purely UI. CSS-in-JS via template literals
at top of each component file.

**MQTT topics:** Always `kestrel/<node_id>/<message_type>` — never hardcode
the prefix, use `settings.mqtt_topic_prefix`.

**Naming:** KESTREL nodes, KESTREL server, KESTREL dashboard (UI). See README for
the complete naming map.
