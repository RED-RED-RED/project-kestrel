#!/usr/bin/env bash
# scripts/deploy-kestrel-node.sh
# ============================================================
# Automated KESTREL Node deployment wizard.
# Run on Raspberry Pi OS Lite 64-bit as root. Trixie (Debian 13) recommended:
# the RTL-SDR Blog V4 needs its rtl-sdr 2.0.2 (Bookworm ships 0.6.0).
# Re-running is safe: the existing config is backed up before it's rewritten.
#
# Usage:
#   sudo bash deploy-kestrel-node.sh
#
# Unattended (CI / pre-provisioning):
#   sudo bash deploy-kestrel-node.sh \
#     --unattended \
#     --node-id KN-01 \
#     --server-ip 192.168.1.100 \
#     --mqtt-password your_password
#
# Optional radios (also offered as prompts in interactive mode):
#   --enable-sdr      RTL-SDR sub-GHz band scan
#   --enable-hackrf   HackRF One for 2.4 / 5.8 GHz (libhackrf + SoapySDR)
#   --enable-adsb     dump1090-fa on a dedicated 1090 MHz RTL-SDR
#
# Two RTL-SDRs (band scan + ADS-B) are told apart by serial number. Give each
# dongle its own serial once with rtl_eeprom (see docs/adsb.md), then:
#   --sdr-serial KESTREL      band-scan RTL-SDR
#   --adsb-serial ADSB1090    1090 MHz RTL-SDR used by dump1090-fa
#
# MQTT username is always "rid" — set by the KESTREL Server at deploy time.
# ============================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
fail() { echo -e "${RED}  ✗ $*${NC}"; exit 1; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
info() { echo -e "${BLUE}  →${NC} $*"; }
step() { echo -e "\n${BOLD}[$1/8]${NC} $2"; }

REPO_URL="https://github.com/RED-RED-RED/project-kestrel.git"
INSTALL_DIR="/opt/kestrel-node"
CONFIG_DIR="/etc/kestrel-node"
LOG_DIR="/var/log/kestrel-node"

# ── Argument parsing ─────────────────────────────────────────────────────
UNATTENDED=false
OPT_NODE_ID=""; OPT_SERVER_IP=""; OPT_MQTT_PASS=""
OPT_WIFI_IFACE="wlan1"; OPT_WIFI_IFACE_5G=""; OPT_SDR=false; OPT_ADSB=false; OPT_HACKRF=false
OPT_REPO=""; OPT_GPS_MODE="usb"; OPT_MQTT_FALLBACK=""
OPT_SDR_SERIAL=""; OPT_ADSB_SERIAL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --unattended)      UNATTENDED=true;;
    --node-id)         OPT_NODE_ID="$2";       shift;;
    --server-ip)       OPT_SERVER_IP="$2";     shift;;
    --mqtt-fallback)   OPT_MQTT_FALLBACK="$2"; shift;;
    --mqtt-password)   OPT_MQTT_PASS="$2";     shift;;
    --wifi-iface)      OPT_WIFI_IFACE="$2";    shift;;
    --wifi-iface-5g)   OPT_WIFI_IFACE_5G="$2"; shift;;
    --enable-sdr)      OPT_SDR=true;;
    --enable-adsb)     OPT_ADSB=true;;
    --enable-hackrf)   OPT_HACKRF=true;;
    --sdr-serial)      OPT_SDR_SERIAL="$2";    shift;;
    --adsb-serial)     OPT_ADSB_SERIAL="$2";   shift;;
    --repo)            OPT_REPO="$2";          shift;;
    --gps-mode)        OPT_GPS_MODE="$2";      shift;;
    *) warn "Unknown argument: $1";;
  esac
  shift 2>/dev/null || true
done

[[ -n "$OPT_REPO" ]] && REPO_URL="$OPT_REPO"
[[ $EUID -eq 0 ]]    || fail "Must run as root:  sudo bash $0"

# ── Banner ───────────────────────────────────────────────────────────────
clear
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        KESTREL Node  —  Deployment Wizard       ║${NC}"
echo -e "${BOLD}║      KESTREL Airspace Protection Platform       ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════╝${NC}"
echo ""

# ── Interactive prompts ───────────────────────────────────────────────────
if [[ "$UNATTENDED" == false ]]; then
  DEFAULT_ID=$(hostname | tr '[:lower:]' '[:upper:]')
  echo "  Configure this Pi as an KESTREL sensor node."
  echo "  Press Enter to accept defaults shown in [brackets]."
  echo ""

  echo "  Node ID"
  echo "  A unique name for this sensor node — used in MQTT topics"
  echo "  and displayed in the KESTREL dashboard."
  read -rp "    ID [${DEFAULT_ID}]: " NODE_ID
  NODE_ID="${NODE_ID:-$DEFAULT_ID}"
  echo ""

  echo "  KESTREL Server IP"
  echo "  The IP address of the machine running the KESTREL Server stack."
  echo "  Check DEPLOYMENT.md on the server if you are unsure."
  read -rp "    Server IP: " SERVER_IP
  [[ -n "$SERVER_IP" ]] || fail "Server IP is required"
  echo ""

  echo "  Tailscale fallback (optional)"
  echo "  The server's Tailscale name (e.g. kestrel-server). If set, a node that"
  echo "  can't reach the LAN IP above reconnects over Tailscale, then flips back."
  echo "  Requires Tailscale running on this node and the server. Leave blank to skip."
  read -rp "    Server Tailscale name: " MQTT_FALLBACK
  MQTT_FALLBACK="${MQTT_FALLBACK:-}"
  echo ""

  echo "  MQTT password"
  echo "  The MQTT broker password set during KESTREL Server deployment."
  echo "  Find it in DEPLOYMENT.md or kestrel-server/docker/.env on the server."
  while true; do
    read -rsp "    Password: " MQTT_PASS; echo
    [[ -n "$MQTT_PASS" ]] || { echo -e "${RED}  Password is required.${NC}"; continue; }
    read -rsp "    Confirm password: " MQTT_PASS_CONFIRM; echo
    [[ "$MQTT_PASS" == "$MQTT_PASS_CONFIRM" ]] && break
    echo -e "${RED}  Passwords do not match. Try again.${NC}"
  done
  MQTT_USER="rid"
  echo ""

  read -rp "  Primary Wi-Fi adapter (2.4 GHz) [wlan1]: " WIFI_IFACE
  WIFI_IFACE="${WIFI_IFACE:-wlan1}"

  read -rp "  Secondary Wi-Fi adapter (5 GHz, leave blank if not fitted): " WIFI_IFACE_5G
  WIFI_IFACE_5G="${WIFI_IFACE_5G:-}"

  read -rp "  Enable RTL-SDR scanner? [y/N]: " SDR_RESP
  [[ "${SDR_RESP,,}" == "y" ]] && ENABLE_SDR=true || ENABLE_SDR=false

  read -rp "  HackRF fitted (2.4 / 5.8 GHz)? [y/N]: " HACKRF_RESP
  [[ "${HACKRF_RESP,,}" == "y" ]] && ENABLE_HACKRF=true || ENABLE_HACKRF=false

  read -rp "  ADS-B (second RTL-SDR on 1090 MHz, dump1090-fa)? [y/N]: " ADSB_RESP
  [[ "${ADSB_RESP,,}" == "y" ]] && ENABLE_ADSB=true || ENABLE_ADSB=false

  SDR_SERIAL=""; ADSB_SERIAL=""
  if [[ "$ENABLE_SDR" == true && "$ENABLE_ADSB" == true ]]; then
    echo ""
    echo "  Two RTL-SDRs are told apart by serial (set once with rtl_eeprom, docs/adsb.md)."
    echo "  Leave blank if you haven't set them yet; you can add them to the config later."
    read -rp "    Band-scan RTL-SDR serial [KESTREL]: " SDR_SERIAL
    read -rp "    ADS-B RTL-SDR serial [ADSB1090]: " ADSB_SERIAL
    SDR_SERIAL="${SDR_SERIAL:-KESTREL}"; ADSB_SERIAL="${ADSB_SERIAL:-ADSB1090}"
  fi

  read -rp "  GPS mode — usb (NEO-M9N) or uart (legacy NEO-M8N) [usb]: " GPS_MODE_RESP
  GPS_MODE="${GPS_MODE_RESP:-usb}"
else
  NODE_ID="${OPT_NODE_ID:-$(hostname | tr '[:lower:]' '[:upper:]')}"
  SERVER_IP="$OPT_SERVER_IP"
  MQTT_USER="rid"
  MQTT_PASS="$OPT_MQTT_PASS"
  MQTT_FALLBACK="$OPT_MQTT_FALLBACK"
  WIFI_IFACE="$OPT_WIFI_IFACE"
  WIFI_IFACE_5G="$OPT_WIFI_IFACE_5G"
  ENABLE_SDR="$OPT_SDR"
  ENABLE_HACKRF="$OPT_HACKRF"
  ENABLE_ADSB="$OPT_ADSB"
  SDR_SERIAL="$OPT_SDR_SERIAL"
  ADSB_SERIAL="$OPT_ADSB_SERIAL"
  GPS_MODE="$OPT_GPS_MODE"
  [[ -n "$SERVER_IP" ]] || fail "--server-ip is required in unattended mode"
  [[ -n "$MQTT_PASS"  ]] || fail "--mqtt-password is required in unattended mode"
fi

# Flags win over the interactive prompts
[[ "$OPT_SDR"  == true ]] && ENABLE_SDR=true
[[ "$OPT_ADSB" == true ]] && ENABLE_ADSB=true
[[ -n "$OPT_SDR_SERIAL"  ]] && SDR_SERIAL="$OPT_SDR_SERIAL"
[[ -n "$OPT_ADSB_SERIAL" ]] && ADSB_SERIAL="$OPT_ADSB_SERIAL"
SDR_STR="false";   [[ "$ENABLE_SDR"    == true ]] && SDR_STR="true"
ADSB_STR="false";  [[ "${ENABLE_ADSB:-false}" == true ]] && ADSB_STR="true"
# --enable-hackrf wins over the interactive prompt
[[ "$OPT_HACKRF" == true ]] && ENABLE_HACKRF=true
HACKRF_STR="false"; [[ "${ENABLE_HACKRF:-false}" == true ]] && HACKRF_STR="true"
WIFI_5G_STR="false"; [[ -n "$WIFI_IFACE_5G" ]] && WIFI_5G_STR="true"
GPS_MODE="${GPS_MODE:-usb}"

echo ""
info "Node ID    : ${BOLD}${NODE_ID}${NC}"
info "Server     : ${BOLD}${SERVER_IP}${NC}"
info "Wi-Fi 2.4G : ${WIFI_IFACE}"
info "Wi-Fi 5G   : ${WIFI_IFACE_5G:-(not fitted)}"
info "SDR        : ${SDR_STR}${SDR_SERIAL:+ (serial ${SDR_SERIAL})}"
info "HackRF     : ${HACKRF_STR}"
info "ADS-B      : ${ADSB_STR}${ADSB_SERIAL:+ (serial ${ADSB_SERIAL})}"
info "GPS mode   : ${GPS_MODE}"
echo ""

# ── 1/8  System packages ─────────────────────────────────────────────────
step 1 "System packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv python3-dev gcc \
  git curl wget \
  aircrack-ng iw wireless-tools rfkill \
  bluez bluez-tools \
  rtl-sdr librtlsdr-dev \
  usbutils tcpdump mosquitto-clients jq
ok "Packages installed"

# dump1090-fa for ADS-B (crewed-aircraft awareness) — only when enabled. It
# decodes 1090 MHz on its own dedicated RTL-SDR and the node agent just reads
# its aircraft.json. Best-effort from the FlightAware apt repo, so a repo hiccup
# never aborts the node deploy; see docs/adsb.md for the dongle-serial setup.
if [[ "$ADSB_STR" == "true" ]] && ! command -v dump1090-fa >/dev/null 2>&1; then
  step 1b "dump1090-fa (ADS-B 1090 MHz)"
  _tmp="$(mktemp -d)"
  if wget -qO "$_tmp/fa-repo.deb" \
       "https://www.flightaware.com/adsb/piaware/files/packages/pool/piaware/f/flightaware-apt-repository/flightaware-apt-repository_1.3_all.deb" \
     && dpkg -i "$_tmp/fa-repo.deb" >/dev/null 2>&1; then
    apt-get update -qq || true
    if DEBIAN_FRONTEND=noninteractive apt-get install -y dump1090-fa >/dev/null 2>&1; then
      ok "dump1090-fa installed"
    else
      warn "dump1090-fa install failed — install it manually (docs/adsb.md)"
    fi
  else
    warn "Could not add the FlightAware repo — install dump1090-fa manually (docs/adsb.md)"
  fi
  rm -rf "$_tmp"
fi

# Point dump1090-fa at its own dongle. Without this it opens the first RTL-SDR
# it finds, which may be the band-scan one.
if [[ "$ADSB_STR" == "true" && -n "$ADSB_SERIAL" && -f /etc/default/dump1090-fa ]]; then
  if grep -q '^RECEIVER_SERIAL=' /etc/default/dump1090-fa; then
    sed -i "s|^RECEIVER_SERIAL=.*|RECEIVER_SERIAL=${ADSB_SERIAL}|" /etc/default/dump1090-fa
  else
    echo "RECEIVER_SERIAL=${ADSB_SERIAL}" >> /etc/default/dump1090-fa
  fi
  systemctl restart dump1090-fa 2>/dev/null || true
  ok "dump1090-fa pinned to RTL-SDR serial ${ADSB_SERIAL}"
fi

# HackRF One for the 2.4 / 5.8 GHz bands — only when enabled. The node drives it
# through SoapySDR, so this needs libhackrf (+ the hackrf CLI tools for
# hackrf_info) and SoapySDR's HackRF module and Python binding, all from Debian.
# Best-effort: a failure warns and leaves the HackRF entry unable to open; the
# rest of the node still runs.
HACKRF_OK=false
if [[ "$HACKRF_STR" == "true" ]]; then
  step 1c "HackRF (libhackrf + SoapySDR)"
  if DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       hackrf libhackrf0 soapysdr-tools soapysdr-module-hackrf python3-soapysdr \
       >/dev/null 2>&1; then
    ok "libhackrf + SoapySDR HackRF module installed"
    HACKRF_OK=true
    if hackrf_info 2>/dev/null | grep -q "Serial number"; then
      ok "HackRF detected: $(hackrf_info 2>/dev/null | grep -m1 'Serial number' | awk '{print $NF}')"
    else
      warn "No HackRF detected right now (fine if it isn't plugged in yet)"
    fi
  else
    warn "HackRF packages failed to install. Run: apt-get install hackrf libhackrf0 soapysdr-module-hackrf python3-soapysdr"
  fi
fi

# ── 2/8  Hardware configuration ──────────────────────────────────────────
step 2 "Hardware configuration"

# RTL-SDR: blacklist DVB drivers so the dongle is claimed by rtl-sdr, not the
# kernel DVB stack.  Both modules must be listed — rtl2832 is the demodulator
# that dvb_usb_rtl28xxu depends on; blacklisting only one leaves the other
# able to load.  modprobe -r evicts any already-loaded instance immediately.
echo "blacklist dvb_usb_rtl28xxu" > /etc/modprobe.d/rtl-sdr-blacklist.conf
echo "blacklist rtl2832"         >> /etc/modprobe.d/rtl-sdr-blacklist.conf
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
modprobe -r rtl2832            2>/dev/null || true
cat > /etc/udev/rules.d/20-rtlsdr.rules << 'UDEV'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", \
  GROUP="plugdev", MODE="0666", SYMLINK+="rtl_sdr"
UDEV
ok "RTL-SDR udev rules set"

# u-blox USB GPS udev rule — stable /dev/ttyGPS symlink for NEO-M9N
# Vendor 1546 = u-blox, Product 01a9 = NEO-M9N
cat > /etc/udev/rules.d/21-ublox-gps.rules << 'UDEV'
SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", \
  SYMLINK+="ttyGPS", MODE="0666"
UDEV
ok "u-blox GPS udev rule set (/dev/ttyGPS symlink for NEO-M9N)"

udevadm control --reload-rules
udevadm trigger

# Check the RTL-SDR serials against what this deploy expects. Listing works
# even while dump1090 or the node agent has a dongle open.
RTL_LIST="$(timeout 6 rtl_test -t 2>&1 | grep -E '^ +[0-9]+: ' || true)"
RTL_COUNT="$(printf '%s' "$RTL_LIST" | grep -c 'SN:' || true)"
if [[ "$RTL_COUNT" -gt 0 ]]; then
  info "RTL-SDRs found:"; printf '%s\n' "$RTL_LIST" | sed 's/^/      /'
  DUP="$(printf '%s\n' "$RTL_LIST" | sed -n 's/.*SN: *//p' | sort | uniq -d)"
  if [[ "$RTL_COUNT" -ge 2 && -n "$DUP" ]]; then
    warn "Two or more RTL-SDRs share serial '${DUP}'. They can't be told apart until"
    warn "each has its own: stop kestrel-node and dump1090-fa, then per dongle"
    warn "  rtl_eeprom -d <index> -s <SERIAL>   and unplug/replug.  See docs/adsb.md"
  fi
  for _want in "$SDR_SERIAL" "$ADSB_SERIAL"; do
    if [[ -n "$_want" ]] && ! printf '%s' "$RTL_LIST" | grep -q "SN: ${_want}\$"; then
      warn "No RTL-SDR with serial '${_want}' connected (yet): set it with rtl_eeprom, then replug"
    fi
  done
  if printf '%s' "$RTL_LIST" | grep -q "Blog V4" && grep -q "VERSION_CODENAME=bookworm" /etc/os-release; then
    warn "RTL-SDR Blog V4 on Bookworm: its rtl-sdr 0.6.0 can't drive the V4's tuner."
    warn "Use Raspberry Pi OS Trixie (rtl-sdr 2.0.2) for the V4."
  fi
fi

# UART-specific setup — only needed for legacy UART GPS (mode=uart)
if [[ "$GPS_MODE" == "uart" ]]; then
  info "Configuring UART for legacy GPS…"
  for CFG in /boot/firmware/config.txt /boot/config.txt; do
    [[ -f "$CFG" ]] || continue
    grep -q "enable_uart=1" "$CFG" || echo "enable_uart=1" >> "$CFG"
    ok "UART enabled in $CFG"
    break
  done
  raspi-config nonint do_serial_cons 1 2>/dev/null && ok "Serial console disabled" || \
    warn "Could not disable serial console via raspi-config — do it manually"
else
  info "USB GPS mode — UART configuration skipped"
fi

# ── 3/8  Fetch code ───────────────────────────────────────────────────────
step 3 "Fetching KESTREL platform code"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Updating existing install at $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" reset --hard origin/main
else
  info "Cloning from $REPO_URL"
  git clone "$REPO_URL" "$INSTALL_DIR" --depth 1
fi
ok "Code ready at $INSTALL_DIR"

# ── 4/8  Python venv ─────────────────────────────────────────────────────
step 4 "Python virtual environment"
python3 -m venv "$INSTALL_DIR/venv" --upgrade-deps
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install \
  -r "$INSTALL_DIR/kestrel-node/requirements.txt" -q
ok "Python dependencies installed"

# pyrtlsdr 0.2.x uses a bare 'import pkg_resources' which is absent from
# Python 3.13 venvs by default.  Patch it in-place; idempotent if already done.
bash "$INSTALL_DIR/scripts/patch-pyrtlsdr.sh" "$INSTALL_DIR/venv"

# Confirm pyrtlsdr imports cleanly before the node starts.
# librtlsdr.so must be present (installed above in step 1).
if ! "$INSTALL_DIR/venv/bin/python" -c "import rtlsdr" 2>/dev/null; then
  fail "pyrtlsdr failed to import — check librtlsdr-dev is installed and the patch ran cleanly"
fi
ok "RTL-SDR Python binding OK"

# The venv is isolated from system packages, and SoapySDR's Python binding only
# ships as an apt package. Link just that module into the venv (the same
# python3 built both, so the compiled extension matches).
if [[ "$HACKRF_OK" == true ]]; then
  SOAPY_DIR="$(python3 -c 'import SoapySDR, os; print(os.path.dirname(SoapySDR.__file__))' 2>/dev/null || true)"
  VENV_SITE="$("$INSTALL_DIR/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  if [[ -n "$SOAPY_DIR" ]]; then
    ln -sf "$SOAPY_DIR/SoapySDR.py" "$VENV_SITE/SoapySDR.py"
    for so in "$SOAPY_DIR"/_SoapySDR*.so; do
      [[ -e "$so" ]] && ln -sf "$so" "$VENV_SITE/$(basename "$so")"
    done
  fi
  if "$INSTALL_DIR/venv/bin/python" -c "import SoapySDR" 2>/dev/null; then
    ok "SoapySDR Python binding OK (HackRF)"
  else
    warn "SoapySDR won't import in the venv, so HackRF scanning will be skipped."
    warn "python3-soapysdr is built for the distro python3; the venv must use that same python3."
  fi
fi

# ── 5/8  Configuration ────────────────────────────────────────────────────
if [[ "$GPS_MODE" == "uart" ]]; then
  GPS_PORT="/dev/ttyAMA0"
else
  GPS_PORT="/dev/ttyGPS"   # stable symlink from 21-ublox-gps.rules
fi
step 5 "Writing node configuration"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
if [[ -f "$CONFIG_DIR/config.yaml" ]]; then
  CONFIG_BAK="$CONFIG_DIR/config.yaml.bak-$(date +%Y%m%d-%H%M%S)"
  cp -p "$CONFIG_DIR/config.yaml" "$CONFIG_BAK"
  info "Existing config backed up to $CONFIG_BAK"
fi
if [[ -n "$SDR_SERIAL" ]]; then
  SDR_SELECT="serial: ${SDR_SERIAL}        # selected by serial; stable across reboots"
else
  SDR_SELECT="device_index: 0
    # serial: KESTREL       # use a serial once a second RTL-SDR (ADS-B) is attached; see docs/adsb.md"
fi

cat > "$CONFIG_DIR/config.yaml" << YAML
# KESTREL Node Configuration
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Node: ${NODE_ID}

node_id: "${NODE_ID}"
site_name: "default"

mqtt:
  host: "${SERVER_IP}"
  host_fallback: "${MQTT_FALLBACK}"
  port: 1883
  connect_timeout_s: 10
  user: "${MQTT_USER}"
  password: "${MQTT_PASS}"
  tls: false
  topic_prefix: "kestrel"

gps:
  serial_port: "${GPS_PORT}"   # /dev/ttyGPS = udev symlink to the u-blox NEO-M9N
  baud: 38400
  mode: "${GPS_MODE}"
  auto_detect: false         # set true only if port assignment is unknown

wifi:
  enabled_2g: true
  interface_2g: "${WIFI_IFACE}"
  enabled_5g: ${WIFI_5G_STR}
  interface_5g: "${WIFI_IFACE_5G:-wlan2}"

bt:
  enabled: true
  hci_index: 1              # nRF52840 dongle — Pi onboard BT is hci0
  coded_phy: true
  backend: auto             # direct HCI access to the dongle, bleak fallback

# SDR radios — each entry is an independent receiver with its own sweep.
# The RTL-SDR covers the sub-GHz control bands; the HackRF covers 2.4/5.8 GHz
# (set by --enable-hackrf). Band codes: 433, 900, 1g2, 2g4, 5g8.
sdrs:
  - enabled: ${SDR_STR}
    backend: rtlsdr
    ${SDR_SELECT}
    bands: ["900", "1g2", "433"]
  - enabled: ${HACKRF_STR}
    backend: hackrf
    device_index: 0
    bands: ["2g4", "5g8"]

# ADS-B — crewed aircraft via dump1090-fa on a dedicated 1090 MHz RTL-SDR.
# dump1090-fa picks its dongle via RECEIVER_SERIAL in /etc/default/dump1090-fa.
adsb:
  enabled: ${ADSB_STR}
  source_url: "http://127.0.0.1:8080/data/aircraft.json"

heartbeat_interval_s: 10
dedup_window_s: 2.0
max_queue_size: 1000
YAML
chmod 640 "$CONFIG_DIR/config.yaml"
ok "Config written to $CONFIG_DIR/config.yaml"

# ── 6/8  GPS and network ownership ───────────────────────────────────────
step 6 "GPS and network ownership"

# The KESTREL agent talks to the u-blox receiver directly over serial (UBX
# NAV-PVT).  gpsd must NOT run: it opens the same port, steals bytes and
# reconfigures the receiver's protocol output.  Remove/mask it if present.
if systemctl list-unit-files 2>/dev/null | grep -q '^gpsd'; then
  systemctl disable --now gpsd.socket gpsd.service 2>/dev/null || true
  systemctl mask gpsd.socket gpsd.service 2>/dev/null || true
  ok "gpsd disabled and masked (agent owns the GPS port)"
else
  ok "gpsd not installed (agent owns the GPS port)"
fi

# NetworkManager must leave the monitor-mode adapters alone, but keep
# managing eth0.  Unmanaging wlan1/wlan2 replaces 'airmon-ng check kill',
# which stopped NetworkManager entirely and could drop the node's wired IP.
if [[ -d /etc/NetworkManager/conf.d ]]; then
  NM_IFACES="interface-name:${WIFI_IFACE}"
  [[ -n "$WIFI_IFACE_5G" ]] && NM_IFACES="${NM_IFACES};interface-name:${WIFI_IFACE_5G}"
  cat > /etc/NetworkManager/conf.d/99-kestrel-monitor.conf << NMCONF
[keyfile]
unmanaged-devices=${NM_IFACES}
NMCONF
  systemctl reload NetworkManager 2>/dev/null || true
  ok "NetworkManager will ignore ${WIFI_IFACE}${WIFI_IFACE_5G:+ and ${WIFI_IFACE_5G}}"
fi

# Post-install GPS device check
if [[ "$GPS_MODE" != "uart" ]]; then
  echo ""
  if [[ -e "/dev/ttyACM0" || -e "/dev/ttyGPS" ]]; then
    ok "GPS device found: $(ls /dev/ttyACM0 /dev/ttyGPS 2>/dev/null | head -1)"
  else
    warn "GPS device not found at /dev/ttyACM0 or /dev/ttyGPS"
    warn "Plug in the u-blox NEO-M9N USB receiver, then run:"
    warn "  ls /dev/ttyACM* /dev/ttyGPS"
    warn "  sudo systemctl restart kestrel-node"
  fi
fi

# ── 7/8  systemd service ──────────────────────────────────────────────────
step 7 "systemd service"
cp "$INSTALL_DIR/kestrel-node/systemd/kestrel-node.service" /etc/systemd/system/
chmod +x "$INSTALL_DIR/kestrel-node/systemd/setup_interfaces.sh"

# Patch install dir and config path if non-default location
sed -i \
  -e "s|/opt/kestrel-node|${INSTALL_DIR}|g" \
  -e "s|KESTREL_CONFIG=.*\"|KESTREL_CONFIG=${CONFIG_DIR}/config.yaml\"|" \
  /etc/systemd/system/kestrel-node.service

systemctl daemon-reload
systemctl enable kestrel-node.service --quiet
ok "kestrel-node.service installed and enabled"

# ── 8/8  Hostname ─────────────────────────────────────────────────────────
step 8 "Hostname"
NEW_HOST=$(echo "$NODE_ID" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
if [[ "$(hostname)" != "$NEW_HOST" ]]; then
  hostnamectl set-hostname "$NEW_HOST" 2>/dev/null && ok "Hostname → $NEW_HOST" || \
    warn "Could not set hostname — do manually: sudo hostnamectl set-hostname $NEW_HOST"
else
  ok "Hostname already set ($NEW_HOST)"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           Deployment Complete!                ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Node ID    :${NC} ${NODE_ID}"
echo -e "  ${GREEN}Server     :${NC} ${SERVER_IP}"
echo -e "  ${GREEN}Wi-Fi 2.4G :${NC} ${WIFI_IFACE}"
if [[ -n "$WIFI_IFACE_5G" ]]; then
  echo -e "  ${GREEN}Wi-Fi 5G   :${NC} ${WIFI_IFACE_5G}"
fi
[[ "$SDR_STR"  == "true" ]] && echo -e "  ${GREEN}SDR        :${NC} RTL-SDR ${SDR_SERIAL:-(device 0)}"
[[ "$ADSB_STR" == "true" ]] && echo -e "  ${GREEN}ADS-B      :${NC} dump1090-fa ${ADSB_SERIAL:+on serial ${ADSB_SERIAL}}"
[[ -n "${CONFIG_BAK:-}" ]] && echo -e "  ${GREEN}Old config :${NC} ${CONFIG_BAK}"
echo -e "  ${GREEN}Service    :${NC} kestrel-node  (auto-starts on boot)"
echo -e "  ${GREEN}Config     :${NC} ${CONFIG_DIR}/config.yaml"
echo -e "  ${GREEN}Logs       :${NC} journalctl -fu kestrel-node"
echo ""
if [[ "$GPS_MODE" == "uart" ]]; then
  echo -e "  ${YELLOW}⚠  A reboot is required to activate UART for GPS.${NC}"
  echo ""
fi
echo "  Post-deployment verification:"
echo "    sudo systemctl status kestrel-node"
echo "    sudo journalctl -fu kestrel-node"
echo "    (dashboard → System: each radio's live status)"
echo ""

if [[ "$UNATTENDED" == false ]]; then
  echo ""
  echo -e "  ${YELLOW}Tip: The Pi runs warm under load. An active cooler is strongly${NC}"
  echo -e "  ${YELLOW}recommended if the node will be deployed in an enclosure.${NC}"
  read -rp "  Reboot now? [Y/n]: " RB
  [[ "${RB,,}" == "n" ]] || reboot
fi
