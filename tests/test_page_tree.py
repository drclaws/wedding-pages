"""Tests for the page tree of an invitation (`tools/_page.py`).

The trees are built from the fictional fixture data (`tests/fixtures_v2.py`);
the facts of the media files come from a stub, no file is read.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from tests import fixtures_v2 as F
from tools import _page, _schema
from tools._media import MediaInfo

ROOT_FIELDS = {
    "greeting", "form", "ty", "vy", "coupleNames", "rsvpDeadline", "mediaPath",
    "faviconPath", "faviconType", "ogImage", "ogImageType", "ogImageWidth",
    "ogImageHeight", "primaryEvent", "sections",
}  # fmt: skip
SECTION_FIELDS = {
    "id", "type", "domId", "titleId", "title", "titleHidden", "align", "width",
    "note", "eyebrow", "event", "widgets",
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
    "ratio", "durationText", "label", "alt", "caption",
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
            site["sections"].pop(8)

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
        self.assertEqual(events_widget(page)["primaryDomId"], "s-where-w1-ceremony")

    def test_texts_use_the_primary_event(self):
        pages = build()
        self.assertTrue(section(pages[2], "invite")["widgets"][0]["text"].endswith("Ждём тебя 15 июня 2030 в 11:00."))
        self.assertTrue(section(pages[0], "invite")["widgets"][0]["text"].endswith("Ждём тебя 15 июня 2030 в 16:00."))

    def test_other_placeholders(self):
        def change(site, invitations):
            site["sections"][1]["widgets"][0]["text"] = "{eventTitle} / {eventPlace} / {greeting}"

        pages = build(change)
        self.assertEqual(
            section(pages[2], "invite")["widgets"][0]["text"],
            "Регистрация / Дворец бракосочетания Энска / Дорогая Ева!",
        )

    def test_primary_card_is_not_among_the_cards(self):
        pages = build(lambda s, i: s["sections"][5]["widgets"][0].update(events=["dinner"]))
        self.assertEqual(events_widget(pages[2])["primaryDomId"], "")
        self.assertEqual(events_widget(pages[0])["primaryDomId"], "s-where-w1-dinner")


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

    def test_hidden_event_does_not_shorten_the_others(self):
        def change(site, invitations):
            site["events"]["ceremony"].pop("end")
            site["events"]["secret"] = {
                "title": "Тайное", "location": "registry",
                "start": "2030-06-15T13:00:00+03:00", "visible": False,
            }  # fmt: skip
            invitations[0]["events"]["secret"] = {"visible": True}

        pages = build(change)
        ceremony = {page["greeting"]: next(e for e in events_widget(page)["items"] if e["id"] == "ceremony") for page in pages[:2]}
        self.assertEqual(ceremony["Дорогая Кэрол!"]["endISO"], "2030-06-15T13:00:00+03:00")
        self.assertEqual(ceremony["Дорогой Дэйв!"]["endISO"], "2030-06-15T16:00:00+03:00")

    def test_default_duration(self):
        pages = build(default_duration=timedelta(hours=2))
        brunch = next(e for e in events_widget(pages[0])["items"] if e["id"] == "brunch")
        self.assertEqual(brunch["endISO"], "2030-06-16T14:00:00+03:00")
        self.assertEqual(brunch["whenText"], "Воскресенье, 16 июня 2030, 12:00")

    def test_date_widget_of_a_hidden_event_is_left_out(self):
        pages = build(lambda s, i: s["sections"][4]["widgets"].append({"type": "date", "event": "brunch"}))
        self.assertEqual(len(widgets(pages[0], "date")), 2)
        self.assertEqual(len(widgets(pages[1], "date")), 1)

    def test_programme_of_hidden_events_is_left_out(self):
        pages = build(lambda s, i: s["sections"][5]["widgets"].append({"type": "schedule", "schedule": "dinner"}))
        self.assertEqual(len(widgets(pages[0], "schedule")), 1)

        def change(site, invitations):
            site["events"]["ceremony"]["schedule"] = "dinner"
            site["events"]["dinner"].pop("schedule")
            site["sections"][5]["widgets"].append({"type": "schedule", "schedule": "dinner"})

        pages = build(change)
        self.assertEqual(len(widgets(pages[0], "schedule")), 1)
        self.assertEqual(widgets(pages[6], "schedule"), [])


class OverrideTests(unittest.TestCase):
    def test_sections_switched_on_and_off(self):
        pages = build()
        self.assertIsNotNone(section(pages[0], "plus-one"))
        self.assertIsNone(section(pages[1], "plus-one"))
        self.assertIsNone(section(pages[1], "personal"))  # nothing to show
        self.assertIsNotNone(section(pages[2], "travel"))

    def test_widget_switched_on(self):
        pages = build()
        travel_3 = [w["domId"] for w in section(pages[2], "travel")["widgets"]]
        travel_6 = [w["domId"] for w in section(pages[5], "travel")["widgets"]]
        self.assertEqual(travel_3, ["s-travel-w1", "s-travel-w2"])
        self.assertEqual(travel_6, ["s-travel-w1", "s-travel-w2", "s-travel-w3"])
        self.assertIn("Для вас забронирован номер", section(pages[5], "travel")["widgets"][2]["text"])

    def test_widget_switched_off(self):
        def change(site, invitations):
            site["sections"][6]["widgets"][2]["visible"] = True
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
        self.assertIn("приглашаем тебя", section(pages[0], "invite")["widgets"][0]["text"])
        self.assertIn("приглашаем вас", section(pages[4], "invite")["widgets"][0]["text"])

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


class DomIdTests(unittest.TestCase):
    def test_ids_are_unique_and_follow_the_path(self):
        for page in build():
            ids = list(walk_dom_ids(page["sections"]))
            self.assertEqual(len(ids), len(set(ids)))
            for dom_id in ids:
                self.assertTrue(dom_id.startswith("s-"), dom_id)

    def test_one_event_in_two_widgets(self):
        page = build()[0]
        self.assertEqual(widgets(page, "date")[0]["event"]["domId"], "s-when-w1-dinner")
        self.assertEqual(events_widget(page)["items"][1]["domId"], "s-where-w1-dinner")
        self.assertEqual(events_widget(page)["items"][1]["location"]["domId"], "s-where-w1-dinner-manor")
        self.assertEqual(events_widget(page)["items"][1]["program"]["domId"], "s-where-w1-dinner-program")
        self.assertEqual(section(page, "cover")["event"]["domId"], "s-cover-dinner")

    def test_sections_and_widgets(self):
        page = build()[0]
        invite = section(page, "invite")
        self.assertEqual((invite["domId"], invite["titleId"]), ("s-invite", "s-invite-title"))
        self.assertEqual(invite["widgets"][0]["domId"], "s-invite-w1")

    def test_location_widget(self):
        page = build()[2]
        place = widgets(page, "location")[0]
        self.assertEqual(place["domId"], "s-travel-w2")
        self.assertEqual(place["location"]["domId"], "s-travel-w2-hotel")
        self.assertTrue(place["location"]["compact"])


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
        self.assertEqual(dinner["icsPath"], "/i/0R-pnCqGFgrPOR5e-NluZw/dinner.ics")

    def test_cards_ordered_by_start(self):
        page = build(lambda s, i: s["sections"][5]["widgets"][0].update(events=["brunch", "dinner", "ceremony"]))[0]
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
        self.assertEqual(event["endISO"], "2030-08-10T21:00:00+05:00")
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
            _page.media_files(site, usage)[:2],
            [("media.directions.file", "directions.png"), ("media.proposal.file", "proposal.mp4")],
        )

    def test_the_data_is_not_changed(self):
        site, invitations = F.site(), F.invitations()
        _page.build_pages(site, invitations, F.settings())
        self.assertEqual((site, invitations), (F.SITE, F.INVITATIONS))


if __name__ == "__main__":
    unittest.main()
