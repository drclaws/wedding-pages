"""Tests for the page tree of an invitation (`tools/_page.py`).

The trees are built from the fictional fixture data (`tests/fixtures_v2.py`);
the facts of the media files come from a stub, no file is read.
"""

from __future__ import annotations

import copy
import unittest
from datetime import timedelta
from unittest import mock

from tests import fixtures_v2 as F
from tests import support
from tools import _page, _schema
from tools._media import MediaInfo

ROOT_FIELDS = {
    "greeting", "form", "ty", "vy", "coupleNames", "rsvpDeadline", "mediaPath",
    "faviconPath", "faviconType", "ogImage", "ogImageType", "ogImageWidth",
    "ogImageHeight", "themeColor", "siteName", "linkDescription", "primaryEvent", "sections",
}  # fmt: skip
SECTION_FIELDS = {
    "id", "type", "domId", "titleId", "title", "titleHidden", "align", "width",
    "note", "eyebrow", "event", "background", "backgroundFocus", "widgets",
}  # fmt: skip
WIDGET_FIELDS = {
    "text": {"text", "variant"},
    "date": {"event", "countdown", "calendar", "showEventTitle"},
    "events": {"items", "count", "multiple", "primaryDomId"},
    "location": {"location"},
    "schedule": {"items", "nested"},
    "media": {"items", "layout"},
}
EVENT_FIELDS = {
    "id", "domId", "title", "description", "note", "isMain", "isPrimary", "kind",
    "startISO", "endISO", "dateText", "timeText", "whenText", "tabText", "icsPath",
    "location", "program",
}  # fmt: skip
PROGRAM_FIELDS = {"domId", "items", "nested"}
LOCATION_FIELDS = {
    "id", "domId", "ready", "name", "address", "description", "mapLinks",
    "hasMapLinks", "photos", "directions", "compact", "nested",
}  # fmt: skip
MEDIA_FIELDS = {
    "id", "type", "isVideo", "src", "posterSrc", "thumbSrc", "width", "height",
    "ratio", "durationText", "label", "alt",
}  # fmt: skip
ITEM_FIELDS = {"time", "title", "text"}


def build(mutate=None, site=None, invitations=None, **settings):
    site = F.site() if site is None else site
    invitations = F.invitations() if invitations is None else invitations
    if mutate is not None:
        mutate(site, invitations)
    report = F.Collector()
    index = _schema.check_data(site, invitations, report)
    assert index.ok and not report.errors, report.errors
    pages, _usage = _page.build_pages(site, invitations, F.settings(**settings))
    return pages


def section(page, section_id):
    return next((item for item in page["sections"] if item["id"] == section_id), None)


def widgets(page, kind):
    return [widget for item in page["sections"] for widget in item["widgets"] if widget["type"] == kind]


def events_widget(page):
    return widgets(page, "events")[0]


def walk_events(page):
    """Every event object of a tree."""
    found = [page["primaryEvent"]]
    for item in page["sections"]:
        if item["event"] is not None:
            found.append(item["event"])
        for widget in item["widgets"]:
            if widget["type"] == "date":
                found.append(widget["event"])
            if widget["type"] == "events":
                found.extend(widget["items"])
    return found


def walk_dom_ids(value):
    if isinstance(value, dict):
        if value.get("domId"):
            yield value["domId"]
        for item in value.values():
            yield from walk_dom_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_dom_ids(item)


class ContractTests(unittest.TestCase):
    """Every field of the contract always exists, and nothing else."""

    def trees(self):
        return build() + build(site=F.pending_site(), invitations=F.pending_invitations())

    def check_location(self, place):
        self.assertEqual(set(place), LOCATION_FIELDS)
        self.assertEqual(set(place["mapLinks"]), {"google", "yandex", "apple"})
        for media in place["photos"] + ([place["directions"]] if place["directions"] else []):
            self.assertEqual(set(media), MEDIA_FIELDS)

    def check_event(self, event):
        self.assertEqual(set(event), EVENT_FIELDS)
        self.check_location(event["location"])
        self.assertTrue(event["location"]["nested"])
        if event["program"] is not None:
            self.assertEqual(set(event["program"]), PROGRAM_FIELDS)
            self.assertTrue(event["program"]["nested"])
            for item in event["program"]["items"]:
                self.assertEqual(set(item), ITEM_FIELDS)

    def test_root_and_sections(self):
        for tree in self.trees():
            self.assertEqual(set(tree), ROOT_FIELDS)
            for item in tree["sections"]:
                self.assertEqual(set(item), SECTION_FIELDS)
                self.assertIn(item["type"], _schema.SECTION_TYPES)
                self.assertIn(item["align"], ("start", "center"))
                self.assertIn(item["width"], ("narrow", "wide"))

    def test_widgets(self):
        for tree in self.trees():
            for item in tree["sections"]:
                for widget in item["widgets"]:
                    base = {"type", "domId", "labelledBy"}
                    self.assertEqual(set(widget), base | WIDGET_FIELDS[widget["type"]])
                    self.assertEqual(widget["labelledBy"], item["titleId"])

    def test_events_locations_and_media(self):
        for tree in self.trees():
            for event in walk_events(tree):
                self.check_event(event)
            for widget in widgets(tree, "location"):
                self.check_location(widget["location"])
                self.assertFalse(widget["location"]["nested"])
            for widget in widgets(tree, "media"):
                for media in widget["items"]:
                    self.assertEqual(set(media), MEDIA_FIELDS)
            for widget in widgets(tree, "schedule"):
                for item in widget["items"]:
                    self.assertEqual(set(item), ITEM_FIELDS)

    def test_root_values(self):
        page = build()[0]
        self.assertEqual(page["mediaPath"], f"/assets/{F.MEDIA_DIR}")
        for key, value in F.SITE_IMAGES.items():
            self.assertEqual(page[key], value)
        self.assertEqual((page["form"], page["ty"], page["vy"]), ("ty", True, False))
        self.assertEqual(page["coupleNames"], "Алиса и Боб")
        self.assertEqual(page["rsvpDeadline"], "1 мая 2030")
        self.assertEqual(page["primaryEvent"]["domId"], "")

    def test_rsvp_deadline_is_an_empty_string_when_not_set(self):
        def change(site, invitations):
            site.pop("rsvpDeadline")
            site["sections"].pop(7)

        self.assertEqual(build(change)[0]["rsvpDeadline"], "")


class PrimaryEventTests(unittest.TestCase):
    def test_main_event_is_primary_by_default(self):
        page = build()[0]
        self.assertEqual(page["primaryEvent"]["id"], "dinner")
        self.assertTrue(page["primaryEvent"]["isMain"])
        self.assertEqual(page["primaryEvent"]["kind"], "primary")

    def test_primary_event_of_an_invitation(self):
        page = build()[2]
        self.assertEqual(page["primaryEvent"]["id"], "ceremony")
        self.assertFalse(page["primaryEvent"]["isMain"])
        self.assertEqual(section(page, "cover")["event"]["id"], "ceremony")
        self.assertEqual(widgets(page, "date")[0]["event"]["id"], "ceremony")
        kinds = {item["id"]: item["kind"] for item in events_widget(page)["items"]}
        self.assertEqual(kinds, {"ceremony": "primary", "dinner": "regular", "brunch": "regular"})
        self.assertEqual(events_widget(page)["primaryDomId"], "s-where--w1--e-ceremony")

    def test_texts_use_the_primary_event(self):
        pages = build()
        self.assertEqual(section(pages[2], "invite")["widgets"][-1]["text"].splitlines()[0], "Ждём тебя 15 июня 2030 в 11:00.")
        self.assertEqual(section(pages[0], "invite")["widgets"][-1]["text"].splitlines()[0], "Ждём тебя 15 июня 2030 в 16:00.")

    def test_other_placeholders(self):
        def change(site, invitations):
            site["sections"][1]["widgets"][0]["text"] = "{eventTitle} / {eventPlace} / {greeting}"

        pages = build(change)
        self.assertEqual(
            section(pages[2], "invite")["widgets"][0]["text"],
            "Регистрация / Дворец бракосочетания Энска / Дорогая Ева!",
        )

    def test_primary_card_is_not_among_the_cards(self):
        pages = build(lambda s, i: s["sections"][4]["widgets"][0].update(events=["dinner"]))
        self.assertEqual(events_widget(pages[2])["primaryDomId"], "")
        self.assertEqual(events_widget(pages[0])["primaryDomId"], "s-where--w1--e-dinner")


class HiddenEventTests(unittest.TestCase):
    def test_hidden_event_is_left_out_everywhere(self):
        page = build()[6]  # the ceremony is hidden
        ids = {event["id"] for event in walk_events(page)}
        self.assertEqual(ids, {"dinner"})
        self.assertFalse(events_widget(page)["multiple"])
        self.assertEqual(events_widget(page)["count"], 1)
        self.assertFalse(widgets(page, "date")[0]["showEventTitle"])

    def test_event_hidden_by_default_is_shown_when_switched_on(self):
        pages = build()
        self.assertIn("brunch", [item["id"] for item in events_widget(pages[0])["items"]])
        self.assertNotIn("brunch", [item["id"] for item in events_widget(pages[1])["items"]])

    def test_other_events_do_not_shorten_an_event(self):
        # the end is the same for everybody: no event, seen or hidden, cuts it
        def change(site, invitations):
            site["events"]["ceremony"].pop("end")
            site["events"]["secret"] = {
                "title": "Тайное", "location": "registry",
                "start": "2030-06-15T13:00:00+03:00",
                "end": "2030-06-15T14:00:00+03:00", "visible": False,
            }  # fmt: skip
            invitations[0]["events"]["secret"] = {"visible": True}

        pages = build(change)
        ceremony = {page["greeting"]: next(e for e in events_widget(page)["items"] if e["id"] == "ceremony") for page in pages[:2]}
        self.assertEqual(ceremony["Дорогая Кэрол!"]["endISO"], "2030-06-15T17:00:00+03:00")
        self.assertEqual(ceremony["Дорогой Дэйв!"]["endISO"], "2030-06-15T17:00:00+03:00")

    def test_default_duration(self):
        pages = build(lambda s, i: s["events"]["brunch"].pop("end"), default_duration=timedelta(hours=2))
        brunch = next(e for e in events_widget(pages[0])["items"] if e["id"] == "brunch")
        self.assertEqual(brunch["endISO"], "2030-06-16T14:00:00+03:00")
        self.assertEqual(brunch["whenText"], "Воскресенье, 16 июня 2030, 12:00")

    def test_date_widget_of_a_hidden_event_is_left_out(self):
        pages = build(lambda s, i: s["sections"][3]["widgets"].append({"type": "date", "event": "brunch"}))
        self.assertEqual(len(widgets(pages[0], "date")), 2)
        self.assertEqual(len(widgets(pages[1], "date")), 1)

    def test_programme_of_hidden_events_is_left_out(self):
        pages = build(lambda s, i: s["sections"][4]["widgets"].append({"type": "schedule", "schedule": "dinner"}))
        self.assertEqual(len(widgets(pages[0], "schedule")), 1)

        def change(site, invitations):
            site["events"]["ceremony"]["schedule"] = "dinner"
            site["events"]["dinner"].pop("schedule")
            site["sections"][4]["widgets"].append({"type": "schedule", "schedule": "dinner"})

        pages = build(change)
        self.assertEqual(len(widgets(pages[0], "schedule")), 1)
        self.assertEqual(widgets(pages[6], "schedule"), [])


class OverrideTests(unittest.TestCase):
    def test_sections_switched_on_and_off(self):
        pages = build()
        self.assertIsNone(section(pages[1], "travel"))
        self.assertIsNone(section(pages[1], "personal"))  # nothing to show
        self.assertIsNotNone(section(pages[2], "travel"))

    def test_widget_switched_on(self):
        pages = build()
        invite_1 = [w["domId"] for w in section(pages[0], "invite")["widgets"]]
        invite_2 = [w["domId"] for w in section(pages[1], "invite")["widgets"]]
        self.assertEqual(invite_1, ["s-invite--w1", "s-invite--w2", "s-invite--w3"])
        self.assertEqual(invite_2, ["s-invite--w1", "s-invite--w3"])
        self.assertIn("со спутником", section(pages[0], "invite")["widgets"][1]["text"])
        travel_3 = [w["domId"] for w in section(pages[2], "travel")["widgets"]]
        travel_6 = [w["domId"] for w in section(pages[5], "travel")["widgets"]]
        self.assertEqual(travel_3, ["s-travel--w1", "s-travel--w2"])
        self.assertEqual(travel_6, ["s-travel--w1", "s-travel--w2", "s-travel--w3"])
        self.assertIn("Для вас забронирован номер", section(pages[5], "travel")["widgets"][2]["text"])

    def test_widget_switched_off(self):
        def change(site, invitations):
            site["sections"][5]["widgets"][2]["visible"] = True
            invitations[1]["sections"] = {
                "travel": {"visible": True, "widgets": {"hotel-booked": {"visible": False}}}
            }

        pages = build(change)
        self.assertEqual(len(section(pages[1], "travel")["widgets"]), 2)
        self.assertEqual(len(section(pages[2], "travel")["widgets"]), 3)

    def test_notes(self):
        pages = build()
        self.assertTrue(section(pages[0], "personal")["note"].startswith("Спасибо"))
        self.assertTrue(section(pages[7], "where")["note"].startswith("Если понадобится"))
        dinner = next(e for e in events_widget(pages[1])["items"] if e["id"] == "dinner")
        self.assertIn("аллергию", dinner["note"])
        self.assertEqual(section(pages[0], "where")["note"], "")

    def test_notes_get_placeholders(self):
        pages = build(lambda s, i: i[0]["sections"]["personal"].update(note="До {eventDate}, {{пока}}!"))
        self.assertEqual(section(pages[0], "personal")["note"], "До 15 июня 2030, {пока}!")

    def test_empty_text_widget_is_left_out(self):
        def change(site, invitations):
            site["sections"][2]["widgets"].append({"type": "text", "text": "{eventPlace}"})
            site["events"]["dinner"]["location"] = "terrace"  # not announced yet

        pages = build(change)
        self.assertIsNone(section(pages[1], "personal"))
        self.assertEqual(section(pages[2], "personal")["widgets"][0]["text"], "Дворец бракосочетания Энска")


class TextTests(unittest.TestCase):
    def test_forms_of_address(self):
        pages = build()
        self.assertIn("с тобой", section(pages[0], "invite")["widgets"][-1]["text"])
        self.assertIn("с вами", section(pages[4], "invite")["widgets"][-1]["text"])

    def test_title_with_the_greeting(self):
        pages = build()
        self.assertEqual(section(pages[4], "invite")["title"], "Дорогие Пегги и Виктор!")

    def test_placeholders_of_the_site(self):
        page = build()[0]
        rsvp = section(page, "rsvp")["widgets"]
        self.assertIn("до 1 мая 2030", rsvp[0]["text"])
        self.assertEqual(rsvp[1]["text"], "Алиса и Боб")
        self.assertEqual(rsvp[1]["variant"], "signature")
        self.assertEqual(rsvp[0]["variant"], "body")


def parse_dom_id(dom_id):
    """The path of a DOM id: its parts, split at `--`."""
    return dom_id.split("--")


class DomIdTests(unittest.TestCase):
    def test_ids_are_unique_and_follow_the_path(self):
        for page in build():
            ids = list(walk_dom_ids(page["sections"]))
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(_page.repeated_ids(page), [])
            for dom_id in ids:
                parts = parse_dom_id(dom_id)
                self.assertTrue(parts[0].startswith("s-"), dom_id)
                for part in parts[1:]:
                    self.assertRegex(part, r"\A(?:w[0-9]+|e-.+|l-.+|title|program)\Z")

    def test_one_event_in_two_widgets(self):
        page = build()[0]
        self.assertEqual(widgets(page, "date")[0]["event"]["domId"], "s-when--w1--e-dinner")
        dinner = events_widget(page)["items"][1]
        self.assertEqual(dinner["domId"], "s-where--w1--e-dinner")
        self.assertEqual(dinner["location"]["domId"], "s-where--w1--e-dinner--l-manor")
        self.assertEqual(dinner["program"]["domId"], "s-where--w1--e-dinner--program")
        self.assertEqual(section(page, "cover")["event"]["domId"], "s-cover--e-dinner")

    def test_sections_and_widgets(self):
        page = build()[0]
        invite = section(page, "invite")
        self.assertEqual((invite["domId"], invite["titleId"]), ("s-invite", "s-invite--title"))
        self.assertEqual(invite["widgets"][0]["domId"], "s-invite--w1")

    def test_location_widget(self):
        page = build()[2]
        place = widgets(page, "location")[0]
        self.assertEqual(place["domId"], "s-travel--w2")
        self.assertEqual(place["location"]["domId"], "s-travel--w2--l-hotel")
        self.assertTrue(place["location"]["compact"])

    def test_number_is_the_position_in_the_data(self):
        def change(site, invitations):
            site["sections"][1]["widgets"].insert(0, {"type": "text", "text": "x", "visible": False})

        invite = section(build(change)[0], "invite")
        self.assertEqual(
            [widget["domId"] for widget in invite["widgets"]], ["s-invite--w2", "s-invite--w3", "s-invite--w4"]
        )


class DomIdCollisionTests(unittest.TestCase):
    """Ids that end or start like the fixed parts of a DOM id do not collide."""

    def check(self, mutate):
        site, invitations = F.site(), F.invitations()
        mutate(site, invitations)
        report = F.Collector()
        index = _schema.check_data(site, invitations, report)
        self.assertTrue(index.ok, report.errors)
        pages, _usage = _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(report.errors, [])
        for page in pages:
            self.assertEqual(_page.repeated_ids(page), [])
        return pages

    @staticmethod
    def rename_event(site, invitations, old, new):
        site["events"][new] = site["events"].pop(old)
        if site["mainEvent"] == old:
            site["mainEvent"] = new
        for invitation in invitations:
            overrides = invitation.get("events", {})
            if old in overrides:
                overrides[new] = overrides.pop(old)

    def test_section_named_like_a_widget(self):
        def change(site, invitations):
            site["sections"].append({"id": "invite-w1", "title": "Ещё", "widgets": [{"type": "text", "text": "x"}]})

        page = self.check(change)[0]
        self.assertEqual(section(page, "invite-w1")["domId"], "s-invite-w1")
        self.assertEqual(section(page, "invite")["widgets"][0]["domId"], "s-invite--w1")

    def test_event_named_title_on_the_cover(self):
        page = self.check(lambda s, i: self.rename_event(s, i, "dinner", "title"))[0]
        cover = section(page, "cover")
        self.assertNotEqual(cover["event"]["domId"], cover["titleId"])

    def test_place_named_program(self):
        def change(site, invitations):
            site["locations"]["program"] = site["locations"].pop("manor")
            site["events"]["dinner"]["location"] = "program"

        page = self.check(change)[0]
        dinner = events_widget(page)["items"][1]
        self.assertEqual(dinner["location"]["id"], "program")
        self.assertNotEqual(dinner["location"]["domId"], dinner["program"]["domId"])

    def test_event_named_after_another_event_and_its_place(self):
        def change(site, invitations):
            site["events"]["dinner-manor"] = dict(
                site["events"]["ceremony"],
                start="2030-06-15T13:00:00+03:00",
                end="2030-06-15T14:00:00+03:00",
            )

        page = self.check(change)[0]
        ids = [item["domId"] for item in events_widget(page)["items"]]
        self.assertIn("s-where--w1--e-dinner-manor", ids)
        dinner = next(item for item in events_widget(page)["items"] if item["id"] == "dinner")
        self.assertEqual(dinner["location"]["domId"], "s-where--w1--e-dinner--l-manor")

    def test_safeguard_reports_repeated_ids(self):
        site, invitations = F.site(), F.invitations()
        site["sections"].append({"id": "invite-w1", "title": "Ещё", "widgets": [{"type": "text", "text": "x"}]})
        report = F.Collector()
        with mock.patch.object(_page, "DOM_SEPARATOR", "-"):  # the old, ambiguous joining
            _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(len(report.errors), len(invitations))
        self.assertEqual(
            report.errors[0],
            "invitation #1: ids of the markup repeat on the page ('s-invite-w1'); "
            "rename one of the ids in site.json",
        )

    def test_safeguard_shows_only_ids_that_look_like_ids(self):
        site, invitations = F.site(), F.invitations()[:1]
        report = F.Collector()
        repeated = ["s-where--w1", "s-Ива н", "s-" + "a" * 400]
        with mock.patch.object(_page, "repeated_ids", return_value=repeated):
            _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(
            report.errors,
            [
                "invitation #1: ids of the markup repeat on the page ('s-where--w1'); "
                "rename one of the ids in site.json"
            ],
        )
        report = F.Collector()
        with mock.patch.object(_page, "repeated_ids", return_value=repeated[1:]):
            _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(
            report.errors,
            [
                "invitation #1: ids of the markup repeat on the page (not shown); "
                "rename one of the ids in site.json"
            ],
        )


class DateTextTests(unittest.TestCase):
    def test_one_day(self):
        page = build()[1]
        self.assertEqual([item["tabText"] for item in events_widget(page)["items"]], ["11:00", "16:00"])

    def test_different_days(self):
        page = build()[0]
        self.assertEqual(
            [item["tabText"] for item in events_widget(page)["items"]],
            ["15 июня, 11:00", "15 июня, 16:00", "16 июня, 12:00"],
        )

    def test_event_strings(self):
        dinner = build()[0]["primaryEvent"]
        self.assertEqual(dinner["dateText"], "15 июня 2030")
        self.assertEqual(dinner["timeText"], "16:00")
        self.assertEqual(dinner["whenText"], "Суббота, 15 июня 2030, 16:00–23:30")
        self.assertEqual(dinner["startISO"], "2030-06-15T16:00:00+03:00")
        self.assertEqual(dinner["endISO"], "2030-06-15T23:30:00+03:00")
        # without a provider the calendar file is named after the event
        self.assertEqual(dinner["icsPath"], f"/assets/{F.MEDIA_DIR}/dinner.ics")

    def test_the_end_is_on_the_page_only_with_show_end(self):
        pages = build()
        brunch = next(e for e in events_widget(pages[0])["items"] if e["id"] == "brunch")
        # the brunch has an end, but does not ask to show it
        self.assertEqual(brunch["whenText"], "Воскресенье, 16 июня 2030, 12:00")
        self.assertEqual(brunch["endISO"], "2030-06-16T15:00:00+03:00")

        def change(site, invitations):
            site["events"]["dinner"]["showEnd"] = False
            site["events"]["brunch"]["showEnd"] = True
            site["events"]["ceremony"]["showEnd"] = True
            site["events"]["ceremony"].pop("end")

        page = build(change)[0]
        found = {item["id"]: item for item in events_widget(page)["items"]}
        self.assertEqual(found["dinner"]["whenText"], "Суббота, 15 июня 2030, 16:00")
        self.assertEqual(found["dinner"]["endISO"], "2030-06-15T23:30:00+03:00")
        self.assertEqual(found["brunch"]["whenText"], "Воскресенье, 16 июня 2030, 12:00–15:00")
        # no end: `showEnd` has nothing to show, the start + 6 h stays hidden
        self.assertEqual(found["ceremony"]["whenText"], "Суббота, 15 июня 2030, 11:00")
        self.assertEqual(found["ceremony"]["endISO"], "2030-06-15T17:00:00+03:00")

    def test_calendar_src(self):
        pages = build(calendar_src=lambda event_id: f"/assets/{F.MEDIA_DIR}/{event_id[::-1]}.ics")
        self.assertEqual(pages[0]["primaryEvent"]["icsPath"], f"/assets/{F.MEDIA_DIR}/rennid.ics")
        for page in pages:
            for event in walk_events(page):
                self.assertEqual(event["icsPath"], f"/assets/{F.MEDIA_DIR}/{event['id'][::-1]}.ics")

    def test_cards_ordered_by_start(self):
        page = build(lambda s, i: s["sections"][4]["widgets"][0].update(events=["brunch", "dinner", "ceremony"]))[0]
        self.assertEqual([item["id"] for item in events_widget(page)["items"]], ["ceremony", "dinner", "brunch"])

    def test_show_event_title(self):
        pages = build()
        self.assertTrue(widgets(pages[0], "date")[0]["showEventTitle"])


class PlaceTests(unittest.TestCase):
    def test_place_not_announced(self):
        brunch = next(e for e in events_widget(build()[0])["items"] if e["id"] == "brunch")
        place = brunch["location"]
        self.assertFalse(place["ready"])
        self.assertEqual((place["name"], place["photos"], place["directions"]), ("", [], None))
        self.assertFalse(place["hasMapLinks"])

    def test_place_and_programme(self):
        dinner = build()[0]["primaryEvent"]
        self.assertEqual(dinner["location"]["name"], "Усадьба «Образец»")
        self.assertTrue(dinner["location"]["hasMapLinks"])
        self.assertEqual(dinner["location"]["directions"]["id"], "directions")
        self.assertEqual(len(dinner["program"]["items"]), 5)
        self.assertEqual(dinner["program"]["items"][2], {"time": "17:00", "title": "Фотосессия", "text": ""})
        ceremony = events_widget(build()[0])["items"][0]
        self.assertIsNone(ceremony["program"])

    def test_set_without_places(self):
        page = build(site=F.pending_site(), invitations=F.pending_invitations())[0]
        event = page["primaryEvent"]
        self.assertFalse(event["location"]["ready"])
        self.assertEqual(event["whenText"], "Суббота, 10 августа 2030, 15:00")
        self.assertEqual(event["endISO"], "2030-08-10T22:00:00+05:00")
        self.assertEqual(len(event["program"]["items"]), 2)


class MediaTests(unittest.TestCase):
    def photos(self, page=0, **settings):
        return {item["id"]: item for item in build(**settings)[page]["primaryEvent"]["location"]["photos"]}

    def test_image(self):
        photo = self.photos()["venue-1"]
        self.assertFalse(photo["isVideo"])
        self.assertEqual(photo["src"], f"/assets/{F.MEDIA_DIR}/venue-1.png")
        self.assertEqual(photo["thumbSrc"], photo["src"])
        self.assertEqual(photo["posterSrc"], "")
        self.assertEqual((photo["width"], photo["height"], photo["ratio"]), (1200, 800, "1200 / 800"))
        self.assertEqual(photo["label"], "Усадьба со стороны пруда")

    def test_video(self):
        walk = self.photos()["walk"]
        self.assertTrue(walk["isVideo"])
        self.assertTrue(walk["posterSrc"].endswith("/walk-poster.png"))
        self.assertEqual(walk["thumbSrc"], walk["posterSrc"])
        self.assertEqual(walk["ratio"], "1280 / 720")
        self.assertEqual(walk["durationText"], "0:05")
        self.assertEqual(walk["label"], "Видео: Прогулка по усадьбе, 0:05")
        story = widgets(build()[0], "media")[0]
        self.assertEqual(story["layout"], "grid")
        self.assertEqual(story["items"][1]["ratio"], "720 / 1280")

    def test_without_description_and_duration(self):
        def change(site, invitations):
            site["media"]["walk"].pop("alt")
            site["media"]["venue-1"].pop("alt")

        info = dict(F.MEDIA_INFO)
        info["walk.mp4"] = MediaInfo("walk.mp4")
        photos = {item["id"]: item for item in build(change, media_info=info)[0]["primaryEvent"]["location"]["photos"]}
        self.assertEqual((photos["walk"]["alt"], photos["walk"]["label"]), ("Видео", "Видео"))
        self.assertEqual(photos["walk"]["durationText"], "")
        self.assertEqual(photos["venue-1"]["alt"], "Фотография")

    def test_size_from_the_file_or_unknown(self):
        def change(site, invitations):
            for key in ("width", "height"):
                site["media"]["walk"].pop(key)

        info = dict(F.MEDIA_INFO)
        info["venue-2.png"] = MediaInfo("venue-2.png")
        photos = {item["id"]: item for item in build(change, media_info=info)[0]["primaryEvent"]["location"]["photos"]}
        self.assertEqual(photos["walk"]["ratio"], "1280 / 720")
        self.assertEqual((photos["venue-2"]["width"], photos["venue-2"]["ratio"]), ("", ""))

    def test_cover_photo(self):
        for page in build():
            cover, *others = page["sections"]
            photo = cover["background"]
            self.assertEqual(set(photo), MEDIA_FIELDS)
            self.assertEqual(photo["id"], "cover")
            self.assertEqual(photo["src"], f"/assets/{F.MEDIA_DIR}/cover.png")
            self.assertEqual((photo["width"], photo["height"]), (2400, 1600))
            self.assertEqual(cover["backgroundFocus"], "center")
            # the fields exist on every section: empty on the others
            for item in others:
                self.assertEqual((item["background"], item["backgroundFocus"]), (None, ""))

    def test_cover_photo_focus_and_its_default(self):
        def focus(site, invitations):
            site["sections"][0]["backgroundFocus"] = "bottom-left"

        def default(site, invitations):
            site["sections"][0].pop("backgroundFocus")

        self.assertEqual(build(focus)[0]["sections"][0]["backgroundFocus"], "bottom-left")
        self.assertEqual(build(default)[0]["sections"][0]["backgroundFocus"], "center")

    def test_cover_without_a_photo(self):
        def change(site, invitations):
            site["sections"][0].pop("background")
            site["sections"][0].pop("backgroundFocus")
            site["media"].pop("cover")

        cover = build(change)[0]["sections"][0]
        self.assertEqual((cover["background"], cover["backgroundFocus"]), (None, "center"))
        pending = build(site=F.pending_site(), invitations=F.pending_invitations())[0]
        self.assertIsNone(pending["sections"][0]["background"])

    def test_cover_photo_is_published(self):
        site, invitations = F.site(), F.invitations()
        _pages, usage = _page.build_pages(site, invitations, F.settings())
        self.assertIn("cover", usage.media)
        self.assertIn(("media.cover.file", "cover.png"), _page.media_files(site, usage))
        # the same picture elsewhere is still one media item
        site["locations"]["manor"]["photos"].append("cover")
        _pages, usage = _page.build_pages(site, invitations, F.settings())
        self.assertEqual(
            [path for path, _name in _page.media_files(site, usage)].count("media.cover.file"), 1
        )

    def test_thumb_and_address_provider(self):
        def change(site, invitations):
            site["media"]["walk"]["thumb"] = "walk-small.jpg"

        page = build(change, media_src=lambda name: f"/assets/x/{len(name)}")[0]
        walk = {item["id"]: item for item in page["primaryEvent"]["location"]["photos"]}["walk"]
        self.assertEqual(walk["thumbSrc"], "/assets/x/14")
        self.assertEqual(walk["src"], "/assets/x/8")


class BuildTests(unittest.TestCase):
    def test_one_page_is_the_same_as_in_the_list(self):
        site, invitations = F.site(), F.invitations()
        self.assertEqual(_page.build_page(site, invitations[2], F.settings()), build()[2])

    def test_usage(self):
        site, invitations = F.site(), F.invitations()
        _pages, usage = _page.build_pages(site, invitations, F.settings())
        self.assertEqual(usage.sections, {s["id"] for s in site["sections"]})
        self.assertEqual(usage.media, set(site["media"]))
        self.assertEqual(usage.locations, set(site["locations"]))
        self.assertEqual(
            _page.media_files(site, usage)[:3],
            [
                ("media.cover.file", "cover.png"),
                ("media.directions.file", "directions.png"),
                ("media.proposal.file", "proposal.mp4"),
            ],
        )

    def test_the_data_is_not_changed(self):
        site, invitations = F.site(), F.invitations()
        _page.build_pages(site, invitations, F.settings())
        self.assertEqual((site, invitations), (F.SITE, F.INVITATIONS))


def inline_texts(site: dict) -> dict:
    """The site as it would be written without named texts: the section
    `invite` holds the texts itself (without the common form)."""
    texts = site.pop("texts")
    for widget in site["sections"][1]["widgets"]:
        text = widget["text"]
        if isinstance(text, str) and text.startswith("{text:"):
            value = texts[text[len("{text:") : -1]]
            widget["text"] = (
                value if isinstance(value, str) else {form: value[form] for form in ("ty", "vy")}
            )
    return site


class NamedTextTests(unittest.TestCase):
    texts = {
        "announce": "Мы, {coupleNames}, женимся!",
        "invite": {
            "ty": "Ждём тебя {eventDate} в {eventTime}. {text:sign}",
            "vy": "Ждём вас {eventDate} в {eventTime}. {text:sign}",
            "all": "ALL-FORM {eventDate}",
        },
        "sign": "{{{coupleNames}}}",
    }

    def with_texts(self, site, _invitations):
        site.pop("linkPreview")
        site["texts"] = copy.deepcopy(self.texts)
        widgets = site["sections"][1]["widgets"]
        widgets[0]["text"] = "{text:announce}"
        widgets[2]["text"] = "{text:invite}"

    @staticmethod
    def invite_texts(page) -> list[str]:
        section = next(item for item in page["sections"] if item["id"] == "invite")
        return [widget["text"] for widget in section["widgets"]]

    def test_the_form_and_the_primary_event_of_the_guest(self):
        pages = build(self.with_texts)
        # invitation #2: ty, the main event; #3: ty, the registration first;
        # #5: vy, the registration first; #6: vy, the main event
        self.assertEqual(
            self.invite_texts(pages[1]),
            ["Мы, Алиса и Боб, женимся!", "Ждём тебя 15 июня 2030 в 16:00. {Алиса и Боб}"],
        )
        self.assertEqual(self.invite_texts(pages[2])[1], "Ждём тебя 15 июня 2030 в 11:00. {Алиса и Боб}")
        self.assertEqual(self.invite_texts(pages[4])[1], "Ждём вас 15 июня 2030 в 11:00. {Алиса и Боб}")
        self.assertEqual(self.invite_texts(pages[5])[1], "Ждём вас 15 июня 2030 в 16:00. {Алиса и Боб}")

    def test_the_common_form_is_not_used_on_pages(self):
        for page in build(self.with_texts):
            self.assertNotIn("ALL-FORM", repr(page))

    def test_a_note_may_use_a_text(self):
        def mutate(site, invitations):
            self.with_texts(site, invitations)
            invitations[1]["events"]["dinner"]["note"] = "{text:invite}"

        pages = build(mutate)
        cards = [widget for section in pages[1]["sections"] for widget in section["widgets"]
                 if widget["type"] == "events"][0]["items"]  # fmt: skip
        dinner = next(item for item in cards if item["id"] == "dinner")
        self.assertEqual(dinner["note"], "Ждём тебя 15 июня 2030 в 16:00. {Алиса и Боб}")

    def test_a_greeting_is_not_parsed_for_texts(self):
        def mutate(site, invitations):
            self.with_texts(site, invitations)
            invitations[0]["greeting"] = "{text:announce}"

        page = build(mutate)[0]
        self.assertEqual(page["sections"][1]["title"], "{text:announce}")

    def test_usage_and_unused_texts(self):
        site, invitations = F.site(), F.invitations()
        self.with_texts(site, invitations)
        site["texts"]["spare"] = "Запасной текст"
        report = F.Collector()
        _pages, usage = _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(usage.texts, {"announce", "invite", "sign"})
        self.assertIn(
            "site.json: text 'spare' is not shown on any page nor in the link preview",
            report.warnings,
        )

    def test_a_text_only_in_a_hidden_widget_is_unused(self):
        def mutate(site, invitations):
            self.with_texts(site, invitations)
            site["texts"]["plus"] = "Со спутником"
            site["sections"][5]["widgets"][2]["text"] = "{text:plus}"  # hotel-booked
            invitations[5]["sections"]["travel"]["widgets"]["hotel-booked"]["visible"] = False

        site, invitations = F.site(), F.invitations()
        mutate(site, invitations)
        report = F.Collector()
        _page.build_pages(site, invitations, F.settings(), report)
        self.assertIn(
            "site.json: text 'plus' is not shown on any page nor in the link preview",
            report.warnings,
        )


class NamedTextsChangeNothingTests(unittest.TestCase):
    """The control case: the same texts written in place or as named texts
    give the same pages."""

    def render(self, site, invitations) -> list[tuple[str, str]]:
        report = F.Collector()
        index = _schema.check_data(site, invitations, report)
        self.assertTrue(index.ok, report.errors)
        trees, _usage = _page.build_pages(site, invitations, F.settings(), report)
        self.assertEqual(report.messages, [])
        template = support.build.load_template(support.ROOT / support.build.TEMPLATE_FILE)
        return support.build.render_pages(template, invitations, trees)

    def test_the_pages_are_byte_for_byte_the_same(self):
        site = F.site()
        site.pop("linkPreview")  # the description needs the named texts
        before = self.render(inline_texts(copy.deepcopy(site)), F.invitations())
        after = self.render(site, F.invitations())
        self.assertEqual(len(before), len(F.INVITATIONS))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
