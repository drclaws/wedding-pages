"""Tests for the checks on the finished output.

Every failing build must exit with code 1, explain the problem, leave the
previous output untouched and keep personal data out of the log.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tests.support import (
    CliTestCase,
    TempDirTestCase,
    build,
    site_data,
    tree_digest,
    write_data,
)

MAP_LINK = "https://www.google.com/maps/search/?api=1&amp;query=1,2"


def exists_in(*names: str):
    return set(names).__contains__


def html_problems(document: str, *existing: str) -> list[str]:
    return [message for _line, message in build.check_html(document, exists_in(*existing))]


def css_problems(text: str, *existing: str) -> list[str]:
    return [message for _line, message in build.check_stylesheet(text, exists_in(*existing))]


class FailingBuildTestCase(CliTestCase):
    """A previous output exists; a failing build must not touch it."""

    def setUp(self) -> None:
        super().setUp()
        (self.out / "i" / "PreviousToken-0123456789").mkdir(parents=True)
        (self.out / "i" / "PreviousToken-0123456789" / "index.html").write_text(
            "previous page", encoding="utf-8"
        )
        (self.out / "index.html").write_text("previous stub", encoding="utf-8")
        self.previous = tree_digest(self.out)

    def add_to_template(self, snippet: str) -> None:
        self.assertIn("</body>", self.template)
        (self.code / "template.html").write_text(
            self.template.replace("</body>", snippet + "\n</body>"), encoding="utf-8"
        )

    def add_to_stub(self, snippet: str) -> None:
        (self.code / "stub.html").write_text(
            support.STUB.replace("</body>", snippet + "\n</body>"), encoding="utf-8"
        )

    def assertBuildFails(self, *fragments: str, in_process: bool = True):
        result = self.build_in_process() if in_process else self.run_build()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)
        self.assertIn("build: failed", result.stderr)
        self.assertNotIn("internal error", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)
        self.assertEqual(tree_digest(self.out), self.previous, "the previous output changed")
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [], "temporary directories were left behind")
        return result

    def assertBuildPasses(self):
        result = self.build_in_process()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result


class ExternalResourceTests(FailingBuildTestCase):
    def test_external_image(self):
        self.add_to_template('<img src="https://cdn.example.invalid/pic.png" alt="">')
        result = self.assertBuildFails(
            "<img src>: external URL 'https://cdn.example.invalid/…'",
            "every resource must be a local file",
        )
        # the same problem on every page is reported once, tokens are shortened
        self.assertIn("3 files:", result.stderr)
        self.assertIn(f"first: i/{support.TOKEN_A[:4]}…/index.html line ", result.stderr)
        self.assertIn("failed with 1 error(s)", result.stderr)

    def test_stylesheet_from_a_cdn(self):
        self.add_to_template(
            '<link rel="stylesheet" href="https://fonts.example.invalid/css?family=X">'
        )
        self.assertBuildFails("<link href>: external URL 'https://fonts.example.invalid/…'")

    def test_protocol_relative_script(self):
        self.add_to_template('<script src="//cdn.example.invalid/lib.js"></script>')
        self.assertBuildFails("<script src>: external URL '//cdn.example.invalid/…'")

    def test_srcset_and_source(self):
        self.add_to_template(
            '<picture><source srcset="/assets/app.css 1x, https://cdn.example.invalid/b.webp 2x">'
            "</picture>"
        )
        self.assertBuildFails("<source srcset>: external URL 'https://cdn.example.invalid/…'")

    def test_video_poster(self):
        self.add_to_template('<video poster="http://cdn.example.invalid/p.jpg"></video>')
        self.assertBuildFails("<video poster>: external URL 'http://cdn.example.invalid/…'")

    def test_embedded_documents(self):
        for tag in ("iframe", "object", "embed"):
            with self.subTest(tag=tag):
                self.add_to_template(f'<{tag} src="/assets/app.css"></{tag}>')
                self.assertBuildFails(f"<{tag}> is not allowed")

    def test_css_url(self):
        (self.code / "assets" / "app.css").write_text(
            "/* comment */\nbody { background: url(https://cdn.example.invalid/bg.png); }\n",
            encoding="utf-8",
        )
        self.assertBuildFails(
            "assets/app.css line 2: CSS url(): external URL 'https://cdn.example.invalid/…'"
        )

    def test_css_import(self):
        (self.code / "assets" / "app.css").write_text(
            '@import url("https://fonts.example.invalid/css2?family=X");\n'
            "@import '//fonts.example.invalid/other.css';\n",
            encoding="utf-8",
        )
        result = self.assertBuildFails(
            "assets/app.css line 1: CSS @import: external URL 'https://fonts.example.invalid/…'",
            "assets/app.css line 2: CSS @import: external URL '//fonts.example.invalid/…'",
        )
        self.assertIn("failed with 2 error(s)", result.stderr)

    def test_css_in_a_vendor_directory_is_checked_too(self):
        (self.code / "assets" / "vendor" / "lib.css").write_text(
            "a { background: url( '//cdn.example.invalid/x.svg' ) }", encoding="utf-8"
        )
        self.assertBuildFails("assets/vendor/lib.css line 1")

    def test_inline_style_and_style_element(self):
        self.add_to_template(
            '<p style="background: url(https://cdn.example.invalid/a.png)">x</p>\n'
            "<style>@import 'https://cdn.example.invalid/b.css';</style>"
        )
        self.assertBuildFails("<p style> url(): external URL", "<style> @import: external URL")

    def test_external_url_in_the_stub(self):
        self.add_to_stub('<img src="https://cdn.example.invalid/pic.png" alt="">')
        result = self.assertBuildFails("2 files:", "first: 404.html line ")
        self.assertIn("<img src>: external URL", result.stderr)

    def test_meta_with_an_external_url(self):
        self.add_to_template(
            '<meta property="og:image" content="https://site.example.invalid/og.png">'
        )
        self.assertBuildFails("<meta og:image>: external URL 'https://site.example.invalid/…'")


class InlineScriptTests(FailingBuildTestCase):
    def test_inline_script(self):
        self.add_to_template("<script>document.title = 'x'</script>")
        self.assertBuildFails("inline <script> is not allowed")

    def test_event_handler_attribute(self):
        self.add_to_template('<button type="button" onclick="go()">x</button>')
        self.assertBuildFails("<button onclick>: inline event handlers are not allowed")

    def test_javascript_url(self):
        self.add_to_template('<a href="javascript:void(0)">x</a>')
        self.assertBuildFails("<a href>: script URL 'javascript:…' is not allowed")

    def test_script_as_a_data_url(self):
        self.add_to_template('<script src="data:text/javascript,alert(1)"></script>')
        self.assertBuildFails("data: URLs are only allowed for images and icons")


class LinkTests(FailingBuildTestCase):
    def test_external_link_that_is_not_a_map(self):
        self.add_to_template(
            '<a href="https://social.example.invalid/us" target="_blank" '
            'rel="noopener noreferrer">x</a>'
        )
        self.assertBuildFails(
            "<a href>: external link 'https://social.example.invalid/…'",
            "only https links to the map services are allowed",
        )

    def test_lookalike_map_hosts(self):
        for url in (
            "https://www.google.com.example.invalid/maps/",
            "https://www.google.com@example.invalid/maps/",
            "https://www.google.com/search?q=maps",
            "http://yandex.ru/maps/",
            "//maps.apple.com/?ll=1,2",
        ):
            with self.subTest(url=url):
                self.add_to_template(
                    f'<a href="{url}" target="_blank" rel="noopener noreferrer">x</a>'
                )
                self.assertBuildFails("only https links to the map services are allowed")

    def test_mailto_and_tel(self):
        self.add_to_template('<a href="mailto:someone@example.invalid">x</a>')
        result = self.assertBuildFails("<a href>: link 'mailto:…' is not allowed")
        self.assertNotIn("someone@", result.stderr)
        self.add_to_template('<a href="tel:+70000000000">x</a>')
        result = self.assertBuildFails("<a href>: link 'tel:…' is not allowed")
        self.assertNotIn("0000000", result.stderr)

    def test_map_link_without_rel(self):
        self.add_to_template(f'<a href="{MAP_LINK}" target="_blank">x</a>')
        self.assertBuildFails(
            "external link 'https://www.google.com/…' needs rel=\"noopener noreferrer\""
        )

    def test_map_link_with_half_of_rel(self):
        self.add_to_template(f'<a href="{MAP_LINK}" target="_blank" rel="noopener">x</a>')
        self.assertBuildFails('needs rel="noopener noreferrer"')

    def test_map_link_without_target(self):
        self.add_to_template(f'<a href="{MAP_LINK}" rel="noreferrer noopener">x</a>')
        self.assertBuildFails('needs target="_blank"')

    def test_well_formed_map_links_pass(self):
        self.add_to_template(
            f'<a href="{MAP_LINK}" target="_blank" rel="nofollow NoReferrer noopener">x</a>\n'
            '<a href="https://yandex.ru/maps/org/1" target="_BLANK" rel="noopener noreferrer">y</a>\n'
            '<a href="https://maps.apple.com/?ll=1,2" target="_blank" rel="noopener noreferrer">a</a>'
        )
        self.assertBuildPasses()


class LocalPathTests(FailingBuildTestCase):
    def test_missing_stylesheet(self):
        self.add_to_template('<link rel="stylesheet" href="/assets/x.css">')
        self.assertBuildFails("<link href>: '/assets/x.css' does not exist in the output")

    def test_missing_media_file(self):
        self.add_to_template('<img src="{{mediaPath}}/forgotten.webp" alt="">')
        self.assertBuildFails(
            f"<img src>: '/assets/{support.MEDIA_DIR}/forgotten.webp' does not exist"
        )

    def test_missing_local_link(self):
        self.add_to_template('<a href="/about/">x</a>')
        self.assertBuildFails("<a href>: '/about/' does not exist in the output")

    def test_missing_file_in_css(self):
        (self.code / "assets" / "app.css").write_text(
            '@font-face { src: url("/assets/fonts/missing.woff2") format("woff2"); }',
            encoding="utf-8",
        )
        self.assertBuildFails(
            "assets/app.css line 1: CSS url(): '/assets/fonts/missing.woff2' does not exist"
        )

    def test_relative_resource_path(self):
        self.add_to_template('<link rel="stylesheet" href="assets/app.css">')
        self.assertBuildFails(
            "<link href>: relative path 'assets/app.css'",
            "must be absolute from the site root",
        )

    def test_relative_path_in_css(self):
        (self.code / "assets" / "app.css").write_text(
            "a { background: url(img/bg.png) }", encoding="utf-8"
        )
        self.assertBuildFails("CSS url(): relative path 'img/bg.png'")

    def test_empty_url(self):
        # e.g. a computed field used outside of its <!-- if: --> block
        self.add_to_template('<img src="" alt="">')
        self.assertBuildFails("<img src>: the URL is empty")

    def test_path_with_dot_segments(self):
        self.add_to_template('<img src="/assets/../index.html" alt="">')
        self.assertBuildFails("is not a plain absolute path")

    def test_local_paths_that_resolve(self):
        (self.code / "assets" / "fonts" / "text.woff2").write_bytes(b"")
        (self.code / "assets" / "app.css").write_text(
            '@font-face { src: url("/assets/fonts/text.woff2") format("woff2"); }\n'
            "a { background: url(/assets/vendor/lib.js?v=1#frag); clip-path: url(#clip) }\n"
            "/* url(https://cdn.example.invalid/commented-out.png) */\n"
            'b::after { content: "// not a url"; background: url("data:image/png;base64,AAAA") }\n',
            encoding="utf-8",
        )
        self.add_to_template(
            '<a href="/">home</a> <a href="#top">top</a> <a href="/?x=1#y">q</a>\n'
            '<img src="/assets/app.css?v=2" srcset="/assets/app.css 1x, /assets/vendor/lib.js 2x" alt="">\n'
            '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="">\n'
            '<link rel="icon" href="data:,">\n'
            '<svg><use href="#icon"></use></svg>\n'
            '<meta property="og:image" content="/assets/app.css">\n'
            '<meta name="description" content="Приглашение">'
        )
        self.assertBuildPasses()


class StubCheckTests(FailingBuildTestCase):
    def test_placeholder_in_the_stub(self):
        self.add_to_stub("<p>{{coupleNames}}</p>")
        self.assertBuildFails("stub.html: must not contain template syntax", "'{{'")

    def test_directive_in_the_stub(self):
        self.add_to_stub("<!-- if:ty -->x<!-- endif -->")
        self.assertBuildFails("stub.html: must not contain template syntax")

    def test_missing_stub(self):
        os.remove(self.code / "stub.html")
        self.assertBuildFails("stub page not found", "stub.html")

    def test_stub_that_is_not_utf8(self):
        (self.code / "stub.html").write_bytes(b"<p>\xff\xfe</p>")
        self.assertBuildFails("stub.html: not valid UTF-8")


class OutputContentsCheckTests(FailingBuildTestCase):
    def test_json_in_assets(self):
        (self.code / "assets" / "data.json").write_text("{}", encoding="utf-8")
        self.assertBuildFails("assets/data.json: '.json' files must not be published")

    def test_source_map_in_assets(self):
        (self.code / "assets" / "vendor" / "lib.js.map").write_text("{}", encoding="utf-8")
        self.assertBuildFails("assets/vendor/lib.js.map: '.map' files must not be published")

    def test_other_forbidden_and_unknown_types(self):
        (self.code / "assets" / "README.md").write_text("# x", encoding="utf-8")
        (self.code / "assets" / "tool.py").write_text("pass", encoding="utf-8")
        (self.code / "assets" / "page.html").write_text("<p>x</p>", encoding="utf-8")
        (self.code / "assets" / "archive.zip").write_bytes(b"PK")
        (self.code / "assets" / "LICENSE").write_text("x", encoding="utf-8")
        result = self.assertBuildFails(
            "assets/README.md: '.md' files must not be published",
            "assets/tool.py: '.py' files must not be published",
            "assets/page.html: file type '.html' is not allowed in assets/",
            "assets/archive.zip: file type '.zip' is not allowed in assets/",
            "assets/LICENSE: file type 'LICENSE' is not allowed in assets/",
        )
        self.assertIn("failed with 5 error(s)", result.stderr)

    def test_vendor_licence_as_text_is_fine(self):
        (self.code / "assets" / "vendor" / "LICENSE.txt").write_text("MIT", encoding="utf-8")
        self.assertBuildPasses()
        self.assertTrue((self.out / "assets" / "vendor" / "LICENSE.txt").is_file())

    def test_symlink_in_assets(self):
        target = self.tmp / "outside.css"
        target.write_text("a{}", encoding="utf-8")
        try:
            os.symlink(target, self.code / "assets" / "vendor" / "linked.css")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        self.assertBuildFails("symbolic links are not allowed in assets", "linked.css")

    def test_symlinked_directory_in_assets(self):
        target = self.tmp / "outside"
        target.mkdir()
        (target / "secret.css").write_text("a{}", encoding="utf-8")
        try:
            os.symlink(target, self.code / "assets" / "linked", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        self.assertBuildFails("symbolic links are not allowed in assets", "linked")

    def test_symlink_in_media(self):
        os.remove(self.media / "route.png")
        target = self.tmp / "outside.png"
        target.write_bytes(b"")
        try:
            os.symlink(target, self.media / "route.png")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        self.assertBuildFails("route.png is a symbolic link")
        # `validate` reports it as well
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("route.png is a symbolic link", result.stderr)

    def test_media_dir_collides_with_an_asset(self):
        collision = "Vendor-Bundle-Directory"
        (self.code / "assets" / collision).mkdir()
        (self.code / "assets" / collision / "lib.js").write_text("// x", encoding="utf-8")
        write_data(self.data, site=site_data(mediaDir=collision.lower()))
        self.assertBuildFails(
            f"site.json: field 'mediaDir' collides with '{collision}'",
            "needs a name of its own",
        )
        result = support.run_main(
            self.code, "validate", "--data", self.data, "--media", self.media
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("field 'mediaDir' collides", result.stderr)

    def test_unsupported_media_type(self):
        site = site_data()
        site["venue"]["photos"] = ["venue-1.webp", "photo.heic"]
        site["video"]["file"] = "clip.mov"
        write_data(self.data, site=site)
        self.assertBuildFails(
            "field 'venue.photos[1]' has an unsupported file type",
            "field 'video.file' has an unsupported file type (allowed: .mp4, .webm)",
        )

    def test_media_name_reserved_for_the_calendar(self):
        site = site_data()
        site["venue"]["directionsImage"] = "Event.ics"
        write_data(self.data, site=site)
        self.assertBuildFails("field 'venue.directionsImage' must not be 'event.ics'")


class FileSizeLimitTests(FailingBuildTestCase):
    LIMIT = 16 * 1024

    def test_the_limit_is_25_mib(self):
        self.assertEqual(build.MAX_FILE_BYTES, 25 * 1024 * 1024)

    def test_oversized_asset(self):
        (self.code / "assets" / "vendor" / "big.js").write_bytes(b"/" * (self.LIMIT + 1))
        with mock.patch.object(build, "MAX_FILE_BYTES", self.LIMIT):
            self.assertBuildFails(
                "assets/vendor/big.js: 16.0 KiB is larger than the limit of 16.0 KiB"
            )

    def test_oversized_media_file_is_reported_by_validation(self):
        (self.media / "clip.mp4").write_bytes(b"\x00" * (self.LIMIT + 1))
        with mock.patch.object(build, "MAX_FILE_BYTES", self.LIMIT):
            self.assertBuildFails(
                "field 'video.file'", "clip.mp4 is 16.0 KiB", "the limit for a single file"
            )

    def test_file_at_the_limit_passes(self):
        (self.code / "assets" / "vendor" / "big.js").write_bytes(b"/" * self.LIMIT)
        with mock.patch.object(build, "MAX_FILE_BYTES", self.LIMIT):
            self.assertBuildPasses()


class CommandLineFailureTests(FailingBuildTestCase):
    """The same through a real subprocess: exit code and stderr."""

    def test_external_resource(self):
        self.add_to_template('<img src="https://cdn.example.invalid/pic.png" alt="">')
        result = self.assertBuildFails("<img src>: external URL", in_process=False)
        self.assertNotIn("Traceback", result.stderr)

    def test_forbidden_file(self):
        (self.code / "assets" / "data.json").write_text("{}", encoding="utf-8")
        self.assertBuildFails("'.json' files must not be published", in_process=False)


class CheckOutputTreeTests(TempDirTestCase):
    """`check_output` on hand-made trees (things a build never produces)."""

    TOKEN = support.TOKEN_A

    def make_tree(self) -> Path:
        out = self.tmp / "dist"
        page = out / "i" / self.TOKEN / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text("<p>page</p>", encoding="utf-8")
        for name in ("index.html", "404.html"):
            (out / name).write_text("<p>stub</p>", encoding="utf-8")
        (out / "_headers").write_text("/*\n", encoding="utf-8")
        (out / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
        media = out / "assets" / support.MEDIA_DIR
        media.mkdir(parents=True)
        (media / "event.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        return out

    def problems(self, out: Path) -> list[str]:
        with self.assertRaises(build.OutputError) as caught:
            build.check_output(out, [self.TOKEN], support.MEDIA_DIR)
        for message in caught.exception.errors:
            self.assertNotIn(self.TOKEN, message)
        return caught.exception.errors

    def test_clean_tree_passes(self):
        stats = build.check_output(self.make_tree(), [self.TOKEN], support.MEDIA_DIR)
        self.assertEqual(stats.files, 6)

    def test_dot_files(self):
        out = self.make_tree()
        (out / "assets" / ".gitkeep").write_text("", encoding="utf-8")
        (out / ".well-known").mkdir()
        problems = self.problems(out)
        self.assertIn("assets/.gitkeep: dot-files must not be published", problems)
        self.assertIn(".well-known: dot-directories must not be published", problems)

    def test_unexpected_top_level_entries(self):
        out = self.make_tree()
        (out / "sitemap.xml").write_text("<x/>", encoding="utf-8")
        (out / "styleguide.html").write_text("<p>x</p>", encoding="utf-8")
        (out / "data").mkdir()
        problems = self.problems(out)
        self.assertIn("sitemap.xml: unexpected file at the top of the output", problems)
        self.assertIn("styleguide.html: unexpected file at the top of the output", problems)
        self.assertIn("data: unexpected directory at the top of the output", problems)

    def test_only_known_tokens_inside_the_pages_directory(self):
        out = self.make_tree()
        stranger = out / "i" / "StrangerToken-0123456789"
        stranger.mkdir()
        (stranger / "index.html").write_text("<p>x</p>", encoding="utf-8")
        (out / "i" / "index.html").write_text("<p>list</p>", encoding="utf-8")
        (out / "i" / self.TOKEN / "extra.html").write_text("<p>x</p>", encoding="utf-8")
        problems = self.problems(out)
        self.assertIn(
            "i/Stra…: unexpected directory (only one directory per invitation is "
            "allowed in i/)",
            problems,
        )
        self.assertIn(
            "i/index.html: unexpected file (only <token>/index.html is allowed in i/)",
            problems,
        )
        self.assertTrue(any(p.startswith("i/EveT…/extra.html: unexpected file") for p in problems))

    def test_missing_pieces(self):
        out = self.make_tree()
        os.remove(out / "404.html")
        os.remove(out / "i" / self.TOKEN / "index.html")
        os.remove(out / "assets" / support.MEDIA_DIR / "event.ics")
        problems = self.problems(out)
        self.assertIn("404.html: missing from the output", problems)
        self.assertIn("i/EveT…/index.html: missing from the output", problems)
        self.assertIn(
            f"assets/{support.MEDIA_DIR}/event.ics: missing from the output", problems
        )

    def test_data_files_anywhere(self):
        out = self.make_tree()
        (out / "i" / self.TOKEN / "guest.json").write_text("{}", encoding="utf-8")
        (out / "assets" / "app.css.map").write_text("{}", encoding="utf-8")
        (out / "assets" / "UPPER.JSON").write_text("{}", encoding="utf-8")
        problems = self.problems(out)
        self.assertIn("i/EveT…/guest.json: '.json' files must not be published", problems)
        self.assertIn("assets/app.css.map: '.map' files must not be published", problems)
        self.assertIn("assets/UPPER.JSON: '.json' files must not be published", problems)

    def test_symlink_in_the_output(self):
        out = self.make_tree()
        try:
            os.symlink(out / "index.html", out / "assets" / "link.css")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        self.assertIn(
            "assets/link.css: symbolic links are not allowed in the output",
            self.problems(out),
        )

    def test_leftover_template_syntax_and_comments(self):
        out = self.make_tree()
        (out / "i" / self.TOKEN / "index.html").write_text(
            "<p>{{greeting}}</p>\n<!-- a note -->\n", encoding="utf-8"
        )
        problems = self.problems(out)
        self.assertIn(
            "i/EveT…/index.html line 1: template syntax left in the output: '{{'", problems
        )
        self.assertIn("i/EveT…/index.html line 2: HTML comment left in the output", problems)

    def test_long_problem_lists_are_capped(self):
        out = self.make_tree()
        for index in range(build.MAX_REPORTED_PROBLEMS + 5):
            (out / f"extra-{index:02}.txt").write_text("x", encoding="utf-8")
        problems = self.problems(out)
        self.assertEqual(len(problems), build.MAX_REPORTED_PROBLEMS + 1)
        self.assertEqual(problems[-1], "… and 5 more problem(s) in the output")

    def test_allowed_asset_types_are_a_named_constant(self):
        self.assertEqual(
            build.ASSET_EXTENSIONS,
            {
                ".css", ".js", ".woff2", ".svg", ".png", ".jpg", ".jpeg", ".webp",
                ".avif", ".gif", ".mp4", ".webm", ".ics", ".ico", ".txt",
            },
        )


class HtmlCheckUnitTests(unittest.TestCase):
    def test_clean_document(self):
        document = (
            '<!doctype html><html><head><link rel="stylesheet" href="/assets/app.css">'
            '<script src="/assets/app.js" defer></script></head>'
            '<body><a href="/i/x/">x</a></body></html>'
        )
        self.assertEqual(
            html_problems(document, "assets/app.css", "assets/app.js", "i/x/index.html"), []
        )

    def test_line_numbers(self):
        found = build.check_html('<p>\n\n<img src="x.png">', exists_in())
        self.assertEqual([line for line, _message in found], [3])

    def test_urls_are_read_the_way_browsers_read_them(self):
        for href in ("java\nscript:alert(1)", "  JavaScript:alert(1)", "\tjavascript:x"):
            with self.subTest(href=href):
                self.assertIn("script URL", html_problems(f'<a href="{href}">x</a>')[0])
        # a backslash is read as a slash: "/\host" leaves the site
        self.assertTrue(html_problems('<a href="/\\host.example.invalid/">x</a>'))
        self.assertTrue(html_problems('<img src="\\\\host.example.invalid/x.png">'))
        self.assertIn("external URL", html_problems('<img src=" HTTPS://H.EXAMPLE.INVALID/x">')[0])

    def test_percent_encoded_local_paths(self):
        self.assertEqual(
            html_problems('<img src="/assets/m/%D0%B7%D0%B0%D0%BB%201.webp">', "assets/m/зал 1.webp"),
            [],
        )

    def test_directory_urls_resolve_to_index_html(self):
        self.assertEqual(html_problems('<a href="/i/t/">x</a>', "i/t/index.html"), [])
        self.assertTrue(html_problems('<a href="/i/t">x</a>', "i/t/index.html"))

    def test_data_urls(self):
        self.assertEqual(html_problems('<img src="data:image/svg+xml,%3Csvg/%3E">'), [])
        self.assertEqual(html_problems('<link rel="icon" href="data:image/png;base64,AA">'), [])
        self.assertTrue(html_problems('<link rel="stylesheet" href="data:text/css,a{}">'))
        self.assertTrue(html_problems('<link rel="stylesheet" href="data:image/png,x">'))
        self.assertTrue(html_problems('<a href="data:text/html,x">x</a>'))
        self.assertTrue(html_problems('<img src="data:text/html,x">'))

    def test_srcset_with_data_url_and_descriptors(self):
        self.assertEqual(
            build._srcset_urls("data:image/png;base64,AA,BB 1x, /a.png 2x,/b.png 640w , /c.png"),
            ["data:image/png;base64,AA,BB", "/a.png", "/b.png", "/c.png"],
        )

    def test_forms_and_base(self):
        self.assertIn(
            "external URL", html_problems('<form action="https://forms.example.invalid/">')[0]
        )
        self.assertEqual(html_problems('<base href="/">'), ["<base> is not allowed"])

    def test_meta_refresh_to_another_site(self):
        problems = html_problems(
            '<meta http-equiv="refresh" content="0; url=https://other.example.invalid/x">'
        )
        self.assertEqual(
            problems,
            ["<meta content>: external URL 'https://other.example.invalid/…' is not allowed"],
        )

    def test_script_and_style_content_is_not_markup(self):
        document = (
            '<script src="/a.js">var x = "<img src=https://h.example.invalid/x>";</script>'
            "<style>/* <!-- --> */ a { color: red }</style>"
        )
        self.assertEqual(html_problems(document, "a.js"), [])

    def test_unterminated_comment(self):
        self.assertTrue(html_problems("<p>x</p><!-- never closed"))

    def test_messages_do_not_quote_the_rest_of_an_external_url(self):
        problems = html_problems(
            '<a href="https://www.google.com/maps/search/?api=1&amp;query=SECRET-NAME">x</a>'
        )
        self.assertTrue(problems)
        self.assertFalse([p for p in problems if "SECRET-NAME" in p])


class CssCheckUnitTests(unittest.TestCase):
    def test_comments_are_ignored(self):
        text = "/* @import 'https://h.example.invalid/x.css'; url(//h.example.invalid/y) */ a{}"
        self.assertEqual(css_problems(text), [])
        self.assertEqual(css_problems("/* never closed url(https://h.example.invalid/x)"), [])

    def test_comment_markers_inside_strings(self):
        text = 'a::before { content: "/*" } b { background: url(https://h.example.invalid/x) } /* */'
        self.assertEqual(len(css_problems(text)), 1)

    def test_url_forms(self):
        for value in (
            "url(https://h.example.invalid/x.png)",
            "URL( 'https://h.example.invalid/x.png' )",
            'url("//h.example.invalid/x.png")',
            'image-set("https://h.example.invalid/x.png" 1x)',
        ):
            with self.subTest(value=value):
                problems = css_problems(f"a {{ background: {value} }}")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("external URL", problems[0])

    def test_imports(self):
        self.assertEqual(css_problems('@import "/assets/base.css";', "assets/base.css"), [])
        self.assertIn("does not exist", css_problems('@import "/assets/base.css";')[0])
        self.assertIn("relative path", css_problems("@import 'base.css';")[0])
        self.assertIn("external URL", css_problems("@IMPORT url(//h.example.invalid/x.css);")[0])

    def test_fonts_as_data_urls_are_refused(self):
        problems = css_problems("@font-face { src: url(data:font/woff2;base64,AAAA) }")
        self.assertIn("data: URLs are only allowed for images", problems[0])

    def test_namespaces_are_not_resources(self):
        text = '@namespace svg url(http://www.w3.org/2000/svg);\n@namespace "http://www.w3.org/1999/xhtml";'
        self.assertEqual(css_problems(text), [])

    def test_line_numbers_survive_comment_removal(self):
        text = "/* one\n two */\n\na { background: url(x.png) }"
        self.assertEqual(build.check_stylesheet(text, exists_in())[0][0], 4)

    def test_real_stylesheet_passes(self):
        text = (support.ROOT / "assets" / "app.css").read_text(encoding="utf-8")
        self.assertEqual(css_problems(text), [])


class CommentStrippingTests(unittest.TestCase):
    def test_comments_are_removed(self):
        self.assertEqual(
            build.strip_html_comments("<p>a</p><!-- note --><p>b</p><!--\nmulti\nline\n-->"),
            "<p>a</p><p>b</p>",
        )

    def test_conditional_and_odd_comments(self):
        self.assertEqual(
            build.strip_html_comments("<!--[if IE]><p>x</p><![endif]-->a<!---->b<!-- -- -->c"),
            "abc",
        )

    def test_doctype_and_text_are_kept(self):
        document = "<!doctype html>\n<p>1 < 2 &amp; 3 > 2</p>\n"
        self.assertEqual(build.strip_html_comments(document), document)

    def test_script_and_style_content_is_kept(self):
        document = (
            '<script src="/a.js"><!-- kept --></script>'
            "<style>a::after { content: '<!-- kept -->' }</style>"
            '<p title="<!-- kept -->">x</p><!-- removed -->'
        )
        self.assertEqual(build.strip_html_comments(document), document[: -len("<!-- removed -->")])

    def test_cyrillic_offsets(self):
        self.assertEqual(
            build.strip_html_comments("<p>Привет</p>\n<!-- заметка -->\n<p>мир</p><!-- ещё -->"),
            "<p>Привет</p>\n\n<p>мир</p>",
        )

    def test_nothing_to_do(self):
        self.assertEqual(build.strip_html_comments("plain text"), "plain text")
        self.assertEqual(build.strip_html_comments(""), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
