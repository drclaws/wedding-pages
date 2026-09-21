"""Facts about an MP4 file, read from its box headers (standard library only).

``probe`` answers the questions a build asks about a video before it is
published: how long it is, whether the index (``moov``) comes before the media
data (``mdat``) so that playback can start before the whole file arrives, which
codec the video track uses and whether the video is tagged as HDR.

The file is never read whole.  The top level is walked box header by box
header with ``seek``; the media data is skipped, and only ``moov`` is read into
memory, up to ``MAX_MOOV_BYTES``.  Inside ``moov`` only a fixed path of boxes
is followed (``trak`` / ``mdia`` / ``minf`` / ``stbl`` / ``stsd`` and the first
sample entry), so the nesting depth is bounded by construction, and the number
of box headers looked at is capped by ``MAX_BOXES``.

A broken structure (not an MP4 file, a truncated file, a box whose size does
not fit its parent) raises ``Mp4Error``; its message never quotes the file.
A structurally valid file whose fields cannot be interpreted (no ``mvhd``, a
zero time scale, an unknown box version) gives ``None`` for those fields.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Dict, Iterator, Optional, Tuple, Union

#: The largest ``moov`` payload that is read into memory.
MAX_MOOV_BYTES = 16 * 1024 * 1024
#: The largest number of box headers looked at in one file.
MAX_BOXES = 100_000
#: ``transfer_characteristics`` of HDR video (ITU-T H.273): PQ and HLG.
HDR_TRANSFERS = frozenset({16, 18})
#: Bytes of a ``VisualSampleEntry`` between its box header and its child boxes.
VISUAL_SAMPLE_ENTRY_BYTES = 78

_UNKNOWN_DURATION = {0: 0xFFFFFFFF, 1: 0xFFFFFFFFFFFFFFFF}
#: (offset of the time scale, layout of the time scale and duration) by version.
_MVHD_LAYOUT = {0: (12, ">II"), 1: (20, ">IQ")}
#: Offset of the transformation matrix in ``tkhd`` by version; the width and
#: the height (16.16 fixed point) follow the nine 32-bit matrix entries.
_TKHD_MATRIX = {0: 40, 1: 52}


class Mp4Error(Exception):
    """The file is not an MP4 file or its box structure is broken."""


@dataclass(frozen=True)
class Mp4Info:
    """What ``probe`` found; ``None`` means "unknown" for every field."""

    #: ``moov/mvhd`` duration divided by its time scale.
    duration_seconds: Optional[float] = None
    #: ``True`` if the top-level ``moov`` comes before the first ``mdat``;
    #: ``None`` if either of them is missing.
    faststart: Optional[bool] = None
    #: Four-character code of the first sample entry of the first video
    #: track, e.g. ``"avc1"`` or ``"hvc1"``; ``None`` without a video track.
    video_codec: Optional[str] = None
    #: ``True`` if the video sample entry has an ``nclx`` (or QuickTime
    #: ``nclc``) colour box with a PQ or HLG transfer, ``False`` with another
    #: transfer, ``None`` without such a box.
    hdr: Optional[bool] = None
    #: Display size of the video track from ``tkhd``, swapped when the track
    #: matrix turns the picture by a quarter turn (portrait phone clips).
    width: Optional[int] = None
    height: Optional[int] = None


Source = Union[str, os.PathLike, BinaryIO]
Span = Tuple[int, int]


def probe(source: Source) -> Mp4Info:
    """Read the facts about the MP4 file at ``source``.

    ``source`` is a path or a seekable binary stream (read from offset 0).
    Raises ``Mp4Error`` if the file cannot be read or is not a valid MP4 file.
    """
    try:
        if isinstance(source, (str, os.PathLike)):
            with open(source, "rb") as stream:
                return _probe_stream(stream)
        return _probe_stream(source)
    except OSError as exc:
        reason = exc.strerror or "input/output error"
        raise Mp4Error(f"cannot read the file: {reason}") from None


def format_duration(seconds: Optional[float]) -> str:
    """``m:ss`` (from an hour on, ``h:mm:ss``), rounded to the nearest second.

    A positive duration shorter than half a second shows as ``0:01``; an
    unknown, zero, negative or non-finite duration gives ``""``.
    """
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return ""
    total = max(1, math.floor(seconds + 0.5))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# --- box headers -------------------------------------------------------------


def _parse_header(head: bytes, room: int, overflow: str) -> Tuple[bytes, int, int]:
    """(type, header length, box size) of the box that starts with ``head``.

    ``head`` holds at least 8 bytes (16 if available); ``room`` is the number
    of bytes from the start of the box to the end of its parent or the file.
    ``overflow`` is the message for a box that does not fit into ``room``.
    """
    size, kind = struct.unpack_from(">I4s", head)
    header = 8
    if size == 1:  # a 64-bit size follows the type
        if len(head) < 16:
            raise Mp4Error(overflow)
        (size,) = struct.unpack_from(">Q", head, 8)
        header = 16
    elif size == 0:  # the box extends to the end of its parent (or the file)
        size = room
    if size < header:
        raise Mp4Error("invalid box size")
    if size > room:
        raise Mp4Error(overflow)
    return kind, header, size


class _Budget:
    """Counts box headers so that a crafted file cannot keep the parser busy."""

    def __init__(self) -> None:
        self.left = MAX_BOXES

    def spend(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise Mp4Error("too many boxes")


class _Moov:
    """The ``moov`` payload in memory and a walk over its boxes."""

    def __init__(self, data: bytes, budget: _Budget) -> None:
        self.data = data
        self.budget = budget

    def children(self, start: int, end: int) -> Iterator[Tuple[bytes, int, int]]:
        """(type, payload start, payload end) of the boxes in ``[start, end)``.

        Fewer than 8 trailing bytes cannot hold a box and are ignored
        (QuickTime ends some lists with a 32-bit zero).
        """
        position = start
        while end - position >= 8:
            self.budget.spend()
            head = self.data[position : min(end, position + 16)]
            kind, header, size = _parse_header(
                head, end - position, "a box extends past its parent"
            )
            yield kind, position + header, position + size
            position += size

    def find(self, start: int, end: int, *kinds: bytes) -> Dict[bytes, Span]:
        """The first box of each of ``kinds`` among the children: type -> payload span."""
        found: Dict[bytes, Span] = {}
        for kind, payload_start, payload_end in self.children(start, end):
            if kind in kinds and kind not in found:
                found[kind] = (payload_start, payload_end)
                if len(found) == len(kinds):
                    break
        return found

    def payload(self, span: Optional[Span]) -> bytes:
        return b"" if span is None else self.data[span[0] : span[1]]


# --- the top level -------------------------------------------------------------


def _probe_stream(stream: BinaryIO) -> Mp4Info:
    file_size = stream.seek(0, os.SEEK_END)
    budget = _Budget()
    moov: Optional[bytes] = None
    moov_at = mdat_at = None
    if file_size < 8:
        raise Mp4Error("not an MP4 file")
    position = 0
    while position < file_size and (moov_at is None or mdat_at is None):
        budget.spend()
        stream.seek(position)
        head = stream.read(min(16, file_size - position))
        if len(head) < 8:
            raise Mp4Error("the file is truncated")
        if position == 0 and head[4:8] != b"ftyp":
            raise Mp4Error("not an MP4 file")
        kind, header, size = _parse_header(
            head, file_size - position, "the file is truncated"
        )
        if kind == b"moov" and moov_at is None:
            moov_at = position
            if size - header > MAX_MOOV_BYTES:
                raise Mp4Error("moov is too large")
            stream.seek(position + header)
            moov = stream.read(size - header)
            if len(moov) != size - header:
                raise Mp4Error("the file is truncated")
        elif kind == b"mdat" and mdat_at is None:
            mdat_at = position
        position += size

    faststart = None if moov_at is None or mdat_at is None else moov_at < mdat_at
    if moov is None:
        return Mp4Info(faststart=faststart)
    return _probe_moov(_Moov(moov, budget), faststart)


# --- inside moov -------------------------------------------------------------------


def _probe_moov(moov: _Moov, faststart: Optional[bool]) -> Mp4Info:
    duration = None
    seen_mvhd = False
    video: Optional[dict] = None
    for kind, start, end in moov.children(0, len(moov.data)):
        if kind == b"mvhd" and not seen_mvhd:
            seen_mvhd = True
            duration = _duration(moov.data[start:end])
        elif kind == b"trak" and video is None:
            video = _video_track(moov, start, end)
        if seen_mvhd and video is not None:
            break
    return Mp4Info(duration_seconds=duration, faststart=faststart, **(video or {}))


def _duration(mvhd: bytes) -> Optional[float]:
    layout = _MVHD_LAYOUT.get(mvhd[0]) if mvhd else None
    if layout is None or len(mvhd) < layout[0] + struct.calcsize(layout[1]):
        return None
    timescale, duration = struct.unpack_from(layout[1], mvhd, layout[0])
    if not timescale or not duration or duration == _UNKNOWN_DURATION[mvhd[0]]:
        return None
    return duration / timescale


def _video_track(moov: _Moov, start: int, end: int) -> Optional[dict]:
    """The facts of a ``trak`` if its handler is ``vide``, else ``None``."""
    trak = moov.find(start, end, b"tkhd", b"mdia")
    if b"mdia" not in trak:
        return None
    mdia = moov.find(*trak[b"mdia"], b"hdlr", b"minf")
    hdlr = moov.payload(mdia.get(b"hdlr"))
    # FullBox header (4 bytes), pre_defined (4 bytes), handler_type
    if hdlr[8:12] != b"vide":
        return None
    codec = hdr = None
    stbl = moov.find(*mdia[b"minf"], b"stbl") if b"minf" in mdia else {}
    stsd = moov.find(*stbl[b"stbl"], b"stsd") if stbl else {}
    if stsd:
        codec, hdr = _sample_entry(moov, *stsd[b"stsd"])
    width, height = _display_size(moov.payload(trak.get(b"tkhd")))
    return {"video_codec": codec, "hdr": hdr, "width": width, "height": height}


def _sample_entry(moov: _Moov, start: int, end: int) -> Tuple[Optional[str], Optional[bool]]:
    """(codec, hdr) of the first entry of an ``stsd`` payload."""
    # FullBox header (4 bytes) and entry_count, then the entries as boxes
    if end - start < 8 or not struct.unpack_from(">I", moov.data, start + 4)[0]:
        return None, None
    entry = next(moov.children(start + 8, end), None)
    if entry is None:
        return None, None
    kind, entry_start, entry_end = entry
    codec = kind.decode("ascii") if all(0x20 <= byte < 0x7F for byte in kind) else None
    hdr = None
    for child, child_start, child_end in moov.children(
        entry_start + VISUAL_SAMPLE_ENTRY_BYTES, entry_end
    ):
        colour = moov.data[child_start:child_end]
        # colour_type, then colour_primaries, transfer_characteristics, ...
        if child == b"colr" and colour[:4] in (b"nclx", b"nclc") and len(colour) >= 10:
            (transfer,) = struct.unpack_from(">H", colour, 6)
            hdr = transfer in HDR_TRANSFERS
            break
    return codec, hdr


def _display_size(tkhd: bytes) -> Tuple[Optional[int], Optional[int]]:
    matrix_at = _TKHD_MATRIX.get(tkhd[0]) if tkhd else None
    if matrix_at is None or len(tkhd) < matrix_at + 44:
        return None, None
    a, b = struct.unpack_from(">ii", tkhd, matrix_at)
    fixed_width, fixed_height = struct.unpack_from(">II", tkhd, matrix_at + 36)
    width, height = round(fixed_width / 65536), round(fixed_height / 65536)
    if abs(b) > abs(a):  # a quarter turn
        width, height = height, width
    if not width or not height:
        return None, None
    return width, height
