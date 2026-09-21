"""Generate placeholder media files for an example data set.

Reads ``site.json`` (the data format 2) from the data directory and creates
in the output directory exactly the files of its media registry
(``media.<id>``), in the order of the registry:

* a picture (``"type": "image"``)  -- ``file``: a photo, or a map when a
  place uses the item as its ``directions``;
* a video (``"type": "video"``)    -- ``file``: a short test-pattern clip, and
  ``poster``: its first-frame picture;
* ``thumb`` of either type         -- the picture of the tile.

Every item has its own size: ``width`` / ``height`` of the item when both are
given, otherwise the default of its role (a video is a portrait clip,
720x1280).  The poster and the tile picture of a video have the size of the
clip, and the poster has no play mark: the page puts its own over it.

File names are taken from the data and follow the rules of the build (plain
names inside the media directory); the extension selects the format.
``.png`` images are written with the standard library only; ``.jpg`` and
``.jpeg`` images are converted from a generated PNG with ``ffmpeg``.  A video
(``.mp4``) also needs ``ffmpeg``; if ``ffmpeg`` is not in ``PATH`` the videos
are skipped with a warning and the exit code stays 0.

Images are neutral gradient cards with a simple geometric pattern (frame,
grid, centre cross, off-centre focal mark, index number), so cropping and
``object-position`` are easy to see on the page.  Their colours come from the
design tokens of ``assets/app.css``.  Files are overwritten on every run;
files the registry does not name are left untouched.  A data set without a
media registry creates nothing.

Usage::

    python tools/gen_example_media.py [--data DIR] [--out DIR] [--css FILE]

Defaults are ``examples/data`` and ``examples/media`` relative to the
repository root; explicit paths are relative to the current directory.
Only the created paths and their sizes are printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run as a script: `tools` lives there
    sys.path.insert(0, str(REPO_ROOT))

from tools._data import check_media_name  # noqa: E402
from tools._png import PNG_SIGNATURE, Canvas, encode_png, write_png  # noqa: E402,F401
from tools._tokens import Palette, TokenError, load_palette  # noqa: E402

DEFAULT_DATA_DIR = REPO_ROOT / "examples" / "data"
DEFAULT_OUT_DIR = REPO_ROOT / "examples" / "media"
DEFAULT_CSS = REPO_ROOT / "assets" / "app.css"

# Hosting limit for a single file (25 MiB); nothing larger is ever written.
MAX_FILE_BYTES = 25 * 1024 * 1024

# Default image sizes (width, height) by role; `width` / `height` of an item
# override them.
PHOTO_SIZE = (1600, 1067)  # 3:2 landscape
PHOTO_PORTRAIT_SIZE = (1067, 1600)  # 2:3 portrait
PORTRAIT_PHOTO_INDEX = 1  # the second photo of the registry is portrait
DIRECTIONS_SIZE = (1200, 900)  # 4:3

# Video parameters: by default a portrait clip, as filmed on a phone.  The
# poster is generated separately with the size of the clip.
VIDEO_SIZE = (720, 1280)  # 9:16
POSTER_SIZE = VIDEO_SIZE
VIDEO_SECONDS = 5
VIDEO_FPS = 25
VIDEO_CRF = 30
VIDEO_TONE_HZ = 440

PNG_EXTENSIONS = frozenset({".png"})
FFMPEG_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg"})
VIDEO_EXTENSIONS = frozenset({".mp4"})

Color = Tuple[int, int, int]

def photo_gradients(palette: Palette) -> Tuple[Tuple[Color, Color], ...]:
    """Top and bottom colours of the photo placeholders, cycled through photos."""
    return (
        (palette.surface, palette.surface_sunken),
        (palette.bg, palette.surface_sunken),
        (palette.surface, palette.line),
        (palette.surface_sunken, palette.line),
    )


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
# Drawing (the PNG writer and the canvas are shared, see `_png.py`)
# --------------------------------------------------------------------------


def draw_digits(canvas: Canvas, value: str, x: float, y: float, scale: int, color: Color) -> None:
    """Draw digits with the built-in 5x7 bitmap font; ``(x, y)`` is top-left."""
    for index, char in enumerate(value):
        gx = x + index * 6 * scale
        for gy, bits in enumerate(DIGITS[char]):
            for col, bit in enumerate(bits):
                if bit == "1":
                    canvas.rect(
                        gx + col * scale,
                        y + gy * scale,
                        gx + (col + 1) * scale,
                        y + (gy + 1) * scale,
                        color,
                    )


def draw_photo(width: int, height: int, index: int, palette: Palette) -> Canvas:
    """Photo placeholder: gradient, grid, centre cross, focal mark, number."""
    canvas = Canvas(width, height, palette.surface)
    gradients = photo_gradients(palette)
    canvas.vertical_gradient(*gradients[index % len(gradients)])
    unit = min(width, height)
    canvas.grid(max(8, unit // 8), max(1, unit // 400), palette.line)

    cx, cy = width / 2, height / 2
    arm, bar = unit / 12, max(2, unit // 160)
    canvas.rect(cx - arm, cy - bar / 2, cx + arm, cy + bar / 2, palette.text)
    canvas.rect(cx - bar / 2, cy - arm, cx + bar / 2, cy + arm, palette.text)

    fx, fy = FOCAL_POINTS[index % len(FOCAL_POINTS)]
    canvas.ring(width * fx, height * fy, unit * 0.13, unit * 0.11, palette.accent)
    canvas.disc(width * fx, height * fy, unit * 0.035, palette.accent)

    border = max(4, unit // 60)
    canvas.frame(border, palette.text_muted)
    draw_digits(canvas, str(index + 1), border * 3, border * 3, max(1, unit // 64), palette.text)
    return canvas


def draw_directions(width: int, height: int, palette: Palette) -> Canvas:
    """Directions placeholder: schematic map with roads, route and a marker."""
    canvas = Canvas(width, height, palette.surface_sunken)
    unit = min(width, height)
    canvas.grid(max(8, unit // 10), max(1, unit // 300), palette.line)

    road = unit * 0.045
    canvas.rect(0, height * 0.62 - road / 2, width, height * 0.62 + road / 2, palette.bg)
    canvas.rect(width * 0.3 - road / 2, 0, width * 0.3 + road / 2, height, palette.bg)
    canvas.line(width * 0.05, height * 0.98, width * 0.95, height * 0.08, road, palette.bg)

    mx, my = width * 0.7, height * 0.36
    route = unit * 0.012
    canvas.line(width * 0.3, height, width * 0.3, height * 0.62, route, palette.accent)
    canvas.line(width * 0.3, height * 0.62, width * 0.52, height * 0.62, route, palette.accent)
    canvas.line(width * 0.52, height * 0.62, mx, my, route, palette.accent)
    canvas.disc(width * 0.3, height - route * 2, route * 2, palette.accent)

    canvas.ring(mx, my, unit * 0.07, unit * 0.045, palette.text)
    canvas.disc(mx, my, unit * 0.02, palette.text)

    # North arrow in the top-left corner.
    ax, ay, size = unit * 0.1, unit * 0.1, unit * 0.04
    canvas.polygon(
        [(ax, ay - size), (ax + size * 0.6, ay + size), (ax - size * 0.6, ay + size)],
        palette.text,
    )

    canvas.frame(max(4, unit // 60), palette.text_muted)
    return canvas


def draw_poster(width: int, height: int, palette: Palette) -> Canvas:
    """Video poster placeholder of any proportion: dark gradient, grid, frame.

    No play mark of its own: the page draws one over the poster.
    """
    canvas = Canvas(width, height, palette.text)
    canvas.vertical_gradient(palette.text_muted, palette.text)
    unit = min(width, height)
    canvas.grid(max(8, unit // 6), max(1, unit // 360), palette.text)
    canvas.frame(max(4, unit // 60), palette.line)
    return canvas


# --------------------------------------------------------------------------
# Data -> list of files
# --------------------------------------------------------------------------


class MediaItem(NamedTuple):
    kind: str  # "photo" | "directions" | "thumb" | "poster" | "video"
    name: str
    field: str  # data field the name comes from, for messages
    size: Tuple[int, int]  # (width, height) in pixels
    index: int = 0  # photo position (the number drawn on it)


def load_site(data_dir: Path) -> dict:
    """Read and parse ``site.json`` from ``data_dir``."""
    path = Path(data_dir) / "site.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise GenerationError(f"{path}: file not found") from None
    except UnicodeDecodeError as exc:
        raise GenerationError(f"{path}: not valid UTF-8 (at byte {exc.start})") from None
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
    """A media file name that the build accepts as well (same rules)."""
    problem = check_media_name(value, extensions)
    if problem:
        raise GenerationError(f"{field}: {problem}")
    return str(value)


def _object(parent: dict, key: str, field: str) -> dict:
    """An object of the data; a missing one is empty."""
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise GenerationError(f"{field}: expected an object")
    return value


def _item_size(item: dict, field: str, default: Tuple[int, int], even: bool) -> Tuple[int, int]:
    """``width`` x ``height`` of a media item, else ``default``.

    ffmpeg needs even numbers for the pixel format of a clip.
    """
    width, height = item.get("width"), item.get("height")
    if width is None and height is None:
        return default
    for key, value in (("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise GenerationError(f"{field}.{key}: expected a positive whole number of pixels")
        if even and value % 2:
            raise GenerationError(f"{field}.{key}: expected an even number for the example clip")
    return width, height


def _directions_ids(site: dict) -> set:
    """Ids of the media items that a place shows as its directions."""
    ids = set()
    for place in _object(site, "locations", "locations").values():
        if isinstance(place, dict) and isinstance(place.get("directions"), str):
            ids.add(place["directions"])
    return ids


def plan_media(site: dict) -> List[MediaItem]:
    """List the files of the media registry of ``site`` (registry order, no
    duplicates), each with its role and size."""
    image_extensions = PNG_EXTENSIONS | FFMPEG_IMAGE_EXTENSIONS
    directions = _directions_ids(site)
    items: List[MediaItem] = []
    photos = 0

    for media_id, item in _object(site, "media", "media").items():
        field = f"media.{media_id}"
        if not isinstance(item, dict):
            raise GenerationError(f"{field}: expected an object")
        kind = item.get("type")
        if kind == "image":
            if media_id in directions:
                size = _item_size(item, field, DIRECTIONS_SIZE, even=False)
                role, index = "directions", 0
            else:
                default = PHOTO_PORTRAIT_SIZE if photos == PORTRAIT_PHOTO_INDEX else PHOTO_SIZE
                size = _item_size(item, field, default, even=False)
                role, index = "photo", photos
                photos += 1
            name = _check_name(item.get("file"), f"{field}.file", image_extensions)
            items.append(MediaItem(role, name, f"{field}.file", size, index))
        elif kind == "video":
            size = _item_size(item, field, VIDEO_SIZE, even=True)
            poster = _check_name(item.get("poster"), f"{field}.poster", image_extensions)
            items.append(MediaItem("poster", poster, f"{field}.poster", size))
            name = _check_name(item.get("file"), f"{field}.file", VIDEO_EXTENSIONS)
            items.append(MediaItem("video", name, f"{field}.file", size))
            index = photos
        else:
            raise GenerationError(f"{field}.type: expected 'image' or 'video'")
        if item.get("thumb") is not None:
            thumb = _check_name(item["thumb"], f"{field}.thumb", image_extensions)
            items.append(MediaItem("thumb", thumb, f"{field}.thumb", size, index))

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


def _draw_image(item: MediaItem, palette: Palette) -> Canvas:
    if item.kind in ("photo", "thumb"):
        return draw_photo(*item.size, item.index, palette)
    if item.kind == "poster":
        return draw_poster(*item.size, palette)
    if item.kind == "directions":
        return draw_directions(*item.size, palette)
    raise ValueError(f"not an image: {item.kind}")


def _render_image(
    canvas: Canvas, item: MediaItem, dest: Path, work_dir: Path, ffmpeg: Optional[str]
) -> None:
    ext = dest.suffix.lower()
    if ext in PNG_EXTENSIONS:
        write_png(dest, canvas.width, canvas.height, canvas.rows)
        return
    if not ffmpeg:
        raise GenerationError(f"{item.field}: {item.name!r} needs ffmpeg for conversion")
    source = work_dir / ".source.png"  # media names never start with a dot
    write_png(source, canvas.width, canvas.height, canvas.rows)
    _run_ffmpeg(ffmpeg, ["-i", str(source), "-frames:v", "1", "-q:v", "3", str(dest)], item.name)
    source.unlink()


def _render_video(item: MediaItem, dest: Path, ffmpeg: str) -> None:
    width, height = item.size
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


def load_tokens(css_path: Path) -> Palette:
    """The colours of the images from the design tokens of a style sheet."""
    try:
        return load_palette(css_path)
    except TokenError as exc:
        raise GenerationError(str(exc)) from None


def generate(
    site: dict,
    out_dir: Path,
    *,
    ffmpeg: Optional[str],
    report: Callable[[Path, int], None] = report_created,
    palette: Optional[Palette] = None,
) -> List[Tuple[Path, int]]:
    """Create the media files referenced by ``site`` in ``out_dir``.

    ``ffmpeg`` is the path to the ffmpeg binary or ``None``; ``palette`` are
    the colours of the images (default: the tokens of ``assets/app.css``).
    Returns the created ``(path, size)`` pairs; ``report`` is called for each
    of them.
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
    if palette is None:
        palette = load_tokens(DEFAULT_CSS)

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
                canvas = _draw_image(item, palette)
                _render_image(canvas, item, staged, work_dir, ffmpeg)
            written = staged.stat().st_size
            if written > MAX_FILE_BYTES:
                raise GenerationError(
                    f"{item.name}: generated file is {written} bytes, "
                    f"over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit"
                )
            dest = out_dir / item.name
            shutil.copyfile(staged, dest)
            staged.unlink()
            created.append((dest, written))
            report(dest, written)
    return created


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate placeholder files for the media registry of site.json.",
        epilog="Videos and non-PNG images need ffmpeg in PATH; "
        "without ffmpeg the videos are skipped with a warning.",
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
    parser.add_argument(
        "--css",
        type=Path,
        default=DEFAULT_CSS,
        help="style sheet with the design tokens (default: assets/app.css in the repository)",
    )
    args = parser.parse_args(argv)
    try:
        site = load_site(args.data)
        generate(
            site, args.out, ffmpeg=shutil.which("ffmpeg"), palette=load_tokens(args.css)
        )
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
