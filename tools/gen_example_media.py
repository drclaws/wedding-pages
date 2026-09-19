#!/usr/bin/env python3
"""Generate placeholder media files for an example data set.

Reads ``site.json`` from the data directory and creates in the output
directory exactly the media files it references:

* venue photos      -- ``venue.photos``
* directions image  -- ``venue.directionsImage``
* video poster      -- ``video.poster``
* video             -- ``video.file``

File names are taken from the data; the extension selects the format.
``.png`` images are written with the standard library only; ``.jpg``,
``.jpeg`` and ``.webp`` images are converted from a generated PNG with
``ffmpeg``.  The video (``.mp4``) is a short test-pattern clip with a sine
tone and also needs ``ffmpeg``; if ``ffmpeg`` is not in ``PATH`` the video is
skipped with a warning and the exit code stays 0.

Images are neutral gradient cards with a simple geometric pattern (frame,
grid, centre cross, off-centre focal mark, index number), so cropping and
``object-position`` are easy to see on the page.  Files are overwritten on
every run; files not referenced by the data are left untouched.  Empty
strings, ``null`` and missing fields mean "no file"; a data set without
media (for example ``venue.ready`` is false and there is no ``video``)
creates nothing.

Usage::

    python tools/gen_example_media.py [--data DIR] [--out DIR]

Defaults are ``examples/data`` and ``examples/media`` relative to the
repository root; explicit paths are relative to the current directory.
Only the created paths and their sizes are printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Callable, Iterable, List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "examples" / "data"
DEFAULT_OUT_DIR = REPO_ROOT / "examples" / "media"

# Hosting limit for a single file (25 MiB); nothing larger is ever written.
MAX_FILE_BYTES = 25 * 1024 * 1024

# Image sizes (width, height) by role.
PHOTO_SIZE = (1600, 1067)  # 3:2 landscape
PHOTO_PORTRAIT_SIZE = (1067, 1600)  # 2:3 portrait
PORTRAIT_PHOTO_INDEX = 1  # the second venue photo is portrait
POSTER_SIZE = (1280, 720)  # 16:9
DIRECTIONS_SIZE = (1200, 900)  # 4:3

# Video parameters (the poster is generated separately, see POSTER_SIZE).
VIDEO_SIZE = (1280, 720)
VIDEO_SECONDS = 5
VIDEO_FPS = 25
VIDEO_CRF = 30
VIDEO_TONE_HZ = 440

PNG_EXTENSIONS = frozenset({".png"})
FFMPEG_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4"})

Color = Tuple[int, int, int]

# TEMP: neutral placeholder colours, will be derived from design tokens (stage S8)
COLOR_LINE: Color = (0xA9, 0xA5, 0x9F)
COLOR_FRAME: Color = (0x7D, 0x79, 0x74)
COLOR_MARK: Color = (0x4B, 0x48, 0x45)
COLOR_MARK_LIGHT: Color = (0xF7, 0xF6, 0xF3)
PHOTO_GRADIENTS: Tuple[Tuple[Color, Color], ...] = (
    ((0xEA, 0xE7, 0xE2), (0xC2, 0xBD, 0xB5)),  # warm grey
    ((0xE4, 0xE8, 0xEB), (0xB6, 0xBE, 0xC5)),  # cool grey
    ((0xE6, 0xE9, 0xE3), (0xBA, 0xC0, 0xB5)),  # green grey
    ((0xEB, 0xE5, 0xE7), (0xC2, 0xB8, 0xBC)),  # rose grey
)
POSTER_GRADIENT: Tuple[Color, Color] = ((0x8E, 0x8A, 0x85), (0x4F, 0x4C, 0x49))
COLOR_MAP_BACKGROUND: Color = (0xE2, 0xE0, 0xDB)
COLOR_MAP_BLOCK_LINE: Color = (0xD2, 0xCF, 0xC9)
COLOR_MAP_ROAD: Color = (0xFB, 0xFA, 0xF8)
COLOR_MAP_ROUTE: Color = (0x6E, 0x6A, 0x65)

# Off-centre focal points (fractions of width/height), cycled through photos.
FOCAL_POINTS: Tuple[Tuple[float, float], ...] = (
    (1 / 3, 1 / 3),
    (2 / 3, 1 / 3),
    (1 / 3, 2 / 3),
    (2 / 3, 2 / 3),
)

# 5x7 bitmap digits for image index labels.
DIGITS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


class GenerationError(Exception):
    """Invalid input or a failed generation step; reported as ``error: ...``."""


# --------------------------------------------------------------------------
# PNG writer (standard library only; shared with other generators)
# --------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def encode_png(
    width: int,
    height: int,
    rows: Iterable[bytes],
    *,
    alpha: bool = False,
    level: int = 9,
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
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), level)),
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


# --------------------------------------------------------------------------
# Raster drawing
# --------------------------------------------------------------------------


class Canvas:
    """RGB raster kept as mutable rows; all drawing is done in row spans."""

    def __init__(self, width: int, height: int, color: Color = (255, 255, 255)):
        if width <= 0 or height <= 0:
            raise ValueError("canvas dimensions must be positive")
        self.width = width
        self.height = height
        row = bytes(color) * width
        self.rows = [bytearray(row) for _ in range(height)]

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

    def frame(self, thickness: int, color: Color) -> None:
        w, h, t = self.width, self.height, thickness
        self.rect(0, 0, w, t, color)
        self.rect(0, h - t, w, h, color)
        self.rect(0, t, t, h - t, color)
        self.rect(w - t, t, w, h - t, color)

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

    def polygon(self, points: Sequence[Tuple[float, float]], color: Color) -> None:
        """Fill a convex polygon (scanline at pixel centres)."""
        ys = [p[1] for p in points]
        start = max(0, math.floor(min(ys)))
        stop = min(self.height, math.ceil(max(ys)))
        edges = list(zip(points, list(points[1:]) + [points[0]]))
        for y in range(start, stop):
            yc = y + 0.5
            xs = [
                xa + (yc - ya) * (xb - xa) / (yb - ya)
                for (xa, ya), (xb, yb) in edges
                if (ya <= yc < yb) or (yb <= yc < ya)
            ]
            if len(xs) >= 2:
                self.span(y, min(xs), max(xs), color)

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

    def disc(self, cx: float, cy: float, radius: float, color: Color) -> None:
        self.ring(cx, cy, radius, 0, color)

    def ring(self, cx: float, cy: float, outer: float, inner: float, color: Color) -> None:
        for y in range(max(0, math.floor(cy - outer)), min(self.height, math.ceil(cy + outer))):
            dy = y + 0.5 - cy
            if abs(dy) > outer:
                continue
            xo = math.sqrt(outer * outer - dy * dy)
            if abs(dy) < inner:
                xi = math.sqrt(inner * inner - dy * dy)
                self.span(y, cx - xo, cx - xi, color)
                self.span(y, cx + xi, cx + xo, color)
            else:
                self.span(y, cx - xo, cx + xo, color)

    def text(self, value: str, x: float, y: float, scale: int, color: Color) -> None:
        """Draw digits with the built-in 5x7 bitmap font; ``(x, y)`` is top-left."""
        for index, char in enumerate(value):
            glyph = DIGITS[char]
            gx = x + index * 6 * scale
            for gy, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit == "1":
                        self.rect(
                            gx + col * scale,
                            y + gy * scale,
                            gx + (col + 1) * scale,
                            y + (gy + 1) * scale,
                            color,
                        )


def draw_photo(width: int, height: int, index: int) -> Canvas:
    """Venue photo placeholder: gradient, grid, centre cross, focal mark, number."""
    canvas = Canvas(width, height)
    top, bottom = PHOTO_GRADIENTS[index % len(PHOTO_GRADIENTS)]
    canvas.vertical_gradient(top, bottom)
    unit = min(width, height)
    canvas.grid(max(8, unit // 8), max(1, unit // 400), COLOR_LINE)

    cx, cy = width / 2, height / 2
    arm, bar = unit / 12, max(2, unit // 160)
    canvas.rect(cx - arm, cy - bar / 2, cx + arm, cy + bar / 2, COLOR_MARK)
    canvas.rect(cx - bar / 2, cy - arm, cx + bar / 2, cy + arm, COLOR_MARK)

    fx, fy = FOCAL_POINTS[index % len(FOCAL_POINTS)]
    canvas.ring(width * fx, height * fy, unit * 0.13, unit * 0.11, COLOR_MARK)
    canvas.disc(width * fx, height * fy, unit * 0.035, COLOR_MARK)

    border = max(4, unit // 60)
    canvas.frame(border, COLOR_FRAME)
    scale = max(1, unit // 64)
    canvas.text(str(index + 1), border * 3, border * 3, scale, COLOR_MARK)
    return canvas


def draw_poster(width: int, height: int) -> Canvas:
    """Video poster placeholder: dark gradient, grid, centred play mark."""
    canvas = Canvas(width, height)
    canvas.vertical_gradient(*POSTER_GRADIENT)
    unit = min(width, height)
    canvas.grid(max(8, unit // 6), max(1, unit // 360), POSTER_GRADIENT[1])
    cx, cy = width / 2, height / 2
    canvas.ring(cx, cy, unit * 0.2, unit * 0.18, COLOR_MARK_LIGHT)
    r = unit * 0.1
    canvas.polygon(
        [(cx - r * 0.6, cy - r), (cx + r, cy), (cx - r * 0.6, cy + r)],
        COLOR_MARK_LIGHT,
    )
    canvas.frame(max(4, unit // 60), COLOR_FRAME)
    return canvas


def draw_directions(width: int, height: int) -> Canvas:
    """Directions placeholder: schematic map with roads, route and a marker."""
    canvas = Canvas(width, height, COLOR_MAP_BACKGROUND)
    unit = min(width, height)
    canvas.grid(max(8, unit // 10), max(1, unit // 300), COLOR_MAP_BLOCK_LINE)

    road = unit * 0.045
    canvas.rect(0, height * 0.62 - road / 2, width, height * 0.62 + road / 2, COLOR_MAP_ROAD)
    canvas.rect(width * 0.3 - road / 2, 0, width * 0.3 + road / 2, height, COLOR_MAP_ROAD)
    canvas.line(width * 0.05, height * 0.98, width * 0.95, height * 0.08, road, COLOR_MAP_ROAD)

    mx, my = width * 0.7, height * 0.36
    route = unit * 0.012
    canvas.line(width * 0.3, height, width * 0.3, height * 0.62, route, COLOR_MAP_ROUTE)
    canvas.line(width * 0.3, height * 0.62, width * 0.52, height * 0.62, route, COLOR_MAP_ROUTE)
    canvas.line(width * 0.52, height * 0.62, mx, my, route, COLOR_MAP_ROUTE)
    canvas.disc(width * 0.3, height - route * 2, route * 2, COLOR_MAP_ROUTE)

    canvas.ring(mx, my, unit * 0.07, unit * 0.045, COLOR_MARK)
    canvas.disc(mx, my, unit * 0.02, COLOR_MARK)

    # North arrow in the top-left corner.
    ax, ay, size = unit * 0.1, unit * 0.1, unit * 0.04
    canvas.polygon([(ax, ay - size), (ax + size * 0.6, ay + size), (ax - size * 0.6, ay + size)], COLOR_MARK)

    canvas.frame(max(4, unit // 60), COLOR_FRAME)
    return canvas


# --------------------------------------------------------------------------
# Data -> list of files
# --------------------------------------------------------------------------


class MediaItem(NamedTuple):
    kind: str  # "photo" | "directions" | "poster" | "video"
    name: str
    field: str  # data field the name comes from, for messages
    index: int = 0  # photo position


def load_site(data_dir: Path) -> dict:
    """Read and parse ``site.json`` from ``data_dir``."""
    path = Path(data_dir) / "site.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GenerationError(f"{path}: file not found") from None
    except OSError as exc:
        raise GenerationError(f"{path}: cannot read: {exc.strerror}") from None
    try:
        site = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"{path}: invalid JSON: {exc}") from None
    if not isinstance(site, dict):
        raise GenerationError(f"{path}: expected a JSON object")
    return site


def _check_name(value: object, field: str, extensions: frozenset) -> str:
    if not isinstance(value, str) or not value:
        raise GenerationError(f"{field}: expected a non-empty file name")
    if any(ch in value for ch in "/\\\0") or ".." in value or value == ".":
        raise GenerationError(
            f"{field}: {value!r} must be a plain file name inside the media directory"
        )
    ext = os.path.splitext(value)[1].lower()
    if ext not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise GenerationError(f"{field}: {value!r} has an unsupported extension (use {allowed})")
    return value


def _optional_object(parent: dict, key: str, field: str) -> dict:
    value = parent.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GenerationError(f"{field}: expected an object or null")
    return value


def _optional_name(parent: dict, key: str, field: str, extensions: frozenset) -> str:
    value = parent.get(key)
    if value is None or value == "":
        return ""
    return _check_name(value, field, extensions)


def plan_media(site: dict) -> List[MediaItem]:
    """List the media files referenced by ``site`` (data order, no duplicates)."""
    image_extensions = PNG_EXTENSIONS | FFMPEG_IMAGE_EXTENSIONS
    items: List[MediaItem] = []

    venue = _optional_object(site, "venue", "venue")
    photos = venue.get("photos")
    if photos is None:
        photos = []
    if not isinstance(photos, list):
        raise GenerationError("venue.photos: expected a list of file names")
    for index, name in enumerate(photos):
        field = f"venue.photos[{index}]"
        items.append(MediaItem("photo", _check_name(name, field, image_extensions), field, index))
    name = _optional_name(venue, "directionsImage", "venue.directionsImage", image_extensions)
    if name:
        items.append(MediaItem("directions", name, "venue.directionsImage"))

    video = _optional_object(site, "video", "video")
    name = _optional_name(video, "poster", "video.poster", image_extensions)
    if name:
        items.append(MediaItem("poster", name, "video.poster"))
    name = _optional_name(video, "file", "video.file", VIDEO_EXTENSIONS)
    if name:
        items.append(MediaItem("video", name, "video.file"))

    unique: List[MediaItem] = []
    seen = set()
    for item in items:
        if item.name not in seen:
            seen.add(item.name)
            unique.append(item)
    return unique


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _run_ffmpeg(ffmpeg: str, args: List[str], name: str) -> None:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GenerationError(f"{name}: cannot run ffmpeg: {exc}") from None
    if result.returncode != 0:
        details = result.stderr.strip()[-800:] or f"exit code {result.returncode}"
        raise GenerationError(f"{name}: ffmpeg failed: {details}")


def _draw_image(item: MediaItem) -> Canvas:
    if item.kind == "photo":
        portrait = item.index == PORTRAIT_PHOTO_INDEX
        width, height = PHOTO_PORTRAIT_SIZE if portrait else PHOTO_SIZE
        return draw_photo(width, height, item.index)
    if item.kind == "poster":
        return draw_poster(*POSTER_SIZE)
    if item.kind == "directions":
        return draw_directions(*DIRECTIONS_SIZE)
    raise ValueError(f"not an image: {item.kind}")


def _render_image(item: MediaItem, dest: Path, work_dir: Path, ffmpeg: Optional[str]) -> None:
    canvas = _draw_image(item)
    ext = dest.suffix.lower()
    if ext in PNG_EXTENSIONS:
        write_png(dest, canvas.width, canvas.height, canvas.rows)
        return
    if not ffmpeg:
        raise GenerationError(f"{item.field}: {item.name!r} needs ffmpeg for conversion")
    source = work_dir / "source.png"
    write_png(source, canvas.width, canvas.height, canvas.rows)
    quality = ["-q:v", "3"] if ext in (".jpg", ".jpeg") else ["-quality", "82"]
    _run_ffmpeg(ffmpeg, ["-i", str(source), "-frames:v", "1", *quality, str(dest)], item.name)
    source.unlink()


def _render_video(item: MediaItem, dest: Path, ffmpeg: str) -> None:
    width, height = VIDEO_SIZE
    seconds = VIDEO_SECONDS
    args = [
        "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate={VIDEO_FPS}:duration={seconds}",
        "-f", "lavfi",
        "-i", f"sine=frequency={VIDEO_TONE_HZ}:sample_rate=48000:duration={seconds}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest", "-movflags", "+faststart", "-map_metadata", "-1",
        str(dest),
    ]  # fmt: skip
    _run_ffmpeg(ffmpeg, args, item.name)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _display_path(path: Path) -> str:
    try:
        relative = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return str(path)
    return str(path) if relative.startswith("..") else relative


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def report_created(path: Path, size: int) -> None:
    print(f"{_display_path(path)}  {_format_size(size)}")


def generate(
    site: dict,
    out_dir: Path,
    *,
    ffmpeg: Optional[str],
    report: Callable[[Path, int], None] = report_created,
) -> List[Tuple[Path, int]]:
    """Create the media files referenced by ``site`` in ``out_dir``.

    ``ffmpeg`` is the path to the ffmpeg binary or ``None``.  Returns the
    created ``(path, size)`` pairs; ``report`` is called for each of them.
    """
    items = plan_media(site)
    if not ffmpeg:
        for item in items:
            if item.kind != "video" and os.path.splitext(item.name)[1].lower() not in PNG_EXTENSIONS:
                raise GenerationError(
                    f"{item.field}: {item.name!r} needs ffmpeg for conversion; "
                    "install ffmpeg or use a .png file name"
                )
        videos = [item for item in items if item.kind == "video"]
        for item in videos:
            _warn(f"ffmpeg not found in PATH; skipping video {item.name!r}")
        items = [item for item in items if item.kind != "video"]
    if not items:
        return []

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    created: List[Tuple[Path, int]] = []
    with tempfile.TemporaryDirectory(prefix="gen-example-media-") as tmp:
        work_dir = Path(tmp)
        for item in items:
            staged = work_dir / item.name
            if item.kind == "video":
                _render_video(item, staged, ffmpeg)
            else:
                _render_image(item, staged, work_dir, ffmpeg)
            size = staged.stat().st_size
            if size > MAX_FILE_BYTES:
                raise GenerationError(
                    f"{item.name}: generated file is {size} bytes, "
                    f"over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit"
                )
            dest = out_dir / item.name
            shutil.copyfile(staged, dest)
            staged.unlink()
            created.append((dest, size))
            report(dest, size)
    return created


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate placeholder media files referenced by site.json.",
        epilog="The video and non-PNG images need ffmpeg in PATH; "
        "without ffmpeg the video is skipped with a warning.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="directory with site.json (default: examples/data in the repository)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="output media directory (default: examples/media in the repository)",
    )
    args = parser.parse_args(argv)
    try:
        site = load_site(args.data)
        generate(site, args.out, ffmpeg=shutil.which("ffmpeg"))
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
