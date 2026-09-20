"""Shared fixtures and helpers for the build tests.

Every fixture lives in a temporary directory, so the tests do not depend on
`examples/`, on the real `assets/` or on `template.html`, and never create
media files inside the working tree.  All data below is obviously fictional.
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

# --- fictional fixture data ------------------------------------------------

COUPLE_NAMES = "Алиса и Боб"
DATE_TEXT = "1 июня 2030 года"
RSVP_DEADLINE = "1 мая 2030 года"
OUT_OF_TOWN_TEXT = "Из Энска ходит автобус.\n\nПодскажем, где остановиться."

VENUE_NAME = "Усадьба в Энске"
VENUE_ADDRESS = "Энск, Вымышленная улица, 1"
MEDIA_DIR = "m3d1a-f1xtur3-dir"

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
    VENUE_NAME,
    VENUE_ADDRESS,
    TOKEN_A,
    TOKEN_B,
    TOKEN_C,
)

SITE: dict = {
    "coupleNames": COUPLE_NAMES,
    "dateISO": "2030-06-01T16:00:00+03:00",
    "dateText": DATE_TEXT,
    "rsvpDeadline": RSVP_DEADLINE,
    "mediaDir": MEDIA_DIR,
    "outOfTownText": OUT_OF_TOWN_TEXT,
    "video": {"file": "clip.mp4", "poster": "poster.jpg"},
    "schedule": [
        {"time": "16:00", "title": "Сбор гостей", "text": "У входа в парк"},
        {"time": "17:00", "title": "Церемония"},
    ],
    "venue": {
        "ready": True,
        "name": VENUE_NAME,
        "description": "Описание площадки.",
        "address": VENUE_ADDRESS,
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

#: Fixture template: uses every field of the template contract (values,
#: conditions, loops and all the computed fields) and passes the output checks.
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
<script src="/assets/vendor/lib.js" defer></script>
</head>
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
<p data-countdown="{{dateISO}}">{{dateText}} / {{rsvpDeadline}}</p>
<p><a href="{{icsPath}}" download>Добавить в календарь</a></p>
<ul>
<!-- each:schedule -->
<li>{{.time}} {{.title}}<!-- if:.text --> - {{.text}}<!-- endif --></li>
<!-- endeach -->
</ul>
<!-- if:venue.ready -->
<h2>{{venue.name}}</h2>
<p>{{venue.description}}</p>
<p>{{venue.address}}</p>
<div data-media="{{mediaPath}}">
<!-- each:venue.photos --><img src="{{.src}}" alt="" loading="lazy"><!-- endeach -->
</div>
<!-- if:venue.directionsSrc --><img src="{{venue.directionsSrc}}" alt="Схема проезда"><!-- endif -->
<!-- if:venue.hasMapLinks --><p>На карте:</p><!-- endif -->
<!-- if:venue.mapLinks.google --><a href="{{venue.mapLinks.google}}" target="_blank" rel="noopener noreferrer">Google</a><!-- endif -->
<!-- if:venue.mapLinks.yandex --><a href="{{venue.mapLinks.yandex}}" target="_blank" rel="noopener noreferrer">Яндекс</a><!-- endif -->
<!-- if:venue.mapLinks.apple --><a href="{{venue.mapLinks.apple}}" target="_blank" rel="noopener noreferrer">Apple</a><!-- endif -->
<!-- endif -->
<!-- if:!venue.ready --><p>Подробности сообщим позже</p><!-- endif -->
<!-- if:video.file -->
<video src="{{video.src}}" poster="{{video.posterSrc}}" controls playsinline preload="none"></video>
<!-- endif -->
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
    (directory / "app.css").write_text(tokens_css(), encoding="utf-8")
    (directory / "vendor" / "lib.js").write_text("// lib\n", encoding="utf-8")
    (directory / "fonts" / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".gitkeep").write_text("", encoding="utf-8")
    (directory / ".DS_Store").write_bytes(b"\x00")
    (directory / ".hidden" / "secret.css").write_text("/* x */\n", encoding="utf-8")
    return directory


def make_code_dir(
    parent: Path, template: str = TEMPLATE, assets: bool = True, stub: str = STUB
) -> Path:
    """A throwaway copy of `build.py` with its own template, stub and assets."""
    code_dir = Path(parent) / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "build.py", code_dir / "build.py")
    # the generators that the build imports (code only, no caches)
    (code_dir / "tools").mkdir(exist_ok=True)
    for source in sorted((ROOT / "tools").glob("*.py")):
        shutil.copy2(source, code_dir / "tools" / source.name)
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
