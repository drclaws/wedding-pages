"""A stand-in for the browser that renders the link preview image.

Imported by the package `tests`: every in-process build of the tests gets
this fake picture instead of starting Chrome, so the tests run without a
browser.  The real renderer stays at `REAL_LINK_PREVIEW_IMAGE` for the tests
of `tools/_preview.py` (the one with a real browser runs only when one is
found).  Builds in a subprocess get `LINK_PREVIEW_IMAGE=off` from
`tests.support.run_cli` unless a test asks otherwise.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import _preview  # noqa: E402

#: A JPEG that is nothing but its headers, 1200x630.
FAKE_JPEG = (
    b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    + b"\xff\xc0" + struct.pack(">HBHHB", 8, 8, 630, 1200, 0) + b"\xff\xd9"
)  # fmt: skip
FAKE_BROWSER = "FakeChrome/1.0"

#: What the fake was asked to render, the last call first: (page, files).
CALLS: list[tuple[str, dict]] = []

REAL_LINK_PREVIEW_IMAGE = _preview.link_preview_image


def fake_link_preview_image(page, files, chrome=None, environ=None):
    CALLS.insert(0, (page, dict(files)))
    for source in files.values():
        if not isinstance(source, (bytes, bytearray)):
            Path(source).read_bytes()  # every file of the page must exist
    return _preview.PreviewImage(
        content=FAKE_JPEG,
        width=1200,
        height=630,
        quality=85,
        browser=FAKE_BROWSER,
        viewport=(600, 315),
    )


_preview.link_preview_image = fake_link_preview_image
