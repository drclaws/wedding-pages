"""Dates in Russian and the end times of events.

Everything is written in the UTC offset of the event itself, that is in the
local time of the place: never in UTC and never in the time zone of the
machine that runs the build.  The names of the months and of the days of the
week come from the tables below, not from the locale of the operating system.

The functions take `datetime` values with a UTC offset (as `parse_date_iso`
reads them from the data); a value without an offset raises `ValueError`.
Pure functions, no input/output.  Standard library only.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, NamedTuple

_DATE_ISO_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2}(?:\.\d{3}|\.\d{6})?)?)"
    r"(Z|z|[+-]\d{2}:\d{2})?\Z"
)


def parse_date_iso(value: str) -> datetime:
    """Parse a date and time from the data; a UTC offset is required.

    Accepts a strict subset of ISO 8601 so that the result does not depend on
    the Python version (3.10's `fromisoformat` is stricter than 3.11's), and
    normalises the `Z` suffix, which `fromisoformat` rejects before 3.11.
    """
    match = _DATE_ISO_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(
            "must be an ISO 8601 date and time with a UTC offset, "
            "e.g. 2030-01-02T18:30:00+03:00"
        )
    if not match.group(3):
        raise ValueError(
            "has no UTC offset; append the offset, e.g. +03:00 (or Z for UTC)"
        )
    offset = match.group(3)
    text = f"{match.group(1)}T{match.group(2)}" + (
        "+00:00" if offset in ("Z", "z") else offset
    )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("is not a valid date and time") from None
    if parsed.utcoffset() is None:  # pragma: no cover - guarded by the regex
        raise ValueError("has no UTC offset")
    return parsed


#: Months in the genitive case, as in "15 июня".
MONTHS_GENITIVE = (
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
#: Days of the week, Monday first (the order of `datetime.weekday()`).
WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def _require_offset(moment: datetime) -> datetime:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("expected a date and time with a UTC offset")
    return moment


def _in_offset_of(moment: datetime, reference: datetime) -> datetime:
    """The same moment on the clock of `reference` (its UTC offset)."""
    return moment.astimezone(timezone(reference.utcoffset()))


def format_time(moment: datetime) -> str:
    """`9:30`, `16:00`: the hour without a leading zero."""
    _require_offset(moment)
    return f"{moment.hour}:{moment.minute:02d}"


def format_day_month(moment: datetime) -> str:
    """`15 июня`."""
    _require_offset(moment)
    return f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]}"


def format_date(moment: datetime) -> str:
    """`15 июня 2030`."""
    return f"{format_day_month(moment)} {moment.year}"


def format_when(start: datetime, end: datetime | None = None) -> str:
    """The time of an event for its card.

    `Суббота, 15 июня 2030, 16:00`; with an end on the same local day
    `…, 16:00–23:30`; with an end on another day `…, 22:00 — 16 июня, 2:00`
    (and the year when that changes too).  The end is shown on the clock of
    the start.
    """
    _require_offset(start)
    weekday = WEEKDAYS[start.weekday()]
    text = f"{weekday[0].upper()}{weekday[1:]}, {format_date(start)}, {format_time(start)}"
    if end is None:
        return text
    _require_offset(end)
    if end <= start:
        raise ValueError("the end must be later than the start")
    end = _in_offset_of(end, start)
    if end.date() == start.date():
        return f"{text}–{format_time(end)}"
    day = format_day_month(end) if end.year == start.year else format_date(end)
    return f"{text} — {day}, {format_time(end)}"


def same_local_day(moments: Iterable[datetime]) -> bool:
    """True when all the moments fall on one day, each on its own clock."""
    return len({_require_offset(moment).date() for moment in moments}) <= 1


def format_tab(start: datetime, same_day: bool) -> str:
    """The label of an event tab: `11:00` when all the events of the set are
    on one day (see `same_local_day`), else `15 июня, 11:00`."""
    if same_day:
        return format_time(start)
    return f"{format_day_month(start)}, {format_time(start)}"


#: How long an event without an end lasts.
DEFAULT_EVENT_DURATION = timedelta(hours=6)


class TimelineEntry(NamedTuple):
    """An event of `event_timeline` with the end it effectively has."""

    id: str
    start: datetime
    #: The given end, or the start plus the default duration.
    end: datetime
    #: True when the end was given.
    explicit: bool


def event_timeline(
    events: Iterable[tuple[str, datetime, datetime | None]],
    default_duration: timedelta = DEFAULT_EVENT_DURATION,
) -> list[TimelineEntry]:
    """Events `(id, start, end or None)` in order, each with an end.

    The order is by the moment of the start, then by id.  An event without an
    end lasts `default_duration`; that end is given on the clock of its start.
    The end of an event never depends on the other events: it is the same for
    everybody who sees the event (and in its calendar file).

    An end that is not later than its start raises `ValueError`.
    """
    if default_duration <= timedelta(0):
        raise ValueError("the default duration must be positive")
    ordered: list[tuple[datetime, str, datetime | None]] = []
    for event_id, start, end in events:
        _require_offset(start)
        if end is not None:
            _require_offset(end)
            if end <= start:
                raise ValueError("the end of an event must be later than its start")
        ordered.append((start, event_id, end))
    ordered.sort(key=lambda item: (item[0], item[1]))
    timeline: list[TimelineEntry] = []
    for start, event_id, end in ordered:
        if end is not None:
            timeline.append(TimelineEntry(event_id, start, end, True))
            continue
        # added in UTC: the duration is real time whatever the clock of start
        effective = _in_offset_of(start.astimezone(timezone.utc) + default_duration, start)
        timeline.append(TimelineEntry(event_id, start, effective, False))
    return timeline
