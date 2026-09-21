"""Tests for the Russian date strings and the end times of events
(`tools/_dates.py`)."""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from tests.support import build

from tools import _dates as dates

SIX_HOURS = timedelta(hours=6)


def at(text: str) -> datetime:
    """A moment as the build parses it from the data."""
    return build.parse_date_iso(text)


class DateStringTests(unittest.TestCase):
    def test_every_month_is_in_the_genitive(self):
        months = (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        )
        for number, name in enumerate(months, start=1):
            with self.subTest(month=number):
                moment = at(f"2030-{number:02d}-15T12:00+03:00")
                self.assertEqual(dates.format_day_month(moment), f"15 {name}")
                self.assertEqual(dates.format_date(moment), f"15 {name} 2030")

    def test_every_day_of_the_week(self):
        # 10 June 2030 is a Monday
        weekdays = (
            "Понедельник",
            "Вторник",
            "Среда",
            "Четверг",
            "Пятница",
            "Суббота",
            "Воскресенье",
        )
        for offset, name in enumerate(weekdays):
            with self.subTest(weekday=name):
                moment = at(f"2030-06-{10 + offset:02d}T16:00+03:00")
                self.assertEqual(
                    dates.format_when(moment), f"{name}, {10 + offset} июня 2030, 16:00"
                )

    def test_time_has_no_leading_zero_in_the_hour(self):
        for text, expected in (
            ("2030-06-15T09:30+03:00", "9:30"),
            ("2030-06-15T16:00+03:00", "16:00"),
            ("2030-06-15T00:00+03:00", "0:00"),
            ("2030-06-15T00:05+03:00", "0:05"),
            ("2030-06-15T01:07:45+03:00", "1:07"),
            ("2030-06-15T23:59+03:00", "23:59"),
        ):
            with self.subTest(text=text):
                self.assertEqual(dates.format_time(at(text)), expected)

    def test_single_digit_days(self):
        self.assertEqual(dates.format_date(at("2031-01-01T00:30+03:00")), "1 января 2031")

    def test_the_date_is_that_of_the_offset_of_the_event(self):
        # 01:00 in Moscow is still 14 June in UTC
        moment = at("2030-06-15T01:00+03:00")
        self.assertEqual(moment.astimezone(timezone.utc).day, 14)
        self.assertEqual(dates.format_date(moment), "15 июня 2030")
        self.assertEqual(dates.format_time(moment), "1:00")
        self.assertEqual(dates.format_when(moment), "Суббота, 15 июня 2030, 1:00")
        # 22:00 west of Greenwich is already 16 June in UTC
        moment = at("2030-06-15T22:00-05:00")
        self.assertEqual(dates.format_when(moment), "Суббота, 15 июня 2030, 22:00")

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is not available")
    def test_the_time_zone_of_the_machine_does_not_matter(self):
        start = at("2030-06-15T22:00+03:00")
        end = at("2030-06-15T21:30Z")
        expected = "Суббота, 15 июня 2030, 22:00 — 16 июня, 0:30"
        saved = os.environ.get("TZ")
        try:
            for zone in ("UTC", "Pacific/Kiritimati", "America/Los_Angeles"):
                with self.subTest(zone=zone):
                    os.environ["TZ"] = zone
                    time.tzset()
                    self.assertEqual(dates.format_when(start, end), expected)
                    self.assertEqual(dates.format_tab(start, False), "15 июня, 22:00")
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()

    def test_a_moment_without_an_offset_is_rejected(self):
        naive = datetime(2030, 6, 15, 16, 0)
        for function in (dates.format_time, dates.format_day_month, dates.format_date):
            with self.subTest(function=function.__name__):
                with self.assertRaises(ValueError):
                    function(naive)
        with self.assertRaises(ValueError):
            dates.format_when(naive)
        with self.assertRaises(ValueError):
            dates.format_when(at("2030-06-15T16:00+03:00"), naive)
        with self.assertRaises(ValueError):
            dates.same_local_day([naive])


class WhenTests(unittest.TestCase):
    start = at("2030-06-15T16:00+03:00")

    def test_without_an_end(self):
        self.assertEqual(dates.format_when(self.start), "Суббота, 15 июня 2030, 16:00")

    def test_an_end_on_the_same_day(self):
        self.assertEqual(
            dates.format_when(self.start, at("2030-06-15T23:30+03:00")),
            "Суббота, 15 июня 2030, 16:00–23:30",
        )

    def test_an_end_after_midnight(self):
        self.assertEqual(
            dates.format_when(at("2030-06-15T22:00+03:00"), at("2030-06-16T02:00+03:00")),
            "Суббота, 15 июня 2030, 22:00 — 16 июня, 2:00",
        )
        self.assertEqual(
            dates.format_when(at("2030-06-15T22:00+03:00"), at("2030-06-16T00:00+03:00")),
            "Суббота, 15 июня 2030, 22:00 — 16 июня, 0:00",
        )

    def test_an_end_in_the_next_year(self):
        self.assertEqual(
            dates.format_when(at("2030-12-31T22:00+03:00"), at("2031-01-01T02:00+03:00")),
            "Вторник, 31 декабря 2030, 22:00 — 1 января 2031, 2:00",
        )

    def test_the_end_is_shown_on_the_clock_of_the_start(self):
        # 20:30 UTC is 23:30 at +03:00: the same local day
        self.assertEqual(
            dates.format_when(self.start, at("2030-06-15T20:30Z")),
            "Суббота, 15 июня 2030, 16:00–23:30",
        )
        # 16 June 01:00 at +05:00 is still 15 June 23:00 at +03:00
        self.assertEqual(
            dates.format_when(self.start, at("2030-06-16T01:00+05:00")),
            "Суббота, 15 июня 2030, 16:00–23:00",
        )
        # 15 June 23:00 at -05:00 is already 16 June 07:00 at +03:00
        self.assertEqual(
            dates.format_when(self.start, at("2030-06-15T23:00-05:00")),
            "Суббота, 15 июня 2030, 16:00 — 16 июня, 7:00",
        )

    def test_an_end_that_is_not_later_is_rejected(self):
        for end in ("2030-06-15T16:00+03:00", "2030-06-15T15:00+03:00", "2030-06-15T13:00Z"):
            with self.subTest(end=end):
                with self.assertRaises(ValueError):
                    dates.format_when(self.start, at(end))


class TabLabelTests(unittest.TestCase):
    def test_one_day(self):
        starts = [at("2030-06-15T11:00+03:00"), at("2030-06-15T16:00+03:00")]
        self.assertTrue(dates.same_local_day(starts))
        self.assertEqual(dates.format_tab(starts[0], True), "11:00")

    def test_several_days(self):
        starts = [at("2030-06-15T11:00+03:00"), at("2030-06-16T09:00+03:00")]
        self.assertFalse(dates.same_local_day(starts))
        self.assertEqual(dates.format_tab(starts[0], False), "15 июня, 11:00")
        self.assertEqual(dates.format_tab(starts[1], False), "16 июня, 9:00")

    def test_each_moment_on_its_own_clock(self):
        # 14 and 15 June in UTC, but both on 15 June where they happen
        self.assertTrue(
            dates.same_local_day([at("2030-06-15T01:00+03:00"), at("2030-06-15T23:00+03:00")])
        )
        # the same local day at different offsets (16 June 04:30 in UTC)
        self.assertTrue(
            dates.same_local_day([at("2030-06-15T10:00+03:00"), at("2030-06-15T23:30-05:00")])
        )
        # one hour apart, but on both sides of the local midnight
        self.assertFalse(
            dates.same_local_day([at("2030-06-15T23:30+03:00"), at("2030-06-16T00:30+03:00")])
        )

    def test_empty_and_single_sets(self):
        self.assertTrue(dates.same_local_day([]))
        self.assertTrue(dates.same_local_day([at("2030-06-15T11:00+03:00")]))


class TimelineTests(unittest.TestCase):
    def timeline(self, *events, duration=SIX_HOURS):
        items = [
            (event_id, at(start), at(end) if end else None) for event_id, start, end in events
        ]
        return dates.event_timeline(items, duration)

    def ends(self, *events, duration=SIX_HOURS):
        return {
            entry.id: (entry.end, entry.explicit)
            for entry in self.timeline(*events, duration=duration)
        }

    def test_order_by_start_then_by_id(self):
        entries = self.timeline(
            ("walk", "2030-06-16T10:00+03:00", None),
            ("dinner", "2030-06-15T16:00+03:00", None),
            ("brunch", "2030-06-15T16:00+03:00", None),
            ("ceremony", "2030-06-15T11:00+03:00", None),
            # the same moment as the dinner, written at another offset
            ("arrival", "2030-06-15T13:00Z", None),
        )
        self.assertEqual(
            [entry.id for entry in entries], ["ceremony", "arrival", "brunch", "dinner", "walk"]
        )

    def test_an_explicit_end_is_kept(self):
        entry = self.timeline(("dinner", "2030-06-15T16:00+03:00", "2030-06-15T23:30+03:00"))[0]
        self.assertEqual(entry.end, at("2030-06-15T23:30+03:00"))
        self.assertTrue(entry.explicit)
        # even when it overlaps the next event
        ends = self.ends(
            ("ceremony", "2030-06-15T11:00+03:00", "2030-06-15T18:00+03:00"),
            ("dinner", "2030-06-15T16:00+03:00", None),
        )
        self.assertEqual(ends["ceremony"], (at("2030-06-15T18:00+03:00"), True))

    def test_the_default_duration(self):
        entry = self.timeline(("dinner", "2030-06-15T16:00+03:00", None))[0]
        self.assertEqual(entry.end, at("2030-06-15T22:00+03:00"))
        self.assertFalse(entry.explicit)
        ends = self.ends(("dinner", "2030-06-15T16:00+03:00", None), duration=timedelta(hours=2))
        self.assertEqual(ends["dinner"], (at("2030-06-15T18:00+03:00"), False))

    def test_cut_by_the_next_start(self):
        ends = self.ends(
            ("ceremony", "2030-06-15T11:00+03:00", None),
            ("lunch", "2030-06-15T12:00+03:00", None),
            ("dinner", "2030-06-15T20:00+03:00", None),
        )
        self.assertEqual(ends["ceremony"], (at("2030-06-15T12:00+03:00"), False))
        # the default duration is shorter than the gap to the dinner
        self.assertEqual(ends["lunch"], (at("2030-06-15T18:00+03:00"), False))
        self.assertEqual(ends["dinner"], (at("2030-06-16T02:00+03:00"), False))

    def test_the_next_event_starts_strictly_later(self):
        ends = self.ends(
            ("a", "2030-06-15T11:00+03:00", None),
            ("b", "2030-06-15T11:00+03:00", None),
            ("c", "2030-06-15T08:00Z", None),  # 11:00 at +03:00 as well
            ("d", "2030-06-15T13:00+03:00", None),
        )
        for event_id in ("a", "b", "c"):
            self.assertEqual(ends[event_id], (at("2030-06-15T13:00+03:00"), False))
        self.assertEqual(ends["d"], (at("2030-06-15T19:00+03:00"), False))

    def test_an_implicit_end_is_on_the_clock_of_its_start(self):
        entries = self.timeline(
            ("ceremony", "2030-06-15T11:00+03:00", None),
            ("call", "2030-06-15T14:00+05:00", None),  # 12:00 at +03:00
        )
        self.assertEqual(entries[0].end, at("2030-06-15T12:00+03:00"))
        self.assertEqual(entries[0].end.utcoffset(), timedelta(hours=3))
        self.assertEqual(entries[0].end.hour, 12)
        self.assertEqual(entries[1].end.utcoffset(), timedelta(hours=5))

    def test_only_the_given_events_count(self):
        # the caller passes the events a guest sees: a hidden lunch at 12:00
        # must not shorten the registration of somebody who is not invited
        registration = ("registration", "2030-06-15T11:00+03:00", None)
        lunch = ("lunch", "2030-06-15T12:00+03:00", None)
        dinner = ("dinner", "2030-06-15T16:00+03:00", "2030-06-15T23:00+03:00")
        with_lunch = self.ends(registration, lunch, dinner)
        without_lunch = self.ends(registration, dinner)
        self.assertEqual(with_lunch["registration"], (at("2030-06-15T12:00+03:00"), False))
        self.assertEqual(without_lunch["registration"], (at("2030-06-15T16:00+03:00"), False))
        self.assertNotIn("lunch", without_lunch)

    def test_an_end_that_is_not_later_than_the_start(self):
        for end in ("2030-06-15T16:00+03:00", "2030-06-15T12:00+03:00", "2030-06-15T13:00Z"):
            with self.subTest(end=end):
                with self.assertRaises(ValueError):
                    self.timeline(("dinner", "2030-06-15T16:00+03:00", end))

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            self.timeline(("dinner", "2030-06-15T16:00+03:00", None), duration=timedelta(0))
        with self.assertRaises(ValueError):
            dates.event_timeline([("dinner", datetime(2030, 6, 15, 16, 0), None)], SIX_HOURS)
        self.assertEqual(dates.event_timeline([], SIX_HOURS), [])

    def test_the_build_constant_fits(self):
        entry = self.timeline(
            ("dinner", "2030-06-15T16:00+03:00", None), duration=build.ICS_DEFAULT_DURATION
        )[0]
        self.assertEqual(entry.end - entry.start, build.ICS_DEFAULT_DURATION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
