"""Tests for the computed template fields: media paths and map links."""

from __future__ import annotations

import unittest

from tests import support
from tests.support import build, invitations_data, site_data

NAME = "Усадьба «Образец» у пруда"
NAME_ENCODED = (
    "%D0%A3%D1%81%D0%B0%D0%B4%D1%8C%D0%B1%D0%B0%20%C2%AB%D0%9E%D0%B1%D1%80%D0%B0"
    "%D0%B7%D0%B5%D1%86%C2%BB%20%D1%83%20%D0%BF%D1%80%D1%83%D0%B4%D0%B0"
)
GEO = {"lat": 12.5, "lng": -23.25}
GOOGLE = "https://www.google.com/maps/search/?api=1"


def links(name=NAME, address="", geo=None, google="", yandex="") -> dict:
    return build.map_links(
        name, address, geo, {"googlePlaceId": google, "yandexOrgId": yandex}
    )


class MapLinkTests(unittest.TestCase):
    def test_ids_and_coordinates(self):
        self.assertEqual(
            links(geo=GEO, google="Place_Id-123", yandex="1234567890"),
            {
                "google": f"{GOOGLE}&query={NAME_ENCODED}&query_place_id=Place_Id-123",
                "yandex": "https://yandex.ru/maps/org/1234567890",
                "apple": f"https://maps.apple.com/?ll=12.5,-23.25&q={NAME_ENCODED}",
            },
        )

    def test_ids_without_coordinates(self):
        self.assertEqual(
            links(google="Place_Id-123", yandex="1234567890"),
            {
                "google": f"{GOOGLE}&query={NAME_ENCODED}&query_place_id=Place_Id-123",
                "yandex": "https://yandex.ru/maps/org/1234567890",
                "apple": "",  # Apple needs coordinates
            },
        )

    def test_coordinates_without_ids(self):
        self.assertEqual(
            links(geo=GEO),
            {
                "google": f"{GOOGLE}&query=12.5,-23.25",
                # Yandex wants the longitude first
                "yandex": "https://yandex.ru/maps/?pt=-23.25,12.5&z=16",
                "apple": f"https://maps.apple.com/?ll=12.5,-23.25&q={NAME_ENCODED}",
            },
        )

    def test_nothing_to_link_to(self):
        empty = {"google": "", "yandex": "", "apple": ""}
        self.assertEqual(links(), empty)
        self.assertEqual(links(geo=None), empty)
        self.assertEqual(build.map_links(NAME, "", None, None), empty)

    def test_zero_coordinates_mean_not_set(self):
        for geo in ({"lat": 0, "lng": 0}, {"lat": 0.0, "lng": -0.0}):
            with self.subTest(geo=geo):
                self.assertEqual(links(geo=geo), {"google": "", "yandex": "", "apple": ""})
                self.assertEqual(
                    links(geo=geo, yandex="42")["yandex"], "https://yandex.ru/maps/org/42"
                )
        # a single zero is a real place (the equator or the prime meridian)
        self.assertEqual(
            links(geo={"lat": 0, "lng": 20})["yandex"],
            "https://yandex.ru/maps/?pt=20,0&z=16",
        )

    def test_one_id_only(self):
        result = links(geo=GEO, yandex="1234567890")
        self.assertEqual(result["google"], f"{GOOGLE}&query=12.5,-23.25")
        self.assertEqual(result["yandex"], "https://yandex.ru/maps/org/1234567890")
        result = links(geo=GEO, google="Place_Id-123")
        self.assertTrue(result["google"].endswith("&query_place_id=Place_Id-123"))
        self.assertEqual(result["yandex"], "https://yandex.ru/maps/?pt=-23.25,12.5&z=16")

    def test_identifiers_and_names_are_url_encoded(self):
        result = links(
            name="A&B / C?d=1#e +f", geo=GEO, google="id &=/?#", yandex="org/../x y"
        )
        self.assertEqual(
            result["google"],
            f"{GOOGLE}&query=A%26B%20%2F%20C%3Fd%3D1%23e%20%2Bf"
            "&query_place_id=id%20%26%3D%2F%3F%23",
        )
        self.assertEqual(result["yandex"], "https://yandex.ru/maps/org/org%2F..%2Fx%20y")
        self.assertTrue(result["apple"].endswith("&q=A%26B%20%2F%20C%3Fd%3D1%23e%20%2Bf"))

    def test_surrounding_whitespace_is_ignored(self):
        result = links(name="  Место  ", geo=GEO, google="  pid  ", yandex=" 7 ")
        self.assertEqual(
            result["google"],
            f"{GOOGLE}&query=%D0%9C%D0%B5%D1%81%D1%82%D0%BE&query_place_id=pid",
        )
        self.assertEqual(result["yandex"], "https://yandex.ru/maps/org/7")

    def test_place_id_without_a_name_falls_back(self):
        self.assertEqual(
            links(name="", geo=GEO, google="pid")["google"],
            f"{GOOGLE}&query=12.5,-23.25&query_place_id=pid",
        )
        self.assertEqual(
            links(name="", address="Энск, 1", google="pid")["google"],
            f"{GOOGLE}&query=%D0%AD%D0%BD%D1%81%D0%BA%2C%201&query_place_id=pid",
        )
        self.assertEqual(links(name="", google="pid")["google"], "")
        # no name: the Apple link is just the pin
        self.assertEqual(
            links(name="", geo=GEO)["apple"], "https://maps.apple.com/?ll=12.5,-23.25"
        )

    def test_coordinates_never_use_exponent_notation(self):
        result = links(geo={"lat": 1e-05, "lng": 100})
        self.assertEqual(result["google"], f"{GOOGLE}&query=0.00001,100")
        self.assertEqual(result["yandex"], "https://yandex.ru/maps/?pt=100,0.00001&z=16")
        cases = {
            12.345678: "12.345678",
            -179.999999: "-179.999999",
            10: "10",
            10.0: "10",
            1e-07: "0.0000001",
            -0.0: "0",
            -5e-05: "-0.00005",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(build.format_coordinate(value), expected)

    def test_unusable_coordinates_mean_not_set(self):
        for geo in (
            None,
            {},
            {"lat": 10},
            {"lat": "10", "lng": "20"},
            {"lat": True, "lng": 20},
            {"lat": 91, "lng": 20},
            {"lat": 10, "lng": float("inf")},
        ):
            with self.subTest(geo=geo):
                self.assertIsNone(build.venue_coordinates(geo))
        self.assertEqual(build.venue_coordinates(GEO), ("12.5", "-23.25"))


class ComputedFieldTests(unittest.TestCase):
    def context(self, site=None) -> dict:
        return build.build_context(site or site_data(), invitations_data(1)[0])

    def test_media_and_calendar_paths(self):
        context = self.context()
        self.assertEqual(context["mediaPath"], f"/assets/{support.MEDIA_DIR}")
        self.assertEqual(context["icsPath"], f"/assets/{support.MEDIA_DIR}/event.ics")

    def test_video_sources(self):
        self.assertEqual(
            self.context()["video"],
            {
                "file": "clip.mp4",
                "poster": "poster.jpg",
                "src": f"/assets/{support.MEDIA_DIR}/clip.mp4",
                "posterSrc": f"/assets/{support.MEDIA_DIR}/poster.jpg",
            },
        )

    def test_photos_become_objects_and_empty_names_are_dropped(self):
        site = site_data()
        site["venue"]["photos"] = ["venue-1.webp", "", "   ", "venue-2.webp"]
        context = self.context(site)
        self.assertEqual(
            context["venue"]["photos"],
            [
                {"src": f"/assets/{support.MEDIA_DIR}/venue-1.webp"},
                {"src": f"/assets/{support.MEDIA_DIR}/venue-2.webp"},
            ],
        )
        rendered = build.render(
            "<!-- each:venue.photos -->[{{.src}}]<!-- endeach -->", context
        )
        self.assertEqual(
            rendered,
            f"[/assets/{support.MEDIA_DIR}/venue-1.webp][/assets/{support.MEDIA_DIR}/venue-2.webp]",
        )

    def test_directions_image(self):
        context = self.context()
        self.assertEqual(
            context["venue"]["directionsSrc"], f"/assets/{support.MEDIA_DIR}/route.png"
        )
        site = site_data()
        site["venue"]["directionsImage"] = ""
        self.assertEqual(self.context(site)["venue"]["directionsSrc"], "")
        del site["venue"]["directionsImage"]
        self.assertEqual(self.context(site)["venue"]["directionsSrc"], "")

    def test_file_names_are_percent_encoded_in_urls(self):
        site = site_data()
        site["venue"]["photos"] = ["зал 1 #2.webp"]
        context = self.context(site)
        self.assertEqual(
            context["venue"]["photos"][0]["src"],
            f"/assets/{support.MEDIA_DIR}/%D0%B7%D0%B0%D0%BB%201%20%232.webp",
        )

    def test_map_links_of_the_fixture(self):
        # the fixture has coordinates and empty ids
        self.assertEqual(
            self.context()["venue"]["mapLinks"],
            {
                "google": "https://www.google.com/maps/search/?api=1&query=10.5,20.25",
                "yandex": "https://yandex.ru/maps/?pt=20.25,10.5&z=16",
                "apple": "https://maps.apple.com/?ll=10.5,20.25&q="
                "%D0%A3%D1%81%D0%B0%D0%B4%D1%8C%D0%B1%D0%B0%20%D0%B2%20"
                "%D0%AD%D0%BD%D1%81%D0%BA%D0%B5",
            },
        )

    def test_map_links_in_a_page_are_html_escaped_once(self):
        rendered = build.render('<a href="{{venue.mapLinks.google}}">', self.context())
        self.assertEqual(
            rendered,
            '<a href="https://www.google.com/maps/search/?api=1&amp;query=10.5,20.25">',
        )

    def test_bare_venue_has_every_computed_field(self):
        site = site_data(video=None)
        site["venue"] = {"ready": False}
        context = self.context(site)
        self.assertEqual(context["venue"]["photos"], [])
        self.assertEqual(context["venue"]["directionsSrc"], "")
        self.assertEqual(
            context["venue"]["mapLinks"], {"google": "", "yandex": "", "apple": ""}
        )
        template = (
            "<!-- if:venue.mapLinks.google -->g<!-- endif -->"
            "<!-- if:venue.mapLinks.yandex -->y<!-- endif -->"
            "<!-- if:venue.mapLinks.apple -->a<!-- endif -->"
            "<!-- if:venue.directionsSrc -->d<!-- endif -->"
            "<!-- if:video.file -->v<!-- endif -->"
            "[{{icsPath}}]"
        )
        self.assertEqual(
            build.render(template, context), f"[/assets/{support.MEDIA_DIR}/event.ics]"
        )

    def test_fixture_template_uses_the_whole_contract(self):
        for field in (
            "{{mediaPath}}",
            "{{icsPath}}",
            "{{video.src}}",
            "{{video.posterSrc}}",
            "if:video.file",
            "each:venue.photos",
            "{{.src}}",
            "{{venue.directionsSrc}}",
            "if:venue.mapLinks.google",
            "if:venue.mapLinks.yandex",
            "if:venue.mapLinks.apple",
        ):
            self.assertIn(field, support.TEMPLATE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
