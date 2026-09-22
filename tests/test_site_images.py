"""The icon and the link preview image in a build: generated from the design
tokens, replaced from the media directory; no address of the site."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
from html.parser import HTMLParser

from tests import fake_preview, support
from tests.support import CliTestCase, build, tree_digest, tree_files

from tools import _png, gen_assets

BASE_URL = "https://invite.example.invalid"
#: A JPEG that is nothing but its headers: enough for the size to be read.
TINY_JPEG = (
    b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    + b"\xff\xc0" + struct.pack(">HBHHB", 8, 8, 600, 1000, 0) + b"\xff\xd9"
)  # fmt: skip
PLAIN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8"/></svg>\n'


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.names: dict[str, list[str]] = {}
        self.icons: list[dict[str, str]] = []
        self.root: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "html":
            self.root = values
        if tag == "meta" and "property" in values:
            self.meta[values["property"]] = values.get("content", "")
        if tag == "meta" and "name" in values:
            self.names.setdefault(values["name"], []).append(values.get("content", ""))
        if tag == "link" and values.get("rel") == "icon":
            self.icons.append(values)


def head_of(document: str) -> _Head:
    parser = _Head()
    parser.feed(document)
    return parser


class SiteImagesTestCase(CliTestCase):
    def build(self, *extra: str, env: dict | None = None):
        result = self.build_in_process(*extra, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def page(self, token: str = support.TOKEN_A) -> _Head:
        return head_of((self.out / "i" / token / "index.html").read_text(encoding="utf-8"))

    def stub(self) -> str:
        return (self.out / "index.html").read_text(encoding="utf-8")


class GeneratedImagesTests(SiteImagesTestCase):
    """The neutral picture of the tokens: the cover is not rendered."""

    def build(self, *extra: str, env: dict | None = None):
        return super().build("--no-link-preview-image", *extra, env=env)

    def test_files_and_links(self):
        self.build()
        palette = gen_assets.load_palette(self.code / "assets" / "app.css")
        self.assertEqual(
            (self.out / "assets" / "og.png").read_bytes(), gen_assets.og_png(palette)
        )
        self.assertEqual(
            (self.out / "assets" / "favicon.svg").read_bytes(), gen_assets.favicon_svg(palette)
        )
        for token in (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C):
            head = self.page(token)
            self.assertEqual(
                head.icons, [{"rel": "icon", "href": "/assets/favicon.svg", "type": "image/svg+xml"}]
            )
            self.assertEqual(head.meta["og:image"], "/assets/og.png")
            self.assertEqual(head.meta["og:image:type"], "image/png")
            self.assertEqual(
                (head.meta["og:image:width"], head.meta["og:image:height"]), ("1200", "630")
            )
            for url in (head.icons[0]["href"], head.meta["og:image"]):
                self.assertTrue((self.out / url.lstrip("/")).is_file(), url)

    def test_stub_has_the_icon_and_nothing_else(self):
        self.build()
        self.assertEqual(
            (self.out / "index.html").read_bytes(), (self.out / "404.html").read_bytes()
        )
        head = head_of(self.stub())
        self.assertEqual(
            head.icons, [{"rel": "icon", "href": "/assets/favicon.svg", "type": "image/svg+xml"}]
        )
        self.assertEqual(head.meta, {})
        self.assertNotIn("og:", self.stub())
        self.assertNotIn("og.png", self.stub())
        self.assertNoPrivateData(self.stub())

    def test_stub_may_use_the_icon_fields_only(self):
        for field in ("ogImage", "ogImageType", "themeColor", "mediaPath", "coupleNames"):
            with self.subTest(field=field):
                (self.code / "stub.html").write_text(
                    support.STUB.replace("</body>", f"<p>{{{{{field}}}}}</p></body>"),
                    encoding="utf-8",
                )
                result = self.build_in_process()
                self.assertEqual(result.returncode, 1)
                self.assertIn("stub.html: must not contain template syntax other than", result.stderr)
                self.assertIn(f"'{{{{{field}}}}}'", result.stderr)

    def test_stub_without_placeholders_still_builds(self):
        (self.code / "stub.html").write_text(
            support.STUB.replace('<link rel="icon" href="{{faviconPath}}" type="{{faviconType}}">\n', ""),
            encoding="utf-8",
        )
        self.build()
        self.assertEqual(head_of(self.stub()).icons, [])

    def test_changing_a_token_changes_the_images(self):
        """Regenerating after a change of the tokens is an ordinary rebuild."""
        self.build()
        before = tree_digest(self.out)
        support.write_app_css(self.code, accent="#0a6b4f")
        self.build()
        after = tree_digest(self.out)
        changed = sorted(name for name in before if before[name] != after[name])
        self.assertEqual(changed, ["assets/app.css", "assets/favicon.svg", "assets/og.png"])
        self.assertIn("#0a6b4f", (self.out / "assets" / "favicon.svg").read_text(encoding="utf-8"))

        support.write_app_css(self.code, line="rgba(0, 0, 0, 0.35)")
        self.build()
        third = tree_digest(self.out)
        self.assertNotEqual(third["assets/og.png"], before["assets/og.png"])
        self.assertEqual(third["assets/favicon.svg"], before["assets/favicon.svg"])

    def test_token_problems_fail_the_build(self):
        cases = {
            ":root{--color-bg:#fff}": "assets/app.css: design token '--color-surface' is not defined",
            support.tokens_css(accent="teal"): "design token '--color-accent': unsupported colour 'teal'",
            support.tokens_css(accent="var(--brand)"): "'--brand' is not defined on :root (referenced by '--color-accent')",
        }  # fmt: skip
        for css, expected in cases.items():
            with self.subTest(expected=expected):
                (self.code / "assets" / "app.css").write_text(css, encoding="utf-8")
                result = self.build_in_process()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertIn("build: failed", result.stderr)
                self.assertNotIn("internal error", result.stderr)
                self.assertFalse(self.out.exists())

    def test_reserved_names_in_assets(self):
        (self.code / "assets" / "OG.png").write_bytes(b"")
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("'OG.png' in", result.stderr)
        self.assertIn("put the file into the media directory", result.stderr)

    def test_log(self):
        result = self.build()
        self.assertIn("build: assets/favicon.svg generated from the design tokens", result.stdout)
        self.assertIn(
            "build: assets/og.png generated from the design tokens (the link preview image "
            "is turned off)",
            result.stdout,
        )
        self.assertNotIn("URL is", result.stdout)  # no line about the address of the site

    def test_command_line_from_another_directory(self):
        """`python path/to/build.py`: the generators are found next to it."""
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        result = self.run_build(cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "assets" / "og.png").is_file())
        self.assertEqual(list(self.code.rglob("__pycache__")), [])


class MediaOverrideTests(SiteImagesTestCase):
    def test_other_files_in_the_media_directory_are_ignored(self):
        self.build()
        expected = tree_digest(self.out)
        (self.media / "favicon.gif").write_bytes(b"GIF89a")
        (self.media / "og.webp").write_bytes(b"RIFF")
        (self.media / "og.jpeg").write_bytes(TINY_JPEG)
        (self.media / "Favicon.svg").write_text(PLAIN_SVG, encoding="utf-8")  # names are exact
        (self.media / "notes.txt").write_text("x", encoding="utf-8")
        self.build()
        self.assertEqual(tree_digest(self.out), expected)

    def test_favicon_variants(self):
        ico = b"\x00\x00\x01\x00" + b"\0" * 18
        png = _png.encode_png(1, 1, [b"\x10\x20\x30"])
        cases = {
            "favicon.svg": (PLAIN_SVG.encode(), "image/svg+xml"),
            "favicon.png": (png, "image/png"),
            "favicon.ico": (ico, "image/x-icon"),
        }
        for name, (content, mime) in cases.items():
            with self.subTest(name=name):
                for other in cases:
                    (self.media / other).unlink(missing_ok=True)
                (self.media / name).write_bytes(content)
                result = self.build()
                self.assertIn(f"build: assets/{name} copied from", result.stdout)
                self.assertEqual((self.out / "assets" / name).read_bytes(), content)
                images = [n for n in tree_files(self.out / "assets") if n.startswith("favicon")]
                self.assertEqual(images, [name])
                for head in (self.page(), head_of(self.stub())):
                    self.assertEqual(
                        head.icons, [{"rel": "icon", "href": f"/assets/{name}", "type": mime}]
                    )
                # the link preview image is still the rendered cover
                self.assertTrue(self.page().meta["og:image"].endswith(".jpg"))

    def test_first_name_wins(self):
        (self.media / "favicon.ico").write_bytes(b"\x00\x00\x01\x00" + b"\0" * 18)
        (self.media / "favicon.svg").write_text(PLAIN_SVG, encoding="utf-8")
        self.build()
        self.assertEqual(self.page().icons[0]["href"], "/assets/favicon.svg")
        self.assertFalse((self.out / "assets" / "favicon.ico").exists())

    def own_image(self, content: bytes, extension: str) -> str:
        """The published path of a picture of one's own: in the media
        directory under the hash of its contents, never at /assets/og.*."""
        name = hashlib.sha256(content).hexdigest()[:16] + extension
        self.assertEqual(
            (self.out / "assets" / support.MEDIA_DIR / name).read_bytes(), content
        )
        for guessable in ("og.png", "og.jpg"):
            self.assertFalse((self.out / "assets" / guessable).exists())
        return f"/assets/{support.MEDIA_DIR}/{name}"

    def test_og_png(self):
        content = _png.encode_png(4, 2, [b"\0" * 12] * 2)
        (self.media / "og.png").write_bytes(content)
        fake_preview.CALLS.clear()
        result = self.build()
        meta = self.page().meta
        self.assertEqual(
            (meta["og:image"], meta["og:image:type"], meta["og:image:width"], meta["og:image:height"]),
            (self.own_image(content, ".png"), "image/png", "4", "2"),
        )
        self.assertEqual(fake_preview.CALLS, [])  # nothing is rendered
        self.assertIn("build: link preview image: og.png of the media directory", result.stdout)

    def test_og_jpg(self):
        (self.media / "og.jpg").write_bytes(TINY_JPEG)
        self.build()
        meta = self.page().meta
        self.assertEqual(
            (meta["og:image"], meta["og:image:type"], meta["og:image:width"], meta["og:image:height"]),
            (self.own_image(TINY_JPEG, ".jpg"), "image/jpeg", "1000", "600"),
        )

    def test_overrides_still_need_the_design_tokens(self):
        """Both images replaced: the pages still take their theme colour from
        the tokens, so broken tokens fail the build all the same."""
        (self.media / "favicon.svg").write_text(PLAIN_SVG, encoding="utf-8")
        (self.media / "og.jpg").write_bytes(TINY_JPEG)
        (self.code / "assets" / "app.css").write_text("body{}", encoding="utf-8")
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("design token '--color-bg' is not defined", result.stderr)
        self.assertIn("the colour of the browser interface", result.stderr)
        self.assertFalse(self.out.exists())

    def test_bad_overrides(self):
        def symlink(path):
            os.symlink(self.media / "route.png", path)

        cases = {
            "og.png": (lambda p: p.write_bytes(b"not an image"), "og.png is not a PNG file"),
            "og.jpg": (lambda p: p.write_bytes(b"\xff\xd8\xff\xd9"), "og.jpg has no readable image size"),
            "favicon.ico": (lambda p: p.write_bytes(b"<svg/>"), "favicon.ico is not a ICO file"),
            "favicon.png": (lambda p: p.mkdir(), "favicon.png is not a regular file"),
            "favicon.svg": (symlink, "favicon.svg is a symbolic link"),
        }  # fmt: skip
        for name, (create, expected) in cases.items():
            with self.subTest(name=name):
                path = self.media / name
                try:
                    create(path)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"cannot prepare the fixture: {exc}")
                for result in (self.build_in_process(), self.run_validate()):
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn(expected, result.stderr)
                self.assertFalse(self.out.exists())
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()

    def test_oversized_override(self):
        (self.media / "og.png").write_bytes(_png.encode_png(1, 1, [b"\0\0\0"]) + b"\0" * 70000)
        with support.mock.patch.object(build, "MAX_FILE_BYTES", 64 * 1024):
            result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"og\.png is 68\.\d KiB, the limit for a single file")

    def test_lookalike_names_are_reported(self):
        """A picture that will never be published should not pass in silence."""
        cases = {
            "OG.PNG": "the name must be exactly 'og' with one of: .jpg, .png",
            "og.jpeg": "the name must be exactly 'og' with one of: .jpg, .png",
            "Favicon.SVG": "the name must be exactly 'favicon' with one of: .ico, .png, .svg",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                path = self.media / name
                path.write_bytes(_png.encode_png(1, 1, [b"\x10\x20\x30"]))
                result = self.build()
                self.assertIn(f"{name} is not published", result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertIn("generated from the design tokens is used", result.stderr)
                # a case-insensitive file system answers exists() for og.png
                self.assertNotIn(name, tree_files(self.out / "assets"))
                path.unlink()

    def test_the_name_that_lost_is_reported(self):
        (self.media / "favicon.svg").write_text(PLAIN_SVG, encoding="utf-8")
        (self.media / "favicon.png").write_bytes(_png.encode_png(1, 1, [b"\x10\x20\x30"]))
        result = self.build()
        self.assertRegex(
            result.stderr, r"favicon\.png is not published: .*favicon\.svg is used instead"
        )
        self.assertNotIn("favicon.svg is not published", result.stderr)
        self.assertEqual(self.page().icons[0]["href"], "/assets/favicon.svg")

    def test_a_chosen_image_is_not_reported(self):
        (self.media / "og.png").write_bytes(_png.encode_png(1200, 630, [b"\0" * 3600] * 630))
        result = self.build()
        self.assertNotIn("is not published", result.stderr)
        self.assertNotIn("link previews expect", result.stderr)

    def test_link_preview_image_with_unexpected_proportions(self):
        wrong = _png.encode_png(600, 600, [b"\0" * 1800] * 600)
        (self.media / "og.png").write_bytes(wrong)
        result = self.build()
        self.assertIn("og.png is 600x600; services that show link previews expect", result.stderr)
        self.assertIn("1200x630 (a ratio of about 1.91:1)", result.stderr)
        # the picture is published all the same: it is the author's choice
        self.own_image(wrong, ".png")
        meta = self.page().meta
        self.assertEqual((meta["og:image:width"], meta["og:image:height"]), ("600", "600"))

    def test_proportions_close_to_the_expected_ones_pass(self):
        for width, height in ((1200, 630), (1280, 640), (1600, 900)):
            with self.subTest(size=(width, height)):
                (self.media / "og.png").write_bytes(
                    _png.encode_png(width, height, [b"\0" * (width * 3)] * height)
                )
                self.assertNotIn("link previews expect", self.build().stderr)

    def test_unsafe_svg_override_fails_the_output_check(self):
        (self.media / "favicon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>', encoding="utf-8"
        )
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("assets/favicon.svg line 1: <script> is not allowed in SVG files", result.stderr)
        self.assertFalse(self.out.exists())


class SiteAddressTests(SiteImagesTestCase):
    """The build never knows the address of the site: every URL of the
    output is a path from the site root."""

    def test_the_option_is_gone(self):
        result = self.build_in_process("--base-url", BASE_URL)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --base-url", result.stderr)
        self.assertFalse(self.out.exists())

    def test_the_old_variable_is_not_read(self):
        result = self.build(env={"SITE_BASE_URL": BASE_URL})
        self.assertTrue(self.page().meta["og:image"].startswith(f"/assets/{support.MEDIA_DIR}/"))
        self.assertNotIn("invite.example", result.stdout + result.stderr)
        for name in tree_files(self.out):
            self.assertNotIn(b"invite.example", (self.out / name).read_bytes(), name)

    def test_foreign_absolute_url_is_an_error(self):
        template = support.TEMPLATE.replace(
            'content="{{ogImage}}"', 'content="https://cdn.example.invalid/og.png"'
        )
        (self.code / "template.html").write_text(template, encoding="utf-8")
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "<meta og:image>: external URL 'https://cdn.example.invalid/…'", result.stderr
        )
        self.assertFalse(self.out.exists())

    def test_any_absolute_url_in_meta_is_an_error(self):
        def problems(document: str) -> list[str]:
            found = build.check_html(document, {"assets/og.png"}.__contains__)
            return [message for _line, message in found]

        self.assertEqual(problems('<meta property="og:image" content="/assets/og.png">'), [])
        cases = {
            f'<meta property="og:image" content="{BASE_URL}/assets/og.png">': "external URL",
            '<meta property="og:image" content="//invite.example.invalid/assets/og.png">': "external URL",
            f'<meta property="og:image:secure_url" content="{BASE_URL}/assets/og.png">': "external URL",
            f'<meta property="og:url" content="{BASE_URL}/">': "external URL",
            f'<meta name="twitter:image" content="{BASE_URL}/assets/og.png">': "external URL",
            f'<meta name="description" content="{BASE_URL}/assets/og.png">': "external URL",
            '<meta property="og:image" content="/assets/missing.png">': "does not exist",
            '<meta property="og:image" content="assets/og.png">': "relative path",
        }  # fmt: skip
        for document, expected in cases.items():
            with self.subTest(document=document):
                found = problems(document)
                self.assertEqual(len(found), 1, found)
                self.assertIn(expected, found[0])


class ContextFieldTests(support.TempDirTestCase):
    def test_defaults(self):
        data = build.load_data(support.write_data(self.tmp / "data"))
        context = build.page_trees(data, build.site_images_context())[0]
        self.assertEqual(
            {key: context[key] for key in build.site_images_context()},
            {
                "faviconPath": "/assets/favicon.svg",
                "faviconType": "image/svg+xml",
                "ogImage": "/assets/og.png",
                "ogImageType": "image/png",
                "ogImageWidth": 1200,
                "ogImageHeight": 630,
            },
        )

    def test_stub_fields_are_not_personal(self):
        self.assertLessEqual(set(build.STUB_FIELDS), set(build.site_images_context()))
        self.assertFalse(any(re.match("og", field) for field in build.STUB_FIELDS))

    def test_real_template_and_stub(self):
        template = (support.ROOT / "template.html").read_text(encoding="utf-8")
        stub = (support.ROOT / "stub.html").read_text(encoding="utf-8")
        icon = '<link rel="icon" href="{{faviconPath}}" type="{{faviconType}}">'
        for document in (template, stub):
            self.assertEqual(document.count(icon), 1)
            self.assertNotIn("data:,", document)
        self.assertIn('<meta property="og:image" content="{{ogImage}}">', template)
        self.assertIn('<meta property="og:image:width" content="{{ogImageWidth}}">', template)
        self.assertNotIn("og:", stub)
        self.assertEqual(set(re.findall(r"\{\{(.*?)\}\}", stub)), set(build.STUB_FIELDS))


class ThemeColorTests(SiteImagesTestCase):
    """`<meta name="theme-color">`: the colour of the folded cover bar, taken
    by the build from the design tokens (`--color-cover-veil`)."""

    #: the fixture: `--color-cover-veil: var(--color-surface)`
    FIXTURE_COLOUR = support.TOKEN_COLORS["--color-surface"]

    def theme_colors(self, token: str = support.TOKEN_A) -> list[str]:
        return self.page(token).names.get("theme-color", [])

    def test_every_page_gets_the_colour_of_the_bar(self):
        self.build()
        palette = gen_assets.load_palette(self.code / "assets" / "app.css")
        self.assertEqual(gen_assets.hex_color(palette.cover_veil), self.FIXTURE_COLOUR)
        for token in (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C):
            with self.subTest(token=token):
                self.assertEqual(self.theme_colors(token), [self.FIXTURE_COLOUR])

    def test_the_stub_has_no_theme_colour(self):
        self.build()
        self.assertNotIn("theme-color", self.stub())
        self.assertNotIn(self.FIXTURE_COLOUR, self.stub())

    def test_changing_the_token_changes_only_the_meta_tag(self):
        self.build()
        before = tree_digest(self.out)
        pages = {
            token: (self.out / "i" / token / "index.html").read_text(encoding="utf-8")
            for token in (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C)
        }
        support.write_app_css(self.code, cover_veil="#123abc")
        self.build()
        after = tree_digest(self.out)
        changed = sorted(name for name in before if before[name] != after[name])
        self.assertEqual(
            changed,
            ["assets/app.css"] + sorted(f"i/{token}/index.html" for token in pages),
        )
        for token, page in pages.items():
            with self.subTest(token=token):
                self.assertEqual(self.theme_colors(token), ["#123abc"])
                now = (self.out / "i" / token / "index.html").read_text(encoding="utf-8")
                self.assertEqual(
                    now.replace('content="#123abc"', f'content="{self.FIXTURE_COLOUR}"'), page
                )

    def test_the_colour_is_resolved_and_made_opaque(self):
        cases = {
            "var(--color-accent)": support.TOKEN_COLORS["--color-accent"],
            "#ABC": "#aabbcc",
            "rgb(18 52 86)": "#123456",
            # flattened on the page background (#fdfcfa) like the images
            "rgba(0, 0, 0, 0.5)": "#7f7e7d",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                support.write_app_css(self.code, cover_veil=value)
                self.build()
                self.assertEqual(self.theme_colors(), [expected])

    def test_token_problems_fail_the_build(self):
        cases = {
            "hsl(200 50% 30%)": "design token '--color-cover-veil': unsupported colour 'hsl(200 50% 30%)'",
            "color-mix(in srgb, #fff, #000)": "design token '--color-cover-veil': unsupported colour",
            "var(--brand)": "'--brand' is not defined on :root (referenced by '--color-cover-veil')",
        }  # fmt: skip
        for value, expected in cases.items():
            with self.subTest(value=value):
                support.write_app_css(self.code, cover_veil=value)
                result = self.build_in_process()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("internal error", result.stderr)
                self.assertFalse(self.out.exists())
        css =support.tokens_css().replace("--color-cover-veil:", "--color-cover-veil-x:")
        (self.code / "assets" / "app.css").write_text(css, encoding="utf-8")
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("design token '--color-cover-veil' is not defined", result.stderr)


class RealTemplateThemeColorTests(SiteImagesTestCase):
    """The same with the real `template.html` and `app.css`."""

    template = (support.ROOT / "template.html").read_text(encoding="utf-8")

    def test_the_meta_tag_and_the_class_of_the_page(self):
        for name in ("app.css", "app.js"):
            shutil.copyfile(support.ROOT / "assets" / name, self.code / "assets" / name)
        self.build()
        expected = gen_assets.hex_color(
            gen_assets.load_palette(support.ROOT / "assets" / "app.css").cover_veil
        )
        for token in (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C):
            with self.subTest(token=token):
                head = self.page(token)
                self.assertEqual(head.names.get("theme-color"), [expected])
                self.assertEqual(head.root.get("class"), "invitation")

    def test_the_template_holds_no_colour(self):
        self.assertEqual(
            self.template.count('<meta name="theme-color" content="{{themeColor}}">'), 1
        )
        self.assertIsNone(
            re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(",
                      self.template)
        )

    def test_the_stub_keeps_its_own_canvas(self):
        stub = (support.ROOT / "stub.html").read_text(encoding="utf-8")
        self.assertNotIn("theme-color", stub)
        self.assertNotIn("invitation", stub)
