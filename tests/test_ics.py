"""Tests for the calendar files: one per event that at least one invitation
sees, for the whole site (`assets/<mediaDir>/<sha256[:16]>.ics`)."""

from __future__ import annotations

import hashlib
import re
import unittest
from datetime import timedelta

from tests import fixtures_v2 as F
from tests import support
from tests.support import build, invitations_data, site_data
from tools import _schema


def unfold(payload: bytes) -> list[str]:
    """Content lines of an iCalendar file with the folding undone."""
    text = payload.decode("utf-8")
    return text.replace("\r\n ", "").split("\r\n")


def properties(payload: bytes) -> dict[str, str]:
    lines = [line for line in unfold(payload) if line]
    return dict(line.split(":", 1) for line in lines if not line.startswith(("BEGIN", "END")))


def calendars(site: dict | None = None, invitations: list | None = None) -> dict[str, bytes]:
    """Event id -> the calendar file of valid data."""
    site = site_data() if site is None else site
    invitations = invitations_data() if invitations is None else invitations
    report = F.Collector()
    assert _schema.check_data(site, invitations, report).ok, report.errors
    return {
        event_id: entry.content
        for event_id, entry in support.fixture_calendars(site, invitations).items()
    }


def dinner(site: dict | None = None) -> dict[str, str]:
    """The properties of the calendar file of the dinner."""
    return properties(calendars(site)["dinner"])


class CalendarFileTests(unittest.TestCase):
    def test_one_file_per_event_somebody_sees(self):
        # Eve sees the brunch: every event has a file, in the order of the start
        self.assertEqual(list(calendars()), ["dinner", "brunch"])
        # nobody sees the brunch: no file for it
        self.assertEqual(list(calendars(invitations=invitations_data()[1:])), ["dinner"])
        site = site_data()
        invitations = invitations_data()
        invitations[1]["events"]["brunch"] = {"visible": True}
        del invitations[0]["events"]
        self.assertEqual(list(calendars(site, invitations)), ["dinner", "brunch"])

    def test_the_name_is_the_hash_of_the_contents(self):
        found = support.fixture_calendars()
        for event_id, entry in found.items():
            with self.subTest(event=event_id):
                digest = hashlib.sha256(entry.content).hexdigest()[:16]
                self.assertEqual(entry.name, f"{digest}.ics")
                # the id of the event and the media directory are not in it
                self.assertNotIn(event_id, entry.name)
        self.assertNotEqual(found["dinner"].name, found["brunch"].name)
        # another end, title or place: another name (and the same uid)
        site = site_data()
        site["events"]["dinner"]["end"] = "2030-06-01T23:00:00+03:00"
        changed = support.fixture_calendars(site)["dinner"]
        self.assertNotEqual(changed.name, found["dinner"].name)
        self.assertEqual(properties(changed.content)["UID"], dinner()["UID"])
        # the notes and the greetings of the invitations change nothing
        invitations = invitations_data()
        invitations[1]["events"]["dinner"]["note"] = "Другая приписка."
        invitations[0]["greeting"] = "Привет!"
        self.assertEqual(support.fixture_calendars(invitations=invitations), found)

    def test_structure(self):
        payload = calendars()["dinner"]
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
        for payload in calendars().values():
            self.assertTrue(payload.endswith(b"END:VCALENDAR\r\n"))
            self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
            self.assertNotIn(b"\r", payload.replace(b"\r\n", b""))
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))

    def test_start_and_end_are_utc(self):
        self.assertEqual(dinner()["DTSTART"], "20300601T130000Z")
        self.assertEqual(dinner()["DTEND"], "20300601T190000Z")
        brunch = properties(calendars()["brunch"])
        self.assertEqual((brunch["DTSTART"], brunch["DTEND"]), ("20300602T090000Z", "20300602T110000Z"))

    def test_an_event_without_an_end_lasts_six_hours(self):
        site = site_data()
        del site["events"]["dinner"]["end"]
        self.assertEqual(dinner(site)["DTEND"], "20300601T190000Z")
        site["events"]["dinner"]["end"] = ""
        self.assertEqual(dinner(site)["DTEND"], "20300601T190000Z")

    def test_the_end_is_not_cut_by_the_next_event(self):
        # the brunch starts two hours after the dinner, which has no end: the
        # dinner still lasts six hours, for everybody
        site = site_data()
        del site["events"]["dinner"]["end"]
        site["events"]["brunch"].update(start="2030-06-01T18:00:00+03:00", end="2030-06-01T23:00:00+03:00")
        self.assertEqual(dinner(site)["DTEND"], "20300601T190000Z")
        # nor is an explicit end that overlaps the next event
        site["events"]["dinner"]["end"] = "2030-06-01T23:30:00+03:00"
        self.assertEqual(dinner(site)["DTEND"], "20300601T203000Z")

    def test_other_time_zone(self):
        site = site_data()
        del site["events"]["dinner"]["end"]
        site["events"]["dinner"]["start"] = "2030-08-10T15:00:00+05:00"
        site["events"]["brunch"]["start"] = "2030-08-11T12:00:00+05:00"
        site["events"]["brunch"]["end"] = "2030-08-11T14:00:00+05:00"
        self.assertEqual(dinner(site)["DTSTART"], "20300810T100000Z")
        self.assertEqual(dinner(site)["DTEND"], "20300810T160000Z")

    def test_utc_midnight_is_crossed_in_both_directions(self):
        site = site_data()
        del site["events"]["brunch"]
        del site["events"]["dinner"]["end"]
        invitations = invitations_data()
        del invitations[0]["events"]
        # early morning east of Greenwich: still the previous day in UTC
        site["events"]["dinner"]["start"] = "2030-08-10T02:30:00+05:00"
        found = properties(calendars(site, invitations)["dinner"])
        self.assertEqual(found["DTSTART"], "20300809T213000Z")
        self.assertEqual(found["DTEND"], "20300810T033000Z")
        # evening west of Greenwich: already the next day in UTC, new year too
        site["events"]["dinner"]["start"] = "2030-12-31T20:00:00-05:00"
        found = properties(calendars(site, invitations)["dinner"])
        self.assertEqual(found["DTSTART"], "20310101T010000Z")
        self.assertEqual(found["DTEND"], "20310101T070000Z")

    def test_z_suffix_and_seconds(self):
        site = site_data()
        del site["events"]["dinner"]["end"]
        site["events"]["dinner"]["start"] = "2030-06-01T16:00Z"
        self.assertEqual(dinner(site)["DTSTART"], "20300601T160000Z")
        site["events"]["dinner"]["start"] = "2030-06-01T16:00:30.500+03:00"
        self.assertEqual(dinner(site)["DTSTART"], "20300601T130030Z")

    def test_default_duration_is_a_named_constant(self):
        self.assertEqual(build.ICS_DEFAULT_DURATION, timedelta(hours=6))

    def test_summary_is_the_title_of_the_event(self):
        self.assertEqual(dinner()["SUMMARY"], "Праздничный ужин")
        for payload in calendars().values():
            text = payload.decode("utf-8")
            for secret in (support.COUPLE_NAMES, "Алиса", "Боб"):
                self.assertNotIn(secret, text)

    def test_nothing_of_the_invitations_goes_into_the_file(self):
        for payload in calendars().values():
            text = payload.decode("utf-8")
            for secret in (
                support.GREETING_TY, support.GREETING_VY, support.GREETING_THIRD,
                support.NOTE, support.TRAVEL_NOTE, support.EVENT_NOTE,
                support.TOKEN_A, support.TOKEN_B, support.TOKEN_C, support.MEDIA_DIR,
            ):  # fmt: skip
                self.assertNotIn(secret, text)

    def test_location_only_when_the_place_is_announced(self):
        self.assertEqual(
            dinner()["LOCATION"], "Усадьба в Энске\\, Энск\\, Вымышленная улица\\, 1"
        )
        site = site_data()
        site["locations"]["manor"] = {"ready": False}
        del site["media"]
        site["sections"] = [section for section in site["sections"] if section["id"] != "video"]
        payload = calendars(site)["dinner"]
        self.assertNotIn("LOCATION", properties(payload))
        self.assertNotIn("Энск", payload.decode("utf-8"))

    def test_sequence_grows_when_the_location_is_announced(self):
        # importing the file again then updates an event saved without a place
        self.assertEqual(dinner()["SEQUENCE"], "1")
        site = site_data()
        site["locations"]["manor"] = {"ready": False}
        del site["media"]
        site["sections"] = [section for section in site["sections"] if section["id"] != "video"]
        self.assertEqual(dinner(site)["SEQUENCE"], "0")
        self.assertEqual(dinner(site)["UID"], dinner()["UID"])

    def test_location_without_an_address(self):
        site = site_data()
        del site["locations"]["manor"]["address"]
        self.assertEqual(dinner(site)["LOCATION"], "Усадьба в Энске")

    def test_text_escaping(self):
        self.assertEqual(
            build.ics_escape("a\\b; c, d\r\ne\nf\rg"), "a\\\\b\\; c\\, d\\ne\\nf\\ng"
        )
        self.assertEqual(build.ics_escape("tab\tand\x00bell\x07"), "tab\tand bell ")
        site = site_data()
        site["locations"]["manor"]["name"] = "Зал; второй, этаж"
        site["locations"]["manor"]["address"] = "Энск, вход со двора \\ арка"
        site["events"]["dinner"]["title"] = "Ужин; танцы, салют"
        found = dinner(site)
        self.assertEqual(
            found["LOCATION"], "Зал\\; второй\\, этаж\\, Энск\\, вход со двора \\\\ арка"
        )
        self.assertEqual(found["SUMMARY"], "Ужин\\; танцы\\, салют")

    def test_uid_is_stable_and_not_personal(self):
        first = dinner()["UID"]
        self.assertEqual(first, dinner()["UID"])
        self.assertRegex(first, r"\A[0-9a-f]{32}@invitation\Z")
        self.assertNotIn(support.MEDIA_DIR, first)
        # the uid: sha256 of the media directory, the id and the start
        expected = hashlib.sha256(
            f"{support.MEDIA_DIR}\ndinner\n2030-06-01T16:00:00+03:00".encode()
        ).hexdigest()[:32]
        self.assertEqual(first, f"{expected}@invitation")
        # another start, media directory or event: another uid
        site = site_data()
        site["events"]["dinner"]["start"] = "2030-06-01T17:00:00+03:00"
        self.assertNotEqual(first, dinner(site)["UID"])
        self.assertNotEqual(first, dinner(site_data(mediaDir="another-m3d1a-d1rectory"))["UID"])
        self.assertNotEqual(first, properties(calendars()["brunch"])["UID"])
        # nothing else takes part: names, titles and ends may change without a
        # new entry
        site = site_data(coupleNames="Кто-то ещё")
        site["events"]["dinner"]["title"] = "Ужин"
        site["events"]["dinner"]["end"] = "2030-06-01T20:00:00+03:00"
        self.assertEqual(first, dinner(site)["UID"])

    def test_output_is_deterministic(self):
        self.assertEqual(calendars(), calendars())
        self.assertRegex(dinner()["DTSTAMP"], r"\A\d{8}T\d{6}Z\Z")




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
        site["locations"]["manor"]["name"] = (
            "Очень длинное название вымышленной усадьбы на берегу пруда"
        )
        site["locations"]["manor"]["address"] = (
            "Вымышленная область, Энский район, посёлок Примерный, улица Образцовая, 1"
        )
        payload = calendars(site)["dinner"]
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


class SingleEventCalendarTests(unittest.TestCase):
    """`build_event_ics`: the calendar file of one event."""

    #: The calendar file of the fixture dinner, byte for byte.
    FIXTURE_ICS = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//invitation//static site build//RU\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:{uid}@invitation\r\n"
        "DTSTAMP:20000101T000000Z\r\n"
        "DTSTART:20300601T130000Z\r\n"
        "DTEND:20300601T190000Z\r\n"
        "SEQUENCE:1\r\n"
        "SUMMARY:Праздничный ужин\r\n"
        "LOCATION:Усадьба в Энске\\, Энск\\, Вымышленная \r\n"
        " улица\\, 1\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    def event_ics(self, **overrides) -> bytes:
        arguments = {
            "uid": "0123456789abcdef",
            "start": build.parse_date_iso("2030-06-15T16:00+03:00"),
            "end": build.parse_date_iso("2030-06-15T23:30+03:00"),
            "summary": "Праздничный ужин",
            "location": "",
        }
        arguments.update(overrides)
        return build.build_event_ics(**arguments)

    def test_the_file_of_an_event_byte_for_byte(self):
        # the uid: sha256 of the media directory, the id and the start of the event
        uid = hashlib.sha256(
            f"{support.MEDIA_DIR}\ndinner\n2030-06-01T16:00:00+03:00".encode()
        ).hexdigest()[:32]
        expected = self.FIXTURE_ICS.format(uid=uid).encode("utf-8")
        self.assertEqual(calendars()["dinner"], expected)
        self.assertEqual(
            build.build_event_ics(
                uid=uid,
                start=build.parse_date_iso("2030-06-01T16:00:00+03:00"),
                end=build.parse_date_iso("2030-06-01T22:00:00+03:00"),
                summary="Праздничный ужин",
                location=f"{support.PLACE_NAME}, {support.PLACE_ADDRESS}",
            ),
            expected,
        )
        site = site_data()
        site["locations"]["manor"] = {"ready": False}
        del site["media"]
        site["sections"] = [section for section in site["sections"] if section["id"] != "video"]
        pending = (
            expected.decode("utf-8")
            .replace("SEQUENCE:1", "SEQUENCE:0")
            .replace("LOCATION:Усадьба в Энске\\, Энск\\, Вымышленная \r\n улица\\, 1\r\n", "")
        )
        self.assertEqual(calendars(site)["dinner"], pending.encode("utf-8"))

    def test_structure_and_times(self):
        payload = self.event_ics()
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
                "END",
                "END",
                "",
            ],
        )
        found = properties(payload)
        self.assertEqual(found["UID"], "0123456789abcdef@invitation")
        self.assertEqual(found["DTSTART"], "20300615T130000Z")
        # the end comes from the argument, not from the default duration
        self.assertEqual(found["DTEND"], "20300615T203000Z")
        self.assertEqual(found["SUMMARY"], "Праздничный ужин")
        found = properties(self.event_ics(end=build.parse_date_iso("2030-06-16T02:00+05:00")))
        self.assertEqual(found["DTEND"], "20300615T210000Z")

    def test_summary_is_escaped_and_folded(self):
        found = properties(self.event_ics(summary="Ужин; танцы, салют \\ фейерверк"))
        self.assertEqual(found["SUMMARY"], "Ужин\\; танцы\\, салют \\\\ фейерверк")
        summary = "Очень длинное название вымышленного праздничного вечера на берегу пруда"
        payload = self.event_ics(summary=summary)
        physical = payload.split(b"\r\n")
        self.assertTrue(any(line.startswith(b"SUMMARY:") for line in physical))
        self.assertTrue(any(line.startswith(b" ") for line in physical))
        for chunk in physical:
            self.assertLessEqual(len(chunk), 75, chunk)
            chunk.decode("utf-8")
        self.assertEqual(properties(payload)["SUMMARY"], summary)

    def test_location_and_sequence(self):
        found = properties(self.event_ics())
        self.assertNotIn("LOCATION", found)
        self.assertEqual(found["SEQUENCE"], "0")
        found = properties(self.event_ics(location="Терраса, Энск, ул. Примерная; вход"))
        self.assertEqual(found["LOCATION"], "Терраса\\, Энск\\, ул. Примерная\\; вход")
        self.assertEqual(found["SEQUENCE"], "1")
        self.assertEqual(
            properties(self.event_ics(location="Терраса"))["UID"],
            properties(self.event_ics())["UID"],
        )

    def test_output_is_deterministic(self):
        self.assertEqual(self.event_ics(), self.event_ics())
        self.assertEqual(
            self.event_ics(location="Терраса"), self.event_ics(location="Терраса")
        )

    def test_no_line_breaks_or_control_characters_from_the_arguments(self):
        hostile = "a\r\nX-INJECTED:1\rb\nc\x00d\x1be\x7ff\x0bg"
        payload = self.event_ics(summary=hostile, location=hostile)
        lines = payload.split(b"\r\n")
        self.assertEqual(lines[-1], b"")
        for line in lines:
            self.assertNotIn(b"\r", line)
            self.assertNotIn(b"\n", line)
            self.assertFalse(any(byte < 0x20 or byte == 0x7F for byte in line), line)
        self.assertFalse(any(line.startswith("X-INJECTED") for line in unfold(payload)))
        self.assertEqual(
            [name for name in properties(payload) if name not in ("SUMMARY", "LOCATION")],
            ["VERSION", "PRODID", "CALSCALE", "UID", "DTSTAMP", "DTSTART", "DTEND", "SEQUENCE"],
        )

    def test_invalid_arguments(self):
        for uid in ("", "abc\r\nX-INJECTED:1", "a@b", "a b", "Ёж", None):
            with self.subTest(uid=uid):
                with self.assertRaises(ValueError):
                    self.event_ics(uid=uid)
        start = build.parse_date_iso("2030-06-15T16:00+03:00")
        for end in (start, start - timedelta(minutes=1)):
            with self.subTest(end=end):
                with self.assertRaises(ValueError):
                    self.event_ics(end=end)
        with self.assertRaises(ValueError):
            self.event_ics(start=start.replace(tzinfo=None))
        with self.assertRaises(TypeError):  # the arguments are keyword-only
            build.build_event_ics("uid", start, start + timedelta(hours=1), "x")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
