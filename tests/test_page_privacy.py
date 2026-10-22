"""The page tree of an invitation holds nothing it does not see.

For every invitation of the fixture the serialised tree is searched for the
titles, times and ids of the events hidden from it, the ids and texts of the
sections and widgets hidden from it, and the greetings and notes of the
other invitations.
"""

from __future__ import annotations

import json
import unittest

from tests import fixtures_v2 as F
from tools import _page


def guest_strings(invitation: dict) -> list[str]:
    """The greeting and the notes of one invitation."""
    notes = [
        override["note"]
        for part in ("events", "sections")
        for override in invitation.get(part, {}).values()
        if override.get("note")
    ]
    return [invitation["greeting"], *notes]


def visible_events(site: dict, invitation: dict) -> set[str]:
    overrides = invitation.get("events", {})
    return {
        event_id
        for event_id, event in site["events"].items()
        if overrides.get(event_id, {}).get("visible", event.get("visible", True))
    }


def visible_sections(site: dict, invitation: dict) -> set[str]:
    overrides = invitation.get("sections", {})
    return {
        section["id"]
        for section in site["sections"]
        if overrides.get(section["id"], {}).get("visible", section.get("visible", True))
    }


def link_nodes(value) -> list[dict]:
    """Every link of every event node of a tree."""
    found = []
    if isinstance(value, dict):
        if isinstance(value.get("links"), list):
            found += value["links"]
        for item in value.values():
            found += link_nodes(item)
    elif isinstance(value, list):
        for item in value:
            found += link_nodes(item)
    return found


def hidden_event_strings(site: dict, event_id: str) -> list[str]:
    event = site["events"][event_id]
    strings = [f'"{event_id}"', f"-{event_id}", f"/{event_id}.ics", event["title"], event["start"][:16]]
    if "end" in event:
        strings.append(event["end"][:16])
    return strings


class TreePrivacyTests(unittest.TestCase):
    def check_set(self, site, invitations, info):
        pages, _usage = _page.build_pages(site, invitations, F.settings(media_info=info))
        checks = 0
        for number, (invitation, page) in enumerate(zip(invitations, pages), start=1):
            text = json.dumps(page, ensure_ascii=False)
            forbidden = []
            for other in invitations:
                if other is not invitation:
                    forbidden += [value for value in guest_strings(other) if value not in guest_strings(invitation)]
                    forbidden.append(other["token"])
            for event_id in set(site["events"]) - visible_events(site, invitation):
                forbidden += hidden_event_strings(site, event_id)
            for section_id in {s["id"] for s in site["sections"]} - visible_sections(site, invitation):
                forbidden += [f'"id": "{section_id}"', f"s-{section_id}"]
            for value in forbidden:
                with self.subTest(invitation=number, value=value):
                    self.assertNotIn(value, text)
                    checks += 1
            for value in guest_strings(invitation)[1:]:
                self.assertIn(json.dumps(value, ensure_ascii=False)[1:-1], text)
        return checks

    def test_main_set(self):
        self.assertGreater(self.check_set(F.site(), F.invitations(), F.MEDIA_INFO), 100)

    def test_set_without_places(self):
        self.assertGreater(self.check_set(F.pending_site(), F.pending_invitations(), {}), 10)

    def test_the_token_is_only_in_the_links_switched_on(self):
        site, invitations = F.site(), F.invitations()
        # one more link, visible to everybody and without the token
        site["events"]["dinner"]["links"]["seating"] = {
            "text": "Рассадка", "href": "/seating/", "visible": True
        }
        pages, _usage = _page.build_pages(site, invitations, F.settings())
        switched_on = 0
        for number, (invitation, page) in enumerate(zip(invitations, pages), start=1):
            token = invitation["token"]
            text = json.dumps(page, ensure_ascii=False)
            hrefs = [link["href"] for link in link_nodes(page)]
            with self.subTest(invitation=number):
                self.assertNotIn("{token}", text)
                self.assertIn("/seating/", hrefs)
                with_token = [href for href in hrefs if token in href]
                self.assertEqual(text.count(token), len(with_token))
                self.assertEqual(set(with_token), {f"/menu/{token}/"} if with_token else set())
                rest = text.replace(f'"href": "/menu/{token}/"', "")
                self.assertNotIn(token, rest)
                for other in invitations:
                    if other is not invitation:
                        self.assertNotIn(other["token"].lower(), text.lower())
            switched_on += bool(with_token)
        # the second and the eighth invitation
        self.assertEqual(switched_on, 2)

    def test_a_text_in_place_of_a_link_holds_no_token(self):
        site, invitations = F.site(), F.invitations()
        site["events"]["dinner"]["links"]["menu"]["pending"] = {
            "ty": "{greeting} Меню появится здесь позже — мы тебе напишем",
            "vy": "{greeting} Меню появится здесь позже — мы вам напишем",
        }
        pages, _usage = _page.build_pages(site, invitations, F.settings())
        guests_with_text = 0
        for number, (invitation, page) in enumerate(zip(invitations, pages), start=1):
            token = invitation["token"]
            text = json.dumps(page, ensure_ascii=False)
            links = link_nodes(page)
            with self.subTest(invitation=number):
                self.assertNotIn("{token}", text)
                for link in links:
                    if link["pending"]:
                        # no address at all: neither the token nor a path
                        self.assertEqual((link["href"], link["text"]), ("", ""))
                        self.assertTrue(link["pending"].startswith(invitation["greeting"]))
                    else:
                        self.assertEqual(link["href"], f"/menu/{token}/")
                # the token only in the `href` of the links shown to the guest
                self.assertEqual(text.count(token), sum(token in link["href"] for link in links))
            guests_with_text += any(link["pending"] for link in links)
        # everybody but the second and the eighth invitation
        self.assertEqual(guests_with_text, 6)

    def test_widget_switched_on_for_one_invitation(self):
        booked = F.SITE["sections"][5]["widgets"][2]["text"]
        pages, _usage = _page.build_pages(F.site(), F.invitations(), F.settings())
        for number, page in enumerate(pages, start=1):
            text = json.dumps(page, ensure_ascii=False)
            with self.subTest(invitation=number):
                self.assertEqual(booked["vy"] in text, number == 6)
                self.assertNotIn(booked["ty"], text)

    def test_places_and_media_of_hidden_events(self):
        pages, _usage = _page.build_pages(F.site(), F.invitations(), F.settings())
        text = json.dumps(pages[6], ensure_ascii=False)  # the ceremony is hidden
        for value in ("registry", "registry-1", F.SITE["locations"]["registry"]["name"]):
            self.assertNotIn(value, text)
        text = json.dumps(pages[1], ensure_ascii=False)  # the second day is hidden
        self.assertNotIn("terrace", text)
        self.assertNotIn("16 июня", text)  # no tab with the date of another day

    def test_ends_of_neighbours_do_not_reveal_a_hidden_event(self):
        site, invitations = F.site(), F.invitations()
        site["events"]["dinner"].pop("end")
        site["events"]["brunch"]["start"] = "2030-06-15T20:00:00+03:00"
        pages, _usage = _page.build_pages(site, invitations, F.settings())
        dinner = pages[1]["primaryEvent"]  # the brunch is hidden here
        self.assertEqual(dinner["endISO"], "2030-06-15T22:00:00+03:00")
        self.assertNotIn("20:00", json.dumps(pages[1], ensure_ascii=False))
        dinner = pages[0]["primaryEvent"]  # and switched on here: the same end
        self.assertEqual(dinner["endISO"], "2030-06-15T22:00:00+03:00")

    def test_calendar_events_hold_nothing_of_an_invitation(self):
        site, invitations = F.site(), F.invitations()
        events = _page.calendar_events(site, invitations, F.settings())
        self.assertEqual([event["id"] for event in events], ["ceremony", "dinner", "brunch"])
        text = json.dumps(events, ensure_ascii=False)
        for invitation in invitations:
            for value in [*guest_strings(invitation), invitation["token"]]:
                self.assertNotIn(value, text)
        # an event nobody sees is left out
        for invitation in invitations:
            invitation.get("events", {}).pop("brunch", None)
        events = _page.calendar_events(site, invitations, F.settings())
        self.assertEqual([event["id"] for event in events], ["ceremony", "dinner"])


if __name__ == "__main__":
    unittest.main()
