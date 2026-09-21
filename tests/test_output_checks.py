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

from tools import _schema as schema
from tools import gen_assets
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
        # the same problem on every page is reported once, tokens are hidden
        self.assertIn("3 files:", result.stderr)
        self.assertIn("first: i/…/index.html line ", result.stderr)
        self.assertNotIn(support.TOKEN_A[:4], result.stderr)
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
        support.write_app_css(
            self.code,
            "/* comment */\nbody { background: url(https://cdn.example.invalid/bg.png); }\n",
        )
        self.assertBuildFails(
            "assets/app.css line 2: CSS url(): external URL 'https://cdn.example.invalid/…'"
        )

    def test_css_import(self):
        support.write_app_css(
            self.code,
            '@import url("https://fonts.example.invalid/css2?family=X");\n'
            "@import '//fonts.example.invalid/other.css';\n",
        )
        result = self.assertBuildFails(
            "assets/app.css line 1: CSS @import: external URL 'https://fonts.example.invalid/…'",
            "assets/app.css line 2: CSS @import: external URL '//fonts.example.invalid/…'",
        )
        self.assertIn("failed with 2 error(s)", result.stderr)

    def test_css_in_a_vendor_directory_is_checked_too(self):
        (self.code / "assets" / "fonts" / "lib.css").write_text(
            "a { background: url( '//cdn.example.invalid/x.svg' ) }", encoding="utf-8"
        )
        self.assertBuildFails("assets/fonts/lib.css line 1")

    def test_inline_style_attribute(self):
        self.add_to_template(
            '<p style="background: url(https://cdn.example.invalid/a.png)">x</p>'
        )
        self.assertBuildFails("<p style> url(): external URL")

    def test_style_elements_are_not_allowed(self):
        self.add_to_template("<style>a { color: red }</style>")
        self.assertBuildFails("<style> elements are not allowed")

    def test_css_escapes_that_hide_a_url(self):
        hidden = "u\\72l(https://cdn.example.invalid/x.png)"
        cases = {
            "assets/app.css": lambda: support.write_app_css(
                self.code, f"a {{ background: {hidden} }}\n"
            ),
            "<p style>": lambda: self.add_to_template(f'<p style="background: {hidden}">x</p>'),
            "<style>": lambda: self.add_to_template(f"<style>a {{ background: {hidden} }}</style>"),
        }
        for where, prepare in cases.items():
            with self.subTest(where=where):
                support.write_app_css(self.code)
                prepare()
                expected = (
                    "<style> elements are not allowed"
                    if where == "<style>"
                    else "CSS escapes are not allowed in function names"
                )
                self.assertBuildFails(expected)

    def test_markup_hidden_in_svg_title(self):
        self.add_to_template(
            '<svg><title><img src="https://cdn.example.invalid/x.png"></title></svg>'
        )
        result = self.assertBuildFails()
        self.assertRegex(result.stderr, "markup-like text is not allowed|<img src>: external URL")
        self.add_to_stub('<svg><textarea><img src="https://cdn.example.invalid/x.png"></textarea></svg>')
        result = self.assertBuildFails()
        self.assertRegex(result.stderr, "markup-like text is not allowed|<img src>: external URL")

    def test_css_line_continuation_and_bare_scheme(self):
        support.write_app_css(
            self.code, "a { background: image-set('ht\\\ntps://cdn.example.invalid/x' 1x) }\n"
        )
        self.assertBuildFails("assets/app.css line 1: CSS: a backslash before a line break")
        support.write_app_css(
            self.code, "a { background: image-set('http:cdn.example.invalid/x' 1x) }\n"
        )
        self.assertBuildFails("assets/app.css line 1: CSS: external URL")

    def test_meta_refresh(self):
        self.add_to_template('<meta HTTP-EQUIV="Refresh" content="5">')
        self.assertBuildFails('<meta http-equiv="refresh"> is not allowed')

    def test_cdata_section_that_hides_markup(self):
        self.add_to_template(
            '<![CDATA[ > <img src="https://cdn.example.invalid/x.png"> ]]>'
        )
        self.assertBuildFails("'<![…' (CDATA sections, processing instructions) is not allowed")
        self.add_to_stub("<?xml version='1.0'?>")
        self.assertBuildFails("stub.html line ", "'<?…'")

    def test_markup_hidden_in_svg_content(self):
        self.add_to_template(
            '<svg><script src="/assets/fonts/sans.woff2">'
            '<img src="https://cdn.example.invalid/x.png"></script></svg>'
        )
        self.assertBuildFails("<script src> must be empty")
        self.add_to_template('<svg><script href="https://cdn.example.invalid/x.js"></script></svg>')
        self.assertBuildFails("<script href> (an SVG script) is not allowed")
        self.add_to_template(
            '<svg><a href="#x"><set attributeName="xlink:href" to="javascript:x"></set></a></svg>'
        )
        self.assertBuildFails("<set>: animating 'href' is not allowed")

    def test_unsafe_svg_file(self):
        (self.code / "assets" / "icon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" onload="x()">\n'
            "<script>x()</script>\n"
            "<foreignObject><p>x</p></foreignObject>\n"
            '<image xlink:href="https://cdn.example.invalid/x.png"/>\n'
            '<use href="//cdn.example.invalid/sprite.svg#a"/></svg>',
            encoding="utf-8",
        )
        self.assertBuildFails(
            "assets/icon.svg line 1: <svg onload>: event handlers are not allowed",
            "assets/icon.svg line 2: <script> is not allowed in SVG files",
            "assets/icon.svg line 3: <foreignobject> is not allowed in SVG files",
            "assets/icon.svg line 4: <image xlink:href>: external URL 'https://cdn.example.invalid/…'",
            "assets/icon.svg line 5: <use href>: external URL '//cdn.example.invalid/…'",
        )

    def test_plain_svg_file_passes(self):
        (self.code / "assets" / "icon.svg").write_text(
            '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"><defs><path id="a" d="M0 0"/></defs>'
            '<use xlink:href="#a"/><a href="/">x</a></svg>',
            encoding="utf-8",
        )
        self.assertBuildPasses()

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
        self.assertBuildFails("<link href>: '/assets/….css' does not exist in the output")

    def test_missing_media_file(self):
        self.add_to_template('<img src="{{mediaPath}}/forgotten.webp" alt="">')
        self.assertBuildFails(
            f"<img src>: '/assets/{support.MEDIA_DIR}/….webp' does not exist"
        )

    def test_missing_local_link(self):
        self.add_to_template('<a href="/about/">x</a>')
        self.assertBuildFails("<a href>: '/…' does not exist in the output")

    def test_missing_file_in_css(self):
        support.write_app_css(
            self.code,
            '@font-face { src: url("/assets/fonts/missing.woff2") format("woff2"); }\n',
        )
        self.assertBuildFails(
            "assets/app.css line 1: CSS url(): '/assets/fonts/….woff2' does not exist"
        )

    def test_relative_resource_path(self):
        self.add_to_template('<link rel="stylesheet" href="assets/app.css">')
        self.assertBuildFails(
            "<link href>: relative path '….css'",
            "must be absolute from the site root",
        )

    def nested_css(self, text: str) -> Path:
        directory = self.code / "assets" / "fonts" / "lib"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "lib.css").write_text(text, encoding="utf-8")
        return directory

    def test_relative_url_in_a_css_file_is_relative_to_the_file(self):
        directory = self.nested_css(
            "a { background: url(img/x.svg) } b { background: url('../sans.woff2?v=1#f') }"
        )
        (directory / "img").mkdir()
        (directory / "img" / "x.svg").write_text("<svg/>", encoding="utf-8")
        self.assertBuildPasses()

    def test_relative_url_in_a_css_file_must_exist(self):
        self.nested_css("a { background: url(img/x.svg) }")
        self.assertBuildFails(
            "assets/fonts/lib/lib.css line 1: CSS url(): '….svg' (relative to the style "
            "sheet) does not exist in the output"
        )

    def test_relative_url_in_a_css_file_must_stay_inside_the_output(self):
        # three levels up from assets/fonts/lib/ is still the output root
        self.nested_css("a { background: url(../../../x.svg) }")
        self.assertBuildFails("'….svg' (relative to the style sheet) does not exist")
        self.nested_css("a { background: url(../../../../x.svg) }")
        self.assertBuildFails("CSS url(): '….svg' points outside the output")
        self.nested_css("a { background: url(img/../../../../../../etc/x.svg) }")
        self.assertBuildFails("points outside the output")

    def test_relative_url_in_an_inline_style_is_refused(self):
        self.add_to_template('<p style="background: url(assets/app.css)">x</p>')
        self.assertBuildFails("<p style> url(): relative path '….css'")

    def test_text_in_a_path_is_not_echoed(self):
        # a Latin word from the data would pass for a plain URL
        self.add_to_template('<img src="/assets/SecretWord.png" alt=""><a href="/SecretWord/">x</a>')
        result = self.assertBuildFails("'/assets/….png' does not exist", "'/…' does not exist")
        self.assertNotIn("SecretWord", result.stderr)

    def test_empty_url(self):
        # e.g. a computed field used outside of its <!-- if: --> block
        self.add_to_template('<img src="" alt="">')
        self.assertBuildFails("<img src>: the URL is empty")

    def test_path_with_dot_segments(self):
        self.add_to_template('<img src="/assets/../index.html" alt="">')
        self.assertBuildFails("is not a plain absolute path")

    def test_local_paths_that_resolve(self):
        (self.code / "assets" / "fonts" / "text.woff2").write_bytes(b"")
        support.write_app_css(
            self.code,
            '@font-face { src: url("/assets/fonts/text.woff2") format("woff2"); }\n'
            "a { background: url(/assets/fonts/sans.woff2?v=1#frag); clip-path: url(#clip) }\n"
            "/* url(https://cdn.example.invalid/commented-out.png) */\n"
            'b::after { content: "// not a url"; background: url("data:image/png;base64,AAAA") }\n',
        )
        self.add_to_template(
            '<a href="/">home</a> <a href="#top">top</a> <a href="/?x=1#y">q</a>\n'
            '<img src="/assets/app.css?v=2" srcset="/assets/app.css 1x, /assets/fonts/sans.woff2 2x" alt="">\n'
            '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="">\n'
            '<link rel="icon" href="data:,">\n'
            '<svg><use href="#icon"></use></svg>\n'
            '<meta property="og:image" content="/assets/app.css">\n'
            '<meta name="description" content="Приглашение">'
        )
        self.assertBuildPasses()

    def test_template_and_stub_must_not_be_symlinks(self):
        for name in ("template.html", "stub.html"):
            with self.subTest(name=name):
                real = self.tmp / f"real-{name}"
                os.replace(self.code / name, real)
                try:
                    os.symlink(real, self.code / name)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"symbolic links are not available: {exc}")
                self.assertBuildFails("must not be a symbolic link", name)
                os.remove(self.code / name)
                os.replace(real, self.code / name)


class StubCheckTests(FailingBuildTestCase):
    def test_placeholder_in_the_stub(self):
        self.add_to_stub("<p>{{coupleNames}}</p>")
        self.assertBuildFails(
            "stub.html: must not contain template syntax", "line 13: '{{coupleNames}}'"
        )

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
        (self.code / "assets" / "fonts" / "lib.js.map").write_text("{}", encoding="utf-8")
        self.assertBuildFails("assets/fonts/lib.js.map: '.map' files must not be published")

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

    def test_a_media_file_under_the_name_of_the_data_stops_the_build(self):
        real_copy = build.copy_media

        def copy_and_plant(media, media_dir, destination):
            count = real_copy(media, media_dir, destination)
            (destination / "venue-1.png").write_bytes((Path(media_dir) / "venue-1.png").read_bytes())
            return count

        with mock.patch.object(build, "copy_media", side_effect=copy_and_plant):
            result = self.assertBuildFails("assets/<mediaDir>/….png: unexpected file")
        self.assertNotIn("venue-1", result.stderr)

    def test_a_directory_of_its_own_in_assets_stops_the_build(self):
        (self.code / "assets" / "vendor").mkdir()
        (self.code / "assets" / "vendor" / "x.js").write_text("// x\n", encoding="utf-8")
        self.assertBuildFails("assets/vendor: unexpected directory")

    def test_vendor_licence_as_text_is_fine(self):
        (self.code / "assets" / "fonts" / "LICENSE.txt").write_text("MIT", encoding="utf-8")
        self.assertBuildPasses()
        self.assertTrue((self.out / "assets" / "fonts" / "LICENSE.txt").is_file())

    def test_symlink_in_assets(self):
        target = self.tmp / "outside.css"
        target.write_text("a{}", encoding="utf-8")
        try:
            os.symlink(target, self.code / "assets" / "fonts" / "linked.css")
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
        self.assertBuildFails("field 'media.route.file': the file is a symbolic link")
        # `validate` reports it as well
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("field 'media.route.file': the file is a symbolic link", result.stderr)
        self.assertNotIn("route.png", result.stderr)

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
        site["media"]["venue-2"]["file"] = "photo.heic"
        site["media"]["clip"]["file"] = "clip.mov"
        write_data(self.data, site=site)
        self.assertBuildFails(
            "field 'media.venue-2.file' has an unsupported file type (allowed: .avif, ",
            "field 'media.clip.file' has an unsupported file type (allowed: .mp4)",
        )

    def test_a_media_file_that_is_not_what_its_name_says(self):
        (self.media / "poster.png").write_bytes(b"GIF89a not a picture of this type")
        (self.media / "clip.mp4").write_bytes(b"not a video at all")
        self.assertBuildFails(
            "field 'media.clip.poster' names a file that is not a valid file of the type "
            "its name gives",
            "field 'media.clip.file' names a file that is not a valid file of the type "
            "its name gives",
        )


class FileSizeLimitTests(FailingBuildTestCase):
    LIMIT = 64 * 1024  # more than the generated images take

    def test_the_limit_is_25_mib(self):
        self.assertEqual(build.MAX_FILE_BYTES, 25 * 1024 * 1024)

    def test_oversized_asset(self):
        (self.code / "assets" / "fonts" / "big.js").write_bytes(b"/" * (self.LIMIT + 1))
        with mock.patch.object(build, "MAX_FILE_BYTES", self.LIMIT):
            self.assertBuildFails(
                "assets/fonts/big.js: 64.0 KiB is larger than the limit of 64.0 KiB"
            )

    def test_oversized_media_file_is_reported_by_validation(self):
        (self.media / "clip.mp4").write_bytes(b"\x00" * (self.LIMIT + 1))
        with mock.patch.object(build, "MAX_FILE_BYTES", self.LIMIT):
            result = self.assertBuildFails(
                "field 'media.clip.file': the file is 64.0 KiB, the limit for a single file "
                "is 64.0 KiB"
            )
        self.assertNotIn("clip.mp4", result.stderr)

    def test_a_large_photo_behind_the_cover_is_a_warning(self):
        site = site_data()
        site["sections"][0]["background"] = "venue-2"
        write_data(self.data, site=site)
        size = (self.media / "venue-2.png").stat().st_size
        with mock.patch.object(schema, "COVER_BACKGROUND_WARN_BYTES", size):
            self.assertNotIn("first screen", self.assertBuildPasses().stderr)
        with mock.patch.object(schema, "COVER_BACKGROUND_WARN_BYTES", size - 1):
            result = self.assertBuildPasses()
        self.assertIn("warning: site.json: field 'media.venue-2.file' is ", result.stderr)
        self.assertIn("first screen: keep it under ~400 KB", result.stderr)
        self.assertNotIn("venue-2.png", result.stderr)

    def test_file_at_the_limit_passes(self):
        (self.code / "assets" / "fonts" / "big.js").write_bytes(b"/" * self.LIMIT)
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
        (out / "assets").mkdir()
        return out

    def problems(self, out: Path) -> list[str]:
        with self.assertRaises(build.OutputError) as caught:
            build.check_output(out, [self.TOKEN])
        for message in caught.exception.errors:
            self.assertNotIn(self.TOKEN, message)
        return caught.exception.errors

    def test_clean_tree_passes(self):
        out = self.make_tree()
        self.assertEqual(build.check_output(out, [self.TOKEN]).files, 5)
        # a mapping of the tokens is read as its keys
        self.assertEqual(build.check_output(out, {self.TOKEN: ()}).files, 5)

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
        # calendar files live in the media directory, never next to a page
        (out / "i" / self.TOKEN / "dinner.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        (out / "i" / self.TOKEN / "0123456789abcdef.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        (out / "i" / "event.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        problems = self.problems(out)
        self.assertIn(
            "i/…: unexpected directory (only one directory per invitation is "
            "allowed in i/)",
            problems,
        )
        self.assertIn(
            "i/index.html: unexpected file (only <token>/index.html is allowed in i/)",
            problems,
        )
        for name in ("extra.html", "dinner.ics", "0123456789abcdef.ics"):
            self.assertTrue(
                any(p.startswith(f"i/…/{name}: unexpected file") for p in problems), name
            )
        self.assertTrue(any(p.startswith("i/event.ics: unexpected file") for p in problems))
        for problem in problems:
            self.assertNotIn("Stra", problem)
            self.assertNotIn(self.TOKEN[:3], problem)

    def test_no_calendar_file_among_the_assets(self):
        out = self.make_tree()
        media = out / "assets" / support.MEDIA_DIR
        media.mkdir(parents=True)
        (media / "event.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        problems = self.problems(out)
        self.assertTrue(
            any(
                p.startswith(f"assets/{support.MEDIA_DIR}/event.ics: file type '.ics' is not "
                             "allowed in assets/")
                for p in problems
            ),
            problems,
        )

    def test_missing_pieces(self):
        out = self.make_tree()
        os.remove(out / "404.html")
        os.remove(out / "i" / self.TOKEN / "index.html")
        problems = self.problems(out)
        self.assertIn("404.html: missing from the output", problems)
        self.assertIn("i/…/index.html: missing from the output", problems)

    def test_data_files_anywhere(self):
        out = self.make_tree()
        (out / "i" / self.TOKEN / "guest.json").write_text("{}", encoding="utf-8")
        (out / "assets" / "app.css.map").write_text("{}", encoding="utf-8")
        (out / "assets" / "UPPER.JSON").write_text("{}", encoding="utf-8")
        problems = self.problems(out)
        self.assertIn("i/…/guest.json: '.json' files must not be published", problems)
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
            "i/…/index.html line 1: template syntax left in the output: '{{'", problems
        )
        self.assertIn("i/…/index.html line 2: HTML comment left in the output", problems)

    HASHED = "0123456789abcdef.png"
    CALENDAR = "fedcba9876543210.ics"

    def media_problems(self, out: Path, media=(HASHED,), calendars=()) -> list[str]:
        with self.assertRaises(build.OutputError) as caught:
            build.check_output(
                out, [self.TOKEN], media_dir=support.MEDIA_DIR, media=set(media),
                calendars=set(calendars),
            )
        for message in caught.exception.errors:
            self.assertNotIn(support.MEDIA_DIR, message)
        return caught.exception.errors

    def with_media(self) -> Path:
        out = self.make_tree()
        (out / "assets" / support.MEDIA_DIR).mkdir()
        (out / "assets" / support.MEDIA_DIR / self.HASHED).write_bytes(b"png")
        return out

    def test_the_media_directory_holds_exactly_the_published_names(self):
        stats = build.check_output(
            self.with_media(), [self.TOKEN], media_dir=support.MEDIA_DIR,
            media={self.HASHED},
        )
        self.assertEqual(stats.files, 6)

    UNEXPECTED_MEDIA = (
        "unexpected file (only the media the pages show and the calendar files of the "
        "events are published, under the hash of their contents)"
    )

    def test_a_media_file_under_the_name_of_the_data_is_refused(self):
        out = self.with_media()
        (out / "assets" / support.MEDIA_DIR / "venue-1.png").write_bytes(b"png")
        problems = self.media_problems(out)
        self.assertIn(f"assets/<mediaDir>/….png: {self.UNEXPECTED_MEDIA}", problems)
        self.assertNotIn("venue-1", "\n".join(problems))

    def test_a_hashed_file_that_no_page_shows_is_refused(self):
        out = self.with_media()
        (out / "assets" / support.MEDIA_DIR / "fedcba9876543210.png").write_bytes(b"png")
        self.assertIn(
            f"assets/<mediaDir>/fedcba9876543210.png: {self.UNEXPECTED_MEDIA}",
            self.media_problems(out),
        )

    def test_the_calendar_files_live_in_the_media_directory(self):
        out = self.with_media()
        (out / "assets" / support.MEDIA_DIR / self.CALENDAR).write_bytes(b"BEGIN:VCALENDAR\r\n")
        stats = build.check_output(
            out, [self.TOKEN], media_dir=support.MEDIA_DIR, media={self.HASHED},
            calendars={self.CALENDAR},
        )
        self.assertEqual(stats.files, 7)
        # without media files the directory holds the calendar files alone
        os.remove(out / "assets" / support.MEDIA_DIR / self.HASHED)
        stats = build.check_output(
            out, [self.TOKEN], media_dir=support.MEDIA_DIR, calendars={self.CALENDAR}
        )
        self.assertEqual(stats.files, 6)

    def test_an_extra_calendar_file_is_refused(self):
        out = self.with_media()
        media = out / "assets" / support.MEDIA_DIR
        (media / self.CALENDAR).write_bytes(b"BEGIN:VCALENDAR\r\n")
        # the file of an event nobody sees, and one named after its event
        (media / "00112233445566ff.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        (media / "brunch.ics").write_bytes(b"BEGIN:VCALENDAR\r\n")
        problems = self.media_problems(out, calendars={self.CALENDAR})
        self.assertIn(f"assets/<mediaDir>/00112233445566ff.ics: {self.UNEXPECTED_MEDIA}", problems)
        self.assertIn(f"assets/<mediaDir>/….ics: {self.UNEXPECTED_MEDIA}", problems)
        self.assertNotIn("brunch", "\n".join(problems))
        self.assertEqual(len(problems), 2)

    def test_a_missing_calendar_file(self):
        out = self.with_media()
        self.assertIn(
            f"assets/<mediaDir>/{self.CALENDAR}: missing from the output",
            self.media_problems(out, calendars={self.CALENDAR}),
        )

    def test_a_published_name_missing_from_the_media_directory(self):
        out = self.make_tree()
        (out / "assets" / support.MEDIA_DIR).mkdir()
        self.assertIn(
            f"assets/<mediaDir>/{self.HASHED}: missing from the output", self.media_problems(out)
        )

    def test_directories_in_assets(self):
        out = self.with_media()
        (out / "assets" / "vendor").mkdir()
        (out / "assets" / "vendor" / "x.js").write_text("//", encoding="utf-8")
        (out / "assets" / support.MEDIA_DIR / "sub").mkdir()
        (out / "assets" / "fonts" / "deep").mkdir(parents=True)
        (out / "assets" / "fonts" / "deep" / "a.woff2").write_bytes(b"w")
        problems = self.media_problems(out)
        tail = "unexpected directory (only fonts/ and the media directory with the media the pages show are allowed in assets/)"
        self.assertIn(f"assets/vendor: {tail}", problems)
        self.assertIn(f"assets/<mediaDir>/…: {tail}", problems)
        self.assertFalse(any(p.startswith("assets/fonts") for p in problems), problems)

    def test_no_media_directory_without_media(self):
        out = self.with_media()
        problems = self.media_problems(out, media=())
        self.assertTrue(any(p.startswith("assets/<mediaDir>: unexpected directory") for p in problems))

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
                ".avif", ".gif", ".mp4", ".ico", ".txt",
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

    def test_meta_refresh_is_refused_whatever_the_content(self):
        for content in ("0; url=https://other.example.invalid/x", "0; url=/", "30"):
            problems = html_problems(f'<meta http-equiv="REFRESH" content="{content}">')
            self.assertEqual(problems, ['<meta http-equiv="refresh"> is not allowed'])

    def test_external_url_in_other_meta_content(self):
        self.assertEqual(
            html_problems('<meta name="x" content="see https://other.example.invalid/x">'),
            ["<meta content>: external URL 'https://other.example.invalid/…' is not allowed"],
        )

    def test_escaped_comment_markers_in_text_are_text(self):
        self.assertEqual(html_problems("<p>a &lt;!-- b</p><p>c --&gt; d</p>"), [])

    def test_declarations_and_processing_instructions(self):
        self.assertEqual(html_problems("<!doctype html><p>x</p>"), [])
        self.assertTrue(html_problems("<!ELEMENT br EMPTY><p>x</p>"))
        self.assertTrue(html_problems("<?php echo 1 ?>"))
        self.assertTrue(html_problems("<![if !IE]><p>x</p><![endif]>"))

    def test_map_links_with_dot_segments(self):
        for url in (
            "https://www.google.com/maps/../x",
            "https://www.google.com/maps/%2e%2e/x",
            "https://www.google.com/maps/%2E%2E%2Fx",
            "https://yandex.ru/maps/./../x",
            "https://maps.apple.com/\\x",
        ):
            with self.subTest(url=url):
                self.assertFalse(build._is_map_link(url))
                self.assertTrue(
                    html_problems(f'<a href="{url}" target="_blank" rel="noopener noreferrer">x</a>')
                )
        self.assertTrue(build._is_map_link("https://www.google.com/maps/search/?api=1&query=1.5,2.5"))

    def test_script_content_and_style_elements(self):
        self.assertEqual(html_problems('<script src="/a.js"> \n</script>', "a.js"), [])
        self.assertEqual(
            html_problems('<script src="/a.js"><img src=https://h.example.invalid/x></script>', "a.js"),
            ["<script src> must be empty"],
        )
        self.assertIn(
            "<style> elements are not allowed; styles must be files in /assets/",
            html_problems("<svg><style><img src=https://h.example.invalid/x></style></svg>"),
        )

    def test_markup_like_text(self):
        # inside <svg> a browser reads the content of <title>/<textarea> as
        # elements; whichever way the parser reads it, the page is refused
        for element in ("title", "textarea", "xmp", "noembed", "plaintext"):
            with self.subTest(element=element):
                problems = html_problems(
                    f"<svg><{element}><img src=https://h.example.invalid/x></{element}></svg>"
                )
                self.assertTrue(problems)
                self.assertTrue(
                    any("markup-like text" in p or "external URL" in p for p in problems),
                    problems,
                )
        self.assertEqual(
            html_problems("<title>Приглашение</title><p>1 < 2, a &lt;b&gt; &amp; <3</p>"), []
        )

    def test_unknown_schemes_are_not_echoed(self):
        problems = html_problems('<a href="SecretWord:x">x</a><img src="Other-Word:y">')
        self.assertEqual(len(problems), 2)
        for problem in problems:
            self.assertIn("'…:'", problem)
            self.assertNotIn("ecret", problem.lower())
            self.assertNotIn("other", problem.lower())
        self.assertIn("'tel:…'", html_problems('<a href="tel:123">x</a>')[0])

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
        self.assertEqual(css_problems("@import 'base.css';", "base.css"), [])
        self.assertIn("relative to the style sheet", css_problems("@import 'base.css';")[0])
        self.assertIn("external URL", css_problems("@IMPORT url(//h.example.invalid/x.css);")[0])

    def test_escapes_are_refused_where_they_could_hide_a_url(self):
        cases = {
            "a { background: u\\72l(https://h.example.invalid/x) }": "function names",
            "a { background: \\55 RL(https://h.example.invalid/x) }": "function names",
            "a { background: url(ht\\74ps://h.example.invalid/x) }": "in URLs",
            "a { background: url('\\2f\\2fh.example.invalid/x') }": "in URLs",
            "@\\69mport 'https://h.example.invalid/x.css';": "at-rule names",
            "@import '\\68ttps://h.example.invalid/x.css';": "in URLs",
            "a { background: image-set('\\68ttps://h.example.invalid/x' 1x) }": "external URL",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                problems = css_problems(text)
                self.assertTrue(problems, text)
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_line_continuation_is_refused(self):
        for ending in ("\n", "\r\n", "\r", "\f"):
            with self.subTest(ending=ending):
                text = f"a {{ color: red }}\nb {{ background: image-set('ht\\{ending}tps://h.example.invalid/x' 1x) }}"
                problems = build.check_stylesheet(text, exists_in())
                self.assertEqual(len(problems), 1, problems)
                self.assertEqual(problems[0][0], 2)
                self.assertIn("a backslash before a line break is not allowed", problems[0][1])

    def test_strings_that_start_with_a_scheme(self):
        for value in ("http:h.example.invalid/x", "HTTPS:h.example.invalid/x", "ftp:x", "\\68ttp:h.example.invalid/x"):
            with self.subTest(value=value):
                problems = css_problems(f"a {{ background: image-set('{value}' 1x) }}")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("external URL", problems[0])
        harmless = (
            'a::before { content: "→" } b { font-family: "Name", serif } '
            'c { grid-template-areas: "a b" "c d" } d::after { content: "12:30" } '
            'e::after { content: "Note: text" } f { background: image-set("data:image/png;base64,AA" 1x) }'
        )
        self.assertEqual(css_problems(harmless), [])

    def test_harmless_escapes_pass(self):
        text = 'a::before { content: "\\201C" } .sm\\:flex { color: red } .w-\\[calc(1px)\\] { top: 0 }'
        self.assertEqual(css_problems(text), [])

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

    def test_odd_comments(self):
        self.assertEqual(build.strip_html_comments("<!--[if IE]><p>x</p>-->a<!---->b<!-- -- -->c"), "abc")

    def test_markup_that_browsers_read_differently_is_refused(self):
        for document in ("<![CDATA[ > <p>x</p> ]]>", "<!--[if IE]>x<![endif]-->", "<?xml?>"):
            with self.subTest(document=document):
                with self.assertRaises(build.TemplateError):
                    build.strip_html_comments(document, "stub.html")

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


def svg_problems(text: str, *existing: str) -> list[str]:
    exists = exists_in(*existing) if existing else None
    return [message for _line, message in build.check_svg(text, exists)]


class SvgFileTests(unittest.TestCase):
    """`check_svg` on its own: what an SVG file must not contain."""

    SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'

    def test_plain_file(self):
        text = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg>\n' + self.SVG
            + '<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
            '<path id="a" d="M0 0h4v4z"/></defs>'
            '<rect width="4" height="4" fill="url(#g)" style="stroke: rgb(1, 2, 3)" '
            'transform="rotate(45) translate(1, 2)"/>'
            '<use xlink:href="#a"/><animate attributeName="opacity" to="0"/></svg>'
        )
        self.assertEqual(svg_problems(text), [])

    def test_namespace_prefix_does_not_hide_an_element(self):
        for tag in ("svg:script", "SVG:Script", "x:foreignObject", "svg:style"):
            with self.subTest(tag=tag):
                problems = svg_problems(f"{self.SVG}<{tag}>x</{tag}></svg>")
                self.assertEqual(problems, [f"<{tag.lower()}> is not allowed in SVG files"])

    def test_style_element(self):
        problems = svg_problems(self.SVG + "<style>rect { fill: red }</style></svg>")
        self.assertEqual(problems, ["<style> is not allowed in SVG files"])

    def test_external_url_in_css(self):
        cases = {
            '<rect style="fill: url(https://h.example.invalid/p.svg#a)"/>': "<rect style> url()",
            '<rect fill="url(//h.example.invalid/p.svg#a)"/>': "<rect fill> url()",
            '<rect filter="URL( \'https://h.example.invalid/f.svg#f\' )"/>': "<rect filter> url()",
            '<rect mask="u\\72l(https://h.example.invalid/m.svg#m)"/>': "CSS escapes",
        }
        for markup, expected in cases.items():
            with self.subTest(markup=markup):
                problems = svg_problems(f"{self.SVG}{markup}</svg>")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(expected, problems[0])

    def test_local_url_in_css_is_looked_up_in_the_output(self):
        text = self.SVG + '<rect fill="url(/assets/missing.svg#a)"/></svg>'
        self.assertEqual(svg_problems(text), [])  # no output to look at
        self.assertIn("does not exist in the output", svg_problems(text, "assets/other.svg")[0])
        self.assertEqual(svg_problems(text, "assets/missing.svg"), [])

    def test_animated_href(self):
        for tag in ("set", "animate", "svg:animate", "animateTransform"):
            for attribute in ("href", "xlink:href", " HREF "):
                with self.subTest(tag=tag, attribute=attribute):
                    problems = svg_problems(
                        f'{self.SVG}<a href="#x"><{tag} attributeName="{attribute}" '
                        'to="https://h.example.invalid/"/></a></svg>'
                    )
                    self.assertEqual(
                        problems, [f"<{tag.lower()}>: animating 'href' is not allowed"]
                    )

    def test_entities_and_style_sheets(self):
        text = (
            '<?xml version="1.0"?>\n<?xml-stylesheet href="https://h.example.invalid/s.css"?>\n'
            '<!DOCTYPE svg [\n<!ENTITY x "<script>alert(1)</script>">\n]>\n'
            + self.SVG + "&x;</svg>"
        )
        found = build.check_svg(text)
        self.assertIn((2, "'<?xml-stylesheet' is not allowed"), found)
        self.assertIn((4, "'<!ENTITY' declarations are not allowed"), found)

    def test_srcset_and_xml_base_are_refused(self):
        """A browser acts on both; the URL rules here cannot follow them."""
        cases = {
            '<image srcset="/assets/a.png 1x, https://h.example.invalid/b.png 2x"/>':
                "<image srcset>: 'srcset' is not allowed in SVG files",
            '<image imagesrcset="/assets/a.png 1x"/>':
                "<image imagesrcset>: 'imagesrcset' is not allowed in SVG files",
            '<g xml:base="https://h.example.invalid/"><image href="a.png"/></g>':
                "<g xml:base>: 'xml:base' is not allowed in SVG files",
        }
        for markup, expected in cases.items():
            with self.subTest(markup=markup):
                self.assertIn(expected, svg_problems(f"{self.SVG}{markup}</svg>"))

    def test_the_namespace_decides_not_the_prefix(self):
        """`<x:div xmlns:x="…/xhtml">` is HTML however it is spelled."""
        cases = {
            '<x:div xmlns:x="http://www.w3.org/1999/xhtml"/>':
                "<x:div>: HTML elements are not allowed in SVG files",
            '<a:img xmlns:a="http://www.w3.org/1999/xhtml"/>':
                "<a:img>: HTML elements are not allowed in SVG files",
            "<foo:bar/>":
                "<foo:bar>: elements of another namespace are not allowed in SVG files",
        }
        for markup, expected in cases.items():
            with self.subTest(markup=markup):
                self.assertEqual(svg_problems(f"{self.SVG}{markup}</svg>"), [expected])

    def test_a_prefix_bound_to_svg_is_fine(self):
        for markup in ('<x:rect xmlns:x="http://www.w3.org/2000/svg" width="4"/>',
                       '<svg:g><svg:rect width="4"/></svg:g>',
                       '<defs><symbol id="a"><path d="M0 0h4v4z"/></symbol></defs>'
                       '<use xlink:href="#a"/><use href="#a"/>',
                       '<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
                       '<rect fill="url(#g)"/>'):  # fmt: skip
            with self.subTest(markup=markup[:40]):
                self.assertEqual(svg_problems(f"{self.SVG}{markup}</svg>"), [])

    def test_the_generated_icon_passes(self):
        palette = build.load_palette(support.ROOT / "assets")
        icon = gen_assets.favicon_svg(palette).decode("utf-8")
        self.assertEqual(svg_problems(icon), [])

    def test_html_elements_inside_svg_are_refused(self):
        for tag in ("xhtml:iframe", "XHTML:img", "html:div"):
            with self.subTest(tag=tag):
                problems = svg_problems(f"{self.SVG}<{tag}/></svg>")
                self.assertEqual(
                    problems, [f"<{tag.lower()}>: HTML elements are not allowed in SVG files"]
                )

    def test_a_script_keeps_its_own_message_whatever_the_prefix(self):
        self.assertEqual(
            svg_problems(f"{self.SVG}<html:script>x()</html:script></svg>"),
            ["<html:script> is not allowed in SVG files"],
        )

    def test_svg_namespace_prefix_is_still_fine(self):
        self.assertEqual(svg_problems(f'{self.SVG}<svg:rect width="4"/></svg>'), [])

    def test_messages_do_not_echo_the_path_of_an_external_url(self):
        problems = svg_problems(
            self.SVG + '<image href="https://h.example.invalid/private/path.png"/></svg>'
        )
        self.assertEqual(
            problems, ["<image href>: external URL 'https://h.example.invalid/…' is not allowed"]
        )


class SiteAddressMaskTests(unittest.TestCase):
    """`mask_site`: the address of the site never reaches a message."""

    BASE = "https://invite.example.invalid"

    def test_the_address_and_the_host_are_masked(self):
        cases = {
            f"external URL '{self.BASE}/…'": "external URL '<site>/…'",
            f"external URL '{self.BASE}'": "external URL '<site>'",
            "host invite.example.invalid here": "host <site> here",
            "//invite.example.invalid:8443/x": "<site>/x",
            "HTTPS://INVITE.EXAMPLE.INVALID/x": "<site>/x",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(build.mask_site(text, self.BASE), expected)

    def test_a_long_address_is_masked_in_its_shortened_form(self):
        base = "https://" + "a" * 70 + ".example.invalid"
        shown = build._show_url(f"{base}/assets/og.png")
        self.assertIn("…", shown)
        masked = build.mask_site(f"external URL '{shown}'", base)
        self.assertNotIn("aaa", masked)

    def test_without_an_address_nothing_changes(self):
        self.assertEqual(build.mask_site("host example.org", ""), "host example.org")

    def test_other_text_is_left_alone(self):
        self.assertEqual(
            build.mask_site("assets/app.css line 3: url()", self.BASE),
            "assets/app.css line 3: url()",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
