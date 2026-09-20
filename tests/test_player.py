"""The video player: the vendored library, the module of the page script and
what a built page carries.

Unlike the other build tests these use the real `assets/` and the real
`template.html`: the point is exactly that the library files ship with the
site and that only a page with a video pulls them in.
"""

from __future__ import annotations

import gzip
import re
import unittest
from pathlib import Path

from tests import support
from tests.support import TempDirTestCase, build, tree_files

ROOT = support.ROOT
VENDOR_DIR = "vendor/plyr-3.8.4"
VENDOR_FILES = ("LICENSE.txt", "plyr.css", "plyr.min.js", "plyr.svg")
#: Everything third-party together must stay well under the size budget.
VENDOR_GZIP_LIMIT = 150 * 1024

APP_JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "assets" / "app.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "template.html").read_text(encoding="utf-8")


def strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(^|[^:'\"])//[^\n]*", r"\1", source)


class VendoredLibraryTests(unittest.TestCase):
    """The files under `assets/vendor/` as they are committed."""

    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / "assets" / VENDOR_DIR

    def test_exactly_the_expected_files(self):
        self.assertEqual(tree_files(self.directory), sorted(VENDOR_FILES))

    def test_every_file_is_allowed_in_the_output(self):
        for name in VENDOR_FILES:
            with self.subTest(name=name):
                extension = Path(name).suffix.lower()
                self.assertIn(extension, build.ASSET_EXTENSIONS)
                self.assertNotIn(extension, build.FORBIDDEN_OUTPUT_EXTENSIONS)

    def test_no_source_maps(self):
        self.assertEqual(list(self.directory.glob("*.map")), [])
        for name in ("plyr.min.js", "plyr.css"):
            text = (self.directory / name).read_text(encoding="utf-8")
            self.assertNotIn("sourceMappingURL", text)

    def test_the_licence_is_the_mit_text(self):
        text = (self.directory / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("The MIT License (MIT)", text)
        self.assertIn("Copyright (c) 2017 Sam Potts", text)

    def test_the_style_sheet_and_the_sprite_pass_the_output_checks(self):
        css = (self.directory / "plyr.css").read_text(encoding="utf-8")
        self.assertEqual(
            build.check_stylesheet(css, lambda _path: True, base=f"assets/{VENDOR_DIR}"), []
        )
        svg = (self.directory / "plyr.svg").read_text(encoding="utf-8")
        self.assertEqual(build.check_svg(svg), [])

    def test_the_sprite_is_the_only_tracked_picture(self):
        """The repository carries no media; the icon sprite is the exception."""
        pictures = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "assets").rglob("*")
            if path.suffix.lower() in build.IMAGE_EXTENSIONS | build.VIDEO_EXTENSIONS
        ]
        self.assertEqual(pictures, [f"assets/{VENDOR_DIR}/plyr.svg"])

    def test_size_budget(self):
        total = 0
        for path in sorted((ROOT / "assets" / "vendor").rglob("*")):
            if path.is_file() and path.suffix.lower() in (".js", ".css", ".svg"):
                total += len(gzip.compress(path.read_bytes(), 9))
        self.assertLess(total, VENDOR_GZIP_LIMIT, f"{total} bytes gzipped")


class PlayerModuleTests(unittest.TestCase):
    """Static checks of the `player` module in `assets/app.js`."""

    @classmethod
    def setUpClass(cls):
        cls.code = strip_comments(APP_JS)
        start = cls.code.index("register('player'")
        cls.module = cls.code[cls.code.rindex("var PLAYER_VENDOR_DIR", 0, start):]

    def test_the_module_is_registered(self):
        names = re.findall(r"\bregister\('([a-z-]+)',", self.code)
        self.assertIn("player", names)
        self.assertEqual(len(names), len(set(names)), "duplicate module names")

    def test_the_hook_is_the_data_attribute(self):
        self.assertIn("querySelectorAll('[data-player]')", self.module)

    def test_the_library_is_optional(self):
        """No library (not loaded, blocked) - the native controls stay."""
        self.assertRegex(
            self.module, r"if \(typeof window\.Plyr !== 'function'\) \{\s*return;"
        )
        self.assertIn("video.setAttribute('controls', '')", self.module)

    def test_privacy_options(self):
        """Without these four the library reaches out to its own CDN."""
        self.assertIn("iconUrl: PLAYER_VENDOR_DIR + '/plyr.svg'", self.module)
        self.assertIn("loadSprite: false", self.module)
        self.assertIn("blankVideo: ''", self.module)
        self.assertIn("storage: { enabled: false }", self.module)
        self.assertIn("iosNative: true", self.module)

    def test_the_vendor_path_is_one_constant_and_points_at_the_files(self):
        found = re.findall(r"var PLAYER_VENDOR_DIR = '([^']+)';", self.code)
        self.assertEqual(found, [f"/assets/{VENDOR_DIR}"])
        self.assertTrue((ROOT / found[0].lstrip("/")).is_dir())

    def test_no_external_addresses_in_our_code(self):
        # the CDN of the library must not appear anywhere in the script
        self.assertNotRegex(APP_JS, r"cdn\.plyr\.io")
        # a network address inside the module itself: the identifier of an XML
        # namespace elsewhere in the file (SVG_NS) is not one, hence the module.
        # A protocol-relative address always follows a quote or a bracket, so
        # that is the boundary to look for - "\b" never matches before "//".
        for pattern in (r"(?i)https?://", r"""(?i)['"(]//[a-z0-9-]+\.[a-z]{2,}"""):
            self.assertNotRegex(self.module, pattern)

    def test_no_autoplay_and_no_ads(self):
        self.assertIn("autoplay: false", self.module)
        self.assertNotIn("ads", self.module)
        self.assertNotIn("previewThumbnails", self.module)

    def test_russian_labels_for_every_control_in_use(self):
        controls = re.search(r"PLAYER_CONTROLS = \[([^\]]*)\]", self.code, re.S).group(1)
        used = set(re.findall(r"'([a-z-]+)'", controls))
        self.assertIn("play-large", used)
        labels = re.search(r"PLAYER_I18N = \{(.*?)\n  \};", self.code, re.S).group(1)
        for key in ("play", "pause", "seek", "currentTime", "mute", "unmute",
                    "enterFullscreen", "exitFullscreen", "volume"):  # fmt: skip
            with self.subTest(key=key):
                value = re.search(rf"\b{key}: '([^']*)'", labels)
                self.assertIsNotNone(value, f"{key} has no label")
                self.assertRegex(value.group(1), r"[А-Яа-яЁё]")

    def test_a_failure_takes_the_panel_of_the_library_away(self):
        """Otherwise the native controls come back next to a dead panel."""
        catch = self.module[self.module.index("} catch (error) {"):]
        self.assertLess(
            catch.index("cleanUpPlayer(video, home)"), catch.index("setAttribute('controls'")
        )
        clean = self.module[self.module.index("function cleanUpPlayer"):]
        clean = clean[: clean.index("register('player'")]
        # the reference comes from the element: when the constructor itself
        # throws, the variable was never assigned but the panel is already there
        self.assertRegex(clean, r"video\.plyr && typeof video\.plyr\.destroy === 'function'")
        self.assertRegex(
            clean, r"try \{[\s\S]*?video\.plyr\.destroy\(\);[\s\S]*?\} catch \(ignored\)"
        )
        # and when destroy() fails too, the wrapper is taken out by hand
        self.assertIn("home.insertBefore(video, wrapper);", clean)
        self.assertIn("home.removeChild(wrapper);", clean)
        self.assertEqual(clean.count("} catch (ignored) {"), 2)

    def test_failures_are_reported_through_the_registry_helper(self):
        self.assertIn("report('player', error);", self.module)
        self.assertNotIn("console.", self.module)

    def test_keyboard_is_bound_to_the_player_only(self):
        self.assertIn("keyboard: { focused: true, global: false }", self.module)


class PlayerStylesTests(unittest.TestCase):
    """Section 9 of `app.css`: the theme of the library."""

    @classmethod
    def setUpClass(cls):
        start = APP_CSS.index("\n   9. Player behaviour")
        cls.section = APP_CSS[start:]

    def test_every_library_variable_is_bound_to_a_token(self):
        pairs = re.findall(r"(--plyr-[a-z-]+):\s*([^;]+);", self.section)
        self.assertTrue(pairs)
        for name, value in pairs:
            with self.subTest(name=name):
                self.assertRegex(
                    value.strip(),
                    r"^(var\(--[a-z0-9-]+\)|initial)$",
                    "a library variable takes a token of the site, not a raw value",
                )

    def test_both_panel_variants_exist(self):
        self.assertIn(".player--overlay", self.section)
        self.assertIn(".player:not(.player--overlay):not(.is-fullscreen) .plyr__controls", self.section)

    def test_the_own_control_panel_is_gone(self):
        """The hand-made panel was replaced by the library; its leftovers
        would only rot."""
        for name in (".player__controls", ".player__button", ".player__icon",
                     ".player__progress", ".player__track", ".player__fill",
                     ".player__thumb", ".player__time", "--player-progress-value",
                     "--player-control-size", "--player-time-width",
                     "--player-progress-min"):  # fmt: skip
            with self.subTest(name=name):
                self.assertNotIn(name, APP_CSS)
                self.assertNotIn(name, (ROOT / "styleguide.html").read_text(encoding="utf-8"))
                self.assertNotIn(
                    name, (ROOT / "assets" / "styleguide.css").read_text(encoding="utf-8")
                )

    def test_the_tap_target_and_the_focus_ring_are_ours(self):
        self.assertIn("min-inline-size: var(--tap-min);", self.section)
        self.assertIn("outline: var(--focus-ring-width) solid var(--plyr-focus-visible-color);",
                      self.section)


class TemplateTests(unittest.TestCase):
    """The video block of the real `template.html`."""

    def test_the_library_is_loaded_only_with_a_video(self):
        head = TEMPLATE[: TEMPLATE.index("</head>")]
        for line in (f'<link rel="stylesheet" href="/assets/{VENDOR_DIR}/plyr.css">',
                     f'<script src="/assets/{VENDOR_DIR}/plyr.min.js" defer></script>'):  # fmt: skip
            with self.subTest(line=line):
                self.assertIn(line, head)
                before = head[: head.index(line)]
                self.assertEqual(
                    before.count("<!-- if:video.file -->"), before.count("<!-- endif -->") + 1,
                    "the line must stand inside <!-- if:video.file -->",
                )
        self.assertLess(head.index("plyr.min.js"), head.index("/assets/app.js"))
        self.assertLess(head.index("plyr.css"), head.index("/assets/app.css"))

    def test_the_markup_works_without_the_script(self):
        block = TEMPLATE[TEMPLATE.index('<div class="player"'):]
        block = block[: block.index("</section>")]
        self.assertEqual(block.count("<video"), 2)  # with and without a size
        for attribute in ("controls", "playsinline", 'preload="none"',
                          'poster="{{video.posterSrc}}"'):  # fmt: skip
            with self.subTest(attribute=attribute):
                self.assertEqual(block.count(attribute), 2)
        self.assertIn('width="{{video.width}}" height="{{video.height}}"', block)
        self.assertNotIn("style=", block)


class BuiltPageTests(TempDirTestCase):
    """A build of the real template and the real assets."""

    @classmethod
    def setUpClass(cls):
        cls.media_names = sorted(
            {
                name
                for directory in ("data", "data-venue-pending")
                for _field, name in build.media_references(
                    build.load_data(ROOT / "examples" / directory)[0]
                )
            }
        )

    def build(self, data_name: str) -> Path:
        data = ROOT / "examples" / data_name
        media = support.write_media(self.tmp / f"media-{data_name}", self.media_names)
        out = self.tmp / f"dist-{data_name}"
        build.build_site(data, media, out, code_dir=ROOT, log=lambda _message: None)
        return out

    def pages(self, out: Path) -> list[str]:
        return [
            path.read_text(encoding="utf-8") for path in sorted((out / "i").rglob("index.html"))
        ]

    def test_the_vendor_directory_is_copied_whole(self):
        out = self.build("data")
        self.assertEqual(
            tree_files(out / "assets" / VENDOR_DIR), sorted(VENDOR_FILES)
        )
        for name in VENDOR_FILES:
            self.assertEqual(
                (out / "assets" / VENDOR_DIR / name).read_bytes(),
                (ROOT / "assets" / VENDOR_DIR / name).read_bytes(),
            )

    def test_a_page_with_a_video_loads_the_library(self):
        out = self.build("data")
        for page in self.pages(out):
            self.assertIn(f'<link rel="stylesheet" href="/assets/{VENDOR_DIR}/plyr.css">', page)
            self.assertIn(f'<script src="/assets/{VENDOR_DIR}/plyr.min.js" defer></script>', page)
            self.assertIn("<video", page)

    def test_a_page_without_a_video_does_not(self):
        out = self.build("data-venue-pending")
        for page in self.pages(out):
            self.assertNotIn("plyr", page)
            self.assertNotIn("<video", page)
        # the files are still published: the assets directory is copied whole
        self.assertEqual(tree_files(out / "assets" / VENDOR_DIR), sorted(VENDOR_FILES))

    def test_the_frame_keeps_the_size_from_the_data(self):
        site, _invitations = build.load_data(ROOT / "examples" / "data")
        width, height = site["video"]["width"], site["video"]["height"]
        self.assertGreater(height, width, "the example clip is a portrait one")
        for page in self.pages(self.build("data")):
            self.assertIn(f'width="{width}" height="{height}"', page)
            self.assertIn(f'<svg width="{width}" height="{height}">', page)
            self.assertEqual(page.count("<video"), 1)

    def test_without_a_size_the_page_falls_back_to_the_css_ratio(self):
        data = self.tmp / "data-no-size"
        site = build.load_data(ROOT / "examples" / "data")[0]
        site["video"].pop("width")
        site["video"].pop("height")
        support.write_data(
            data, site=site, invitations=build.load_data(ROOT / "examples" / "data")[1]
        )
        media = support.write_media(self.tmp / "media-no-size", self.media_names)
        out = self.tmp / "dist-no-size"
        build.build_site(data, media, out, code_dir=ROOT, log=lambda _message: None)
        for path in sorted((out / "i").rglob("index.html")):
            page = path.read_text(encoding="utf-8")
            self.assertIn('class="player__sizer player__sizer--default"', page)
            self.assertNotIn("<video class=\"player__media\" controls playsinline "
                             "preload=\"none\" poster=\"\" width=", page)
            self.assertEqual(page.count("<video"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
