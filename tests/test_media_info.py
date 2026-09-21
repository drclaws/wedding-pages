"""Tests for the facts of media files (`tools/_media.py`).

No media file is stored in the repository: every picture and video below is
assembled from bytes in memory and, where a path is needed, written to a
temporary directory.
"""

from __future__ import annotations

import io
import struct
import unittest
from pathlib import Path

from tests import test_mp4 as mp4
from tests.support import TempDirTestCase
from tools import _media
from tools._png import encode_png


def png(width: int, height: int) -> bytes:
    return encode_png(width, height, [bytes(width * 3)] * height)


def jpeg(width: int, height: int, *, extra: bytes = b"", marker: int = 0xC0) -> bytes:
    """A JPEG header: SOI, APP0, optional segments, a frame header, EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
    frame = b"\xff" + bytes([marker]) + struct.pack(">HBHHB", 11, 8, height, width, 1) + bytes(3)
    return b"\xff\xd8" + app0 + extra + frame + b"\xff\xd9"


def app_segment(length: int) -> bytes:
    return b"\xff\xe1" + struct.pack(">H", length + 2) + bytes(length)


def riff(chunk: bytes, payload: bytes) -> bytes:
    body = b"WEBP" + chunk + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossy(width: int, height: int) -> bytes:
    payload = b"\x10\x02\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height) + bytes(8)
    return riff(b"VP8 ", payload)


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    return riff(b"VP8L", b"\x2f" + struct.pack("<I", bits) + bytes(8))


def webp_extended(width: int, height: int) -> bytes:
    payload = bytes(4) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return riff(b"VP8X", payload)


class ImageSizeTests(unittest.TestCase):
    def size(self, data: bytes):
        return _media.image_size(io.BytesIO(data))

    def test_png(self):
        self.assertEqual(self.size(png(7, 3)), (7, 3))

    def test_jpeg(self):
        self.assertEqual(self.size(jpeg(640, 480)), (640, 480))
        self.assertEqual(self.size(jpeg(64, 48, marker=0xC2)), (64, 48))

    def test_jpeg_with_large_segments_before_the_frame(self):
        extra = app_segment(60000) + app_segment(60000) + b"\xff\xff"
        self.assertEqual(self.size(jpeg(1920, 1080, extra=extra)), (1920, 1080))

    def test_jpeg_without_a_frame_header(self):
        data = b"\xff\xd8" + b"\xff\xda" + struct.pack(">H", 8) + bytes(6)
        self.assertIsNone(self.size(data))

    def test_webp_lossy_lossless_and_extended(self):
        self.assertEqual(self.size(webp_lossy(300, 200)), (300, 200))
        self.assertEqual(self.size(webp_lossless(301, 17)), (301, 17))
        self.assertEqual(self.size(webp_extended(4000, 3000)), (4000, 3000))

    def test_broken_headers(self):
        cases = {
            "empty": b"",
            "text": b"not a picture at all, just text",
            "png cut": png(4, 4)[:20],
            "png zero": png(4, 4)[:16] + bytes(8),
            "jpeg cut": jpeg(10, 10)[:25],
            "jpeg bad length": b"\xff\xd8\xff\xe0\x00\x01",
            "webp cut": webp_lossy(10, 10)[:24],
            "webp bad start code": webp_lossy(10, 10).replace(b"\x9d\x01\x2a", b"\x00\x00\x00"),
            "webp unknown chunk": riff(b"ABCD", bytes(20)),
            "webp lossless bad signature": webp_lossless(5, 5).replace(b"\x2f", b"\x00", 1),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.size(data))

    def test_the_file_is_not_read_whole(self):
        stream = mp4.CountingStream(jpeg(10, 20, extra=app_segment(60000)) + bytes(5_000_000))
        self.assertEqual(_media.image_size(stream), (10, 20))
        self.assertLess(stream.bytes_read, 1000)


class InspectTests(TempDirTestCase):
    def write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_pictures(self):
        for name, data, size in (
            ("a.png", png(3, 2), (3, 2)),
            ("b.JPG", jpeg(30, 20), (30, 20)),
            ("c.webp", webp_extended(8, 9), (8, 9)),
        ):
            with self.subTest(name):
                info = _media.inspect(self.write(name, data))
                self.assertEqual((info.width, info.height), size)
                self.assertEqual(info.problems, ())
                self.assertEqual(info.name, name)

    def test_other_formats_have_no_size(self):
        info = _media.inspect(self.write("a.gif", b"GIF89a" + bytes(20)))
        self.assertEqual(info.problems, (_media.SIZE_UNKNOWN,))
        self.assertIsNone(info.size)

    def test_contents_that_do_not_match_the_name(self):
        info = _media.inspect(self.write("a.png", jpeg(3, 3)))
        self.assertEqual(info.problems, (_media.NOT_THIS_TYPE,))

    def test_broken_picture(self):
        info = _media.inspect(self.write("a.png", png(3, 3)[:18]))
        self.assertEqual(info.problems, (_media.NOT_THIS_TYPE, _media.SIZE_UNKNOWN))

    def test_unreadable(self):
        self.assertEqual(_media.inspect(self.tmp / "missing.png").problems, (_media.UNREADABLE,))
        self.assertEqual(_media.inspect(self.tmp / "missing.mp4").problems, (_media.UNREADABLE,))

    def test_name_from_the_data(self):
        info = _media.inspect(self.write("x.png", png(1, 1)), name="photo.png")
        self.assertEqual(info.name, "photo.png")

    def test_good_video(self):
        info = _media.inspect(self.write("v.mp4", mp4.sample_file(mp4.video_trak(width=1280, height=720))))
        self.assertEqual(info.problems, ())
        self.assertEqual((info.width, info.height), (1280, 720))
        self.assertEqual(info.duration_seconds, 2.0)
        self.assertEqual(info.video_codec, "avc1")
        self.assertTrue(info.faststart)

    def test_video_problems(self):
        data = mp4.sample_file(
            mp4.video_trak(b"hvc1", mp4.colr(16)), faststart=False, header=mp4.mvhd(duration=0)
        )
        info = _media.inspect(self.write("v.mp4", data))
        self.assertEqual(
            info.problems,
            (_media.DURATION_UNKNOWN, _media.NOT_FASTSTART, _media.UNEXPECTED_CODEC, _media.HDR_VIDEO),
        )

    def test_video_without_a_codec(self):
        cases = {
            # a sample table without entries
            "no entry": mp4.ftyp()
            + mp4.moov(mp4.trak(b"vide", mp4.stsd(), mp4.tkhd(10, 10)))
            + mp4.mdat(),
            # a box of size 0 at the top swallows the rest of the file
            "open box": mp4.ftyp() + mp4.open_box(b"free", mp4.moov(mp4.video_trak()), mp4.mdat()),
        }
        for name, data in cases.items():
            with self.subTest(name):
                info = _media.inspect(self.write("v.mp4", data))
                self.assertIn(_media.CODEC_UNKNOWN, info.problems)

    def test_fragmented_video_without_duration(self):
        data = mp4.sample_file(header=mp4.mvhd(duration=0))
        info = _media.inspect(self.write("v.mp4", data))
        self.assertIn(_media.DURATION_UNKNOWN, info.problems)

    def test_not_a_video(self):
        info = _media.inspect(self.write("v.mp4", png(2, 2)))
        self.assertEqual(info.problems, (_media.NOT_THIS_TYPE,))


class HashedNameTests(TempDirTestCase):
    def test_name_from_the_contents(self):
        name = _media.hashed_name(b"abc", "Photo.PNG")
        self.assertEqual(name, "ba7816bf8f01cfea.png")
        self.assertEqual(_media.hashed_name(b"abc", "x.png"), name)
        self.assertNotEqual(_media.hashed_name(b"abd", "x.png"), name)

    def test_path_and_bytes_agree(self):
        path = self.tmp / "venue-1.webp"
        path.write_bytes(webp_lossy(2, 2))
        self.assertEqual(
            _media.hashed_name(path), _media.hashed_name(webp_lossy(2, 2), "venue-1.webp")
        )
        self.assertTrue(_media.hashed_name(path).endswith(".webp"))
        self.assertNotIn("venue", _media.hashed_name(path))

    def test_bytes_need_a_name(self):
        with self.assertRaises(ValueError):
            _media.hashed_name(b"abc")


if __name__ == "__main__":
    unittest.main()
