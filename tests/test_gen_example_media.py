"""Tests for tools/gen_example_media.py.

The image part needs nothing but the standard library; the tests that need
``ffmpeg`` (video, JPEG conversion) are skipped when it is not installed.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "gen_example_media.py"
EXAMPLES_DATA = REPO_ROOT / "examples" / "data"
EXAMPLES_DATA_PENDING = REPO_ROOT / "examples" / "data-venue-pending"

FFMPEG = shutil.which("ffmpeg")


def _load_generator():
    """Import the generator by path (``tools`` is not a package)."""
    spec = importlib.util.spec_from_file_location("gen_example_media", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def read_png(data):
    """Minimal PNG reader: returns (width, height, color_type, pixel rows)."""
    if data[:8] != gen.PNG_SIGNATURE:
        raise AssertionError("not a PNG file")
    offset = 8
    header = None
    idat = b""
    saw_end = False
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        if crc != zlib.crc32(kind + payload) & 0xFFFFFFFF:
            raise AssertionError(f"bad CRC in chunk {kind!r}")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            saw_end = True
        offset += 12 + length
    if header is None or not saw_end:
        raise AssertionError("PNG without IHDR or IEND")
    width, height, depth, color_type = header[0], header[1], header[2], header[3]
    if depth != 8 or color_type not in (2, 6):
        raise AssertionError("unexpected PNG bit depth or colour type")
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        if raw[start] != 0:
            raise AssertionError("unexpected PNG row filter")
        rows.append(raw[start + 1 : start + 1 + stride])
    if len(raw) != height * (stride + 1):
        raise AssertionError("unexpected PNG data length")
    return width, height, color_type, rows


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class PngWriterTest(unittest.TestCase):
    def test_rgb_roundtrip(self):
        rows = [bytes([255, 0, 0, 0, 255, 0]), bytes([0, 0, 255, 9, 9, 9])]
        width, height, color_type, decoded = read_png(gen.encode_png(2, 2, rows))
        self.assertEqual((width, height, color_type), (2, 2, 2))
        self.assertEqual(decoded, rows)

    def test_rgba_roundtrip(self):
        rows = [bytes([1, 2, 3, 128]), bytes([4, 5, 6, 255])]
        width, height, color_type, decoded = read_png(gen.encode_png(1, 2, rows, alpha=True))
        self.assertEqual((width, height, color_type), (1, 2, 6))
        self.assertEqual(decoded, rows)

    def test_row_length_is_checked(self):
        with self.assertRaises(ValueError):
            gen.encode_png(2, 1, [b"\x00\x00\x00"])

    def test_row_count_is_checked(self):
        with self.assertRaises(ValueError):
            gen.encode_png(1, 2, [b"\x00\x00\x00"])

    def test_write_png_returns_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.png"
            size = gen.write_png(path, 1, 1, [b"\x10\x20\x30"])
            self.assertEqual(size, path.stat().st_size)
            self.assertEqual(read_png(path.read_bytes())[3], [b"\x10\x20\x30"])


class PlanMediaTest(unittest.TestCase):
    def test_example_data_plan(self):
        site = gen.load_site(EXAMPLES_DATA)
        kinds = [item.kind for item in gen.plan_media(site)]
        self.assertEqual(kinds, ["photo"] * 4 + ["directions", "poster", "video"])

    def test_pending_example_data_has_no_media(self):
        site = gen.load_site(EXAMPLES_DATA_PENDING)
        self.assertEqual(gen.plan_media(site), [])

    def test_empty_and_missing_fields_mean_no_file(self):
        site = {"venue": {"ready": False, "directionsImage": ""}, "video": None}
        self.assertEqual(gen.plan_media(site), [])

    def test_unsafe_names_are_rejected(self):
        for name in ("../escape.png", "sub/dir.png", "sub\\dir.png", "..", "a..b.png"):
            with self.subTest(name=name):
                with self.assertRaises(gen.GenerationError):
                    gen.plan_media({"venue": {"photos": [name]}})

    def test_unsupported_extensions_are_rejected(self):
        with self.assertRaises(gen.GenerationError):
            gen.plan_media({"venue": {"photos": ["photo.gif"]}})
        with self.assertRaises(gen.GenerationError):
            gen.plan_media({"video": {"file": "clip.mkv"}})

    def test_duplicate_names_are_generated_once(self):
        site = {"venue": {"photos": ["a.png", "a.png"], "directionsImage": "a.png"}}
        self.assertEqual([item.name for item in gen.plan_media(site)], ["a.png"])

    def test_bad_types_are_rejected(self):
        with self.assertRaises(gen.GenerationError):
            gen.plan_media({"venue": {"photos": "a.png"}})
        with self.assertRaises(gen.GenerationError):
            gen.plan_media({"video": "clip.mp4"})

    def test_load_site_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gen.GenerationError):
                gen.load_site(Path(tmp))


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "media"

    def generate(self, site, ffmpeg=None):
        created = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = gen.generate(
                site, self.out, ffmpeg=ffmpeg, report=lambda path, size: created.append(path)
            )
        self.assertEqual([path for path, _ in result], created)
        return result, stderr.getvalue()

    def test_example_images_without_ffmpeg(self):
        site = gen.load_site(EXAMPLES_DATA)
        created, stderr = self.generate(site)
        names = [path.name for path, _ in created]
        self.assertEqual(
            names,
            ["venue-1.png", "venue-2.png", "venue-3.png", "venue-4.png",
             "directions.png", "proposal-poster.png"],
        )
        self.assertIn("ffmpeg", stderr)
        self.assertIn(site["video"]["file"], stderr)
        self.assertFalse((self.out / site["video"]["file"]).exists())

        sizes = {}
        for path, size in created:
            self.assertEqual(size, path.stat().st_size)
            width, height, color_type, rows = read_png(path.read_bytes())
            self.assertEqual(color_type, 2)
            self.assertEqual(len(rows), height)
            sizes[path.name] = (width, height)
        self.assertEqual(sizes["venue-1.png"], gen.PHOTO_SIZE)
        self.assertEqual(sizes["venue-2.png"], gen.PHOTO_PORTRAIT_SIZE)
        self.assertEqual(sizes["proposal-poster.png"], gen.POSTER_SIZE)
        self.assertEqual(sizes["directions.png"], gen.DIRECTIONS_SIZE)
        self.assertGreater(sizes["venue-1.png"][0], sizes["venue-1.png"][1])
        self.assertLess(sizes["venue-2.png"][0], sizes["venue-2.png"][1])

    def test_pending_example_creates_nothing(self):
        created, stderr = self.generate(gen.load_site(EXAMPLES_DATA_PENDING))
        self.assertEqual(created, [])
        self.assertEqual(stderr, "")
        self.assertFalse(self.out.exists())

    def test_rerun_overwrites_with_identical_content(self):
        site = {"venue": {"photos": ["venue-1.png"]}}
        first, _ = self.generate(site)
        content = first[0][0].read_bytes()
        second, _ = self.generate(site)
        self.assertEqual(second[0][0], first[0][0])
        self.assertEqual(second[0][0].read_bytes(), content)

    def test_non_png_image_without_ffmpeg_is_an_error(self):
        with self.assertRaises(gen.GenerationError):
            self.generate({"venue": {"photos": ["venue-1.jpg"]}})
        self.assertFalse(self.out.exists())

    def test_oversized_file_is_refused(self):
        original = gen.MAX_FILE_BYTES
        gen.MAX_FILE_BYTES = 1024
        self.addCleanup(setattr, gen, "MAX_FILE_BYTES", original)
        with self.assertRaises(gen.GenerationError):
            self.generate({"venue": {"photos": ["venue-1.png"]}})
        self.assertFalse((self.out / "venue-1.png").exists())

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_video_with_ffmpeg(self):
        created, stderr = self.generate({"video": {"file": "clip.mp4"}}, ffmpeg=FFMPEG)
        self.assertEqual(stderr, "")
        self.assertEqual(len(created), 1)
        path, size = created[0]
        self.assertEqual(path.name, "clip.mp4")
        self.assertLess(size, gen.MAX_FILE_BYTES)
        self.assertGreater(size, 10 * 1024)
        data = path.read_bytes()
        self.assertEqual(data[4:8], b"ftyp")
        # -movflags +faststart puts the index in front of the media data
        self.assertLess(data.find(b"moov"), data.find(b"mdat"))

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_jpeg_conversion_with_ffmpeg(self):
        created, _ = self.generate({"venue": {"photos": ["venue-1.jpg"]}}, ffmpeg=FFMPEG)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][0].read_bytes()[:3], b"\xff\xd8\xff")


class CommandLineTest(unittest.TestCase):
    def test_cli_without_ffmpeg_in_path(self):
        """Explicit relative paths, no ffmpeg: warning, no video, exit code 0."""
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "data").mkdir()
            shutil.copyfile(EXAMPLES_DATA / "site.json", workdir / "data" / "site.json")
            (workdir / "empty-path").mkdir()
            env = dict(os.environ, PATH=str(workdir / "empty-path"))
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--data", "data", "--out", "media"],
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ffmpeg not found", result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 6)
            for line in lines:
                self.assertTrue(line.startswith("media/"), line)
            site = load_json(workdir / "data" / "site.json")
            for name in site["venue"]["photos"] + [site["venue"]["directionsImage"]]:
                self.assertTrue((workdir / "media" / name).is_file(), name)
            self.assertFalse((workdir / "media" / site["video"]["file"]).exists())

    def test_cli_reports_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--data", tmp, "--out", tmp],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("error:", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
