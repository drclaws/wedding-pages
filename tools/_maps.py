"""Links to map services and the URLs of media files (standard library only).

- `map_links` builds the links to Google, Yandex and Apple maps from the ids
  and the text queries of a place (the name, the address and the coordinates
  only complete them);
- `place_coordinates` and `format_coordinate` write coordinates as text;
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


def place_coordinates(geo: Any) -> tuple[str, str] | None:
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
    """Ready-made map URLs; "" when the place is not set for a service.

    A service gets a link only when `maps` names the place for it: by its
    id, else by its own text query.  The coordinates alone give no link; they
    only narrow down a text query to the area (and help Apple find a place
    whose id it does not know).

    * Google: `googlePlaceId` (the URL format requires `query` too, used only
      when the id is not found: `googleQuery`, else the name, else the
      coordinates, else the address), else `googleQuery`;
    * Yandex: `yandexOrgId` (an organisation) or `yandexGeoId` (a building or
      another map object), else `yandexQuery` near the coordinates;
    * Apple: `applePlaceId` (with the coordinates and the name), else
      `appleQuery` near the coordinates.
    """
    maps = maps if isinstance(maps, dict) else {}

    def field(key: str) -> str:
        return _optional_text(maps.get(key)).strip()

    name = name.strip()
    coordinates = place_coordinates(geo)
    lat, lng = coordinates if coordinates else ("", "")

    google = yandex = apple = ""
    google_id, google_query = field("googlePlaceId"), field("googleQuery")
    if google_id:
        query = (
            _url_component(google_query or name)
            if google_query or name
            else f"{lat},{lng}"
            if coordinates
            else _url_component(address.strip())
        )
        if query:
            google = (
                "https://www.google.com/maps/search/?api=1"
                f"&query={query}&query_place_id={_url_component(google_id)}"
            )
    elif google_query:
        google = f"https://www.google.com/maps/search/?api=1&query={_url_component(google_query)}"

    org_id, geo_id, yandex_query = field("yandexOrgId"), field("yandexGeoId"), field("yandexQuery")
    if org_id:
        yandex = f"https://yandex.ru/maps/org/{_url_component(org_id)}"
    elif geo_id:
        yandex = f"https://yandex.ru/maps/geo/{_url_component(geo_id)}/"
    elif yandex_query:
        yandex = f"https://yandex.ru/maps/?text={_url_component(yandex_query)}"
        if coordinates:
            yandex += f"&ll={lng},{lat}&z={YANDEX_MAPS_ZOOM}"

    apple_id, apple_query = field("applePlaceId"), field("appleQuery")
    if apple_id:
        apple = f"https://maps.apple.com/place?place-id={_url_component(apple_id)}"
        if coordinates:
            apple += f"&coordinate={lat},{lng}"
        if name:
            apple += f"&name={_url_component(name)}"
    elif apple_query:
        apple = f"https://maps.apple.com/search?query={_url_component(apple_query)}"
        if coordinates:
            apple += f"&center={lat},{lng}"
    return {"google": google, "yandex": yandex, "apple": apple}


def has_map_links(links: dict[str, str]) -> bool:
    return any(links.values())


def media_url(media_path: str, name: str) -> str:
    """Root-absolute URL of a media file; "" when the file is not set."""
    return f"{media_path}/{_url_component(name)}" if name else ""
