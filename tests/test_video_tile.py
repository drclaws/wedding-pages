"""The video block: a tile that links to the clip, and what a built page carries.

Unlike the other build tests these use the real `assets/` and the real
`template.html`. On the page the clip is a tile - a link to the file with the
poster and a play badge; without the script the browser opens the file with
its own player, with the script the same link opens the viewer, where one
`<video controls>` with the browser's panel plays it. No third-party player
ships with the site, and nothing takes the native controls away.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests import support
from tests.support import TempDirTestCase, build

ROOT = support.ROOT

APP_JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "assets" / "app.css").read_text(encoding="utf-8")
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

TILE_CLASS = 'class="gallery__link media-tile media-tile--video"'
TILE_HOOKS = (
    'href="{{video.src}}" type="video/mp4" data-lightbox data-media="video" '
    'data-media-poster="{{video.posterSrc}}"'
)
BADGE = (
    '<span class="media-tile__badge" aria-hidden="true">'
    '<svg class="media-tile__icon" viewBox="0 0 24 24" focusable="false">'
    '<path d="M8 5v14l11-7z"/></svg></span>'
)


def strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def css_section(number: int) -> str:
    """Section `number` of app.css, comments removed."""
    start = APP_CSS.index(f"\n   {number}. ")
    end = APP_CSS.find(f"\n   {number + 1}. ", start)
    return strip_css_comments("/*" + (APP_CSS[start:] if end == -1 else APP_CSS[start:end] + "*/"))


def video_block(template: str = TEMPLATE) -> str:
    block = template[template.index("<!-- 8. "):]
    return block[: block.index("</section>")]


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

    def test_the_viewer_video_has_the_native_panel_and_loads_nothing_ahead(self):
        for attribute in ("controls", "playsinline", "disablepictureinpicture"):
            with self.subTest(attribute=attribute):
                self.assertIn(f"video.setAttribute('{attribute}', '');", APP_JS)
        self.assertIn("video.setAttribute('preload', 'none');", APP_JS)
        self.assertNotRegex(APP_JS, r"""['"]autoplay['"]|\.autoplay\s*=""")
        self.assertEqual(APP_JS.count("createElement('video'"), 1, "one <video> per viewer")


class TemplateTests(unittest.TestCase):
    """The video block of the real `template.html`."""

    def test_the_video_block_is_a_tile_that_links_to_the_file(self):
        block = video_block()
        self.assertNotIn("<video", block)
        self.assertNotIn("player", block)
        tiles = re.findall(r"<a [^>]*>", block)
        self.assertEqual(len(tiles), 2)  # with and without a size
        for tile in tiles:
            with self.subTest(tile=tile):
                self.assertTrue(tile.startswith(f"<a {TILE_CLASS} {TILE_HOOKS}"))
                self.assertIn('aria-label="Видео"', tile)
                self.assertNotIn("aria-haspopup", tile)  # the script adds it
        self.assertEqual(block.count('<img src="{{video.posterSrc}}" alt=""'), 2)
        self.assertEqual(block.count(BADGE), 2)
        self.assertNotIn("data-reveal", block[block.index("<ul"):])
        self.assertRegex(block, r'<ul class="gallery gallery--single" role="list">')
        self.assertNotIn("data-gallery", block)

    def test_the_frame_ratio_comes_from_the_size_in_the_data(self):
        block = video_block()
        sized = block[block.index("<!-- if:video.width -->"):block.index("<!-- if:!video.width -->")]
        unsized = block[block.index("<!-- if:!video.width -->"):]
        ratio = "{{video.width}} / {{video.height}}"
        self.assertIn(f'data-media-ratio="{ratio}"', sized)
        # the size comes from an invisible sizer, not from a style attribute
        self.assertIn(
            '<li class="gallery__item"><svg class="gallery__sizer" width="{{video.width}}" '
            'height="{{video.height}}" viewBox="0 0 {{video.width}} {{video.height}}" '
            'aria-hidden="true" focusable="false"></svg><a ',
            sized,
        )
        self.assertIn('alt="" width="{{video.width}}" height="{{video.height}}"', sized)
        self.assertIn(
            '<li class="gallery__item"><span class="gallery__sizer gallery__sizer--default" '
            'aria-hidden="true"></span><a ',
            unsized,
        )
        for text in ("data-media-ratio", "width=", "{{video.width}}"):
            with self.subTest(text=text):
                self.assertNotIn(text, unsized)
        self.assertNotIn("style", block)

    def test_venue_photos_are_media_tiles(self):
        self.assertIn(
            '<a class="gallery__link media-tile" href="{{.src}}" data-lightbox>', TEMPLATE
        )

    def test_the_head_loads_only_the_own_style_sheet_and_script(self):
        head = TEMPLATE[: TEMPLATE.index("</head>")]
        self.assertEqual(head.count('rel="stylesheet"'), 1)
        self.assertEqual(head.count("<script"), 1)
        self.assertIn('<link rel="stylesheet" href="/assets/app.css">', head)
        self.assertIn('<script src="/assets/app.js" defer></script>', head)


class StylesTests(unittest.TestCase):
    """The tile, the single layout and the video in the viewer."""

    def test_media_tokens_are_marked_and_the_player_ones_are_gone(self):
        self.assertRegex(APP_CSS, r"\n  --media-play-size: calc\(var\(--tap-min\) \+ var\(--icon-size\)\);\s*/\* TEMP")
        self.assertRegex(APP_CSS, r"\n  --media-play-icon-size: [^;]+;\s*/\* TEMP")
        self.assertNotIn("--player-", APP_CSS)
        self.assertIn("--ratio-video:", APP_CSS)
        for selector in (".player", ".player__sizer", ".player__media"):
            with self.subTest(selector=selector):
                self.assertNotIn(selector + " {", APP_CSS)

    def test_the_tile_badge(self):
        section = css_section(5)
        badge = re.search(r"\.media-tile__badge \{([^}]*)\}", section).group(1)
        for declaration in (
            "inline-size: var(--media-play-size);",
            "block-size: var(--media-play-size);",
            "background-color: var(--color-overlay-control);",
            "color: var(--color-on-overlay);",
            "pointer-events: none;",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, badge)
        self.assertRegex(section, r"\.media-tile__icon \{[^}]*fill: currentColor;")
        self.assertRegex(section, r"\.media-tile__duration \{")

    def test_hover_of_the_tile_only_inside_the_hover_media_query(self):
        section = css_section(5)
        outside = re.sub(r"@media \(hover: hover\)\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", section)
        self.assertNotIn(":hover", outside)
        self.assertRegex(
            section,
            r"@media \(hover: hover\)\s*\{[^@]*\.media-tile:hover \.media-tile__badge,\s*"
            r"\.media-tile\.is-hover \.media-tile__badge\s*\{\s*"
            r"background-color: var\(--color-overlay-control-hover\);",
        )

    def test_a_single_tile_takes_the_ratio_of_its_frame(self):
        section = css_section(5)
        rule = re.search(r"\.gallery--single > \.gallery__item \{([^}]*)\}", section).group(1)
        for declaration in ("inline-size: fit-content;", "max-inline-size: 100%;", "aspect-ratio: auto;"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, rule)
        self.assertRegex(section, r"\.gallery--single > \.gallery__item > \.gallery__link \{\s*"
                                  r"position: absolute;\s*inset: 0;")
        sizer = re.search(r"\.gallery__sizer \{([^}]*)\}", section).group(1)
        for declaration in ("max-inline-size: 100%;", "max-block-size: var(--media-max-height);",
                            "visibility: hidden;"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, sizer)
        # without a size in the data the ratio of the token
        default = re.search(r"\.gallery__sizer--default \{([^}]*)\}", section).group(1)
        self.assertIn("calc(var(--media-max-height) * var(--ratio-video))", default)
        self.assertIn("aspect-ratio: var(--ratio-video);", default)

    def test_the_viewer_video(self):
        section = css_section(8)
        self.assertRegex(section, r"\.lightbox__video \{[^}]*--lightbox-ratio-used: "
                                  r"var\(--lightbox-ratio, var\(--ratio-video\)\);")
        self.assertRegex(section, r"\.lightbox__video:focus-visible \{\s*outline: "
                                  r"var\(--focus-ring-width\) solid var\(--color-focus\);")
        self.assertRegex(section, r"\.lightbox:not\(\.is-video\) \.lightbox__video,\s*"
                                  r"\.lightbox\.is-video \.lightbox__image,\s*"
                                  r"\.lightbox\.is-video\.is-error \.lightbox__video \{\s*display: none;")


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

    def test_without_the_script_the_tile_is_a_link_to_the_file_with_the_poster(self):
        site = build.load_data(ROOT / "examples" / "data")[0]
        video, poster = site["video"]["file"], site["video"]["poster"]
        out = self.build("data")
        for page in self.pages(out):
            tile = re.search(r'<a class="gallery__link media-tile media-tile--video" [^>]*>', page)
            self.assertIsNotNone(tile)
            href = re.search(r'href="([^"]+)"', tile.group(0)).group(1)
            self.assertTrue(href.endswith("/" + video))
            self.assertTrue((out / href.lstrip("/")).is_file())
            self.assertIn(f'data-media-poster="{href[: -len(video)]}{poster}"', tile.group(0))
            self.assertIn(f'<img src="{href[: -len(video)]}{poster}" alt=""', page)
            # no <video> on the page: nothing is fetched before the tap
            self.assertNotIn("<video", page)
            self.assertNotIn("aria-haspopup", page)
            self.assertEqual(page.count('data-media="video"'), 1)

    def test_the_frame_ratio_comes_from_the_size_in_the_data(self):
        site, _invitations = build.load_data(ROOT / "examples" / "data")
        width, height = site["video"]["width"], site["video"]["height"]
        self.assertGreater(height, width, "the example clip is a portrait one")
        for page in self.pages(self.build("data")):
            self.assertIn(f'data-media-ratio="{width} / {height}"', page)
            self.assertIn(
                f'<svg class="gallery__sizer" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}"', page)
            self.assertIn(f'alt="" width="{width}" height="{height}"', page)

    def test_pages_have_no_inline_styles(self):
        """The pages work under a style-src without 'unsafe-inline': they
        have no style attributes and no <style> elements."""
        self.assertIn("style-src 'self';", build.HEADERS_TEXT)
        self.assertNotIn("unsafe-inline", build.HEADERS_TEXT)
        style_attribute = re.compile(r"<[a-zA-Z][^>]*\sstyle\s*=", re.S)
        for data_name in ("data", "data-venue-pending"):
            out = self.build(data_name)
            for path in sorted(out.rglob("*.html")):
                with self.subTest(data=data_name, page=str(path.relative_to(out))):
                    page = path.read_text(encoding="utf-8")
                    self.assertNotRegex(page, style_attribute)
                    self.assertNotRegex(page, r"(?i)<style[\s>]")

    def test_without_a_size_the_tile_falls_back_to_the_css_ratio(self):
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
            self.assertEqual(page.count('data-media="video"'), 1)
            self.assertNotIn("data-media-ratio", page)
            self.assertNotIn('<svg class="gallery__sizer"', page)
            self.assertIn('<span class="gallery__sizer gallery__sizer--default" aria-hidden="true">', page)
            self.assertNotIn("<video", page)
        # the tile then takes the ratio of the token
        self.assertRegex(APP_CSS, r"\.gallery__sizer--default \{[^}]*aspect-ratio: var\(--ratio-video\);")

    def test_a_page_without_a_video_has_no_tile(self):
        for page in self.pages(self.build("data-venue-pending")):
            self.assertNotIn("media-tile--video", page)
            self.assertNotIn("data-media", page)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
