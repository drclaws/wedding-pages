"""Tests for the calendar file (`event.ics`)."""

from __future__ import annotations

import re
import unittest
from datetime import timedelta

from tests import support
from tests.support import build, site_data


def unfold(payload: bytes) -> list[str]:
    """Content lines of an iCalendar file with the folding undone."""
    text = payload.decode("utf-8")
    return text.replace("\r\n ", "").split("\r\n")


def properties(payload: bytes) -> dict[str, str]:
    lines = [line for line in unfold(payload) if line]
    return dict(line.split(":", 1) for line in lines if not line.startswith(("BEGIN", "END")))


class CalendarFileTests(unittest.TestCase):
    def test_structure(self):
        payload = build.build_ics(site_data())
        self.assertEqual(
            [line.split(":", 1)[0] for line in unfold(payload)],
            [
                "BEGIN",
                "VERSION",
                "PRODID",
                "CALSCALE",
                "BEGIN",
                "UID",
                "DTSTAMP",
                "DTSTART",
                "DTEND",
                "SEQUENCE",
                "SUMMARY",
                "LOCATION",
                "END",
                "END",
                "",
            ],
        )
        lines = unfold(payload)
        self.assertEqual(lines[0], "BEGIN:VCALENDAR")
        self.assertEqual(lines[1], "VERSION:2.0")
        self.assertEqual(lines[4], "BEGIN:VEVENT")
        self.assertEqual(lines[-3:], ["END:VEVENT", "END:VCALENDAR", ""])

    def test_line_breaks_are_crlf_only(self):
        payload = build.build_ics(site_data())
        self.assertTrue(payload.endswith(b"END:VCALENDAR\r\n"))
        self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
        self.assertNotIn(b"\r", payload.replace(b"\r\n", b""))
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))

    def test_start_and_end_are_utc(self):
        # fixture: 16:00 at +03:00
        found = properties(build.build_ics(site_data()))
        self.assertEqual(found["DTSTART"], "20300601T130000Z")
        self.assertEqual(found["DTEND"], "20300601T190000Z")

    def test_other_time_zone(self):
        found = properties(build.build_ics(site_data(dateISO="2030-08-10T15:00:00+05:00")))
        self.assertEqual(found["DTSTART"], "20300810T100000Z")
        self.assertEqual(found["DTEND"], "20300810T160000Z")

    def test_utc_midnight_is_crossed_in_both_directions(self):
        # early morning east of Greenwich: still the previous day in UTC
        found = properties(build.build_ics(site_data(dateISO="2030-08-10T02:30:00+05:00")))
        self.assertEqual(found["DTSTART"], "20300809T213000Z")
        self.assertEqual(found["DTEND"], "20300810T033000Z")
        # evening west of Greenwich: already the next day in UTC, new year too
        found = properties(build.build_ics(site_data(dateISO="2030-12-31T20:00:00-05:00")))
        self.assertEqual(found["DTSTART"], "20310101T010000Z")
        self.assertEqual(found["DTEND"], "20310101T070000Z")

    def test_z_suffix_and_seconds(self):
        found = properties(build.build_ics(site_data(dateISO="2030-06-01T16:00Z")))
        self.assertEqual(found["DTSTART"], "20300601T160000Z")
        found = properties(build.build_ics(site_data(dateISO="2030-06-01T16:00:30.500+03:00")))
        self.assertEqual(found["DTSTART"], "20300601T130030Z")

    def test_default_duration_is_a_named_constant(self):
        self.assertEqual(build.ICS_DEFAULT_DURATION, timedelta(hours=6))

    def test_summary_is_neutral(self):
        payload = build.build_ics(site_data())
        self.assertEqual(properties(payload)["SUMMARY"], "Приглашение")
        text = payload.decode("utf-8")
        for secret in (support.COUPLE_NAMES, "Алиса", "Боб", support.DATE_TEXT):
            self.assertNotIn(secret, text)

    def test_location_only_when_the_venue_is_ready(self):
        found = properties(build.build_ics(site_data()))
        self.assertEqual(
            found["LOCATION"], "Усадьба в Энске\\, Энск\\, Вымышленная улица\\, 1"
        )
        site = site_data()
        site["venue"]["ready"] = False
        payload = build.build_ics(site)
        self.assertNotIn("LOCATION", properties(payload))
        self.assertNotIn("Энск", payload.decode("utf-8"))

    def test_sequence_grows_when_the_location_is_announced(self):
        # importing the file again then updates an event saved without a place
        self.assertEqual(properties(build.build_ics(site_data()))["SEQUENCE"], "1")
        site = site_data()
        site["venue"]["ready"] = False
        self.assertEqual(properties(build.build_ics(site))["SEQUENCE"], "0")
        self.assertEqual(
            properties(build.build_ics(site))["UID"],
            properties(build.build_ics(site_data()))["UID"],
        )

    def test_location_without_an_address(self):
        site = site_data()
        site["venue"]["address"] = ""
        self.assertEqual(properties(build.build_ics(site))["LOCATION"], "Усадьба в Энске")

    def test_text_escaping(self):
        self.assertEqual(
            build.ics_escape("a\\b; c, d\r\ne\nf\rg"), "a\\\\b\\; c\\, d\\ne\\nf\\ng"
        )
        self.assertEqual(build.ics_escape("tab\tand\x00bell\x07"), "tab\tand bell ")
        site = site_data()
        site["venue"]["name"] = "Зал; второй, этаж"
        site["venue"]["address"] = "Энск\nвход со двора \\ арка"
        self.assertEqual(
            properties(build.build_ics(site))["LOCATION"],
            "Зал\\; второй\\, этаж\\, Энск\\nвход со двора \\\\ арка",
        )

    def test_uid_is_stable_and_not_personal(self):
        first = properties(build.build_ics(site_data()))["UID"]
        self.assertEqual(first, properties(build.build_ics(site_data()))["UID"])
        self.assertRegex(first, r"\A[0-9a-f]{32}@invitation\Z")
        self.assertNotIn(support.MEDIA_DIR, first)
        other_date = site_data(dateISO="2030-06-02T16:00:00+03:00")
        self.assertNotEqual(first, properties(build.build_ics(other_date))["UID"])
        other_dir = site_data(mediaDir="another-m3d1a-d1rectory")
        self.assertNotEqual(first, properties(build.build_ics(other_dir))["UID"])
        # nothing else takes part: the names may change without a new event
        renamed = site_data(coupleNames="Кто-то ещё")
        self.assertEqual(first, properties(build.build_ics(renamed))["UID"])

    def test_output_is_deterministic(self):
        self.assertEqual(build.build_ics(site_data()), build.build_ics(site_data()))
        self.assertRegex(
            properties(build.build_ics(site_data()))["DTSTAMP"], r"\A\d{8}T\d{6}Z\Z"
        )


class FoldingTests(unittest.TestCase):
    def assertFolded(self, line: str) -> list[bytes]:
        folded = build.ics_fold(line).encode("utf-8")
        physical = folded.split(b"\r\n")
        for chunk in physical:
            self.assertLessEqual(len(chunk), 75, chunk)
            chunk.decode("utf-8")  # never split inside a UTF-8 sequence
        for chunk in physical[1:]:
            self.assertTrue(chunk.startswith(b" "), chunk)
        self.assertEqual(folded.decode("utf-8").replace("\r\n ", ""), line)
        return physical

    def test_short_lines_are_left_alone(self):
        self.assertEqual(build.ics_fold("SUMMARY:x"), "SUMMARY:x")
        exact = "X" * 75
        self.assertEqual(build.ics_fold(exact), exact)

    def test_ascii(self):
        physical = self.assertFolded("DESCRIPTION:" + "a" * 200)
        self.assertEqual(len(physical[0]), 75)
        self.assertEqual(len(physical[1]), 75)  # the leading space is counted

    def test_cyrillic_is_folded_on_character_boundaries(self):
        # two octets per letter and an odd offset: a naive split at octet 75
        # would cut a letter in half
        physical = self.assertFolded("LOCATION:" + "Ж" * 120)
        self.assertGreater(len(physical), 3)
        self.assertEqual(len(physical[0]), 75)  # 9 + 33 * 2
        self.assertEqual(len(physical[1]), 75)  # 1 + 37 * 2

    def test_wide_characters(self):
        self.assertFolded("LOCATION:" + "语" * 60 + "🎉" * 40 + "é" * 50)

    def test_long_cyrillic_location_in_the_file(self):
        site = site_data()
        site["venue"]["name"] = "Очень длинное название вымышленной усадьбы на берегу пруда"
        site["venue"]["address"] = (
            "Вымышленная область, Энский район, посёлок Примерный, улица Образцовая, 1"
        )
        payload = build.build_ics(site)
        for chunk in payload.split(b"\r\n"):
            self.assertLessEqual(len(chunk), 75, chunk)
            chunk.decode("utf-8")
        self.assertRegex(payload.decode("utf-8"), re.compile(r"\r\n [^\r]", re.S))
        self.assertEqual(
            properties(payload)["LOCATION"],
            "Очень длинное название вымышленной усадьбы на берегу пруда\\, "
            "Вымышленная область\\, Энский район\\, посёлок Примерный\\, "
            "улица Образцовая\\, 1",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
