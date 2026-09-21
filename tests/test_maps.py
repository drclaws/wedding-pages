"""Links to the map services: which one a place gets, and how it is encoded.

Every id and query here is made up.
"""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from tests.support import build
from tools import _maps

NAME = "Усадьба «Образец»"
ADDRESS = "г. Энск, ул. Примерная, д. 1"
GEO = {"lat": 12.5, "lng": 23.25}
QUERY = "Гостевой вход, Энск"
#: QUERY and NAME, percent-encoded as UTF-8
Q = (
    "%D0%93%D0%BE%D1%81%D1%82%D0%B5%D0%B2%D0%BE%D0%B9%20"
    "%D0%B2%D1%85%D0%BE%D0%B4%2C%20"
    "%D0%AD%D0%BD%D1%81%D0%BA"
)
N = (
    "%D0%A3%D1%81%D0%B0%D0%B4%D1%8C%D0%B1%D0%B0%20"
    "%C2%AB%D0%9E%D0%B1%D1%80%D0%B0%D0%B7%D0%B5%D1%86%C2%BB"
)

GOOGLE = "https://www.google.com/maps/search/?api=1&query="
YANDEX = "https://yandex.ru/maps/"
APPLE = "https://maps.apple.com/"

ALL_IDS = {
    "googlePlaceId": "EXAMPLE-not-a-real-place-id",
    "yandexOrgId": "1000000001",
    "applePlaceId": "IEXAMPLE0000001",
}
ALL_QUERIES = {"googleQuery": QUERY, "yandexQuery": QUERY, "appleQuery": QUERY}


def links(maps, geo=GEO, name=NAME, address=ADDRESS):
    return _maps.map_links(name, address, geo, maps)


class OrderTests(unittest.TestCase):
    """id -> the query of the service -> no link; coordinates alone give none."""

    def test_nothing_set_gives_no_links(self):
        for maps in (None, {}, {"googlePlaceId": ""}, {"yandexQuery": "   "}, "x", []):
            with self.subTest(maps=maps):
                result = links(maps)
                self.assertEqual(result, {"google": "", "yandex": "", "apple": ""})
                self.assertFalse(_maps.has_map_links(result))

    def test_coordinates_alone_give_no_links(self):
        self.assertFalse(_maps.has_map_links(links({}, geo=GEO)))
        self.assertFalse(_maps.has_map_links(links(None, geo={"lat": 1, "lng": 2}, name="")))

    def test_ids_win_over_queries(self):
        result = links({**ALL_IDS, **ALL_QUERIES})
        self.assertEqual(
            result["google"],
            f"{GOOGLE}{Q}&query_place_id=EXAMPLE-not-a-real-place-id",
        )
        self.assertEqual(result["yandex"], f"{YANDEX}org/1000000001")
        self.assertEqual(
            result["apple"],
            f"{APPLE}place?place-id=IEXAMPLE0000001&coordinate=12.5,23.25&name={N}",
        )

    def test_queries_when_there_is_no_id(self):
        result = links(ALL_QUERIES)
        self.assertEqual(result["google"], f"{GOOGLE}{Q}")
        self.assertEqual(result["yandex"], f"{YANDEX}?text={Q}&ll=23.25,12.5&z=16")
        self.assertEqual(result["apple"], f"{APPLE}search?query={Q}&center=12.5,23.25")

    def test_each_service_only_takes_its_own_fields(self):
        cases = {
            "googlePlaceId": "google",
            "googleQuery": "google",
            "yandexOrgId": "yandex",
            "yandexGeoId": "yandex",
            "yandexQuery": "yandex",
            "applePlaceId": "apple",
            "appleQuery": "apple",
        }
        for key, service in cases.items():
            value = QUERY if key.endswith("Query") else "1234567890"
            with self.subTest(key=key):
                result = links({key: value})
                self.assertTrue(result[service])
                self.assertEqual([k for k, v in result.items() if v], [service])

    def test_yandex_geo_object(self):
        self.assertEqual(links({"yandexGeoId": "1234567890"})["yandex"], f"{YANDEX}geo/1234567890/")
        self.assertEqual(
            links({"yandexGeoId": "1234567890", "yandexQuery": QUERY})["yandex"],
            f"{YANDEX}geo/1234567890/",
        )

    def test_ids_are_trimmed(self):
        result = links({"yandexOrgId": " 1000000001 ", "googleQuery": f"  {QUERY}\t"})
        self.assertEqual(result["yandex"], f"{YANDEX}org/1000000001")
        self.assertEqual(result["google"], f"{GOOGLE}{Q}")


class CompletionTests(unittest.TestCase):
    """What the name, the address and the coordinates add to a link."""

    def test_queries_without_coordinates_have_no_area_hint(self):
        for geo in (None, {}, {"lat": 0, "lng": 0}, {"lat": 100, "lng": 1}):
            with self.subTest(geo=geo):
                result = links(ALL_QUERIES, geo=geo)
                self.assertEqual(result["google"], f"{GOOGLE}{Q}")
                self.assertEqual(result["yandex"], f"{YANDEX}?text={Q}")
                self.assertEqual(result["apple"], f"{APPLE}search?query={Q}")

    def test_google_query_of_a_place_id(self):
        place = {"googlePlaceId": "EXAMPLE_id-1"}
        suffix = "&query_place_id=EXAMPLE_id-1"
        self.assertEqual(links(place)["google"], f"{GOOGLE}{N}{suffix}")
        self.assertEqual(links(place, name="")["google"], f"{GOOGLE}12.5,23.25{suffix}")
        self.assertEqual(
            links(place, name="", geo=None, address="Энск")["google"],
            f"{GOOGLE}%D0%AD%D0%BD%D1%81%D0%BA{suffix}",
        )
        self.assertEqual(links(place, name="", geo=None, address=" ")["google"], "")

    def test_apple_place_id_without_name_or_coordinates(self):
        self.assertEqual(
            links({"applePlaceId": "IEXAMPLE0000001"}, geo=None, name="")["apple"],
            f"{APPLE}place?place-id=IEXAMPLE0000001",
        )
        self.assertEqual(
            links({"applePlaceId": "IEXAMPLE0000001"}, geo=None)["apple"],
            f"{APPLE}place?place-id=IEXAMPLE0000001&name={N}",
        )

    def test_coordinates_are_plain_decimals(self):
        result = links({"yandexQuery": "x"}, geo={"lat": 1e-05, "lng": -10.0})
        self.assertEqual(result["yandex"], f"{YANDEX}?text=x&ll=-10,0.00001&z=16")


class EncodingTests(unittest.TestCase):
    """No character of a query or an id can break the URL."""

    NASTY = 'Вход «А» & Б #2? a=b/c+d%20 "e" \'f\' <g> \\h'

    def test_every_character_is_encoded(self):
        result = links({key: self.NASTY for key in ALL_QUERIES}, geo=None)
        for service, url in result.items():
            with self.subTest(service=service):
                parts = urlsplit(url)
                self.assertEqual(parts.fragment, "")
                query = parse_qs(parts.query, keep_blank_values=True)
                key = "text" if service == "yandex" else "query"
                self.assertEqual(query[key], [self.NASTY])
                self.assertFalse(set(url) & set(" #\"'<>\\«»"))
                self.assertEqual(url.count("?"), 1)

    def test_query_does_not_add_parameters(self):
        url = links({"appleQuery": "a&center=0,0"})["apple"]
        self.assertEqual(parse_qs(urlsplit(url).query)["center"], ["12.5,23.25"])
        self.assertEqual(unquote(url.split("query=")[1].split("&")[0]), "a&center=0,0")

    def test_ids_are_encoded_too(self):
        # the schema only lets letters and digits through; the links stay safe anyway
        result = links({"googlePlaceId": "a/b", "yandexGeoId": "../1", "applePlaceId": "a&b"})
        self.assertIn("query_place_id=a%2Fb", result["google"])
        self.assertEqual(result["yandex"], f"{YANDEX}geo/..%2F1/")
        self.assertIn("place-id=a%26b&", result["apple"])


class OutputCheckTests(unittest.TestCase):
    """The checks of the output let every map link through, and nothing else."""

    def test_every_generated_link_is_a_map_link(self):
        variants = (
            {**ALL_IDS, **ALL_QUERIES},
            ALL_QUERIES,
            {"yandexGeoId": "1234567890"},
            {key: EncodingTests.NASTY for key in ALL_QUERIES},
        )
        for maps in variants:
            for geo in (GEO, None):
                for service, url in links(maps, geo=geo).items():
                    if not url:
                        continue
                    with self.subTest(maps=maps, geo=geo, service=service):
                        self.assertTrue(build._is_map_link(url), url)

    def test_other_hosts_stay_out(self):
        for url in (
            "https://yandex.com/maps/geo/1234567890/",
            "https://maps.yandex.ru/?text=x",
            "https://maps.google.com/?q=x",
            "https://www.google.com/search?q=x",
            "https://apple.com/maps/place?place-id=IEXAMPLE0000001",
            "http://maps.apple.com/search?query=x",
            "https://maps.apple.com.example.invalid/search?query=x",
        ):
            with self.subTest(url=url):
                self.assertFalse(build._is_map_link(url))


if __name__ == "__main__":
    unittest.main()
