"""The section, widget and media fragments against the page tree contract.

The page tree is what the data layer hands to the fragments: every field a
fragment reads always exists (an empty string, `[]`, `false` or `null` for an
object).  Here the trees are written by hand.  Every node of the tree is a
`Strict` dict: a fragment that asks for a field outside the contract, even in
an `if:`, fails the test instead of quietly rendering nothing.

The full pages are rendered with `template.html` twice: from hand-written
trees of eight fictional guests that cover every combination of the form of
address, out-of-town guests and "+1", and from the trees that the data layer
(`tools._page.build_pages`) builds from the fixture data of the format 2.
Both are wrapped in `Strict` nodes, so a fragment and the data layer cannot
drift apart unnoticed.
"""

from __future__ import annotations

import collections
import hashlib
import re
import unittest
from html.parser import HTMLParser
from typing import Callable

from tests import fixtures_v2 as F
from tests.support import ROOT, build
from tools import _maps, _page, _schema

FRAGMENTS_DIR = ROOT / build.FRAGMENTS_DIRNAME

#: The registered types: the file names in fragments/sections, widgets, media
#: are the types the checks of the data accept.
SECTION_TYPES = _schema.SECTION_TYPES
WIDGET_TYPES = _schema.WIDGET_TYPES
MEDIA_TYPES = _schema.MEDIA_TYPES

# --- the contract: the fields of every node of the page tree ----------------

ROOT_FIELDS = (
    "greeting", "form", "ty", "vy", "coupleNames", "rsvpDeadline", "mediaPath",
    "faviconPath", "faviconType", "ogImage", "ogImageType", "ogImageWidth",
    "ogImageHeight", "primaryEvent", "sections",
)
SECTION_FIELDS = {
    "cover": ("id", "type", "domId", "titleId", "eyebrow", "event", "background",
              "backgroundFocus"),
    "custom": ("id", "type", "domId", "titleId", "title", "titleHidden", "align", "width",
               "note", "widgets"),
}
WIDGET_COMMON_FIELDS = ("type", "domId", "labelledBy")
WIDGET_FIELDS = {
    "text": ("text", "variant"),
    "date": ("event", "countdown", "calendar", "showEventTitle"),
    "events": ("items", "count", "multiple", "primaryDomId"),
    "location": ("location",),
    "schedule": ("items", "nested"),
    "media": ("items", "layout"),
}
EVENT_FIELDS = (
    "id", "domId", "title", "description", "note", "isMain", "isPrimary", "kind",
    "startISO", "endISO", "dateText", "timeText", "whenText", "tabText", "icsPath",
    "location", "program",
)
#: `primaryEvent` in the root has the fields of every event; its DOM id is ""
#: (and so are those of its place and its program): it is not on the page.
PRIMARY_EVENT_FIELDS = EVENT_FIELDS
LOCATION_FIELDS = (
    "id", "domId", "ready", "name", "address", "description", "mapLinks", "hasMapLinks",
    "photos", "directions", "compact", "nested",
)
MAP_LINK_FIELDS = ("google", "yandex", "apple")
MEDIA_FIELDS = (
    "id", "type", "isVideo", "src", "posterSrc", "thumbSrc", "width", "height", "ratio",
    "durationText", "label", "alt",
)
SCHEDULE_ITEM_FIELDS = ("time", "title", "text")
#: The program of an event (`null` when it has none).
PROGRAM_FIELDS = ("domId", "items", "nested")

#: Checked enumerations: the only fields a class modifier may come from.
ENUM_FIELDS = {
    "align": ("start", "center"),
    "width": ("narrow", "wide"),
    "variant": ("body", "lead", "signature"),
    "layout": ("grid", "ribbon", "single"),
    "kind": ("primary", "regular"),
    "backgroundFocus": _schema.COVER_FOCUSES,
}

#: Script hooks whose elements must never carry the reveal mark.
NO_REVEAL_HOOKS = (
    "data-gallery", "data-lightbox", "data-countdown", "data-events-panel",
    "data-events-tablist",
)


class Strict(dict):
    """A node of the page tree that fails on a field outside the contract."""

    def __contains__(self, key: object) -> bool:
        if not dict.__contains__(self, key):
            raise AssertionError(f"a fragment reads the field {key!r}, which the node lacks")
        return True


def node(fields: tuple[str, ...], **values) -> Strict:
    """A tree node with exactly the fields of the contract."""
    missing = sorted(set(fields) - set(values))
    extra = sorted(set(values) - set(fields))
    if missing or extra:
        raise AssertionError(f"fixture node: missing {missing}, extra {extra}")
    return Strict(values)


# --- fictional site data -----------------------------------------------------

COUPLE_NAMES = "Алиса и Боб"
RSVP_DEADLINE = "1 мая 2030"
MEDIA_DIR = "Zq7Lw2Xn9Rb4Tc1Vy8Hs5A"
MEDIA_PATH = f"/assets/{MEDIA_DIR}"
MAIN_EVENT = "dinner"


def hashed_url(file: str) -> str:
    """The published address of a media file: a content-hash name (here: of the name)."""
    stem, dot, extension = file.rpartition(".")
    return f"{MEDIA_PATH}/{hashlib.sha256(file.encode()).hexdigest()[:16]}.{extension}"


MEDIA = {
    # the photo behind the cover: no description of its own (the tree has the
    # neutral label, the page an empty alt)
    "cover": {"type": "image", "file": "cover.png", "size": (2400, 1600), "alt": "Фотография"},
    "registry-1": {"type": "image", "file": "registry-1.png", "size": (1200, 900),
                   "alt": "Дворец бракосочетания"},
    "venue-1": {"type": "image", "file": "venue-1.png", "size": (1200, 800),
                "alt": "Усадьба со стороны пруда"},
    "venue-2": {"type": "image", "file": "venue-2.png", "size": (1200, 800),
                "alt": "Сад усадьбы"},
    "venue-3": {"type": "image", "file": "venue-3.png", "size": (1200, 800),
                "alt": "Главный зал"},
    "walk": {"type": "video", "file": "walk.mp4", "poster": "walk-poster.png",
             "size": (1280, 720), "duration": "0:05", "alt": "Прогулка по саду"},
    "directions": {"type": "image", "file": "directions.png", "size": (1600, 900),
                   "alt": "Схема проезда от станции"},
    "story-1": {"type": "image", "file": "story-1.png", "size": (1200, 900),
                "alt": "Первая встреча"},
    "proposal": {"type": "video", "file": "proposal.mp4", "poster": "proposal-poster.png",
                 "size": (720, 1280), "duration": "0:20", "alt": "предложение"},
    # the size of this one is unknown: the tile goes without width and height
    "story-2": {"type": "image", "file": "story-2.png", "size": None, "alt": "Помолвка"},
}

LOCATIONS = {
    "registry": {"name": "Дворец бракосочетания Энска",
                 "address": "г. Энск, Центральная площадь, д. 2",
                 "geo": {"lat": 12.340001, "lng": 23.450002}, "photos": ["registry-1"]},
    "manor": {"name": "Усадьба «Образец»", "address": "г. Энск, ул. Примерная, д. 1",
              "description": "Старинная усадьба на берегу пруда.\n\n"
                             "На территории есть бесплатная парковка.",
              "geo": {"lat": 12.345678, "lng": 23.456789},
              "maps": {"googlePlaceId": "EXAMPLE-not-a-real-place-id"},
              "photos": ["venue-1", "venue-2", "walk", "venue-3"],
              "directions": "directions"},
    "terrace": {"ready": False},
    "hotel": {"name": "Гостиница «Пример»", "address": "г. Энск, Вокзальная ул., д. 5",
              "geo": {"lat": 12.35, "lng": 23.46}},
}

SCHEDULES = {
    "dinner": [
        {"time": "15:30", "title": "Сбор гостей", "text": "Напитки и закуски в саду."},
        {"time": "16:00", "title": "Церемония", "text": "Выездная регистрация у пруда."},
        {"time": "17:00", "title": "Фотосессия"},
        {"time": "18:00", "title": "Праздничный ужин",
         "text": "Главный зал усадьбы.\nРассадка — по карточкам."},
        {"time": "", "title": "Завершение вечера"},
    ],
    "transfer": [
        {"time": "14:45", "title": "От вокзала до усадьбы", "text": "Автобус у главного входа."},
        {"time": "23:15", "title": "Обратно к вокзалу"},
    ],
}

#: Events with the strings the data layer generates (dates in the offset of the venue).
EVENTS = {
    "ceremony": {"title": "Регистрация", "location": "registry",
                 "start": "2030-06-15T11:00:00+03:00", "end": "2030-06-15T12:00:00+03:00",
                 "dateText": "15 июня 2030", "timeText": "11:00", "dayMonth": "15 июня",
                 "whenText": "Суббота, 15 июня 2030, 11:00–12:00"},
    "dinner": {"title": "Праздничный ужин", "location": "manor", "schedule": "dinner",
               "description": "Дресс-код — светлые тона.",
               "start": "2030-06-15T16:00:00+03:00", "end": "2030-06-15T23:30:00+03:00",
               "dateText": "15 июня 2030", "timeText": "16:00", "dayMonth": "15 июня",
               "whenText": "Суббота, 15 июня 2030, 16:00–23:30"},
    # hidden by default; no end: the effective end is six hours after the start
    "brunch": {"title": "Второй день", "location": "terrace", "visible": False,
               "start": "2030-06-16T12:00:00+03:00", "end": "2030-06-16T18:00:00+03:00",
               "dateText": "16 июня 2030", "timeText": "12:00", "dayMonth": "16 июня",
               "whenText": "Воскресенье, 16 июня 2030, 12:00"},
}

SECTIONS = [
    {"id": "cover", "type": "cover", "eyebrow": "Приглашение", "background": "cover",
     "backgroundFocus": "top"},
    {"id": "invite", "title": "{greeting}", "align": "center", "widgets": [
        {"type": "text", "variant": "lead", "text": {
            "ty": "Мы, {coupleNames}, приглашаем тебя на наш праздник.\n\n"
                  "Ждём тебя {eventDate} в {eventTime}.",
            "vy": "Мы, {coupleNames}, приглашаем вас на наш праздник.\n\n"
                  "Ждём вас {eventDate} в {eventTime}."}},
    ]},
    {"id": "personal", "title": "Несколько слов лично", "widgets": []},
    {"id": "plus-one", "title": "Плюс один", "visible": False, "widgets": [
        {"type": "text", "text": {
            "ty": "Ты можешь прийти со спутником или спутницей.",
            "vy": "Вы можете прийти со спутником или спутницей."}},
    ]},
    {"id": "when", "title": "Дата и время", "align": "center", "widgets": [{"type": "date"}]},
    {"id": "where", "title": "Где и когда", "width": "wide", "widgets": [{"type": "events"}]},
    {"id": "travel", "title": "Гостям из других городов", "visible": False, "widgets": [
        {"type": "text", "text": "От вокзала до усадьбы и обратно ходит трансфер."},
        {"type": "location", "location": "hotel", "variant": "compact"},
        {"type": "schedule", "schedule": "transfer"},
        {"id": "hotel-booked", "type": "text", "visible": False, "text": {
            "ty": "Для тебя забронирован номер в этой гостинице.",
            "vy": "Для вас забронирован номер в этой гостинице."}},
    ]},
    {"id": "story", "title": "Наша история", "width": "wide", "widgets": [
        {"type": "text", "text": "Несколько кадров из нашей истории."},
        {"type": "media", "items": ["story-1", "proposal", "story-2"]},
    ]},
    {"id": "venue", "title": "Место", "titleHidden": True, "width": "wide", "widgets": [
        {"type": "location", "location": "manor"},
        {"type": "media", "layout": "single", "items": ["walk"]},
    ]},
    {"id": "rsvp", "title": "Подтверждение", "align": "center", "widgets": [
        {"type": "text", "text": {
            "ty": "Пожалуйста, ответь нам до {rsvpDeadline}.\n\nБудем очень рады встрече!",
            "vy": "Пожалуйста, ответьте нам до {rsvpDeadline}.\n\nБудем очень рады встрече!"}},
        {"type": "text", "variant": "signature", "text": "{coupleNames}"},
    ]},
]

#: Eight guests: ty/vy × local/out of town × "+1" and the rest of the example matrix.
GUESTS = [
    {"token": "0R-pnCqGFgrPOR5e-NluZw", "greeting": "Дорогая Кэрол!", "form": "ty",
     "events": {"brunch": {"visible": True}},
     "sections": {"personal": {"note": "Спасибо, что столько лет рядом. Очень ждём! <3"},
                  "plus-one": {"visible": True}}},
    {"token": "oWaEF01q51e11fwVhCZQqQ", "greeting": "Дорогой Дэйв!", "form": "ty",
     "events": {"dinner": {"note": "Напиши нам, пожалуйста, про аллергию."}}},
    {"token": "48lh3whME-jqDOw3yzzWpQ", "greeting": "Дорогая Ева!", "form": "ty",
     "primaryEvent": "ceremony", "events": {"brunch": {"visible": True}},
     "sections": {"personal": {"note": "Помнишь, как мы познакомились в поезде?\n\nЖдём!"},
                  "travel": {"visible": True,
                             "note": "Встретим тебя на вокзале — скажи, каким поездом."}}},
    {"token": "lXG6kSgXRvYCN6GGk5IcBA", "greeting": "Дорогой Трент!", "form": "ty",
     "sections": {"plus-one": {"visible": True}, "travel": {"visible": True}}},
    {"token": "uGeJJ1NFz9Q2YYhekmwPtw", "greeting": "Дорогие Пегги и Виктор!", "form": "vy",
     "primaryEvent": "ceremony",
     "sections": {"personal": {"note": "Очень надеемся, что вы разделите с нами этот день."}}},
    {"token": "IBAhKaLEOj1DHuOUdUMzQA", "greeting": "Дорогие Грейс и Фрэнк!", "form": "vy",
     "sections": {"travel": {"visible": True, "note": "Заезд в гостиницу — накануне.",
                             "widgets": {"hotel-booked": {"visible": True}}}}},
    {"token": "hoC853fn9btumFjSJTlzpQ", "greeting": "Уважаемый Оскар Петрович!", "form": "vy",
     "events": {"ceremony": {"visible": False}},
     "sections": {"personal": {"note": "Для нас большая честь пригласить вас."},
                  "plus-one": {"visible": True}}},
    {"token": "04e2efb6-33cd-423f-acff-3c14562a3113", "greeting": "Уважаемая Хайди Андреевна!",
     "form": "vy",
     "sections": {"plus-one": {"visible": True},
                  "travel": {"visible": True, "note": "Трансфер будет ждать у главного входа."},
                  "where": {"note": "Если понадобится помощь с дорогой — напишите нам."}}},
]


# --- the page tree of one guest -------------------------------------------------

def join_id(*parts: str) -> str:
    """A DOM id from its parts, joined by `--` (never inside an id of the data)."""
    return "--".join(parts) if parts and parts[0] else ""


def media_node(media_id: str, url: Callable[[str], str] = hashed_url) -> Strict:
    item = MEDIA[media_id]
    video = item["type"] == "video"
    width, height = item["size"] or ("", "")
    src = url(item["file"])
    poster = url(item["poster"]) if video else ""
    duration = item.get("duration", "") if video else ""
    return node(
        MEDIA_FIELDS, id=media_id, type=item["type"], isVideo=video, src=src,
        posterSrc=poster, thumbSrc=poster or src, width=width, height=height,
        ratio=f"{width} / {height}" if width else "", durationText=duration,
        label=f"Видео: {item['alt']}" + (f", {duration}" if duration else "") if video else "",
        alt=item["alt"],
    )


def location_node(location_id: str, dom_id: str, compact: bool, nested: bool,
                  url: Callable[[str], str] = hashed_url) -> Strict:
    item = LOCATIONS[location_id]
    ready = item.get("ready", True)
    name, address = item.get("name", ""), item.get("address", "")
    links = _maps.map_links(name, address, item.get("geo"), item.get("maps")) if ready else {
        "google": "", "yandex": "", "apple": ""}
    directions = item.get("directions")
    return node(
        LOCATION_FIELDS, id=location_id, domId=dom_id, ready=ready, name=name,
        address=address, description=item.get("description", ""),
        mapLinks=node(MAP_LINK_FIELDS, **links), hasMapLinks=any(links.values()),
        photos=[media_node(media_id, url) for media_id in item.get("photos", [])],
        directions=media_node(directions, url) if directions else None,
        compact=compact, nested=nested,
    )


def schedule_items(schedule_id: str | None) -> list[Strict]:
    return [node(SCHEDULE_ITEM_FIELDS, **{"time": "", "text": "", **item})
            for item in SCHEDULES.get(schedule_id or "", [])]


class PageTree:
    """Builds the page tree of one guest the way the data layer does."""

    def __init__(self, guest: dict, url: Callable[[str], str] = hashed_url):
        self.guest = guest
        self.url = url
        overrides = guest.get("events", {})
        self.visible = sorted(
            (event_id for event_id, event in EVENTS.items()
             if overrides.get(event_id, {}).get("visible", event.get("visible", True))),
            key=lambda event_id: EVENTS[event_id]["start"],
        )
        self.primary = guest.get("primaryEvent", MAIN_EVENT)
        assert self.primary in self.visible
        same_day = len({EVENTS[event_id]["dateText"] for event_id in self.visible}) == 1
        self.same_day = same_day

    def text(self, value, primary: dict | None = None) -> str:
        if isinstance(value, dict):
            value = value[self.guest["form"]]
        event = EVENTS[self.primary]
        location = LOCATIONS[event["location"]]
        values = {
            "coupleNames": COUPLE_NAMES, "greeting": self.guest["greeting"],
            "rsvpDeadline": RSVP_DEADLINE, "eventTitle": event["title"],
            "eventDate": event["dateText"], "eventTime": event["timeText"],
            "eventPlace": location.get("name", ""),
        }
        # `{{` and `}}` are literal braces, `{name}` is a substitution
        return re.sub(r"\{\{|\}\}|\{(\w+)\}",
                      lambda match: values[match.group(1)] if match.group(1)
                      else match.group(0)[0], value)

    def event(self, event_id: str, dom_id: str | None) -> Strict:
        item = EVENTS[event_id]
        fields = EVENT_FIELDS if dom_id is not None else PRIMARY_EVENT_FIELDS
        values = dict(
            id=event_id, title=item["title"], description=item.get("description", ""),
            note=self.guest.get("events", {}).get(event_id, {}).get("note", ""),
            isMain=event_id == MAIN_EVENT, isPrimary=event_id == self.primary,
            kind="primary" if event_id == self.primary else "regular",
            startISO=item["start"], endISO=item["end"], dateText=item["dateText"],
            timeText=item["timeText"], whenText=item["whenText"],
            tabText=item["timeText"] if self.same_day
            else f"{item['dayMonth']}, {item['timeText']}",
            icsPath=hashed_url(f"{event_id}.ics"),
            location=location_node(item["location"],
                                   join_id(dom_id or "", f"l-{item['location']}"),
                                   compact=False, nested=True, url=self.url),
            program=node(PROGRAM_FIELDS, domId=join_id(dom_id or "", "program"),
                         items=schedule_items(item["schedule"]), nested=True)
            if item.get("schedule") else None,
        )
        values["domId"] = dom_id or ""
        return node(fields, **values)

    def widget(self, config: dict, dom_id: str, labelled_by: str) -> Strict:
        kind = config["type"]
        common = dict(type=kind, domId=dom_id, labelledBy=labelled_by)
        fields = WIDGET_COMMON_FIELDS + WIDGET_FIELDS[kind]
        if kind == "text":
            return node(fields, **common, text=self.text(config["text"]),
                        variant=config.get("variant", "body"))
        if kind == "date":
            return node(fields, **common, event=self.event(self.primary, join_id(dom_id, f"e-{self.primary}")),
                        countdown=True, calendar=True, showEventTitle=len(self.visible) > 1)
        if kind == "events":
            items = [self.event(event_id, join_id(dom_id, f"e-{event_id}")) for event_id in self.visible]
            return node(fields, **common, items=items, count=len(items),
                        multiple=len(items) > 1, primaryDomId=join_id(dom_id, f"e-{self.primary}"))
        if kind == "location":
            location_id = config["location"]
            return node(fields, **common, location=location_node(
                location_id, join_id(dom_id, f"l-{location_id}"),
                compact=config.get("variant") == "compact", nested=False, url=self.url))
        if kind == "schedule":
            return node(fields, **common, items=schedule_items(config["schedule"]), nested=False)
        return node(fields, **common, items=[media_node(item, self.url) for item in config["items"]],
                    layout=config.get("layout", "grid"))

    def sections(self) -> list[Strict]:
        result = []
        overrides = self.guest.get("sections", {})
        for config in SECTIONS:
            section_id, dom_id = config["id"], f"s-{config['id']}"
            if config.get("type") == "cover":
                result.append(node(
                    SECTION_FIELDS["cover"], id=section_id, type="cover", domId=dom_id,
                    titleId=join_id(dom_id, "title"), eyebrow=config["eyebrow"],
                    event=self.event(self.primary, join_id(dom_id, f"e-{self.primary}")),
                    background=media_node(config["background"], self.url)
                    if config.get("background") else None,
                    backgroundFocus=config.get("backgroundFocus", "center")))
                continue
            override = overrides.get(section_id, {})
            if not override.get("visible", config.get("visible", True)):
                continue
            widgets = [
                self.widget(widget, join_id(dom_id, f"w{number}"), join_id(dom_id, "title"))
                for number, widget in enumerate(config["widgets"], 1)
                if override.get("widgets", {}).get(widget.get("id"), {}).get(
                    "visible", widget.get("visible", True))
            ]
            note = self.text(override.get("note", ""))
            if not widgets and not note:
                continue
            result.append(node(
                SECTION_FIELDS["custom"], id=section_id, type="custom", domId=dom_id,
                titleId=join_id(dom_id, "title"), title=self.text(config["title"]),
                titleHidden=config.get("titleHidden", False),
                align=config.get("align", "start"), width=config.get("width", "narrow"),
                note=note, widgets=widgets))
        return result

    def root(self) -> Strict:
        return node(
            ROOT_FIELDS, greeting=self.guest["greeting"], form=self.guest["form"],
            ty=self.guest["form"] == "ty", vy=self.guest["form"] == "vy",
            coupleNames=COUPLE_NAMES, rsvpDeadline=RSVP_DEADLINE, mediaPath=MEDIA_PATH,
            faviconPath="/assets/favicon.svg", faviconType="image/svg+xml",
            ogImage="/assets/og.png", ogImageType="image/png", ogImageWidth=1200,
            ogImageHeight=630, primaryEvent=self.event(self.primary, None),
            sections=self.sections(),
        )


def page_tree(guest: dict, url: Callable[[str], str] = hashed_url) -> Strict:
    return PageTree(guest, url).root()


# --- rendering --------------------------------------------------------------------

def page_frame() -> str:
    """`template.html`: the page head and `<main>` made of the sections."""
    source = (ROOT / build.TEMPLATE_FILE).read_text(encoding="utf-8")
    assert '<main class="page">\n<!-- include:partials/sections -->\n</main>' in source
    return source


def load_fragments() -> build.Fragments:
    return build.load_fragments(FRAGMENTS_DIR)


def render_page(tree: dict, fragments: build.Fragments | None = None) -> str:
    """A finished page: rendered, comments removed (as the build publishes it)."""
    template = build.parse_template(page_frame(), "page", fragments or load_fragments())
    return build.strip_html_comments(template.render(tree), "page")


def published_files(tree: dict) -> set[str]:
    """Every local file the page may refer to, as `check_html` sees them."""
    files = {"assets/app.css", "assets/app.js", "assets/favicon.svg", "assets/og.png"}

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("src", "posterSrc", "thumbSrc", "icsPath") and item:
                    files.add(item.lstrip("/"))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(tree)
    return files


class Outline(HTMLParser):
    """Ids, references, headings and reveal marks of a rendered page."""

    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                      "meta", "source", "track", "wbr"})

    def __init__(self, document: str):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.references: list[tuple[str, str]] = []
        self.headings: list[int] = []
        self.reveals: list[tuple[str, dict]] = []
        self.nested_reveals = 0
        self.open: list[tuple[str, bool]] = []
        self.feed(document)
        self.close()

    def element(self, tag: str, attrs: list[tuple[str, str | None]], void: bool) -> None:
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"] or "")
        for name in ("aria-labelledby", "aria-controls", "aria-describedby"):
            for target in (attributes.get(name) or "").split():
                self.references.append((name, target))
        if (attributes.get("href") or "").startswith("#"):
            self.references.append(("href", attributes["href"][1:]))
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        reveal = "data-reveal" in attributes
        if reveal:
            self.reveals.append((tag, attributes))
            if any(marked for _tag, marked in self.open):
                self.nested_reveals += 1
        if not void:
            self.open.append((tag, reveal))

    def handle_starttag(self, tag, attrs):
        self.element(tag, attrs, tag in self.VOID)

    def handle_startendtag(self, tag, attrs):
        self.element(tag, attrs, True)

    def handle_endtag(self, tag):
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                del self.open[index:]
                break


def html_problems(document: str, files: set[str] | None = None) -> list[tuple[int, str]]:
    exists = (lambda _path: True) if files is None else files.__contains__
    return build.check_html(document, exists)


# --- the contract of every fragment on its own -------------------------------------

def bare_media(kind: str = "image", sized: bool = True, duration: str = "0:20") -> Strict:
    video = kind == "video"
    return node(
        MEDIA_FIELDS, id="m", type=kind, isVideo=video, src="/assets/m/a.mp4" if video
        else "/assets/m/a.png", posterSrc="/assets/m/p.png" if video else "",
        thumbSrc="/assets/m/p.png" if video else "/assets/m/a.png",
        width=640 if sized else "", height=480 if sized else "",
        ratio="640 / 480" if sized else "", durationText=duration if video else "",
        label="Видео: кадр, 0:20" if video else "", alt="Кадр",
    )


def bare_location(full: bool, ready: bool = True, compact: bool = False,
                  nested: bool = False) -> Strict:
    links = {"google": "https://www.google.com/maps/search/?api=1&query=1,2",
             "yandex": "https://yandex.ru/maps/?pt=2,1&z=16",
             "apple": "https://maps.apple.com/?ll=1,2"} if full else dict.fromkeys(MAP_LINK_FIELDS, "")
    return node(
        LOCATION_FIELDS, id="l", domId="w--l-l", ready=ready, name="Место" if ready else "",
        address="Энск" if full else "", description="Описание" if full else "",
        mapLinks=node(MAP_LINK_FIELDS, **links), hasMapLinks=full,
        photos=[bare_media("image"), bare_media("video", sized=False, duration="")] if full else [],
        directions=bare_media("image") if full else None, compact=compact, nested=nested,
    )


def bare_event(event_id: str, full: bool, primary: bool = False, with_dom_id: bool = True) -> Strict:
    values = dict(
        id=event_id, title="Событие", description="Описание" if full else "",
        note="Приписка" if full else "", isMain=primary, isPrimary=primary,
        kind="primary" if primary else "regular", startISO="2030-06-15T16:00:00+03:00",
        endISO="2030-06-15T22:00:00+03:00", dateText="15 июня 2030", timeText="16:00",
        whenText="Суббота, 15 июня 2030, 16:00", tabText="16:00",
        icsPath=f"/assets/m/{event_id}.ics",
        location=bare_location(full, ready=full, nested=True),
        program=node(PROGRAM_FIELDS, domId=f"w--e-{event_id}--program", nested=True, items=[
            node(SCHEDULE_ITEM_FIELDS, time="16:00", title="Пункт", text="Текст"),
            node(SCHEDULE_ITEM_FIELDS, time="", title="Пункт", text="")]) if full else None,
    )
    values["domId"] = f"w--e-{event_id}" if with_dom_id else ""
    return node(EVENT_FIELDS if with_dom_id else PRIMARY_EVENT_FIELDS, **values)


def bare_widget(kind: str, **values) -> Strict:
    return node(WIDGET_COMMON_FIELDS + WIDGET_FIELDS[kind], type=kind, domId="w",
                labelledBy="s--title", **values)


def widget_variants() -> list[Strict]:
    """Every widget type with every enumeration value, all flags on and all off."""
    widgets = [bare_widget("text", text="Абзац\n\nещё абзац", variant=variant)
               for variant in ENUM_FIELDS["variant"]]
    for flag in (True, False):
        widgets.append(bare_widget("date", event=bare_event("e", flag, True), countdown=flag,
                                   calendar=flag, showEventTitle=flag))
    for full in (True, False):
        one = [bare_event("a", full, primary=True)]
        many = one + [bare_event("b", full), bare_event("c", full)]
        for items, primary in ((one, "w--e-a"), (many, "w--e-a"), (many, "")):
            widgets.append(bare_widget("events", items=items, count=len(items),
                                       multiple=len(items) > 1, primaryDomId=primary))
        for ready in (True, False):
            for compact in (True, False):
                widgets.append(bare_widget("location", location=bare_location(
                    full, ready=ready, compact=compact, nested=False)))
        widgets.append(bare_widget("schedule", nested=False, items=[
            node(SCHEDULE_ITEM_FIELDS, time="10:00" if full else "", title="Пункт",
                 text="Текст" if full else "")]))
    widgets.append(bare_widget("schedule", items=[], nested=False))
    for layout in ENUM_FIELDS["layout"]:
        items = [bare_media("video")] if layout == "single" else [
            bare_media("image"), bare_media("image", sized=False), bare_media("video"),
            bare_media("video", sized=False, duration="")]
        widgets.append(bare_widget("media", items=items, layout=layout))
    return widgets


def section_variants() -> list[Strict]:
    sections = [
        node(SECTION_FIELDS["cover"], id="cover", type="cover", domId="s-cover",
             titleId="s-cover--title", eyebrow=eyebrow, event=bare_event("e", True, True),
             background=None, backgroundFocus="center")
        for eyebrow in ("Приглашение", "")
    ]
    sections += [
        node(SECTION_FIELDS["cover"], id="cover", type="cover", domId="s-cover",
             titleId="s-cover--title", eyebrow="Приглашение", event=bare_event("e", True, True),
             background=bare_media("image", sized=sized), backgroundFocus=focus)
        for focus in ENUM_FIELDS["backgroundFocus"]
        for sized in (True, False)
    ]
    widget = bare_widget("text", text="Текст", variant="body")
    for align in ENUM_FIELDS["align"]:
        for width in ENUM_FIELDS["width"]:
            for hidden, note, widgets in ((False, "Приписка", [widget]), (True, "", [widget]),
                                          (False, "Только приписка", [])):
                sections.append(node(
                    SECTION_FIELDS["custom"], id="s", type="custom", domId="s",
                    titleId="s--title", title="Заголовок", titleHidden=hidden, align=align,
                    width=width, note=note, widgets=widgets))
    return sections


def harness_root(items: list) -> Strict:
    """The root of the page plus `harness`: the list the stub frame goes over."""
    return node(
        ROOT_FIELDS + ("harness",), greeting="Дорогая Ева!", form="ty", ty=True, vy=False,
        coupleNames=COUPLE_NAMES, rsvpDeadline=RSVP_DEADLINE, mediaPath=MEDIA_PATH,
        faviconPath="/assets/favicon.svg", faviconType="image/svg+xml",
        ogImage="/assets/og.png", ogImageType="image/png", ogImageWidth=1200,
        ogImageHeight=630, primaryEvent=bare_event("e", True, True, False), sections=[],
        harness=items,
    )


STUB_PAGE = (
    '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
    "<title>Фрагмент</title>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
)
STUB_FRAMES = {
    "sections": "<!-- each:harness --><!-- include:sections/{{.type}} --><!-- endeach -->",
    "widgets": "<!-- each:harness --><!-- include:widgets/{{.type}} --><!-- endeach -->",
    "media": '<ul class="gallery" role="list">'
             "<!-- each:harness --><!-- include:media/{{.type}} --><!-- endeach --></ul>",
}


class FragmentFilesTests(unittest.TestCase):
    def test_the_files_are_the_registered_types(self):
        fragments = load_fragments()
        self.assertEqual(fragments.names("sections"), sorted(SECTION_TYPES))
        self.assertEqual(fragments.names("widgets"), sorted(WIDGET_TYPES))
        self.assertEqual(fragments.names("media"), sorted(MEDIA_TYPES))

    def test_every_fragment_starts_with_its_contract(self):
        for path in sorted(FRAGMENTS_DIR.rglob("*.html")):
            with self.subTest(fragment=path.relative_to(FRAGMENTS_DIR).as_posix()):
                source = path.read_text(encoding="utf-8")
                first = source[: source.index("-->")]
                self.assertTrue(source.startswith("<!-- "), "no contract comment")
                self.assertIn("ожидает в ", first)

    def test_class_modifiers_come_only_from_checked_enumerations(self):
        for path in sorted(FRAGMENTS_DIR.rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            for classes in re.findall(r'class="([^"]*)"', source):
                for field in re.findall(r"\{\{\s*([^}]*?)\s*\}\}", classes):
                    with self.subTest(fragment=path.name, field=field):
                        self.assertIn(field.lstrip("."), ENUM_FIELDS)
                        self.assertTrue(field.startswith("."), field)

    def test_the_reveal_mark_is_only_on_the_container_of_a_section(self):
        found = {
            path.relative_to(FRAGMENTS_DIR).as_posix(): path.read_text(encoding="utf-8")
            .count("data-reveal")
            for path in FRAGMENTS_DIR.rglob("*.html")
        }
        self.assertEqual({name: count for name, count in found.items() if count},
                         {"sections/custom.html": 1})
        custom = (FRAGMENTS_DIR / "sections" / "custom.html").read_text(encoding="utf-8")
        self.assertRegex(custom, r'<div class="container container--\{\{\.width\}\}" data-reveal>')


class FragmentContractTests(unittest.TestCase):
    """Every fragment renders on the fields of the contract and nothing else."""

    fragments: build.Fragments

    @classmethod
    def setUpClass(cls):
        cls.fragments = load_fragments()

    def render(self, frame: str, items: list) -> str:
        page = STUB_PAGE.format(body=STUB_FRAMES[frame])
        template = build.parse_template(page, "stub", self.fragments)
        return build.strip_html_comments(template.render(harness_root(items)), "stub")

    def assertRendersCleanly(self, frame: str, item: Strict) -> str:
        document = self.render(frame, [item])
        self.assertEqual(html_problems(document), [])
        return document

    def test_sections(self):
        seen = set()
        for section in section_variants():
            seen.add(section["type"])
            with self.subTest(section=section["type"], align=section.get("align"),
                              width=section.get("width")):
                self.assertRendersCleanly("sections", section)
        self.assertEqual(seen, set(SECTION_TYPES))

    def test_widgets(self):
        seen = set()
        for widget in widget_variants():
            seen.add(widget["type"])
            with self.subTest(widget=widget["type"]):
                self.assertRendersCleanly("widgets", widget)
        self.assertEqual(seen, set(WIDGET_TYPES))

    def test_media(self):
        for item in (bare_media("image"), bare_media("image", sized=False), bare_media("video"),
                     bare_media("video", sized=False, duration="")):
            with self.subTest(media=item["type"], sized=bool(item["width"])):
                document = self.assertRendersCleanly("media", item)
                self.assertEqual(document.count('<li class="gallery__item">'), 1)
                self.assertIn("data-lightbox", document)
                self.assertEqual("width=" in document.split("<img", 1)[1].split(">", 1)[0],
                                 bool(item["width"]))
                # the sizer of a single tile: the size of the frame, or the ratio token
                if item["width"]:
                    self.assertIn('<li class="gallery__item"><svg class="gallery__sizer" '
                                  'width="640" height="480" viewBox="0 0 640 480"', document)
                else:
                    self.assertIn('<li class="gallery__item"><span class="gallery__sizer '
                                  'gallery__sizer--default" aria-hidden="true"></span>', document)
                    self.assertNotIn("data-media-ratio", document)

    def test_a_field_outside_the_contract_fails(self):
        widget = bare_widget("text", text="Текст", variant="body")
        del widget["variant"]
        with self.assertRaises(AssertionError):
            self.render("widgets", [widget])

    def test_the_modifiers_are_the_enumerations(self):
        document = self.render("widgets", widget_variants())
        for variant in ENUM_FIELDS["variant"]:
            self.assertIn(f"text-widget--{variant}", document)
        for layout in ENUM_FIELDS["layout"]:
            self.assertIn(f"gallery--{layout}", document)
        for kind in ENUM_FIELDS["kind"]:
            self.assertIn(f"events__panel--{kind}", document)
        document = self.render("sections", section_variants())
        for align in ENUM_FIELDS["align"]:
            self.assertIn(f"section--{align}", document)
        for width in ENUM_FIELDS["width"]:
            self.assertIn(f"container--{width}", document)
        for focus in ENUM_FIELDS["backgroundFocus"]:
            self.assertIn(f"cover__photo--{focus}", document)

    def test_the_events_widget_works_without_a_script(self):
        many = [bare_event("a", True, primary=True), bare_event("b", False)]
        document = self.render("widgets", [bare_widget(
            "events", items=many, count=2, multiple=True, primaryDomId="w--e-a")])
        tablist = re.search(r"<div [^>]*data-events-tablist[^>]*>", document).group(0)
        self.assertIn(" hidden", tablist)
        self.assertIn('aria-labelledby="s--title"', tablist)
        self.assertIn('data-events-primary="w--e-a"', document)
        panels = re.findall(r"<article [^>]*>", document)
        self.assertEqual(len(panels), 2)
        for panel in panels:
            self.assertNotIn("hidden", panel)
            self.assertIn("data-events-start=", panel)
            self.assertIn("data-events-end=", panel)
        self.assertIn("events__panel--primary", panels[0])
        self.assertIn('aria-controls="w--e-a"', document)
        self.assertIn('id="w--e-a--tab"', document)
        # the marks are in the markup, hidden until the script sets them
        for mark in re.findall(r"<span [^>]*data-events-when[^>]*>", document):
            self.assertIn(" hidden", mark)
        self.assertNotIn("data-countdown", document)
        # a single event: no tab list at all
        document = self.render("widgets", [bare_widget(
            "events", items=many[:1], count=1, multiple=False, primaryDomId="w--e-a")])
        self.assertNotIn("data-events-tablist", document)
        self.assertNotIn("role=\"tab\"", document)

    def test_the_card_of_an_event_keeps_its_order(self):
        document = self.render("widgets", [bare_widget(
            "events", items=[bare_event("a", True, primary=True)], count=1, multiple=False,
            primaryDomId="w--e-a")])
        order = ["<h3", "events__when", "text/calendar", "events__description",
                 "events__note", "venue__name", "events__program"]
        positions = [document.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(document, r"<h4 class=\"venue__name\">")
        self.assertRegex(document, r"<h4 class=\"events__program-title\" id=\"w--e-a--program\">")
        self.assertRegex(document, r"<h5 class=\"schedule__name\">")

    def test_one_program_markup_at_two_depths(self):
        items = [node(SCHEDULE_ITEM_FIELDS, time="10:00", title="Пункт", text="")]
        widget = self.render("widgets", [bare_widget("schedule", items=items, nested=False)])
        self.assertIn('<h3 class="schedule__name">Пункт</h3>', widget)
        self.assertNotIn("<h5", widget)
        card = self.render("widgets", [bare_widget(
            "events", items=[bare_event("a", True, primary=True)], count=1, multiple=False,
            primaryDomId="w--e-a")])
        self.assertIn('<section class="events__program" aria-labelledby="w--e-a--program">', card)
        self.assertNotIn('<h3 class="schedule__name">', card)
        # an event without a program: no heading "Программа" at all
        bare = self.render("widgets", [bare_widget(
            "events", items=[bare_event("a", False, primary=True)], count=1, multiple=False,
            primaryDomId="w--e-a")])
        self.assertNotIn("events__program", bare)
        self.assertNotIn("schedule__list", bare)

    def test_the_location_card(self):
        def card(**flags) -> str:
            return self.render("widgets", [bare_widget("location", location=bare_location(
                True, **flags))])

        full = card()
        self.assertIn('<h3 class="venue__name">', full)
        for part in ("venue__address", "venue__description", "venue__maps", "venue__scheme",
                     "data-gallery", "media-tile--video"):
            self.assertIn(part, full)
        compact = card(compact=True)
        self.assertIn("location-card--compact", compact)
        for part in ("venue__address", "venue__maps"):
            self.assertIn(part, compact)
        for part in ("venue__description", "venue__scheme", "data-gallery"):
            self.assertNotIn(part, compact)
        self.assertIn('<h4 class="venue__name">', card(nested=True))
        pending = card(ready=False)
        self.assertIn("Подробности сообщим позже.", pending)
        self.assertNotIn("venue__name", pending)
        self.assertNotIn("venue__maps", pending)

    def test_the_date_widget(self):
        on = self.render("widgets", [bare_widget(
            "date", event=bare_event("e", True, True), countdown=True, calendar=True,
            showEventTitle=True)])
        self.assertIn('data-countdown="2030-06-15T16:00:00+03:00"', on)
        self.assertRegex(on, r'<div class="countdown date__countdown"[^>]* hidden>')
        self.assertIn('href="/assets/m/e.ics"', on)
        self.assertIn("15 июня 2030, 16:00", on)
        self.assertIn("date__event", on)
        off = self.render("widgets", [bare_widget(
            "date", event=bare_event("e", True, True), countdown=False, calendar=False,
            showEventTitle=False)])
        self.assertIn("15 июня 2030, 16:00", off)
        for part in ("data-countdown", "text/calendar", "date__event"):
            self.assertNotIn(part, off)

    def cover(self, background, focus: str = "center") -> str:
        return self.render("sections", [node(
            SECTION_FIELDS["cover"], id="cover", type="cover", domId="s-cover",
            titleId="s-cover--title", eyebrow="", event=bare_event("e", True, True),
            background=background, backgroundFocus=focus)])

    def test_the_cover_without_a_photo_has_no_backdrop(self):
        document = self.cover(None)
        for part in ("cover__backdrop", "cover__photo", "<img"):
            self.assertNotIn(part, document)
        self.assertRegex(document, r'<section class="cover" [^>]*>\s*<div class="container '
                                   r'cover__inner">')

    def test_the_cover_photo_is_a_decorative_backdrop(self):
        document = self.cover(bare_media("image"))
        backdrop = re.search(r'<div class="cover__backdrop"[^>]*>(<img [^>]*>)</div>', document)
        self.assertIsNotNone(backdrop)
        self.assertIn('aria-hidden="true"', backdrop.group(0))
        photo = backdrop.group(1)
        self.assertIn('src="/assets/m/a.png"', photo)
        # decoration: an empty alt, not the label of the media item
        self.assertIn('alt=""', photo)
        self.assertNotIn("Кадр", photo)
        # the first screen: loaded first, never lazily; the cover gives the size
        self.assertIn('fetchpriority="high"', photo)
        for attribute in ("loading=", "width=", "height=", "style="):
            self.assertNotIn(attribute, photo)
        # the backdrop comes before the content of the cover (the styles rely on it)
        self.assertLess(document.index("cover__backdrop"), document.index("cover__inner"))
        self.assertEqual(Outline(document).headings, [1])

    def test_the_focus_of_the_cover_photo_is_a_modifier(self):
        for focus in _schema.COVER_FOCUSES:
            with self.subTest(focus=focus):
                document = self.cover(bare_media("image"), focus)
                self.assertIn(f'class="cover__photo cover__photo--{focus}"', document)

    def test_the_cover_photo_is_on_the_full_pages(self):
        tree = page_tree(GUESTS[0])
        page = render_page(tree)
        cover = tree["sections"][0]
        self.assertIn(f'<img class="cover__photo cover__photo--top" src="{cover["background"]["src"]}" '
                      'alt="" fetchpriority="high">', page)
        self.assertIn(cover["background"]["src"].lstrip("/"), published_files(tree))

    def test_the_video_tile(self):
        document = self.render("media", [bare_media("video")])
        tile = re.search(r"<a [^>]*>", document).group(0)
        for part in ('href="/assets/m/a.mp4"', 'type="video/mp4"', "data-lightbox",
                     'data-media="video"', 'data-media-poster="/assets/m/p.png"',
                     'data-media-ratio="640 / 480"', 'aria-label="Видео: кадр, 0:20"'):
            self.assertIn(part, tile)
        self.assertIn('<svg class="media-tile__icon"', document)
        self.assertIn('media-tile__duration" aria-hidden="true">0:20<', document)
        unsized = self.render("media", [bare_media("video", sized=False, duration="")])
        self.assertNotIn("data-media-ratio", unsized)
        self.assertNotIn("media-tile__duration", unsized)


class FullPageTests(unittest.TestCase):
    """The pages of eight guests, rendered from their page trees."""

    pages: dict[str, tuple[Strict, str]]

    @classmethod
    def setUpClass(cls):
        fragments = load_fragments()
        cls.pages = {}
        for guest in GUESTS:
            tree = page_tree(guest)
            cls.pages[guest["token"]] = (tree, render_page(tree, fragments))

    def test_the_fixture_covers_the_matrix(self):
        combinations = set()
        for guest in GUESTS:
            sections = guest.get("sections", {})
            combinations.add((guest["form"], sections.get("travel", {}).get("visible", False),
                              sections.get("plus-one", {}).get("visible", False)))
        self.assertEqual(len(combinations), 8)

    def test_the_pages_pass_the_checks_of_the_build(self):
        for token, (tree, page) in self.pages.items():
            with self.subTest(token=token):
                build.check_rendered(page)
                self.assertEqual(html_problems(page, published_files(tree)), [])

    def test_the_pages_have_no_inline_styles(self):
        for token, (_tree, page) in self.pages.items():
            with self.subTest(token=token):
                self.assertNotRegex(page, r"<[a-zA-Z][^>]*\sstyle\s*=")
                self.assertNotRegex(page, r"(?i)<style[\s>]")

    def test_ids_are_unique_and_every_reference_resolves(self):
        for token, (_tree, page) in self.pages.items():
            outline = Outline(page)
            with self.subTest(token=token):
                repeated = [name for name, count in collections.Counter(outline.ids).items()
                            if count > 1]
                self.assertEqual(repeated, [])
                self.assertTrue(all(outline.ids))
                dangling = [(attribute, target) for attribute, target in outline.references
                            if target not in outline.ids]
                self.assertEqual(dangling, [])
                self.assertTrue(outline.references)

    def test_the_heading_outline_has_no_gaps(self):
        for token, (_tree, page) in self.pages.items():
            levels = Outline(page).headings
            with self.subTest(token=token, levels=levels):
                self.assertEqual(levels.count(1), 1)
                self.assertEqual(levels[0], 1)
                for previous, level in zip(levels, levels[1:]):
                    self.assertLessEqual(level, previous + 1)
                self.assertIn(5, levels)  # the program of the dinner

    def test_one_reveal_mark_per_section_and_never_on_parts(self):
        for token, (tree, page) in self.pages.items():
            outline = Outline(page)
            with self.subTest(token=token):
                custom = [section for section in tree["sections"] if section["type"] == "custom"]
                self.assertEqual(len(outline.reveals), len(custom))
                self.assertEqual(outline.nested_reveals, 0)
                for tag, attributes in outline.reveals:
                    self.assertEqual(tag, "div")
                    self.assertIn("container", attributes.get("class", "").split())
                    for hook in NO_REVEAL_HOOKS:
                        self.assertNotIn(hook, attributes)
                self.assertNotRegex(page, r"<li\b[^>]*data-reveal")
                self.assertNotRegex(page, r"<section class=\"cover\"[^>]*data-reveal")

    def test_everything_is_visible_without_a_script(self):
        for token, (_tree, page) in self.pages.items():
            with self.subTest(token=token):
                # only the tab list, the timer and the marks wait for the script
                hidden = re.findall(r"<\w+\b[^>]*\shidden[\s>]", page)
                self.assertTrue(hidden)
                for element in hidden:
                    self.assertRegex(element, r"data-events-tablist|data-countdown|data-events-when")

    def test_the_guest_sees_his_events_and_the_primary_one_is_marked(self):
        for guest in GUESTS:
            tree, page = self.pages[guest["token"]]
            builder = PageTree(guest)
            with self.subTest(token=guest["token"]):
                panels = re.findall(r'<article class="card event events__panel '
                                    r'events__panel--(\w+)" id="([\w-]+)"', page)
                self.assertEqual([panel_id for _kind, panel_id in panels],
                                 [f"s-where--w1--e-{event_id}" for event_id in builder.visible])
                self.assertEqual([kind for kind, panel_id in panels if kind == "primary"],
                                 ["primary"])
                self.assertIn(f'id="s-where--w1--e-{builder.primary}"', page)
                self.assertIn(f'data-events-primary="s-where--w1--e-{builder.primary}"', page)
                self.assertEqual("data-events-tablist" in page, len(builder.visible) > 1)
                cover_date = EVENTS[builder.primary]["dateText"]
                self.assertRegex(page, rf'<p class="cover__date"><time [^>]*>{cover_date}</time>')

    def test_values_are_escaped(self):
        guest = dict(GUESTS[1], greeting="Ева <b>& Боб</b> {{coupleNames}}")
        guest["sections"] = {"personal": {"note": "<script>alert(1)</script> & {{x}}"}}
        page = render_page(page_tree(guest))
        self.assertNotIn("<b>", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("Ева &lt;b&gt;&amp; Боб&lt;/b&gt; &#123;&#123;coupleNames&#125;&#125;", page)
        # the data layer turns `{{x}}` in a note into the literal `{x}`
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#123;x&#125;", page)
        build.check_rendered(page)
        self.assertEqual(html_problems(page), [])


# --- the trees of the data layer --------------------------------------------------

def strict(value):
    """A tree of the data layer with every node a `Strict` dict."""
    if isinstance(value, dict):
        return Strict({key: strict(item) for key, item in value.items()})
    if isinstance(value, list):
        return [strict(item) for item in value]
    return value


def without_sizes(site: dict) -> dict:
    """The media without `width` / `height`: the tiles take the size of no file."""
    for item in site.get("media", {}).values():
        item.pop("width", None)
        item.pop("height", None)
    return site


#: The fields of every node, by the place in the tree where the node is found.
NODE_FIELDS = {
    "root": set(ROOT_FIELDS),
    "section": set(SECTION_FIELDS["cover"]) | set(SECTION_FIELDS["custom"]),
    "event": set(EVENT_FIELDS),
    "location": set(LOCATION_FIELDS),
    "mapLinks": set(MAP_LINK_FIELDS),
    "media": set(MEDIA_FIELDS),
    "program": set(PROGRAM_FIELDS),
    "item": set(SCHEDULE_ITEM_FIELDS),
}


def nodes_of(tree: dict):
    """(kind, node) of every node of a real tree, by where it is found."""

    def event(value):
        yield "event", value
        yield from place(value["location"])
        if value["program"] is not None:
            yield "program", value["program"]
            for item in value["program"]["items"]:
                yield "item", item

    def place(value):
        yield "location", value
        yield "mapLinks", value["mapLinks"]
        for item in value["photos"] + ([value["directions"]] if value["directions"] else []):
            yield "media", item

    yield "root", tree
    yield from event(tree["primaryEvent"])
    for section in tree["sections"]:
        yield "section", section
        if section["event"] is not None:
            yield from event(section["event"])
        if section["background"] is not None:
            yield "media", section["background"]
        for widget in section["widgets"]:
            yield f"widget:{widget['type']}", widget
            if widget["type"] == "date":
                yield from event(widget["event"])
            elif widget["type"] == "events":
                for item in widget["items"]:
                    yield from event(item)
            elif widget["type"] == "location":
                yield from place(widget["location"])
            elif widget["type"] == "schedule":
                for item in widget["items"]:
                    yield "item", item
            elif widget["type"] == "media":
                for item in widget["items"]:
                    yield "media", item


class DataLayerPageTests(unittest.TestCase):
    """The pages rendered from the trees of `tools._page.build_pages`.

    Every node is `Strict`: a field that a fragment reads and the data layer
    does not build fails the rendering, even inside an `if:`.
    """

    pages: list[tuple[str, dict, str]]

    @classmethod
    def setUpClass(cls):
        fragments = load_fragments()
        sets = {
            "main": (F.site(), F.invitations(), F.settings()),
            "pending": (F.pending_site(), F.pending_invitations(), F.settings()),
            "no sizes": (without_sizes(F.site()), F.invitations(), F.settings(media_info={})),
        }
        cls.pages = []
        for name, (site, invitations, settings) in sets.items():
            report = F.Collector()
            assert _schema.check_data(site, invitations, report).ok, report.errors
            trees, _usage = _page.build_pages(site, invitations, settings, report)
            assert not report.errors, report.errors
            for invitation, tree in zip(invitations, trees):
                page = render_page(strict(tree), fragments)
                cls.pages.append((f"{name}: {invitation['token'][:4]}", tree, page))

    def test_every_page_renders_and_passes_the_checks_of_the_build(self):
        self.assertEqual(len(self.pages), 8 + 3 + 8)
        for label, tree, page in self.pages:
            with self.subTest(page=label):
                build.check_rendered(page)
                self.assertEqual(html_problems(page, published_files(tree)), [])

    def test_the_nodes_have_exactly_the_fields_of_the_contract(self):
        widget_fields = {
            f"widget:{kind}": set(WIDGET_COMMON_FIELDS) | set(fields)
            for kind, fields in WIDGET_FIELDS.items()
        }
        seen = set()
        for label, tree, _page_text in self.pages:
            for kind, value in nodes_of(tree):
                seen.add(kind)
                with self.subTest(page=label, node=kind):
                    self.assertEqual(set(value), {**NODE_FIELDS, **widget_fields}[kind])
        # the fixture has no schedule widget of its own: the programme is in the card
        self.assertEqual(seen, set(NODE_FIELDS) | set(widget_fields) - {"widget:schedule"})

    def test_a_field_outside_the_tree_fails_the_rendering(self):
        tree = strict(_page.build_pages(F.site(), F.invitations(), F.settings())[0][0])
        del tree["sections"][1]["widgets"][0]["variant"]
        with self.assertRaises(AssertionError):
            render_page(tree)

    def test_ids_are_unique_and_every_reference_resolves(self):
        for label, _tree, page in self.pages:
            outline = Outline(page)
            with self.subTest(page=label):
                repeated = [name for name, count in collections.Counter(outline.ids).items()
                            if count > 1]
                self.assertEqual(repeated, [])
                self.assertTrue(all(outline.ids))
                dangling = [(attribute, target) for attribute, target in outline.references
                            if target not in outline.ids]
                self.assertEqual(dangling, [])

    def test_the_heading_outline_has_no_gaps(self):
        for label, _tree, page in self.pages:
            levels = Outline(page).headings
            with self.subTest(page=label, levels=levels):
                self.assertEqual(levels.count(1), 1)
                self.assertEqual(levels[0], 1)
                for previous, level in zip(levels, levels[1:]):
                    self.assertLessEqual(level, previous + 1)

    def test_one_reveal_mark_per_section(self):
        for label, tree, page in self.pages:
            outline = Outline(page)
            with self.subTest(page=label):
                custom = [section for section in tree["sections"] if section["type"] == "custom"]
                self.assertEqual(len(outline.reveals), len(custom))
                self.assertEqual(outline.nested_reveals, 0)
                for tag, attributes in outline.reveals:
                    self.assertEqual(tag, "div")
                    for hook in NO_REVEAL_HOOKS:
                        self.assertNotIn(hook, attributes)

    def test_the_tiles_without_a_size(self):
        pages = [page for label, _tree, page in self.pages if label.startswith("no sizes")]
        unsized = [page for page in pages if "gallery__sizer--default" in page]
        self.assertTrue(unsized)
        for page in unsized:
            self.assertNotIn("data-media-ratio", page)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
