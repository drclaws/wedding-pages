"""Tests for the checks of the data format 2 (`tools/_schema.py`).

Every scenario changes the fictional fixture data (`tests/fixtures_v2.py`)
in one place and looks at the messages: the path to the field, ids only by
the id rule, no data values, and in the invitations no key that is not a
field name or an id declared in `site.json`.
"""

from __future__ import annotations

import unittest

from tests import fixtures_v2 as F
from tests import support
from tools import _data, _media, _page, _schema
from tools._media import MediaInfo

#: Personal values of the fixture: none of them may reach a message.
PRIVATE = tuple(
    [invitation["greeting"] for invitation in F.INVITATIONS]
    + [invitation["token"] for invitation in F.INVITATIONS]
    + [
        override["note"]
        for invitation in F.INVITATIONS
        for part in ("events", "sections")
        for override in invitation.get(part, {}).values()
        if "note" in override
    ]
)
#: Keys an invitation might hold that are somebody's name.
NAME_KEYS = ("IvanPetrov", "ivan-petrov", "ivan")
#: A value that stands for personal data inside the data.
SECRET = "Секрет-Метка"

GUEST_1 = "invitation #1"
GUEST_2 = "invitation #2"


def run(mutate=None, site=None, invitations=None) -> F.Collector:
    site = F.site() if site is None else site
    invitations = F.invitations() if invitations is None else invitations
    if mutate is not None:
        mutate(site, invitations)
    report = F.Collector()
    report.index = _schema.check_data(site, invitations, report)
    return report


class SchemaTestCase(unittest.TestCase):
    def assertMessages(self, report, errors=(), warnings=()):
        self.assertEqual(report.errors, list(errors))
        self.assertEqual(report.warnings, list(warnings))

    def assertNoPrivateData(self, report, *extra):
        for message in report.messages:
            for value in (*PRIVATE, *extra):
                self.assertNotIn(value, message)

    def assertOneError(self, report, expected):
        self.assertEqual(report.errors, [expected])
        self.assertNoPrivateData(report)


# --------------------------------------------------------------------------


class FixtureTests(SchemaTestCase):
    def test_the_main_set_is_valid(self):
        report = run()
        self.assertMessages(report)
        self.assertTrue(report.index.ok)

    def test_ok_counts_the_errors_of_both_documents(self):
        report = run(lambda s, i: i[1].update(form="tu"))
        self.assertEqual((report.index.errors, report.index.invitation_errors), (0, 1))
        self.assertFalse(report.index.ok)
        report = run(lambda s, i: i.append("x"))
        self.assertEqual(report.index.invitation_errors, 1)
        report = run(lambda s, i: s.update(coupleNames=""))
        self.assertEqual((report.index.errors, report.index.invitation_errors), (1, 0))
        self.assertFalse(report.index.ok)
        report = run(invitations={})
        self.assertFalse(report.index.ok)

    def test_the_set_without_places_is_valid(self):
        report = run(site=F.pending_site(), invitations=F.pending_invitations())
        self.assertMessages(report)

    def test_the_report_of_the_build_is_accepted(self):
        report = support.build.Report()
        site = F.site()
        site["media"]["story-1"].pop("alt")
        _schema.check_data(site, F.invitations(), report)
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.warnings), 1)

    def test_type_lists_are_the_contract(self):
        self.assertEqual(_schema.SECTION_TYPES, ("cover", "custom"))
        self.assertEqual(
            _schema.WIDGET_TYPES, ("text", "date", "events", "location", "schedule", "media")
        )
        self.assertEqual(_schema.MEDIA_TYPES, ("image", "video"))
        self.assertEqual(_schema.VIDEO_EXTENSIONS, frozenset({".mp4"}))


#: Data in the old format (fictional): the build reads none of it.
OLD_SITE = {
    "coupleNames": "Алиса и Боб",
    "dateISO": "2030-06-01T16:00:00+03:00",
    "dateText": "1 июня 2030 года",
    "rsvpDeadline": "1 мая 2030 года",
    "mediaDir": "m3d1a-f1xtur3-dir",
    "outOfTownText": "Из Энска ходит автобус.",
    "venue": {"ready": True, "name": "Усадьба в Энске", "address": "Энск, Вымышленная улица, 1"},
    "schedule": [{"time": "16:00", "title": "Сбор гостей"}],
}
OLD_INVITATIONS = [
    {"token": "EveToken-0123456789abcdefg", "greeting": "Дорогая Ева!", "ty": True, "vy": False,
     "plusOne": False, "outOfTown": False, "note": "Личная приписка для Евы."},
    {"token": "KarlKlaraToken-zyxwvutsrq98", "greeting": "Дорогие Карл и Клара!", "ty": False,
     "vy": True, "plusOne": True, "outOfTown": False},
    {"token": "GoshaToken-qwertyuiop12345", "greeting": "Дорогой Гоша!", "ty": True,
     "vy": False, "plusOne": False, "outOfTown": True, "travelNote": "Встретим Гошу."},
]  # fmt: skip


class VersionTests(SchemaTestCase):
    def test_old_data_gives_one_message_per_file(self):
        report = run(site=OLD_SITE, invitations=OLD_INVITATIONS)
        self.assertEqual(len(report.errors), 2)
        self.assertTrue(report.errors[0].startswith("site.json: is in the old data format (it has "))
        self.assertIn("'dateISO'", report.errors[0])
        self.assertIn("'venue'", report.errors[0])
        self.assertIn('add "schemaVersion": 2', report.errors[0])
        self.assertTrue(
            report.errors[1].startswith("invitations.json: all 3 invitations are in the old data format")
        )
        self.assertIn("'plusOne'", report.errors[1])
        private = [
            value
            for invitation in OLD_INVITATIONS
            for key, value in invitation.items()
            if isinstance(value, str)
        ]
        for value in (*private, OLD_SITE["coupleNames"], OLD_SITE["venue"]["name"]):
            for message in report.messages:
                self.assertNotIn(value, message)

    def test_another_version(self):
        report = run(lambda s, i: s.update(schemaVersion=3))
        self.assertOneError(
            report,
            "site.json: field 'schemaVersion' is 3 - not supported: this version of the "
            "site reads version 2 only",
        )

    def test_a_version_that_is_not_a_number_is_not_quoted(self):
        report = run(lambda s, i: s.update(schemaVersion=SECRET))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("is a string - not supported", report.errors[0])
        self.assertNoPrivateData(report, SECRET)

    def test_missing_version(self):
        report = run(lambda s, i: s.pop("schemaVersion"))
        self.assertOneError(report, "site.json: field 'schemaVersion' is required (use 2)")

    def test_without_the_version_references_of_invitations_are_not_checked(self):
        def change(site, invitations):
            site["schemaVersion"] = 1
            invitations[0]["sections"]["travle"] = {"visible": True}
            invitations[1]["form"] = "tu"

        report = run(change)
        self.assertEqual(len(report.errors), 2)
        self.assertIn("schemaVersion", report.errors[0])
        self.assertEqual(report.errors[1], f"{GUEST_2}: field 'form' must be one of: ty, vy")

    def test_top_level_types(self):
        report = run(site=[], invitations={})
        self.assertEqual(
            report.errors,
            [
                "site.json: the top-level value must be an object, got an array",
                "invitations.json: the top-level value must be an array of invitations, "
                "got an object",
            ],
        )

    def test_no_invitations_is_a_warning(self):
        report = run(invitations=[])
        self.assertMessages(report, warnings=["invitations.json: contains no invitations"])


class FieldTests(SchemaTestCase):
    def test_unknown_field_with_a_hint(self):
        report = run(lambda s, i: s["sections"][5].update(visibel=False))
        self.assertOneError(report, "site.json: unknown field 'sections[5].visibel' (did you mean 'visible'?)")

    def test_unknown_top_level_field(self):
        report = run(lambda s, i: s.update(coupleName="x"))
        self.assertOneError(report, "site.json: unknown field 'coupleName' (did you mean 'coupleNames'?)")

    def test_field_of_the_old_format_says_where_it_went(self):
        report = run(lambda s, i: s.update(venue={}))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].startswith("site.json: field 'venue' is no longer supported: "))

    def test_comment_keys_are_unknown_fields(self):
        report = run(lambda s, i: s.update(_comment=SECRET))
        self.assertOneError(report, "site.json: unknown field '_comment'")

    def test_null_is_never_accepted(self):
        cases = {
            "coupleNames": lambda s: s.update(coupleNames=None),
            "rsvpDeadline": lambda s: s.update(rsvpDeadline=None),
            "locations.hotel.address": lambda s: s["locations"]["hotel"].update(address=None),
            "events.dinner.end": lambda s: s["events"]["dinner"].update(end=None),
            "sections[4].widgets[0].events": lambda s: s["sections"][4]["widgets"][0].update(events=None),
        }
        for path, change in cases.items():
            with self.subTest(path):
                report = run(lambda s, i: change(s))
                self.assertEqual(len(report.errors), 1)
                self.assertIn(f"field '{path}' must be", report.errors[0])
                self.assertTrue(report.errors[0].endswith("got null"))

    def test_type_errors_name_the_type_only(self):
        report = run(lambda s, i: s["sections"][3].update(titleHidden=SECRET))
        self.assertOneError(
            report,
            "site.json: field 'sections[3].titleHidden' must be a boolean (true or false), "
            "got a string",
        )
        self.assertNoPrivateData(report, SECRET)

    def test_single_line(self):
        report = run(lambda s, i: s.update(coupleNames="Алиса\nБоб"))
        self.assertOneError(report, "site.json: field 'coupleNames' must be a single line (no line breaks)")

    def test_required_and_empty(self):
        report = run(lambda s, i: s.pop("coupleNames"))
        self.assertOneError(report, "site.json: field 'coupleNames' is required")
        report = run(lambda s, i: s.update(coupleNames="  "))
        self.assertOneError(report, "site.json: field 'coupleNames' must not be empty")

    def test_media_dir(self):
        report = run(lambda s, i: s.update(mediaDir="short"))
        self.assertOneError(
            report,
            "site.json: field 'mediaDir' must be 16 to 200 characters long and may only "
            "contain A-Z, a-z, 0-9, '_' and '-'",
        )

    def test_enumerations_do_not_quote_the_value(self):
        report = run(lambda s, i: s["sections"][4].update(width=SECRET))
        self.assertOneError(report, "site.json: field 'sections[4].width' must be one of: narrow, wide")
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(variant="big"))
        self.assertOneError(
            report, "site.json: field 'sections[1].widgets[0].variant' must be one of: body, lead, signature"
        )


class IdentifierTests(SchemaTestCase):
    def test_section_id_rule(self):
        report = run(lambda s, i: s["sections"][2].update(id="Personal Note"))
        self.assertOneError(
            report,
            "site.json: field 'sections[2].id' must be an identifier: 1-32 characters, "
            "lowercase a-z, 0-9 and single hyphens, starting with a letter",
        )

    def test_repeated_section_id(self):
        report = run(lambda s, i: s["sections"][3].update(id="invite"))
        self.assertOneError(
            report, "site.json: field 'sections[3].id' repeats 'invite' (already used by sections[1])"
        )

    def test_repeated_widget_id_within_a_section(self):
        def change(site, invitations):
            site["sections"][5]["widgets"][0]["id"] = "hotel-booked"

        report = run(change)
        self.assertOneError(
            report, "site.json: field 'sections[5].widgets[2].id' repeats 'hotel-booked' within the section"
        )

    def test_same_widget_id_in_two_sections_is_fine(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(id="hotel-booked"))
        self.assertMessages(report)

    def test_widget_id_rule(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(id="Lead_Text"))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("field 'sections[1].widgets[0].id' must be an identifier", report.errors[0])

    def test_registry_key_that_is_not_an_id(self):
        def change(site, invitations):
            site["locations"][SECRET] = site["locations"].pop("hotel")

        report = run(change)
        self.assertIn(
            "site.json: field 'locations.<non-identifier key>' is not an identifier: an id has "
            "1-32 characters, lowercase a-z, 0-9 and single hyphens, starting with a letter",
            report.errors,
        )
        self.assertNoPrivateData(report, SECRET)

    def test_long_id(self):
        report = run(lambda s, i: s["sections"][2].update(id="a" * 33))
        self.assertIn("must be an identifier", report.errors[0])


class SectionTests(SchemaTestCase):
    def test_unknown_widget_type(self):
        report = run(lambda s, i: s["sections"][4]["widgets"][0].update(type="evnts"))
        self.assertOneError(
            report,
            "site.json: field 'sections[4].widgets[0].type' names an unknown widget type "
            "'evnts' (did you mean 'events'?); known: text, date, events, location, schedule, media",
        )

    def test_unknown_widget_type_that_is_not_an_id_is_not_quoted(self):
        report = run(lambda s, i: s["sections"][4]["widgets"][0].update(type=SECRET))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("names an unknown widget type; known:", report.errors[0])
        self.assertNoPrivateData(report, SECRET)

    def test_widget_without_type(self):
        report = run(lambda s, i: s["sections"][4]["widgets"][0].pop("type"))
        self.assertOneError(
            report,
            "site.json: field 'sections[4].widgets[0].type' is required "
            "(text, date, events, location, schedule, media)",
        )

    def test_unknown_section_type(self):
        report = run(lambda s, i: s["sections"][2].update(type="gallery"))
        self.assertOneError(report, "site.json: field 'sections[2].type' must be one of: cover, custom")

    def test_cover_must_be_first(self):
        report = run(lambda s, i: s["sections"].insert(0, s["sections"].pop(1)))
        self.assertOneError(
            report, "site.json: field 'sections[1].type' is 'cover': the cover must be the first section"
        )

    def test_one_cover_only(self):
        report = run(lambda s, i: s["sections"].append({"id": "cover-2", "type": "cover"}))
        self.assertOneError(
            report, "site.json: field 'sections[8].type' is 'cover': there may be only one cover"
        )

    def test_no_cover_is_a_warning(self):
        def change(site, invitations):
            site["sections"].pop(0)
            site["media"].pop("cover")  # else: a photo without a description

        report = run(change)
        self.assertMessages(
            report,
            warnings=["site.json: field 'sections' has no 'cover' section: the page will have no main heading"],
        )

    def test_cover_fields(self):
        report = run(lambda s, i: s["sections"][0].update(title="Обложка"))
        self.assertOneError(report, "site.json: unknown field 'sections[0].title'")

    def test_background_is_only_for_the_cover(self):
        report = run(lambda s, i: s["sections"][1].update(background="cover"))
        self.assertOneError(report, "site.json: unknown field 'sections[1].background'")


class CoverPhotoTests(SchemaTestCase):
    """`background` and `backgroundFocus` of the cover."""

    @staticmethod
    def cover(**fields):
        def change(site, invitations):
            site["sections"][0].update(fields)

        return change

    def test_background_refers_to_a_media_item(self):
        report = run(self.cover(background="covr"))
        self.assertOneError(
            report,
            "site.json: field 'sections[0].background' refers to an unknown media item 'covr' "
            "(did you mean 'cover'?); known: cover, registry-1, venue-1, venue-2, venue-3, "
            "venue-4, directions, story-1, …",
        )

    def test_background_is_an_id(self):
        report = run(self.cover(background=["cover"]))
        self.assertOneError(
            report, "site.json: field 'sections[0].background' must be the id of a media item, got an array"
        )
        report = run(self.cover(background=""))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("'sections[0].background' must be the id of a media item", report.errors[0])

    def test_background_must_be_an_image(self):
        report = run(self.cover(background="walk"))
        self.assertOneError(
            report, "site.json: field 'sections[0].background' must be an image, but 'walk' is a video"
        )

    def test_every_focus_is_accepted(self):
        for focus in _schema.COVER_FOCUSES:
            with self.subTest(focus=focus):
                self.assertMessages(run(self.cover(backgroundFocus=focus)))

    def test_focus_is_one_of_the_list(self):
        for value in ("middle", "Top", "top left", 1, None):
            with self.subTest(value=value):
                report = run(self.cover(backgroundFocus=value))
                self.assertOneError(
                    report,
                    "site.json: field 'sections[0].backgroundFocus' must be one of: center, top, "
                    "bottom, left, right, top-left, top-right, bottom-left, bottom-right",
                )

    def test_focus_without_a_photo_is_a_warning(self):
        def change(site, invitations):
            site["sections"][0].pop("background")
            site["media"].pop("cover")

        report = run(change)
        self.assertMessages(
            report,
            warnings=["site.json: field 'sections[0].backgroundFocus' has no effect without 'background'"],
        )

    def test_the_photo_needs_no_description(self):
        # decoration: the page gives it an empty alt, no warning about it
        self.assertNotIn("alt", F.SITE["media"]["cover"])
        self.assertMessages(run())
        # shown as a tile as well: the tile needs a description
        report = run(lambda s, i: s["locations"]["manor"]["photos"].append("cover"))
        self.assertMessages(
            report,
            warnings=[
                "site.json: field 'media.cover.alt' is not set: the tile gets a neutral label "
                "instead of a description"
            ],
        )
        report = run(lambda s, i: s["sections"][6]["widgets"][0]["items"].append("cover"))
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("'media.cover.alt' is not set", report.warnings[0])

    def test_the_guest_cannot_change_the_photo(self):
        report = run(lambda s, i: i[0].setdefault("sections", {}).update(cover={"background": "venue-1"}))
        self.assertOneError(
            report,
            f"{GUEST_1}: field 'sections.cover' cannot be set: the cover is the same for everybody",
        )

    def test_title_is_required(self):
        report = run(lambda s, i: s["sections"][3].pop("title"))
        self.assertOneError(report, "site.json: field 'sections[3].title' is required")

    def test_widgets_are_required_but_may_be_empty(self):
        report = run(lambda s, i: s["sections"][3].pop("widgets"))
        self.assertOneError(report, "site.json: field 'sections[3].widgets' is required")
        report = run(lambda s, i: s["sections"][3].update(widgets=[]))
        self.assertEqual(report.errors, [])

    def test_sections_must_not_be_empty(self):
        report = run(lambda s, i: s.update(sections=[]))
        self.assertEqual(report.errors[0], "site.json: field 'sections' must list at least one section")

    def test_widget_fields_by_type(self):
        report = run(lambda s, i: s["sections"][3]["widgets"][0].update(items=["story-1"]))
        self.assertOneError(report, "site.json: unknown field 'sections[3].widgets[0].items'")


class ReferenceTests(SchemaTestCase):
    def test_unknown_location(self):
        report = run(lambda s, i: s["sections"][5]["widgets"][1].update(location="hotle"))
        self.assertOneError(
            report,
            "site.json: field 'sections[5].widgets[1].location' refers to an unknown location "
            "'hotle' (did you mean 'hotel'?); known: registry, manor, terrace, hotel",
        )

    def test_the_list_of_known_ids_is_short(self):
        def change(site, invitations):
            site["sections"][6]["widgets"][0]["items"] = ["story-9"]

        report = run(change)
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].endswith(", …"))
        self.assertEqual(report.errors[0].count(","), _schema.MAX_LISTED)

    def test_a_reference_that_is_not_an_id_is_not_quoted(self):
        report = run(lambda s, i: s["sections"][5]["widgets"][1].update(location=SECRET))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("must be the id of a location: 1-32 characters", report.errors[0])
        self.assertNoPrivateData(report, SECRET)

    def test_a_reference_must_be_a_string(self):
        report = run(lambda s, i: s["sections"][5]["widgets"][1].update(location=3))
        self.assertOneError(
            report, "site.json: field 'sections[5].widgets[1].location' must be the id of a location, got a number"
        )

    def test_directions_must_be_an_image(self):
        report = run(lambda s, i: s["locations"]["manor"].update(directions="proposal"))
        self.assertIn(
            "site.json: field 'locations.manor.directions' must be an image, but 'proposal' is a video",
            report.errors,
        )

    def test_repeat_in_a_list(self):
        report = run(lambda s, i: s["sections"][4]["widgets"][0].update(events=["dinner", "ceremony", "dinner"]))
        self.assertOneError(report, "site.json: field 'sections[4].widgets[0].events[2]' repeats 'dinner'")

    def test_empty_lists(self):
        report = run(lambda s, i: s["sections"][4]["widgets"][0].update(events=[]))
        self.assertOneError(report, "site.json: field 'sections[4].widgets[0].events' must list at least one event")
        report = run(lambda s, i: s["sections"][6]["widgets"][0].update(items=[]))
        self.assertOneError(report, "site.json: field 'sections[6].widgets[0].items' must list at least one media item")

    def test_single_layout_takes_one_item(self):
        report = run(lambda s, i: s["sections"][6]["widgets"][0].update(layout="single"))
        self.assertOneError(
            report,
            "site.json: field 'sections[6].widgets[0].items' must list exactly one media item "
            "for the 'single' layout",
        )

    def test_date_schedule_and_photo_references(self):
        cases = {
            "sections[3].widgets[0].event": lambda s: s["sections"][3]["widgets"][0].update(event="party"),
            "events.dinner.schedule": lambda s: s["events"]["dinner"].update(schedule="evening"),
            "locations.manor.photos[1]": lambda s: s["locations"]["manor"]["photos"].__setitem__(1, "venue-9"),
        }
        for path, change in cases.items():
            with self.subTest(path):
                report = run(lambda s, i: change(s))
                self.assertEqual(len(report.errors), 1)
                self.assertTrue(report.errors[0].startswith(f"site.json: field '{path}' refers to an unknown"))

    def test_schedule_widget_needs_a_schedule(self):
        def change(site, invitations):
            site["sections"][4]["widgets"].append({"type": "schedule"})

        report = run(change)
        self.assertOneError(report, "site.json: field 'sections[4].widgets[1].schedule' is required")

    def test_empty_registry_list(self):
        def change(site, invitations):
            site["schedules"] = {}

        report = run(change)
        self.assertOneError(
            report,
            "site.json: field 'events.dinner.schedule' refers to an unknown schedule 'dinner'; "
            "there are no schedules",
        )


class EventTests(SchemaTestCase):
    def test_end_before_start(self):
        report = run(lambda s, i: s["events"]["ceremony"].update(end="2030-06-15T10:00:00+03:00"))
        self.assertOneError(report, "site.json: field 'events.ceremony.end' must be later than 'start'")

    NO_END = (
        "site.json: field 'events.{}.end' is not set: the calendar and the past/now marks "
        "use start + 6 h"
    )

    def test_an_event_without_an_end_is_a_warning(self):
        for end in (None, "", "  "):
            with self.subTest(end=end):
                def change(site, invitations):
                    if end is None:
                        site["events"]["ceremony"].pop("end")
                    else:
                        site["events"]["ceremony"]["end"] = end

                self.assertMessages(run(change), warnings=[self.NO_END.format("ceremony")])

    def test_an_end_of_a_wrong_type_is_an_error_only(self):
        report = run(lambda s, i: s["events"]["ceremony"].update(end=None))
        self.assertOneError(report, "site.json: field 'events.ceremony.end' must be a string, got null")
        self.assertEqual(report.warnings, [])

    def test_show_end(self):
        # a boolean, false by default; with an end it only changes the page
        self.assertMessages(run(lambda s, i: s["events"]["ceremony"].update(showEnd=True)))
        self.assertMessages(run(lambda s, i: s["events"]["dinner"].update(showEnd=False)))
        report = run(lambda s, i: s["events"]["ceremony"].update(showEnd="yes"))
        self.assertOneError(report, "site.json: field 'events.ceremony.showEnd' must be a boolean (true or false), got a string")

        def without_end(site, invitations):
            site["events"]["dinner"].pop("end")

        self.assertMessages(
            run(without_end),
            warnings=[
                self.NO_END.format("dinner"),
                "site.json: field 'events.dinner.showEnd' has no effect without 'end'",
            ],
        )

    def test_show_end_is_spelt_out(self):
        report = run(lambda s, i: s["events"]["ceremony"].update(showend=True))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("did you mean 'showEnd'?", report.errors[0])

    def test_offset_is_required(self):
        report = run(lambda s, i: s["events"]["brunch"].update(start="2030-06-16T12:00:00"))
        self.assertOneError(
            report,
            "site.json: field 'events.brunch.start' has no UTC offset; append the offset, "
            "e.g. +03:00 (or Z for UTC)",
        )

    def test_not_a_date(self):
        report = run(lambda s, i: s["events"]["brunch"].update(start=SECRET))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("must be an ISO 8601 date and time", report.errors[0])
        self.assertNoPrivateData(report, SECRET)

    def test_main_event_hidden_by_default(self):
        report = run(lambda s, i: s["events"]["dinner"].update(visible=False))
        self.assertEqual(
            report.errors[0],
            "site.json: field 'mainEvent' refers to 'dinner', which is hidden by default "
            "('visible': false); the main event must be visible to everybody who has no "
            "'primaryEvent'",
        )
        self.assertEqual(
            report.errors[1:],
            ["invitation #7: field 'events' hides every event; at least one must stay visible"],
        )

    def test_unknown_main_event(self):
        report = run(lambda s, i: s.update(mainEvent="diner"))
        self.assertEqual(
            report.errors[0],
            "site.json: field 'mainEvent' refers to an unknown event 'diner' (did you mean "
            "'dinner'?); known: ceremony, dinner, brunch",
        )

    def test_different_offsets_are_a_warning(self):
        report = run(lambda s, i: s["events"]["brunch"].update(start="2030-06-16T12:00:00+04:00"))
        self.assertMessages(
            report,
            warnings=[
                "site.json: field 'events' has events with different UTC offsets (ceremony, "
                "dinner, brunch); check the offsets, especially around a change to or from "
                "summer time"
            ],
        )

    def test_location_is_required(self):
        report = run(lambda s, i: s["events"]["dinner"].pop("location"))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].startswith("site.json: field 'events.dinner.location' is required"))

    def test_events_must_not_be_empty(self):
        report = run(lambda s, i: s.update(events={}))
        self.assertEqual(report.errors[0], "site.json: field 'events' must describe at least one event")

    def test_title_has_no_forms(self):
        report = run(lambda s, i: s["events"]["dinner"].update(title={"ty": "a", "vy": "b"}))
        self.assertOneError(report, "site.json: field 'events.dinner.title' must be a string, got an object")


class LocationTests(SchemaTestCase):
    def test_name_is_required_when_ready(self):
        report = run(lambda s, i: s["locations"]["hotel"].pop("name"))
        self.assertOneError(
            report,
            "site.json: field 'locations.hotel.name' is required unless the place is not "
            "announced yet ('ready': false)",
        )

    def test_name_is_not_needed_when_not_ready(self):
        report = run(lambda s, i: s["locations"]["hotel"].update(ready=False, name=""))
        self.assertEqual(report.errors, [])

    def test_coordinates(self):
        report = run(lambda s, i: s["locations"]["hotel"]["geo"].update(lat=91))
        self.assertOneError(report, "site.json: field 'locations.hotel.geo.lat' is outside [-90, 90]")

    def test_map_ids_are_not_links(self):
        report = run(lambda s, i: s["locations"]["hotel"].update(maps={"yandexOrgId": "https://x"}))
        self.assertOneError(
            report,
            "site.json: field 'locations.hotel.maps.yandexOrgId' may only contain digits "
            "(use the identifier, not a link)",
        )

    def maps_report(self, **maps):
        return run(lambda s, i: s["locations"]["hotel"].update(maps=maps))

    def test_every_map_field_is_accepted(self):
        report = self.maps_report(
            googlePlaceId="EXAMPLE_place-id",
            googleQuery="Гостевой вход, Энск",
            yandexGeoId="1234567890",
            yandexQuery="Гостевой вход, Энск",
            applePlaceId="IEXAMPLE0000001",
            appleQuery="Гостевой вход, Энск",
        )
        self.assertEqual(report.errors, [])

    def test_new_map_ids_have_a_format(self):
        cases = {
            "yandexGeoId": ("12a", "may only contain digits"),
            "applePlaceId": ("I-EXAMPLE", "may only contain A-Z, a-z and 0-9"),
        }
        for key, (value, rule) in cases.items():
            with self.subTest(key=key):
                self.assertOneError(
                    self.maps_report(**{key: value}),
                    f"site.json: field 'locations.hotel.maps.{key}' {rule} "
                    "(use the identifier, not a link)",
                )
        link = "https://maps.apple.com/place?place-id=IEXAMPLE0000001"
        self.assertEqual(len(self.maps_report(applePlaceId=link).errors), 1)

    def test_yandex_org_and_geo_ids_exclude_each_other(self):
        self.assertOneError(
            self.maps_report(yandexOrgId="1000000001", yandexGeoId="1234567890"),
            "site.json: field 'locations.hotel.maps' has both 'yandexOrgId' and "
            "'yandexGeoId': set either 'yandexOrgId' or 'yandexGeoId'",
        )
        self.assertEqual(self.maps_report(yandexOrgId="1000000001", yandexGeoId="").errors, [])

    def test_bad_map_queries(self):
        cases = {
            "": "must not be empty (remove the field instead)",
            "   ": "must not be empty (remove the field instead)",
            "Вход\nЭнск": "must be a single line (no line breaks)",
            "Вход\rЭнск": "must be a single line (no line breaks)",
            "Вход\tЭнск": "must not contain control characters",
            "Вход\x00": "must not contain control characters",
            "Вход\x7f": "must not contain control characters",
            "я" * 201: "is longer than 200 characters",
        }
        for key in ("googleQuery", "yandexQuery", "appleQuery"):
            for value, problem in cases.items():
                with self.subTest(key=key, value=value[:12]):
                    self.assertOneError(
                        self.maps_report(**{key: value}),
                        f"site.json: field 'locations.hotel.maps.{key}' {problem}",
                    )
            with self.subTest(key=key, value="200 characters"):
                self.assertEqual(self.maps_report(**{key: "я" * 200}).errors, [])
            with self.subTest(key=key, value="not a string"):
                self.assertOneError(
                    self.maps_report(**{key: 5}),
                    f"site.json: field 'locations.hotel.maps.{key}' must be a string, got a number",
                )

    def test_map_queries_are_not_quoted_in_messages(self):
        report = self.maps_report(googleQuery="Секретный вход\nЭнск")
        self.assertTrue(report.errors)
        self.assertNotIn("Секретный", " ".join(report.errors))

    def test_unknown_map_fields(self):
        self.assertOneError(
            self.maps_report(yandexGeoID="1"),
            "site.json: unknown field 'locations.hotel.maps.yandexGeoID' "
            "(did you mean 'yandexGeoId'?)",
        )
        self.assertOneError(
            self.maps_report(appleQueri="Энск"),
            "site.json: unknown field 'locations.hotel.maps.appleQueri' "
            "(did you mean 'appleQuery'?)",
        )
        self.assertOneError(
            self.maps_report(query="Энск"),
            "site.json: field 'locations.hotel.maps.query' is not supported: a text query "
            "belongs to one map service, use 'googleQuery', 'yandexQuery' or 'appleQuery'",
        )

    def test_old_directions_field(self):
        report = run(lambda s, i: s["locations"]["hotel"].update(directionsImage="x.png"))
        self.assertOneError(
            report,
            "site.json: field 'locations.hotel.directionsImage' is no longer supported: use "
            "'directions' with the id of a media item",
        )


class ScheduleTests(SchemaTestCase):
    def test_empty_programme_is_a_warning(self):
        report = run(lambda s, i: s["schedules"]["dinner"].clear())
        self.assertIn(
            "site.json: field 'schedules.dinner' is empty (the programme will not be shown)",
            report.warnings,
        )
        self.assertEqual(report.errors, [])

    def test_item_title_is_required(self):
        report = run(lambda s, i: s["schedules"]["dinner"][2].pop("title"))
        self.assertOneError(report, "site.json: field 'schedules.dinner[2].title' is required")


class RegistryTextTests(SchemaTestCase):
    """The descriptions of the events and the places and the texts of a
    programme are texts: forms of address, named texts and placeholders."""

    def test_forms_and_named_texts_are_accepted(self):
        def mutate(site, _invitations):
            site["texts"]["wait"] = {"ty": "Подожди у входа.", "vy": "Подождите у входа."}
            site["events"]["ceremony"]["description"] = "Зал небольшой. {text:wait}"
            site["locations"]["manor"]["description"] = {
                "ty": "Тебя встретят, {greeting}",
                "vy": "Вас встретят, {greeting}",
            }
            site["schedules"]["dinner"][0]["title"] = {"ty": "Ждём тебя", "vy": "Ждём вас"}
            site["schedules"]["dinner"][0]["text"] = "Сбор к {eventTime}."

        self.assertMessages(run(mutate))

    def test_unknown_placeholder_in_an_event_description(self):
        report = run(lambda s, i: s["events"]["ceremony"].update(description="Ждём {coupleName}."))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(
            report.errors[0].startswith(
                "site.json: field 'events.ceremony.description' has an unknown placeholder "
                "at character 6 (did you mean {coupleNames}?)"
            ),
            report.errors[0],
        )

    def test_unknown_named_text_in_a_place_description(self):
        report = run(lambda s, i: s["locations"]["manor"].update(description="{text:nowhere}"))
        self.assertOneError(
            report,
            "site.json: field 'locations.manor.description' refers to an unknown text "
            "'nowhere' at character 1; known: announce, invite",
        )

    def test_a_programme_title_stays_one_line(self):
        report = run(lambda s, i: s["schedules"]["dinner"][0].update(title="Сбор\nгостей"))
        self.assertOneError(
            report,
            "site.json: field 'schedules.dinner[0].title' must be a single line (no line breaks)",
        )

    def test_an_event_title_is_not_a_text(self):
        """It goes into the calendar file, the same for everybody."""
        report = run(lambda s, i: s["events"]["ceremony"].update(title={"ty": "Роспись", "vy": "Роспись"}))
        self.assertOneError(
            report, "site.json: field 'events.ceremony.title' must be a string, got an object"
        )


class MediaDataTests(SchemaTestCase):
    def test_video_must_be_mp4(self):
        report = run(lambda s, i: s["media"]["proposal"].update(file="proposal.webm"))
        self.assertOneError(
            report,
            "site.json: field 'media.proposal.file' must be an .mp4 file (H.264 video and AAC "
            "sound): other video formats do not play in every browser",
        )

    def test_image_must_be_an_image(self):
        report = run(lambda s, i: s["media"]["story-1"].update(file="story.mp4"))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].startswith("site.json: field 'media.story-1.file' must be an image (allowed: .avif"))

    def test_video_needs_a_poster(self):
        report = run(lambda s, i: s["media"]["proposal"].pop("poster"))
        self.assertOneError(report, "site.json: field 'media.proposal.poster' is required")

    def test_image_has_no_poster(self):
        report = run(lambda s, i: s["media"]["story-1"].update(poster="p.png"))
        self.assertOneError(
            report, "site.json: field 'media.story-1.poster' is not allowed: only a video has a poster"
        )

    def test_a_tile_has_no_caption(self):
        for media_id in ("story-1", "proposal"):
            with self.subTest(media_id):
                report = run(lambda s, i: s["media"][media_id].update(caption="Энск, 2024"))
                self.assertOneError(
                    report,
                    f"site.json: field 'media.{media_id}.caption' is not supported: a tile "
                    "shows no caption; describe the picture in 'alt'",
                )

    def test_size_is_a_pair_of_positive_numbers(self):
        report = run(lambda s, i: s["media"]["proposal"].pop("height"))
        self.assertOneError(
            report, "site.json: field 'media.proposal.width' is set without 'height'; set both or neither"
        )
        report = run(lambda s, i: s["media"]["proposal"].update(width=0))
        self.assertOneError(
            report, "site.json: field 'media.proposal.width' must be a positive whole number, got a number"
        )

    def test_missing_alt_is_a_warning(self):
        report = run(lambda s, i: s["media"]["story-1"].pop("alt"))
        self.assertMessages(
            report,
            warnings=[
                "site.json: field 'media.story-1.alt' is not set: the tile gets a neutral "
                "label instead of a description"
            ],
        )

    def test_alt_is_one_line(self):
        report = run(lambda s, i: s["media"]["story-1"].update(alt="a\nb"))
        self.assertOneError(report, "site.json: field 'media.story-1.alt' must be a single line (no line breaks)")

    def test_thumb_must_be_an_image(self):
        report = run(lambda s, i: s["media"]["proposal"].update(thumb="t.mp4"))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("field 'media.proposal.thumb' must be an image", report.errors[0])

    def test_unsafe_file_names(self):
        for name in ("../x.png", ".x.png", "a:b.png"):
            with self.subTest(name):
                report = run(lambda s, i: s["media"]["story-1"].update(file=name))
                self.assertEqual(len(report.errors), 1)
                self.assertTrue(report.errors[0].startswith("site.json: field 'media.story-1.file' must "))
                self.assertNotIn(name, report.errors[0])

    def test_no_name_is_reserved_for_the_calendar(self):
        # the calendar files live next to the pages, not in the media directory
        self.assertIsNone(_data.check_media_name("event.png"))
        report = run(lambda s, i: s["media"]["story-1"].update(file="event.ics"))
        self.assertEqual(len(report.errors), 1)
        self.assertIn("has an unsupported file type", report.errors[0])
        self.assertNotIn("reserved", report.errors[0])

    def test_type_is_required(self):
        report = run(lambda s, i: s["media"]["story-1"].pop("type"))
        self.assertOneError(report, "site.json: field 'media.story-1.type' is required (image or video)")


class TextTests(SchemaTestCase):
    def test_both_forms(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(text={"ty": "Привет"}))
        self.assertOneError(
            report, "site.json: field 'sections[1].widgets[0].text' must have both 'ty' and 'vy' (missing 'vy')"
        )

    def test_unknown_placeholder_is_not_quoted(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(text="Привет, {coupleName}!"))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(
            report.errors[0].startswith(
                "site.json: field 'sections[1].widgets[0].text' has an unknown placeholder at "
                "character 9 (did you mean {coupleNames}?); available: {coupleNames}"
            )
        )
        self.assertNotIn("coupleName}!", report.errors[0])
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(text="{Masha}"))
        self.assertNotIn("Masha", report.errors[0])

    def test_unclosed_brace(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][1]["text"].update(vy="Вы можете {со"))
        self.assertOneError(
            report,
            "site.json: field 'sections[1].widgets[1].text.vy' has an unclosed '{' at character "
            "11 (write '{{' for a literal brace)",
        )

    def test_placeholder_without_value(self):
        report = run(lambda s, i: s.pop("rsvpDeadline"))
        self.assertEqual(
            report.errors,
            [
                "site.json: field 'sections[7].widgets[0].text.ty' uses {rsvpDeadline}, but "
                "'rsvpDeadline' is not set",
                "site.json: field 'sections[7].widgets[0].text.vy' uses {rsvpDeadline}, but "
                "'rsvpDeadline' is not set",
            ],
        )

    def test_title_is_one_line(self):
        report = run(lambda s, i: s["sections"][3].update(title={"ty": "Дата\nи время", "vy": "Дата"}))
        self.assertOneError(report, "site.json: field 'sections[3].title.ty' must be a single line (no line breaks)")

    def test_literal_braces(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(text="{{скобки}} {greeting}"))
        self.assertMessages(report)

    def test_text_is_required(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].pop("text"))
        self.assertOneError(report, "site.json: field 'sections[1].widgets[0].text' is required")


def with_texts(site: dict, **texts) -> None:
    """Named texts: `announce` and `invite` by default, used by the section
    `invite`; the link preview of the fixture is removed."""
    site.pop("linkPreview", None)
    site["texts"] = texts or {
        "announce": "Мы, {coupleNames}, женимся!",
        "invite": {
            "ty": "Ждём тебя {eventDate}.",
            "vy": "Ждём вас {eventDate}.",
            "all": "Ждём вас {eventDate}.",
        },
    }
    widgets = site["sections"][1]["widgets"]
    widgets[0]["text"] = "{text:announce}"
    widgets[2]["text"] = "{text:invite}"


class NamedTextTests(SchemaTestCase):
    WIDGET = "site.json: field 'sections[1].widgets[0].text'"

    def test_named_texts_and_the_link_preview_are_valid(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["linkPreview"] = {"description": "{text:announce} {text:invite}"}

        self.assertMessages(run(mutate))

    def test_an_unknown_text(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["sections"][1]["widgets"][0]["text"] = "{text:anounce}"

        self.assertOneError(
            run(mutate),
            f"{self.WIDGET} refers to an unknown text 'anounce' at character 1 (did you mean "
            "'announce'?); known: announce, invite",
        )

    def test_a_reference_with_a_bad_id(self):
        report = run(lambda s, i: s["sections"][1]["widgets"][0].update(text="{text:Анна}"))
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(
            report.errors[0].startswith(f"{self.WIDGET} has a malformed text reference at character 1")
        )
        self.assertNotIn("Анна", report.errors[0])

    def test_a_cycle_and_its_chain(self):
        def mutate(site, _invitations):
            with_texts(site, announce="{text:invite}", invite={"ty": "{text:announce}", "vy": "x"})

        report = run(mutate)
        self.assertIn(
            "site.json: field 'texts.announce' uses texts in a cycle: 'invite' -> 'announce' -> "
            "'invite'",
            report.errors,
        )
        self.assertIn(
            "site.json: field 'sections[1].widgets[0].text' uses texts in a cycle: 'announce' -> "
            "'invite' -> 'announce'",
            report.errors,
        )

    def test_a_text_with_several_lines_in_a_title(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["texts"]["heading"] = {"ty": "Дата\nи время", "vy": "Дата", "all": "Дата"}
            site["sections"][3]["title"] = "{text:heading}"

        self.assertOneError(
            run(mutate),
            "site.json: field 'sections[3].title' must be a single line (no line breaks), but a "
            "text it uses has several lines",
        )

    def test_the_shape_of_a_named_text(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["texts"].update(
                number=5, half={"ty": "a"}, extra={"ty": "a", "vy": "b", "al": "c"}, empty=""
            )

        report = run(mutate)
        self.assertEqual(
            report.errors,
            [
                "site.json: field 'texts.number' must be a string or an object with the strings "
                "'ty' and 'vy' (and, for places without a guest, the common form 'all'), got a number",
                "site.json: field 'texts.half' must have both 'ty' and 'vy' (missing 'vy')",
                "site.json: unknown field 'texts.extra.al' (did you mean 'all'?)",
                "site.json: field 'texts.empty' must not be empty",
            ],
        )

    def test_texts_are_not_quoted(self):
        def mutate(site, _invitations):
            with_texts(site, a=f"{SECRET} {{unknownName}}", b={"ty": SECRET, "vy": f"{SECRET} {{"})

        report = run(mutate)
        self.assertEqual(len(report.errors), 4)  # two in the registry, two unknown uses
        self.assertNoPrivateData(report, SECRET, "unknownName")

    def test_the_common_form_knows_no_guest(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["texts"]["invite"]["all"] = "{greeting}, ждём вас."

        self.assertOneError(
            run(mutate),
            "site.json: field 'texts.invite.all' uses {greeting} (directly or through another "
            "text): the common form 'all' is the same for everybody and knows no guest",
        )

    def test_the_common_form_knows_no_guest_through_another_text(self):
        def mutate(site, _invitations):
            with_texts(site)
            site["texts"]["hello"] = "{greeting}"
            site["texts"]["invite"]["all"] = "{text:hello} Ждём вас."
            site["sections"][1]["widgets"][1]["text"] = "{text:hello}"

        report = run(mutate)
        self.assertEqual(
            report.errors,
            [
                "site.json: field 'texts.invite.all' uses {greeting} (directly or through another "
                "text): the common form 'all' is the same for everybody and knows no guest"
            ],
        )

    def test_the_common_form_is_for_named_texts_only(self):
        def mutate(site, _invitations):
            site["sections"][1]["widgets"][1]["text"]["all"] = "Для всех"

        self.assertOneError(
            run(mutate),
            "site.json: unknown field 'sections[1].widgets[1].text.all' (the common form 'all' is "
            "allowed in 'texts' only)",
        )

    def test_a_note_may_use_a_text_but_an_unknown_id_is_not_shown(self):
        def mutate(site, invitations):
            with_texts(site)
            invitations[1]["events"]["dinner"]["note"] = "{text:announce}"
            invitations[2]["sections"]["personal"]["note"] = "{text:ivan}"

        report = run(mutate)
        self.assertEqual(
            report.errors,
            [
                "invitation #3: field 'sections.personal.note' refers to an unknown text at "
                "character 1; known: announce, invite"
            ],
        )

    def test_a_texts_registry_of_the_wrong_type(self):
        report = run(lambda s, i: s.update(texts=["a"]))
        self.assertOneError(report, "site.json: field 'texts' must be an object { id: … }, got an array")


class LinkPreviewTests(SchemaTestCase):
    FIELD = "site.json: field 'linkPreview.description'"

    def run_with(self, description, **texts):
        def mutate(site, _invitations):
            with_texts(site, **texts)
            site["linkPreview"] = {"description": description}

        return run(mutate)

    def test_one_string_for_everybody(self):
        report = self.run_with({"ty": "a", "vy": "b"})
        self.assertOneError(
            report,
            f"{self.FIELD} must be one string for everybody: the link preview knows no guest; put "
            "the forms of address into a named text ('texts') with the common form 'all' and use "
            "it here as {text:<id>}",
        )

    def test_a_text_with_forms_needs_the_common_form(self):
        report = self.run_with(
            "{text:invite}", announce="Мы женимся!", invite={"ty": "Ждём тебя.", "vy": "Ждём вас."}
        )
        self.assertEqual(
            report.errors,
            [
                f"{self.FIELD} uses the text 'invite', which has forms of address but no common "
                "form 'all'; add \"all\" to 'texts.invite' - the variant for everybody"
            ],
        )

    def test_no_greeting_directly_or_through_a_text(self):
        expected = (
            f"{self.FIELD} uses {{greeting}} (directly or through a text): the link preview is "
            "the same for everybody; use named texts with the common form 'all' without {greeting}"
        )
        self.assertOneError(self.run_with("{greeting}, ждём!"), expected)
        report = self.run_with("{text:hello}", hello="{greeting}!", announce="a", invite="b")
        self.assertEqual(report.errors, [expected])

    def test_shape(self):
        self.assertOneError(
            run(lambda s, i: s.update(linkPreview="x")),
            "site.json: field 'linkPreview' must be an object, got a string",
        )
        self.assertOneError(
            run(lambda s, i: s.update(linkPreview={"descripton": "x"})),
            "site.json: unknown field 'linkPreview.descripton' (did you mean 'description'?)",
        )
        self.assertOneError(
            run(lambda s, i: s.update(linkPreview={"description": " "})),
            f"{self.FIELD} must not be empty (remove the field instead)",
        )
        self.assertOneError(
            run(lambda s, i: s.update(linkPreview={"description": 5})),
            f"{self.FIELD} must be a string, got a number",
        )


class InvitationTests(SchemaTestCase):
    def test_token_rules(self):
        report = run(lambda s, i: i[1].update(token="tiny"))
        self.assertOneError(
            report,
            "invitation #2: 'token' is too short: at least 5 characters required, got 4",
        )

    def test_token_length_limits(self):
        for length, valid in ((4, False), (5, True), (19, True), (20, True), (200, True), (201, False)):
            token = ("otter_" * 40)[:length]
            with self.subTest(length=length):
                report = run(lambda s, i: i[1].update(token=token))
                self.assertEqual(not report.errors, valid, report.errors)
                for message in report.errors:
                    self.assertNotIn(token[:3], message)

    def test_readable_tokens(self):
        for token in ("quokka", "otter_king", "badger_den", "heron-4tx9", "Walrus-Bay_12"):
            with self.subTest(token=token):
                self.assertMessages(run(lambda s, i: i[1].update(token=token)))

    def test_token_characters(self):
        for token in ("otter king", "otter.king", "выдра-выдра", "otter/king", "otter%20"):
            with self.subTest(token=token):
                report = run(lambda s, i: i[1].update(token=token))
                self.assertOneError(
                    report, "invitation #2: 'token' may only contain A-Z, a-z, 0-9, '_' and '-'"
                )

    def test_uuid_like_tokens_have_no_rule_of_their_own(self):
        for token in ("9b1f0c3e-5a7d-4e2b-8c6f-1d2e3f4a5b6c", "abc12", "12345", "dead-beef"):
            with self.subTest(token=token):
                self.assertMessages(run(lambda s, i: i[1].update(token=token)))

    def test_duplicate_token(self):
        report = run(lambda s, i: i[1].update(token=i[0]["token"].upper()))
        self.assertOneError(
            report,
            "invitation #2: duplicate token (same as invitation #1; tokens are compared "
            "case-insensitively because they become directory names)",
        )

    def test_duplicate_short_token_in_another_letter_case(self):
        def change(site, invitations):
            invitations[2]["token"] = "Quokka"
            invitations[6]["token"] = "quokka"

        report = run(change)
        self.assertOneError(
            report,
            "invitation #7: duplicate token (same as invitation #3; tokens are compared "
            "case-insensitively because they become directory names)",
        )

    def test_label_holds_no_character_of_the_token(self):
        report = run(lambda s, i: i[0].update(greeting=""))
        self.assertOneError(report, f"{GUEST_1}: field 'greeting' must not be empty")
        self.assertEqual(_data.invitation_label(12), "invitation #12")

    def test_form(self):
        report = run(lambda s, i: i[5].update(form="tu"))
        self.assertOneError(report, "invitation #6: field 'form' must be one of: ty, vy")
        report = run(lambda s, i: i[5].pop("form"))
        self.assertOneError(report, "invitation #6: field 'form' is required ('ty' or 'vy')")

    def test_greeting_is_one_line(self):
        report = run(lambda s, i: i[0].update(greeting="Дорогая\nКэрол"))
        self.assertOneError(report, f"{GUEST_1}: field 'greeting' must be a single line (no line breaks)")

    def test_old_fields_of_one_invitation(self):
        def change(site, invitations):
            invitations[0].pop("form")
            invitations[0].update(ty=True, plusOne=True, note=SECRET)

        report = run(change)
        self.assertEqual(
            [message.split(":")[1] for message in report.errors],
            [" field 'ty' is no longer supported", " field 'plusOne' is no longer supported",
             " field 'note' is no longer supported"],
        )
        self.assertNoPrivateData(report, SECRET)

    def test_unknown_section(self):
        report = run(lambda s, i: i[0]["sections"].update(travle={"visible": True}))
        self.assertOneError(
            report,
            f"{GUEST_1}: field 'sections.<unknown key>' refers to an unknown section (did you "
            "mean 'travel'?); known: cover, invite, personal, when, where, travel, story, rsvp",
        )

    def test_unknown_event(self):
        report = run(lambda s, i: i[1]["events"].update(brunhc={"visible": True}))
        self.assertOneError(
            report,
            f"{GUEST_2}: field 'events.<unknown key>' refers to an unknown event (did you mean "
            "'brunch'?); known: ceremony, dinner, brunch",
        )

    def test_widget_without_an_id(self):
        report = run(lambda s, i: i[1].update(sections={"where": {"widgets": {"events": {"visible": False}}}}))
        self.assertOneError(
            report,
            f"{GUEST_2}: field 'sections.where.widgets.events' refers to no widget of section "
            "'where'; give the widget an \"id\" in site.json to address it",
        )

    def test_unknown_widget_lists_the_ids(self):
        report = run(lambda s, i: i[5]["sections"]["travel"]["widgets"].update(hotel={"visible": True}))
        self.assertOneError(
            report,
            "invitation #6: field 'sections.travel.widgets.<unknown key>' refers to no "
            "widget of section 'travel'; widgets with an id: hotel-booked",
        )

    def test_unknown_field_in_an_override(self):
        report = run(lambda s, i: i[1].update(sections={"where": {"visibel": False}}))
        self.assertOneError(report, f"{GUEST_2}: unknown field 'sections.where.<unknown key>' (did you mean 'visible'?)")

    def test_primary_event(self):
        report = run(lambda s, i: i[2].update(primaryEvent="brunhc"))
        self.assertOneError(
            report,
            "invitation #3: field 'primaryEvent' refers to an unknown event (did you "
            "mean 'brunch'?); known: ceremony, dinner, brunch",
        )
        report = run(lambda s, i: i[4].update(events={"ceremony": {"visible": False}}))
        self.assertOneError(
            report, "invitation #5: field 'primaryEvent' refers to 'ceremony', which is hidden for this invitation"
        )
        report = run(lambda s, i: i[3].update(events={"dinner": {"visible": False}}))
        self.assertOneError(
            report, "invitation #4: field 'primaryEvent' is required: the main event 'dinner' is hidden for this invitation"
        )
        report = run(lambda s, i: i[3].update(primaryEvent="brunch"))
        self.assertOneError(
            report, "invitation #4: field 'primaryEvent' refers to 'brunch', which is hidden for this invitation"
        )

    def test_every_event_hidden(self):
        report = run(lambda s, i: i[6].update(events={e: {"visible": False} for e in ("ceremony", "dinner")}))
        self.assertIn("invitation #7: field 'events' hides every event; at least one must stay visible", report.errors)

    def test_cover_cannot_be_set(self):
        report = run(lambda s, i: i[0]["sections"].update(cover={"visible": False}))
        self.assertOneError(report, f"{GUEST_1}: field 'sections.cover' cannot be set: the cover is the same for everybody")

    def test_note_is_a_string(self):
        report = run(lambda s, i: i[0]["sections"]["personal"].update(note={"ty": "a", "vy": "b"}))
        self.assertOneError(report, f"{GUEST_1}: field 'sections.personal.note' must be a string, got an object")

    def test_note_placeholders(self):
        report = run(lambda s, i: i[2]["sections"]["personal"].update(note="Ждём тебя к {eventTme}, {Masha}!"))
        self.assertEqual(len(report.errors), 2)
        self.assertIn("field 'sections.personal.note' has an unknown placeholder at character 13 (did you mean {eventTime}?)", report.errors[0])
        self.assertIn("at character 25; available:", report.errors[1])
        self.assertNoPrivateData(report, "Masha", "eventTme")

    def test_note_on_hidden_section_or_event(self):
        def change(site, invitations):
            invitations[4]["sections"]["travel"] = {"note": "Встретим вас на вокзале"}
            invitations[1]["events"]["brunch"] = {"note": "Второй день"}

        report = run(change)
        self.assertEqual(report.errors, [])
        self.assertEqual(
            report.warnings,
            [
                f"{GUEST_2}: field 'events.brunch.note' is set, but the event is hidden for this invitation",
                "invitation #5: field 'sections.travel.note' is set, but the section is hidden for this invitation",
            ],
        )

    def test_override_types(self):
        report = run(lambda s, i: i[0]["sections"]["personal"].update(visible="yes"))
        self.assertOneError(report, f"{GUEST_1}: field 'sections.personal.visible' must be a boolean (true or false), got a string")
        report = run(lambda s, i: i[0]["sections"]["invite"]["widgets"]["plus-one"].update(visible="yes"))
        self.assertOneError(
            report,
            f"{GUEST_1}: field 'sections.invite.widgets.plus-one.visible' must be a boolean (true or false), got a string",
        )
        report = run(lambda s, i: i[0].update(events=[]))
        self.assertOneError(report, f"{GUEST_1}: field 'events' must be an object, got an array")
        report = run(lambda s, i: i[0]["sections"].update(travel=True))
        self.assertOneError(report, f"{GUEST_1}: field 'sections.travel' must be an object, got a boolean")

    def test_invitation_must_be_an_object(self):
        report = run(lambda s, i: i.append("x"))
        self.assertOneError(report, "invitation #9: must be an object, got a string")

    def test_broken_sections_are_not_reported_again_for_the_invitations(self):
        def change(site, invitations):
            site["sections"][5]["id"] = "Travel"

        report = run(change)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("sections[5].id", report.errors[0])

    def test_broken_widgets_are_not_reported_again_for_the_invitations(self):
        cases = {
            "type": lambda widget: widget.update(type="txt"),
            "id": lambda widget: widget.update(id="Hotel_Booked"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                report = run(lambda s, i: change(s["sections"][5]["widgets"][2]))
                self.assertEqual(len(report.errors), 1, report.errors)
                self.assertTrue(report.errors[0].startswith("site.json: field 'sections[5].widgets[2]."))

    def test_other_sections_still_check_widget_references(self):
        def change(site, invitations):
            site["sections"][5]["widgets"][2]["type"] = "txt"
            invitations[1]["sections"] = {"where": {"widgets": {"events": {"visible": False}}}}

        report = run(change)
        self.assertEqual(len(report.errors), 2)
        self.assertIn("refers to no widget of section 'where'", report.errors[1])

    def test_broken_events_registry_is_not_reported_again(self):
        report = run(lambda s, i: s.update(events=[]))
        self.assertTrue(all(message.startswith("site.json") for message in report.errors))


class GuestKeyPrivacyTests(SchemaTestCase):
    """A key of an invitation is shown only when site.json declares it."""

    def scenarios(self, key):
        return {
            "section": lambda s, i: i[0]["sections"].update({key: {"visible": True}}),
            "event": lambda s, i: i[0]["events"].update({key: {"visible": True}}),
            "widget": lambda s, i: i[5]["sections"]["travel"]["widgets"].update({key: {"visible": True}}),
            "field": lambda s, i: i[0].update({key: True}),
            "section field": lambda s, i: i[0]["sections"]["personal"].update({key: True}),
            "event field": lambda s, i: i[1]["events"]["dinner"].update({key: True}),
            "widget field": lambda s, i: i[5]["sections"]["travel"]["widgets"]["hotel-booked"].update({key: True}),
            "primary event": lambda s, i: i[0].update(primaryEvent=key),
        }

    def test_name_like_keys_never_reach_a_message(self):
        for key in NAME_KEYS:
            for name, change in self.scenarios(key).items():
                with self.subTest(key=key, place=name):
                    report = run(change)
                    self.assertEqual(len(report.errors), 1, report.errors)
                    self.assertNotIn(key, report.errors[0])
                    self.assertNoPrivateData(report)

    def test_hints_come_from_site_json(self):
        report = run(lambda s, i: i[0]["sections"].update({"ivan": {"visible": True}}))
        self.assertIn("; known: cover, invite", report.errors[0])
        self.assertIn("<unknown key>", report.errors[0])

    def test_declared_ids_are_shown(self):
        report = run(lambda s, i: i[0]["sections"]["personal"].update(visible="x"))
        self.assertIn("'sections.personal.visible'", report.errors[0])

    def test_rules_for_site_json_are_unchanged(self):
        report = run(lambda s, i: s.update(IvanPetrov=True))
        self.assertOneError(report, "site.json: unknown field 'IvanPetrov'")


class StrictKeyTests(unittest.TestCase):
    """The strict mode of the key helpers of `tools/_data.py`."""

    def test_show_key(self):
        known = {"travel", "visible"}
        self.assertEqual(_data.show_key("travel", known), "'travel'")
        for key in (*NAME_KEYS, SECRET, 3, None):
            with self.subTest(key=key):
                self.assertEqual(_data.show_key(key, known), "<unknown key>")
        self.assertEqual(_data.show_key("ivan"), "'ivan'")  # the rule without names

    def test_format_path(self):
        known = {"sections", "travel"}
        self.assertEqual(_data.format_path(("sections", "ivan", "note"), known), "sections.<unknown key>.<unknown key>")
        self.assertEqual(_data.format_path(("sections", "travel", 2), known), "sections.travel[2]")
        self.assertEqual(_data.format_path(("sections", "ivan")), "sections.ivan")


class PageWarningTests(SchemaTestCase):
    """Warnings that need the page trees (`tools._page.build_pages`)."""

    def warnings(self, mutate=None, info=F.MEDIA_INFO):
        site, invitations = F.site(), F.invitations()
        if mutate is not None:
            mutate(site, invitations)
        report = F.Collector()
        index = _schema.check_data(site, invitations, report)
        self.assertEqual(report.errors, [])
        self.assertTrue(index.ok)
        _page.build_pages(site, invitations, F.settings(media_info=info), report)
        self.assertNoPrivateData(report)
        return report.warnings

    def test_fixture_has_none(self):
        self.assertEqual(self.warnings(), [])

    def test_section_switched_on_but_empty(self):
        warnings = self.warnings(lambda s, i: i[1].update(sections={"personal": {"visible": True}}))
        self.assertEqual(
            warnings,
            [f"{GUEST_2}: section 'personal' is switched on, but has nothing to show for this invitation"],
        )

    def test_event_note_that_no_widget_shows(self):
        def change(site, invitations):
            site["sections"][4]["widgets"][0]["events"] = ["dinner", "brunch"]
            invitations[1]["events"]["ceremony"] = {"note": "Приходи пораньше"}

        warnings = self.warnings(change)
        self.assertIn(
            f"{GUEST_2}: field 'events.ceremony.note' is set, but no 'events' widget on this page shows the event",
            warnings,
        )
        self.assertNotIn("Приходи пораньше", " ".join(warnings))

    def test_unused_parts_of_site_json(self):
        def change(site, invitations):
            site["sections"].insert(7, {"id": "gifts", "title": "Подарки", "visible": False, "widgets": [{"type": "text", "text": "x"}]})
            site["locations"]["station"] = {"name": "Вокзал"}
            site["schedules"]["spare"] = [{"title": "Сбор"}]
            site["media"]["unused-1"] = {"type": "image", "file": "unused-1.webp", "alt": "x"}
            site["events"]["rehearsal"] = {"title": "Репетиция", "location": "manor", "start": "2030-06-14T18:00:00+03:00", "end": "2030-06-14T20:00:00+03:00", "visible": False}

        self.assertEqual(
            self.warnings(change),
            [
                "site.json: section 'gifts' is not shown on any page",
                "site.json: event 'rehearsal' is not shown on any page",
                "site.json: location 'station' is not shown on any page",
                "site.json: schedule 'spare' is not shown on any page",
                "site.json: media item 'unused-1' is not shown on any page and its files are not published",
            ],
        )

    def test_place_not_announced(self):
        def change(site, invitations):
            site["sections"][1]["widgets"][0]["text"] = "Ждём вас в {eventPlace}. {text:announce}"
            invitations[0]["primaryEvent"] = "brunch"

        warnings = self.warnings(change)
        self.assertEqual(
            warnings,
            [
                "site.json: field 'sections[1].widgets[0].text' uses {eventPlace}, but the place "
                "of the event 'brunch' is not announced yet (the text gets an empty string)"
            ],
        )


class MediaFileTests(SchemaTestCase):
    def check(self, infos, change=None):
        site = F.site()
        if change is not None:
            change(site)
        report = F.Collector()
        _schema.check_media_files(site, infos, report)
        for message in report.messages:
            self.assertNotIn(".png", message)
            self.assertNotIn(".mp4", message)
        return report

    def with_video(self, **facts):
        infos = dict(F.MEDIA_INFO)
        infos["proposal.mp4"] = MediaInfo("proposal.mp4", **facts)
        return infos

    def test_good_files(self):
        self.assertMessages(self.check(F.MEDIA_INFO))

    def test_unknown_duration(self):
        report = self.check(self.with_video(video_codec="avc1", problems=(_media.DURATION_UNKNOWN,)))
        self.assertEqual(
            report.warnings,
            ["site.json: field 'media.proposal.file' has no readable duration; the tile will show none"],
        )

    def test_video_warnings(self):
        codes = (_media.NOT_FASTSTART, _media.UNEXPECTED_CODEC, _media.HDR_VIDEO)
        report = self.check(self.with_video(problems=codes))
        self.assertEqual(len(report.warnings), 3)
        self.assertIn("+faststart", report.warnings[0])
        self.assertIn("not H.264", report.warnings[1])
        self.assertIn("HDR", report.warnings[2])

    def test_unknown_codec(self):
        report = self.check(self.with_video(problems=(_media.CODEC_UNKNOWN,)))
        self.assertIn("has a video codec that cannot be read", report.warnings[0])

    def test_video_without_a_readable_poster_size_takes_the_video_size(self):
        infos = dict(F.MEDIA_INFO)
        infos["proposal-poster.png"] = MediaInfo("proposal-poster.png", problems=(_media.SIZE_UNKNOWN,))
        def change(site):
            for key in ("width", "height"):
                site["media"]["proposal"].pop(key)

        self.assertMessages(self.check(infos, change))
        infos["proposal.mp4"] = MediaInfo("proposal.mp4", video_codec="avc1", duration_seconds=1.0)
        report = self.check(infos, change)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("'media.proposal.poster' has no readable picture size", report.warnings[0])

    def test_poster_of_another_proportion(self):
        infos = dict(F.MEDIA_INFO)
        infos["proposal-poster.png"] = MediaInfo("proposal-poster.png", width=720, height=1200)
        report = self.check(infos)
        self.assertEqual(
            report.warnings,
            [
                "site.json: field 'media.proposal.poster' has another proportion than 'width' "
                "and 'height'; make the poster the same proportion as the video"
            ],
        )

    def test_one_percent_is_tolerated(self):
        infos = dict(F.MEDIA_INFO)
        infos["proposal-poster.png"] = MediaInfo("proposal-poster.png", width=721, height=1280)
        self.assertMessages(self.check(infos))

    def test_unknown_picture_size(self):
        infos = dict(F.MEDIA_INFO)
        infos["story-1.png"] = MediaInfo("story-1.png", problems=(_media.SIZE_UNKNOWN,))
        report = self.check(infos)
        self.assertEqual(len(report.warnings), 1)
        self.assertTrue(report.warnings[0].startswith("site.json: field 'media.story-1.file' has no readable picture size"))
        report = self.check(infos, lambda s: s["media"]["story-1"].update(width=10, height=10))
        self.assertMessages(report)

    def test_broken_files(self):
        infos = dict(F.MEDIA_INFO)
        infos["story-1.png"] = MediaInfo("story-1.png", problems=(_media.NOT_THIS_TYPE, _media.SIZE_UNKNOWN))
        infos["proposal.mp4"] = MediaInfo("proposal.mp4", problems=(_media.UNREADABLE,))
        report = self.check(infos)
        self.assertEqual(
            report.errors,
            [
                "site.json: field 'media.proposal.file' names a file that cannot be read",
                "site.json: field 'media.story-1.file' names a file that is not a valid file "
                "of the type its name gives",
            ],
        )
        self.assertEqual(report.warnings, [])

    def test_a_large_cover_photo_is_a_warning(self):
        limit = _schema.COVER_BACKGROUND_WARN_BYTES
        report = F.Collector()
        _schema.check_media_files(F.site(), F.MEDIA_INFO, report, sizes={"cover.png": limit})
        self.assertMessages(report)
        _schema.check_media_files(F.site(), F.MEDIA_INFO, report, sizes={"cover.png": limit + 1})
        self.assertEqual(len(report.warnings), 1)
        self.assertTrue(report.warnings[0].startswith("site.json: field 'media.cover.file' is 1.0 MiB;"))
        self.assertIn("first screen: keep it under ~400 KB", report.warnings[0])
        self.assertNotIn(".png", report.warnings[0])
        # any other picture of that size is fine; so is an unknown size
        report = F.Collector()
        _schema.check_media_files(F.site(), F.MEDIA_INFO, report, sizes={"venue-1.png": limit * 5})
        _schema.check_media_files(F.site(), F.MEDIA_INFO, report)
        self.assertMessages(report)

    def test_only_shown_items(self):
        infos = dict(F.MEDIA_INFO)
        infos["story-1.png"] = MediaInfo("story-1.png", problems=(_media.UNREADABLE,))
        report = F.Collector()
        _schema.check_media_files(F.site(), infos, report, {"proposal"})
        self.assertMessages(report)
        _schema.check_media_files(F.site(), infos, report, {"story-1"})
        self.assertEqual(len(report.errors), 1)


class NegativeScenarioPrivacyTests(SchemaTestCase):
    """No scenario prints a value of the data (the fixture's personal values,
    titles, addresses, texts)."""

    def test_values_of_site_json_are_not_quoted(self):
        values = (
            F.SITE["coupleNames"],
            F.SITE["events"]["dinner"]["title"],
            F.SITE["locations"]["manor"]["name"],
            F.SITE["locations"]["manor"]["address"],
            F.SITE["events"]["dinner"]["start"],
        )

        def change(site, invitations):
            site["coupleNames"] = [site["coupleNames"]]
            site["events"]["dinner"]["title"] += "\n"
            site["locations"]["manor"]["name"] = {"x": site["locations"]["manor"]["name"]}
            site["locations"]["manor"]["address"] += "\n"
            site["events"]["dinner"]["start"] = site["events"]["dinner"]["start"][:19] + "+3"

        report = run(change)
        self.assertEqual(len(report.errors), 5)
        self.assertNoPrivateData(report, *values)


if __name__ == "__main__":
    unittest.main()
