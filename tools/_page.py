"""The page tree of one invitation: everything the fragments of its page read.

`build_pages` turns data that passed `tools._schema.check_data` into one tree
per invitation.  What an invitation does not see is cut out while the tree
is built: a hidden section, widget or event is not in the tree at all, and a
hidden event is left out of the cards, the dates, the programmes and the end
times of the other events.  Every field of the tree always exists: an empty
string, `[]`, `false` or `null` for an object.  Texts come ready: the form of
address is picked and the placeholders are filled in.

Everything outside the data comes in through `PageSettings`: the URL of a
media file, the facts of the media files (sizes, durations), the default
duration of an event, the path of the pages and the fields of the site
images.  The module reads no files.

`Usage` records what the trees show; `warn_unused` reports the parts of
`site.json` that no page shows (their media files are not published).

Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping, Sequence

from tools import _dates, _maps
from tools._data import format_path, invitation_label, resolve_text, show_id, substitute
from tools._mp4 import format_duration
from tools._schema import (
    PLACEHOLDERS,
    SITE_FILE,
    report_warning,
)

#: The fields of the site images at the root of every tree.
SITE_IMAGE_FIELDS = (
    "faviconPath",
    "faviconType",
    "ogImage",
    "ogImageType",
    "ogImageWidth",
    "ogImageHeight",
)
#: Joins the parts of a DOM id.  An id of the data never holds `--`, so the
#: parts are always told apart: `s-where--w1--e-dinner--l-manor` is the place
#: `manor` of the event `dinner` in the first widget of the section `where`.
DOM_SEPARATOR = "--"


def dom_id(*parts: str) -> str:
    """A DOM id from its parts; "" when the first part is "" (no id)."""
    return DOM_SEPARATOR.join(parts) if parts and parts[0] else ""


def section_part(section_id: str) -> str:
    return f"s-{section_id}"


def widget_part(number: int) -> str:
    return f"w{number}"


def event_part(event_id: str) -> str:
    return f"e-{event_id}"


def location_part(location_id: str) -> str:
    return f"l-{location_id}"


TITLE_PART = "title"
PROGRAM_PART = "program"

#: Labels of a media tile without a description.
DEFAULT_ALT = {"image": "Фотография", "video": "Видео"}
VIDEO_LABEL = "Видео"


@dataclass(frozen=True)
class PageSettings:
    """What the trees need from outside the data."""

    #: Duration of an event without an end (it ends earlier when the next
    #: event of the invitation starts).
    default_duration: timedelta
    #: `faviconPath`, `faviconType`, `ogImage`, `ogImageType`, `ogImageWidth`,
    #: `ogImageHeight`: copied to the root of every tree.
    site_images: Mapping[str, Any]
    #: File name from the data -> its URL; by default the name under
    #: `mediaPath`.
    media_src: Callable[[str], str] | None = None
    #: File name from the data -> its facts (`tools._media.MediaInfo` or an
    #: object with the same attributes); a missing entry means "unknown".
    media_info: Mapping[str, Any] = field(default_factory=dict)
    #: The pages live in `<pages_path>/<token>/`.
    pages_path: str = "/i"
    #: The media directory is `<assets_path>/<mediaDir>`.
    assets_path: str = "/assets"


@dataclass
class Usage:
    """Ids of the parts of `site.json` that at least one tree shows."""

    sections: set[str] = field(default_factory=set)
    events: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    schedules: set[str] = field(default_factory=set)
    media: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------
# One invitation
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else ""


class _Page:
    """Builds the tree of one invitation."""

    def __init__(
        self,
        site: dict,
        invitation: dict,
        settings: PageSettings,
        usage: Usage,
        warn: Callable[[str], None] | None,
        label: str,
        place_warnings: set[tuple[str, str]],
    ) -> None:
        self.site = site
        self.invitation = invitation
        self.settings = settings
        self.usage = usage
        self.warn = warn
        self.label = label
        self.place_warnings = place_warnings
        # default: the form of address is the field `form` of the invitation
        self.form = invitation["form"]
        self.media_path = f"{settings.assets_path}/{site['mediaDir']}"
        self.events = site["events"]
        self.event_overrides = invitation.get("events", {})
        self.section_overrides = invitation.get("sections", {})
        self.main_id = site["mainEvent"]
        #: Events shown as cards of an `events` widget.
        self.cards: set[str] = set()

        self.visible = self._visible_events()
        self.primary_id = invitation.get("primaryEvent") or self.main_id
        self.same_day = _dates.same_local_day(item.start for item in self.timeline)
        self.values = self._text_values()

    # -- events --------------------------------------------------------------

    def _visible_events(self) -> list[str]:
        """The events the invitation sees, by start; their effective ends."""
        seen = []
        for event_id, event in self.events.items():
            # default: an event hidden in the registry is shown to the
            # invitations that switch it on
            override = self.event_overrides.get(event_id, {})
            shown = override.get("visible", event.get("visible", True))
            if shown:
                seen.append(event_id)
        # only the visible events: a hidden one would shorten the others
        self.timeline = _dates.event_timeline(
            (
                (
                    event_id,
                    _dates.parse_date_iso(self.events[event_id]["start"]),
                    _dates.parse_date_iso(self.events[event_id]["end"])
                    if _text(self.events[event_id].get("end"))
                    else None,
                )
                for event_id in seen
            ),
            self.settings.default_duration,
        )
        self.ends = {item.id: item for item in self.timeline}
        # default: the cards are ordered by the time they start
        return [item.id for item in self.timeline]

    def _place_name(self, event_id: str) -> str:
        place = self.site.get("locations", {}).get(self.events[event_id]["location"], {})
        return _text(place.get("name")) if place.get("ready", True) is not False else ""

    def _text_values(self) -> dict[str, str]:
        primary = self.events[self.primary_id]
        start = _dates.parse_date_iso(primary["start"])
        values = {
            "coupleNames": self.site["coupleNames"],
            "greeting": self.invitation["greeting"],
            "eventTitle": primary["title"],
            "eventDate": _dates.format_date(start),
            "eventTime": _dates.format_time(start),
            "eventPlace": self._place_name(self.primary_id),
        }
        if _text(self.site.get("rsvpDeadline")):
            values["rsvpDeadline"] = self.site["rsvpDeadline"]
        return values

    def resolve(self, value: Any, parts: Sequence[Any]) -> str:
        """A text of `site.json` in the form of the invitation."""
        if value is None:
            return ""
        self._check_place(value, parts)
        return resolve_text(value, self.form, self.values, PLACEHOLDERS)

    def resolve_note(self, value: Any) -> str:
        """A note of the invitation (placeholders only, no forms)."""
        if not _text(value):
            return ""
        return substitute(value, self.values, PLACEHOLDERS)

    def _check_place(self, value: Any, parts: Sequence[Any]) -> None:
        if self.warn is None or self.values["eventPlace"]:
            return
        texts = value.values() if isinstance(value, dict) else [value]
        if any(isinstance(text, str) and "{eventPlace}" in text for text in texts):
            path = format_path(parts)
            key = (path, self.primary_id)
            if key not in self.place_warnings:
                self.place_warnings.add(key)
                self.warn(
                    f"{SITE_FILE}: field '{path}' uses {{eventPlace}}, but the place of "
                    f"the event {show_id(self.primary_id)} is not announced yet (the "
                    "text gets an empty string)"
                )

    def event(self, event_id: str, parent_id: str, same_day: bool | None = None) -> dict:
        """An event inside the element `parent_id` ("" at the root: no ids)."""
        own_id = dom_id(parent_id, event_part(event_id))
        raw = self.events[event_id]
        self.usage.events.add(event_id)
        entry = self.ends[event_id]
        start_text = raw["start"]
        end_text = raw["end"] if entry.explicit else entry.end.isoformat()
        same_day = self.same_day if same_day is None else same_day
        is_primary = event_id == self.primary_id
        return {
            "id": event_id,
            "domId": own_id,
            "title": raw["title"],
            "description": _text(raw.get("description")),
            "note": self.resolve_note(self.event_overrides.get(event_id, {}).get("note")),
            "isMain": event_id == self.main_id,
            "isPrimary": is_primary,
            "kind": "primary" if is_primary else "regular",
            "startISO": start_text,
            "endISO": end_text,
            # default: the build always writes the dates, an event has no
            # spelling of its own
            "dateText": _dates.format_date(entry.start),
            "timeText": _dates.format_time(entry.start),
            "whenText": _dates.format_when(entry.start, entry.end if entry.explicit else None),
            "tabText": _dates.format_tab(entry.start, same_day),
            "icsPath": f"{self.settings.pages_path}/{self.invitation['token']}/{event_id}.ics",
            "location": self.location(
                raw["location"], dom_id(own_id, location_part(raw["location"])), nested=True
            ),
            "program": self.program(raw, own_id),
        }

    def program(self, raw: dict, event_dom_id: str) -> dict | None:
        """The programme of an event; null when it has none (or it is empty)."""
        if not _text(raw.get("schedule")):
            return None
        items = self.schedule(raw["schedule"])
        if not items:
            return None
        return {"domId": dom_id(event_dom_id, PROGRAM_PART), "items": items, "nested": True}

    def cover_event(self) -> str:
        # default: the cover shows the primary event of the invitation, even
        # when its events fall on different days
        return self.primary_id

    # -- registries ------------------------------------------------------------

    def location(self, location_id: str, own_id: str, *, nested: bool, compact: bool = False) -> dict:
        place = self.site.get("locations", {})[location_id]
        self.usage.locations.add(location_id)
        ready = place.get("ready", True) is not False
        tree = {
            "id": location_id,
            "domId": own_id,
            "ready": ready,
            "name": "",
            "address": "",
            "description": "",
            "mapLinks": {"google": "", "yandex": "", "apple": ""},
            "hasMapLinks": False,
            "photos": [],
            "directions": None,
            "compact": compact,
            "nested": nested,
        }
        if not ready:
            return tree
        name, address = _text(place.get("name")), _text(place.get("address"))
        links = _maps.map_links(name, address, place.get("geo"), place.get("maps"))
        tree.update(
            name=name,
            address=address,
            description=_text(place.get("description")),
            mapLinks=links,
            hasMapLinks=_maps.has_map_links(links),
            photos=[self.media(media_id) for media_id in place.get("photos", [])],
            directions=self.media(place["directions"]) if _text(place.get("directions")) else None,
        )
        return tree

    def schedule(self, schedule_id: str) -> list[dict]:
        items = self.site.get("schedules", {})[schedule_id]
        self.usage.schedules.add(schedule_id)
        return [
            {
                "time": _text(item.get("time")),
                "title": item["title"],
                "text": _text(item.get("text")),
            }
            for item in items
        ]

    def _src(self, name: str) -> str:
        if not name:
            return ""
        if self.settings.media_src is not None:
            return self.settings.media_src(name)
        return _maps.media_url(self.media_path, name)

    def media(self, media_id: str) -> dict:
        item = self.site["media"][media_id]
        self.usage.media.add(media_id)
        kind = item["type"]
        is_video = kind == "video"
        file, poster, thumb = (_text(item.get(key)) for key in ("file", "poster", "thumb"))
        infos = self.settings.media_info
        width, height = item.get("width"), item.get("height")
        if not (width and height):
            width = height = None
            for name in (poster if is_video else file, file if is_video else ""):
                info = infos.get(name) if name else None
                if info is not None and info.width and info.height:
                    width, height = info.width, info.height
                    break
        duration = ""
        if is_video and infos.get(file) is not None:
            duration = format_duration(infos[file].duration_seconds)
        given_alt = _text(item.get("alt"))
        alt = given_alt or DEFAULT_ALT[kind]
        if is_video:
            label = f"{VIDEO_LABEL}: {given_alt}" if given_alt else VIDEO_LABEL
            label += f", {duration}" if duration else ""
        else:
            label = alt
        return {
            "id": media_id,
            "type": kind,
            "isVideo": is_video,
            "src": self._src(file),
            "posterSrc": self._src(poster) if is_video else "",
            "thumbSrc": self._src(thumb or (poster if is_video else file)),
            "width": width or "",
            "height": height or "",
            "ratio": f"{width} / {height}" if width and height else "",
            "durationText": duration,
            "label": label,
            "alt": alt,
        }

    # -- sections and widgets ------------------------------------------------------

    def tree(self) -> dict:
        sections = []
        for position, section in enumerate(self.site["sections"]):
            resolved = self.section(section, position)
            if resolved is not None:
                sections.append(resolved)
                self.usage.sections.add(section["id"])
        self._warn_notes()
        images = {key: self.settings.site_images.get(key, "") for key in SITE_IMAGE_FIELDS}
        return {
            "greeting": self.invitation["greeting"],
            "form": self.form,
            "ty": self.form == "ty",
            "vy": self.form == "vy",
            "coupleNames": self.site["coupleNames"],
            "rsvpDeadline": _text(self.site.get("rsvpDeadline")),
            "mediaPath": self.media_path,
            **images,
            "primaryEvent": self.event(self.primary_id, ""),
            "sections": sections,
        }

    def section(self, section: dict, position: int) -> dict | None:
        section_id = section["id"]
        override = self.section_overrides.get(section_id, {})
        if not override.get("visible", section.get("visible", True)):
            return None
        kind = section.get("type", "custom")
        own_id = section_part(section_id)
        tree = {
            "id": section_id,
            "type": kind,
            "domId": own_id,
            "titleId": dom_id(own_id, TITLE_PART),
            "title": "",
            "titleHidden": False,
            "align": "start",
            "width": "narrow",
            "note": "",
            "eyebrow": "",
            "event": None,
            "widgets": [],
        }
        parts = ("sections", position)
        if kind == "cover":
            event_id = self.cover_event()
            tree.update(
                eyebrow=self.resolve(section.get("eyebrow"), (*parts, "eyebrow")),
                event=self.event(event_id, own_id),
            )
            return tree
        # default: an invitation switches the widgets of a section by their id
        widget_overrides = override.get("widgets", {})
        widgets = []
        for number, widget in enumerate(section.get("widgets", []), start=1):
            widget_id = widget.get("id")
            shown = widget_overrides.get(widget_id, {}).get("visible") if widget_id else None
            if not (shown if shown is not None else widget.get("visible", True)):
                continue
            # the number is the position in the data, hidden widgets included
            resolved = self.widget(
                widget,
                dom_id(own_id, widget_part(number)),
                tree["titleId"],
                (*parts, "widgets", number - 1),
            )
            if resolved is not None:
                widgets.append(resolved)
        note = self.resolve_note(override.get("note"))
        if not widgets and not note:
            if self.warn is not None and override.get("visible") is True:
                self.warn(
                    f"{self.label}: section {show_id(section_id)} is switched on, but has "
                    "nothing to show for this invitation"
                )
            return None
        tree.update(
            title=self.resolve(section.get("title"), (*parts, "title")),
            titleHidden=section.get("titleHidden", False),
            align=section.get("align", "start"),
            width=section.get("width", "narrow"),
            note=note,
            widgets=widgets,
        )
        return tree

    def widget(self, widget: dict, own_id: str, labelled_by: str, parts: tuple) -> dict | None:
        kind = widget["type"]
        base = {"type": kind, "domId": own_id, "labelledBy": labelled_by}
        if kind == "text":
            text = self.resolve(widget["text"], (*parts, "text"))
            if not text.strip():
                return None
            return {**base, "text": text, "variant": widget.get("variant", "body")}
        if kind == "date":
            event_id = widget.get("event") or self.primary_id
            if event_id not in self.visible:
                return None
            return {
                **base,
                "event": self.event(event_id, own_id),
                "countdown": widget.get("countdown", True),
                "calendar": widget.get("calendar", True),
                "showEventTitle": len(self.visible) > 1,
            }
        if kind == "events":
            wanted = widget.get("events")
            ids = [item for item in self.visible if wanted is None or item in wanted]
            if not ids:
                return None
            self.cards.update(ids)
            same_day = _dates.same_local_day(self.ends[item].start for item in ids)
            items = [self.event(item, own_id, same_day) for item in ids]
            primary = next((item["domId"] for item in items if item["isPrimary"]), "")
            return {
                **base,
                "items": items,
                "count": len(items),
                "multiple": len(items) > 1,
                "primaryDomId": primary,
            }
        if kind == "location":
            location_id = widget["location"]
            return {
                **base,
                "location": self.location(
                    location_id,
                    dom_id(own_id, location_part(location_id)),
                    nested=False,
                    compact=widget.get("variant") == "compact",
                ),
            }
        if kind == "schedule":
            schedule_id = widget["schedule"]
            owners = [
                event_id
                for event_id, event in self.events.items()
                if event.get("schedule") == schedule_id
            ]
            if owners and not any(owner in self.visible for owner in owners):
                return None  # the programme of events the invitation does not see
            items = self.schedule(schedule_id)
            if not items:
                return None
            return {**base, "items": items, "nested": False}
        if kind == "media":
            return {
                **base,
                "items": [self.media(media_id) for media_id in widget["items"]],
                "layout": widget.get("layout", "grid"),
            }
        raise ValueError(f"unknown widget type {show_id(kind)}")

    def _warn_notes(self) -> None:
        if self.warn is None:
            return
        for event_id, override in self.event_overrides.items():
            if event_id in self.visible and _text(override.get("note")) and event_id not in self.cards:
                self.warn(
                    f"{self.label}: field 'events.{event_id}.note' is set, but no 'events' "
                    "widget on this page shows the event"
                )


# --------------------------------------------------------------------------
# All invitations
# --------------------------------------------------------------------------


def build_page(
    site: dict,
    invitation: dict,
    settings: PageSettings,
    usage: Usage | None = None,
) -> dict:
    """The tree of one invitation (no warnings); the data must be valid."""
    page = _Page(site, invitation, settings, usage or Usage(), None, "", set())
    return page.tree()


def visible_events(site: dict, invitation: dict, settings: PageSettings) -> list[dict]:
    """The events an invitation sees, by start, as its tree shows them.

    Each is the event of the tree without DOM ids (as `primaryEvent`): the
    effective end, the place and the programme are the same as on the page.
    The calendar files of the invitation are made from these.
    """
    page = _Page(site, invitation, settings, Usage(), None, "", set())
    return [page.event(event_id, "") for event_id in page.visible]


def build_pages(
    site: dict,
    invitations: Sequence[dict],
    settings: PageSettings,
    report: Any = None,
) -> tuple[list[dict], Usage]:
    """The trees of all invitations and what they show.

    The data must have passed `tools._schema.check_data`.  With a `report`,
    the warnings that need the finished trees are reported to it, including
    `warn_unused`, and an error for DOM ids that repeat on a page (a
    safeguard: `dom_id` makes the ids unique by construction).
    """
    usage = Usage()
    warn = report_warning(report) if report is not None else None
    place_warnings: set[tuple[str, str]] = set()
    pages = []
    for number, invitation in enumerate(invitations, start=1):
        label = invitation_label(number)
        page = _Page(site, invitation, settings, usage, warn, label, place_warnings)
        tree = page.tree()
        repeated = repeated_ids(tree)
        if repeated and report is not None:
            # a safeguard: the ids are built so that this cannot happen
            listed = ", ".join(f"'{item}'" for item in repeated if _SAFE_DOM_ID_RE.fullmatch(item))
            report.error(
                f"{label}: ids of the markup repeat on the page ({listed or 'not shown'}); "
                "rename one of the ids in site.json"
            )
        pages.append(tree)
    if report is not None:
        warn_unused(site, usage, report)
    return pages, usage


#: What a DOM id built from valid ids looks like (safe to show in a message).
_SAFE_DOM_ID_RE = re.compile(r"[a-z0-9-]{1,300}")


def repeated_ids(tree: Any) -> list[str]:
    """The DOM ids (`domId`, `titleId`) that occur more than once in a tree."""
    seen: set[str] = set()
    repeated: list[str] = []
    stack = [tree]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key in ("domId", "titleId"):
                item = value.get(key)
                if isinstance(item, str) and item:
                    if item in seen and item not in repeated:
                        repeated.append(item)
                    seen.add(item)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return sorted(repeated)


def warn_unused(site: dict, usage: Usage, report: Any) -> None:
    """Warn about the parts of `site.json` that no page shows."""
    warn = report_warning(report)
    for section in site.get("sections", []):
        if section["id"] not in usage.sections:
            warn(f"{SITE_FILE}: section {show_id(section['id'])} is not shown on any page")
    registries = (
        ("events", "event", usage.events),
        ("locations", "location", usage.locations),
        ("schedules", "schedule", usage.schedules),
        ("media", "media item", usage.media),
    )
    for registry, what, shown in registries:
        for entry_id in site.get(registry, {}):
            if entry_id in shown:
                continue
            tail = " and its files are not published" if registry == "media" else ""
            warn(f"{SITE_FILE}: {what} {show_id(entry_id)} is not shown on any page{tail}")


def media_files(site: dict, usage: Usage) -> list[tuple[str, str]]:
    """(field path, file name) of every file of the media items shown."""
    files = []
    for media_id in sorted(usage.media):
        item = site["media"][media_id]
        for key in ("file", "poster", "thumb"):
            if _text(item.get(key)):
                files.append((f"media.{media_id}.{key}", item[key]))
    return files
