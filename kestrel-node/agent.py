#!/usr/bin/env python3
"""
KESTREL Node
====================
Main entry point. Starts all scanner threads and the MQTT publisher.
Runs on each Raspberry Pi KESTREL node.

Hardware expected:
  - Alfa AWUS036ACM  (Wi-Fi, monitor mode)   → wlan1
  - nRF52840 USB     (Bluetooth 4/5 LR)      → hci1 (taken from BlueZ while running)
  - u-blox NEO-M9N   (GPS, USB)              → /dev/ttyGPS
  - RTL-SDR Blog V4  (optional SDR)          → device 0

Author: KESTREL project
"""

import logging
import os
import signal
import sys
import threading
import time

from config.settings import load_config, NodeConfig, resolve_sdrs
from banner import banner
from scanner.wifi_nan import WiFiNANScanner
from scanner.bluetooth import BluetoothScanner
from scanner.sdr import SDRScanner
from scanner.adsb import ADSBScanner
from publisher.mqtt_client import MQTTPublisher
from publisher.gps import GPSDaemon
from scanner.health import collect as collect_radio_status, off as radio_off, StatusThrottle

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
_LOG_DIR = "/var/log/kestrel-node"
_LOG_FILE = f"{_LOG_DIR}/kestrel-node.log"

os.makedirs(_LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE),
    ],
)
log = logging.getLogger("agent")


# --------------------------------------------------------------------------- #
# Graceful shutdown
# --------------------------------------------------------------------------- #
_stop_event = threading.Event()

def _handle_signal(sig, frame):
    log.info(f"Received signal {sig}, shutting down…")
    _stop_event.set()

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    cfg: NodeConfig = load_config()
    for line in banner(cfg.node_id, cfg.site_name).splitlines():
        log.info(line)
    log.info(f"Starting KESTREL Node — ID={cfg.node_id}, site={cfg.site_name}")

    # ---- MQTT publisher (shared across all scanners) ----
    publisher = MQTTPublisher(cfg)
    publisher.connect()

    # ---- GPS daemon (runs in background, updates shared position) ----
    gps = GPSDaemon(
        cfg.gps_serial_port,
        cfg.gps_baud,
        mode=cfg.gps_mode,
        auto_detect=cfg.gps_auto_detect,
    )
    gps_thread = threading.Thread(target=gps.run, args=(_stop_event,), daemon=True)
    gps_thread.start()

    # Give GPS a moment to get a fix
    log.info("Waiting for GPS fix (up to 60 s)…")
    gps.wait_for_fix(timeout=60)
    if not gps.has_fix:
        log.warning("No GPS fix yet — will use last known position or (0,0)")

    # ---- Scanner threads ----
    threads = []
    health_sources = []   # (status_fn, thread) per scanner, or an "off" dict

    # Wi-Fi NAN scanners (one per adapter; each requires its interface in monitor mode)
    wifi_bands_active = []

    if cfg.wifi_enabled_2g:
        wifi_2g = WiFiNANScanner(
            iface=cfg.wifi_interface_2g,
            publisher=publisher,
            gps=gps,
            node_id=cfg.node_id,
            stop_event=_stop_event,
            band="2.4",
            channels=cfg.wifi_channels_2g,
            dwell_ms=cfg.wifi_dwell_ms_2g,
        )
        threads.append(threading.Thread(target=wifi_2g.run, name="wifi-nan-2g", daemon=True))
        health_sources.append((wifi_2g.status, threads[-1]))
        wifi_bands_active.append(f"2.4 GHz ({cfg.wifi_interface_2g})")
    else:
        health_sources.append(radio_off("wifi_2g", "wifi", "Wi-Fi 2.4 GHz"))

    if cfg.wifi_enabled_5g:
        wifi_5g = WiFiNANScanner(
            iface=cfg.wifi_interface_5g,
            publisher=publisher,
            gps=gps,
            node_id=cfg.node_id,
            stop_event=_stop_event,
            band="5",
            channels=cfg.wifi_channels_5g,
            dwell_ms=cfg.wifi_dwell_ms_5g,
        )
        threads.append(threading.Thread(target=wifi_5g.run, name="wifi-nan-5g", daemon=True))
        health_sources.append((wifi_5g.status, threads[-1]))
        wifi_bands_active.append(f"5 GHz ({cfg.wifi_interface_5g})")
    else:
        health_sources.append(radio_off("wifi_5g", "wifi", "Wi-Fi 5 GHz"))

    if wifi_bands_active:
        log.info(f"WiFi NAN scanning: {', '.join(wifi_bands_active)}")

    # Bluetooth 4/5 LR scanner (direct HCI on the dongle, bleak fallback)
    if cfg.bt_enabled:
        bt = BluetoothScanner(
            hci_index=cfg.bt_hci_index,
            publisher=publisher,
            gps=gps,
            node_id=cfg.node_id,
            stop_event=_stop_event,
            enable_coded_phy=cfg.bt_coded_phy,  # BT5 LR — nRF52840 only
            backend=cfg.bt_backend,
        )
        t = threading.Thread(target=bt.run_sync, name="bluetooth", daemon=True)
        threads.append(t)
        health_sources.append((bt.status, t))
        log.info(f"Bluetooth scanner → hci{cfg.bt_hci_index} (Coded PHY={cfg.bt_coded_phy})")

    if not cfg.bt_enabled:
        health_sources.append(radio_off("bt", "bt", "Bluetooth"))

    # SDR radios (optional — passive RF fingerprinting). Supports several at
    # once, e.g. an RTL-SDR on the sub-GHz control bands plus a HackRF on
    # 2.4/5.8 GHz, each running its own parallel sweep.
    sdr_radios = resolve_sdrs(cfg)
    for i, radio in enumerate(sdr_radios):
        sdr = SDRScanner(
            device_index=radio["device_index"],
            publisher=publisher,
            gps=gps,
            node_id=cfg.node_id,
            stop_event=_stop_event,
            backend=radio["backend"],
            sample_rate=radio["sample_rate"],
            bands=radio["bands"],
            dwell_s=cfg.sdr_dwell_s,
            min_confidence=cfg.sdr_min_confidence,
            publish_interval_s=cfg.sdr_publish_interval_s,
            rssi_offset_dbm=cfg.sdr_rssi_offset_dbm,
            serial=radio.get("serial"),
        )
        t = threading.Thread(target=sdr.run, name=f"sdr-{radio['backend']}-{i}", daemon=True)
        threads.append(t)
        health_sources.append((sdr.status, t))
        bands_desc = ",".join(radio["bands"]) if radio["bands"] else "auto"
        which = f"serial {radio['serial']}" if radio.get("serial") else f"device {radio['device_index']}"
        log.info(f"SDR scanner → {radio['backend']} ({which}, bands {bands_desc})")

    # ADS-B (optional — crewed aircraft via dump1090 on a dedicated 1090 dongle)
    if cfg.adsb_enabled:
        adsb = ADSBScanner(
            publisher=publisher,
            gps=gps,
            node_id=cfg.node_id,
            stop_event=_stop_event,
            source_url=cfg.adsb_source_url,
            publish_interval_s=cfg.adsb_publish_interval_s,
            max_age_s=cfg.adsb_max_age_s,
        )
        threads.append(threading.Thread(target=adsb.run, name="adsb", daemon=True))
        health_sources.append((adsb.status, threads[-1]))
        log.info(f"ADS-B scanner → {cfg.adsb_source_url}")
    else:
        health_sources.append(radio_off("adsb", "adsb", "ADS-B"))

    if not threads:
        log.error("No scanners enabled — check config. Exiting.")
        sys.exit(1)

    # Start all threads
    for t in threads:
        t.start()
    log.info(f"All {len(threads)} scanner(s) running. Press Ctrl+C to stop.")

    # Build radios list based on enabled scanners
    active_radios = []
    if wifi_bands_active:    active_radios.append("wifi")
    if cfg.bt_enabled:       active_radios.append("bt")
    if sdr_radios:           active_radios.append("sdr")
    if cfg.adsb_enabled:     active_radios.append("adsb")

    # ---- Heartbeat loop ----
    throttle = StatusThrottle(cfg.radio_status_interval_s)
    try:
        while not _stop_event.is_set():
            status = collect_radio_status(health_sources, gps)
            publisher.send_heartbeat(gps, radios=active_radios,
                                     radio_status=status if throttle.due(status) else None)
            time.sleep(cfg.heartbeat_interval_s)
    finally:
        log.info("Stopping…")
        _stop_event.set()
        for t in threads:
            t.join(timeout=5)
        publisher.disconnect()
        log.info("Node agent stopped.")


if __name__ == "__main__":
    main()
