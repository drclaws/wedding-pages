"""Shared fixtures and helpers for the build tests.

Every fixture lives in a temporary directory, so the tests do not depend on
`examples/`, on the real `assets/` or on `template.html`, and never create
media files inside the working tree.  The data is in the format 2 and
obviously fictional; the media files are tiny pictures and an MP4 file
assembled from boxes, made while the test runs.  The fixture template is a
page frame like `template.html`: the sections come from the fragments of the
repository.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build  # noqa: E402  (imported after sys.path is prepared)

if Path(build.__file__).resolve() != ROOT / "build.py":  # pragma: no cover
    raise RuntimeError(f"unexpected build module: {build.__file__}")

from tests.test_mp4 import audio_trak, sample_file, video_trak  # noqa: E402
from tools._png import encode_png  # noqa: E402

# --- fictional fixture data ------------------------------------------------

COUPLE_NAMES = "Алиса и Боб"
RSVP_DEADLINE = "1 мая 2030 года"
OUT_OF_TOWN_TEXT = "Из Энска ходит автобус.\n\nПодскажем, где остановиться."

PLACE_NAME = "Усадьба в Энске"
PLACE_ADDRESS = "Энск, Вымышленная улица, 1"
MEDIA_DIR = "m3d1a-f1xtur3-dir"

GREETING_TY = "Дорогая Ева!"
GREETING_VY = "Дорогие Карл и Клара!"
GREETING_THIRD = "Дорогой Гоша!"
#: A note to a section (Eve), to a section switched on for one guest (Gosha)
#: and to an event (Karl and Klara).
NOTE = "Личная приписка для Евы."
TRAVEL_NOTE = "Встретим Гошу на вокзале Энска."
EVENT_NOTE = "Про аллергию Клары помним."
#: The event that only Eve sees.
HIDDEN_EVENT_TITLE = "Второй день у пруда"

TOKEN_A = "EveToken-0123456789abcdefg"
TOKEN_B = "KarlKlaraToken-zyxwvutsrq98"
TOKEN_C = "GoshaToken-qwertyuiop12345"

MEDIA_FILES = ("clip.mp4", "poster.png", "venue-1.png", "venue-2.png", "route.png")
#: The size of the fixture clip and its poster.
VIDEO_SIZE = (640, 360)

#: Strings that must never appear in the output of `build` / `validate`.
PRIVATE_STRINGS = (
    COUPLE_NAMES,
    GREETING_TY,
    GREETING_VY,
    GREETING_THIRD,
    NOTE,
    TRAVEL_NOTE,
    EVENT_NOTE,
    HIDDEN_EVENT_TITLE,
    OUT_OF_TOWN_TEXT.splitlines()[0],
    PLACE_NAME,
    PLACE_ADDRESS,
    TOKEN_A,
    TOKEN_B,
    TOKEN_C,
)

SITE: dict = {
    "schemaVersion": 2,
    "coupleNames": COUPLE_NAMES,
    "rsvpDeadline": RSVP_DEADLINE,
    "mediaDir": MEDIA_DIR,
    "mainEvent": "dinner",
    "sections": [
        {"id": "cover", "type": "cover", "eyebrow": "Приглашение"},
        {
            "id": "invite",
            "title": "{greeting}",
            "widgets": [
                {
                    "type": "text",
                    "text": {"ty": "Приходи {eventDate}.", "vy": "Приходите {eventDate}."},
                }
            ],
        },
        {"id": "personal", "title": "Несколько слов лично", "widgets": []},
        {"id": "when", "title": "Дата и время", "widgets": [{"type": "date"}]},
        {"id": "where", "title": "Где и когда", "widgets": [{"type": "events"}]},
        {
            "id": "travel",
            "title": "Гостям из других городов",
            "visible": False,
            "widgets": [{"type": "text", "text": OUT_OF_TOWN_TEXT}],
        },
        {
            "id": "video",
            "title": "Видео",
            "widgets": [{"type": "media", "layout": "single", "items": ["clip"]}],
        },
        {
            "id": "rsvp",
            "title": "Подтверждение",
            "widgets": [
                {
                    "type": "text",
                    "text": {
                        "ty": "Ответь нам до {rsvpDeadline}.",
                        "vy": "Ответьте нам до {rsvpDeadline}.",
                    },
                }
            ],
        },
    ],
    "events": {
        "dinner": {
            "title": "Праздничный ужин",
            "location": "manor",
            "start": "2030-06-01T16:00:00+03:00",
            "end": "2030-06-01T22:00:00+03:00",
            "schedule": "day",
        },
        "brunch": {
            "title": HIDDEN_EVENT_TITLE,
            "location": "manor",
            "start": "2030-06-02T12:00:00+03:00",
            "end": "2030-06-02T14:00:00+03:00",
            "visible": False,
        },
    },
    "locations": {
        "manor": {
            "name": PLACE_NAME,
            "address": PLACE_ADDRESS,
            "description": "Описание площадки.",
            "geo": {"lat": 10.5, "lng": 20.25},
            "photos": ["venue-1", "venue-2"],
            "directions": "route",
        }
    },
    "schedules": {
        "day": [
            {"time": "16:00", "title": "Сбор гостей", "text": "У входа в парк"},
            {"time": "17:00", "title": "Церемония"},
        ]
    },
    "media": {
        "venue-1": {"type": "image", "file": "venue-1.png", "alt": "Усадьба"},
        "venue-2": {"type": "image", "file": "venue-2.png", "alt": "Сад"},
        "route": {"type": "image", "file": "route.png", "alt": "Схема проезда"},
        "clip": {"type": "video", "file": "clip.mp4", "poster": "poster.png", "alt": "Ролик"},
    },
}

INVITATIONS: list = [
    {
        "token": TOKEN_A,
        "greeting": GREETING_TY,
        "form": "ty",
        "events": {"brunch": {"visible": True}},
        "sections": {"personal": {"note": NOTE}},
    },
    {
        "token": TOKEN_B,
        "greeting": GREETING_VY,
        "form": "vy",
        "events": {"dinner": {"note": EVENT_NOTE}},
    },
    {
        "token": TOKEN_C,
        "greeting": GREETING_THIRD,
        "form": "ty",
        "sections": {"travel": {"visible": True, "note": TRAVEL_NOTE}},
    },
]

#: The events each fixture invitation sees (the calendar files its page links).
VISIBLE_EVENTS = {TOKEN_A: ("brunch", "dinner"), TOKEN_B: ("dinner",), TOKEN_C: ("dinner",)}

#: Fixture template: the page frame of `template.html` (the sections come
#: from the fragments of the repository).
TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Приглашение</title>
<meta property="og:image" content="{{ogImage}}">
<meta property="og:image:type" content="{{ogImageType}}">
<meta property="og:image:width" content="{{ogImageWidth}}">
<meta property="og:image:height" content="{{ogImageHeight}}">
<link rel="icon" href="{{faviconPath}}" type="{{faviconType}}">
<!-- a note for whoever edits the template: never published -->
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
<main class="page">
<!-- include:partials/sections -->
</main>
</body>
</html>
"""

#: Fixture stub: neutral, the same for every wrong address.
STUB = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Страница не найдена</title>
<link rel="icon" href="{{faviconPath}}" type="{{faviconType}}">
<!-- a note for whoever edits the stub: never published -->
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
<p>Такой страницы нет.</p>
</body>
</html>
"""


#: Design tokens of the fixture `app.css` (one line, so that a test can append
#: it to a style sheet of its own without moving the line numbers).
TOKEN_COLORS = {
    "--color-bg": "#fdfcfa",
    "--color-surface": "#f2efe9",
    "--color-surface-sunken": "#e6e1d8",
    "--color-text": "#22201d",
    "--color-text-muted": "#5c5851",
    "--color-accent": "#7a3e2b",
    "--color-on-accent": "#ffffff",
    "--color-line": "#cfc8bb",
}


def tokens_css(**overrides: str) -> str:
    """`:root{…}` with the fixture tokens; `accent="#123456"` replaces one."""
    colors = dict(TOKEN_COLORS)
    for role, value in overrides.items():
        colors[f"--color-{role.replace('_', '-')}"] = value
    return ":root{" + ";".join(f"{name}:{value}" for name, value in colors.items()) + "}\n"


def write_app_css(code_dir: Path, text: str = "", **overrides: str) -> Path:
    """Replace the fixture `app.css`: `text`, then the tokens the build needs."""
    path = Path(code_dir) / "assets" / "app.css"
    path.write_text(text + tokens_css(**overrides), encoding="utf-8")
    return path


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


def png_bytes(width: int = 4, height: int = 3) -> bytes:
    """A tiny grey PNG picture."""
    return encode_png(width, height, [bytes([128, 128, 128]) * width] * height)


def mp4_bytes(width: int = VIDEO_SIZE[0], height: int = VIDEO_SIZE[1]) -> bytes:
    """An MP4 file made of boxes only: H.264 video, sound, 2 s, `+faststart`."""
    return sample_file(video_trak(width=width, height=height), audio_trak())


#: Pictures of their own size, so that every fixture file has its own contents.
PICTURE_SIZES = {
    "poster.png": VIDEO_SIZE,
    "venue-1.png": (4, 3),
    "venue-2.png": (3, 4),
    "route.png": (5, 4),
}


def media_bytes(name: str) -> bytes:
    """Placeholder contents by the extension: a picture, a clip or nothing."""
    extension = os.path.splitext(name)[1].lower()
    if extension == ".png":
        return png_bytes(*PICTURE_SIZES.get(name, (2, 2)))
    if extension == ".mp4":
        return mp4_bytes()
    return b""


def write_media(directory: Path, names=MEDIA_FILES) -> Path:
    """Create placeholder media files (never real media)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(media_bytes(name))
    return directory


def published_name(name: str) -> str:
    """The name the build publishes a fixture media file under."""
    return build.media_tools.hashed_name(media_bytes(name), name)


def fixture_calendars(site: dict | None = None, invitations: list | None = None) -> dict:
    """Event id -> `build.CalendarFile` of the fixture data (or of the given data)."""
    site = site_data() if site is None else site
    invitations = invitations_data() if invitations is None else invitations
    return build.calendar_files(build.Data(site, invitations, build.page_tools.Usage(), {}))


def calendar_names(site: dict | None = None, invitations: list | None = None) -> dict[str, str]:
    """Event id -> the name the build publishes its calendar file under."""
    return {event_id: entry.name for event_id, entry in fixture_calendars(site, invitations).items()}


def write_assets(directory: Path) -> Path:
    """Assets fixture: regular files, dot-files and a dot-directory."""
    directory = Path(directory)
    (directory / "fonts").mkdir(parents=True, exist_ok=True)
    (directory / ".hidden").mkdir(parents=True, exist_ok=True)
    (directory / "app.css").write_text(tokens_css(), encoding="utf-8")
    (directory / "fonts" / "sans.woff2").write_bytes(b"wOF2 fixture")
    (directory / "fonts" / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".DS_Store").write_bytes(b"\x00")
    (directory / ".hidden" / "secret.css").write_text("/* x */\n", encoding="utf-8")
    return directory


def make_code_dir(
    parent: Path, template: str = TEMPLATE, assets: bool = True, stub: str = STUB
) -> Path:
    """A throwaway copy of `build.py` with its own template, stub and assets.

    The fragments of the template (`fragments/`) are copied as they are, if
    the code has any: the build reads them together with every template.
    """
    code_dir = Path(parent) / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "build.py", code_dir / "build.py")
    # the generators that the build imports (code only, no caches)
    (code_dir / "tools").mkdir(exist_ok=True)
    for source in sorted((ROOT / "tools").glob("*.py")):
        shutil.copy2(source, code_dir / "tools" / source.name)
    fragments = ROOT / build.FRAGMENTS_DIRNAME
    if fragments.is_dir():
        shutil.copytree(
            fragments, code_dir / build.FRAGMENTS_DIRNAME, symlinks=True, dirs_exist_ok=True
        )
    (code_dir / "template.html").write_text(template, encoding="utf-8")
    (code_dir / "stub.html").write_text(stub, encoding="utf-8")
    if assets:
        write_assets(code_dir / "assets")
    return code_dir


def run_cli(code_dir: Path, *args: str, cwd: Path, env: dict | None = None):
    """Run `python build.py …` from the throwaway code directory."""
    environ = dict(os.environ)
    # the tests must behave the same on a developer machine and in CI
    for name in ("BUILD_DEBUG", "CI", "GITHUB_ACTIONS"):
        environ.pop(name, None)
    environ["PYTHONIOENCODING"] = "utf-8"
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
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


def run_main(code_dir: Path, *args: str, env: dict | None = None):
    """Run `build.main()` in-process as if `build.py` lived in `code_dir`.

    Much faster than a subprocess and lets a test patch module constants.
    Returns an object with `returncode`, `stdout` and `stderr`.
    """
    environ = {
        key: value
        for key, value in os.environ.items()
        if key not in ("BUILD_DEBUG", "CI", "GITHUB_ACTIONS")
    }
    environ.update(env or {})
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(build, "CODE_DIR", Path(code_dir)), mock.patch.dict(
        os.environ, environ, clear=True
    ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = build.main([str(arg) for arg in args])
        except SystemExit as exc:  # argparse: usage errors
            code = exc.code if isinstance(exc.code, int) else 2
    return SimpleNamespace(
        returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
    )


def tree_files(root: Path) -> list[str]:
    """Every file below `root` as a sorted list of relative '/' paths.

    Directories are listed as well (with a trailing '/') when they are empty,
    so that a snapshot also proves that no empty directory was created.
    """
    root = Path(root)
    found = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_dir() and not path.is_symlink():
            if not any(path.iterdir()):
                found.append(relative + "/")
        else:
            found.append(relative)
    return sorted(found)


def tree_digest(root: Path) -> dict[str, str]:
    """Relative path -> SHA-256 of the content, for every file below `root`."""
    root = Path(root)
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in tree_files(root)
        if not name.endswith("/")
    }


class CliTestCase(TempDirTestCase):
    """Fixture: code directory, data directory and placeholder media."""

    template = TEMPLATE

    def setUp(self) -> None:
        super().setUp()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.code = make_code_dir(self.tmp, template=self.template)
        self.data = write_data(self.tmp / "data")
        self.media = write_media(self.tmp / "media")
        self.out = self.work / "dist"

    def build_args(self, *extra: str) -> list[str]:
        return [
            "build",
            "--data",
            str(self.data),
            "--media",
            str(self.media),
            "--out",
            str(self.out),
            *extra,
        ]

    def run_build(self, *extra: str, cwd: Path | None = None, env: dict | None = None):
        return run_cli(self.code, *self.build_args(*extra), cwd=cwd or self.work, env=env)

    def run_validate(self, *extra: str):
        return run_cli(
            self.code,
            "validate",
            "--data",
            str(self.data),
            "--media",
            str(self.media),
            *extra,
            cwd=self.work,
        )

    def build_in_process(self, *extra: str, env: dict | None = None):
        """The same build through `run_main` (no subprocess)."""
        return run_main(self.code, *self.build_args(*extra), env=env)
