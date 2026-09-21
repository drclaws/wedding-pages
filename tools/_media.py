"""Facts about media files: the size of a picture, the facts of a video and the
published name of a file (standard library only).

- `image_size` reads the size of a PNG, JPEG or WebP picture from its header;
  the file is never read whole (a JPEG is walked segment header by segment
  header with `seek`).
- `inspect` returns a `MediaInfo` for one file: its size in pixels, for an
  `.mp4` also the facts of `tools._mp4.probe`, and the problems found, as
  codes.  It never builds a message: the caller knows the field the file
  belongs to and words the message itself.
- `hashed_name` is the name a file is published under: `<sha256[:16]>.<ext>`,
  so that the name tells nothing about the contents and cannot be guessed.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Tuple, Union

from tools import _mp4

#: Hex digits of the content hash in a published name.
HASH_NAME_DIGITS = 16
#: How much is read at once while hashing.
_CHUNK_BYTES = 1024 * 1024
#: The largest JPEG header segment walked over before the frame header.
MAX_JPEG_SEGMENTS = 1000

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
#: JPEG markers that start a frame header (SOF0-SOF15 without DHT, JPG, DAC).
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
#: Markers without a length field.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})

#: The formats whose size `image_size` reads, by extension.
SIZED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
VIDEO_EXTENSION = ".mp4"
#: The codec every browser plays.
EXPECTED_VIDEO_CODEC = "avc1"

# Problem codes of `MediaInfo.problems`.
#: The file cannot be opened or read.
UNREADABLE = "unreadable"
#: The contents do not match the extension (or the header is broken).
NOT_THIS_TYPE = "not-this-type"
#: A picture whose size is not known: a format without a size reader
#: (AVIF, GIF, SVG) or a header without a usable size.
SIZE_UNKNOWN = "size-unknown"
#: The duration of a video is not in its header (e.g. a fragmented file
#: without `mehd`).
DURATION_UNKNOWN = "duration-unknown"
#: The index of a video (`moov`) comes after the media data.
NOT_FASTSTART = "not-faststart"
#: The video codec is not `EXPECTED_VIDEO_CODEC`.
UNEXPECTED_CODEC = "unexpected-codec"
#: The video codec cannot be read.
CODEC_UNKNOWN = "codec-unknown"
#: The video is tagged as HDR (PQ or HLG).
HDR_VIDEO = "hdr"

Size = Tuple[int, int]
Source = Union[str, os.PathLike, BinaryIO]


@dataclass(frozen=True)
class MediaInfo:
    """What `inspect` found out about one file; `None` means "unknown"."""

    #: The name of the file (as the data gives it); not a path.
    name: str
    width: Optional[int] = None
    height: Optional[int] = None
    #: Videos only.
    duration_seconds: Optional[float] = None
    faststart: Optional[bool] = None
    video_codec: Optional[str] = None
    hdr: Optional[bool] = None
    #: Problem codes, in a fixed order.
    problems: Tuple[str, ...] = ()

    @property
    def size(self) -> Optional[Size]:
        if self.width and self.height:
            return self.width, self.height
        return None


# --- pictures ---------------------------------------------------------------


def _png_size(head: bytes) -> Optional[Size]:
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return (width, height) if width and height else None


def _webp_size(head: bytes) -> Optional[Size]:
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    chunk = head[12:16]
    if chunk == b"VP8 ":
        # frame tag (3 bytes), start code, then 14-bit width and height
        if head[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", head[26:30])
        width, height = width & 0x3FFF, height & 0x3FFF
    elif chunk == b"VP8L":
        if head[20] != 0x2F:
            return None
        (bits,) = struct.unpack("<I", head[21:25])
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X":
        # flags (4 bytes), then the canvas size minus one, 24 bits each
        width = int.from_bytes(head[24:27], "little") + 1
        height = int.from_bytes(head[27:30], "little") + 1
    else:
        return None
    return (width, height) if width and height else None


def _jpeg_size(stream: BinaryIO) -> Optional[Size]:
    stream.seek(0)
    if stream.read(3) != _JPEG_SIGNATURE:
        return None
    position = 2
    for _ in range(MAX_JPEG_SEGMENTS):
        stream.seek(position)
        head = stream.read(4)
        if len(head) < 2 or head[0] != 0xFF:
            return None
        marker = head[1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker in _JPEG_STANDALONE:
            position += 2
            continue
        if len(head) < 4:
            return None
        (length,) = struct.unpack(">H", head[2:4])
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            frame = stream.read(5)
            if len(frame) < 5:
                return None
            height, width = struct.unpack(">HH", frame[1:5])
            return (width, height) if width and height else None
        if marker == 0xDA:  # start of scan: no frame header before the data
            return None
        position += 2 + length
    return None


def image_size(source: Source) -> Optional[Size]:
    """(width, height) of a PNG, JPEG or WebP picture, or None.

    `source` is a path or a seekable binary stream.  The type is recognised
    by the contents, not by the name.  Raises `OSError` if a path cannot be
    read.
    """
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return image_size(stream)
    source.seek(0)
    head = source.read(32)
    if head.startswith(PNG_SIGNATURE):
        return _png_size(head)
    if head.startswith(b"RIFF"):
        return _webp_size(head)
    if head.startswith(_JPEG_SIGNATURE):
        return _jpeg_size(source)
    return None


def _signature_matches(head: bytes, extension: str) -> bool:
    if extension == ".png":
        return head.startswith(PNG_SIGNATURE)
    if extension in (".jpg", ".jpeg"):
        return head.startswith(_JPEG_SIGNATURE)
    if extension == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return True  # no signature check for the other formats


# --- one file -------------------------------------------------------------------


def inspect(path: Union[str, os.PathLike], name: Optional[str] = None) -> MediaInfo:
    """The facts of the media file at `path`.

    `name` is the name the data uses for the file (the base name of `path`
    by default).  Problems are returned as codes in `MediaInfo.problems`,
    never raised; the contents of the file never reach them.
    """
    path = Path(path)
    name = path.name if name is None else name
    extension = path.suffix.lower()
    if extension == VIDEO_EXTENSION:
        return _inspect_video(path, name)
    try:
        with open(path, "rb") as stream:
            head = stream.read(32)
            if not _signature_matches(head, extension):
                return MediaInfo(name, problems=(NOT_THIS_TYPE,))
            size = image_size(stream) if extension in SIZED_IMAGE_EXTENSIONS else None
    except OSError:
        return MediaInfo(name, problems=(UNREADABLE,))
    if size is None:
        problems = (NOT_THIS_TYPE,) if extension in SIZED_IMAGE_EXTENSIONS else ()
        return MediaInfo(name, problems=problems + (SIZE_UNKNOWN,))
    return MediaInfo(name, width=size[0], height=size[1])


def _inspect_video(path: Path, name: str) -> MediaInfo:
    try:
        facts = _mp4.probe(path)
    except _mp4.Mp4Error:
        if not os.access(path, os.R_OK):
            return MediaInfo(name, problems=(UNREADABLE,))
        return MediaInfo(name, problems=(NOT_THIS_TYPE,))
    problems = []
    if facts.duration_seconds is None:
        problems.append(DURATION_UNKNOWN)
    if facts.faststart is False:
        problems.append(NOT_FASTSTART)
    if facts.video_codec is None:
        problems.append(CODEC_UNKNOWN)
    elif facts.video_codec != EXPECTED_VIDEO_CODEC:
        problems.append(UNEXPECTED_CODEC)
    if facts.hdr:
        problems.append(HDR_VIDEO)
    return MediaInfo(
        name,
        width=facts.width,
        height=facts.height,
        duration_seconds=facts.duration_seconds,
        faststart=facts.faststart,
        video_codec=facts.video_codec,
        hdr=facts.hdr,
        problems=tuple(problems),
    )


# --- published names ------------------------------------------------------------


def hashed_name(source: Union[str, os.PathLike, bytes], name: Optional[str] = None) -> str:
    """`<sha256[:16]>.<ext>`: the name a media file is published under.

    `source` is a path or the contents of the file; the extension (lower
    case) comes from `name`, else from the path.  Equal contents with the
    same extension always get the same name.
    """
    digest = hashlib.sha256()
    if isinstance(source, (bytes, bytearray)):
        digest.update(source)
        if name is None:
            raise ValueError("the name is needed to know the extension")
    else:
        with open(source, "rb") as stream:
            for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
        name = os.fspath(source) if name is None else name
    extension = os.path.splitext(name)[1].lower()
    return f"{digest.hexdigest()[:HASH_NAME_DIGITS]}{extension}"
