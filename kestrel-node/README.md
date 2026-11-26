# KESTREL Node

The sensor half of KESTREL. Each node (callsigns `KN-01`, `KN-02`, ...) is a
Raspberry Pi that listens for FAA Remote ID over Wi-Fi NAN/Beacon (2.4 and
5 GHz) and Bluetooth 4 / 5 Long Range, optionally scans for drone control links
with an SDR and hears crewed aircraft over ADS-B, and publishes everything to
the KESTREL Server over MQTT.

Hardware, parts list and prices are in the [main README](../README.md#hardware).

## Install

On Raspberry Pi OS Lite 64-bit. **Trixie** is recommended: the RTL-SDR Blog V4
needs its rtl-sdr 2.0.2 (Bookworm ships 0.6.0). Download the deploy script and
run it (don't pipe it into bash: it asks questions):

```bash
wget https://raw.githubusercontent.com/RED-RED-RED/project-kestrel/main/scripts/deploy-kestrel-node.sh
sudo bash deploy-kestrel-node.sh
```

It installs packages, clones the repo to `/opt/kestrel-node`, builds the Python
venv, writes `/etc/kestrel-node/config.yaml`, sets up udev rules and the
`kestrel-node` service, and offers to reboot. Re-running it is safe: the
existing config is backed up first, but **pass the same flags again**, because
the config is regenerated from them.

Unattended, with every radio:

```bash
sudo bash deploy-kestrel-node.sh --unattended \
  --node-id KN-01 --server-ip 192.168.1.100 --mqtt-password '...' \
  --wifi-iface-5g wlan2 \
  --enable-sdr --sdr-serial KESTREL \
  --enable-adsb --adsb-serial ADSB1090
```

| Flag | Default | |
|---|---|---|
| `--node-id` | hostname | Shown on the dashboard and used in MQTT topics |
| `--server-ip` | (required) | Server LAN address |
| `--mqtt-fallback` | none | Server's Tailscale name, used when the LAN IP is unreachable |
| `--mqtt-password` | (required) | From the server's `DEPLOYMENT.md` or `kestrel-server/docker/.env` |
| `--wifi-iface` / `--wifi-iface-5g` | `wlan1` / none | Monitor-mode adapters for 2.4 and 5 GHz |
| `--enable-sdr` | off | RTL-SDR band scan (433 / 915 MHz / 1.2 GHz) |
| `--enable-hackrf` | off | HackRF for 2.4 / 5.8 GHz (installs libhackrf + SoapySDR) |
| `--enable-adsb` | off | dump1090-fa on a second RTL-SDR ([docs/adsb.md](../docs/adsb.md)) |
| `--sdr-serial` / `--adsb-serial` | none | Which RTL-SDR does what, when there are two |
| `--gps-mode` | `usb` | `uart` only for a legacy NEO-M8N on the Pi's UART (needs a reboot) |

With two RTL-SDRs, each dongle needs its own serial number first (one-time
`rtl_eeprom`). See [docs/adsb.md](../docs/adsb.md#multiple-rtl-sdrs-pin-them-by-serial);
the deploy warns if they still share one.

## Update

```bash
cd /opt/kestrel-node
sudo bash scripts/update.sh node
```

Pulls the latest `main`, updates the venv, refreshes the systemd unit and
restarts the service. `/etc/kestrel-node/config.yaml` is not touched.

## Check the radios

The dashboard's **System** view shows each radio's live state (running /
degraded / down, with the reason). On the node itself:

```bash
sudo systemctl status kestrel-node
journalctl -u kestrel-node -n 80 --no-pager | grep -iE "Scanner starting|RTL-SDR open|ADS-B|hci"
lsusb                 # every radio enumerates: RTL2838 ×n, MT7612U, u-blox, Nordic
rtl_test -t           # RTL-SDR index / model / serial (run with services stopped to open them)
iw dev                # wlan1 + wlan2 in "type monitor", channels changing between runs
iw reg get            # "country US" (00 disables most 5 GHz channels)
```

Scanners start up to 60 s after boot: the agent waits for a GPS fix first.
Indoors the GPS may not get one, which is fine for bench testing.

## Layout

```
kestrel-node/
├── agent.py                 Entry point: starts scanners, heartbeat loop
├── config/
│   ├── settings.py          YAML + env loader, SDR list resolution
│   └── config.example.yaml  Reference config (deploy writes the real one)
├── parser/
│   ├── opendroneid.py       ASTM F3411 Remote ID decoder
│   └── assembler.py         Merges a drone's messages into one snapshot
├── scanner/
│   ├── wifi_nan.py          Wi-Fi NAN + Beacon Remote ID (Scapy, monitor mode)
│   ├── bluetooth.py         BT4 + BT5 Long Range Remote ID
│   ├── hci_le.py            Direct HCI access to the nRF52840 (extended scanning)
│   ├── sdr.py               RF band sweep + link detection
│   ├── sdr_source.py        RTL-SDR / HackRF (SoapySDR) backends
│   ├── sdr_classify.py      Classifies captures (OcuSync, ELRS, FPV video, ...)
│   ├── adsb.py              Reads dump1090-fa's aircraft.json
│   └── health.py            Per-radio live status for the heartbeat
├── publisher/
│   ├── mqtt_client.py       MQTT publisher (queue, reconnect, LAN/Tailscale fallback)
│   └── gps.py               u-blox UBX/NMEA daemon, survey-in, jamming/spoofing
├── systemd/
│   ├── kestrel-node.service
│   └── setup_interfaces.sh  Puts the Wi-Fi adapters in monitor mode at start
├── firmware/nrf52840-hci-usb/   Dongle firmware with Coded PHY scanning
└── tests/
```

## MQTT topics

| Topic | QoS | Content |
|---|---|---|
| `kestrel/<node>/detection` | 1 | One Remote ID detection (below) |
| `kestrel/<node>/heartbeat` | 0 | Health + GPS every 10 s; per-radio status once a minute or on any change |
| `kestrel/<node>/status` | 1 | `online` / `offline` (retained; `offline` is the last will) |
| `kestrel/<node>/rf_detection` | 0 | An SDR-classified control/video link |
| `kestrel/<node>/aircraft` | 0 | ADS-B aircraft heard this interval |

Payloads are compact JSON. A detection:

```json
{
  "node_id": "KN-01",
  "transport": "wifi_nan",
  "rssi": -68,
  "src_addr": "fa:3b:92:0e:11:22",
  "node_position": { "lat": 41.5440, "lon": -96.1250, "alt": 317.9 },
  "drone": {
    "id": "1581F5FJD239C00B12A4",
    "id_type": "serial_number",
    "ua_type": "helicopter_or_mr",
    "status": "airborne",
    "lat": 41.5480, "lon": -96.1190,
    "alt_baro": 380.0, "alt_geo": 352.5,
    "height_agl": 34.0, "height_type": "takeoff",
    "speed_h": 8.2, "speed_v": 0.5, "heading": 127.0,
    "operator_id": "FIN87astrdge12k8",
    "operator_lat": 41.5441, "operator_lon": -96.1248,
    "description": ""
  },
  "ts": 1742000000.123
}
```

`node_position.alt` and `alt_geo` are heights above the WGS-84 ellipsoid; the
server subtracts them to estimate height above ground. `height_type` says
whether `height_agl` is above the takeoff point or above the ground.

## Hardware notes

**Wi-Fi.** The Alfa AWUS036ACM (MediaTek MT7612U, `mt76x2u` driver) reliably
passes NAN action frames in monitor mode; many other chipsets drop them. Both
adapters can be the same dual-band model: one is pinned to 2.4 GHz channels,
the other to 5 GHz, so it doesn't matter which comes up as `wlan1`. Check with
`iw dev <iface> info` and `dmesg | grep -i mt76`.

**Bluetooth.** The Nordic nRF52840 dongle with the firmware in
[`firmware/nrf52840-hci-usb`](firmware/nrf52840-hci-usb/README.md) supports
extended scanning, which Bluetooth 5 Long Range (Coded PHY) Remote ID needs.
The agent drives it over a raw HCI channel (`CAP_NET_RAW`, granted by the
service). With stock firmware it falls back to BT4 only, and the dashboard shows
Long Range as degraded.

**Power.** Two MT7612Us, two RTL-SDRs, the nRF52840 and the GPS can exceed what
the Pi's USB ports supply. Use a powered hub if radios drop out.

## Tests

On a development machine, from the repo:

```bash
cd kestrel-node
python3 -m pytest tests/ -q      # needs requirements.txt + pytest, pytest-asyncio
```
