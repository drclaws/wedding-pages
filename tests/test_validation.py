"""Tests for data loading, validation and context building."""

from __future__ import annotations

import json
import unittest
import uuid

from tests import support
from tests.support import (
    TempDirTestCase,
    build,
    invitations_data,
    site_data,
    write_data,
    write_json,
    write_media,
)


def errors_of(site=None, invitations=None, media=True, tmp=None):
    """Validate a fixture and return the list of error messages."""
    data_dir = tmp / "data"
    media_dir = tmp / "media"
    write_data(data_dir, site=site, invitations=invitations)
    write_media(media_dir)
    report = build.Report()
    try:
        build.load_data(data_dir, media_dir if media else None, report)
    except build.ValidationError as exc:
        return exc.errors
    return []


class TokenTests(unittest.TestCase):
    def test_generated_token_passes_its_own_check(self):
        for _ in range(200):
            token = build.generate_token()
            self.assertIsNone(build.check_token(token), token)
            self.assertGreaterEqual(len(token), build.MIN_TOKEN_LENGTH)

    def test_uuid_token_is_accepted(self):
        self.assertIsNone(build.check_token(str(uuid.uuid4())))
        self.assertIsNone(build.check_token(uuid.uuid4().hex))

    def test_hex_only_token_needs_more_digits(self):
        # 28 hex digits and dashes: below the UUID threshold although it is
        # longer than the generic minimum.
        weak = "1234abcd-5678-90ef-1234-567890ab"
        self.assertLess(len(weak) - weak.count("-"), build.MIN_UUID_HEX_DIGITS)
        self.assertIn("hex digits", build.check_token(weak) or "")
        self.assertIsNone(build.check_token("0" * 30))

    def test_short_token(self):
        self.assertIn("too short", build.check_token("short-token-xyz") or "")

    def test_token_length_is_capped(self):
        self.assertIsNone(build.check_token("Z" * build.MAX_NAME_LENGTH))
        self.assertIn("too long", build.check_token("Z" * (build.MAX_NAME_LENGTH + 1)) or "")
        self.assertIn("too long", build.check_token("0" * 5000) or "")

    def test_invalid_characters(self):
        for token in ("with space aaaaaaaaaaaaa", "токен-кириллица-длинный", "plus+slash/aaaaaaaaaa"):
            with self.subTest(token=token):
                self.assertIn("may only contain", build.check_token(token) or "")

    def test_empty_and_wrong_type(self):
        self.assertIn("empty", build.check_token("") or "")
        self.assertIn("must be a string", build.check_token(1234) or "")
        self.assertIn("must be a string", build.check_token(None) or "")

    def test_label_shows_only_four_characters(self):
        label = build.invitation_label(3, support.TOKEN_A)
        self.assertEqual(label, f"invitation #3 ({support.TOKEN_A[:4]}…)")
        self.assertNotIn(support.TOKEN_A, label)
        self.assertEqual(build.invitation_label(1, None), "invitation #1 (no token)")


class InvitationValidationTests(TempDirTestCase):
    def errors(self, invitations):
        return errors_of(invitations=invitations, tmp=self.tmp)

    def test_valid_fixture_has_no_errors(self):
        self.assertEqual(self.errors(invitations_data()), [])

    def test_duplicate_tokens(self):
        invitations = invitations_data(2)
        invitations[1]["token"] = invitations[0]["token"]
        errors = self.errors(invitations)
        self.assertTrue(any("duplicate token" in error for error in errors), errors)
        self.assertTrue(any("invitation #2" in error for error in errors), errors)

    def test_duplicate_tokens_differing_in_case(self):
        invitations = invitations_data(2)
        invitations[1]["token"] = invitations[0]["token"].upper()
        errors = self.errors(invitations)
        self.assertTrue(any("duplicate token" in error for error in errors), errors)

    def test_short_token(self):
        invitations = invitations_data(1)
        invitations[0]["token"] = "tiny-token"
        self.assertTrue(any("too short" in error for error in self.errors(invitations)))

    def test_invalid_token_characters(self):
        invitations = invitations_data(1)
        invitations[0]["token"] = "invalid token with spaces!!"
        self.assertTrue(
            any("may only contain" in error for error in self.errors(invitations))
        )

    def test_uuid_token_passes(self):
        invitations = invitations_data(1)
        invitations[0]["token"] = str(uuid.uuid4())
        self.assertEqual(self.errors(invitations), [])

    def test_ty_and_vy_both_true(self):
        invitations = invitations_data(1)
        invitations[0]["vy"] = True
        errors = self.errors(invitations)
        self.assertTrue(any("both are true" in error for error in errors), errors)

    def test_ty_and_vy_both_false(self):
        invitations = invitations_data(1)
        invitations[0]["ty"] = False
        errors = self.errors(invitations)
        self.assertTrue(any("both are false" in error for error in errors), errors)

    def test_missing_required_fields(self):
        invitations = invitations_data(1)
        del invitations[0]["greeting"]
        del invitations[0]["plusOne"]
        errors = self.errors(invitations)
        self.assertTrue(any("'greeting'" in error for error in errors), errors)
        self.assertTrue(any("'plusOne'" in error for error in errors), errors)

    def test_empty_greeting(self):
        invitations = invitations_data(1)
        invitations[0]["greeting"] = "   "
        self.assertTrue(any("must not be empty" in e for e in self.errors(invitations)))

    def test_boolean_fields_must_be_booleans(self):
        invitations = invitations_data(1)
        invitations[0]["plusOne"] = 1
        errors = self.errors(invitations)
        self.assertTrue(any("must be a boolean" in error for error in errors), errors)

    def test_greeting_must_be_a_single_line(self):
        invitations = invitations_data()
        invitations[0]["greeting"] = "Дорогая\n\nЕва!"
        invitations[0]["note"] = "first\n\nsecond"  # notes may have paragraphs
        errors = self.errors(invitations)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("invitation #1 (EveT…): field 'greeting' must be a single line", errors[0])

    def test_out_of_town_section_without_text_is_a_warning(self):
        report = build.Report()
        site = site_data(outOfTownText="  ")
        build.warn_empty_out_of_town(site, invitations_data(), report)
        self.assertEqual(report.warnings, [])  # the only such guest has a travel note
        invitations = invitations_data()
        invitations[2]["travelNote"] = ""
        invitations[1]["outOfTown"] = True
        build.warn_empty_out_of_town(site, invitations, report)
        self.assertEqual(len(report.warnings), 2)
        self.assertIn("invitation #2 (Karl…): 'outOfTown' is true", report.warnings[0])
        self.assertIn("invitation #3 (Gosh…)", report.warnings[1])
        self.assertEqual(report.errors, [])
        for secret in support.PRIVATE_STRINGS:
            self.assertNotIn(secret, " ".join(report.warnings))
        # with a shared text the section is never empty
        report = build.Report()
        build.warn_empty_out_of_town(site_data(), invitations, report)
        self.assertEqual(report.warnings, [])

    def test_optional_notes(self):
        invitations = invitations_data(1)
        invitations[0]["note"] = None
        invitations[0]["travelNote"] = ""
        self.assertEqual(self.errors(invitations), [])
        invitations[0]["note"] = 42
        self.assertTrue(any("'note'" in error for error in self.errors(invitations)))

    def test_invitation_must_be_an_object(self):
        errors = self.errors(["not an object"])
        self.assertTrue(any("must be an object" in error for error in errors), errors)

    def test_top_level_must_be_an_array(self):
        errors = self.errors({"token": "x"})
        self.assertTrue(any("must be an array" in error for error in errors), errors)

    def test_unknown_field_is_only_a_warning(self):
        invitations = invitations_data(1)
        invitations[0]["surpriseField"] = "VALUE-MUST-NOT-LEAK"
        data_dir = write_data(self.tmp / "data", invitations=invitations)
        media_dir = write_media(self.tmp / "media")
        report = build.Report()
        build.load_data(data_dir, media_dir, report)
        self.assertEqual(report.errors, [])
        self.assertTrue(any("surpriseField" in w for w in report.warnings), report.warnings)
        self.assertFalse(any("VALUE-MUST-NOT-LEAK" in w for w in report.warnings))

    def test_all_errors_are_collected(self):
        invitations = invitations_data(2)
        invitations[0]["token"] = "short"
        del invitations[0]["greeting"]
        invitations[1]["ty"] = True
        errors = self.errors(invitations)
        self.assertGreaterEqual(len(errors), 3)


class SiteValidationTests(TempDirTestCase):
    def errors(self, site):
        return errors_of(site=site, tmp=self.tmp)

    def test_valid_fixture_has_no_errors(self):
        self.assertEqual(self.errors(site_data()), [])

    def test_date_iso_requires_an_offset(self):
        errors = self.errors(site_data(dateISO="2030-06-01T16:00:00"))
        self.assertTrue(any("no UTC offset" in error for error in errors), errors)

    def test_date_iso_accepts_z_suffix(self):
        self.assertEqual(self.errors(site_data(dateISO="2030-06-01T13:00:00Z")), [])
        self.assertEqual(self.errors(site_data(dateISO="2030-06-01T16:00+03:00")), [])

    def test_date_iso_rejects_date_only_and_garbage(self):
        for value in ("2030-06-01", "tomorrow", "2030-13-01T10:00:00+03:00"):
            with self.subTest(value=value):
                self.assertTrue(self.errors(site_data(dateISO=value)))

    def test_parse_date_iso_returns_aware_datetime(self):
        parsed = build.parse_date_iso("2030-06-01T16:00:00+03:00")
        self.assertIsNotNone(parsed.utcoffset())
        self.assertEqual(parsed.hour, 16)

    def test_media_dir_rules(self):
        self.assertTrue(self.errors(site_data(mediaDir="short")))
        self.assertTrue(self.errors(site_data(mediaDir="media dir with spaces")))
        self.assertEqual(self.errors(site_data(mediaDir="a" * 16)), [])
        self.assertEqual(self.errors(site_data(mediaDir="a" * build.MAX_NAME_LENGTH)), [])
        errors = self.errors(site_data(mediaDir="a" * (build.MAX_NAME_LENGTH + 1)))
        self.assertTrue(any("'mediaDir'" in error for error in errors), errors)

    def test_missing_required_site_fields(self):
        site = site_data()
        del site["coupleNames"]
        del site["venue"]
        errors = self.errors(site)
        self.assertTrue(any("'coupleNames'" in error for error in errors), errors)
        self.assertTrue(any("'venue'" in error for error in errors), errors)

    def test_video_is_optional(self):
        site = site_data()
        del site["video"]
        self.assertEqual(self.errors(site), [])
        self.assertEqual(self.errors(site_data(video=None)), [])

    def test_video_needs_both_fields(self):
        errors = self.errors(site_data(video={"file": "clip.mp4"}))
        self.assertTrue(any("'video.poster'" in error for error in errors), errors)

    def test_missing_video_file(self):
        errors = self.errors(site_data(video={"file": "absent.mp4", "poster": "poster.jpg"}))
        self.assertTrue(any("absent.mp4" in error for error in errors), errors)

    def test_missing_photo_and_directions_image(self):
        site = site_data()
        site["venue"]["photos"] = ["venue-1.webp", "absent.webp"]
        site["venue"]["directionsImage"] = "absent-route.png"
        errors = self.errors(site)
        self.assertTrue(any("absent.webp" in error for error in errors), errors)
        self.assertTrue(any("absent-route.png" in error for error in errors), errors)

    def test_media_names_must_be_plain(self):
        for name in ("../secret.webp", "sub/dir.webp", "back\\slash.webp", ".hidden.webp"):
            with self.subTest(name=name):
                site = site_data()
                site["venue"]["photos"] = [name]
                errors = self.errors(site)
                self.assertTrue(errors, name)
                self.assertTrue(
                    any("venue.photos[0]" in error for error in errors), errors
                )

    def test_media_file_types(self):
        self.assertIsNone(build.check_media_name("photo.JPG"))
        self.assertIsNone(build.check_media_name("scheme.svg", build.IMAGE_EXTENSIONS))
        self.assertIn("unsupported file type", build.check_media_name("photo.heic"))
        self.assertIn("unsupported file type", build.check_media_name("no-extension"))
        self.assertIn(
            "unsupported file type", build.check_media_name("clip.mp4", build.IMAGE_EXTENSIONS)
        )
        self.assertIn("reserved", build.check_media_name("EVENT.ics"))
        # a video where an image is expected, and the other way round
        errors = self.errors(site_data(video={"file": "poster.jpg", "poster": "clip.mp4"}))
        self.assertTrue(any("'video.file' has an unsupported" in e for e in errors), errors)
        self.assertTrue(any("'video.poster' has an unsupported" in e for e in errors), errors)
        # everything the data may refer to can be published
        self.assertLessEqual(
            build.IMAGE_EXTENSIONS | build.VIDEO_EXTENSIONS, build.ASSET_EXTENSIONS
        )

    def test_media_dir_must_not_shadow_an_asset(self):
        assets = self.tmp / "assets"
        (assets / "Fonts-And-Vendor-Files").mkdir(parents=True)
        report = build.Report()
        build.check_media_dir_collision(
            site_data(mediaDir="fonts-and-vendor-files"), assets, report
        )
        self.assertEqual(len(report.errors), 1)
        self.assertIn("'mediaDir' collides with 'Fonts-And-Vendor-Files'", report.errors[0])
        report = build.Report()
        build.check_media_dir_collision(site_data(), assets, report)
        build.check_media_dir_collision(site_data(), self.tmp / "no-assets", report)
        self.assertEqual(report.errors, [])

    def test_single_line_fields(self):
        for key in ("coupleNames", "dateText", "rsvpDeadline"):
            errors = self.errors(site_data(**{key: "first\n\nsecond"}))
            self.assertTrue(
                any(f"'{key}' must be a single line" in e for e in errors), (key, errors)
            )
        site = site_data()
        site["venue"]["name"] = "first\nsecond"
        site["venue"]["address"] = "first\r\nsecond"
        site["schedule"][0]["time"] = "16:00\n"
        site["schedule"][1]["title"] = "first\nsecond"
        errors = self.errors(site)
        for name in ("venue.name", "venue.address", "schedule[0].time", "schedule[1].title"):
            self.assertTrue(any(f"'{name}' must be a single line" in e for e in errors), (name, errors))
        self.assertFalse(any("second" in e for e in errors), errors)

    def test_multi_line_fields(self):
        site = site_data(outOfTownText="first\n\nsecond")
        site["venue"]["description"] = "first\n\nsecond"
        site["schedule"][0]["text"] = "first\nsecond"
        self.assertEqual(self.errors(site), [])

    def test_media_case_sensitivity(self):
        site = site_data()
        site["venue"]["photos"] = ["Venue-1.webp"]
        errors = self.errors(site)
        self.assertTrue(any("case-sensitive" in error for error in errors), errors)

    def test_media_files_are_checked_even_when_venue_is_not_ready(self):
        site = site_data()
        site["venue"]["ready"] = False
        site["venue"]["photos"] = ["absent.webp"]
        self.assertTrue(self.errors(site))

    def test_venue_not_ready_may_be_empty(self):
        site = site_data()
        site["venue"] = {"ready": False, "photos": [], "directionsImage": ""}
        self.assertEqual(self.errors(site), [])

    def test_venue_ready_requires_a_name(self):
        site = site_data()
        site["venue"]["name"] = ""
        errors = self.errors(site)
        self.assertTrue(any("'venue.name'" in error for error in errors), errors)

    def test_schedule_may_be_empty_but_must_be_an_array(self):
        self.assertEqual(self.errors(site_data(schedule=[])), [])
        self.assertTrue(self.errors(site_data(schedule={"time": "16:00"})))

    def test_schedule_entry_fields(self):
        errors = self.errors(site_data(schedule=[{"time": "16:00"}]))
        self.assertTrue(any("schedule[0].title" in error for error in errors), errors)

    def test_geo_bounds_and_types(self):
        site = site_data()
        site["venue"]["geo"] = {"lat": 100.0, "lng": "20"}
        errors = self.errors(site)
        self.assertTrue(any("venue.geo.lat" in error for error in errors), errors)
        self.assertTrue(any("venue.geo.lng" in error for error in errors), errors)

    def test_huge_and_infinite_numbers(self):
        # 10**400 does not fit into a float, 1e999 is parsed as infinity
        for literal in ("1" + "0" * 400, "1e999", "-1e999"):
            with self.subTest(literal=literal):
                data_dir = write_data(self.tmp / "data")
                raw = (data_dir / "site.json").read_text(encoding="utf-8")
                self.assertEqual(raw.count('"lat": 10.5'), 1)
                (data_dir / "site.json").write_text(
                    raw.replace('"lat": 10.5', f'"lat": {literal}'), encoding="utf-8"
                )
                with self.assertRaises(build.ValidationError) as caught:
                    build.load_data(data_dir)
                errors = caught.exception.errors
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("'venue.geo.lat' must be a finite number", errors[0])

    def test_top_level_must_be_an_object(self):
        errors = self.errors([1, 2])
        self.assertTrue(any("must be an object" in error for error in errors), errors)

    def test_unknown_site_field_is_a_warning(self):
        data_dir = write_data(self.tmp / "data", site=site_data(extraField="x"))
        media_dir = write_media(self.tmp / "media")
        report = build.Report()
        build.load_data(data_dir, media_dir, report)
        self.assertEqual(report.errors, [])
        self.assertTrue(any("extraField" in w for w in report.warnings), report.warnings)


class MediaAndLoadingTests(TempDirTestCase):
    def test_media_files_counted(self):
        data_dir = write_data(self.tmp / "data")
        media_dir = write_media(self.tmp / "media")
        report = build.Report()
        build.load_data(data_dir, media_dir, report)
        self.assertEqual(report.media_files, len(support.MEDIA_FILES))

    def test_media_is_not_checked_without_a_media_dir(self):
        data_dir = write_data(self.tmp / "data")
        site, invitations = build.load_data(data_dir)
        self.assertEqual(len(invitations), 3)
        self.assertIn("venue", site)

    def test_missing_media_directory_with_references(self):
        data_dir = write_data(self.tmp / "data")
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir, self.tmp / "no-media")
        errors = caught.exception.errors
        self.assertTrue(any("media directory not found" in e for e in errors), errors)
        # the missing files are named as well
        self.assertTrue(any("clip.mp4" in e for e in errors), errors)

    def test_missing_media_directory_is_fine_without_references(self):
        # venue.ready = false and no video: the data set does not need media
        # at all, so a missing --media directory must not fail the build.
        site = site_data(video=None, schedule=[])
        site["venue"] = {"ready": False, "photos": [], "directionsImage": ""}
        data_dir = write_data(self.tmp / "data", site=site)
        report = build.Report()
        build.load_data(data_dir, self.tmp / "no-media", report)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.media_files, 0)

    def test_missing_data_directory(self):
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(self.tmp / "nowhere")
        self.assertIn("data directory not found", caught.exception.errors[0])

    def test_missing_data_file(self):
        data_dir = self.tmp / "data"
        write_json(data_dir / "site.json", site_data())
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir)
        self.assertTrue(
            any("invitations.json: file not found" in e for e in caught.exception.errors)
        )

    def test_invalid_json(self):
        data_dir = self.tmp / "data"
        write_data(data_dir)
        (data_dir / "invitations.json").write_text("[{", encoding="utf-8")
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir)
        self.assertTrue(any("invalid JSON" in e for e in caught.exception.errors))

    def test_data_that_is_not_utf8(self):
        data_dir = write_data(self.tmp / "data")
        (data_dir / "invitations.json").write_bytes(b'[{"greeting": "\xff\xfe"}]')
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir)
        self.assertTrue(
            any("invitations.json: not valid UTF-8" in e for e in caught.exception.errors),
            caught.exception.errors,
        )

    def test_nan_is_rejected(self):
        data_dir = self.tmp / "data"
        write_data(data_dir)
        site = json.loads((data_dir / "site.json").read_text(encoding="utf-8"))
        site["venue"]["geo"] = {"lat": 1.0, "lng": 2.0}
        raw = json.dumps(site, ensure_ascii=False).replace('"lat": 1.0', '"lat": NaN')
        (data_dir / "site.json").write_text(raw, encoding="utf-8")
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir)
        self.assertTrue(any("NaN" in e for e in caught.exception.errors))

    def test_utf8_bom_is_tolerated(self):
        data_dir = write_data(self.tmp / "data")
        raw = (data_dir / "site.json").read_text(encoding="utf-8")
        (data_dir / "site.json").write_text("\ufeff" + raw, encoding="utf-8")
        build.load_data(data_dir)

    def test_error_messages_contain_no_guest_data(self):
        invitations = invitations_data()
        invitations[1]["token"] = invitations[0]["token"]
        del invitations[2]["greeting"]
        errors = errors_of(invitations=invitations, tmp=self.tmp)
        joined = "\n".join(errors)
        for secret in support.PRIVATE_STRINGS:
            self.assertNotIn(secret, joined)
        self.assertIn(f"invitation #2 ({support.TOKEN_A[:4]}…)", joined)


class ContextTests(unittest.TestCase):
    def test_invitation_and_site_fields_are_at_the_root(self):
        context = build.build_context(site_data(), invitations_data(1)[0])
        self.assertEqual(context["greeting"], support.GREETING_TY)
        self.assertEqual(context["coupleNames"], support.COUPLE_NAMES)
        self.assertEqual(context["mediaDir"], site_data()["mediaDir"])
        self.assertIs(context["ty"], True)
        self.assertIs(context["vy"], False)

    def test_optional_fields_are_normalised(self):
        invitation = invitations_data()[1]  # note: null, no travelNote key
        context = build.build_context(site_data(), invitation)
        self.assertEqual(context["note"], "")
        self.assertEqual(context["travelNote"], "")
        self.assertEqual(build.render("<!-- if:note -->{{note}}<!-- endif -->", context), "")

    def test_video_is_an_empty_object_when_absent(self):
        absent = site_data()
        del absent["video"]
        for site in (absent, site_data(video=None)):
            context = build.build_context(site, invitations_data(1)[0])
            self.assertEqual(
                context["video"], {"file": "", "poster": "", "src": "", "posterSrc": ""}
            )
            self.assertEqual(
                build.render("<!-- if:video.file -->x<!-- endif -->", context), ""
            )
            # the fields resolve even outside of a condition
            self.assertEqual(build.render("[{{video.src}}]", context), "[]")

    def test_venue_is_fully_normalised(self):
        site = site_data()
        site["venue"] = {"ready": False}
        context = build.build_context(site, invitations_data(1)[0])
        venue = context["venue"]
        self.assertEqual(venue["photos"], [])
        self.assertEqual(venue["name"], "")
        self.assertIsNone(venue["geo"])
        self.assertEqual(venue["maps"], {"googlePlaceId": "", "yandexOrgId": ""})
        self.assertEqual(
            build.render("<!-- each:venue.photos -->x<!-- endeach -->", context), ""
        )

    def test_schedule_text_is_normalised(self):
        context = build.build_context(site_data(), invitations_data(1)[0])
        self.assertEqual(context["schedule"][1]["text"], "")

    def test_contexts_are_independent(self):
        site = site_data()
        first = build.build_context(site, invitations_data(1)[0])
        first["venue"]["name"] = "changed"
        second = build.build_context(site, invitations_data(1)[0])
        self.assertNotEqual(second["venue"]["name"], "changed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
