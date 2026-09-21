"""End-to-end tests of the complete output: contents, privacy, reproducibility."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests import fixtures_v2 as F
from tests import support
from tests.support import (
    CliTestCase,
    build,
    invitations_data,
    published_name,
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

#: Every file of the fixture build.
FILES_IN_TOTAL = 20


def pending_site() -> dict:
    """The place is not announced and there is no media: nothing to publish."""
    site = site_data()
    site["locations"] = {"manor": {"ready": False}}
    del site["media"]
    site["sections"] = [section for section in site["sections"] if section["id"] != "video"]
    return site


def calendar_files() -> list[str]:
    return [
        f"i/{token}/{event_id}.ics"
        for token, event_ids in support.VISIBLE_EVENTS.items()
        for event_id in event_ids
    ]


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
    def test_snapshot_with_places_and_media(self):
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
                    *(f"{MEDIA}/{published_name(name)}" for name in support.MEDIA_FILES),
                    *(f"i/{token}/index.html" for token in TOKENS),
                    *calendar_files(),
                    "index.html",
                    "robots.txt",
                ]
            ),
        )
        self.assertEqual(len(tree_files(self.out)), FILES_IN_TOTAL)

    def test_snapshot_without_places_media_and_media_directory(self):
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
                    *(f"i/{token}/index.html" for token in TOKENS),
                    *calendar_files(),
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
        self.assertNotIn(support.MEDIA_DIR, page)
        self.assertIn(f'href="/i/{support.TOKEN_A}/dinner.ics"', page)

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

    def test_no_shared_calendar_file(self):
        self.build()
        self.assertEqual(
            sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*.ics")),
            sorted(calendar_files()),
        )

    def test_calendar_files_are_those_of_the_tree(self):
        self.build()
        data = build.load_data(self.data)
        expected = build.calendar_files(data)
        self.assertEqual(
            {token: sorted(files) for token, files in expected.items()},
            {token: sorted(events) for token, events in support.VISIBLE_EVENTS.items()},
        )
        for token, files in expected.items():
            for event_id, content in files.items():
                self.assertEqual(
                    (self.out / "i" / token / f"{event_id}.ics").read_bytes(), content
                )

    def test_media_files_are_published_under_the_hash_of_their_contents(self):
        self.build()
        for name in support.MEDIA_FILES:
            with self.subTest(name=name):
                published = self.out / MEDIA / published_name(name)
                self.assertEqual(published.read_bytes(), support.media_bytes(name))
                self.assertRegex(published.name, r"\A[0-9a-f]{16}\.(png|mp4)\Z")
        names = [path.name for path in self.out.rglob("*")]
        for name in support.MEDIA_FILES:
            self.assertNotIn(name, names)
            for token in TOKENS:
                self.assertNotIn(name, self.page(token))

    def test_equal_files_are_published_once(self):
        site = site_data()
        site["media"]["venue-2"]["file"] = "venue-copy.png"
        write_data(self.data, site=site)
        (self.media / "venue-copy.png").write_bytes(support.media_bytes("venue-1.png"))
        self.build()
        self.assertEqual(len(list((self.out / MEDIA).iterdir())), len(support.MEDIA_FILES) - 1)
        page = self.page(support.TOKEN_A)
        # both photos of the place, in both cards of the events that take place there
        self.assertEqual(page.count(f'src="/{MEDIA}/{published_name("venue-1.png")}"'), 4)
        self.assertNotIn(published_name("venue-2.png"), page)

    def test_media_name_with_spaces_and_cyrillic(self):
        site = site_data()
        site["media"]["venue-1"]["file"] = "зал 1.png"
        write_data(self.data, site=site)
        (self.media / "зал 1.png").write_bytes(support.png_bytes(7, 5))
        self.build()
        published = build.media_tools.hashed_name(support.png_bytes(7, 5), "зал 1.png")
        self.assertTrue((self.out / MEDIA / published).is_file())
        page = self.page(support.TOKEN_A)
        self.assertIn(f'src="/{MEDIA}/{published}"', page)
        self.assertNotIn("%D0%B7", page)

    def test_media_shown_on_no_page_is_not_published(self):
        site = site_data()
        site["media"]["spare"] = {"type": "image", "file": "spare.png", "alt": "Запасное"}
        write_data(self.data, site=site)
        support.write_media(self.media, ["spare.png"])
        result = self.build()
        self.assertIn("media item 'spare' is not shown on any page", result.stderr)
        self.assertNotIn(
            published_name("spare.png"), [path.name for path in (self.out / MEDIA).iterdir()]
        )

    def test_pages_are_made_of_the_tree(self):
        self.build()
        page = self.page(support.TOKEN_A)
        video, poster = published_name("clip.mp4"), published_name("poster.png")
        for fragment in (
            f'<img src="/{MEDIA}/{published_name("venue-1.png")}"',
            f'<img src="/{MEDIA}/{published_name("venue-2.png")}"',
            f'<img src="/{MEDIA}/{published_name("route.png")}"',
            f'href="/{MEDIA}/{video}" type="video/mp4"',
            f'data-media-poster="/{MEDIA}/{poster}"',
            f'data-media-ratio="{support.VIDEO_SIZE[0]} / {support.VIDEO_SIZE[1]}"',
            "0:02",
            f'href="/i/{support.TOKEN_A}/dinner.ics"',
            f'href="/i/{support.TOKEN_A}/brunch.ics"',
            'href="https://www.google.com/maps/search/?api=1&amp;query=10.5,20.25"',
            'href="https://yandex.ru/maps/?pt=20.25,10.5&amp;z=16"',
            'href="https://maps.apple.com/?ll=10.5,20.25&amp;q=',
            '<h1 class="cover__title" id="s-cover--title">Алиса и Боб</h1>',
            "Приходи 1 июня 2030.",
        ):
            self.assertIn(fragment, page)
        self.assertIn("Приходите 1 июня 2030.", self.page(support.TOKEN_B))

    def test_comment_markers_in_guest_text_are_just_text(self):
        invitations = invitations_data()
        invitations[0]["sections"]["personal"]["note"] = "a <!-- b"
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
        site = site_data(coupleNames=hostile, rsvpDeadline=hostile)
        site["sections"][0]["eyebrow"] = hostile
        for section in site["sections"][1:]:
            section["title"] = hostile
        site["sections"][5]["widgets"][0]["text"] = hostile
        site["locations"]["manor"].update(name=hostile, description=hostile, address=hostile)
        for event in site["events"].values():
            event.update(title=hostile, description=hostile)
        for item in site["schedules"]["day"]:
            item.update(time=hostile, title=hostile, text=hostile)
        for item in site["media"].values():
            item["alt"] = hostile
        invitations = invitations_data()
        for invitation in invitations:
            invitation["greeting"] = hostile
            invitation["events"] = {"dinner": {"note": hostile}}
            invitation["sections"] = {
                "personal": {"note": hostile},
                "travel": {"visible": True, "note": hostile},
            }
        write_data(self.data, site=site, invitations=invitations)
        self.build()
        page = self.page(support.TOKEN_A)
        self.assertNotIn("<img src=https", page)
        self.assertGreater(page.count("&lt;img src=https://h.example.invalid/x&gt;"), 20)
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
        self.assertIn("3 page(s) and 4 calendar file(s)", result.stdout)
        self.assertIn("2 asset file(s)", result.stdout)
        self.assertIn("copied 5 media file(s) under the hash of their contents", result.stdout)
        last = result.stdout.strip().splitlines()[-1]
        self.assertRegex(
            last,
            r"\Abuild: OK -> dist \(3 page\(s\), 5 media file\(s\), "
            rf"{FILES_IN_TOTAL} file\(s\) in total, \d+(\.\d)? (B|KiB|MiB)\)\Z",
        )
        self.assertEqual(result.stderr, "")
        self.assertNoPrivateData(result.stdout, result.stderr)
        self.assertNotIn(support.MEDIA_DIR, result.stdout)
        for name in support.MEDIA_FILES:
            self.assertNotIn(name, result.stdout)


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
            values = [invitation["greeting"]] + [
                override["note"]
                for part in ("sections", "events")
                for override in invitation.get(part, {}).values()
                if "note" in override
            ]
            for value in values:
                self.assertIn(value.encode(), contents[own_page])
                for name, content in contents.items():
                    if name != own_page:
                        self.assertNotIn(value.encode(), content, f"{value!r} in {name}")
                checked += 1
        self.assertEqual(checked, 6)  # 3 greetings, 2 notes to sections, 1 to an event

    def test_a_hidden_event_is_only_where_it_is_shown(self):
        self.build()
        contents = self.contents()
        title = support.HIDDEN_EVENT_TITLE.encode()
        found = sorted(name for name, content in contents.items() if title in content)
        self.assertEqual(
            found, [f"i/{support.TOKEN_A}/brunch.ics", f"i/{support.TOKEN_A}/index.html"]
        )
        for token in (support.TOKEN_B, support.TOKEN_C):
            page = contents[f"i/{token}/index.html"]
            self.assertNotIn(b"brunch", page)
            self.assertNotIn(b"2030-06-02", page)

    def test_tokens_are_only_names_of_directories(self):
        self.build()
        contents = self.contents()
        for token in TOKENS:
            own_page = f"i/{token}/index.html"
            for name, content in contents.items():
                if name != own_page:  # the page links to its own calendar files
                    self.assertNotIn(token.encode(), content, name)
                    self.assertNotIn(token.lower().encode(), content.lower(), name)
            paths = sorted(name for name in contents if token in name)
            self.assertEqual(
                paths,
                sorted(
                    [own_page, *(f"i/{token}/{event}.ics" for event in support.VISIBLE_EVENTS[token])]
                ),
            )
            links = {
                part.split('"', 1)[0]
                for part in contents[own_page].decode().split(f"/{token}/")[1:]
            }
            self.assertEqual(links, {f"{event}.ics" for event in support.VISIBLE_EVENTS[token]})

    def test_calendar_files_carry_no_guest_data(self):
        self.build()
        for token, event_ids in support.VISIBLE_EVENTS.items():
            for event_id in event_ids:
                content = (self.out / "i" / token / f"{event_id}.ics").read_text(encoding="utf-8")
                for secret in (
                    support.GREETING_TY, support.GREETING_VY, support.GREETING_THIRD,
                    support.NOTE, support.TRAVEL_NOTE, support.EVENT_NOTE, support.COUPLE_NAMES,
                    *TOKENS,
                ):  # fmt: skip
                    self.assertNotIn(secret, content)
        # the file of an event is the same for everybody who sees it
        dinner = {(self.out / "i" / token / "dinner.ics").read_bytes() for token in TOKENS}
        self.assertEqual(len(dinner), 1)

    def test_names_stay_on_the_pages(self):
        self.build()
        contents = self.contents()
        self.assertIn(support.COUPLE_NAMES.encode(), contents[f"i/{support.TOKEN_A}/index.html"])
        for name, content in contents.items():
            if name.startswith("i/") and name.endswith(".html"):
                continue
            for secret in (support.COUPLE_NAMES, "Алиса", "Боб"):
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
        self.assertEqual(len(first), FILES_IN_TOTAL)
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
        self.assertEqual(stats.files, FILES_IN_TOTAL)
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
        return self.out

    def load(self, name: str) -> tuple:
        directory = support.ROOT / "examples" / name
        return tuple(
            json.loads((directory / file).read_text(encoding="utf-8"))
            for file in ("site.json", "invitations.json")
        )

    def test_the_examples_are_the_fixture_data(self):
        self.assertEqual(self.load("data"), (F.SITE, F.INVITATIONS))
        self.assertEqual(self.load("data-venue-pending"), (F.PENDING_SITE, F.PENDING_INVITATIONS))

    def test_example_with_places_and_media(self):
        site, invitations = self.load("data")
        names = sorted(
            {item[key] for item in site["media"].values() for key in ("file", "poster", "thumb")
             if key in item}
        )  # fmt: skip
        out = self.build_example("data", names)
        media = out / "assets" / site["mediaDir"]
        self.assertEqual(
            sorted(p.name for p in media.iterdir()), sorted({published_name(n) for n in names})
        )
        self.assertEqual(len(list((out / "i").iterdir())), len(invitations))
        self.assertEqual(len(list((out / "i").rglob("*.ics"))), 17)
        self.assertEqual(list(out.rglob("event.ics")), [])

    def test_example_with_pending_venue(self):
        site, invitations = self.load("data-venue-pending")
        out = self.build_example("data-venue-pending")
        self.assertFalse((out / "assets" / site["mediaDir"]).exists())
        self.assertEqual(len(list((out / "i").rglob("*.ics"))), len(invitations))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
