#!/bin/bash
# scripts/setup_interfaces.sh
# ============================================================
# Run before the KESTREL Node starts (ExecStartPre in systemd).
# Sets up Wi-Fi monitor mode and ensures Bluetooth is ready.
#
# Dual-adapter support: wlan2 is configured only when present.
# Single-adapter deployments are fully supported.
# ============================================================

set -euo pipefail

# Interface names come from the node config (wifi.interface_2g / interface_5g),
# so a node deployed with --wifi-iface / --wifi-iface-5g gets the right ones.
CONFIG="${KESTREL_CONFIG:-/etc/kestrel-node/config.yaml}"
cfg_iface() {   # cfg_iface <key> <default>: first "key: value" in the file, unquoted
    local v=""
    [[ -r "$CONFIG" ]] && v="$(sed -n "s/^[[:space:]]*$1:[[:space:]]*\"\{0,1\}\([A-Za-z0-9_.-]*\)\"\{0,1\}.*/\1/p" "$CONFIG" | head -1)"
    echo "${v:-$2}"
}
WIFI_IFACE_2G="$(cfg_iface interface_2g wlan1)"    # Primary adapter — 2.4 GHz
WIFI_IFACE_5G="$(cfg_iface interface_5g wlan2)"    # Secondary adapter — 5 GHz (optional)

# ---- Helper: put an interface into monitor mode ----
setup_monitor() {
    local iface="$1"
    local label="$2"

    echo "[setup] Configuring monitor mode on $iface ($label)"

    if ip link show "$iface" &>/dev/null; then
        ip link set "$iface" down 2>/dev/null || true
        iw dev "$iface" set type monitor
        ip link set "$iface" up
        echo "[setup] $iface ($label) now in monitor mode"
    else
        echo "[setup] WARNING: $iface not found — skipping ($label)"
    fi
}

# Unblock all RF devices (WiFi + Bluetooth) in one pass.
# rfkill unblock wifi alone is insufficient on first boot — the nRF52840 dongle
# can come up RF-killed and hciconfig up will silently fail without this.
rfkill unblock all 2>/dev/null || true

# Keep NetworkManager off the monitor adapters without stopping it (it still
# manages eth0).  The deploy script also writes a persistent unmanaged rule.
if command -v nmcli &>/dev/null; then
    nmcli device set "$WIFI_IFACE_2G" managed no 2>/dev/null || true
    nmcli device set "$WIFI_IFACE_5G" managed no 2>/dev/null || true
fi

# ---- Primary adapter (2.4 GHz) ----
setup_monitor "$WIFI_IFACE_2G" "2.4 GHz"
iw dev "$WIFI_IFACE_2G" set channel 6 2>/dev/null || true

# ---- Secondary adapter (5 GHz) — only if present ----
if ip link show "$WIFI_IFACE_5G" &>/dev/null; then
    setup_monitor "$WIFI_IFACE_5G" "5 GHz"
    iw dev "$WIFI_IFACE_5G" set channel 36 2>/dev/null || true
else
    echo "[setup] $WIFI_IFACE_5G not found — single-adapter mode (2.4 GHz only)"
fi

# ---- Bluetooth ----
# Prefer the nRF52840 USB dongle (hci1) over the Pi's onboard adapter (hci0).
# Note: the agent then takes hci1 exclusively over an HCI user channel
# (scanner/hci_le.py), so BlueZ tools won't see it while the agent runs.
# Bringing it up here still matters for rfkill state and the bleak fallback.
# The nRF52840 is required for BT5 Long Range (Coded PHY).  If the dongle is
# absent we fall back to hci0 so the service still starts in degraded mode.
if hciconfig hci1 2>/dev/null | grep -q "hci1"; then
    BT_HCI="hci1"
    echo "[setup] nRF52840 dongle detected — using hci1"
else
    BT_HCI="hci0"
    echo "[setup] hci1 not found — falling back to onboard hci0 (no Coded PHY)"
fi

echo "[setup] Bringing up Bluetooth $BT_HCI"
hciconfig "$BT_HCI" up 2>/dev/null || true

# Disable page scan / inquiry scan (we're passive only)
hciconfig "$BT_HCI" noscan 2>/dev/null || true

echo "[setup] Interface setup complete"
exit 0
