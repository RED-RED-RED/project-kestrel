# ADS-B (crewed-aircraft awareness)

> **Requires a second RTL-SDR.** ADS-B needs its own dongle pinned to 1090 MHz
> full-time. It cannot share the RTL-SDR that runs the sub-GHz band scan. Until
> that second radio is installed, leave `adsb.enabled: false`; the dashboard's
> ADS-B layer just shows `ON · 0` and nothing alerts.

KESTREL can show crewed aircraft alongside drones by ingesting ADS-B. The node
runs `dump1090-fa` (the standard 1090 MHz Mode S / ADS-B decoder) on its own
dedicated RTL-SDR; the node agent reads dump1090's `aircraft.json` and
republishes a compact aircraft list to `kestrel/<node>/aircraft`.

Aircraft are **informational, not threats** — they never enter the drone
threat/alert pipeline. The one exception is a **low, nearby aircraft**: one
estimated under ~400 ft AGL within the node's area raises a separate
deconfliction advisory, since that is the drone operating band.

## Hardware

- A **dedicated RTL-SDR** for 1090 MHz (dump1090 needs it full-time; you can't
  share it with the KESTREL sub-GHz scanner).
- A **1090 MHz antenna** (a tuned ADS-B antenna, or any whip — 1090 MHz is a
  short quarter-wave). This is a good use of the enclosure's spare connector.

## Multiple RTL-SDRs: pin them by serial

With two RTL-SDRs (the KESTREL sub-GHz scanner plus the 1090 dongle), device
index order is **not stable** across reboots, so dump1090 and the node scanner
can grab each other's dongle. Most dongles ship with the same serial
(`00000001`), so give each its own, once. It's stored on the dongle, so it
survives reboots, reimaging and moving it to another node.

```bash
sudo systemctl stop kestrel-node dump1090-fa
rtl_test -t      # lists each dongle: index, model, serial
```

If two show the same serial, write new ones. Different models (say a Nooelec
and an RTL-SDR Blog V4) can be told apart by name in that list, so both can stay
plugged in; with two identical dongles, unplug one first so `-d 0` is certain.

```bash
rtl_eeprom -d 0 -s ADSB1090   # the 1090 dongle (answer y)
rtl_eeprom -d 1 -s KESTREL    # the sub-GHz scanner dongle (answer y)
```

**Unplug and replug both**: the serial is only read when a dongle connects.
`rtl_test -t` should now show the new serials. (The `PLL not locked` / `No E4000
tuner found` lines `-t` prints are normal: that test is for a different tuner.)

The deploy script does the next two steps for you with
`--sdr-serial KESTREL --adsb-serial ADSB1090`. By hand:

- **dump1090-fa**: in `/etc/default/dump1090-fa` set
  ```
  RECEIVER_SERIAL=ADSB1090
  ```
  then `sudo systemctl restart dump1090-fa`.
- **KESTREL sub-GHz scanner**: add `serial:` to its `sdrs:` entry in
  `/etc/kestrel-node/config.yaml` (serial wins over `device_index`):
  ```yaml
  sdrs:
    - enabled: true
      backend: rtlsdr
      serial: KESTREL
      bands: ["900", "1g2", "433"]
  ```
  then `sudo systemctl restart kestrel-node`. The log confirms it:
  `RTL-SDR open: serial KESTREL (index N)`. An unknown serial fails loudly
  instead of silently grabbing the 1090 dongle.

## Enable it

Deploying the node with `--enable-adsb` (plus the serial flags above) does all
of this. By hand:

1. Install dump1090-fa from FlightAware's apt repo:
   ```bash
   wget https://www.flightaware.com/adsb/piaware/files/packages/pool/piaware/f/flightaware-apt-repository/flightaware-apt-repository_1.3_all.deb
   sudo dpkg -i flightaware-apt-repository_1.3_all.deb
   sudo apt update && sudo apt install -y dump1090-fa
   ```
   It starts straight away and opens the first RTL-SDR it finds, so set
   `RECEIVER_SERIAL` (above) before relying on it.
2. In `/etc/kestrel-node/config.yaml`:
   ```yaml
   adsb:
     enabled: true
     source_url: "http://127.0.0.1:8080/data/aircraft.json"
   ```
3. `sudo systemctl restart kestrel-node`.

## Verify

```bash
# dump1090 opened the right dongle
journalctl -u dump1090-fa -n 20 --no-pager      # "using device ... SN ADSB1090"

# it's decoding: run twice a few seconds apart, messages should climb
curl -s http://127.0.0.1:8080/data/aircraft.json | jq '{messages, aircraft: (.aircraft | length)}'

# the node is forwarding them (scanners start up to 60 s after boot, waiting for GPS)
journalctl -u kestrel-node -n 60 --no-pager | grep -iE "ADS-B|RTL-SDR open"
```

The dashboard's **System** view shows the ADS-B radio with messages per minute
and signal vs noise; aircraft appear on the map's ADS-B layer. A low, nearby
aircraft also raises a deconfliction advisory.

### Troubleshooting

| Symptom | Meaning |
|---|---|
| `aircraft.json` returns 404 | dump1090-fa isn't running, or can't find its dongle: check `journalctl -u dump1090-fa` |
| `usb_claim_interface error -6` from `rtl_test` | Another program has that dongle open (normally dump1090 or the node). Expected while they run |
| Node log: `No RTL-SDR with serial 'KESTREL'. Connected serials: ...` | Serial not set yet, or the dongle wasn't replugged after `rtl_eeprom` |
| Messages barely climb, signal ≈ noise | Nothing is reaching the antenna. 1090 MHz is line-of-sight: indoors, and especially a basement, gets little or nothing. Try a window, or mount the antenna high outside |
| RTL-SDR Blog V4 tunes badly | It needs rtl-sdr 2.0.2 (Raspberry Pi OS Trixie). Bookworm's 0.6.0 doesn't support its tuner |

## How AGL is worked out

Every altitude in the dashboard is height above ground (AGL).

- **Aircraft:** GNSS altitude (`alt_geom`) minus the hearing node's ground
  elevation. Both are WGS-84 ellipsoid heights, so the subtraction is clean. If
  an aircraft only sends pressure altitude, that's used instead and marked
  `baro` (rougher: uncorrected for local pressure). Shown as `~` because it
  assumes the ground under the aircraft matches the node's; that holds near
  the node, which is where the advisory looks.
- **Drones:** the Remote ID height field when the drone sends one. Remote ID
  says whether that height is above the ground or above the takeoff point;
  takeoff-referenced heights are labeled "above takeoff" in the map popup and
  contact tooltip (they read low after a roof launch). With no height field,
  geodetic altitude minus the detecting node's ground, shown with `~`.

The node's ground elevation comes from its GPS survey, so a node that hasn't
got a fix yet can't produce estimates (the field shows `—`).

## Advisory thresholds

Settings → **Airspace (ADS-B)**, applied live with no redeploy:

| Setting | Default | Range |
|---|---|---|
| Alert below (ft AGL) | 400 | 50 to 5000 |
| Within radius of a node (km) | 8 | 0.5 to 50 |
| Re-alert same aircraft after (s) | 120 | 10 to 3600 |

Out-of-range values are clamped to the nearest bound.
