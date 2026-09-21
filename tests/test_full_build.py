"""End-to-end tests of the complete output: contents, privacy, reproducibility."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from tests import support
from tests.support import (
    CliTestCase,
    build,
    invitations_data,
    site_data,
    tree_digest,
    tree_files,
    write_data,
)

MEDIA = f"assets/{support.MEDIA_DIR}"
TOKENS = (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C)

EXPECTED_HEADERS = """/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
/i/*
  Cache-Control: private, no-cache
"""


def pending_site() -> dict:
    """`venue.ready` is false, no video: a data set that needs no media."""
    site = site_data()
    del site["video"]
    site["venue"] = {
        "ready": False,
        "name": "",
        "description": "",
        "address": "",
        "photos": [],
        "directionsImage": "",
    }
    return site


class FullBuildTestCase(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        # things that live next to the site but are not part of it
        (self.code / "styleguide.html").write_text("<p>styleguide</p>", encoding="utf-8")
        assets = self.code / "assets"
        (assets / "styleguide.css").write_text("/* sg */\n", encoding="utf-8")
        (assets / "styleguide.js").write_text("// sg\n", encoding="utf-8")
        (assets / "vendor" / "Styleguide.extra.css").write_text("/* sg */\n", encoding="utf-8")
        (assets / "empty" / "nested").mkdir(parents=True)
        # media that the data does not refer to
        (self.media / "unused.webp").write_bytes(b"")
        (self.media / "notes.txt").write_text("not for publishing", encoding="utf-8")
        (self.media / ".DS_Store").write_bytes(b"\x00")

    def build(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def page(self, token: str) -> str:
        return (self.out / "i" / token / "index.html").read_text(encoding="utf-8")


class OutputContentsTests(FullBuildTestCase):
    def test_snapshot_with_video_and_venue(self):
        self.build()
        self.assertEqual(
            tree_files(self.out),
            sorted(
                [
                    "404.html",
                    "_headers",
                    "assets/app.css",
                    "assets/favicon.svg",
                    "assets/og.png",
                    "assets/vendor/lib.js",
                    f"{MEDIA}/clip.mp4",
                    f"{MEDIA}/event.ics",
                    f"{MEDIA}/poster.jpg",
                    f"{MEDIA}/route.png",
                    f"{MEDIA}/venue-1.webp",
                    f"{MEDIA}/venue-2.webp",
                    *(f"i/{token}/index.html" for token in TOKENS),
                    "index.html",
                    "robots.txt",
                ]
            ),
        )

    def test_snapshot_without_venue_video_and_media_directory(self):
        write_data(self.data, site=pending_site())
        shutil.rmtree(self.media)
        self.build()
        self.assertEqual(
            tree_files(self.out),
            sorted(
                [
                    "404.html",
                    "_headers",
                    "assets/app.css",
                    "assets/favicon.svg",
                    "assets/og.png",
                    "assets/vendor/lib.js",
                    f"{MEDIA}/event.ics",
                    *(f"i/{token}/index.html" for token in TOKENS),
                    "index.html",
                    "robots.txt",
                ]
            ),
        )
        page = self.page(support.TOKEN_A)
        self.assertIn("Подробности сообщим позже", page)
        self.assertNotIn("<video", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("maps", page)
        self.assertIn(f'href="/{MEDIA}/event.ics"', page)

    def test_top_level_matches_the_allow_list(self):
        self.build()
        self.assertEqual({path.name for path in self.out.iterdir()}, build.OUTPUT_TOP_LEVEL)

    def test_headers_and_robots(self):
        self.build()
        self.assertEqual((self.out / "_headers").read_bytes(), EXPECTED_HEADERS.encode())
        self.assertEqual(
            (self.out / "robots.txt").read_bytes(), b"User-agent: *\nDisallow: /\n"
        )
        self.assertEqual(list(self.out.rglob("sitemap*")), [])

    def test_calendar_file(self):
        self.build()
        self.assertEqual(
            (self.out / MEDIA / "event.ics").read_bytes(), build.build_ics(site_data())
        )

    def test_media_files_are_copied_as_they_are(self):
        (self.media / "clip.mp4").write_bytes(b"\x00\x01video-bytes\xff")
        self.build()
        self.assertEqual((self.out / MEDIA / "clip.mp4").read_bytes(), b"\x00\x01video-bytes\xff")

    def test_media_name_with_spaces_and_cyrillic(self):
        site = site_data()
        site["venue"]["photos"] = ["зал 1.webp"]
        write_data(self.data, site=site)
        (self.media / "зал 1.webp").write_bytes(b"")
        self.build()
        self.assertTrue((self.out / MEDIA / "зал 1.webp").is_file())
        self.assertIn(
            f'src="/{MEDIA}/%D0%B7%D0%B0%D0%BB%201.webp"', self.page(support.TOKEN_A)
        )

    def test_pages_use_the_computed_fields(self):
        self.build()
        page = self.page(support.TOKEN_A)
        for fragment in (
            f'<img src="/{MEDIA}/venue-1.webp"',
            f'<img src="/{MEDIA}/venue-2.webp"',
            f'<img src="/{MEDIA}/route.png"',
            f'<video src="/{MEDIA}/clip.mp4" poster="/{MEDIA}/poster.jpg"',
            f'href="/{MEDIA}/event.ics"',
            f'data-media="/{MEDIA}"',
            'href="https://www.google.com/maps/search/?api=1&amp;query=10.5,20.25"',
            'href="https://yandex.ru/maps/?pt=20.25,10.5&amp;z=16"',
            'href="https://maps.apple.com/?ll=10.5,20.25&amp;q=',
        ):
            self.assertIn(fragment, page)

    def test_comment_markers_in_guest_text_are_just_text(self):
        invitations = invitations_data()
        invitations[0]["note"] = "a <!-- b"
        invitations[0]["greeting"] = "Ева --> !"
        write_data(self.data, invitations=invitations)
        self.build()
        page = self.page(support.TOKEN_A)
        self.assertIn("a &lt;!-- b", page)
        self.assertIn("Ева --&gt; !", page)
        self.assertNotIn("<!--", page)
        self.assertIn("</html>", page)  # nothing was cut out

    def test_markup_in_every_text_field_is_just_text(self):
        hostile = '<img src=https://h.example.invalid/x> <!-- <![CDATA[ <?php </p> {{x}}'
        site = site_data(
            coupleNames=hostile, dateText=hostile, rsvpDeadline=hostile, outOfTownText=hostile
        )
        site["venue"].update(name=hostile, description=hostile, address=hostile)
        for entry in site["schedule"]:
            entry.update(time=hostile, title=hostile, text=hostile)
        invitations = invitations_data()
        for invitation in invitations:
            invitation.update(greeting=hostile, note=hostile, travelNote=hostile, outOfTown=True)
        write_data(self.data, site=site, invitations=invitations)
        self.build()
        page = self.page(support.TOKEN_A)
        self.assertNotIn("<img src=https", page)
        self.assertGreater(page.count("&lt;img src=https://h.example.invalid/x&gt;"), 10)
        self.assertNotIn("h.example.invalid", (self.out / "index.html").read_text(encoding="utf-8"))

    def test_html_comments_are_removed(self):
        self.assertIn("<!-- a note", support.TEMPLATE)
        self.assertIn("<!-- a note", support.STUB)
        self.build()
        for path in self.out.rglob("*.html"):
            self.assertNotIn("<!--", path.read_text(encoding="utf-8"), path)

    def test_log_has_counters_and_no_data(self):
        result = self.build()
        self.assertIn("3 invitation(s), 5 media file(s) validated", result.stdout)
        self.assertIn("3 page(s)", result.stdout)
        self.assertIn("2 asset file(s)", result.stdout)
        last = result.stdout.strip().splitlines()[-1]
        self.assertRegex(
            last,
            r"\Abuild: OK -> dist \(3 page\(s\), 5 media file\(s\), "
            r"17 file\(s\) in total, \d+(\.\d)? (B|KiB|MiB)\)\Z",
        )
        self.assertEqual(result.stderr, "")
        self.assertNoPrivateData(result.stdout, result.stderr)
        self.assertNotIn(support.MEDIA_DIR, result.stdout)


class StubTests(FullBuildTestCase):
    def test_index_and_404_are_identical(self):
        self.build()
        index = (self.out / "index.html").read_bytes()
        self.assertEqual(index, (self.out / "404.html").read_bytes())
        self.assertIn("Такой страницы нет.", index.decode("utf-8"))

    def test_stub_gets_no_data(self):
        self.build()
        stub = (self.out / "index.html").read_text(encoding="utf-8")
        for secret in (*support.PRIVATE_STRINGS, support.MEDIA_DIR, "/i/", "2030"):
            self.assertNotIn(secret, stub)

    def test_stub_is_the_same_for_every_data_set(self):
        self.build()
        first = (self.out / "index.html").read_bytes()
        write_data(self.data, site=pending_site(), invitations=invitations_data(1))
        self.build()
        self.assertEqual((self.out / "index.html").read_bytes(), first)


class PrivacyTests(FullBuildTestCase):
    """A guest's data is found on that guest's page and nowhere else."""

    def contents(self) -> dict[str, bytes]:
        return {name: (self.out / name).read_bytes() for name in tree_files(self.out)}

    def test_guest_data_stays_on_the_guest_page(self):
        self.build()
        contents = self.contents()
        checked = 0
        for invitation in invitations_data():
            own_page = f"i/{invitation['token']}/index.html"
            for field in ("greeting", "note", "travelNote"):
                value = invitation.get(field)
                if not value:
                    continue
                self.assertIn(value.encode(), contents[own_page], field)
                for name, content in contents.items():
                    if name != own_page:
                        self.assertNotIn(value.encode(), content, f"{field} in {name}")
                checked += 1
        self.assertEqual(checked, 5)  # 3 greetings, 1 note, 1 travel note

    def test_tokens_stay_out_of_other_files(self):
        self.build()
        contents = self.contents()
        for token in TOKENS:
            own_page = f"i/{token}/index.html"
            for name, content in contents.items():
                if name != own_page:
                    self.assertNotIn(token.encode(), content, name)
                    self.assertNotIn(token.lower().encode(), content.lower(), name)
            paths = [name for name in contents if token in name]
            self.assertEqual(paths, [own_page])

    def test_names_stay_on_the_pages(self):
        self.build()
        contents = self.contents()
        self.assertIn(support.COUPLE_NAMES.encode(), contents[f"i/{support.TOKEN_A}/index.html"])
        for name, content in contents.items():
            if name.startswith("i/"):
                continue
            for secret in (support.COUPLE_NAMES, "Алиса", "Боб", support.DATE_TEXT):
                self.assertNotIn(secret.encode(), content, f"{secret!r} in {name}")

    def test_no_data_files_in_the_output(self):
        self.build()
        for name in tree_files(self.out):
            self.assertFalse(
                name.endswith((".json", ".map", ".py", ".md")) or "/." in f"/{name}", name
            )


class ReproducibilityTests(FullBuildTestCase):
    def test_two_builds_are_byte_identical(self):
        self.build()
        first = tree_digest(self.out)
        self.assertEqual(len(first), 17)
        self.build()  # replaces the previous output
        self.assertEqual(tree_digest(self.out), first)

        other = self.work / "second"
        result = support.run_cli(
            self.code, "build", "--data", str(self.data), "--media", str(self.media),
            "--out", str(other), cwd=self.work,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree_digest(other), first)

    def test_in_process_build_matches_the_command_line(self):
        self.build()
        expected = tree_digest(self.out)
        other = self.work / "in-process"
        stats = build.build_site(
            self.data, self.media, other, code_dir=self.code, log=lambda _line: None
        )
        self.assertEqual(tree_digest(other), expected)
        self.assertEqual(stats.files, 17)
        self.assertEqual(
            stats.size, sum(path.stat().st_size for path in other.rglob("*") if path.is_file())
        )


class ExampleDataTests(CliTestCase):
    """The example data sets of the repository build with the fixture template."""

    def build_example(self, name: str, media_names=()) -> Path:
        data = support.ROOT / "examples" / name
        media = self.tmp / f"media-{name}"
        support.write_media(media, media_names)
        result = support.run_cli(
            self.code, "build", "--data", str(data), "--media", str(media),
            "--out", str(self.out), cwd=self.work,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return self.out

    def test_example_with_venue_and_video(self):
        site, invitations = build.load_data(support.ROOT / "examples" / "data")
        names = sorted({name for _field, name in build.media_references(site)})
        self.assertTrue(names)
        out = self.build_example("data", names)
        media = out / "assets" / site["mediaDir"]
        self.assertEqual(sorted(p.name for p in media.iterdir()), sorted([*names, "event.ics"]))
        self.assertEqual(len(list((out / "i").iterdir())), len(invitations))

    def test_example_with_pending_venue(self):
        site, _invitations = build.load_data(support.ROOT / "examples" / "data-venue-pending")
        out = self.build_example("data-venue-pending")
        media = out / "assets" / site["mediaDir"]
        self.assertEqual([p.name for p in media.iterdir()], ["event.ics"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
