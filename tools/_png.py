"""A PNG writer and a small raster canvas (standard library only).

Shared by the generators of this directory.  The output depends on the input
only: a fixed compression level, no timestamps and no metadata chunks, so
equal pixels give identical bytes.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

Color = Tuple[int, int, int]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Fixed, so that the files are reproducible.
PNG_COMPRESSION_LEVEL = 9
#: Sub-scanlines per pixel row of the anti-aliased shapes.
SUBSAMPLES = 4


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def encode_png(
    width: int,
    height: int,
    rows: Iterable[bytes],
    *,
    alpha: bool = False,
) -> bytes:
    """Encode 8-bit RGB (or RGBA if ``alpha``) pixel rows as a PNG file.

    ``rows`` yields ``height`` byte strings of ``width * 3`` (or ``* 4``)
    bytes each, top to bottom.
    """
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    channels = 4 if alpha else 3
    stride = width * channels
    raw = bytearray()
    count = 0
    for row in rows:
        if len(row) != stride:
            raise ValueError(f"PNG row {count}: expected {stride} bytes, got {len(row)}")
        raw.append(0)  # filter type 0 (None)
        raw += row
        count += 1
    if count != height:
        raise ValueError(f"PNG: expected {height} rows, got {count}")
    color_type = 6 if alpha else 2
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"".join(
        (
            PNG_SIGNATURE,
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), PNG_COMPRESSION_LEVEL)),
            _png_chunk(b"IEND", b""),
        )
    )


def write_png(
    path: Path,
    width: int,
    height: int,
    rows: Iterable[bytes],
    *,
    alpha: bool = False,
) -> int:
    """Write a PNG file (see ``encode_png``); return its size in bytes."""
    data = encode_png(width, height, rows, alpha=alpha)
    Path(path).write_bytes(data)
    return len(data)


def png_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from the header of a PNG file, or None."""
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


#: JPEG markers that start a frame header (SOF0-SOF15 without DHT, JPG, DAC).
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from the frame header of a JPEG file, or None."""
    if data[:3] != b"\xff\xd8\xff":
        return None
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            return None
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD8)):  # no payload
            position += 2
            continue
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            if position + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return (width, height) if width and height else None
        if marker == 0xDA:  # start of scan: no frame header before the data
            return None
        position += 2 + length
    return None


class Canvas:
    """RGB raster kept as mutable rows.

    Rectangles, frames and grids have hard pixel edges; discs, polygons and
    lines are anti-aliased (``SUBSAMPLES`` sub-scanlines, exact horizontal
    coverage).  Polygons must be convex.
    """

    def __init__(self, width: int, height: int, color: Color):
        if width <= 0 or height <= 0:
            raise ValueError("canvas dimensions must be positive")
        self.width = width
        self.height = height
        row = bytes(color) * width
        self.rows = [bytearray(row) for _ in range(height)]

    def to_png(self) -> bytes:
        return encode_png(self.width, self.height, self.rows)

    # --- hard-edged primitives ------------------------------------------

    def span(self, y: int, x0: float, x1: float, color: Color) -> None:
        """Fill pixels ``[x0, x1)`` of row ``y`` (clipped to the canvas)."""
        if not 0 <= y < self.height:
            return
        left = max(0, int(round(x0)))
        right = min(self.width, int(round(x1)))
        if right > left:
            self.rows[y][left * 3 : right * 3] = bytes(color) * (right - left)

    def vertical_gradient(self, top: Color, bottom: Color) -> None:
        last = max(1, self.height - 1)
        for y, row in enumerate(self.rows):
            t = y / last
            color = bytes(round(a + (b - a) * t) for a, b in zip(top, bottom))
            row[:] = color * self.width

    def rect(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        for y in range(max(0, int(round(y0))), min(self.height, int(round(y1)))):
            self.span(y, x0, x1, color)

    def outline(
        self, x0: float, y0: float, x1: float, y1: float, thickness: float, color: Color
    ) -> None:
        """The border of a rectangle, drawn inwards."""
        t = thickness
        self.rect(x0, y0, x1, y0 + t, color)
        self.rect(x0, y1 - t, x1, y1, color)
        self.rect(x0, y0 + t, x0 + t, y1 - t, color)
        self.rect(x1 - t, y0 + t, x1, y1 - t, color)

    def frame(self, thickness: int, color: Color) -> None:
        self.outline(0, 0, self.width, self.height, thickness, color)

    def grid(self, step: int, thickness: int, color: Color) -> None:
        """Lines every ``step`` pixels, aligned to the canvas centre."""
        cx, cy = self.width / 2, self.height / 2
        half = thickness / 2
        k = -math.floor(cx / step)
        while cx + k * step - half < self.width:
            x = cx + k * step
            self.rect(x - half, 0, x + half, self.height, color)
            k += 1
        k = -math.floor(cy / step)
        while cy + k * step - half < self.height:
            y = cy + k * step
            self.rect(0, y - half, self.width, y + half, color)
            k += 1

    # --- anti-aliased convex shapes ----------------------------------------

    def _blend(self, y: int, x: int, color: Color, coverage: float) -> None:
        if coverage <= 0 or not 0 <= x < self.width:
            return
        row = self.rows[y]
        offset = x * 3
        if coverage >= 1:
            row[offset : offset + 3] = bytes(color)
            return
        for channel in range(3):
            old = row[offset + channel]
            row[offset + channel] = int(old + (color[channel] - old) * coverage + 0.5)

    def fill(
        self,
        y_min: float,
        y_max: float,
        spans_at: Callable[[float], Sequence[Tuple[float, float]]],
        color: Color,
    ) -> None:
        """Fill a shape: ``spans_at(y)`` lists its ``(x0, x1)`` spans at height ``y``.

        Rows where every sub-scanline has a single span (convex shapes) get
        their inside filled in one step; only the edge pixels are blended.
        """
        weight = 1 / SUBSAMPLES
        for y in range(max(0, math.floor(y_min)), min(self.height, math.ceil(y_max))):
            samples = [
                [s for s in spans_at(y + (k + 0.5) * weight) if s[1] > s[0]]
                for k in range(SUBSAMPLES)
            ]
            spans = [s for sample in samples for s in sample]
            if not spans:
                continue
            lo = math.floor(min(s[0] for s in spans))
            hi = math.ceil(max(s[1] for s in spans))
            solid_from = solid_to = lo
            if all(len(sample) == 1 for sample in samples):
                left = math.ceil(max(s[0] for s in spans))
                right = math.floor(min(s[1] for s in spans))
                if right > left:
                    solid_from, solid_to = left, right
                    self.span(y, left, right, color)
            for x in (*range(lo, solid_from), *range(solid_to, hi)):
                coverage = 0.0
                for x0, x1 in spans:
                    coverage += max(0.0, min(x + 1, x1) - max(x, x0))
                self._blend(y, x, color, coverage * weight)

    def polygon(self, points: Sequence[Tuple[float, float]], color: Color) -> None:
        """Fill a convex polygon."""
        ys = [p[1] for p in points]
        edges = [
            (a, b)
            for a, b in zip(points, [*points[1:], points[0]])
            if a[1] != b[1]
        ]

        def spans_at(y: float) -> Sequence[Tuple[float, float]]:
            xs = [
                xa + (y - ya) * (xb - xa) / (yb - ya)
                for (xa, ya), (xb, yb) in edges
                if (ya <= y < yb) or (yb <= y < ya)
            ]
            return [(min(xs), max(xs))] if len(xs) >= 2 else []

        self.fill(min(ys), max(ys), spans_at, color)

    def line(
        self, x0: float, y0: float, x1: float, y1: float, thickness: float, color: Color
    ) -> None:
        """Thick straight segment drawn as a quadrilateral."""
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            return
        nx = -(y1 - y0) / length * thickness / 2
        ny = (x1 - x0) / length * thickness / 2
        self.polygon(
            [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)],
            color,
        )

    def diamond(self, cx: float, cy: float, radius: float, color: Color) -> None:
        self.polygon(
            [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)], color
        )

    def disc(self, cx: float, cy: float, radius: float, color: Color) -> None:
        self.ring(cx, cy, radius, 0, color)

    def ring(self, cx: float, cy: float, outer: float, inner: float, color: Color) -> None:
        def half_width(radius: float, dy: float) -> float:
            return math.sqrt(radius * radius - dy * dy) if abs(dy) < radius else 0.0

        def spans_at(y: float) -> Sequence[Tuple[float, float]]:
            dy = y - cy
            wide, hole = half_width(outer, dy), half_width(inner, dy)
            if not hole:
                return [(cx - wide, cx + wide)]
            return [(cx - wide, cx - hole), (cx + hole, cx + wide)]

        self.fill(cy - outer, cy + outer, spans_at, color)
