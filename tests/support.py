"""Shared fixtures and helpers for the build tests.

Every fixture lives in a temporary directory, so the tests do not depend on
`examples/`, on the real `assets/` or on `template.html`, and never create
media files inside the working tree.  All data below is obviously fictional.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build  # noqa: E402  (imported after sys.path is prepared)

if Path(build.__file__).resolve() != ROOT / "build.py":  # pragma: no cover
    raise RuntimeError(f"unexpected build module: {build.__file__}")

# --- fictional fixture data ------------------------------------------------

COUPLE_NAMES = "Алиса и Боб"
DATE_TEXT = "1 июня 2030 года"
RSVP_DEADLINE = "1 мая 2030 года"
OUT_OF_TOWN_TEXT = "Из Энска ходит автобус.\n\nПодскажем, где остановиться."

GREETING_TY = "Дорогая Ева!"
GREETING_VY = "Дорогие Карл и Клара!"
GREETING_THIRD = "Дорогой Гоша!"
NOTE = "Личная приписка для Евы."
TRAVEL_NOTE = "Встретим Гошу на вокзале Энска."

TOKEN_A = "EveToken-0123456789abcdefg"
TOKEN_B = "KarlKlaraToken-zyxwvutsrq98"
TOKEN_C = "GoshaToken-qwertyuiop12345"

MEDIA_FILES = ("clip.mp4", "poster.jpg", "venue-1.webp", "venue-2.webp", "route.png")

#: Strings that must never appear in the output of `build` / `validate`.
PRIVATE_STRINGS = (
    COUPLE_NAMES,
    GREETING_TY,
    GREETING_VY,
    GREETING_THIRD,
    NOTE,
    TRAVEL_NOTE,
    OUT_OF_TOWN_TEXT.splitlines()[0],
    DATE_TEXT,
    TOKEN_A,
    TOKEN_B,
    TOKEN_C,
)

SITE: dict = {
    "coupleNames": COUPLE_NAMES,
    "dateISO": "2030-06-01T16:00:00+03:00",
    "dateText": DATE_TEXT,
    "rsvpDeadline": RSVP_DEADLINE,
    "mediaDir": "m3d1a-f1xtur3-dir",
    "outOfTownText": OUT_OF_TOWN_TEXT,
    "video": {"file": "clip.mp4", "poster": "poster.jpg"},
    "schedule": [
        {"time": "16:00", "title": "Сбор гостей", "text": "У входа в парк"},
        {"time": "17:00", "title": "Церемония"},
    ],
    "venue": {
        "ready": True,
        "name": "Усадьба в Энске",
        "description": "Описание площадки.",
        "address": "Энск, Вымышленная улица, 1",
        "photos": ["venue-1.webp", "venue-2.webp"],
        "directionsImage": "route.png",
        "geo": {"lat": 10.5, "lng": 20.25},
        "maps": {"googlePlaceId": "", "yandexOrgId": ""},
    },
}

INVITATIONS: list = [
    {
        "token": TOKEN_A,
        "greeting": GREETING_TY,
        "ty": True,
        "vy": False,
        "plusOne": False,
        "outOfTown": False,
        "note": NOTE,
    },
    {
        "token": TOKEN_B,
        "greeting": GREETING_VY,
        "ty": False,
        "vy": True,
        "plusOne": True,
        "outOfTown": False,
        "note": None,
        "travelNote": None,
    },
    {
        "token": TOKEN_C,
        "greeting": GREETING_THIRD,
        "ty": True,
        "vy": False,
        "plusOne": False,
        "outOfTown": True,
        "travelNote": TRAVEL_NOTE,
    },
]

#: Fixture template: exercises values, conditions and loops, nothing else.
TEMPLATE = """<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Приглашение</title></head>
<body>
<h1>{{coupleNames}}</h1>
<p class="greeting">{{greeting}}</p>
<!-- if:ty --><p>Приходи</p><!-- endif -->
<!-- if:vy --><p>Приходите</p><!-- endif -->
<!-- if:note --><p class="note">{{note}}</p><!-- endif -->
<!-- if:plusOne --><p>Можно со спутником</p><!-- endif -->
<!-- if:outOfTown -->
<p>{{outOfTownText}}</p>
<!-- if:travelNote --><p>{{travelNote}}</p><!-- endif -->
<!-- endif -->
<p>{{dateText}} / {{dateISO}} / {{rsvpDeadline}}</p>
<ul>
<!-- each:schedule -->
<li>{{.time}} {{.title}}<!-- if:.text --> - {{.text}}<!-- endif --></li>
<!-- endeach -->
</ul>
<!-- if:venue.ready -->
<h2>{{venue.name}}</h2>
<p>{{venue.address}}</p>
<!-- each:venue.photos --><img src="/assets/{{mediaDir}}/{{.}}" alt=""><!-- endeach -->
<!-- endif -->
<!-- if:!venue.ready --><p>Подробности сообщим позже</p><!-- endif -->
<!-- if:video.file -->
<video src="/assets/{{mediaDir}}/{{video.file}}" poster="/assets/{{mediaDir}}/{{video.poster}}"></video>
<!-- endif -->
</body>
</html>
"""


def site_data(**overrides) -> dict:
    """A deep copy of the fixture `site.json` with optional overrides."""
    data = copy.deepcopy(SITE)
    data.update(copy.deepcopy(overrides))
    return data


def invitations_data(count: int | None = None) -> list:
    """A deep copy of the fixture `invitations.json`."""
    data = copy.deepcopy(INVITATIONS)
    return data if count is None else data[:count]


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def write_data(directory: Path, site=None, invitations=None) -> Path:
    """Create a data directory with `site.json` and `invitations.json`."""
    directory = Path(directory)
    write_json(directory / "site.json", site_data() if site is None else site)
    write_json(
        directory / "invitations.json",
        invitations_data() if invitations is None else invitations,
    )
    return directory


def write_media(directory: Path, names=MEDIA_FILES) -> Path:
    """Create empty placeholder media files (never real media)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


def write_assets(directory: Path) -> Path:
    """Assets fixture: regular files, dot-files and a dot-directory."""
    directory = Path(directory)
    (directory / "fonts").mkdir(parents=True, exist_ok=True)
    (directory / "vendor").mkdir(parents=True, exist_ok=True)
    (directory / ".hidden").mkdir(parents=True, exist_ok=True)
    (directory / "app.css").write_text(":root{}\n", encoding="utf-8")
    (directory / "vendor" / "lib.js").write_text("// lib\n", encoding="utf-8")
    (directory / "fonts" / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".DS_Store").write_bytes(b"\x00")
    (directory / ".hidden" / "secret.css").write_text("/* x */\n", encoding="utf-8")
    return directory


def make_code_dir(parent: Path, template: str = TEMPLATE, assets: bool = True) -> Path:
    """A throwaway copy of `build.py` with its own template and assets."""
    code_dir = Path(parent) / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "build.py", code_dir / "build.py")
    (code_dir / "template.html").write_text(template, encoding="utf-8")
    if assets:
        write_assets(code_dir / "assets")
    return code_dir


def run_cli(code_dir: Path, *args: str, cwd: Path, env: dict | None = None):
    """Run `python build.py …` from the throwaway code directory."""
    environ = dict(os.environ)
    environ.pop("BUILD_DEBUG", None)
    environ["PYTHONIOENCODING"] = "utf-8"
    if env:
        environ.update(env)
    return subprocess.run(
        [sys.executable, str(Path(code_dir) / "build.py"), *args],
        cwd=str(cwd),
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TempDirTestCase(unittest.TestCase):
    """Base class that gives every test its own temporary directory."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="invite-build-test-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()

    def assertNoPrivateData(self, *outputs: str) -> None:
        """Guest data and full tokens must never reach the log output."""
        for output in outputs:
            for secret in PRIVATE_STRINGS:
                self.assertNotIn(secret, output, f"{secret!r} leaked into the output")
            self.assertNotIn("Traceback", output)
