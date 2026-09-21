"""Links to map services and the URLs of media files (standard library only).

- `map_links` builds the links to Google, Yandex and Apple maps from the name,
  the address, the coordinates and the ids of a place;
- `venue_coordinates` and `format_coordinate` write coordinates as text;
- `media_url` is the URL of a file in the media directory.

Pure functions, no input/output.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import quote

from tools._data import _is_finite

YANDEX_MAPS_ZOOM = 16


def _optional_text(value: Any) -> str:
    """Optional text field -> "" when it is absent, null or blank."""
    return value if isinstance(value, str) and value.strip() else ""


def format_coordinate(value: int | float) -> str:
    """Decimal notation without an exponent: 1e-05 -> '0.00001', 10.0 -> '10'."""
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def venue_coordinates(geo: Any) -> tuple[str, str] | None:
    """(`lat`, `lng`) as text, or None when the coordinates are not set.

    `lat = 0, lng = 0` is the placeholder of the data schema, not a location.
    """
    if not isinstance(geo, dict):
        return None
    lat, lng = geo.get("lat"), geo.get("lng")
    for value, limit in ((lat, 90.0), (lng, 180.0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not _is_finite(value) or abs(value) > limit:
            return None
    if lat == 0 and lng == 0:
        return None
    return format_coordinate(lat), format_coordinate(lng)


def _url_component(text: str) -> str:
    """Percent-encode text for a URL (UTF-8; a space becomes %20)."""
    return quote(text, safe="")


def map_links(name: str, address: str, geo: Any, maps: Any) -> dict[str, str]:
    """Ready-made map URLs; "" when there is no data for a service.

    * Google: the place id when it is set (`query` is required by the URL
      format and only used as a fallback: the name, else the coordinates, else
      the address), otherwise the coordinates;
    * Yandex: the organisation id when it is set, otherwise the coordinates
      (longitude first);
    * Apple: the coordinates, labelled with the name.
    """
    maps = maps if isinstance(maps, dict) else {}
    name = name.strip()
    place_id = _optional_text(maps.get("googlePlaceId")).strip()
    org_id = _optional_text(maps.get("yandexOrgId")).strip()
    coordinates = venue_coordinates(geo)
    lat, lng = coordinates if coordinates else ("", "")

    google = yandex = apple = ""
    if place_id:
        query = (
            _url_component(name)
            if name
            else f"{lat},{lng}"
            if coordinates
            else _url_component(address.strip())
        )
        if query:
            google = (
                "https://www.google.com/maps/search/?api=1"
                f"&query={query}&query_place_id={_url_component(place_id)}"
            )
    elif coordinates:
        google = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"

    if org_id:
        yandex = f"https://yandex.ru/maps/org/{_url_component(org_id)}"
    elif coordinates:
        yandex = f"https://yandex.ru/maps/?pt={lng},{lat}&z={YANDEX_MAPS_ZOOM}"

    if coordinates:
        apple = f"https://maps.apple.com/?ll={lat},{lng}"
        if name:
            apple += f"&q={_url_component(name)}"
    return {"google": google, "yandex": yandex, "apple": apple}


def has_map_links(links: dict[str, str]) -> bool:
    return any(links.values())


def media_url(media_path: str, name: str) -> str:
    """Root-absolute URL of a media file; "" when the file is not set."""
    return f"{media_path}/{_url_component(name)}" if name else ""
