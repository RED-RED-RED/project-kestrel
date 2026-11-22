"""
analysis/agl.py
===============
One place that decides "how high above the ground is this thing", so the map,
alerts, threat scoring and flight history all agree.

Datums (why the subtraction is valid):
  * Remote ID geodetic altitude is WGS-84 height above ellipsoid (HAE).
  * ADS-B geometric altitude (dump1090 alt_geom) is WGS-84 HAE.
  * A u-blox node reports its own height as HAE (UBX-NAV-PVT / survey-in).
So target HAE minus node HAE is height above the node's ground. A node on the
NMEA path reports MSL instead; that leaves a geoid offset (~27 m in eastern
Nebraska) in the estimate.

The estimate assumes the ground under the target is about the same elevation
as the ground under the node. Good near a node, rougher with distance.
"""

from __future__ import annotations

from typing import Optional

FT_PER_M = 3.28084


def _valid(v) -> bool:
    # ODID "unknown" decodes to 0.0 on the node, so 0.0 is treated as absent.
    return v is not None and v != 0.0


def resolve_drone_agl(
    height_agl: Optional[float],
    alt_geo: Optional[float],
    ground_alt_m: Optional[float],
) -> tuple[Optional[float], Optional[str]]:
    """
    Best AGL for a Remote ID drone, in metres, plus where it came from.

      "rid" -> the drone's own height field (above takeoff or ground).
      "est" -> geodetic altitude minus the detecting node's ground elevation.
      (None, None) -> nothing usable.
    """
    if _valid(height_agl):
        return round(float(height_agl), 1), "rid"
    if _valid(alt_geo) and _valid(ground_alt_m):
        return round(max(float(alt_geo) - float(ground_alt_m), 0.0), 1), "est"
    return None, None


def resolve_aircraft_agl_ft(
    alt_geom_ft: Optional[float],
    alt_baro_ft: Optional[float],
    ground_alt_m: Optional[float],
) -> tuple[Optional[float], Optional[str]]:
    """
    AGL for an ADS-B aircraft, in feet, relative to the hearing node's ground.

      "geo"  -> GNSS altitude (same datum as the node; preferred).
      "baro" -> pressure altitude (uncorrected for local pressure; rougher).
    Ground is taken as 0 when the node has no altitude yet.
    """
    ground_ft = (ground_alt_m or 0.0) * FT_PER_M
    if alt_geom_ft is not None:
        return round(float(alt_geom_ft) - ground_ft), "geo"
    if alt_baro_ft is not None:
        return round(float(alt_baro_ft) - ground_ft), "baro"
    return None, None
