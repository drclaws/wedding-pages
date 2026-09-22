"""Checks of the data format 2: `site.json` and `invitations.json`.

`check_site` and `check_invitations` report every problem of the documents to
a report object with the methods `error(text)` and `warning(text)` (`warn`
is accepted as well); `check_data` runs both.  `check_media_files` turns the
facts of the media files (`tools._media.inspect`) into messages.  Whether
the media files exist is checked by the caller.

Messages have the form `<where>: field '<path>' <problem>` and never quote
the data.  Ids from `site.json` are shown when they follow the id rule.  In
the invitations a key or an id is shown only when it is a field name of the
format or an id declared in `site.json`: such a key may be somebody's name,
so every other key is replaced by a neutral label.  One broken thing gives
one message: the references of the invitations to sections are not checked
when a section has a broken id or type, and not at all when `site.json` is
not in the format 2.

Standard library only; no input/output.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from tools import _data, _media
from tools._data import (
    FORMS,
    ID_RULE,
    IMAGE_EXTENSIONS,
    PathPart,
    did_you_mean,
    format_path,
    invitation_label,
    is_identifier,
    json_type,
    show_id,
)
from tools._dates import DEFAULT_EVENT_DURATION, parse_date_iso

SCHEMA_VERSION = 2
SITE_FILE = "site.json"
INVITATIONS_FILE = "invitations.json"

#: Types of sections, widgets and media items; each has a fragment of its own.
SECTION_TYPES = ("cover", "custom")
WIDGET_TYPES = ("text", "date", "events", "location", "schedule", "media")
MEDIA_TYPES = ("image", "video")
#: A video is published as one file that every browser plays.
VIDEO_EXTENSIONS = frozenset({".mp4"})

#: Values a text may insert, in the order they are listed in messages.
PLACEHOLDERS = (
    "coupleNames",
    "greeting",
    "rsvpDeadline",
    "eventTitle",
    "eventDate",
    "eventTime",
    "eventPlace",
)

SITE_FIELDS = (
    "schemaVersion",
    "coupleNames",
    "rsvpDeadline",
    "mediaDir",
    "mainEvent",
    "sections",
    "events",
    "locations",
    "schedules",
    "media",
    "texts",
    "linkPreview",
)
#: Fields of `linkPreview`: what the services that show link previews read.
LINK_PREVIEW_FIELDS = ("description",)
#: Fields of the old format and where their contents live now.
V1_SITE_HINTS = {
    "dateISO": "the date and time belong to the events now ('events.<id>.start')",
    "dateText": "the build writes the dates from 'events.<id>.start'",
    "outOfTownText": "put the text into a 'text' widget of the section for guests "
    "from other cities",
    "video": "describe the video in 'media' and show it with a 'media' widget",
    "schedule": "programmes live in 'schedules' and belong to an event "
    "('events.<id>.schedule')",
    "venue": "places live in 'locations', the time of each event in 'events'",
}

SECTION_FIELDS = {
    "cover": ("id", "type", "visible", "eyebrow", "background", "backgroundFocus"),
    "custom": ("id", "type", "visible", "title", "titleHidden", "width", "align", "widgets"),
}
SECTION_WIDTHS = ("narrow", "wide")
SECTION_ALIGNS = ("start", "center")
#: Which part of the photo behind the cover stays in view when the screen
#: crops it (the first one is the default).
COVER_FOCUSES = (
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
)
#: A photo behind the cover larger than this gets a warning: it is loaded
#: first, before anything else of the page.  TEMP: the size is not final.
COVER_BACKGROUND_WARN_BYTES = 1024 * 1024

WIDGET_BASE_FIELDS = ("type", "id", "visible")
WIDGET_FIELDS = {
    "text": ("text", "variant"),
    "date": ("event", "countdown", "calendar"),
    "events": ("events",),
    "location": ("location", "variant"),
    "schedule": ("schedule",),
    "media": ("items", "layout"),
}
WIDGET_VARIANTS = {
    "text": ("body", "lead", "signature"),
    "location": ("full", "compact"),
}
MEDIA_LAYOUTS = ("grid", "ribbon", "single")

EVENT_FIELDS = (
    "title", "location", "start", "end", "showEnd", "schedule", "description", "visible"
)
LOCATION_FIELDS = (
    "ready",
    "name",
    "address",
    "description",
    "geo",
    "maps",
    "photos",
    "directions",
)
V1_LOCATION_HINTS = {"directionsImage": "use 'directions' with the id of a media item"}
GEO_FIELDS = ("lat", "lng")
GEO_LIMITS = {"lat": 90.0, "lng": 180.0}
_LETTERS_DIGITS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_MAP_ID_RULES = {
    "googlePlaceId": (
        _LETTERS_DIGITS | frozenset("_-"),
        "may only contain A-Z, a-z, 0-9, '_' and '-'",
    ),
    "yandexOrgId": (frozenset("0123456789"), "may only contain digits"),
    "yandexGeoId": (frozenset("0123456789"), "may only contain digits"),
    "applePlaceId": (_LETTERS_DIGITS, "may only contain A-Z, a-z and 0-9"),
}
#: A text query to a map service: what a guest would type in its search box.
MAP_QUERY_FIELDS = ("googleQuery", "yandexQuery", "appleQuery")
MAP_QUERY_MAX_LENGTH = 200
MAPS_FIELDS = (
    "googlePlaceId",
    "googleQuery",
    "yandexOrgId",
    "yandexGeoId",
    "yandexQuery",
    "applePlaceId",
    "appleQuery",
)
_MAPS_PROBLEMS = {
    "query": "is not supported: a text query belongs to one map service, "
    "use 'googleQuery', 'yandexQuery' or 'appleQuery'",
}
SCHEDULE_ITEM_FIELDS = ("time", "title", "text")
MEDIA_FIELDS = {
    "image": ("type", "file", "thumb", "width", "height", "alt"),
    "video": ("type", "file", "poster", "thumb", "width", "height", "alt"),
}
#: A tile has a fixed proportion and shows no text under it.
_CAPTION_HINT = "is not supported: a tile shows no caption; describe the picture in 'alt'"
MEDIA_HINTS = {
    "image": {"poster": "is not allowed: only a video has a poster", "caption": _CAPTION_HINT},
    "video": {"caption": _CAPTION_HINT},
}
#: Largest difference between the proportions of a video and its poster.
RATIO_TOLERANCE = 0.01

INVITATION_FIELDS = ("token", "greeting", "form", "primaryEvent", "events", "sections")
V1_INVITATION_HINTS = {
    "ty": 'use "form": "ty"',
    "vy": 'use "form": "vy"',
    "plusOne": 'show the section about a companion: "sections": '
    '{ "<id>": { "visible": true } }',
    "outOfTown": "show the section for guests from other cities: "
    '"sections": { "<id>": { "visible": true } }',
    "note": 'a personal note belongs to a section: "sections": { "<id>": { "note": … } }',
    "travelNote": "put it into the section for guests from other cities: "
    '"sections": { "<id>": { "note": … } }',
}
# default: an invitation may switch sections, the widgets of a section (by
# their id) and events on or off and add notes to sections and events
GUEST_EVENT_FIELDS = ("visible", "note")
GUEST_SECTION_FIELDS = ("visible", "note", "widgets")
GUEST_WIDGET_FIELDS = ("visible",)
#: Every field name of an invitation: always safe to show.
GUEST_FIELD_NAMES = frozenset(
    {*INVITATION_FIELDS, *GUEST_EVENT_FIELDS, *GUEST_SECTION_FIELDS, *V1_INVITATION_HINTS}
)

#: How many known ids a message lists at most.
MAX_LISTED = 8


def _old(hints: Mapping[str, str]) -> dict[str, str]:
    """The problems of the fields of the old format, with their hints."""
    return {name: f"is no longer supported: {hint}" for name, hint in hints.items()}


_V1_SITE_PROBLEMS = _old(V1_SITE_HINTS)
_V1_LOCATION_PROBLEMS = _old(V1_LOCATION_HINTS)
_V1_INVITATION_PROBLEMS = _old(V1_INVITATION_HINTS)


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


#: "No usable value": the field is absent or its problem is reported.
MISSING: Any = _Missing()


def report_warning(report: Any) -> Callable[[str], None]:
    """The warning method of a report: `warning`, else `warn`."""
    method = getattr(report, "warning", None)
    return method if callable(method) else report.warn


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


class _Checker:
    """Messages about one document or one invitation.

    `known` switches on the strict mode of `format_path`: only these keys
    are shown.  `errors` counts the errors reported through the checker.
    """

    def __init__(self, report: Any, where: str, known: Collection[str] | None = None):
        self.report = report
        self.where = where
        self.known = known
        self.errors = 0
        self._warning = report_warning(report)

    def path(self, parts: Sequence[PathPart]) -> str:
        return format_path(parts, self.known)

    def _text(self, parts: Sequence[PathPart], problem: str) -> str:
        subject = f"field '{self.path(parts)}' " if parts else ""
        return f"{self.where}: {subject}{problem}"

    def error(self, parts: Sequence[PathPart], problem: str) -> None:
        self.errors += 1
        self.report.error(self._text(parts, problem))

    def warning(self, parts: Sequence[PathPart], problem: str) -> None:
        self._warning(self._text(parts, problem))

    def raw_error(self, message: str) -> None:
        """A message that is not about one field (already worded)."""
        self.errors += 1
        self.report.error(f"{self.where}: {message}")

    def show(self, value: Any) -> str:
        """An id for a message; in strict mode only a known one."""
        if self.known is not None and value not in self.known:
            return ""
        return show_id(value)

    # -- shapes ------------------------------------------------------------

    def unknown_fields(
        self,
        obj: dict,
        allowed: Sequence[str],
        parts: Sequence[PathPart],
        hints: Mapping[str, str] | None = None,
    ) -> None:
        # default: a misspelt field name is an error, not a warning
        # default: keys that start with "_" are not skipped (no comments in JSON)
        for key in obj:
            if key in allowed:
                continue
            where = (*parts, key)
            hint = (hints or {}).get(key)
            if hint:
                self.error(where, hint)
            else:
                self.errors += 1
                self.report.error(
                    f"{self.where}: unknown field '{self.path(where)}'"
                    f"{did_you_mean(key, allowed)}"
                )

    def object(self, value: Any, parts: Sequence[PathPart]) -> dict | None:
        if isinstance(value, dict):
            return value
        self.error(parts, f"must be an object, got {json_type(value)}")
        return None

    def value(
        self,
        obj: dict,
        key: str,
        kind: str,
        parts: Sequence[PathPart],
        *,
        required: bool = False,
        single_line: bool = False,
    ) -> Any:
        """A field of `kind`: string, boolean, number, count, array, object.

        An empty string counts as "not set".  `null` is never accepted.
        """
        where = (*parts, key)
        if key not in obj:
            if required:
                self.error(where, "is required")
            return MISSING
        value = obj[key]
        expected = _KINDS[kind]
        if not expected[1](value):
            self.error(where, f"must be {expected[0]}, got {json_type(value)}")
            return MISSING
        if kind == "string":
            if not value.strip():
                if required:
                    self.error(where, "must not be empty")
                return MISSING
            if single_line and ("\n" in value or "\r" in value):
                self.error(where, "must be a single line (no line breaks)")
                return MISSING
        if kind == "number" and not _data._is_finite(value):
            self.error(where, "must be a finite number")
            return MISSING
        return value

    def choice(
        self, obj: dict, key: str, allowed: Sequence[str], parts: Sequence[PathPart]
    ) -> Any:
        """A field that holds one of `allowed` (the value is never shown)."""
        if key not in obj:
            return MISSING
        if obj[key] not in allowed or not isinstance(obj[key], str):
            self.error((*parts, key), f"must be one of: {', '.join(allowed)}")
            return MISSING
        return obj[key]

    def ref(
        self,
        value: Any,
        parts: Sequence[PathPart],
        registry: Mapping[str, Any] | None,
        what: str,
    ) -> str | None:
        """A reference by id; None when it cannot be used."""
        if not isinstance(value, str):
            self.error(parts, f"must be the id of {_article(what)}, got {json_type(value)}")
            return None
        if registry is None:
            return None  # the registry itself is broken and reported
        if value in registry:
            return value
        if self.known is None and not is_identifier(value):
            self.error(parts, f"must be the id of {_article(what)}: {ID_RULE}")
            return None
        shown = self.show(value)
        self.error(
            parts,
            f"refers to an unknown {what}{' ' + shown if shown else ''}"
            f"{did_you_mean(value, registry)}{_known_list(registry, what)}",
        )
        return None

    def ref_list(
        self,
        obj: dict,
        key: str,
        parts: Sequence[PathPart],
        registry: Mapping[str, Any] | None,
        what: str,
        *,
        required: bool,
        allow_empty: bool,
    ) -> list[str] | None:
        values = self.value(obj, key, "array", parts, required=required)
        if values is MISSING:
            return None
        where = (*parts, key)
        if not values and not allow_empty:
            self.error(where, f"must list at least one {what}")
            return None
        seen: list[str] = []
        for position, value in enumerate(values):
            found = self.ref(value, (*where, position), registry, what)
            if found is None:
                continue
            if found in seen:
                self.error((*where, position), f"repeats {show_id(found)}")
                continue
            seen.append(found)
        return seen


_KINDS: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "string": ("a string", lambda v: isinstance(v, str)),
    "boolean": ("a boolean (true or false)", lambda v: isinstance(v, bool)),
    "number": ("a number", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)),
    "count": (
        "a positive whole number",
        lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    ),
    "array": ("an array", lambda v: isinstance(v, list)),
    "object": ("an object", lambda v: isinstance(v, dict)),
}


def _article(what: str) -> str:
    return f"an {what}" if what[0] in "aeiou" else f"a {what}"


def _known_list(registry: Iterable[str], what: str) -> str:
    known = [item for item in registry if is_identifier(item)]
    if not known:
        return f"; there are no {what}s"
    listed = ", ".join(known[:MAX_LISTED])
    return f"; known: {listed}{', …' if len(known) > MAX_LISTED else ''}"


# --------------------------------------------------------------------------
# What the invitations and the page tree need to know about site.json
# --------------------------------------------------------------------------


@dataclass
class SiteIndex:
    """What `check_site` learned; a registry is None when it is broken."""

    #: `site.json` is in the format 2 (else nothing else is checked).
    version_ok: bool = False
    #: Number of errors in `site.json`.
    errors: int = 0
    events: dict | None = None
    locations: dict | None = None
    schedules: dict | None = None
    media: dict | None = None
    #: The named texts (`texts`); None when the registry is broken.
    texts: dict | None = None
    main_event: str | None = None
    #: Sections with a usable id and type, in their order.
    sections: dict[str, dict] = field(default_factory=dict)
    #: Section id -> its position in `sections` (the first one with the id).
    section_positions: dict[str, int] = field(default_factory=dict)
    #: Section id -> the ids of its widgets.
    widget_ids: dict[str, list[str]] = field(default_factory=dict)
    #: Sections with a widget of broken shape, type or id: the references of
    #: the invitations to their widgets are not checked.
    broken_widgets: set[str] = field(default_factory=set)
    #: False when a section has no usable id or type: the references of the
    #: invitations to sections are not checked then.
    sections_ok: bool = True
    #: Placeholders that have a value.
    available: frozenset[str] = frozenset(PLACEHOLDERS)

    #: Number of errors in `invitations.json` (set by `check_invitations`).
    invitation_errors: int = 0

    @property
    def ok(self) -> bool:
        """No error in `site.json` nor in `invitations.json`: pages can be built."""
        return self.version_ok and not self.errors and not self.invitation_errors

    def declared_ids(self) -> frozenset[str]:
        """Ids an invitation may refer to: safe to show in its messages."""
        ids = set(self.sections) | set(self.events or ())
        for widgets in self.widget_ids.values():
            ids.update(widgets)
        return frozenset(item for item in ids if is_identifier(item))


# --------------------------------------------------------------------------
# site.json
# --------------------------------------------------------------------------


def check_site(site: Any, report: Any, where: str = SITE_FILE) -> SiteIndex:
    """Check `site.json`; the result tells the other checks what exists."""
    check = _Checker(report, where)
    index = SiteIndex()
    if not isinstance(site, dict):
        check.raw_error(f"the top-level value must be an object, got {json_type(site)}")
        index.errors = check.errors
        return index
    if not _check_version(check, site):
        index.errors = check.errors
        return index
    index.version_ok = True

    check.unknown_fields(site, SITE_FIELDS, (), _V1_SITE_PROBLEMS)
    check.value(site, "coupleNames", "string", (), required=True, single_line=True)
    check.value(site, "rsvpDeadline", "string", (), single_line=True)
    deadline = site.get("rsvpDeadline")
    # a deadline of the wrong type is reported once, not again for every use
    if "rsvpDeadline" not in site or (isinstance(deadline, str) and not deadline.strip()):
        index.available = frozenset(PLACEHOLDERS) - {"rsvpDeadline"}
    media_dir = check.value(site, "mediaDir", "string", (), required=True)
    if media_dir is not MISSING and not _data._MEDIA_DIR_RE.match(media_dir):
        check.error(
            ("mediaDir",),
            f"must be {_data.MIN_MEDIA_DIR_LENGTH} to {_data.MAX_NAME_LENGTH} characters "
            "long and may only contain A-Z, a-z, 0-9, '_' and '-'",
        )

    index.media = _check_media(check, site)
    index.schedules = _check_schedules(check, site)
    index.locations = _check_locations(check, site, index)
    index.events = _check_events(check, site, index)
    _check_main_event(check, site, index)
    index.texts = _check_texts(check, site, index)
    _check_sections(check, site, index)
    _check_link_preview(check, site, index)
    index.errors = check.errors
    return index


def _check_version(check: _Checker, site: dict) -> bool:
    if "schemaVersion" not in site:
        legacy = [name for name in V1_SITE_HINTS if name in site]
        if legacy:
            listed = ", ".join(f"'{name}'" for name in legacy)
            check.raw_error(
                f"is in the old data format (it has {listed}), which this version no "
                f'longer reads; add "schemaVersion": {SCHEMA_VERSION} and convert the '
                "data as described in the README (data format)"
            )
        else:
            check.error(("schemaVersion",), f"is required (use {SCHEMA_VERSION})")
        return False
    version = site["schemaVersion"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        shown = (
            f" {version}"
            if isinstance(version, int) and not isinstance(version, bool)
            else f" {json_type(version)}"
        )
        check.error(
            ("schemaVersion",),
            f"is{shown} - not supported: this version of the site reads version "
            f"{SCHEMA_VERSION} only",
        )
        return False
    return True


def _registry(check: _Checker, site: dict, key: str, *, required: bool = False) -> dict | None:
    """A registry `{id: entry}` without the entries whose key is not an id."""
    if key not in site:
        if required:
            check.error((key,), "is required")
            return None
        return {}
    value = site[key]
    if not isinstance(value, dict):
        check.error((key,), f"must be an object {{ id: … }}, got {json_type(value)}")
        return None
    entries = {}
    for entry_id, entry in value.items():
        if is_identifier(entry_id):
            entries[entry_id] = entry
        else:
            check.error((key, entry_id), f"is not an identifier: an id has {ID_RULE}")
    return entries


def _media_name(
    check: _Checker,
    item: dict,
    key: str,
    parts: Sequence[PathPart],
    extensions: frozenset[str],
    kind: str,
    *,
    required: bool,
) -> None:
    name = check.value(item, key, "string", parts, required=required)
    if name is MISSING:
        return
    problem = _data.check_media_name(name, extensions)
    if problem is None:
        return
    if _data.check_media_name(name) is not None:
        check.error((*parts, key), problem)  # not about the type of the file
    elif kind == "video" and key == "file":
        check.error(
            (*parts, key),
            "must be an .mp4 file (H.264 video and AAC sound): other video formats "
            "do not play in every browser",
        )
    else:
        check.error(
            (*parts, key),
            f"must be an image (allowed: {_data._extension_list(extensions)})",
        )


def _ids(value: Any) -> list[str]:
    """The strings of a list of ids (a broken list is reported elsewhere)."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def cover_backgrounds(site: Any) -> set[str]:
    """Ids of the media items that a cover shows as its background photo."""
    sections = site.get("sections") if isinstance(site, dict) else None
    return {
        section["background"]
        for section in (sections if isinstance(sections, list) else [])
        if isinstance(section, dict)
        and section.get("type") == "cover"
        and isinstance(section.get("background"), str)
    }


def _described_media(site: dict) -> set[str]:
    """Ids of the media items shown as a tile or a picture with a description."""
    ids: set[str] = set()
    locations = site.get("locations")
    for place in (locations.values() if isinstance(locations, dict) else ()):
        if isinstance(place, dict):
            ids.update(_ids(place.get("photos")))
            if isinstance(place.get("directions"), str):
                ids.add(place["directions"])
    sections = site.get("sections")
    for section in (sections if isinstance(sections, list) else ()):
        widgets = section.get("widgets") if isinstance(section, dict) else None
        for widget in (widgets if isinstance(widgets, list) else ()):
            if isinstance(widget, dict) and widget.get("type") == "media":
                ids.update(_ids(widget.get("items")))
    return ids


def _check_media(check: _Checker, site: dict) -> dict | None:
    media = _registry(check, site, "media")
    if not media:
        return media
    # a photo behind the cover is decoration: the page gives it an empty alt
    decorative = cover_backgrounds(site) - _described_media(site)
    for media_id, item in media.items():
        parts = ("media", media_id)
        item = check.object(item, parts)
        if item is None:
            continue
        if "type" not in item:
            check.error((*parts, "type"), f"is required ({' or '.join(MEDIA_TYPES)})")
            continue
        kind = check.choice(item, "type", MEDIA_TYPES, parts)
        if kind is MISSING:
            continue
        check.unknown_fields(item, MEDIA_FIELDS[kind], parts, MEDIA_HINTS.get(kind))
        _media_name(
            check, item, "file", parts,
            VIDEO_EXTENSIONS if kind == "video" else IMAGE_EXTENSIONS, kind, required=True,
        )
        if kind == "video":
            _media_name(check, item, "poster", parts, IMAGE_EXTENSIONS, kind, required=True)
        _media_name(check, item, "thumb", parts, IMAGE_EXTENSIONS, kind, required=False)
        given = [key for key in ("width", "height") if key in item]
        for key in given:
            check.value(item, key, "count", parts)
        if len(given) == 1:
            other = "height" if given[0] == "width" else "width"
            check.error((*parts, given[0]), f"is set without '{other}'; set both or neither")
        check.value(item, "alt", "string", parts, single_line=True)
        alt = item.get("alt")
        if media_id in decorative:
            continue
        if "alt" not in item or (isinstance(alt, str) and not alt.strip()):
            # default: a missing description is a warning, not an error
            check.warning(
                (*parts, "alt"),
                "is not set: the tile gets a neutral label instead of a description",
            )
    return media


def _check_schedules(check: _Checker, site: dict) -> dict | None:
    schedules = _registry(check, site, "schedules")
    if not schedules:
        return schedules
    for schedule_id, items in schedules.items():
        parts = ("schedules", schedule_id)
        if not isinstance(items, list):
            check.error(parts, f"must be an array of programme items, got {json_type(items)}")
            continue
        if not items:
            check.warning(parts, "is empty (the programme will not be shown)")
        for position, item in enumerate(items):
            item_parts = (*parts, position)
            item = check.object(item, item_parts)
            if item is None:
                continue
            check.unknown_fields(item, SCHEDULE_ITEM_FIELDS, item_parts)
            check.value(item, "time", "string", item_parts, single_line=True)
            check.value(item, "title", "string", item_parts, required=True, single_line=True)
            check.value(item, "text", "string", item_parts)
    return schedules


def _check_locations(check: _Checker, site: dict, index: SiteIndex) -> dict | None:
    locations = _registry(check, site, "locations")
    if not locations:
        return locations
    for location_id, place in locations.items():
        parts = ("locations", location_id)
        place = check.object(place, parts)
        if place is None:
            continue
        check.unknown_fields(place, LOCATION_FIELDS, parts, _V1_LOCATION_PROBLEMS)
        ready = check.value(place, "ready", "boolean", parts)
        check.value(place, "name", "string", parts, single_line=True)
        name = place.get("name")
        if ready is not False and (
            "name" not in place or (isinstance(name, str) and not name.strip())
        ):
            check.error(
                (*parts, "name"),
                "is required unless the place is not announced yet ('ready': false)",
            )
        check.value(place, "address", "string", parts, single_line=True)
        check.value(place, "description", "string", parts)
        geo = check.value(place, "geo", "object", parts)
        if geo is not MISSING:
            geo_parts = (*parts, "geo")
            check.unknown_fields(geo, GEO_FIELDS, geo_parts)
            for key in GEO_FIELDS:
                value = check.value(geo, key, "number", geo_parts, required=True)
                limit = GEO_LIMITS[key]
                if value is not MISSING and abs(value) > limit:
                    check.error((*geo_parts, key), f"is outside [-{limit:g}, {limit:g}]")
        maps = check.value(place, "maps", "object", parts)
        if maps is not MISSING:
            _check_maps(check, maps, (*parts, "maps"))
        check.ref_list(
            place, "photos", parts, index.media, "media item", required=False, allow_empty=True
        )
        if "directions" in place:
            found = check.ref(place["directions"], (*parts, "directions"), index.media, "media item")
            kind = _media_type(index, found)
            if found is not None and kind is not None and kind != "image":
                check.error(
                    (*parts, "directions"),
                    f"must be an image, but {show_id(found)} is a {kind}",
                )
    return locations


def _check_maps(check: _Checker, maps: dict, parts: Sequence[PathPart]) -> None:
    """Ids and text queries of a place in the map services."""
    check.unknown_fields(maps, MAPS_FIELDS, parts, _MAPS_PROBLEMS)
    for key, (allowed, rule) in _MAP_ID_RULES.items():
        value = check.value(maps, key, "string", parts)
        if value is not MISSING and not set(value.strip()) <= allowed:
            check.error((*parts, key), f"{rule} (use the identifier, not a link)")
    for key in MAP_QUERY_FIELDS:
        where = (*parts, key)
        if key in maps and isinstance(maps[key], str) and not maps[key].strip():
            check.error(where, "must not be empty (remove the field instead)")
            continue
        value = check.value(maps, key, "string", parts, single_line=True)
        if value is MISSING:
            continue
        if any(unicodedata.category(char) == "Cc" for char in value):
            check.error(where, "must not contain control characters")
        elif len(value) > MAP_QUERY_MAX_LENGTH:
            check.error(where, f"is longer than {MAP_QUERY_MAX_LENGTH} characters")
    both = ("yandexOrgId", "yandexGeoId")
    if all(isinstance(maps.get(key), str) and maps[key].strip() for key in both):
        check.error(
            parts,
            "has both 'yandexOrgId' and 'yandexGeoId': "
            "set either 'yandexOrgId' or 'yandexGeoId'",
        )


def _media_type(index: SiteIndex, media_id: str | None) -> str | None:
    item = (index.media or {}).get(media_id) if media_id else None
    kind = item.get("type") if isinstance(item, dict) else None
    return kind if kind in MEDIA_TYPES else None


def _duration_text(duration: Any) -> str:
    """`6 h`, `90 min`: a duration for a message."""
    minutes = int(duration.total_seconds()) // 60
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


def _check_events(check: _Checker, site: dict, index: SiteIndex) -> dict | None:
    events = _registry(check, site, "events", required=True)
    if events is None:
        return None
    if not events and isinstance(site.get("events"), dict) and not site["events"]:
        check.error(("events",), "must describe at least one event")
        return None
    offsets: dict[str, Any] = {}
    for event_id, event in events.items():
        parts = ("events", event_id)
        event = check.object(event, parts)
        if event is None:
            continue
        check.unknown_fields(event, EVENT_FIELDS, parts)
        check.value(event, "title", "string", parts, required=True, single_line=True)
        check.value(event, "description", "string", parts)
        check.value(event, "visible", "boolean", parts)
        show_end = check.value(event, "showEnd", "boolean", parts)
        if "location" not in event:
            check.error(
                (*parts, "location"),
                "is required (a place that is not announced yet is a location with "
                "'ready': false)",
            )
        else:
            check.ref(event["location"], (*parts, "location"), index.locations, "location")
        if "schedule" in event:
            check.ref(event["schedule"], (*parts, "schedule"), index.schedules, "schedule")
        moments: dict[str, datetime] = {}
        given: set[str] = set()
        for key in ("start", "end"):
            text = check.value(event, key, "string", parts, required=key == "start")
            if text is MISSING:
                continue
            given.add(key)
            try:
                moments[key] = parse_date_iso(text)
            except ValueError as exc:
                check.error((*parts, key), str(exc))
        if "start" in moments and "end" in moments and moments["end"] <= moments["start"]:
            check.error((*parts, "end"), "must be later than 'start'")
        # default: the end is optional; without it the calendar file and the
        # past/now marks of the page use the start plus the default duration
        # (an end of a wrong type is an error above, not "not set")
        end = event.get("end", "")
        if "end" not in given and isinstance(end, str):
            check.warning(
                (*parts, "end"),
                "is not set: the calendar and the past/now marks use start + "
                f"{_duration_text(DEFAULT_EVENT_DURATION)}",
            )
            if show_end is True:
                check.warning((*parts, "showEnd"), "has no effect without 'end'")
        if "start" in moments:
            offsets[event_id] = moments["start"].utcoffset()
    if len(set(offsets.values())) > 1:
        listed = ", ".join(event_id for event_id in offsets)
        check.warning(
            ("events",),
            f"has events with different UTC offsets ({listed}); check the offsets, "
            "especially around a change to or from summer time",
        )
    return events


def _check_main_event(check: _Checker, site: dict, index: SiteIndex) -> None:
    if "mainEvent" not in site:
        check.error(("mainEvent",), "is required")
        return
    found = check.ref(site["mainEvent"], ("mainEvent",), index.events, "event")
    if found is None:
        return
    event = index.events[found]
    if isinstance(event, dict) and event.get("visible") is False:
        check.error(
            ("mainEvent",),
            f"refers to {show_id(found)}, which is hidden by default ('visible': false); "
            "the main event must be visible to everybody who has no 'primaryEvent'",
        )
        return
    index.main_event = found


def _check_text(
    check: _Checker,
    obj: dict,
    key: str,
    parts: Sequence[PathPart],
    index: SiteIndex,
    *,
    required: bool = False,
    single_line: bool = False,
) -> None:
    where = (*parts, key)
    if key not in obj:
        if required:
            check.error(where, "is required")
        return
    value = obj[key]
    for problem in _data.check_text(value, where, PLACEHOLDERS, index.available):
        check.raw_error(problem)
    variants = (
        [(where, value)]
        if isinstance(value, str)
        else [((*where, form), value.get(form)) for form in FORMS]
        if isinstance(value, dict)
        else []
    )
    for variant_parts, text in variants:
        if not isinstance(text, str):
            continue
        if required and not text.strip():
            check.error(variant_parts, "must not be empty")
        elif single_line and ("\n" in text or "\r" in text):
            check.error(variant_parts, "must be a single line (no line breaks)")
        elif index.texts is not None and _valid_syntax(text):
            # the named texts in the form of this variant (a string serves both)
            forms = FORMS if isinstance(value, str) else (variant_parts[-1],)
            for form in forms:
                try:
                    expanded = _data.expand_texts(text, form, index.texts)
                except _data.TextError as exc:
                    check.error(variant_parts, str(exc))
                    break
                if single_line and ("\n" in expanded or "\r" in expanded):
                    check.error(
                        variant_parts,
                        "must be a single line (no line breaks), but a text it uses has "
                        "several lines",
                    )
                    break


def _valid_syntax(text: str) -> bool:
    """False when a text has a syntax problem (it is reported on its own)."""
    try:
        _data.parse_text(text)
    except _data.TextError:
        return False
    return True


def _placeholder_names(text: str) -> set[str]:
    """The built-in placeholders of a text whose syntax is valid."""
    try:
        parts = _data.parse_text(text)
    except _data.TextError:
        return set()
    return {part.name for part in parts if isinstance(part, _data.Placeholder)}


_TEXT_SHAPE = (
    "must be a string or an object with the strings 'ty' and 'vy' "
    f"(and, for places without a guest, the common form '{_data.COMMON_FORM}')"
)


def _check_texts(check: _Checker, site: dict, index: SiteIndex) -> dict | None:
    """The named texts: `texts.<id>` is a string or `{ty, vy[, all]}`."""
    texts = _registry(check, site, "texts")
    if not texts:
        return texts
    common = _data.COMMON_FORM
    for text_id, value in texts.items():
        parts = ("texts", text_id)
        if isinstance(value, str):
            variants = [(parts, None, value)]
        elif isinstance(value, dict):
            for key in value:
                if key not in _data.TEXT_FORMS:
                    check.errors += 1
                    check.report.error(
                        f"{check.where}: unknown field '{check.path((*parts, key))}'"
                        f"{did_you_mean(key, _data.TEXT_FORMS)}"
                    )
            missing = [form for form in FORMS if form not in value]
            if missing:
                listed = " and ".join(f"'{form}'" for form in missing)
                check.error(parts, f"must have both 'ty' and 'vy' (missing {listed})")
            for key in _data.TEXT_FORMS:
                if key in value and not isinstance(value[key], str):
                    check.error((*parts, key), f"must be a string, got {json_type(value[key])}")
            variants = [
                ((*parts, form), form, value[form])
                for form in _data.TEXT_FORMS
                if isinstance(value.get(form), str)
            ]
        else:
            check.error(parts, f"{_TEXT_SHAPE}, got {json_type(value)}")
            continue
        for where, form, text in variants:
            if not text.strip():
                check.error(where, "must not be empty")
                continue
            problems = _data.placeholder_problems(text, PLACEHOLDERS, index.available)
            for problem in problems:
                check.error(where, problem)
            if problems:
                continue
            for expand_form in (form,) if form is not None else FORMS:
                try:
                    expanded = _data.expand_texts(text, expand_form, texts)
                except _data.TextError as exc:
                    check.error(where, str(exc))
                    break
                if form == common and "greeting" in _placeholder_names(expanded):
                    check.error(
                        where,
                        "uses {greeting} (directly or through another text): the common "
                        f"form '{common}' is the same for everybody and knows no guest",
                    )
    return texts


def _check_link_preview(check: _Checker, site: dict, index: SiteIndex) -> None:
    """`linkPreview.description`: one string for everybody."""
    preview = check.value(site, "linkPreview", "object", ())
    if preview is MISSING:
        return
    parts = ("linkPreview",)
    check.unknown_fields(preview, LINK_PREVIEW_FIELDS, parts)
    if "description" not in preview:
        return
    where = (*parts, "description")
    value = preview["description"]
    if isinstance(value, dict):
        check.error(
            where,
            "must be one string for everybody: the link preview knows no guest; put the "
            "forms of address into a named text ('texts') with the common form "
            f"'{_data.COMMON_FORM}' and use it here as {{text:<id>}}",
        )
        return
    text = check.value(preview, "description", "string", parts)
    if text is MISSING:
        if isinstance(value, str):
            check.error(where, "must not be empty (remove the field instead)")
        return
    problems = _data.placeholder_problems(text, PLACEHOLDERS, index.available)
    for problem in problems:
        check.error(where, problem)
    if problems or index.texts is None:
        return
    try:
        expanded = _data.expand_texts(text, _data.COMMON_FORM, index.texts)
    except _data.TextError as exc:
        check.error(where, str(exc))
        return
    if "greeting" in _placeholder_names(expanded):
        check.error(
            where,
            "uses {greeting} (directly or through a text): the link preview is the same "
            "for everybody; use named texts with the common form "
            f"'{_data.COMMON_FORM}' without {{greeting}}",
        )


def _check_sections(check: _Checker, site: dict, index: SiteIndex) -> None:
    sections = check.value(site, "sections", "array", (), required=True)
    if sections is MISSING:
        index.sections_ok = False
        return
    if not sections:
        check.error(("sections",), "must list at least one section")
    covers: list[int] = []
    for position, section in enumerate(sections):
        parts = ("sections", position)
        section = check.object(section, parts)
        if section is None:
            index.sections_ok = False
            continue
        section_id = _section_id(check, section, parts, index)
        kind = check.choice(section, "type", SECTION_TYPES, parts) if "type" in section else "custom"
        if kind is MISSING:
            index.sections_ok = False
            continue
        if section_id is not None:
            index.sections[section_id] = section
            index.widget_ids[section_id] = []
        check.unknown_fields(section, SECTION_FIELDS[kind], parts)
        check.value(section, "visible", "boolean", parts)
        if kind == "cover":
            if covers:
                check.error((*parts, "type"), "is 'cover': there may be only one cover")
            elif position != 0:
                check.error((*parts, "type"), "is 'cover': the cover must be the first section")
            covers.append(position)
            _check_text(check, section, "eyebrow", parts, index, single_line=True)
            _check_cover_background(check, section, parts, index)
            continue
        _check_text(check, section, "title", parts, index, required=True, single_line=True)
        check.value(section, "titleHidden", "boolean", parts)
        check.choice(section, "width", SECTION_WIDTHS, parts)
        check.choice(section, "align", SECTION_ALIGNS, parts)
        widgets = check.value(section, "widgets", "array", parts, required=True)
        if widgets is MISSING:
            continue
        for widget_position, widget in enumerate(widgets):
            _check_widget(
                check, widget, (*parts, "widgets", widget_position), index, section_id
            )
    if not covers and sections:
        check.warning(("sections",), "has no 'cover' section: the page will have no main heading")


def _check_cover_background(
    check: _Checker, section: dict, parts: Sequence[PathPart], index: SiteIndex
) -> None:
    """`background` (a picture of the media registry) and `backgroundFocus`."""
    if "background" in section:
        where = (*parts, "background")
        found = check.ref(section["background"], where, index.media, "media item")
        kind = _media_type(index, found)
        if found is not None and kind is not None and kind != "image":
            check.error(where, f"must be an image, but {show_id(found)} is a {kind}")
    check.choice(section, "backgroundFocus", COVER_FOCUSES, parts)
    if "backgroundFocus" in section and "background" not in section:
        check.warning((*parts, "backgroundFocus"), "has no effect without 'background'")


def _section_id(
    check: _Checker, section: dict, parts: Sequence[PathPart], index: SiteIndex
) -> str | None:
    if "id" not in section:
        check.error((*parts, "id"), "is required")
    elif not is_identifier(section["id"]):
        check.error((*parts, "id"), f"must be an identifier: {ID_RULE}")
    elif section["id"] in index.section_positions:
        first = index.section_positions[section["id"]]
        check.error(
            (*parts, "id"),
            f"repeats {show_id(section['id'])} (already used by sections[{first}])",
        )
    else:
        index.section_positions[section["id"]] = parts[-1]
        return section["id"]
    index.sections_ok = False
    return None


def _check_widget(
    check: _Checker,
    widget: Any,
    parts: Sequence[PathPart],
    index: SiteIndex,
    section_id: str | None,
) -> None:
    def broken() -> None:
        # the invitations may refer to this widget: their references to the
        # widgets of the section are not checked, the problem is reported here
        if section_id is not None:
            index.broken_widgets.add(section_id)

    widget = check.object(widget, parts)
    if widget is None:
        broken()
        return
    if "type" not in widget:
        check.error((*parts, "type"), f"is required ({', '.join(WIDGET_TYPES)})")
        broken()
        return
    kind = widget["type"]
    if kind not in WIDGET_TYPES:
        shown = f" {show_id(kind)}" if is_identifier(kind) else ""
        check.error(
            (*parts, "type"),
            f"names an unknown widget type{shown}{did_you_mean(kind, WIDGET_TYPES)}; "
            f"known: {', '.join(WIDGET_TYPES)}",
        )
        broken()
        return
    check.unknown_fields(widget, WIDGET_BASE_FIELDS + WIDGET_FIELDS[kind], parts)
    check.value(widget, "visible", "boolean", parts)
    if "id" in widget:
        widget_id = widget["id"]
        if not is_identifier(widget_id):
            check.error((*parts, "id"), f"must be an identifier: {ID_RULE}")
            broken()
        elif section_id is not None:
            if widget_id in index.widget_ids[section_id]:
                check.error((*parts, "id"), f"repeats {show_id(widget_id)} within the section")
            else:
                index.widget_ids[section_id].append(widget_id)
    if kind in WIDGET_VARIANTS:
        check.choice(widget, "variant", WIDGET_VARIANTS[kind], parts)

    if kind == "text":
        _check_text(check, widget, "text", parts, index, required=True)
    elif kind == "date":
        if "event" in widget:
            check.ref(widget["event"], (*parts, "event"), index.events, "event")
        for key in ("countdown", "calendar"):
            check.value(widget, key, "boolean", parts)
    elif kind == "events":
        check.ref_list(
            widget, "events", parts, index.events, "event", required=False, allow_empty=False
        )
    elif kind == "location":
        if "location" not in widget:
            check.error((*parts, "location"), "is required")
        else:
            check.ref(widget["location"], (*parts, "location"), index.locations, "location")
    elif kind == "schedule":
        if "schedule" not in widget:
            check.error((*parts, "schedule"), "is required")
        else:
            check.ref(widget["schedule"], (*parts, "schedule"), index.schedules, "schedule")
    elif kind == "media":
        items = check.ref_list(
            widget, "items", parts, index.media, "media item", required=True, allow_empty=False
        )
        layout = check.choice(widget, "layout", MEDIA_LAYOUTS, parts)
        if layout == "single" and items is not None and len(widget["items"]) != 1:
            check.error(
                (*parts, "items"), "must list exactly one media item for the 'single' layout"
            )


# --------------------------------------------------------------------------
# invitations.json
# --------------------------------------------------------------------------


def check_invitations(
    invitations: Any, index: SiteIndex, report: Any, where: str = INVITATIONS_FILE
) -> None:
    """Check `invitations.json` against what `check_site` found.

    The number of errors is kept in `index.invitation_errors`.
    """
    checkers: list[_Checker] = []
    try:
        _check_invitations(invitations, index, report, where, checkers)
    finally:
        index.invitation_errors = sum(check.errors for check in checkers)


def _check_invitations(
    invitations: Any, index: SiteIndex, report: Any, where: str, checkers: list[_Checker]
) -> None:
    top = _Checker(report, where)
    checkers.append(top)
    if not isinstance(invitations, list):
        top.raw_error(
            "the top-level value must be an array of invitations, "
            f"got {json_type(invitations)}"
        )
        return
    if not invitations:
        top.warning((), "contains no invitations")
        return
    legacy = [
        [name for name in V1_INVITATION_HINTS if name in invitation]
        for invitation in invitations
        if isinstance(invitation, dict) and "form" not in invitation
    ]
    if len(legacy) == len(invitations) and all(legacy):
        # the whole file is in the old format: one message, not one per field
        names = [name for name in V1_INVITATION_HINTS if any(name in item for item in legacy)]
        top.raw_error(
            f"all {len(invitations)} invitations are in the old data format (fields "
            f"{', '.join(repr(name) for name in names)}); convert them as described "
            "in the README (data format)"
        )
        return
    known = GUEST_FIELD_NAMES | index.declared_ids()
    seen: dict[str, int] = {}
    for number, invitation in enumerate(invitations, start=1):
        if not isinstance(invitation, dict):
            top.errors += 1
            report.error(
                f"{invitation_label(number)}: must be an object, got {json_type(invitation)}"
            )
            continue
        check = _Checker(report, invitation_label(number), known)
        checkers.append(check)
        _check_invitation(check, invitation, index, number, seen)


def _check_invitation(
    check: _Checker, invitation: dict, index: SiteIndex, number: int, seen: dict[str, int]
) -> None:
    if "token" not in invitation:
        check.error(("token",), "is required")
    else:
        problem = _data.check_token(invitation["token"])
        if problem:
            check.raw_error(problem)
        else:
            key = invitation["token"].lower()
            if key in seen:
                check.raw_error(
                    f"duplicate token (same as invitation #{seen[key]}; tokens are "
                    "compared case-insensitively because they become directory names)"
                )
            else:
                seen[key] = number
    check.unknown_fields(invitation, INVITATION_FIELDS, (), _V1_INVITATION_PROBLEMS)
    check.value(invitation, "greeting", "string", (), required=True, single_line=True)
    # default: the form of address is one field
    if "form" not in invitation:
        if "ty" not in invitation and "vy" not in invitation:  # else: an old field
            check.error(("form",), "is required ('ty' or 'vy')")
    else:
        check.choice(invitation, "form", FORMS, ())
    if not index.version_ok:
        return  # site.json cannot be used: the references are not checked

    visible = _check_guest_events(check, invitation, index)
    _check_primary_event(check, invitation, index, visible)
    _check_guest_sections(check, invitation, index)


def _event_visible_by_default(event: Any) -> bool:
    # default: an event may be hidden in the registry and shown to some guests
    return not (isinstance(event, dict) and event.get("visible") is False)


def _check_note(check: _Checker, override: dict, parts: Sequence[PathPart], index: SiteIndex) -> None:
    """A note of an invitation: a string with placeholders, no forms."""
    note = check.value(override, "note", "string", parts)
    if note is MISSING:
        return
    problems = _data.placeholder_problems(note, PLACEHOLDERS, index.available)
    for problem in problems:
        check.error((*parts, "note"), problem)
    if problems or index.texts is None:
        return
    # a note has no forms: the named texts take the form of the invitation,
    # checked here in both (the form itself is checked on its own)
    for form in FORMS:
        try:
            _data.expand_texts(note, form, index.texts, show_unknown=False)
        except _data.TextError as exc:
            check.error((*parts, "note"), str(exc))
            return


def _check_guest_events(
    check: _Checker, invitation: dict, index: SiteIndex
) -> dict[str, bool] | None:
    """Which events the invitation sees, or None when that is not known."""
    if index.events is None:
        return None
    visible = {
        event_id: _event_visible_by_default(event) for event_id, event in index.events.items()
    }
    overrides = check.value(invitation, "events", "object", ())
    if overrides is MISSING:
        return visible
    for event_id, override in overrides.items():
        parts = ("events", event_id)
        if event_id not in index.events:
            check.error(
                parts,
                f"refers to an unknown event{did_you_mean(event_id, index.events)}"
                f"{_known_list(index.events, 'event')}",
            )
            continue
        override = check.object(override, parts)
        if override is None:
            continue
        check.unknown_fields(override, GUEST_EVENT_FIELDS, parts)
        shown = check.value(override, "visible", "boolean", parts)
        if shown is not MISSING:
            visible[event_id] = shown
        _check_note(check, override, parts, index)
        if not visible[event_id] and isinstance(override.get("note"), str) and override["note"].strip():
            check.warning(
                (*parts, "note"), "is set, but the event is hidden for this invitation"
            )
    if visible and not any(visible.values()):
        check.error(("events",), "hides every event; at least one must stay visible")
    return visible


def _check_primary_event(
    check: _Checker, invitation: dict, index: SiteIndex, visible: dict[str, bool] | None
) -> None:
    if index.events is None or visible is None:
        return
    if "primaryEvent" in invitation:
        found = check.ref(invitation["primaryEvent"], ("primaryEvent",), index.events, "event")
        if found is not None and not visible.get(found, False):
            check.error(
                ("primaryEvent",),
                f"refers to {show_id(found)}, which is hidden for this invitation",
            )
    elif index.main_event is not None and not visible.get(index.main_event, False):
        check.error(
            ("primaryEvent",),
            f"is required: the main event {show_id(index.main_event)} is hidden for "
            "this invitation",
        )


def _check_guest_sections(check: _Checker, invitation: dict, index: SiteIndex) -> None:
    overrides = check.value(invitation, "sections", "object", ())
    if overrides is MISSING:
        return
    for section_id, override in overrides.items():
        parts = ("sections", section_id)
        section = index.sections.get(section_id)
        if section is None:
            if index.sections_ok:  # else it may be one of the broken sections
                check.error(
                    parts,
                    f"refers to an unknown section{did_you_mean(section_id, index.sections)}"
                    f"{_known_list(index.sections, 'section')}",
                )
            continue
        override = check.object(override, parts)
        if override is None:
            continue
        if section.get("type") == "cover":
            check.error(parts, "cannot be set: the cover is the same for everybody")
            continue
        check.unknown_fields(override, GUEST_SECTION_FIELDS, parts)
        shown = check.value(override, "visible", "boolean", parts)
        _check_note(check, override, parts, index)
        visible = shown if shown is not MISSING else section.get("visible") is not False
        note = override.get("note")
        if not visible and isinstance(note, str) and note.strip():
            check.warning(
                (*parts, "note"), "is set, but the section is hidden for this invitation"
            )
        widgets = check.value(override, "widgets", "object", parts)
        if widgets is MISSING:
            continue
        declared = index.widget_ids.get(section_id, [])
        for widget_id, widget_override in widgets.items():
            widget_parts = (*parts, "widgets", widget_id)
            if widget_id not in declared:
                if section_id in index.broken_widgets:
                    continue  # it may be the broken widget, reported in site.json
                hint = (
                    f"{did_you_mean(widget_id, declared)}; widgets with an id: "
                    f"{', '.join(declared)}"
                    if declared
                    else '; give the widget an "id" in site.json to address it'
                )
                check.error(
                    widget_parts,
                    f"refers to no widget of section {show_id(section_id)}{hint}",
                )
                continue
            widget_override = check.object(widget_override, widget_parts)
            if widget_override is None:
                continue
            check.unknown_fields(widget_override, GUEST_WIDGET_FIELDS, widget_parts)
            check.value(widget_override, "visible", "boolean", widget_parts)


def check_data(
    site: Any,
    invitations: Any,
    report: Any,
    *,
    site_where: str = SITE_FILE,
    invitations_where: str = INVITATIONS_FILE,
) -> SiteIndex:
    """`check_site`, then `check_invitations`.

    `SiteIndex.ok` of the result is true when neither document has an error
    reported through these checks, that is when the page trees can be built.
    Problems the caller reports itself (reading the files, missing media) are
    not counted: the build decides by its report.
    """
    index = check_site(site, report, site_where)
    check_invitations(invitations, index, report, invitations_where)
    return index


# --------------------------------------------------------------------------
# Media files
# --------------------------------------------------------------------------

_FILE_ROLES = ("file", "poster", "thumb")


def _image_problem_text(problems: Sequence[str]) -> str | None:
    if _media.UNREADABLE in problems:
        return "names a file that cannot be read"
    if _media.NOT_THIS_TYPE in problems:
        return "names a file that is not a valid file of the type its name gives"
    return None


def check_media_files(
    site: dict,
    infos: Mapping[str, Any],
    report: Any,
    shown: Iterable[str] | None = None,
    where: str = SITE_FILE,
    sizes: Mapping[str, int] | None = None,
) -> None:
    """Messages about the media files of the items in `shown` (all by default).

    `infos` maps a file name from the data to its `tools._media.MediaInfo`;
    a file without an entry is skipped (its absence is reported by whoever
    looked for it).  `sizes` maps a file name to its size in bytes, when
    known.  Only paths to fields are printed, never file names.
    """
    check = _Checker(report, where)
    media = site.get("media") if isinstance(site, dict) else None
    if not isinstance(media, dict):
        return
    backgrounds = cover_backgrounds(site)
    for media_id in sorted(media if shown is None else set(shown)):
        item = media.get(media_id)
        if not isinstance(item, dict):
            continue
        parts = ("media", media_id)
        for role in _FILE_ROLES:
            name = item.get(role)
            info = infos.get(name) if isinstance(name, str) else None
            if info is None:
                continue
            problem = _image_problem_text(info.problems)
            if problem is not None:
                check.error((*parts, role), problem)
                continue
            if role == "file" and item.get("type") == "video":
                _video_warnings(check, (*parts, role), info.problems)
        _size_warnings(check, item, parts, infos)
        file = item.get("file")
        size = (sizes or {}).get(file) if isinstance(file, str) else None
        if media_id in backgrounds and size is not None and size > COVER_BACKGROUND_WARN_BYTES:
            check.warning(
                (*parts, "file"),
                f"is {_data.format_size(size)}; it is the background of the cover, the "
                "first screen: keep it under ~400 KB (a long side of 2000-2400 px, JPEG "
                "quality ~70 or WebP)",
            )


_VIDEO_WARNINGS = {
    _media.DURATION_UNKNOWN: "has no readable duration; the tile will show none",
    _media.NOT_FASTSTART: "has its index after the video data, so playback waits for "
    "the whole file; re-encode it with '-movflags +faststart'",
    _media.CODEC_UNKNOWN: "has a video codec that cannot be read; H.264 ('avc1') plays "
    "everywhere",
    _media.UNEXPECTED_CODEC: "is not H.264 ('avc1') video and may not play on some "
    "devices",
    _media.HDR_VIDEO: "is HDR video; convert it to SDR, HDR looks washed out on many "
    "screens",
}


def _video_warnings(check: _Checker, parts: Sequence[PathPart], problems: Sequence[str]) -> None:
    for code in problems:
        if code in _VIDEO_WARNINGS:
            check.warning(parts, _VIDEO_WARNINGS[code])


def _info_size(infos: Mapping[str, Any], name: Any) -> tuple[Any, Any]:
    """(the facts of a file, its size in pixels or None)."""
    info = infos.get(name) if isinstance(name, str) else None
    return info, (info.size if info is not None else None)


def _size_warnings(
    check: _Checker, item: dict, parts: Sequence[PathPart], infos: Mapping[str, Any]
) -> None:
    is_video = item.get("type") == "video"
    source = "poster" if is_video else "file"
    info, size = _info_size(infos, item.get(source))
    width, height = item.get("width"), item.get("height")
    has_size = all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (width, height)
    )
    video_size = _info_size(infos, item.get("file"))[1] if is_video else None
    if (
        info is not None
        and size is None
        and video_size is None
        and not has_size
        and not _image_problem_text(info.problems)
    ):
        check.warning(
            (*parts, source),
            "has no readable picture size; set 'width' and 'height' so that the page "
            "does not jump while it loads",
        )
    if is_video and has_size and size is not None:
        ratio, actual = width / height, size[0] / size[1]
        if abs(ratio - actual) / actual > RATIO_TOLERANCE:
            check.warning(
                (*parts, "poster"),
                "has another proportion than 'width' and 'height'; make the poster the "
                "same proportion as the video",
            )
