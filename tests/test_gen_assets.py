"""Tests for the design token reader, the PNG writer and `tools/gen_assets.py`."""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from tests import support
from tests.support import build

from tools import _png, _tokens, gen_assets

APP_CSS = support.ROOT / "assets" / "app.css"
SCRIPT = support.ROOT / "tools" / "gen_assets.py"


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """(type, payload) of every chunk; the signature and the CRCs are verified."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG file")
    chunks = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        if crc != zlib.crc32(kind + payload) & 0xFFFFFFFF:
            raise AssertionError(f"bad CRC in chunk {kind!r}")
        chunks.append((kind, payload))
        offset += 12 + length
    return chunks


class TokenParsingTests(unittest.TestCase):
    def test_root_declarations(self):
        tokens = _tokens.parse_tokens(
            "/* :root { --ghost: #000; } */\n"
            ":root {\n"
            "  color-scheme: only light;\n"
            "  --color-bg: #ffffff;   /* TEMP  page; with a semicolon */\n"
            '  --font-body: system-ui, "Segoe UI; x", sans-serif;\n'
            "  --fluid: calc((100vw - var(--vw-min) * 1px)\n"
            "    / (var(--vw-max) - var(--vw-min)));\n"
            "  --last: 1\n"
            "}\n"
            "@media (min-width: 768px) { :root { --color-bg: #000000; --only-wide: 1; } }\n"
            ".lightbox { --color-text: #fff; }\n"
        )
        self.assertEqual(tokens["--color-bg"], "#ffffff")
        self.assertEqual(tokens["--font-body"], 'system-ui, "Segoe UI; x", sans-serif')
        self.assertEqual(
            tokens["--fluid"],
            "calc((100vw - var(--vw-min) * 1px) / (var(--vw-max) - var(--vw-min)))",
        )
        self.assertEqual(tokens["--last"], "1")
        for absent in ("--ghost", "--only-wide", "--color-text", "color-scheme"):
            self.assertNotIn(absent, tokens)

    def test_later_root_rule_wins(self):
        tokens = _tokens.parse_tokens(":root{--a:#111}\nhtml:root{--b:#222}\n:root{--a:#333}")
        self.assertEqual(tokens["--a"], "#333")

    def test_var_references(self):
        tokens = {
            "--base": "#1f5673",
            "--alias": "var(--base)",
            "--alias-2": "var( --alias )",
            "--fallback": "var(--nowhere, #abc)",
            "--dangling": "var(--nowhere)",
            "--loop-a": "var(--loop-b)",
            "--loop-b": "var(--loop-a)",
        }
        self.assertEqual(_tokens.resolve(tokens, "--alias-2"), "#1f5673")
        self.assertEqual(_tokens.color_token(tokens, "--alias-2"), (0x1F, 0x56, 0x73, 1.0))
        self.assertEqual(_tokens.resolve(tokens, "--fallback"), "#abc")
        with self.assertRaisesRegex(_tokens.TokenError, r"'--nowhere'.*referenced by '--dangling'"):
            _tokens.resolve(tokens, "--dangling")
        with self.assertRaisesRegex(_tokens.TokenError, "'--loop-a' refers to itself"):
            _tokens.resolve(tokens, "--loop-a")

    def test_colour_notations(self):
        cases = {
            "#fff": (255, 255, 255, 1.0),
            "#1F5673": (0x1F, 0x56, 0x73, 1.0),
            "#0008": (0, 0, 0, 0x88 / 255),
            "#11223380": (0x11, 0x22, 0x33, 0x80 / 255),
            "rgb(14, 16, 18)": (14, 16, 18, 1.0),
            "rgba(14, 16, 18, 0.78)": (14, 16, 18, 0.78),
            "RGBA( 14 16 18 / 50% )": (14, 16, 18, 0.5),
            "rgb(100%, 0%, 50%)": (255, 0, 128, 1.0),
            "rgb(300, -5, 0)": (255, 0, 0, 1.0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_tokens.parse_color(text), expected)

    def test_unsupported_colour_names_the_token(self):
        for text in ("teal", "hsl(200 50% 30%)", "#12", "#12345", "rgb(1, 2)", "rgb(a, b, c)",
                     "color-mix(in srgb, #fff, #000)", ""):  # fmt: skip
            with self.subTest(text=text):
                with self.assertRaisesRegex(
                    _tokens.TokenError, r"'--color-accent': unsupported colour .*expected #rgb"
                ):
                    _tokens.parse_color(text, "--color-accent")

    def test_missing_token_is_named(self):
        for name in _tokens.PALETTE_TOKENS.values():
            with self.subTest(name=name):
                css = support.tokens_css().replace(f"{name}:", f"{name}-x:")
                with self.assertRaisesRegex(_tokens.TokenError, f"'{name}' is not defined"):
                    _tokens.palette_from_tokens(_tokens.parse_tokens(css))

    def test_translucent_colours_are_flattened(self):
        css = support.tokens_css(bg="#000000", line="rgba(255, 255, 255, 0.5)")
        palette = _tokens.palette_from_tokens(_tokens.parse_tokens(css))
        self.assertEqual(palette.line, (128, 128, 128))

    def test_the_real_style_sheet(self):
        """Every role resolves, and to what the file says (whatever that is)."""
        text = APP_CSS.read_text(encoding="utf-8")
        palette = gen_assets.load_palette(APP_CSS)
        self.assertEqual(set(palette._fields), set(_tokens.PALETTE_TOKENS))
        for role, name in _tokens.PALETTE_TOKENS.items():
            declared = re.search(rf"^\s*{name}:\s*(#[0-9a-fA-F]{{6}});", text, re.MULTILINE)
            if declared:  # a plain hex value can be compared literally
                self.assertEqual(_tokens.hex_color(getattr(palette, role)), declared.group(1).lower())

    def test_unreadable_style_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(_tokens.TokenError, "app.css: cannot read"):
                gen_assets.load_palette(Path(tmp) / "app.css")
            (Path(tmp) / "app.css").write_bytes(b":root{--color-bg:\xff}")
            with self.assertRaisesRegex(_tokens.TokenError, "not valid UTF-8"):
                gen_assets.load_palette(Path(tmp) / "app.css")


class OgImageTests(unittest.TestCase):
    def setUp(self):
        self.palette = _tokens.palette_from_tokens(_tokens.parse_tokens(support.tokens_css()))

    def test_png_structure(self):
        data = gen_assets.og_png(self.palette)
        chunks = png_chunks(data)
        # nothing but the image: no text, time or other metadata chunks
        self.assertEqual([kind for kind, _ in chunks], [b"IHDR", b"IDAT", b"IEND"])
        width, height, depth, color_type, *_rest = struct.unpack(">IIBBBBB", chunks[0][1])
        self.assertEqual((width, height, depth, color_type), (1200, 630, 8, 2))
        self.assertEqual(_png.png_size(data), (1200, 630))
        raw = zlib.decompress(chunks[1][1])
        self.assertEqual(len(raw), 630 * (1 + 1200 * 3))
        # the corner is the page background, the centre is the mark on the accent
        self.assertEqual(tuple(raw[1:4]), self.palette.bg)
        middle = 315 * (1 + 1200 * 3) + 1 + 600 * 3
        self.assertEqual(tuple(raw[middle : middle + 3]), self.palette.on_accent)

    def test_size_stays_small(self):
        self.assertLess(len(gen_assets.og_png(self.palette)), 64 * 1024)

    def test_deterministic(self):
        first = gen_assets.og_canvas(self.palette).to_png()
        self.assertEqual(gen_assets.og_canvas(self.palette).to_png(), first)
        self.assertEqual(gen_assets.og_png(self.palette), first)

    def test_every_role_is_used(self):
        """Changing any token of the images changes the picture.

        `cover_veil` is the colour of the browser interface (`themeColor` of
        the pages), not a colour of the images."""
        first = gen_assets.og_png(self.palette)
        for role in self.palette._fields:
            if role == "cover_veil":
                self.assertEqual(
                    gen_assets.og_png(self.palette._replace(cover_veil=(1, 2, 3))), first
                )
                continue
            with self.subTest(role=role):
                changed = self.palette._replace(**{role: (1, 2, 3)})
                self.assertNotEqual(gen_assets.og_png(changed), first)

    def test_poster_has_the_requested_size(self):
        canvas = gen_assets.poster_canvas(90, 160, self.palette)
        self.assertEqual(_png.png_size(canvas.to_png()), (90, 160))


class ImageSizeTests(unittest.TestCase):
    def test_png_size(self):
        self.assertEqual(_png.png_size(_png.encode_png(3, 2, [b"\0" * 9] * 2)), (3, 2))
        self.assertIsNone(_png.png_size(b"\x89PNG\r\n\x1a\n"))
        self.assertIsNone(_png.png_size(b"GIF89a" + b"\0" * 40))

    def test_jpeg_size(self):
        app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
        frame = b"\xff\xc2" + struct.pack(">HBHHB", 8, 8, 630, 1200, 0)
        self.assertEqual(_png.jpeg_size(b"\xff\xd8" + app0 + frame), (1200, 630))
        self.assertIsNone(_png.jpeg_size(b"\xff\xd8" + app0))
        self.assertIsNone(_png.jpeg_size(b"\xff\xd8" + app0 + b"\xff\xda\0\x02"))
        self.assertIsNone(_png.jpeg_size(b"not a jpeg"))


class CanvasTests(unittest.TestCase):
    def test_shapes_are_anti_aliased_and_clipped(self):
        canvas = _png.Canvas(40, 40, (255, 255, 255))
        canvas.disc(20, 20, 12.5, (0, 0, 0))
        values = {bytes(row[x * 3 : x * 3 + 3])[0] for row in canvas.rows for x in range(40)}
        self.assertIn(0, values)
        self.assertIn(255, values)
        self.assertGreater(len(values), 5)  # partial coverage along the edge
        canvas.disc(-10, 50, 30, (9, 9, 9))  # mostly outside: no error
        canvas.ring(20, 20, 100, 90, (9, 9, 9))
        self.assertEqual(len(canvas.rows), 40)
        self.assertTrue(all(len(row) == 120 for row in canvas.rows))

    def test_ring_keeps_what_is_inside(self):
        canvas = _png.Canvas(60, 60, (255, 255, 255))
        canvas.ring(30, 30, 25, 20, (0, 0, 0))
        self.assertEqual(bytes(canvas.rows[30][30 * 3 : 31 * 3]), b"\xff\xff\xff")
        self.assertEqual(bytes(canvas.rows[30][8 * 3 : 9 * 3]), b"\0\0\0")


class FaviconTests(unittest.TestCase):
    def setUp(self):
        self.palette = _tokens.palette_from_tokens(_tokens.parse_tokens(support.tokens_css()))

    def test_passes_the_svg_check(self):
        text = gen_assets.favicon_svg(self.palette).decode("utf-8")
        self.assertEqual(build.check_svg(text), [])
        self.assertTrue(text.startswith('<svg xmlns="http://www.w3.org/2000/svg"'))
        for forbidden in ("<script", "<style", "<text", "foreignObject", "href", "<!", "<?"):
            self.assertNotIn(forbidden, text)

    def test_colours_come_from_the_tokens(self):
        text = gen_assets.favicon_svg(self.palette).decode("utf-8")
        colors = set(re.findall(r"#[0-9a-f]{6}", text))
        self.assertEqual(
            colors, {support.TOKEN_COLORS["--color-accent"], support.TOKEN_COLORS["--color-on-accent"]}
        )
        other = gen_assets.favicon_svg(self.palette._replace(accent=(1, 2, 3))).decode("utf-8")
        self.assertIn("#010203", other)
        self.assertNotIn(support.TOKEN_COLORS["--color-accent"], other)

    def test_small(self):
        self.assertLess(len(gen_assets.favicon_svg(self.palette)), 1024)


class CommandLineTests(unittest.TestCase):
    def run_script(self, *args: str, cwd: Path):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(cwd),
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True,
            text=True,
        )

    def test_writes_both_files_from_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            css = Path(tmp) / "tokens.css"
            css.write_text(support.tokens_css(), encoding="utf-8")
            result = self.run_script("--css", "tokens.css", "--out", "images", cwd=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(path.name for path in (Path(tmp) / "images").iterdir()),
                ["favicon.svg", "og.png"],
            )
            palette = gen_assets.load_palette(css)
            self.assertEqual(
                (Path(tmp) / "images" / "og.png").read_bytes(), gen_assets.og_png(palette)
            )

    def test_missing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            css = Path(tmp) / "tokens.css"
            css.write_text(":root{--color-bg:#fff}", encoding="utf-8")
            result = self.run_script("--css", str(css), "--out", "images", cwd=Path(tmp))
            self.assertEqual(result.returncode, 1)
            self.assertIn("error: tokens.css: design token '--color-", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((Path(tmp) / "images").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
