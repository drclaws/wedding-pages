"""Generate the site images that are derived from the design tokens.

* ``favicon.svg`` -- a small neutral mark (plain geometry);
* ``og.png``      -- the 1200x630 link preview image: a neutral composition
  without names, dates or any other text.

The colours come from the ``:root`` tokens of ``assets/app.css``, so the
images follow the design: change the tokens and build again.  The build calls
the functions of this module and writes the files straight into the output
directory; nothing generated is kept in the repository.  The same input gives
byte-identical files.

Usage (to look at the images without building the site)::

    python tools/gen_assets.py --out DIR [--css FILE]

Standard library only.
"""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

if __package__ in (None, ""):  # run as a script: make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._png import Canvas  # noqa: E402
from tools._tokens import Palette, TokenError, hex_color, load_palette  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSS = REPO_ROOT / "assets" / "app.css"

FAVICON_FILE = "favicon.svg"
OG_FILE = "og.png"
OG_SIZE = (1200, 630)

__all__ = [
    "FAVICON_FILE",
    "OG_FILE",
    "OG_SIZE",
    "Palette",
    "TokenError",
    "favicon_svg",
    "load_palette",
    "og_png",
    "poster_canvas",
]


def favicon_svg(palette: Palette) -> bytes:
    """The icon: a rounded accent square with a diamond outline and a dot."""
    accent = hex_color(palette.accent)
    mark = hex_color(palette.on_accent)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{accent}"/>'
        f'<path d="M32 13 51 32 32 51 13 32z" fill="none" stroke="{mark}" '
        'stroke-width="4" stroke-linejoin="round"/>'
        f'<circle cx="32" cy="32" r="5" fill="{mark}"/>'
        "</svg>\n"
    ).encode("utf-8")


def og_canvas(palette: Palette) -> Canvas:
    """The link preview: a framed panel with a centred geometric emblem."""
    width, height = OG_SIZE
    canvas = Canvas(width, height, palette.bg)
    cx, cy = width / 2, height / 2

    # panel with a border, and a hairline frame inside it
    margin = 36
    canvas.rect(margin, margin, width - margin, height - margin, palette.line)
    canvas.rect(margin + 2, margin + 2, width - margin - 2, height - margin - 2, palette.surface)
    inset = 60
    canvas.outline(inset, inset, width - inset, height - inset, 1, palette.line)
    # corner marks on the hairline frame
    arm, bar = 22, 3
    for dx in (1, -1):
        for dy in (1, -1):
            x = inset if dx > 0 else width - inset
            y = inset if dy > 0 else height - inset
            for w, h in ((arm, bar), (bar, arm)):
                canvas.rect(
                    min(x, x + dx * w), min(y, y + dy * h),
                    max(x, x + dx * w), max(y, y + dy * h),
                    palette.text,
                )  # fmt: skip

    # rules to the left and to the right of the emblem, closed by small diamonds
    for side in (-1, 1):
        near, far = cx + side * 232, cx + side * 430
        canvas.rect(min(near, far), cy - 1, max(near, far), cy + 1, palette.line)
        canvas.diamond(far, cy, 9, palette.text_muted)
        canvas.diamond(near, cy, 5, palette.accent)

    # emblem: sunken disc, accent ring, accent disc with a diamond outline and a dot
    canvas.disc(cx, cy, 190, palette.surface_sunken)
    canvas.disc(cx, cy, 146, palette.accent)
    canvas.disc(cx, cy, 140, palette.surface_sunken)
    canvas.disc(cx, cy, 110, palette.accent)
    canvas.diamond(cx, cy, 62, palette.on_accent)
    canvas.diamond(cx, cy, 50, palette.accent)
    canvas.disc(cx, cy, 13, palette.on_accent)
    return canvas


@functools.lru_cache(maxsize=8)
def og_png(palette: Palette) -> bytes:
    """``og.png`` as bytes (cached: a palette always gives the same file)."""
    return og_canvas(palette).to_png()


def poster_canvas(width: int, height: int, palette: Palette) -> Canvas:
    """A video poster placeholder of any proportion: dark gradient, play mark."""
    canvas = Canvas(width, height, palette.text)
    canvas.vertical_gradient(palette.text_muted, palette.text)
    unit = min(width, height)
    canvas.grid(max(8, unit // 6), max(1, unit // 360), palette.text)
    cx, cy = width / 2, height / 2
    canvas.ring(cx, cy, unit * 0.2, unit * 0.18, palette.bg)
    radius = unit * 0.1
    canvas.polygon(
        [(cx - radius * 0.6, cy - radius), (cx + radius, cy), (cx - radius * 0.6, cy + radius)],
        palette.bg,
    )
    canvas.frame(max(4, unit // 60), palette.line)
    return canvas


def generate(out_dir: Path, palette: Palette) -> list[Tuple[Path, int]]:
    """Write ``favicon.svg`` and ``og.png`` into ``out_dir``; (path, size) pairs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for name, data in ((FAVICON_FILE, favicon_svg(palette)), (OG_FILE, og_png(palette))):
        path = out_dir / name
        path.write_bytes(data)
        created.append((path, len(data)))
    return created


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate favicon.svg and og.png from the design tokens.",
    )
    parser.add_argument(
        "--css",
        type=Path,
        default=DEFAULT_CSS,
        help="style sheet with the :root tokens (default: assets/app.css in the repository)",
    )
    parser.add_argument("--out", type=Path, required=True, help="directory to write into")
    args = parser.parse_args(argv)
    try:
        for path, size in generate(args.out, load_palette(args.css)):
            print(f"{path}  {size} B")
    except TokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
