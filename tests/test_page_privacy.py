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
        dinner = pages[0]["primaryEvent"]  # and switched on here
        self.assertEqual(dinner["endISO"], "2030-06-15T20:00:00+03:00")


if __name__ == "__main__":
    unittest.main()
