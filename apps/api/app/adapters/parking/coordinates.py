"""Coordinate conversions used by official Singapore government datasets."""

from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer


@lru_cache(maxsize=1)
def _svy21_transformer() -> Transformer:
    # EPSG:3414 is SVY21 / Singapore TM; EPSG:4326 is WGS84.
    return Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)


def svy21_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Return (latitude, longitude), never (easting, northing)."""

    longitude, latitude = _svy21_transformer().transform(easting, northing)
    return float(latitude), float(longitude)
