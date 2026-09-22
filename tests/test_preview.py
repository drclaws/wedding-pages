"""The link preview image: the preview page, the browser, the build.

The preview page is the cover of the site for nobody in particular: the
date of the main event, the eyebrow only when it does not depend on a
guest, nothing of an invitation.  The browser is replaced by a stand-in
(`tests.fake_preview`) everywhere but in `RealBrowserTests`, which runs when
Chrome or Chromium is found.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from tests import fake_preview
from tests import fixtures_v2 as F
from tests import support
from tests.support import CliTestCase, build
from tools import _page, _png, _preview

REAL = fake_preview.REAL_LINK_PREVIEW_IMAGE
CHROME = _preview.find_chrome()
LONG_NAMES = "Анастасия-Александра и Константин"


def preview_html(site: dict) -> str:
    tree = _page.site_cover(site, F.settings())
    template = build.parse_template(
        (support.ROOT / build.PREVIEW_TEMPLATE_FILE).read_text(encoding="utf-8"),
        build.PREVIEW_TEMPLATE_FILE,
        build.load_fragments(support.ROOT / build.FRAGMENTS_DIRNAME),
    )
    return build.strip_html_comments(template.render(tree), "preview")


class PreviewPageTests(unittest.TestCase):
    def test_the_cover_of_the_site(self):
        page = preview_html(F.site())
        self.assertIn('<html lang="ru" class="invitation link-preview">', page)
        self.assertIn('<h1 class="cover__title" id="s-cover--title">Алиса и Боб</h1>', page)
        # the main event, whatever the primary event of a guest
        self.assertIn('<time datetime="2030-06-15T16:00:00+03:00">15 июня 2030</time>', page)
        self.assertIn('class="cover__photo cover__photo--center" src="/assets/', page)
        self.assertIn('href="/assets/preview.css"', page)
        self.assertNotIn("<script", page)

    def test_nothing_of_a_guest(self):
        site = F.site()
        site["sections"][0]["eyebrow"] = {"ty": "Тебя ждём", "vy": "Вас ждём"}
        page = preview_html(site)
        for invitation in F.INVITATIONS:
            self.assertNotIn(invitation["greeting"], page)
            self.assertNotIn(invitation["token"], page)
        for text in ("Тебя ждём", "Вас ждём", "Второй день", "Регистрация", "11:00"):
            self.assertNotIn(text, page)

    def test_the_eyebrow_only_when_it_is_the_same_for_everybody(self):
        cases = {
            "Приглашение на свадьбу": "Приглашение на свадьбу",
            "{coupleNames} · {eventDate}": "Алиса и Боб · 15 июня 2030",
            "{text:announce}": "Мы, Алиса и Боб, женимся!",
            "{greeting}": None,
            "{text:invite}": "Ждём вас 15 июня 2030 в 16:00.",
        }
        for eyebrow, expected in cases.items():
            with self.subTest(eyebrow=eyebrow):
                site = F.site()
                site["sections"][0]["eyebrow"] = eyebrow
                self.assertEqual(_page.site_eyebrow(site, eyebrow), expected)
                page = preview_html(site)
                if expected is None:
                    self.assertNotIn("cover__eyebrow", page)
                else:
                    self.assertIn(f'cover__eyebrow">{expected}</p>', page)
        site = F.site()
        site["texts"]["invite"].pop("all")
        self.assertIsNone(_page.site_eyebrow(site, "{text:invite}"))
        self.assertIsNone(_page.site_eyebrow(site, {"ty": "a", "vy": "b"}))
        self.assertEqual(_page.site_eyebrow(site, None), "")

    def test_a_site_without_a_cover(self):
        site = F.site()
        site["sections"].pop(0)
        self.assertIsNone(_page.site_cover(site, F.settings()))

    def test_the_preview_style_sheet_keeps_the_text_in_the_middle_square(self):
        css = (support.ROOT / "assets" / build.PREVIEW_STYLESHEET).read_text(encoding="utf-8")
        # 630 of 1200 pixels: what a square crop of the middle keeps
        self.assertIn("max-inline-size: 52.5vw;", css)
        self.assertIn(".link-preview .cover-bar", css)


def executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class FindChromeTests(support.TempDirTestCase):
    def test_the_option_then_the_variable_then_the_usual_places(self):
        first = executable(self.tmp / "first")
        second = executable(self.tmp / "second")
        usual = executable(self.tmp / "usual")
        in_path = self.tmp / "bin"
        in_path.mkdir()
        chromium = executable(in_path / "chromium")
        environ = {"CHROME": second, "PATH": str(in_path)}
        self.assertEqual(_preview.find_chrome(first, environ, (usual,)), first)
        self.assertEqual(_preview.find_chrome(None, environ, (usual,)), second)
        self.assertEqual(_preview.find_chrome(None, {"PATH": str(in_path)}, (usual,)), usual)
        self.assertEqual(_preview.find_chrome(None, {"PATH": str(in_path)}, ()), chromium)
        self.assertIsNone(_preview.find_chrome(None, {"PATH": str(self.tmp / "none")}, ()))

    def test_a_given_path_must_be_a_browser(self):
        secret = str(self.tmp / "secret-place" / "chrome")
        for explicit, environ, name in ((secret, {}, "--chrome"), (None, {"CHROME": secret}, "CHROME")):
            with self.subTest(name=name):
                with self.assertRaises(_preview.PreviewError) as caught:
                    _preview.find_chrome(explicit, environ, ())
                self.assertEqual(
                    str(caught.exception), f"{name} does not name an executable file of a browser"
                )
                self.assertNotIn("secret-place", str(caught.exception))

    def test_no_browser_is_an_error_with_a_hint(self):
        with mock.patch.object(_preview, "find_chrome", return_value=None):
            with self.assertRaises(_preview.PreviewError) as caught:
                REAL("<html></html>", {})
        self.assertIn("no browser found", str(caught.exception))
        self.assertIn("--no-link-preview-image", str(caught.exception))
        self.assertIn("CHROME", str(caught.exception))


class _FakeBrowser:
    """Answers the DevTools calls of `_render` without a browser."""

    def __init__(self, fits_from: int, sizes: dict[int, int]):
        self.fits_from = fits_from
        self.sizes = sizes
        self.viewport = None
        self.calls: list[str] = []

    def call(self, method, params=None, session=None):
        self.calls.append(method)
        if method == "Browser.getVersion":
            return {"product": "Chrome/1.2.3"}
        if method == "Target.createTarget":
            return {"targetId": "t"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s"}
        if method == "Emulation.setDeviceMetricsOverride":
            self.viewport = (params["width"], params["height"])
            self.scale = params["deviceScaleFactor"]
        if method == "Runtime.evaluate":
            if params["expression"] is _preview._FIT_SCRIPT:
                return {"result": {"value": self.viewport[0] >= self.fits_from}}
            if params["expression"] is _preview._TEXT_BOX_SCRIPT:
                return {"result": {"value": [150, 20, 450, 290]}}
            return {"result": {"value": True}}
        if method == "Page.captureScreenshot":
            size = self.sizes.get(params["quality"], 1000)
            return {"data": base64.b64encode(bytes([params["quality"]]) * size).decode()}
        return {}

    def wait_for(self, method, session):
        return {}


class RenderLogicTests(unittest.TestCase):
    def test_the_first_window_the_cover_fits_into(self):
        browser = _FakeBrowser(fits_from=800, sizes={})
        image = _preview._render(browser, "http://127.0.0.1:1/")
        self.assertEqual(image.viewport, (800, 420))
        self.assertEqual((image.width, image.height, image.quality), (1200, 630, 85))
        self.assertEqual(image.browser, "Chrome/1.2.3")
        self.assertEqual(image.text_box, (225.0, 30.0, 675.0, 435.0))
        self.assertIn("Emulation.setScriptExecutionDisabled", browser.calls)

    def test_nothing_fits_the_widest_window_is_taken(self):
        image = _preview._render(_FakeBrowser(fits_from=5000, sizes={}), "http://127.0.0.1:1/")
        self.assertEqual(image.viewport, (1200, 630))

    def test_the_quality_goes_down_until_the_image_is_small_enough(self):
        big = _preview.MAX_IMAGE_BYTES + 1
        sizes = {85: big, 80: big, 75: 1000}
        image = _preview._render(_FakeBrowser(600, sizes), "http://127.0.0.1:1/")
        self.assertEqual(image.quality, 75)
        self.assertTrue(image.fits)
        image = _preview._render(
            _FakeBrowser(600, {quality: big for quality in _preview.QUALITIES}), "http://127.0.0.1:1/"
        )
        self.assertEqual((image.quality, image.fits), (60, False))

    def test_captures_until_two_are_the_same(self):
        class Flaky(_FakeBrowser):
            count = 0

            def call(self, method, params=None, session=None):
                if method == "Page.captureScreenshot":
                    self.count += 1
                    data = b"blank" if self.count == 1 else b"final"
                    return {"data": base64.b64encode(data).decode()}
                return super().call(method, params, session)

        browser = Flaky(600, {})
        self.assertEqual(_preview._render(browser, "u").content, b"final")
        self.assertEqual(browser.count, 3)


class LinkPreviewBuildTests(CliTestCase):
    def build(self, *extra, env=None):
        result = self.build_in_process(*extra, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def og_image(self, token=support.TOKEN_A) -> str:
        page = (self.out / "i" / token / "index.html").read_text(encoding="utf-8")
        return page.split('<meta property="og:image" content="', 1)[1].split('"', 1)[0]

    def test_the_cover_is_published_in_the_media_directory(self):
        fake_preview.CALLS.clear()
        result = self.build()
        name = hashlib.sha256(fake_preview.FAKE_JPEG).hexdigest()[:16] + ".jpg"
        self.assertEqual(self.og_image(), f"/assets/{support.MEDIA_DIR}/{name}")
        self.assertEqual(
            (self.out / "assets" / support.MEDIA_DIR / name).read_bytes(), fake_preview.FAKE_JPEG
        )
        for token in (support.TOKEN_B, support.TOKEN_C):
            self.assertEqual(self.og_image(token), self.og_image())
        # nothing with data at an address that can be guessed
        self.assertEqual([n for n in support.tree_files(self.out) if "og." in n], [])
        self.assertFalse((self.out / "assets" / build.PREVIEW_STYLESHEET).exists())
        self.assertIn(
            "build: link preview image: the cover, 1200x630 JPEG, 32 B, quality 85, CSS window "
            f"600x315, rendered by {fake_preview.FAKE_BROWSER}",
            result.stdout,
        )
        self.assertNoPrivateData(result.stdout, result.stderr)
        page, files = fake_preview.CALLS[0]
        self.assertEqual(
            sorted(files),
            ["/assets/app.css", "/assets/fonts/sans.woff2", "/assets/preview.css"],
        )
        self.assertIn(support.COUPLE_NAMES, page)
        for secret in (support.GREETING_TY, support.NOTE, support.HIDDEN_EVENT_TITLE):
            self.assertNotIn(secret, page)

    def test_the_photo_of_the_cover_is_served_to_the_browser(self):
        site = support.site_data()
        site["sections"][0].update(background="venue-1", backgroundFocus="top")
        support.write_data(self.data, site=site)
        fake_preview.CALLS.clear()
        self.build()
        page, files = fake_preview.CALLS[0]
        src = page.split('class="cover__photo cover__photo--top" src="', 1)[1].split('"', 1)[0]
        self.assertEqual(files[src], self.media / "venue-1.png")

    def test_turned_off(self):
        for extra, env in ((("--no-link-preview-image",), None), ((), {"LINK_PREVIEW_IMAGE": "off"})):
            with self.subTest(extra=extra, env=env):
                fake_preview.CALLS.clear()
                result = self.build(*extra, env=env)
                self.assertEqual(fake_preview.CALLS, [])
                self.assertEqual(self.og_image(), "/assets/og.png")
                self.assertTrue((self.out / "assets" / "og.png").is_file())
                self.assertIn("(the link preview image is turned off)", result.stdout)

    def test_a_bad_switch(self):
        result = self.build_in_process(env={"LINK_PREVIEW_IMAGE": "maybe"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("LINK_PREVIEW_IMAGE: must be 'on' or 'off'", result.stderr)

    def test_a_site_without_a_cover_gets_the_neutral_picture(self):
        site = support.site_data()
        site["sections"].pop(0)
        support.write_data(self.data, site=site)
        fake_preview.CALLS.clear()
        result = self.build()
        self.assertEqual(fake_preview.CALLS, [])
        self.assertEqual(self.og_image(), "/assets/og.png")
        self.assertIn("(the site has no cover to show in link previews)", result.stdout)

    def test_without_a_browser_the_build_fails_with_a_hint(self):
        with mock.patch.object(_preview, "link_preview_image", REAL), mock.patch.object(
            _preview, "find_chrome", return_value=None
        ):
            result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: link preview image: no browser found", result.stderr)
        self.assertIn("--no-link-preview-image", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_bad_chrome_option(self):
        with mock.patch.object(_preview, "link_preview_image", REAL):
            result = self.build_in_process("--chrome", str(self.tmp / "nowhere" / "chrome"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--chrome does not name an executable file of a browser", result.stderr)
        self.assertNotIn("nowhere", result.stderr)

    def test_a_render_error_fails_the_build(self):
        def broken(*_args, **_kwargs):
            raise _preview.PreviewError("the browser closed the connection")

        with mock.patch.object(_preview, "link_preview_image", broken):
            result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: link preview image: the browser closed the connection", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_large_image_is_a_warning(self):
        def large(*_args, **_kwargs):
            return _preview.PreviewImage(
                content=fake_preview.FAKE_JPEG, width=1200, height=630, quality=60,
                browser="X", viewport=(600, 315), fits=False,
            )  # fmt: skip

        with mock.patch.object(_preview, "link_preview_image", large):
            result = self.build()
        self.assertIn("warning: the link preview image is 32 B even at JPEG quality 60", result.stderr)

    def test_validate_notes_an_eyebrow_that_depends_on_the_guest(self):
        site = support.site_data()
        site["sections"][0]["eyebrow"] = "{greeting}"
        support.write_data(self.data, site=site)
        result = support.run_main(self.code, "validate", "--data", self.data, "--media", self.media)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validate: the eyebrow of the cover depends on the guest", result.stdout)
        support.write_data(self.data)
        result = support.run_main(self.code, "validate", "--data", self.data, "--media", self.media)
        self.assertNotIn("eyebrow", result.stdout)


@unittest.skipUnless(CHROME, "Chrome or Chromium is not installed")
class RealBrowserTests(support.TempDirTestCase):
    """The real browser: a JPEG of the right size, twice the same bytes."""

    def render(self, site: dict) -> _preview.PreviewImage:
        assets = support.ROOT / "assets"
        files = {
            "/assets/app.css": assets / "app.css",
            "/assets/preview.css": assets / build.PREVIEW_STYLESHEET,
        }
        return REAL(preview_html(site), files)

    def test_the_image(self):
        site = F.site()
        site["sections"][0].pop("background")  # no media files in the fixture
        first = self.render(site)
        self.assertEqual(first.content[:3], b"\xff\xd8\xff")
        self.assertEqual(_png.jpeg_size(first.content), (1200, 630))
        self.assertLessEqual(len(first.content), _preview.MAX_IMAGE_BYTES)
        self.assertEqual(first.viewport, (600, 315))
        self.assertTrue(first.browser.startswith(("Chrome/", "HeadlessChrome/", "Chromium/")))
        self.assertEqual(self.render(site).content, first.content)
        self.assertEqual(os.listdir(self.tmp), [])  # nothing is left behind

    def test_long_names_stay_in_the_middle_square(self):
        site = F.site()
        site["sections"][0].pop("background")
        site["coupleNames"] = LONG_NAMES
        image = self.render(site)
        left, top, right, bottom = image.text_box
        # the square crop of the middle keeps x from 285 to 915
        self.assertGreaterEqual(left, 285)
        self.assertLessEqual(right, 915)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(bottom, 630)
        # the layout of a phone, with the largest text, still fits
        self.assertEqual(image.viewport, (600, 315))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
