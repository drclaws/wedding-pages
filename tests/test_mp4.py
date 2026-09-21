"""Tests for `tools/_mp4.py`.

No media file is stored in the repository: the MP4 files below are assembled
in memory from boxes, and the test with real clips makes them with ffmpeg in
a temporary directory (it is skipped when ffmpeg is not installed).
"""

from __future__ import annotations

import io
import math
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import _mp4

FFMPEG = shutil.which("ffmpeg")

#: Every message `Mp4Error` may carry for broken data: fixed texts only, so
#: that nothing read from the file can reach the build log.
MESSAGES = {
    "not an MP4 file",
    "the file is truncated",
    "invalid box size",
    "a box extends past its parent",
    "moov is too large",
    "too many boxes",
}

IDENTITY = (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
QUARTER_TURN = (0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000)

# --- building boxes -----------------------------------------------------------


def u16(value: int) -> bytes:
    return struct.pack(">H", value)


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def u64(value: int) -> bytes:
    return struct.pack(">Q", value)


def box(kind: bytes, *parts: bytes) -> bytes:
    """A box with a 32-bit size."""
    payload = b"".join(parts)
    return u32(8 + len(payload)) + kind + payload


def large_box(kind: bytes, *parts: bytes) -> bytes:
    """A box with ``size == 1`` and a 64-bit ``largesize``."""
    payload = b"".join(parts)
    return u32(1) + kind + u64(16 + len(payload)) + payload


def open_box(kind: bytes, *parts: bytes) -> bytes:
    """A box with ``size == 0``: it extends to the end of the file."""
    return u32(0) + kind + b"".join(parts)


def full_box(kind: bytes, version: int, *parts: bytes, flags: int = 0) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big"), *parts)


def ftyp(brand: bytes = b"isom") -> bytes:
    return box(b"ftyp", brand, u32(0x200), b"isomiso2avc1mp41")


def mvhd(timescale: int = 1000, duration: int = 2000, version: int = 0) -> bytes:
    if version == 1:
        times = u64(0) + u64(0) + u32(timescale) + u64(duration)
    else:
        times = u32(0) + u32(0) + u32(timescale) + u32(duration)
    # rate, volume, reserved, matrix, pre_defined, next_track_ID
    rest = u32(0x10000) + u16(0x100) + bytes(10) + struct.pack(">9i", *IDENTITY)
    return full_box(b"mvhd", version, times, rest, bytes(24), u32(3))


def tkhd(width: int, height: int, version: int = 0, matrix=IDENTITY) -> bytes:
    if version == 1:  # creation, modification, track_ID, reserved, duration
        head = u64(0) + u64(0) + u32(1) + u32(0) + u64(2000)
    else:
        head = u32(0) + u32(0) + u32(1) + u32(0) + u32(2000)
    # reserved, layer, alternate_group, volume, reserved, matrix, width, height
    tail = bytes(16) + struct.pack(">9i", *matrix) + u32(width << 16) + u32(height << 16)
    return full_box(b"tkhd", version, head, tail, flags=3)


def mdhd() -> bytes:
    return full_box(b"mdhd", 0, u32(0), u32(0), u32(1000), u32(2000), u16(0x55C4), u16(0))


def hdlr(handler: bytes) -> bytes:
    return full_box(b"hdlr", 0, u32(0), handler, bytes(12), b"handler\0")


def colr(transfer: int, kind: bytes = b"nclx", primaries: int = 9, matrix: int = 9) -> bytes:
    values = struct.pack(">HHH", primaries, transfer, matrix)
    return box(b"colr", kind, values + (b"\0" if kind == b"nclx" else b""))


def visual_entry(codec: bytes, *children: bytes, width: int = 320, height: int = 240) -> bytes:
    """A ``VisualSampleEntry``: 78 bytes of fields, then its child boxes."""
    fields = (
        bytes(6) + u16(1) + bytes(16) + u16(width) + u16(height)
        + u32(0x480000) + u32(0x480000) + u32(0) + u16(1) + bytes(32)
        + u16(0x18) + struct.pack(">h", -1)
    )  # fmt: skip
    assert len(fields) == _mp4.VISUAL_SAMPLE_ENTRY_BYTES
    return box(codec, fields, box(b"avcC", b"\x01\x64\x00\x1f\xff\xe1"), *children)


def audio_entry(codec: bytes = b"mp4a") -> bytes:
    fields = bytes(6) + u16(1) + bytes(8) + u16(2) + u16(16) + bytes(4) + u32(44100 << 16)
    return box(codec, fields, box(b"esds", bytes(8)))


def stsd(*entries: bytes) -> bytes:
    return full_box(b"stsd", 0, u32(len(entries)), *entries)


def trak(handler: bytes, sample_table: bytes, header: bytes) -> bytes:
    """``trak`` / ``mdia`` / ``minf`` / ``stbl``; ``sample_table`` opens the ``stbl``."""
    stbl = box(b"stbl", sample_table, box(b"stts", bytes(8)), box(b"stsz", bytes(12)))
    minf = box(b"minf", box(b"vmhd", bytes(12)), stbl)
    return box(b"trak", header, box(b"mdia", mdhd(), hdlr(handler), minf))


def video_trak(
    codec: bytes = b"avc1",
    *children: bytes,
    width: int = 320,
    height: int = 240,
    tkhd_version: int = 0,
    matrix=IDENTITY,
) -> bytes:
    entry = visual_entry(codec, *children, width=width, height=height)
    return trak(b"vide", stsd(entry), tkhd(width, height, tkhd_version, matrix))


def audio_trak() -> bytes:
    return trak(b"soun", stsd(audio_entry()), tkhd(0, 0))


def moov(*traks: bytes, header: bytes | None = None) -> bytes:
    return box(b"moov", mvhd() if header is None else header, *traks)


def mdat(length: int = 256) -> bytes:
    return box(b"mdat", bytes(length))


def sample_file(*traks: bytes, faststart: bool = True, header: bytes | None = None) -> bytes:
    index = moov(*(traks or (video_trak(),)), header=header)
    body = [index, mdat()] if faststart else [mdat(), index]
    return ftyp() + b"".join(body)


class CountingStream(io.BytesIO):
    """A stream that remembers how many bytes were read from it."""

    bytes_read = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


def probe_bytes(data: bytes) -> _mp4.Mp4Info:
    return _mp4.probe(io.BytesIO(data))


class Mp4TestCase(unittest.TestCase):
    def assertBroken(self, data: bytes, message: str | None = None) -> None:
        with self.assertRaises(_mp4.Mp4Error) as caught:
            probe_bytes(data)
        text = str(caught.exception)
        self.assertIn(text, MESSAGES)
        if message is not None:
            self.assertEqual(text, message)


# --- the facts --------------------------------------------------------------------


class DurationTest(Mp4TestCase):
    def test_version_0(self):
        info = probe_bytes(sample_file(header=mvhd(timescale=1000, duration=20_480)))
        self.assertEqual(info.duration_seconds, 20.48)

    def test_version_1(self):
        # a duration that does not fit into 32 bits
        info = probe_bytes(sample_file(header=mvhd(timescale=90_000, duration=2**33, version=1)))
        self.assertAlmostEqual(info.duration_seconds, 2**33 / 90_000)

    def test_zero_time_scale_is_unknown(self):
        info = probe_bytes(sample_file(header=mvhd(timescale=0)))
        self.assertIsNone(info.duration_seconds)
        self.assertEqual(info.video_codec, "avc1")

    def test_zero_and_unknown_durations_are_unknown(self):
        for header in (
            mvhd(duration=0),
            mvhd(duration=0xFFFFFFFF),
            mvhd(duration=2**64 - 1, version=1),
        ):
            with self.subTest(header=header[:24].hex()):
                self.assertIsNone(probe_bytes(sample_file(header=header)).duration_seconds)

    def test_moov_without_mvhd(self):
        info = probe_bytes(sample_file(header=box(b"free")))
        self.assertIsNone(info.duration_seconds)
        self.assertEqual((info.video_codec, info.faststart), ("avc1", True))

    def test_unknown_version_and_short_box(self):
        for header in (full_box(b"mvhd", 2, bytes(96)), full_box(b"mvhd", 0, bytes(12)), box(b"mvhd")):
            with self.subTest(header=header.hex()):
                self.assertIsNone(probe_bytes(sample_file(header=header)).duration_seconds)


class FaststartTest(Mp4TestCase):
    def test_moov_before_mdat(self):
        self.assertIs(probe_bytes(sample_file(faststart=True)).faststart, True)

    def test_mdat_before_moov(self):
        info = probe_bytes(sample_file(faststart=False))
        self.assertIs(info.faststart, False)
        self.assertEqual((info.duration_seconds, info.video_codec), (2.0, "avc1"))

    def test_other_boxes_do_not_matter(self):
        data = ftyp() + box(b"free", bytes(40)) + box(b"skip") + moov(video_trak()) + mdat()
        self.assertIs(probe_bytes(data).faststart, True)
        data = ftyp() + box(b"wide") + mdat() + box(b"free", bytes(3)) + moov(video_trak())
        self.assertIs(probe_bytes(data).faststart, False)

    def test_largesize(self):
        data = ftyp() + large_box(b"mdat", bytes(100)) + moov(video_trak())
        self.assertEqual(probe_bytes(data), probe_bytes(sample_file(faststart=False)))
        index = large_box(b"moov", mvhd(), video_trak())
        info = probe_bytes(ftyp() + index + mdat())
        self.assertEqual((info.faststart, info.duration_seconds, info.video_codec), (True, 2.0, "avc1"))

    def test_last_box_extends_to_the_end_of_the_file(self):
        info = probe_bytes(ftyp() + moov(video_trak()) + open_box(b"mdat", bytes(500)))
        self.assertEqual((info.faststart, info.duration_seconds), (True, 2.0))
        index = open_box(b"moov", mvhd(), video_trak())
        info = probe_bytes(ftyp() + mdat() + index)
        self.assertEqual((info.faststart, info.duration_seconds, info.video_codec), (False, 2.0, "avc1"))

    def test_missing_boxes(self):
        self.assertIsNone(probe_bytes(ftyp() + moov(video_trak())).faststart)
        self.assertEqual(probe_bytes(ftyp() + mdat()), _mp4.Mp4Info())
        self.assertEqual(probe_bytes(ftyp()), _mp4.Mp4Info())

    def test_the_media_data_is_not_read(self):
        index = moov(video_trak())
        stream = CountingStream(ftyp() + mdat(1 << 20) + index)
        info = _mp4.probe(stream)
        self.assertIs(info.faststart, False)
        self.assertLess(stream.bytes_read, len(index) + 200)
        self.assertFalse(stream.closed)

    def test_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(sample_file())
            self.assertEqual(_mp4.probe(path), _mp4.probe(str(path)))
            self.assertEqual(_mp4.probe(path).video_codec, "avc1")


class CodecTest(Mp4TestCase):
    def test_codecs(self):
        for codec in ("avc1", "avc3", "hvc1", "hev1", "av01", "vp09"):
            with self.subTest(codec=codec):
                info = probe_bytes(sample_file(video_trak(codec.encode())))
                self.assertEqual(info.video_codec, codec)

    def test_the_video_track_is_found_after_the_audio_track(self):
        info = probe_bytes(sample_file(audio_trak(), video_trak(b"hvc1", colr(16))))
        self.assertEqual((info.video_codec, info.hdr), ("hvc1", True))
        self.assertEqual((info.width, info.height), (320, 240))

    def test_the_first_video_track_wins(self):
        info = probe_bytes(sample_file(video_trak(b"avc1"), video_trak(b"hvc1", colr(18))))
        self.assertEqual((info.video_codec, info.hdr), ("avc1", None))

    def test_the_first_sample_entry_wins(self):
        entries = stsd(visual_entry(b"hvc1"), visual_entry(b"avc1"))
        info = probe_bytes(sample_file(trak(b"vide", entries, tkhd(320, 240))))
        self.assertEqual(info.video_codec, "hvc1")

    def test_audio_only(self):
        info = probe_bytes(sample_file(audio_trak()))
        self.assertEqual(info, _mp4.Mp4Info(duration_seconds=2.0, faststart=True))

    def test_incomplete_tracks_are_skipped(self):
        without_mdia = box(b"trak", tkhd(640, 360))
        without_hdlr = box(b"trak", box(b"mdia", mdhd()))
        info = probe_bytes(sample_file(without_mdia, without_hdlr, video_trak(b"av01")))
        self.assertEqual(info.video_codec, "av01")

    def test_video_track_without_sample_entries(self):
        for table in (stsd(), full_box(b"stsd", 0, u32(1)), full_box(b"stsd", 0), box(b"free")):
            with self.subTest(table=table.hex()):
                info = probe_bytes(sample_file(trak(b"vide", table, tkhd(320, 240))))
                self.assertEqual((info.video_codec, info.hdr), (None, None))
                self.assertEqual((info.width, info.height), (320, 240))

    def test_unprintable_code_is_unknown(self):
        info = probe_bytes(sample_file(video_trak(b"av\x00\x01")))
        self.assertIsNone(info.video_codec)


class HdrTest(Mp4TestCase):
    def hdr(self, *children: bytes):
        return probe_bytes(sample_file(video_trak(b"avc1", *children))).hdr

    def test_transfer_characteristics(self):
        self.assertIs(self.hdr(colr(16)), True)  # PQ
        self.assertIs(self.hdr(colr(18)), True)  # HLG
        self.assertIs(self.hdr(colr(1, primaries=1, matrix=1)), False)  # BT.709
        self.assertIs(self.hdr(colr(2, primaries=2, matrix=2)), False)  # unspecified
        self.assertIsNone(self.hdr())

    def test_quicktime_colour_box(self):
        self.assertIs(self.hdr(colr(18, kind=b"nclc")), True)
        self.assertIs(self.hdr(colr(1, kind=b"nclc")), False)

    def test_icc_profile_gives_no_answer(self):
        profile = box(b"colr", b"prof", b"not really an ICC profile")
        self.assertIsNone(self.hdr(profile))
        self.assertIs(self.hdr(profile, colr(16)), True)

    def test_short_colour_box_is_ignored(self):
        self.assertIsNone(self.hdr(box(b"colr", b"nclx\x00\x09")))

    def test_colour_outside_the_sample_entry_is_ignored(self):
        # next to `stsd` in the sample table of the video track
        table = stsd(visual_entry(b"avc1")) + colr(16)
        info = probe_bytes(sample_file(trak(b"vide", table, tkhd(320, 240))))
        self.assertEqual((info.video_codec, info.hdr), ("avc1", None))
        # in the sample entry of an audio track
        sound = trak(b"soun", stsd(box(b"mp4a", bytes(28), colr(16))), tkhd(0, 0))
        info = probe_bytes(sample_file(sound, video_trak()))
        self.assertEqual((info.video_codec, info.hdr), ("avc1", None))


class NestedBoxTest(Mp4TestCase):
    def test_lists_ended_by_a_zero(self):
        """QuickTime ends some lists of boxes with a 32-bit zero."""
        index = box(b"moov", mvhd(), video_trak(b"avc1", u32(0)), u32(0))
        info = probe_bytes(ftyp() + index + mdat())
        self.assertEqual((info.duration_seconds, info.video_codec, info.hdr), (2.0, "avc1", None))

    def test_size_zero_extends_to_the_end_of_the_parent(self):
        track = video_trak(b"hvc1", colr(18))
        index = box(b"moov", mvhd(), open_box(b"trak", track[8:]))
        info = probe_bytes(ftyp() + index + mdat())
        self.assertEqual((info.video_codec, info.hdr), ("hvc1", True))


class SizeTest(Mp4TestCase):
    def test_track_header_versions(self):
        for version in (0, 1):
            with self.subTest(version=version):
                info = probe_bytes(sample_file(video_trak(width=1280, height=720, tkhd_version=version)))
                self.assertEqual((info.width, info.height), (1280, 720))

    def test_quarter_turn(self):
        info = probe_bytes(sample_file(video_trak(width=1280, height=720, matrix=QUARTER_TURN)))
        self.assertEqual((info.width, info.height), (720, 1280))

    def test_unknown_size(self):
        info = probe_bytes(sample_file(video_trak(width=0, height=0)))
        self.assertEqual((info.width, info.height, info.video_codec), (None, None, "avc1"))
        header = full_box(b"tkhd", 0, bytes(20))
        info = probe_bytes(sample_file(trak(b"vide", stsd(visual_entry(b"avc1")), header)))
        self.assertEqual((info.width, info.height, info.video_codec), (None, None, "avc1"))


class FormatDurationTest(unittest.TestCase):
    def test_values(self):
        cases = {
            0.4: "0:01",
            1: "0:01",
            2.5: "0:03",
            5: "0:05",
            20: "0:20",
            59.4: "0:59",
            59.6: "1:00",
            600: "10:00",
            3599.4: "59:59",
            3599.5: "1:00:00",
            3725: "1:02:05",
            36_000: "10:00:00",
        }
        for seconds, text in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(_mp4.format_duration(seconds), text)

    def test_no_duration(self):
        for seconds in (None, 0, 0.0, -1, math.nan, math.inf):
            with self.subTest(seconds=seconds):
                self.assertEqual(_mp4.format_duration(seconds), "")


# --- broken files --------------------------------------------------------------------


class BrokenFileTest(Mp4TestCase):
    def test_not_an_mp4_file(self):
        for data in (
            b"",
            b"\0\0\0\x18ft",
            sample_file()[:7],
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
            b"<!doctype html>\n<p>not a video</p>\n",
            moov(video_trak()) + mdat(),
        ):
            with self.subTest(data=data[:16]):
                self.assertBroken(data, "not an MP4 file")

    def test_truncated_file(self):
        data = sample_file(faststart=False)
        for length in (len(data) - 1, len(data) - 100, len(ftyp()) + 12, len(ftyp()) + 4):
            with self.subTest(length=length):
                self.assertBroken(data[:length], "the file is truncated")
        faststart = sample_file()
        self.assertBroken(faststart[:-1], "the file is truncated")

    def test_box_larger_than_the_file(self):
        data = ftyp() + u32(10_000) + b"mdat" + bytes(100)
        self.assertBroken(data, "the file is truncated")
        data = ftyp() + u32(1) + b"mdat" + u64(2**63) + bytes(100)
        self.assertBroken(data, "the file is truncated")

    def test_box_smaller_than_its_header(self):
        for size in (2, 3, 7):
            with self.subTest(size=size):
                self.assertBroken(ftyp() + u32(size) + b"free" + mdat(), "invalid box size")
                index = box(b"moov", mvhd(), u32(size) + b"trak" + bytes(8))
                self.assertBroken(ftyp() + index + mdat(), "invalid box size")
        for largesize in (0, 8, 15):
            with self.subTest(largesize=largesize):
                data = ftyp() + u32(1) + b"mdat" + u64(largesize) + bytes(32)
                self.assertBroken(data, "invalid box size")

    def test_ftyp_with_a_bad_size(self):
        self.assertBroken(u32(4) + b"ftyp" + bytes(16), "invalid box size")
        self.assertBroken(u32(1) + b"ftyp" + b"\0\0", "the file is truncated")

    def test_box_past_its_parent(self):
        track = video_trak()
        # the track claims 10 more bytes than the moov box holds
        grown = u32(len(track) + 10) + track[4:]
        self.assertBroken(ftyp() + moov(grown) + mdat(), "a box extends past its parent")
        # the sample entry claims more than its stsd box
        entry = visual_entry(b"avc1")
        table = full_box(b"stsd", 0, u32(1), u32(len(entry) + 1) + entry[4:])
        self.assertBroken(
            sample_file(trak(b"vide", table, tkhd(1, 1))), "a box extends past its parent"
        )
        # a 64-bit size inside moov that runs past the parent
        index = box(b"moov", mvhd(), u32(1) + b"trak" + u64(2**64 - 1))
        self.assertBroken(ftyp() + index + mdat(), "a box extends past its parent")

    def test_broken_colour_box(self):
        data = sample_file(video_trak(b"avc1", u32(500) + b"colr" + b"nclx"))
        self.assertBroken(data, "a box extends past its parent")

    def test_huge_declared_moov(self):
        self.assertBroken(ftyp() + u32(0xFFFFFFF0) + b"moov" + mvhd(), "the file is truncated")
        self.assertBroken(ftyp() + u32(1) + b"moov" + u64(2**40) + mvhd(), "the file is truncated")

    def test_moov_over_the_limit_is_not_read(self):
        index = moov(video_trak(), box(b"free", bytes(4096)))
        stream = CountingStream(ftyp() + index + mdat())
        with mock.patch.object(_mp4, "MAX_MOOV_BYTES", 4096):
            with self.assertRaisesRegex(_mp4.Mp4Error, "^moov is too large$"):
                _mp4.probe(stream)
        self.assertLess(stream.bytes_read, 100)
        # the limit is on the payload: exactly the limit is still read
        with mock.patch.object(_mp4, "MAX_MOOV_BYTES", len(index) - 8):
            self.assertEqual(probe_bytes(ftyp() + index + mdat()).video_codec, "avc1")

    def test_deep_nesting_is_not_followed(self):
        depth = 100_000
        inner = video_trak()
        # `trak` boxes nested 100 000 deep, each holding only the next one
        headers = b"".join(
            u32(8 * (depth - level) + len(inner)) + b"trak" for level in range(depth)
        )
        info = probe_bytes(ftyp() + box(b"moov", mvhd(), headers + inner) + mdat())
        self.assertEqual((info.duration_seconds, info.video_codec), (2.0, None))

    def test_too_many_boxes(self):
        crowd = box(b"free") * (_mp4.MAX_BOXES + 1)
        self.assertBroken(ftyp() + box(b"moov", crowd, mvhd()) + mdat(), "too many boxes")
        with mock.patch.object(_mp4, "MAX_BOXES", 50):
            self.assertBroken(ftyp() + box(b"free") * 60 + sample_file()[len(ftyp()) :], "too many boxes")
            self.assertEqual(probe_bytes(sample_file()).video_codec, "avc1")

    def test_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(_mp4.Mp4Error, "^cannot read the file: "):
                _mp4.probe(Path(tmp) / "missing.mp4")
            with self.assertRaisesRegex(_mp4.Mp4Error, "^cannot read the file: "):
                _mp4.probe(tmp)


class CorruptedCopiesTest(unittest.TestCase):
    """Randomly damaged copies of valid files: `Mp4Error` or a result, nothing else."""

    COPIES = 1000
    KINDS = (
        b"ftyp", b"moov", b"mvhd", b"trak", b"tkhd", b"mdia", b"mdhd", b"hdlr", b"minf",
        b"vmhd", b"stbl", b"stsd", b"avc1", b"hvc1", b"mp4a", b"avcC", b"esds", b"colr",
        b"stts", b"stsz", b"mdat", b"free",
    )  # fmt: skip

    @staticmethod
    def sources() -> list[bytes]:
        return [
            sample_file(audio_trak(), video_trak(b"avc1", colr(16))),
            ftyp() + large_box(b"mdat", bytes(64)) + moov(video_trak(b"hvc1", colr(1), tkhd_version=1)),
            ftyp() + box(b"free", bytes(8)) + moov(video_trak(), header=mvhd(version=1))
            + open_box(b"mdat", bytes(64)),
        ]  # fmt: skip

    def headers(self, data: bytes) -> list[int]:
        """Start offsets of the box headers (and sample entries) in ``data``."""
        found = []
        for kind in self.KINDS:
            at = data.find(kind)
            while at >= 0:
                if at >= 4:
                    found.append(at - 4)
                at = data.find(kind, at + 1)
        return sorted(found)

    def damage(self, rng: random.Random, data: bytes) -> bytes:
        copy = bytearray(data)
        for _ in range(rng.randint(1, 3)):
            action = rng.choice(("truncate", "size", "largesize", "type", "byte"))
            headers = self.headers(bytes(copy)) or [0]
            at = rng.choice(headers)
            if action == "truncate":
                del copy[rng.randrange(len(copy) + 1) :]
            elif action == "size":
                old = int.from_bytes(copy[at : at + 4], "big")
                size = rng.choice((0, 1, 2, 3, 7, 8, 9, 0xFFFFFFFF, rng.getrandbits(32),
                                   max(0, old + rng.randint(-16, 16))))  # fmt: skip
                copy[at : at + 4] = u32(size % 2**32)
            elif action == "largesize":
                largesize = rng.choice((0, 8, 15, 16, 17, 2**63, 2**64 - 1, rng.getrandbits(64),
                                        rng.randint(16, 4096)))  # fmt: skip
                copy[at : at + 4] = u32(1)
                copy[at + 8 : at + 16] = u64(largesize)
            elif action == "type":
                copy[at + 4 : at + 8] = rng.choice((*self.KINDS, bytes(rng.getrandbits(8) for _ in range(4))))
            elif copy:
                copy[rng.randrange(len(copy))] = rng.getrandbits(8)
        return bytes(copy)

    def test_damaged_copies(self):
        rng = random.Random(20300601)
        sources = self.sources()
        failures = []
        outcomes = {"error": 0, "result": 0}
        for number in range(self.COPIES):
            data = self.damage(rng, sources[number % len(sources)])
            try:
                info = probe_bytes(data)
            except _mp4.Mp4Error as exc:
                outcomes["error"] += 1
                if str(exc) not in MESSAGES:
                    failures.append((number, f"unexpected message {exc}"))
                continue
            except Exception as exc:  # noqa: BLE001 - this is what the test looks for
                failures.append((number, repr(exc)))
                continue
            outcomes["result"] += 1
            problem = self.check(info)
            if problem:
                failures.append((number, problem))
        self.assertEqual(failures, [])
        # the damage is varied enough to reach both outcomes
        self.assertGreater(outcomes["error"], self.COPIES // 10)
        self.assertGreater(outcomes["result"], self.COPIES // 10)

    @staticmethod
    def check(info: _mp4.Mp4Info) -> str:
        if info.duration_seconds is not None and not (
            math.isfinite(info.duration_seconds) and info.duration_seconds > 0
        ):
            return f"duration {info.duration_seconds!r}"
        codec = info.video_codec
        if codec is not None and not (len(codec) == 4 and codec.isprintable() and codec.isascii()):
            return f"codec {codec!r}"
        for value in (info.width, info.height):
            if value is not None and value <= 0:
                return f"size {info.width}x{info.height}"
        return ""


# --- real clips -------------------------------------------------------------------


@unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
class FfmpegClipTest(unittest.TestCase):
    SOURCE = ("-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10")
    ENCODE = ("-c:v", "libx264", "-pix_fmt", "yuv420p")
    #: The colour tags of the stream itself (ffmpeg writes them to `colr`).
    PQ = ("-vf", "setparams=color_trc=smpte2084:color_primaries=bt2020:colorspace=bt2020nc")

    def make(self, name: str, *options: str) -> Path:
        path = Path(self.tmp.name) / name
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             *self.SOURCE, *options, *self.ENCODE, str(path)],
            check=True, capture_output=True,
        )  # fmt: skip
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_faststart_clip(self):
        info = _mp4.probe(self.make("faststart.mp4", "-movflags", "+faststart"))
        self.assertAlmostEqual(info.duration_seconds, 2.0, delta=0.05)
        self.assertEqual(_mp4.format_duration(info.duration_seconds), "0:02")
        self.assertEqual(info.video_codec, "avc1")
        self.assertIs(info.faststart, True)
        self.assertIn(info.hdr, (None, False))
        self.assertEqual((info.width, info.height), (320, 240))

    def test_plain_hdr_clip(self):
        info = _mp4.probe(self.make("plain.mp4", *self.PQ))
        self.assertAlmostEqual(info.duration_seconds, 2.0, delta=0.05)
        self.assertEqual(info.video_codec, "avc1")
        self.assertIs(info.faststart, False)
        self.assertIs(info.hdr, True)


if __name__ == "__main__":
    unittest.main()
