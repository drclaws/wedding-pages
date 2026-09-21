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

# the generator is imported by path, which must not leave `__pycache__`
# directories in the working tree
sys.dont_write_bytecode = True

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


def image(name, **fields):
    return {"type": "image", "file": name, **fields}


def video(name, poster, **fields):
    return {"type": "video", "file": name, "poster": poster, **fields}


class PlanMediaTest(unittest.TestCase):
    def test_example_data_plan(self):
        site = gen.load_site(EXAMPLES_DATA)
        plan = gen.plan_media(site)
        self.assertEqual(
            [(item.kind, item.name) for item in plan],
            [
                ("cover", "cover.png"),
                ("photo", "registry-1.png"),
                ("photo", "venue-1.png"),
                ("photo", "venue-2.png"),
                ("photo", "venue-3.png"),
                ("photo", "venue-4.png"),
                ("directions", "directions.png"),
                ("photo", "story-1.png"),
                ("photo", "story-2.png"),
                ("poster", "proposal-poster.png"),
                ("video", "proposal.mp4"),
                ("poster", "walk-poster.png"),
                ("video", "walk.mp4"),
            ],
        )
        sizes = {item.name: item.size for item in plan}
        # every clip has its own size, the poster that of its clip
        self.assertEqual(sizes["proposal.mp4"], (720, 1280))
        self.assertEqual(sizes["proposal-poster.png"], (720, 1280))
        self.assertEqual(sizes["walk.mp4"], (1280, 720))
        self.assertEqual(sizes["walk-poster.png"], (1280, 720))
        self.assertEqual(sizes["directions.png"], gen.DIRECTIONS_SIZE)
        self.assertEqual(sizes["cover.png"], gen.COVER_SIZE)
        self.assertEqual(sizes["registry-1.png"], gen.PHOTO_SIZE)
        self.assertEqual(sizes["venue-1.png"], gen.PHOTO_PORTRAIT_SIZE)
        self.assertEqual(len([item for item in plan if item.kind == "video"]), 2)

    def test_pending_example_data_has_no_media(self):
        site = gen.load_site(EXAMPLES_DATA_PENDING)
        self.assertEqual(gen.plan_media(site), [])

    def test_no_registry_means_no_files(self):
        self.assertEqual(gen.plan_media({}), [])
        self.assertEqual(gen.plan_media({"media": {}}), [])

    def test_sizes_of_the_items_and_the_defaults(self):
        site = {
            "media": {
                "a": image("a.png", width=300, height=200),
                "b": video("b.mp4", "b.png"),
                "c": video("c.mp4", "c.png", width=640, height=360, thumb="c-thumb.png"),
            }
        }
        plan = {item.name: item for item in gen.plan_media(site)}
        self.assertEqual(plan["a.png"].size, (300, 200))
        self.assertEqual(plan["b.mp4"].size, gen.VIDEO_SIZE)
        self.assertEqual(plan["b.png"].size, gen.POSTER_SIZE)
        self.assertEqual(plan["c.mp4"].size, (640, 360))
        self.assertEqual((plan["c-thumb.png"].kind, plan["c-thumb.png"].size), ("thumb", (640, 360)))

    def test_bad_sizes_are_rejected(self):
        for fields in ({"width": 640}, {"width": 0, "height": 2}, {"width": 3, "height": 4},
                       {"width": True, "height": 2}):  # fmt: skip
            with self.subTest(fields=fields), self.assertRaises(gen.GenerationError):
                gen.plan_media({"media": {"v": video("v.mp4", "v.png", **fields)}})
        # an odd size is fine for a picture
        plan = gen.plan_media({"media": {"a": image("a.png", width=3, height=5)}})
        self.assertEqual(plan[0].size, (3, 5))

    def test_unsafe_names_are_rejected(self):
        """The same rules as in the build: a plain, visible, clean file name."""
        names = (
            "../escape.png", "sub/dir.png", "sub\\dir.png", "..", "a..b.png",
            ".hidden.png", " padded.png", "padded.png ", "tab\there.png", "c:drive.png",
            "", 7, None,
        )  # fmt: skip
        for name in names:
            with self.subTest(name=name):
                with self.assertRaises(gen.GenerationError):
                    gen.plan_media({"media": {"a": image(name)}})
                with self.assertRaises(gen.GenerationError):
                    gen.plan_media({"media": {"v": video("v.mp4", name)}})

    def test_unsupported_extensions_are_rejected(self):
        for name in ("photo.gif", "photo.webp", "photo.svg", "clip.mp4"):
            with self.subTest(name=name), self.assertRaises(gen.GenerationError):
                gen.plan_media({"media": {"a": image(name)}})
        for name in ("clip.mkv", "clip.webm", "clip.png"):
            with self.subTest(name=name), self.assertRaises(gen.GenerationError):
                gen.plan_media({"media": {"v": video(name, "v.png")}})

    def test_duplicate_names_are_generated_once(self):
        site = {"media": {"a": image("a.png"), "b": image("a.png"), "c": video("c.mp4", "a.png")}}
        self.assertEqual([item.name for item in gen.plan_media(site)], ["a.png", "c.mp4"])

    def test_bad_types_are_rejected(self):
        for site in (
            {"media": []},
            {"media": {"a": "a.png"}},
            {"media": {"a": {"type": "audio", "file": "a.mp3"}}},
            {"media": {"a": {"file": "a.png"}}},
            {"media": {"a": image("a.png")}, "locations": []},
        ):
            with self.subTest(site=site), self.assertRaises(gen.GenerationError):
                gen.plan_media(site)

    def test_directions_are_the_items_a_place_points_at(self):
        site = {
            "locations": {"manor": {"directions": "map"}, "hall": {"ready": False}},
            "media": {"photo": image("photo.png"), "map": image("map.png")},
        }
        self.assertEqual(
            [(item.kind, item.name) for item in gen.plan_media(site)],
            [("photo", "photo.png"), ("directions", "map.png")],
        )

    def test_the_cover_background_is_a_landscape_picture(self):
        site = {
            "sections": [{"id": "cover", "type": "cover", "background": "back"}],
            "media": {"photo": image("photo.png"), "back": image("back.png"),
                      "sized": image("sized.png")},
        }  # fmt: skip
        plan = {item.name: item for item in gen.plan_media(site)}
        self.assertEqual((plan["back.png"].kind, plan["back.png"].size), ("cover", gen.COVER_SIZE))
        self.assertGreater(gen.COVER_SIZE[0], gen.COVER_SIZE[1])
        # the photos keep their numbers: the background is not one of them
        self.assertEqual((plan["photo.png"].index, plan["sized.png"].index), (0, 1))
        site["media"]["back"].update(width=1000, height=500)
        self.assertEqual(gen.plan_media(site)[1].size, (1000, 500))
        # only the cover has a background
        site["sections"][0]["type"] = "custom"
        self.assertEqual(gen.plan_media(site)[1].kind, "photo")
        with self.assertRaises(gen.GenerationError):
            gen.plan_media({"sections": {}, "media": {}})

    def test_load_site_reports_bad_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "site.json").write_bytes(b'{"coupleNames": "\xff\xfe"}')
            with self.assertRaisesRegex(gen.GenerationError, "not valid UTF-8"):
                gen.load_site(Path(tmp))

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
            ["cover.png", "registry-1.png", "venue-1.png", "venue-2.png", "venue-3.png",
             "venue-4.png", "directions.png", "story-1.png", "story-2.png",
             "proposal-poster.png", "walk-poster.png"],
        )  # fmt: skip
        self.assertIn("ffmpeg", stderr)
        for clip in ("proposal.mp4", "walk.mp4"):
            self.assertIn(clip, stderr)
            self.assertFalse((self.out / clip).exists())

        sizes = {}
        for path, size in created:
            self.assertEqual(size, path.stat().st_size)
            width, height, color_type, rows = read_png(path.read_bytes())
            self.assertEqual(color_type, 2)
            self.assertEqual(len(rows), height)
            sizes[path.name] = (width, height)
        self.assertEqual(sizes["venue-2.png"], gen.PHOTO_SIZE)
        self.assertEqual(sizes["venue-1.png"], gen.PHOTO_PORTRAIT_SIZE)
        self.assertEqual(sizes["directions.png"], gen.DIRECTIONS_SIZE)
        # the background of the cover: a .png, so it is made without ffmpeg too
        self.assertEqual(sizes["cover.png"], gen.COVER_SIZE)
        # the posters have the proportions of their clips: portrait and landscape
        self.assertEqual(sizes["proposal-poster.png"], (720, 1280))
        self.assertEqual(sizes["walk-poster.png"], (1280, 720))
        self.assertEqual(gen.POSTER_SIZE, gen.VIDEO_SIZE)
        self.assertLess(gen.VIDEO_SIZE[0], gen.VIDEO_SIZE[1])

    def test_colours_come_from_the_tokens(self):
        site = {"media": {"a": image("venue-1.png"), "v": video("v.mp4", "poster.png")}}
        default, _ = self.generate(site)
        before = [path.read_bytes() for path, _size in default]
        frame = (1, 2, 3)
        palette = gen.load_tokens(gen.DEFAULT_CSS)._replace(text_muted=frame, line=frame)
        gen.generate(site, self.out, ffmpeg=None, report=lambda *_: None, palette=palette)
        for (path, _size), old in zip(default, before):
            self.assertNotEqual(path.read_bytes(), old)
            rows = read_png(path.read_bytes())[3]
            self.assertEqual(bytes(rows[0][:3]), bytes(frame))  # the frame

    def test_poster_and_thumb_follow_the_video_size(self):
        site = {"media": {"v": video("v.mp4", "poster.png", width=640, height=360, thumb="t.png")}}
        created, _ = self.generate(site)
        self.assertEqual([path.name for path, _ in created], ["poster.png", "t.png"])
        for path, _size in created:
            self.assertEqual(read_png(path.read_bytes())[:2], (640, 360))

    def test_the_poster_has_no_play_mark(self):
        """The page draws the play badge over the poster; a second one would show."""
        created, _ = self.generate(
            {"media": {"v": video("v.mp4", "poster.png", width=360, height=640)}}
        )
        width, height, _type, rows = read_png(created[0][0].read_bytes())
        palette = gen.load_tokens(gen.DEFAULT_CSS)
        light = bytes(palette.bg)
        # the centre third of the frame holds only the dark gradient and grid
        for y in range(height // 3, height * 2 // 3):
            row = rows[y]
            for x in range(width // 3, width * 2 // 3):
                self.assertNotEqual(bytes(row[x * 3 : x * 3 + 3]), light, (x, y))

    def test_missing_token_is_an_error(self):
        css = Path(self.tmp.name) / "app.css"
        css.write_text(":root { --color-bg: #fff; }", encoding="utf-8")
        with self.assertRaisesRegex(gen.GenerationError, "--color-"):
            gen.load_tokens(css)

    def test_pending_example_creates_nothing(self):
        created, stderr = self.generate(gen.load_site(EXAMPLES_DATA_PENDING))
        self.assertEqual(created, [])
        self.assertEqual(stderr, "")
        self.assertFalse(self.out.exists())

    def test_rerun_overwrites_with_identical_content(self):
        site = {"media": {"a": image("venue-1.png")}}
        first, _ = self.generate(site)
        content = first[0][0].read_bytes()
        second, _ = self.generate(site)
        self.assertEqual(second[0][0], first[0][0])
        self.assertEqual(second[0][0].read_bytes(), content)

    def test_non_png_image_without_ffmpeg_is_an_error(self):
        with self.assertRaises(gen.GenerationError):
            self.generate({"media": {"a": image("venue-1.jpg")}})
        self.assertFalse(self.out.exists())

    def test_oversized_file_is_refused(self):
        original = gen.MAX_FILE_BYTES
        gen.MAX_FILE_BYTES = 1024
        self.addCleanup(setattr, gen, "MAX_FILE_BYTES", original)
        with self.assertRaises(gen.GenerationError):
            self.generate({"media": {"a": image("venue-1.png")}})
        self.assertFalse((self.out / "venue-1.png").exists())

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_two_videos_of_their_own_sizes_with_ffmpeg(self):
        site = {
            "media": {
                "a": video("portrait.mp4", "portrait.png"),
                "b": video("landscape.mp4", "landscape.png", width=640, height=360),
            }
        }
        created, stderr = self.generate(site, ffmpeg=FFMPEG)
        self.assertEqual(stderr, "")
        self.assertEqual(
            [path.name for path, _ in created],
            ["portrait.png", "portrait.mp4", "landscape.png", "landscape.mp4"],
        )
        for name, size in (("portrait.mp4", "720x1280"), ("landscape.mp4", "640x360")):
            path = self.out / name
            probe = subprocess.run(
                [FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True
            )
            self.assertIn(size, probe.stderr)
            self.assertIn("Duration: 00:00:05", probe.stderr)
            self.assertLess(path.stat().st_size, gen.MAX_FILE_BYTES)
            self.assertGreater(path.stat().st_size, 10 * 1024)
            data = path.read_bytes()
            self.assertEqual(data[4:8], b"ftyp")
            # -movflags +faststart puts the index in front of the media data
            self.assertLess(data.find(b"moov"), data.find(b"mdat"))

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_jpeg_conversion_with_ffmpeg(self):
        created, _ = self.generate({"media": {"a": image("venue-1.jpg")}}, ffmpeg=FFMPEG)
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
            env = dict(
                os.environ, PATH=str(workdir / "empty-path"), PYTHONDONTWRITEBYTECODE="1"
            )
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
            self.assertEqual(len(lines), 11)
            for line in lines:
                self.assertTrue(line.startswith("media/"), line)
            site = load_json(workdir / "data" / "site.json")
            for item in site["media"].values():
                self.assertTrue((workdir / "media" / item.get("poster", item["file"])).is_file())
                if item["type"] == "video":
                    self.assertFalse((workdir / "media" / item["file"]).exists())

    def test_cli_reports_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--data", tmp, "--out", tmp],
                capture_output=True,
                text=True,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("error:", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
