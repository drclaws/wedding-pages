"""The video block: a native `<video controls>` in the real template and what
a built page carries.

Unlike the other build tests these use the real `assets/` and the real
`template.html`. The control panel is the one of the browser, with the script
and without it: no third-party player ships with the site, and nothing on the
page takes the native controls away.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests import support
from tests.support import TempDirTestCase, build

ROOT = support.ROOT

APP_JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "template.html").read_text(encoding="utf-8")

#: The page sources first, then everything else that describes them.
SOURCES = (
    "template.html",
    "assets/app.js",
    "assets/app.css",
    "stub.html",
    "styleguide.html",
    "assets/styleguide.js",
    "assets/styleguide.css",
    "build.py",
    "README.md",
    ".github/workflows/ci.yml",
)


class NativeControlsTests(unittest.TestCase):
    """No player library in the repository: the panel is the browser's."""

    def test_there_is_no_vendor_directory(self):
        self.assertFalse((ROOT / "assets" / "vendor").exists())

    def test_no_trace_of_the_former_library(self):
        for name in SOURCES:
            with self.subTest(name=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?i)plyr")
                self.assertNotIn("data-player", text)

    def test_nothing_takes_the_native_controls_away(self):
        self.assertNotRegex(APP_JS, r"""removeAttribute\(\s*['"]controls['"]\s*\)""")
        self.assertNotRegex(APP_JS, r"\.controls\s*=\s*false")


class TemplateTests(unittest.TestCase):
    """The video block of the real `template.html`."""

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

    def test_the_head_loads_only_the_own_style_sheet_and_script(self):
        head = TEMPLATE[: TEMPLATE.index("</head>")]
        self.assertEqual(head.count('rel="stylesheet"'), 1)
        self.assertEqual(head.count("<script"), 1)
        self.assertIn('<link rel="stylesheet" href="/assets/app.css">', head)
        self.assertIn('<script src="/assets/app.js" defer></script>', head)


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

    def test_the_output_has_no_vendor_directory(self):
        for data_name in ("data", "data-venue-pending"):
            with self.subTest(data=data_name):
                out = self.build(data_name)
                self.assertFalse((out / "assets" / "vendor").exists())
                for page in self.pages(out):
                    self.assertNotIn("/assets/vendor/", page)
                    self.assertNotRegex(page, r"(?i)plyr")

    def test_a_page_with_a_video_keeps_the_native_controls(self):
        for page in self.pages(self.build("data")):
            self.assertIn('<video class="player__media" controls playsinline preload="none"', page)
            self.assertNotIn("data-player", page)

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
