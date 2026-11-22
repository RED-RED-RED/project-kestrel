# ADS-B (crewed-aircraft awareness)

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

Once you have more than one RTL-SDR (the KESTREL sub-GHz scanner plus the 1090
dongle), device index order is **not stable** across reboots, so dump1090 and
the node scanner can grab each other's dongle. Give each a unique serial once:

```bash
# list what's attached and their current serials
rtl_test        # Ctrl-C after it prints the device list

# write serials (do one dongle at a time; -d is the current index)
rtl_eeprom -d 0 -s KESTREL   # the sub-GHz scanner dongle
rtl_eeprom -d 1 -s ADSB1090  # the 1090 dongle
# replug the dongles afterward
```

Then point each tool at its dongle by serial:

- **dump1090-fa** — edit `/etc/default/dump1090-fa`, set:
  ```
  RECEIVER_OPTIONS="--device ADSB1090 --gain -10 --ppm 0 --net-bo-port 30005 --fix"
  ```
  then `sudo systemctl restart dump1090-fa`.
- **KESTREL sub-GHz scanner** — the `sdrs:` entry's `device_index` still selects
  by index; with serials set, confirm it opens the `KESTREL` dongle (the log
  prints which device it opened on start).

## Enable it

1. Install dump1090-fa (the deploy script does this with `--enable-adsb`, or
   install it manually from the FlightAware apt repo).
2. In `/etc/kestrel-node/config.yaml`:
   ```yaml
   adsb:
     enabled: true
     source_url: "http://127.0.0.1:8080/data/aircraft.json"
   ```
3. `sudo systemctl restart kestrel-node`.

## Verify

```bash
# dump1090 is decoding (should list aircraft)
curl -s http://127.0.0.1:8080/data/aircraft.json | head

# the node is forwarding them
journalctl -u kestrel-node -f | grep -i adsb
```

You should see the ADS-B scanner start, and aircraft appear on the dashboard's
aircraft layer. A low, nearby aircraft also raises a deconfliction advisory.
