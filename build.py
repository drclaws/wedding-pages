#!/usr/bin/env python3
"""Static builder for personal invitation pages.

    python build.py build    [--data DIR] [--media DIR] [--out DIR]
    python build.py validate [--data DIR] [--media DIR]
    python build.py token
    python build.py links    --base URL [--data DIR]

Only the Python 3 standard library is used (Python >= 3.10).

`build` writes the pages (`i/<token>/index.html`), the stub (`index.html` and
`404.html`), `assets/` (including `assets/<mediaDir>/` with the referenced media
and `event.ics`), `_headers` and `robots.txt`, and then checks the finished
output: nothing but the expected files, no external resources, no inline
scripts, every local link resolves, no file above the hosting size limit.

Importing this module has no side effects: everything happens inside `main()`,
so other tools (tests, preview scripts) can reuse the public helpers:

    load_data(data_dir)                -> (site, invitations)
    build_context(site, invitation)    -> dict
    render(template_source, context)   -> str
    parse_template(source).render(ctx) -> str
    build_ics(site)                    -> bytes
    check_output(out_dir, tokens)      -> OutputStats

Exit codes: 0 - success, 1 - data/build error, 2 - bad command line.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import html
import json
import math
import os
import re
import secrets
import shutil
import stat
import sys
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Sequence
from urllib.parse import quote, unquote, urlsplit

# --------------------------------------------------------------------------
# Layout constants
# --------------------------------------------------------------------------

#: Directory that holds the code (template, assets).  Data/media/output
#: directories are resolved against the current working directory instead.
CODE_DIR = Path(__file__).resolve().parent

TEMPLATE_FILE = "template.html"
STUB_FILE = "stub.html"
ASSETS_DIRNAME = "assets"
SITE_FILE = "site.json"
INVITATIONS_FILE = "invitations.json"

#: Output layout.
PAGES_DIRNAME = "i"
PAGE_FILE = "index.html"
#: The stub is published under both names, byte for byte the same.
STUB_OUTPUTS = ("index.html", "404.html")
HEADERS_FILE = "_headers"
ROBOTS_FILE = "robots.txt"
#: Calendar file inside `assets/<mediaDir>/`: one for everybody, neutral name.
ICS_FILE = "event.ics"

#: Defaults relative to CODE_DIR.
DEFAULT_DATA_DIR = Path("examples") / "data"
DEFAULT_MEDIA_DIR = Path("examples") / "media"
#: Default relative to the current working directory.
DEFAULT_OUT_DIR = Path("dist")

#: Token rules (>= 120 bits of entropy).
MIN_TOKEN_LENGTH = 20
MIN_UUID_HEX_DIGITS = 30
#: Random media directory name (`site.json` -> `mediaDir`).
MIN_MEDIA_DIR_LENGTH = 16
#: Tokens and `mediaDir` become directory names, so their length is capped well
#: below the file name limit of common file systems (255 bytes).
MAX_NAME_LENGTH = 200

#: Everything a build may put at the top level of the output directory.  It is
#: used to recognise a previous build before replacing `--out`, and it is the
#: allow-list that the finished output is checked against.
OUTPUT_TOP_LEVEL = frozenset(
    {PAGES_DIRNAME, ASSETS_DIRNAME, *STUB_OUTPUTS, HEADERS_FILE, ROBOTS_FILE}
)
#: The entries of `OUTPUT_TOP_LEVEL` that are directories (the rest are files).
OUTPUT_TOP_LEVEL_DIRS = frozenset({PAGES_DIRNAME, ASSETS_DIRNAME})
#: Files created by the operating system; ignored when `--out` is inspected.
OS_JUNK_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

#: Hosting limit for a single file.
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Media files the data may refer to, by role.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm"})
#: Everything that may live below `assets/` in the output (`.txt` is meant for
#: the licences of vendored libraries).
ASSET_EXTENSIONS = frozenset(
    {".css", ".js", ".woff2", ".ico", ".ics", ".txt"} | IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
)
#: Never published, wherever they turn up in the output.
FORBIDDEN_OUTPUT_EXTENSIONS = frozenset({".json", ".map", ".py", ".md"})
#: Asset files that belong to the style guide and are not part of the site.
STYLEGUIDE_PREFIX = "styleguide."

#: `_headers` (Cloudflare Pages syntax) and `robots.txt`, written verbatim.
HEADERS_TEXT = """\
/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
/i/*
  Cache-Control: private, no-cache
"""
ROBOTS_TEXT = "User-agent: *\nDisallow: /\n"

#: Calendar entry: neutral on purpose (no names, nothing about the occasion).
ICS_SUMMARY = "Приглашение"
ICS_DEFAULT_DURATION = timedelta(hours=6)
ICS_PRODID = "-//invitation//static site build//RU"
ICS_UID_DOMAIN = "invitation"
#: `DTSTAMP` is a constant so that equal inputs give a byte-identical output.
ICS_DTSTAMP = "20000101T000000Z"
ICS_LINE_OCTETS = 75

#: The only external links a page may contain: map services, opened on click.
#: (host, required path prefix); the scheme is always https.
MAP_LINK_TARGETS = (
    ("www.google.com", "/maps/"),
    ("yandex.ru", "/maps/"),
    ("maps.apple.com", "/"),
)
YANDEX_MAPS_ZOOM = 16

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class BuildError(Exception):
    """Expected failure: reported as a plain message, never as a traceback."""


class TemplateError(BuildError):
    """Malformed template or a value that cannot be rendered."""

    def __init__(self, message: str, line: int | None = None, name: str = "template"):
        self.reason = message
        self.line = line
        self.name = name
        where = f"{name} line {line}" if line else name
        super().__init__(f"{where}: {message}")


class ValidationError(BuildError):
    """One or more problems in the input data (all of them are collected)."""

    def __init__(self, errors: Iterable[str], warnings: Iterable[str] = ()):
        self.errors = list(errors)
        self.warnings = list(warnings)
        super().__init__("; ".join(self.errors))


class Report:
    """Collects validation errors and warnings instead of failing fast.

    `on_warn` lets the caller show warnings as soon as they appear (the CLI
    prints them to stderr); errors are always reported together at the end.
    """

    def __init__(self, on_warn: Callable[[str], None] | None = None) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        #: Number of distinct media files referenced by the data.
        self.media_files = 0
        self._on_warn = on_warn

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        if self._on_warn is not None:
            self._on_warn(message)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_failed(self) -> None:
        if self.errors:
            raise ValidationError(self.errors, self.warnings)


# Sentinel for "no such field" (distinct from a field whose value is null).
class _MissingType:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingType()


# --------------------------------------------------------------------------
# Template engine
# --------------------------------------------------------------------------

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
#: `field`, `venue.name`, `.field` (field of the current each item), `.` (item)
_PATH_RE = re.compile(rf"(?:\.|\.?{_NAME}(?:\.{_NAME})*)\Z")
_PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}")
_BRACES_RE = re.compile(r"\{\{|\}\}")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
#: A comment that is meant to be a directive (anything else stays literal text).
_DIRECTIVE_LIKE_RE = re.compile(r"(?:if|each)\s*:|(?:endif|endeach)\b", re.IGNORECASE)
_IF_RE = re.compile(r"if\s*:\s*(!?)\s*(\S*)\Z")
_EACH_RE = re.compile(r"each\s*:\s*(\S*)\Z")
#: Template syntax that must not survive rendering.
_LEFTOVER_RE = re.compile(
    r"\{\{|\}\}|<!--\s*(?:(?:if|each)\s*:|(?:endif|endeach)\b)", re.IGNORECASE
)

_PARAGRAPH_BREAK_RE = re.compile(r"\n(?:[ \t]*\n)+")
_LEADING_BLANK_RE = re.compile(r"\A(?:[ \t]*\n)+")
_TRAILING_BLANK_RE = re.compile(r"(?:\n[ \t]*)+\Z")


class _Var:
    """`{{path}}` inside the template."""

    __slots__ = ("path", "line")

    def __init__(self, path: str, line: int):
        self.path = path
        self.line = line


class _Block:
    """`<!-- if:path -->…<!-- endif -->` or `<!-- each:path -->…<!-- endeach -->`."""

    __slots__ = ("kind", "path", "negate", "line", "children")

    def __init__(self, kind: str, path: str, negate: bool, line: int):
        self.kind = kind  # "if" | "each"
        self.path = path
        self.negate = negate
        self.line = line
        self.children: list[Any] = []


def text_to_html(text: str) -> str:
    """Escape a text value and turn line breaks into paragraphs / `<br>`.

    Escaping happens first, so nothing from the data can produce markup.
    Curly braces are escaped as well: a value may legitimately contain `{{`,
    while `{{` in the rendered page always means an unfilled placeholder.

    `\\r\\n` -> `\\n`; two or more line breaks -> `</p><p>`; a single line break
    -> `<br>`.  Leading and trailing blank lines are dropped.  A multi-line
    field is therefore expected to be the only content of a `<p>…</p>`.
    """
    escaped = html.escape(text, quote=True).replace("{", "&#123;").replace("}", "&#125;")
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    escaped = _LEADING_BLANK_RE.sub("", escaped)
    escaped = _TRAILING_BLANK_RE.sub("", escaped)
    escaped = _PARAGRAPH_BREAK_RE.sub("</p><p>", escaped)
    return escaped.replace("\n", "<br>")


def lookup(path: str, context: dict, item: Any = MISSING) -> Any:
    """Resolve a template path; returns MISSING when the field does not exist.

    `field` / `a.b.c` are resolved against the page context, `.field` against
    the current `each` item and `.` is the item itself.
    """
    if path == ".":
        return item
    if path.startswith("."):
        current: Any = item
        parts = path[1:].split(".")
    else:
        current = context
        parts = path.split(".")
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def is_truthy(value: Any) -> bool:
    """`if:` truthiness: false for missing, null, false, "", [], {} and 0."""
    if value is MISSING or value is None:
        return False
    return bool(value)


def format_value(
    value: Any, path: str = "?", line: int | None = None, name: str = "template"
) -> str:
    """Render a single `{{path}}` value."""
    if value is None:
        return ""
    if isinstance(value, str):
        return text_to_html(value)
    if isinstance(value, (bool, int, float)):
        return text_to_html(str(value))
    if isinstance(value, (list, tuple)):
        raise TemplateError(
            f"field '{path}' is an array and cannot be inserted with {{{{…}}}}; "
            f"use <!-- each:{path} --> instead",
            line,
            name,
        )
    if isinstance(value, dict):
        raise TemplateError(
            f"field '{path}' is an object and cannot be inserted with {{{{…}}}}; "
            "insert one of its fields instead",
            line,
            name,
        )
    raise TemplateError(
        f"field '{path}' has unsupported type {type(value).__name__}", line, name
    )


def _shorten(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Template:
    """A parsed template; parse once, render for every invitation."""

    def __init__(self, source: str, name: str = "template"):
        self.name = name
        self.nodes = _parse(source, name)

    def render(self, context: dict) -> str:
        out: list[str] = []
        _render_nodes(self.nodes, context, MISSING, out, self.name)
        result = "".join(out)
        check_rendered(result, self.name)
        return result


def parse_template(source: str, name: str = "template") -> Template:
    """Parse a template, raising TemplateError with a line number on failure."""
    return Template(source, name)


def render(template_source: str, context: dict, name: str = "template") -> str:
    """Parse and render a template in one step."""
    return Template(template_source, name).render(context)


def check_rendered(
    output: str,
    name: str = "template",
    problem: str = "rendered output still contains template syntax",
) -> None:
    """Fail if the rendered page still contains template syntax."""
    problems: list[str] = []
    newlines = [m.start() for m in re.finditer("\n", output)]
    for match in _LEFTOVER_RE.finditer(output):
        line = bisect.bisect_left(newlines, match.start()) + 1
        problems.append(f"line {line}: '{_shorten(match.group(0), 20)}'")
        if len(problems) == 5:
            break
    if problems:
        raise TemplateError(f"{problem}: " + "; ".join(problems), None, name)


def _parse(source: str, name: str) -> list[Any]:
    newlines = [m.start() for m in re.finditer("\n", source)]

    def line_at(pos: int) -> int:
        return bisect.bisect_left(newlines, pos) + 1

    root: list[Any] = []
    stack: list[_Block] = []

    def children() -> list[Any]:
        return stack[-1].children if stack else root

    def in_each() -> bool:
        return any(block.kind == "each" for block in stack)

    def check_path(path: str, line: int, shown: str) -> None:
        if not path:
            raise TemplateError(f"{shown} has no field path", line, name)
        if not _PATH_RE.match(path):
            raise TemplateError(f"{shown} has an invalid field path", line, name)
        if path.startswith(".") and not in_each():
            raise TemplateError(
                f"{shown} refers to an each item but is not inside <!-- each:… -->",
                line,
                name,
            )

    def add_literal(literal: str, offset: int) -> None:
        if not literal:
            return
        stray = _BRACES_RE.search(literal)
        if stray:
            raise TemplateError(
                f"unmatched '{stray.group(0)}'", line_at(offset + stray.start()), name
            )
        children().append(literal)

    def add_text(start: int, end: int) -> None:
        text = source[start:end]
        pos = 0
        for match in _PLACEHOLDER_RE.finditer(text):
            add_literal(text[pos : match.start()], start + pos)
            line = line_at(start + match.start())
            path = match.group(1).strip()
            shown = "{{" + _shorten(match.group(1), 40) + "}}"
            check_path(path, line, shown)
            children().append(_Var(path, line))
            pos = match.end()
        add_literal(text[pos:], start + pos)

    pos = 0
    for match in _COMMENT_RE.finditer(source):
        inner = match.group(1).strip()
        if not _DIRECTIVE_LIKE_RE.match(inner):
            continue  # an ordinary HTML comment: keep it as text
        add_text(pos, match.start())
        pos = match.end()
        line = line_at(match.start())
        shown = _shorten(match.group(0))

        if inner in ("endif", "endeach"):
            kind = "if" if inner == "endif" else "each"
            if not stack:
                raise TemplateError(
                    f"{shown} without a matching <!-- {kind}:… -->", line, name
                )
            open_block = stack[-1]
            if open_block.kind != kind:
                raise TemplateError(
                    f"{shown} does not match <!-- {open_block.kind}:{open_block.path} -->"
                    f" opened at line {open_block.line}",
                    line,
                    name,
                )
            stack.pop()
            continue

        if_match = _IF_RE.match(inner)
        each_match = _EACH_RE.match(inner)
        if if_match:
            kind, negate, path = "if", bool(if_match.group(1)), if_match.group(2)
        elif each_match:
            kind, negate, path = "each", False, each_match.group(1)
        else:
            raise TemplateError(
                f"malformed directive {shown} (expected <!-- if:path -->, "
                "<!-- if:!path -->, <!-- endif -->, <!-- each:path --> or "
                "<!-- endeach -->, all lowercase)",
                line,
                name,
            )
        check_path(path, line, shown)
        block = _Block(kind, path, negate, line)
        children().append(block)
        stack.append(block)

    add_text(pos, len(source))

    if stack:
        open_block = stack[-1]
        raise TemplateError(
            f"<!-- {open_block.kind}:{open_block.path} --> is not closed "
            f"(missing <!-- end{open_block.kind} -->)",
            open_block.line,
            name,
        )
    return root


def _render_nodes(
    nodes: Sequence[Any], context: dict, item: Any, out: list[str], name: str
) -> None:
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, _Var):
            value = lookup(node.path, context, item)
            if value is MISSING:
                raise TemplateError(f"field '{node.path}' not found", node.line, name)
            out.append(format_value(value, node.path, node.line, name))
        elif node.kind == "if":
            if is_truthy(lookup(node.path, context, item)) is not node.negate:
                _render_nodes(node.children, context, item, out, name)
        else:  # each
            sequence = lookup(node.path, context, item)
            if sequence is MISSING:
                raise TemplateError(
                    f"<!-- each:{node.path} -->: field '{node.path}' not found",
                    node.line,
                    name,
                )
            if sequence is None:
                continue
            if not isinstance(sequence, (list, tuple)):
                raise TemplateError(
                    f"<!-- each:{node.path} -->: field '{node.path}' is "
                    f"{json_type(sequence)}, expected an array",
                    node.line,
                    name,
                )
            for element in sequence:
                _render_nodes(node.children, context, element, out, name)


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

_TOKEN_CHARS_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_HEX_DASH_RE = re.compile(r"[0-9A-Fa-f-]+\Z")
_TOKEN_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def check_token(token: Any) -> str | None:
    """Return a problem description for `token`, or None when it is valid."""
    if not isinstance(token, str):
        return f"'token' must be a string, got {json_type(token)}"
    if not token:
        return "'token' is empty"
    if not _TOKEN_CHARS_RE.match(token):
        return "'token' may only contain A-Z, a-z, 0-9, '_' and '-'"
    if len(token) > MAX_NAME_LENGTH:
        return (
            f"'token' is too long: at most {MAX_NAME_LENGTH} characters allowed "
            f"(it becomes a directory name), got {len(token)}"
        )
    if _HEX_DASH_RE.match(token):
        # UUID-like token: only hex digits carry entropy.
        digits = len(token) - token.count("-")
        if digits < MIN_UUID_HEX_DIGITS:
            return (
                "'token' is too weak: a token built from hex digits and dashes needs "
                f"at least {MIN_UUID_HEX_DIGITS} hex digits, got {digits}"
            )
    elif len(token) < MIN_TOKEN_LENGTH:
        return (
            f"'token' is too short: at least {MIN_TOKEN_LENGTH} characters required, "
            f"got {len(token)}"
        )
    return None


def generate_token() -> str:
    """A fresh URL-safe token that passes `check_token`."""
    while True:
        token = secrets.token_urlsafe(16)
        if check_token(token) is None:
            return token


def invitation_label(index: int, token: Any = None) -> str:
    """Log-safe identification of an invitation: number + first 4 token chars."""
    if isinstance(token, str) and token:
        head = "".join(ch if ch in _TOKEN_ALPHABET else "?" for ch in token[:4])
        return f"invitation #{index} ({head}…)"
    return f"invitation #{index} (no token)"


# --------------------------------------------------------------------------
# Data loading and validation
# --------------------------------------------------------------------------

_MEDIA_DIR_RE = re.compile(
    rf"[A-Za-z0-9_-]{{{MIN_MEDIA_DIR_LENGTH},{MAX_NAME_LENGTH}}}\Z"
)
_DATE_ISO_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2}(?:\.\d{3}|\.\d{6})?)?)"
    r"(Z|z|[+-]\d{2}:\d{2})?\Z"
)

INVITATION_FIELDS = (
    "token",
    "greeting",
    "ty",
    "vy",
    "plusOne",
    "outOfTown",
    "note",
    "travelNote",
)
SITE_FIELDS = (
    "coupleNames",
    "dateISO",
    "dateText",
    "rsvpDeadline",
    "outOfTownText",
    "mediaDir",
    "video",
    "schedule",
    "venue",
)
VIDEO_FIELDS = ("file", "poster")
SCHEDULE_FIELDS = ("time", "title", "text")
VENUE_FIELDS = (
    "ready",
    "name",
    "description",
    "address",
    "photos",
    "directionsImage",
    "geo",
    "maps",
)
GEO_FIELDS = ("lat", "lng")
MAPS_FIELDS = ("googlePlaceId", "yandexOrgId")

_KINDS: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "string": ("a string", lambda v: isinstance(v, str)),
    "boolean": ("a boolean (true/false)", lambda v: isinstance(v, bool)),
    "number": (
        "a number",
        lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    ),
    "array": ("an array", lambda v: isinstance(v, list)),
    "object": ("an object", lambda v: isinstance(v, dict)),
}


def _is_finite(value: int | float) -> bool:
    """False for infinities, NaN and integers too large to fit into a float."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def json_type(value: Any) -> str:
    """Name of a value's JSON type, for error messages (never the value)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def parse_date_iso(value: str) -> datetime:
    """Parse `site.json` -> `dateISO`; a UTC offset is required.

    Accepts a strict subset of ISO 8601 so that the result does not depend on
    the Python version (3.10's `fromisoformat` is stricter than 3.11's), and
    normalises the `Z` suffix, which `fromisoformat` rejects before 3.11.
    """
    match = _DATE_ISO_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(
            "must be an ISO 8601 date and time with a UTC offset, "
            "e.g. 2030-01-02T18:30:00+03:00"
        )
    if not match.group(3):
        raise ValueError(
            "has no UTC offset; append the offset, e.g. +03:00 (or Z for UTC)"
        )
    offset = match.group(3)
    text = f"{match.group(1)}T{match.group(2)}" + (
        "+00:00" if offset in ("Z", "z") else offset
    )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("is not a valid date and time") from None
    if parsed.utcoffset() is None:  # pragma: no cover - guarded by the regex
        raise ValueError("has no UTC offset")
    return parsed


def format_size(size: int) -> str:
    """A byte count for the log: `512 B`, `1.5 KiB`, `25.0 MiB`."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _extension_list(extensions: Iterable[str]) -> str:
    return ", ".join(sorted(extensions))


def check_media_name(
    name: Any, extensions: Iterable[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
) -> str | None:
    """Problem description for a media file reference, or None when it is fine.

    `extensions` are the file types accepted for the field (compared without
    regard to letter case); only types that may be published are accepted.
    """
    if not isinstance(name, str):
        return f"must be a string, got {json_type(name)}"
    if not name.strip():
        return "is empty"
    if name != name.strip():
        return "has leading or trailing whitespace"
    if "/" in name or "\\" in name:
        return "must be a plain file name inside the media directory (no '/' or '\\')"
    if ".." in name:
        return "must not contain '..'"
    if name.startswith("."):
        return "must not start with a dot"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "contains control characters"
    if name.lower() == ICS_FILE:
        return f"must not be '{ICS_FILE}' (the name is reserved for the calendar file)"
    if os.path.splitext(name)[1].lower() not in extensions:
        return f"has an unsupported file type (allowed: {_extension_list(extensions)})"
    return None


def _media_extensions(field: str) -> frozenset[str]:
    """File types accepted for a media field (`video.file` is the only video)."""
    return VIDEO_EXTENSIONS if field == "video.file" else IMAGE_EXTENSIONS


def _quote_key(key: Any) -> str:
    text = key if isinstance(key, str) else str(key)
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return f"'{_shorten(text, 40)}'"


def _warn_unknown(obj: dict, known: Iterable[str], where: str, report: Report) -> None:
    for key in sorted(set(obj) - set(known), key=str):
        report.warn(f"{where}: unknown field {_quote_key(key)} is ignored")


def _field(
    obj: dict,
    key: str,
    kind: str,
    where: str,
    report: Report,
    *,
    prefix: str = "",
    required: bool = True,
    nullable: bool = False,
    nonempty: bool = False,
) -> Any:
    """Type-checked field access; reports problems and returns MISSING on error."""
    description, matches = _KINDS[kind]
    name = f"{prefix}{key}"
    if key not in obj:
        if required:
            report.error(f"{where}: missing required field '{name}'")
        return MISSING
    value = obj[key]
    if value is None and nullable:
        return None
    if not matches(value):
        suffix = " or null" if nullable else ""
        report.error(
            f"{where}: field '{name}' must be {description}{suffix}, got {json_type(value)}"
        )
        return MISSING
    if nonempty and kind == "string" and not value.strip():
        report.error(f"{where}: field '{name}' must not be empty")
        return MISSING
    if kind == "number" and not _is_finite(value):
        report.error(
            f"{where}: field '{name}' must be a finite number "
            "(the value is infinite or too large)"
        )
        return MISSING
    return value


def check_invitations(
    invitations: Any, report: Report, where: str = INVITATIONS_FILE
) -> None:
    """Validate the whole `invitations.json` document."""
    if not isinstance(invitations, list):
        report.error(
            f"{where}: the top-level value must be an array of invitations, "
            f"got {json_type(invitations)}"
        )
        return
    if not invitations:
        report.warn(f"{where}: contains no invitations")

    seen: dict[str, int] = {}
    for index, invitation in enumerate(invitations, start=1):
        if not isinstance(invitation, dict):
            report.error(
                f"{invitation_label(index)}: must be an object, "
                f"got {json_type(invitation)}"
            )
            continue
        token = invitation.get("token")
        label = invitation_label(index, token)

        if "token" not in invitation:
            report.error(f"{label}: missing required field 'token'")
        else:
            problem = check_token(token)
            if problem:
                report.error(f"{label}: {problem}")
            else:
                key = token.lower()
                if key in seen:
                    report.error(
                        f"{label}: duplicate token (same as invitation #{seen[key]}; "
                        "tokens are compared case-insensitively because they become "
                        "directory names)"
                    )
                else:
                    seen[key] = index

        _field(invitation, "greeting", "string", label, report, nonempty=True)
        flags = {
            key: _field(invitation, key, "boolean", label, report)
            for key in ("ty", "vy", "plusOne", "outOfTown")
        }
        ty, vy = flags["ty"], flags["vy"]
        if isinstance(ty, bool) and isinstance(vy, bool) and ty is vy:
            state = "true" if ty else "false"
            report.error(
                f"{label}: exactly one of 'ty' and 'vy' must be true (both are {state})"
            )
        for key in ("note", "travelNote"):
            _field(
                invitation, key, "string", label, report, required=False, nullable=True
            )
        _warn_unknown(invitation, INVITATION_FIELDS, label, report)


def check_site(site: Any, report: Report, where: str = SITE_FILE) -> None:
    """Validate the whole `site.json` document (media existence is separate)."""
    if not isinstance(site, dict):
        report.error(
            f"{where}: the top-level value must be an object, got {json_type(site)}"
        )
        return

    for key in ("coupleNames", "dateText", "rsvpDeadline"):
        _field(site, key, "string", where, report, nonempty=True)
    _field(site, "outOfTownText", "string", where, report)

    date_iso = _field(site, "dateISO", "string", where, report, nonempty=True)
    if isinstance(date_iso, str):
        try:
            parse_date_iso(date_iso)
        except ValueError as exc:
            report.error(f"{where}: field 'dateISO' {exc}")

    media_dir = _field(site, "mediaDir", "string", where, report, nonempty=True)
    if isinstance(media_dir, str) and not _MEDIA_DIR_RE.match(media_dir):
        report.error(
            f"{where}: field 'mediaDir' must be {MIN_MEDIA_DIR_LENGTH} to "
            f"{MAX_NAME_LENGTH} characters long and may only contain A-Z, a-z, 0-9, "
            f"'_' and '-' (got {len(media_dir)} characters)"
        )

    _check_video(site, report, where)
    _check_schedule(site, report, where)
    _check_venue(site, report, where)
    _warn_unknown(site, SITE_FIELDS, where, report)


def _check_video(site: dict, report: Report, where: str) -> None:
    video = _field(site, "video", "object", where, report, required=False, nullable=True)
    if not isinstance(video, dict):
        return  # absent or null: the video block is optional
    for key in VIDEO_FIELDS:
        name = _field(
            video, key, "string", where, report, prefix="video.", nonempty=True
        )
        if isinstance(name, str):
            problem = check_media_name(name, _media_extensions(f"video.{key}"))
            if problem:
                report.error(f"{where}: field 'video.{key}' {problem}")
    _warn_unknown(video, VIDEO_FIELDS, f"{where}: video", report)


def _check_schedule(site: dict, report: Report, where: str) -> None:
    schedule = _field(site, "schedule", "array", where, report)
    if not isinstance(schedule, list):
        return
    for index, entry in enumerate(schedule):
        prefix = f"schedule[{index}]."
        if not isinstance(entry, dict):
            report.error(
                f"{where}: schedule[{index}] must be an object, got {json_type(entry)}"
            )
            continue
        _field(entry, "time", "string", where, report, prefix=prefix)
        _field(entry, "title", "string", where, report, prefix=prefix, nonempty=True)
        _field(
            entry, "text", "string", where, report, prefix=prefix, required=False,
            nullable=True,
        )
        _warn_unknown(entry, SCHEDULE_FIELDS, f"{where}: schedule[{index}]", report)


def _check_venue(site: dict, report: Report, where: str) -> None:
    venue = _field(site, "venue", "object", where, report)
    if not isinstance(venue, dict):
        return
    ready = _field(venue, "ready", "boolean", where, report, prefix="venue.")
    name = _field(
        venue, "name", "string", where, report, prefix="venue.", required=False,
        nullable=True,
    )
    if ready is True and not (isinstance(name, str) and name.strip()):
        if "name" not in venue or name is None or isinstance(name, str):
            report.error(
                f"{where}: field 'venue.name' must not be empty when 'venue.ready' is true"
            )
    for key in ("description", "address"):
        _field(
            venue, key, "string", where, report, prefix="venue.", required=False,
            nullable=True,
        )

    photos = _field(
        venue, "photos", "array", where, report, prefix="venue.", required=False,
        nullable=True,
    )
    if isinstance(photos, list):
        for index, photo in enumerate(photos):
            if isinstance(photo, str) and not photo.strip():
                continue  # empty entry means "not set"
            problem = check_media_name(photo, IMAGE_EXTENSIONS)
            if problem:
                report.error(f"{where}: field 'venue.photos[{index}]' {problem}")

    directions = _field(
        venue, "directionsImage", "string", where, report, prefix="venue.",
        required=False, nullable=True,
    )
    if isinstance(directions, str) and directions.strip():
        problem = check_media_name(directions, IMAGE_EXTENSIONS)
        if problem:
            report.error(f"{where}: field 'venue.directionsImage' {problem}")

    geo = _field(
        venue, "geo", "object", where, report, prefix="venue.", required=False,
        nullable=True,
    )
    if isinstance(geo, dict):
        limits = {"lat": 90.0, "lng": 180.0}
        for key in GEO_FIELDS:
            value = _field(geo, key, "number", where, report, prefix="venue.geo.")
            if isinstance(value, (int, float)) and abs(value) > limits[key]:
                report.error(
                    f"{where}: field 'venue.geo.{key}' is outside "
                    f"[-{limits[key]:g}, {limits[key]:g}]"
                )
        _warn_unknown(geo, GEO_FIELDS, f"{where}: venue.geo", report)

    maps = _field(
        venue, "maps", "object", where, report, prefix="venue.", required=False,
        nullable=True,
    )
    if isinstance(maps, dict):
        for key in MAPS_FIELDS:
            _field(
                maps, key, "string", where, report, prefix="venue.maps.",
                required=False, nullable=True,
            )
        _warn_unknown(maps, MAPS_FIELDS, f"{where}: venue.maps", report)

    _warn_unknown(venue, VENUE_FIELDS, f"{where}: venue", report)


def media_references(site: Any) -> list[tuple[str, str]]:
    """(field path, file name) for every media file referenced by `site.json`.

    Entries with the wrong type are skipped: they are reported by `check_site`.
    """
    references: list[tuple[str, str]] = []
    if not isinstance(site, dict):
        return references
    video = site.get("video")
    if isinstance(video, dict):
        for key in VIDEO_FIELDS:
            value = video.get(key)
            if isinstance(value, str) and value.strip():
                references.append((f"video.{key}", value))
    venue = site.get("venue")
    if isinstance(venue, dict):
        photos = venue.get("photos")
        if isinstance(photos, list):
            for index, photo in enumerate(photos):
                if isinstance(photo, str) and photo.strip():
                    references.append((f"venue.photos[{index}]", photo))
        directions = venue.get("directionsImage")
        if isinstance(directions, str) and directions.strip():
            references.append(("venue.directionsImage", directions))
    return [
        (path, name)
        for path, name in references
        if check_media_name(name, _media_extensions(path)) is None
    ]


def check_media(
    site: Any, media_dir: Path | str, report: Report, where: str = SITE_FILE
) -> int:
    """Check that every referenced media file exists; returns their count."""
    references = media_references(site)
    if not references:
        return 0
    media_dir = Path(media_dir)
    listing: dict[str, os.DirEntry] = {}
    try:
        with os.scandir(media_dir) as entries:
            listing = {entry.name: entry for entry in entries}
    except FileNotFoundError:
        report.error(f"media directory not found: {display_path(media_dir)}")
    except NotADirectoryError:
        report.error(f"media path is not a directory: {display_path(media_dir)}")
    except OSError as exc:
        report.error(
            f"cannot read the media directory {display_path(media_dir)}: {exc.strerror}"
        )

    lowercase = {name.lower() for name in listing}
    for path, name in references:
        entry = listing.get(name)
        if entry is None:
            hint = (
                " (a file with a different letter case exists; names are case-sensitive)"
                if name.lower() in lowercase
                else ""
            )
            report.error(
                f"{where}: field '{path}': file '{name}' not found in "
                f"{display_path(media_dir)}{hint}"
            )
        elif entry.is_symlink():
            report.error(
                f"{where}: field '{path}': {display_path(media_dir / name)} is a "
                "symbolic link; media must be regular files"
            )
        elif not entry.is_file():
            report.error(
                f"{where}: field '{path}': '{name}' in {display_path(media_dir)} "
                "is not a regular file"
            )
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                continue  # reported when the file is copied
            if size > MAX_FILE_BYTES:
                report.error(
                    f"{where}: field '{path}': {display_path(media_dir / name)} is "
                    f"{format_size(size)}, the limit for a single file is "
                    f"{format_size(MAX_FILE_BYTES)}"
                )
    return len({name for _, name in references})


def check_media_dir_collision(
    site: Any, assets_dir: Path | str, report: Report, where: str = SITE_FILE
) -> None:
    """`mediaDir` becomes `assets/<mediaDir>/`: it must not shadow an asset.

    Names are compared without regard to letter case, because the output may
    be written to (or served from) a case-insensitive file system.
    """
    media_dir = site.get("mediaDir") if isinstance(site, dict) else None
    if not isinstance(media_dir, str) or not media_dir:
        return
    try:
        with os.scandir(assets_dir) as entries:
            names = [entry.name for entry in entries]
    except OSError:
        return  # a missing assets directory is reported by the build itself
    for name in names:
        if name.lower() == media_dir.lower():
            report.error(
                f"{where}: field 'mediaDir' collides with '{name}' in "
                f"{display_path(assets_dir)}; the media directory needs a name of its own"
            )
            return


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON data")


def read_json(path: Path, report: Report) -> Any:
    """Read a JSON document, reporting I/O and syntax problems (never values)."""
    shown = display_path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        report.error(f"{shown}: file not found")
        return MISSING
    except IsADirectoryError:
        report.error(f"{shown}: expected a file, found a directory")
        return MISSING
    except UnicodeDecodeError as exc:
        report.error(f"{shown}: not valid UTF-8 (at byte {exc.start})")
        return MISSING
    except OSError as exc:
        report.error(f"{shown}: cannot read the file ({exc.strerror})")
        return MISSING
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        report.error(
            f"{shown}: invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        )
    except ValueError as exc:
        report.error(f"{shown}: invalid JSON: {exc}")
    return MISSING


def load_data(
    data_dir: Path | str,
    media_dir: Path | str | None = None,
    report: Report | None = None,
    assets_dir: Path | str | None = None,
) -> tuple[dict, list]:
    """Load and validate `site.json` and `invitations.json`.

    Media files are checked as well when `media_dir` is given, and `mediaDir`
    is compared with the contents of `assets_dir` when that is given.  All
    problems are collected and raised together as a `ValidationError`.
    """
    report = report if report is not None else Report()
    data_path = Path(data_dir)
    if not data_path.is_dir():
        report.error(f"data directory not found: {display_path(data_path)}")
        report.raise_if_failed()

    site = read_json(data_path / SITE_FILE, report)
    invitations = read_json(data_path / INVITATIONS_FILE, report)
    if site is not MISSING:
        check_site(site, report)
        if media_dir is not None:
            report.media_files = check_media(site, media_dir, report)
        if assets_dir is not None:
            check_media_dir_collision(site, assets_dir, report)
    if invitations is not MISSING:
        check_invitations(invitations, report)
    report.raise_if_failed()
    return site, invitations


# --------------------------------------------------------------------------
# Template context
# --------------------------------------------------------------------------


def _optional_text(value: Any) -> str:
    """Optional text field -> "" when it is absent, null or blank."""
    return value if isinstance(value, str) and value.strip() else ""


def format_coordinate(value: int | float) -> str:
    """Decimal notation without an exponent: 1e-05 -> '0.00001', 10.0 -> '10'."""
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def venue_coordinates(geo: Any) -> tuple[str, str] | None:
    """(`lat`, `lng`) as text, or None when the coordinates are not set.

    `lat = 0, lng = 0` is the placeholder of the data schema, not a location.
    """
    if not isinstance(geo, dict):
        return None
    lat, lng = geo.get("lat"), geo.get("lng")
    for value, limit in ((lat, 90.0), (lng, 180.0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not _is_finite(value) or abs(value) > limit:
            return None
    if lat == 0 and lng == 0:
        return None
    return format_coordinate(lat), format_coordinate(lng)


def _url_component(text: str) -> str:
    """Percent-encode text for a URL (UTF-8; a space becomes %20)."""
    return quote(text, safe="")


def map_links(name: str, address: str, geo: Any, maps: Any) -> dict[str, str]:
    """Ready-made map URLs; "" when there is no data for a service.

    * Google: the place id when it is set (`query` is required by the URL
      format and only used as a fallback: the name, else the coordinates, else
      the address), otherwise the coordinates;
    * Yandex: the organisation id when it is set, otherwise the coordinates
      (longitude first);
    * Apple: the coordinates, labelled with the name.
    """
    maps = maps if isinstance(maps, dict) else {}
    name = name.strip()
    place_id = _optional_text(maps.get("googlePlaceId")).strip()
    org_id = _optional_text(maps.get("yandexOrgId")).strip()
    coordinates = venue_coordinates(geo)
    lat, lng = coordinates if coordinates else ("", "")

    google = yandex = apple = ""
    if place_id:
        query = (
            _url_component(name)
            if name
            else f"{lat},{lng}"
            if coordinates
            else _url_component(address.strip())
        )
        if query:
            google = (
                "https://www.google.com/maps/search/?api=1"
                f"&query={query}&query_place_id={_url_component(place_id)}"
            )
    elif coordinates:
        google = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"

    if org_id:
        yandex = f"https://yandex.ru/maps/org/{_url_component(org_id)}"
    elif coordinates:
        yandex = f"https://yandex.ru/maps/?pt={lng},{lat}&z={YANDEX_MAPS_ZOOM}"

    if coordinates:
        apple = f"https://maps.apple.com/?ll={lat},{lng}"
        if name:
            apple += f"&q={_url_component(name)}"
    return {"google": google, "yandex": yandex, "apple": apple}


def media_url(media_path: str, name: str) -> str:
    """Root-absolute URL of a media file; "" when the file is not set."""
    return f"{media_path}/{_url_component(name)}" if name else ""


def site_context(site: dict) -> dict:
    """Template-ready copy of `site.json` (the shared part of every page).

    Every optional field is normalised so that `{{field}}` inside
    `<!-- if:field -->` always resolves.  Expects data that passed validation.

    Computed fields, available to the template like any other field:

    * `mediaPath` - `/assets/<mediaDir>`; `icsPath` - the calendar file;
    * `video.src`, `video.posterSrc` - URLs of the video and its poster; `video`
      is always an object (all strings empty when the data has no video), so
      `<!-- if:video.file -->` works either way;
    * `venue.photos` - a list of `{src}` objects (`{{.src}}` inside `each`);
    * `venue.directionsSrc` - URL of the directions image or "";
    * `venue.mapLinks.google` / `.yandex` / `.apple` - see `map_links`.

    File names are percent-encoded in the URLs.
    """
    video = site.get("video")
    video = video if isinstance(video, dict) else {}
    schedule = site.get("schedule")
    venue = site.get("venue")
    venue = venue if isinstance(venue, dict) else {}
    geo = venue.get("geo")
    maps = venue.get("maps")
    maps = maps if isinstance(maps, dict) else {}
    photos = venue.get("photos")

    media_dir = _optional_text(site.get("mediaDir"))
    media_path = f"/{ASSETS_DIRNAME}/{media_dir}"
    video_file = _optional_text(video.get("file"))
    video_poster = _optional_text(video.get("poster"))
    venue_name = _optional_text(venue.get("name"))
    venue_address = _optional_text(venue.get("address"))
    directions = _optional_text(venue.get("directionsImage"))

    return {
        "coupleNames": _optional_text(site.get("coupleNames")),
        "dateISO": _optional_text(site.get("dateISO")),
        "dateText": _optional_text(site.get("dateText")),
        "rsvpDeadline": _optional_text(site.get("rsvpDeadline")),
        "outOfTownText": _optional_text(site.get("outOfTownText")),
        "mediaDir": media_dir,
        "mediaPath": media_path,
        "icsPath": f"{media_path}/{ICS_FILE}",
        "video": {
            "file": video_file,
            "poster": video_poster,
            "src": media_url(media_path, video_file),
            "posterSrc": media_url(media_path, video_poster),
        },
        "schedule": [
            {
                "time": _optional_text(entry.get("time")),
                "title": _optional_text(entry.get("title")),
                "text": _optional_text(entry.get("text")),
            }
            for entry in (schedule if isinstance(schedule, list) else [])
            if isinstance(entry, dict)
        ],
        "venue": {
            "ready": venue.get("ready") is True,
            "name": venue_name,
            "description": _optional_text(venue.get("description")),
            "address": venue_address,
            "photos": [
                {"src": media_url(media_path, photo)}
                for photo in (photos if isinstance(photos, list) else [])
                if isinstance(photo, str) and photo.strip()
            ],
            "directionsImage": directions,
            "directionsSrc": media_url(media_path, directions),
            "geo": (
                {"lat": geo.get("lat"), "lng": geo.get("lng")}
                if isinstance(geo, dict)
                else None
            ),
            "maps": {
                "googlePlaceId": _optional_text(maps.get("googlePlaceId")),
                "yandexOrgId": _optional_text(maps.get("yandexOrgId")),
            },
            "mapLinks": map_links(venue_name, venue_address, geo, maps),
        },
    }


def invitation_context(invitation: dict) -> dict:
    """Template-ready copy of one invitation (guest fields at the context root)."""
    return {
        "greeting": _optional_text(invitation.get("greeting")),
        "ty": invitation.get("ty") is True,
        "vy": invitation.get("vy") is True,
        "plusOne": invitation.get("plusOne") is True,
        "outOfTown": invitation.get("outOfTown") is True,
        "note": _optional_text(invitation.get("note")),
        "travelNote": _optional_text(invitation.get("travelNote")),
    }


def build_context(site: dict, invitation: dict) -> dict:
    """Full template context for one page: site fields + invitation fields.

    A fresh context is built for every page, so per-page computed fields added
    by later build steps cannot leak between invitations.
    """
    context = site_context(site)
    context.update(invitation_context(invitation))
    return context


# --------------------------------------------------------------------------
# Calendar file
# --------------------------------------------------------------------------

_ICS_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def ics_escape(text: str) -> str:
    """Escape a TEXT value (RFC 5545, 3.3.11): `\\`, `;`, `,` and line breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ICS_CONTROL_RE.sub(" ", text)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def ics_fold(line: str, limit: int = ICS_LINE_OCTETS) -> str:
    """Fold a content line longer than `limit` octets (RFC 5545, 3.1).

    Continuation lines start with a space, which counts towards the limit.  A
    line is only ever split between characters, never inside a UTF-8 sequence.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for char in line:
        octets = len(char.encode("utf-8"))
        if size + octets > limit and current:
            chunks.append("".join(current))
            current, size = [" "], 1
        current.append(char)
        size += octets
    chunks.append("".join(current))
    return "\r\n".join(chunks)


def _ics_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(site: dict) -> bytes:
    """The calendar file: one event in UTC, without names or guest data.

    The result depends on the data only (no clock, no random values), so equal
    inputs give identical bytes.  The location is included once the venue is
    announced (`venue.ready`).
    """
    date_iso = _optional_text(site.get("dateISO"))
    try:
        start = parse_date_iso(date_iso)
    except ValueError as exc:  # pragma: no cover - the data is validated first
        raise BuildError(f"{SITE_FILE}: field 'dateISO' {exc}") from None
    media_dir = _optional_text(site.get("mediaDir"))
    uid = hashlib.sha256(f"{media_dir}\n{date_iso}".encode("utf-8")).hexdigest()[:32]

    venue = site.get("venue")
    venue = venue if isinstance(venue, dict) else {}
    location = ""
    if venue.get("ready") is True:
        parts = (_optional_text(venue.get(key)).strip() for key in ("name", "address"))
        location = ", ".join(part for part in parts if part)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}@{ICS_UID_DOMAIN}",
        f"DTSTAMP:{ICS_DTSTAMP}",
        f"DTSTART:{_ics_timestamp(start)}",
        f"DTEND:{_ics_timestamp(start + ICS_DEFAULT_DURATION)}",
        f"SUMMARY:{ics_escape(ICS_SUMMARY)}",
    ]
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(ics_fold(line) + "\r\n" for line in lines).encode("utf-8")


# --------------------------------------------------------------------------
# HTML comments
# --------------------------------------------------------------------------

#: What may follow the text of a comment: `-->`, `--!>`, or the `>` / `->` of an
#: abruptly closed `<!-->` / `<!--->`.
_COMMENT_END_RE = re.compile(r"-{0,2}!?\s*>")


class _CommentLocator(HTMLParser):
    """Finds the HTML comments of a document (never the `<!--` inside scripts,
    styles or attribute values, which is why this is not a regular expression).
    """

    def __init__(self, document: str):
        super().__init__(convert_charrefs=False)
        self.document = document
        self.spans: list[tuple[int, int]] = []
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", document)]

    def handle_comment(self, data: str) -> None:
        line, column = self.getpos()
        start = self._line_starts[line - 1] + column
        document = self.document
        if document.startswith("<!--", start):
            end = start + 4 + len(data)
            closing = _COMMENT_END_RE.match(document, end)
            end = closing.end() if closing else len(document)
        else:  # a "bogus comment" such as <!x> or </ x>: it ends at the next '>'
            closing_at = document.find(">", start)
            end = closing_at + 1 if closing_at >= 0 else len(document)
        self.spans.append((start, end))


def strip_html_comments(document: str) -> str:
    """Remove every HTML comment, so that template notes are never published."""
    if "<!" not in document and "</" not in document:
        return document
    locator = _CommentLocator(document)
    locator.feed(document)
    locator.close()
    if not locator.spans:
        return document
    parts: list[str] = []
    position = 0
    for start, end in locator.spans:
        if start < position:  # pragma: no cover - spans never overlap
            continue
        parts.append(document[position:start])
        position = end
    parts.append(document[position:])
    return "".join(parts)


# --------------------------------------------------------------------------
# Checks on the finished output
# --------------------------------------------------------------------------


class OutputError(ValidationError):
    """The finished output failed one or more checks (all of them are listed)."""


class OutputStats(NamedTuple):
    """What `check_output` counted."""

    files: int
    size: int


#: Attributes whose value is a URL that the browser fetches (or submits to).
_URL_ATTRIBUTES = frozenset(
    {
        "src",
        "poster",
        "href",
        "xlink:href",
        "action",
        "formaction",
        "background",
        "manifest",
        "ping",
    }
)
_SRCSET_ATTRIBUTES = frozenset({"srcset", "imagesrcset"})
#: Elements that embed foreign documents (or redirect every URL of the page).
_FORBIDDEN_ELEMENTS = frozenset(
    {"iframe", "frame", "frameset", "object", "embed", "applet", "base"}
)
_LINK_ELEMENTS = frozenset({"a", "area"})
#: `<meta>` entries whose `content` is a URL.
_META_URL_KEYS = frozenset(
    {
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "og:url",
        "og:video",
        "og:video:url",
        "og:audio",
        "twitter:image",
        "twitter:image:src",
        "twitter:player",
        "msapplication-tileimage",
    }
)
_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_EXTERNAL_TEXT_RE = re.compile(r"(?:https?:)?//[A-Za-z0-9\[]", re.IGNORECASE)
_URL_WHITESPACE_RE = re.compile(r"[\t\n\r]")
_URL_TRIM = "".join(map(chr, range(33)))  # C0 control characters and space
#: URLs made of these characters only are quoted in messages; anything else may
#: be text that a template put into a URL by mistake, and is not echoed.
_PLAIN_URL_RE = re.compile(r"[A-Za-z0-9/._~%?=&#+,:;@-]*\Z")
_HTML_SPACE = " \t\n\f\r"


def _clean_url(value: str) -> str:
    """The URL as a browser reads it: trimmed, without tabs and line breaks."""
    return _URL_WHITESPACE_RE.sub("", value.strip(_URL_TRIM))


def _show_url(url: str) -> str:
    """A URL for an error message: external URLs are cut down to the host (the
    rest may carry data), everything else is shortened."""
    url = _clean_url(url)
    if url.lower().startswith("data:"):
        return _shorten(re.split(r"[;,]", url, maxsplit=1)[0], 40) + ",…"
    scheme = _SCHEME_RE.match(url)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return f"{scheme.group(1).lower()}:…"  # e.g. mailto: - the rest is data
    if scheme or url.replace("\\", "/").startswith("//"):
        head = re.match(r"[^/\\?#]*[/\\]{0,2}[^/\\?#]*", url)
        shown = head.group(0) if head else url
        if not _PLAIN_URL_RE.match(shown):
            return f"{scheme.group(0) if scheme else ''}//…"
        return _shorten(shown, 60) + ("/…" if len(shown) < len(url) else "")
    return _shorten(url, 80) if _PLAIN_URL_RE.match(url) else "…"


def _classify_url(value: str) -> tuple[str, str]:
    """(kind, cleaned URL); kind is one of `empty`, `fragment`, `local`,
    `relative`, `external`, `data`, `script`, `scheme` or `malformed`."""
    url = _clean_url(value)
    if not url:
        return "empty", url
    if url.startswith("#"):
        return "fragment", url
    scheme = _SCHEME_RE.match(url)
    if scheme:
        name = scheme.group(1).lower()
        if name in ("http", "https"):
            return "external", url
        if name == "data":
            return "data", url
        if name in ("javascript", "vbscript"):
            return "script", url
        return "scheme", url
    if "\\" in url:
        return "malformed", url  # browsers read '\' as '/': "/\host" is external
    if url.startswith("//"):
        return "external", url
    if url.startswith("/"):
        return "local", url
    return "relative", url


def _local_file(url: str) -> str | None:
    """Output file that a root-absolute URL points at; None if it is not plain."""
    path = unquote(url.split("#", 1)[0].split("?", 1)[0])
    if path.endswith("/"):
        path += PAGE_FILE
    segments = path.split("/")[1:]
    if any(segment in ("", ".", "..") for segment in segments) or "\\" in path:
        return None
    return "/".join(segments)


def _data_url_type(url: str) -> str:
    return re.split(r"[;,]", url[5:], maxsplit=1)[0].strip().lower()


def _is_map_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    host = parts.netloc.lower()  # with user info or a port it is not the host
    return any(
        host == allowed and parts.path.startswith(prefix)
        for allowed, prefix in MAP_LINK_TARGETS
    )


def _srcset_urls(value: str) -> list[str]:
    """The URLs of a `srcset` (a URL may contain commas, e.g. `data:` URLs)."""
    urls: list[str] = []
    position, length = 0, len(value)
    while position < length:
        while position < length and (
            value[position] in _HTML_SPACE or value[position] == ","
        ):
            position += 1
        start = position
        while position < length and value[position] not in _HTML_SPACE:
            position += 1
        url = value[start:position]
        if url.endswith(","):
            url = url.rstrip(",")
        else:  # skip the descriptors up to the next candidate
            depth = 0
            while position < length:
                char = value[position]
                position += 1
                if char == "(":
                    depth += 1
                elif char == ")" and depth:
                    depth -= 1
                elif char == "," and not depth:
                    break
        if url:
            urls.append(url)
    return urls


class _UrlPolicy:
    """Shared URL rules of the HTML and CSS checks; collects (line, problem)."""

    def __init__(self, exists: Callable[[str], bool]):
        self.exists = exists
        self.problems: list[tuple[int, str]] = []

    def problem(self, line: int, message: str) -> None:
        self.problems.append((line, message))

    def _common(self, line: int, where: str, kind: str, url: str, what: str) -> bool:
        """Rules shared by resources and links; True when the URL is settled."""
        if kind == "fragment":
            return True
        if kind == "local":
            target = _local_file(url)
            if target is None:
                self.problem(
                    line, f"{where}: '{_show_url(url)}' is not a plain absolute path"
                )
            elif not self.exists(target):
                self.problem(
                    line, f"{where}: '{_show_url(url)}' does not exist in the output"
                )
        elif kind == "empty":
            self.problem(line, f"{where}: the URL is empty")
        elif kind == "relative":
            self.problem(
                line,
                f"{where}: relative path '{_show_url(url)}'; {what} must be absolute "
                "from the site root (start with '/')",
            )
        elif kind == "script":
            self.problem(line, f"{where}: script URL '{_show_url(url)}' is not allowed")
        else:
            return False
        return True

    def check_resource(
        self,
        line: int,
        where: str,
        value: str,
        *,
        data_images: bool = True,
        empty_data: bool = False,
    ) -> None:
        """A URL that is fetched when the page loads: local files only."""
        kind, url = _classify_url(value)
        if self._common(line, where, kind, url, "resource paths"):
            return
        if kind == "external":
            self.problem(
                line,
                f"{where}: external URL '{_show_url(url)}'; every resource must be "
                "a local file",
            )
        elif kind == "data":
            allowed = data_images and _data_url_type(url).startswith("image/")
            if empty_data and url.lower() == "data:,":
                allowed = True
            if not allowed:
                self.problem(
                    line,
                    f"{where}: '{_show_url(url)}' - data: URLs are only allowed "
                    "for images and icons",
                )
        else:
            self.problem(line, f"{where}: URL '{_show_url(url)}' is not allowed")

    def check_link(self, line: int, where: str, value: str, attrs: dict) -> None:
        """`<a href>`: a local page, a fragment, or a link to a map service."""
        kind, url = _classify_url(value)
        if self._common(line, where, kind, url, "links"):
            return
        if kind == "external" and _is_map_link(url):
            if (attrs.get("target") or "").strip().lower() != "_blank":
                self.problem(
                    line,
                    f"{where}: external link '{_show_url(url)}' needs target=\"_blank\"",
                )
            rel = set((attrs.get("rel") or "").lower().split())
            if not {"noopener", "noreferrer"} <= rel:
                self.problem(
                    line,
                    f"{where}: external link '{_show_url(url)}' needs "
                    'rel="noopener noreferrer"',
                )
        elif kind == "external":
            self.problem(
                line,
                f"{where}: external link '{_show_url(url)}'; only https links to the "
                "map services are allowed",
            )
        else:  # mailto:, tel:, data: and other schemes
            self.problem(line, f"{where}: link '{_show_url(url)}' is not allowed")


_CSS_STRING = r'"(?:[^"\\\n]|\\.)*"' + "|" + r"'(?:[^'\\\n]|\\.)*'"
_CSS_COMMENT_OR_STRING_RE = re.compile(rf"/\*.*?(?:\*/|\Z)|{_CSS_STRING}", re.DOTALL)
_CSS_NAMESPACE_RE = re.compile(r"@namespace\b[^;{}]*;?", re.IGNORECASE)
_CSS_URL_RE = re.compile(
    rf"(?<![\w-])url\(\s*({_CSS_STRING}|[^)]*?)\s*(?:\)|\Z)", re.IGNORECASE
)
_CSS_IMPORT_RE = re.compile(rf"@import\s*({_CSS_STRING})", re.IGNORECASE)
_CSS_IMPORT_BEFORE_RE = re.compile(r"@import\s*\Z", re.IGNORECASE)
_CSS_STRING_RE = re.compile(_CSS_STRING)


def _blank(match: re.Match) -> str:
    """Replacement that keeps the line numbers of the rest of the text."""
    return "\n" * match.group(0).count("\n") or " "


def strip_css_comments(text: str) -> str:
    """CSS without comments (strings are left alone, line numbers are kept)."""
    return _CSS_COMMENT_OR_STRING_RE.sub(
        lambda match: _blank(match) if match.group(0).startswith("/*") else match.group(0),
        text,
    )


def _css_string_value(token: str) -> str:
    return token[1:-1] if len(token) >= 2 and token[0] in "\"'" else token


def check_css(
    text: str, policy: _UrlPolicy, where: str = "CSS", first_line: int = 1
) -> None:
    """`url()`, `@import` and URL-like strings of a style sheet (or of a
    `<style>` element / `style` attribute) must point at local files."""
    text = _CSS_NAMESPACE_RE.sub(_blank, strip_css_comments(text))

    def line_at(offset: int) -> int:
        return first_line + text.count("\n", 0, offset)

    covered: list[tuple[int, int]] = []
    for match in _CSS_IMPORT_RE.finditer(text):
        covered.append(match.span())
        policy.check_resource(
            line_at(match.start()),
            f"{where} @import",
            _css_string_value(match.group(1)),
            data_images=False,
        )
    for match in _CSS_URL_RE.finditer(text):
        covered.append(match.span())
        before = text[max(0, match.start() - 40) : match.start()]
        is_import = _CSS_IMPORT_BEFORE_RE.search(before) is not None
        policy.check_resource(
            line_at(match.start()),
            f"{where} {'@import' if is_import else 'url()'}",
            _css_string_value(match.group(1)),
            data_images=not is_import,
        )
    for match in _CSS_STRING_RE.finditer(text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        value = _css_string_value(match.group(0))
        if _EXTERNAL_TEXT_RE.match(value.strip()):
            policy.problem(
                line_at(match.start()),
                f"{where}: external URL '{_show_url(value)}' in a string; every "
                "resource must be a local file",
            )


class _HtmlChecker(HTMLParser):
    """Walks one HTML document and applies the URL and script rules."""

    def __init__(self, policy: _UrlPolicy):
        super().__init__(convert_charrefs=True)
        self.policy = policy
        self._style: list[str] | None = None
        self._style_line = 1

    @property
    def line(self) -> int:
        return self.getpos()[0]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        policy, line = self.policy, self.line
        values: dict[str, str] = {}
        for name, value in attrs:  # a repeated attribute: the first one wins
            values.setdefault(name, value or "")
        if tag in _FORBIDDEN_ELEMENTS:
            policy.problem(line, f"<{tag}> is not allowed")
            return
        if tag == "script" and not values.get("src", "").strip():
            policy.problem(
                line, "inline <script> is not allowed; scripts must be files in /assets/"
            )
        if tag == "style":
            self._style, self._style_line = [], line

        for name, value in values.items():
            where = f"<{tag} {name}>"
            if name.startswith("on") and len(name) > 2:
                policy.problem(line, f"{where}: inline event handlers are not allowed")
            elif name in _SRCSET_ATTRIBUTES:
                urls = _srcset_urls(value)
                if not urls:
                    policy.problem(line, f"{where}: the URL is empty")
                for url in urls:
                    policy.check_resource(line, where, url)
            elif name in _URL_ATTRIBUTES:
                if tag in _LINK_ELEMENTS and name == "href":
                    policy.check_link(line, where, value, values)
                else:
                    rel = values.get("rel", "").lower().split()
                    is_icon = tag == "link" and "icon" in rel
                    policy.check_resource(
                        line,
                        where,
                        value,
                        data_images=is_icon or tag not in ("script", "a", "area", "link"),
                        empty_data=is_icon,
                    )
            elif name == "style":
                check_css(value, policy, f"<{tag} style>", line)

        if tag == "meta":
            self._check_meta(values, line)

    def _check_meta(self, values: dict, line: int) -> None:
        content = values.get("content", "")
        key = (values.get("property") or values.get("name") or "").strip().lower()
        if key in _META_URL_KEYS:
            self.policy.check_resource(line, f"<meta {key}>", content)
            return
        external = _EXTERNAL_TEXT_RE.search(content)
        if external:
            shown = _show_url(content[external.start() :].split()[0])
            self.policy.problem(
                line, f"<meta content>: external URL '{shown}' is not allowed"
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.finish_style()

    def finish_style(self) -> None:
        if self._style is not None:
            check_css("".join(self._style), self.policy, "<style>", self._style_line)
            self._style = None

    def handle_data(self, data: str) -> None:
        if self._style is not None:
            self._style.append(data)
        elif "<!--" in data:
            self.policy.problem(self.line, "unterminated HTML comment")

    def handle_comment(self, data: str) -> None:
        self.policy.problem(self.line, "HTML comment left in the output")


def check_html(document: str, exists: Callable[[str], bool]) -> list[tuple[int, str]]:
    """Problems of one HTML document as (line, message) pairs.

    `exists` tells whether a file (relative path with '/') is in the output.
    """
    policy = _UrlPolicy(exists)
    leftover = _LEFTOVER_RE.search(document)
    if leftover:
        policy.problem(
            document.count("\n", 0, leftover.start()) + 1,
            f"template syntax left in the output: '{_shorten(leftover.group(0), 20)}'",
        )
    checker = _HtmlChecker(policy)
    checker.feed(document)
    checker.close()
    checker.finish_style()  # an unclosed <style> is checked as well
    return policy.problems


def check_stylesheet(text: str, exists: Callable[[str], bool]) -> list[tuple[int, str]]:
    """Problems of one CSS file as (line, message) pairs."""
    policy = _UrlPolicy(exists)
    check_css(text, policy)
    return policy.problems


#: At most this many problems are listed (a problem that repeats on every page
#: is reported once).
MAX_REPORTED_PROBLEMS = 25


def _scan_output(root: Path) -> tuple[dict[str, int], list[str], list[str]]:
    """(files -> size, directories, problems) of an output tree; paths are
    relative and use '/'.  Symbolic links are reported, never followed."""
    files: dict[str, int] = {}
    directories: list[str] = []
    problems: list[str] = []

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            relative = f"{prefix}{entry.name}"
            if entry.is_symlink():
                problems.append(f"{relative}: symbolic links are not allowed in the output")
            elif entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                walk(Path(entry.path), f"{relative}/")
            elif entry.is_file(follow_symlinks=False):
                files[relative] = entry.stat(follow_symlinks=False).st_size
            else:
                problems.append(f"{relative}: not a regular file")

    walk(root, "")
    return files, directories, problems


def _check_output_tree(
    files: dict[str, int],
    directories: Sequence[str],
    tokens: Sequence[str],
    media_dir: str,
) -> list[str]:
    """Allow-list of the output: only what the site needs is published."""
    problems: list[str] = []
    expected_pages = {f"{PAGES_DIRNAME}/{token}/{PAGE_FILE}" for token in tokens}
    expected_dirs = {f"{PAGES_DIRNAME}/{token}" for token in tokens}

    for relative in directories:
        parts = relative.split("/")
        if parts[-1].startswith("."):
            problems.append(f"{relative}: dot-directories must not be published")
        elif len(parts) == 1:
            if relative not in OUTPUT_TOP_LEVEL_DIRS:
                problems.append(
                    f"{relative}: unexpected directory at the top of the output"
                )
        elif parts[0] == PAGES_DIRNAME and relative not in expected_dirs:
            problems.append(
                f"{relative}: unexpected directory (only one directory per invitation "
                f"is allowed in {PAGES_DIRNAME}/)"
            )

    for relative, size in files.items():
        parts = relative.split("/")
        name = parts[-1]
        extension = os.path.splitext(name)[1].lower()
        if name.startswith("."):
            problems.append(f"{relative}: dot-files must not be published")
        elif extension in FORBIDDEN_OUTPUT_EXTENSIONS:
            problems.append(f"{relative}: '{extension}' files must not be published")
        elif len(parts) == 1:
            if relative not in OUTPUT_TOP_LEVEL or relative in OUTPUT_TOP_LEVEL_DIRS:
                problems.append(f"{relative}: unexpected file at the top of the output")
        elif parts[0] == PAGES_DIRNAME:
            if relative not in expected_pages:
                problems.append(
                    f"{relative}: unexpected file (only <token>/{PAGE_FILE} is allowed "
                    f"in {PAGES_DIRNAME}/)"
                )
        elif parts[0] == ASSETS_DIRNAME:
            if extension not in ASSET_EXTENSIONS:
                problems.append(
                    f"{relative}: file type '{extension or name}' is not allowed in "
                    f"{ASSETS_DIRNAME}/ (allowed: {_extension_list(ASSET_EXTENSIONS)})"
                )
        else:
            problems.append(f"{relative}: unexpected file")
        if size > MAX_FILE_BYTES:
            problems.append(
                f"{relative}: {format_size(size)} is larger than the limit of "
                f"{format_size(MAX_FILE_BYTES)} for a single file"
            )

    required = [*STUB_OUTPUTS, HEADERS_FILE, ROBOTS_FILE, *sorted(expected_pages)]
    if media_dir:
        required.append(f"{ASSETS_DIRNAME}/{media_dir}/{ICS_FILE}")
    for relative in required:
        if relative not in files:
            problems.append(f"{relative}: missing from the output")
    return problems


def check_output(
    out_dir: Path | str, tokens: Sequence[str], media_dir: str = ""
) -> OutputStats:
    """Check a finished output directory; raises `OutputError` with every problem.

    * only the expected files are present (see `_check_output_tree`), none of
      them larger than `MAX_FILE_BYTES`;
    * HTML: no template syntax or comments left, no external resources, no
      inline scripts or event handlers, no embedded documents; external links
      only to the map services and with `target`/`rel`; every local URL
      resolves to a file of the output;
    * CSS: `url()` and `@import` point at local files that exist.

    Messages never quote page text, and tokens in paths are shortened.
    """
    root = Path(out_dir)
    try:
        files, directories, problems = _scan_output(root)
    except OSError as exc:
        raise BuildError(
            f"cannot read the output directory {display_path(root)}: {exc.strerror}"
        ) from None
    problems += _check_output_tree(files, directories, tokens, media_dir)
    problems = [_redact_paths(problem) for problem in problems]

    # problem -> [(file, line)], in order of appearance
    found: dict[str, list[tuple[str, int]]] = {}
    for relative, size in files.items():
        extension = os.path.splitext(relative)[1].lower()
        if extension not in (".html", ".css") or size > MAX_FILE_BYTES:
            continue
        shown = _redact_paths(relative)
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{shown}: not valid UTF-8 (at byte {exc.start})")
            continue
        except OSError as exc:
            raise BuildError(f"cannot read {shown}: {exc.strerror}") from None
        check = check_html if extension == ".html" else check_stylesheet
        for line, message in check(text, files.__contains__):
            found.setdefault(_redact_paths(message), []).append((shown, line))

    for message, places in found.items():
        first_file, first_line = places[0]
        distinct = len({name for name, _line in places})
        if distinct == 1:
            problems.append(f"{first_file} line {first_line}: {message}")
        else:
            problems.append(
                f"{distinct} files: {message}; first: {first_file} line {first_line}"
            )

    if problems:
        hidden = len(problems) - MAX_REPORTED_PROBLEMS
        if hidden > 0:
            problems = problems[:MAX_REPORTED_PROBLEMS] + [
                f"… and {hidden} more problem(s) in the output"
            ]
        raise OutputError(problems)
    return OutputStats(files=len(files), size=sum(files.values()))


# --------------------------------------------------------------------------
# Output directory handling
# --------------------------------------------------------------------------


def display_path(path: Path | str) -> str:
    """Path for logs: relative to the working directory when possible."""
    path = Path(path)
    try:
        return str(path.absolute().relative_to(Path.cwd())) or "."
    except ValueError:
        return str(path)


#: `i/<token>`: both are whole path components, `i` may start the path, and a
#: name with a dot (`i/index.html`) is a file, not a token.
_TOKEN_IN_PATH_RE = re.compile(
    rf"(?<![A-Za-z0-9_.-])({PAGES_DIRNAME}[/\\])([A-Za-z0-9_-]{{4}})[A-Za-z0-9_-]+"
    r"(?![A-Za-z0-9_.-])"
)
#: A name that may be an invitation token or a media directory.
_SECRET_LIKE_RE = re.compile(rf"[A-Za-z0-9_-]{{{MIN_MEDIA_DIR_LENGTH},}}\Z")


def _redact_paths(text: Any) -> str:
    """Hide invitation tokens that may appear inside file system paths."""
    return _TOKEN_IN_PATH_RE.sub(r"\1\2…", str(text))


def _mask_name(name: str) -> str:
    """A directory entry for an error message: names that look like a token or
    a media directory are cut down to their first four characters."""
    if _SECRET_LIKE_RE.match(name):
        return f"{name[:4]}…"
    return _shorten(name, 40)


def _same_path(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return first == second


def _is_within(path: Path, ancestor: Path) -> bool:
    """True when `path` is `ancestor` or lies inside it (both already resolved)."""
    return any(_same_path(candidate, ancestor) for candidate in (path, *path.parents))


def check_out_dir(
    out_dir: Path | str,
    *,
    code_dir: Path | str,
    data_dir: Path | str,
    media_dir: Path | str,
) -> None:
    """Refuse output directories whose replacement would destroy anything.

    The build replaces `--out` completely, so it must not be the code, data or
    media directory (or a parent of one), the working directory (or a parent of
    it), the home directory, the file system root, or a directory inside the
    trees that are copied into the output.  An existing directory is replaced
    only when it is empty or looks like the output of a previous build.

    Extension point: later build steps may pass further protected directories
    through the same checks.
    """
    out = Path(out_dir).absolute()
    resolved = Path(os.path.realpath(out))
    shown = display_path(out)

    if resolved == Path(resolved.anchor):
        raise BuildError("--out must not be the file system root")
    try:
        home = Path(os.path.realpath(Path.home()))
    except (RuntimeError, OSError):  # pragma: no cover - home is normally defined
        home = None
    if home is not None and _same_path(resolved, home):
        raise BuildError("--out must not be the home directory")
    if out.exists() and not out.is_dir():
        raise BuildError(f"--out exists and is not a directory: {shown}")

    protected = {
        "code": Path(code_dir),
        "data": Path(data_dir),
        "media": Path(media_dir),
        "working": Path.cwd(),
    }
    for label, path in protected.items():
        target = Path(os.path.realpath(path.absolute()))
        for candidate in (target, *target.parents):
            if _same_path(resolved, candidate):
                relation = "the" if candidate == target else "a parent of the"
                raise BuildError(
                    f"--out must not be {relation} {label} directory "
                    f"({shown}); choose a separate output directory"
                )

    inside = {
        "media": Path(media_dir),
        "assets": Path(code_dir) / ASSETS_DIRNAME,
    }
    for label, path in inside.items():
        target = Path(os.path.realpath(path.absolute()))
        if _is_within(resolved, target):
            relation = "the" if _same_path(resolved, target) else "inside the"
            raise BuildError(
                f"--out must not be {relation} {label} directory ({shown}); "
                "choose a separate output directory"
            )

    if out.is_symlink():
        raise BuildError(
            f"--out must not be a symbolic link ({shown}); "
            "point --out at the real directory"
        )
    check_replaceable(out)


def check_replaceable(out_dir: Path | str) -> None:
    """Allow replacing an existing directory only if it is a previous build.

    A directory qualifies when it is empty or when every top-level entry is one
    of `OUTPUT_TOP_LEVEL` and has the expected type: `i` and `assets` are real
    directories, everything else is a regular file, nothing is a symbolic link
    (files created by the operating system are ignored).  No marker file is
    written into the output: its contents stay limited to what the site needs.

    The message never shows a name in full if it looks like a token or a media
    directory (`--out` may point inside a previous build by mistake).
    """
    out = Path(out_dir)
    try:
        with os.scandir(out) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BuildError(
            f"cannot read the output directory {display_path(out)}: {exc.strerror}"
        ) from None
    entries = [entry for entry in entries if entry.name not in OS_JUNK_FILES]
    advice = "remove it manually or choose another --out"
    unexpected = [entry.name for entry in entries if entry.name not in OUTPUT_TOP_LEVEL]
    if unexpected:
        listed = ", ".join(f"'{_mask_name(name)}'" for name in unexpected[:3])
        more = f" and {len(unexpected) - 3} more" if len(unexpected) > 3 else ""
        raise BuildError(
            f"refusing to replace {display_path(out)}: it does not look like a "
            f"previous build output (unexpected: {listed}{more}); {advice}"
        )
    for entry in entries:
        is_directory = entry.name in OUTPUT_TOP_LEVEL_DIRS
        matches = (
            entry.is_dir(follow_symlinks=False)
            if is_directory
            else entry.is_file(follow_symlinks=False)
        )
        if not matches:
            expected = "a directory" if is_directory else "a regular file"
            raise BuildError(
                f"refusing to replace {display_path(out)}: it does not look like a "
                f"previous build output ('{entry.name}' is not {expected}); {advice}"
            )


def _remove_tree(path: Path) -> bool:
    """Remove a directory tree (or a symlink); True when nothing is left."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
    return not os.path.lexists(path)


class _StagedOutput:
    """Build into a temporary directory next to `--out`, then swap it in.

    The temporary names are `.<name>.tmp-<hex>` (the new output) and
    `.<name>.old-<hex>` (the previous one while it is being replaced).  They
    may hold rendered pages, so leftovers of an interrupted build are removed
    before the next one starts, and a failure to remove them is reported.
    """

    def __init__(self, out_dir: Path, warn: Callable[[str], None] | None = None):
        self.out = Path(out_dir).absolute()
        self.warn = warn if warn is not None else (lambda _message: None)
        self.stage = self.out.parent / f".{self.out.name}.tmp-{secrets.token_hex(6)}"
        self._stale_re = re.compile(
            rf"\.{re.escape(self.out.name)}\.(?:tmp|old)-[0-9a-f]{{12}}\Z"
        )

    def __enter__(self) -> Path:
        try:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self._remove_stale()
            self.stage.mkdir()
        except OSError as exc:
            raise BuildError(
                "cannot create a temporary directory next to "
                f"{display_path(self.out)}: {exc.strerror}"
            ) from None
        return self.stage

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._discard(self.stage)
            return False
        self._swap()
        return False

    def _remove_stale(self) -> None:
        """Remove what an interrupted build left next to the output directory."""
        with os.scandir(self.out.parent) as entries:
            stale = [Path(entry.path) for entry in entries if self._stale_re.match(entry.name)]
        for path in stale:
            self._discard(path)

    def _discard(self, path: Path) -> None:
        if not _remove_tree(path):
            self.warn(
                f"could not remove the temporary directory {display_path(path)}; "
                "it may contain rendered pages, remove it manually"
            )

    def _swap(self) -> None:
        previous = self.out.parent / f".{self.out.name}.old-{secrets.token_hex(6)}"
        try:
            if os.path.lexists(self.out):
                os.rename(self.out, previous)
            os.rename(self.stage, self.out)
        except OSError as exc:
            hint = ""
            if os.path.lexists(previous) and not os.path.lexists(self.out):
                try:  # put the previous output back
                    os.rename(previous, self.out)
                except OSError:
                    hint = f"; the previous output was kept in {display_path(previous)}"
            self._discard(self.stage)
            raise BuildError(
                f"cannot replace the output directory {display_path(self.out)}: "
                f"{exc.strerror}{hint}"
            ) from None
        if os.path.lexists(previous):
            self._discard(previous)


def is_styleguide_asset(relative: str) -> bool:
    """`styleguide.*` files document the design system; they are not published."""
    return relative.rsplit("/", 1)[-1].lower().startswith(STYLEGUIDE_PREFIX)


def copy_assets(
    source: Path,
    destination: Path,
    ignore: Callable[[str], bool] | None = None,
) -> int:
    """Copy `assets/` into the output; returns the number of files.

    Dot-files and dot-directories are skipped.  `ignore` receives the path of a
    file or directory relative to `source` (with '/') and returns True for
    entries that must not reach the output.  Symbolic links are an error (they
    are never followed), and directories that end up empty are not created.
    """
    source = Path(source)
    if source.is_symlink():
        raise BuildError(
            f"the assets directory must not be a symbolic link: {display_path(source)}"
        )
    if not source.is_dir():
        raise BuildError(f"assets directory not found: {display_path(source)}")

    def skip(directory: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, source).replace(os.sep, "/")
            if name.startswith(".") or (ignore is not None and ignore(relative)):
                skipped.add(name)
                continue
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise BuildError(
                    f"symbolic links are not allowed in assets: {display_path(path)}"
                )
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise BuildError(f"not a regular file in assets: {display_path(path)}")
        return skipped

    shown = display_path(source)
    try:
        shutil.copytree(source, destination, ignore=skip)
        for directory, _dirs, _files in os.walk(destination, topdown=False):
            if Path(directory) != Path(destination) and not os.listdir(directory):
                os.rmdir(directory)
    except shutil.Error as exc:  # a list of (source, destination, reason)
        failures = exc.args[0] if exc.args and isinstance(exc.args[0], list) else []
        detail = f"{len(failures)} file(s) could not be copied"
        if failures:
            failed, _target, reason = failures[0]
            detail += f"; first: {display_path(failed)}: {reason}"
        raise BuildError(f"cannot copy assets from {shown}: {detail}") from None
    except OSError as exc:
        where = f" ({display_path(exc.filename)})" if exc.filename else ""
        raise BuildError(
            f"cannot copy assets from {shown}: {exc.strerror or type(exc).__name__}{where}"
        ) from None
    return sum(len(files) for _root, _dirs, files in os.walk(destination))


def copy_media(site: dict, media_dir: Path | str, destination: Path) -> int:
    """Copy the media files the data refers to - and nothing else - into
    `assets/<mediaDir>/`; returns the number of files."""
    names = sorted({name for _field, name in media_references(site)})
    try:
        destination.mkdir(parents=True)
    except FileExistsError:
        raise BuildError(
            f"{SITE_FILE}: field 'mediaDir' collides with an entry of "
            f"{ASSETS_DIRNAME}/; the media directory needs a name of its own"
        ) from None
    except OSError as exc:
        raise BuildError(f"cannot create the media directory: {exc.strerror}") from None
    for name in names:
        source = Path(media_dir) / name
        if source.is_symlink():
            raise BuildError(
                f"symbolic links are not allowed in media: {display_path(source)}"
            )
        try:
            shutil.copyfile(source, destination / name)
        except OSError as exc:
            raise BuildError(
                f"cannot copy the media file {display_path(source)}: "
                f"{exc.strerror or type(exc).__name__}"
            ) from None
    return len(names)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def _read_source(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise BuildError(f"{what} not found: {display_path(path)}") from None
    except UnicodeDecodeError as exc:
        raise BuildError(
            f"{display_path(path)}: not valid UTF-8 (at byte {exc.start})"
        ) from None
    except OSError as exc:
        raise BuildError(
            f"cannot read the {what} {display_path(path)}: {exc.strerror}"
        ) from None


def load_template(path: Path) -> Template:
    """Read and parse the page template."""
    return parse_template(_read_source(path, "template"), path.name)


def load_stub(path: Path) -> str:
    """Read the stub page (published as `index.html` and `404.html`).

    The stub is the same for everybody and gets no data at all, so template
    syntax in it is an error rather than something to render.  HTML comments
    are removed, as on the pages.
    """
    source = _read_source(path, "stub page")
    check_rendered(
        source,
        path.name,
        problem="must not contain template syntax (the stub gets no data)",
    )
    return strip_html_comments(source)


def render_pages(
    template: Template, site: dict, invitations: Sequence[dict]
) -> list[tuple[str, str]]:
    """Render every invitation; returns (token, html) pairs.

    Rendering happens entirely in memory: a broken template must not leave a
    half-written output directory behind.  HTML comments are removed from the
    result, so that notes in the template are never published.
    """
    pages: list[tuple[str, str]] = []
    failures: dict[str, list[str]] = {}  # message -> labels, in order of appearance
    for index, invitation in enumerate(invitations, start=1):
        label = invitation_label(index, invitation.get("token"))
        try:
            page = template.render(build_context(site, invitation))
            pages.append((invitation["token"], strip_html_comments(page)))
        except TemplateError as exc:
            failures.setdefault(str(exc), []).append(label)
    if failures:
        # A template problem usually hits every page: report it once.
        raise ValidationError(
            f"{labels[0]}: {message}"
            if len(labels) == 1
            else f"{len(labels)} invitation(s): {message}; first: {labels[0]}"
            for message, labels in failures.items()
        )
    return pages


def write_pages(stage: Path, pages: Iterable[tuple[str, str]]) -> int:
    """Write `i/<token>/index.html` for every rendered page."""
    count = 0
    for token, page in pages:
        path = stage / PAGES_DIRNAME / token / PAGE_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(page)
        except OSError as exc:
            raise BuildError(f"cannot write a page: {exc.strerror}") from None
        count += 1
    return count


def _write_file(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as exc:
        raise BuildError(
            f"cannot write {_redact_paths(path.name)}: {exc.strerror}"
        ) from None


def build_site(
    data_dir: Path,
    media_dir: Path,
    out_dir: Path,
    code_dir: Path = CODE_DIR,
    report: Report | None = None,
    log: Callable[[str], None] = print,
) -> OutputStats:
    """Validate the data, write the output directory and check the result.

    The output is assembled in a temporary directory and only replaces
    `out_dir` when every check has passed.  The log gets counters and paths,
    never data.
    """
    report = report if report is not None else Report()
    assets_dir = code_dir / ASSETS_DIRNAME
    check_out_dir(out_dir, code_dir=code_dir, data_dir=data_dir, media_dir=media_dir)

    site, invitations = load_data(data_dir, media_dir, report, assets_dir=assets_dir)
    log(
        f"build: {len(invitations)} invitation(s), {report.media_files} media file(s) "
        f"validated (data: {display_path(data_dir)}, media: {display_path(media_dir)})"
    )

    template = load_template(code_dir / TEMPLATE_FILE)
    stub = load_stub(code_dir / STUB_FILE).encode("utf-8")
    pages = render_pages(template, site, invitations)
    media_name = site["mediaDir"]

    with _StagedOutput(out_dir, warn=report.warn) as stage:
        written = write_pages(stage, pages)
        for name in STUB_OUTPUTS:
            _write_file(stage / name, stub)
        assets = copy_assets(assets_dir, stage / ASSETS_DIRNAME, ignore=is_styleguide_asset)
        media = copy_media(site, media_dir, stage / ASSETS_DIRNAME / media_name)
        _write_file(stage / ASSETS_DIRNAME / media_name / ICS_FILE, build_ics(site))
        _write_file(stage / HEADERS_FILE, HEADERS_TEXT.encode("utf-8"))
        _write_file(stage / ROBOTS_FILE, ROBOTS_TEXT.encode("utf-8"))
        stats = check_output(stage, [token for token, _page in pages], media_name)

    out_shown = display_path(out_dir)
    log(f"build: wrote {written} page(s) to {out_shown}/{PAGES_DIRNAME}/")
    log(f"build: copied {assets} asset file(s) to {out_shown}/{ASSETS_DIRNAME}/")
    log(
        f"build: copied {media} media file(s) and wrote {ICS_FILE} to "
        f"{out_shown}/{ASSETS_DIRNAME}/<mediaDir>/"
    )
    log(
        f"build: OK -> {out_shown} ({written} page(s), {media} media file(s), "
        f"{stats.files} file(s) in total, {format_size(stats.size)})"
    )
    return stats


# --------------------------------------------------------------------------
# Command line interface
# --------------------------------------------------------------------------


def _stdout(message: str) -> None:
    # Flushed on every line so that stdout and stderr stay in order in CI logs.
    print(message, flush=True)


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _cli_report() -> Report:
    return Report(on_warn=lambda message: _stderr(f"warning: {message}"))


def _data_dir(args: argparse.Namespace, code_dir: Path) -> Path:
    return Path(args.data) if args.data is not None else code_dir / DEFAULT_DATA_DIR


def _media_dir(args: argparse.Namespace, code_dir: Path) -> Path:
    return Path(args.media) if args.media is not None else code_dir / DEFAULT_MEDIA_DIR


def cmd_build(args: argparse.Namespace) -> int:
    code_dir = CODE_DIR
    build_site(
        _data_dir(args, code_dir),
        _media_dir(args, code_dir),
        Path(args.out) if args.out is not None else DEFAULT_OUT_DIR,
        code_dir=code_dir,
        report=_cli_report(),
        log=_stdout,
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    code_dir = CODE_DIR
    data_dir = _data_dir(args, code_dir)
    media_dir = _media_dir(args, code_dir)
    report = _cli_report()
    _site, invitations = load_data(
        data_dir, media_dir, report, assets_dir=code_dir / ASSETS_DIRNAME
    )
    _stdout(
        f"validate: OK - {len(invitations)} invitation(s), "
        f"{report.media_files} media file(s) (data: {display_path(data_dir)}, "
        f"media: {display_path(media_dir)})"
    )
    return EXIT_OK


def cmd_token(_args: argparse.Namespace) -> int:
    _stdout(generate_token())
    return EXIT_OK


#: Environment variables that mark an automated run; `links` refuses to work
#: there, because it is the one command that prints personal data.
CI_ENVIRONMENT_VARIABLES = ("CI", "GITHUB_ACTIONS")


def running_in_ci(environ: Any = None) -> bool:
    """True when `CI` or `GITHUB_ACTIONS` is set (and is not empty, 0 or false)."""
    environ = os.environ if environ is None else environ
    return any(
        environ.get(name, "").strip().lower() not in ("", "0", "false")
        for name in CI_ENVIRONMENT_VARIABLES
    )


def parse_base_url(value: str) -> str:
    """`--base`: an http(s) URL of the site; the trailing '/' is dropped."""
    value = value.strip()
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        parts = None
    if (
        parts is None
        or parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
        or any(char.isspace() for char in value)
    ):
        raise argparse.ArgumentTypeError(
            "expected the site address, e.g. https://example.org "
            "(http or https, no query string or fragment)"
        )
    return value.rstrip("/")


def invitation_links(base: str, invitations: Sequence[dict]) -> list[str]:
    """`<greeting><TAB><base>/i/<token>/` for every invitation."""
    return [
        f"{' '.join(invitation['greeting'].split())}\t"
        f"{base}/{PAGES_DIRNAME}/{invitation['token']}/"
        for invitation in invitations
    ]


def cmd_links(args: argparse.Namespace) -> int:
    if running_in_ci():
        raise BuildError(
            "this command prints personal data and is meant for local use only; "
            f"it does not run in CI ({' or '.join(CI_ENVIRONMENT_VARIABLES)} is set)"
        )
    _site, invitations = load_data(_data_dir(args, CODE_DIR), report=_cli_report())
    # Nothing is printed unless the whole data set is valid.
    for line in invitation_links(args.base, invitations):
        _stdout(line)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description="Build the static site of personal invitation pages.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add_data_options(subparser: argparse.ArgumentParser, *, media: bool) -> None:
        subparser.add_argument(
            "--data",
            metavar="DIR",
            help="data directory with site.json and invitations.json "
            f"(default: {DEFAULT_DATA_DIR} next to build.py)",
        )
        if media:
            subparser.add_argument(
                "--media",
                metavar="DIR",
                help="directory with media files "
                f"(default: {DEFAULT_MEDIA_DIR} next to build.py)",
            )

    build_cmd = subparsers.add_parser("build", help="validate the data and write the site")
    add_data_options(build_cmd, media=True)
    build_cmd.add_argument(
        "--out",
        metavar="DIR",
        help=f"output directory, replaced on every build (default: {DEFAULT_OUT_DIR})",
    )
    build_cmd.set_defaults(func=cmd_build)

    validate_cmd = subparsers.add_parser(
        "validate", help="check the data and media without writing anything"
    )
    add_data_options(validate_cmd, media=True)
    validate_cmd.set_defaults(func=cmd_validate)

    token_cmd = subparsers.add_parser("token", help="print a new invitation token")
    token_cmd.set_defaults(func=cmd_token)

    links_cmd = subparsers.add_parser(
        "links", help="print invitation links for manual sending (local use only)"
    )
    links_cmd.add_argument(
        "--base",
        metavar="URL",
        required=True,
        type=parse_base_url,
        help="address of the published site, e.g. https://example.org",
    )
    add_data_options(links_cmd, media=False)
    links_cmd.set_defaults(func=cmd_links)

    return parser


def debug_enabled() -> bool:
    """`BUILD_DEBUG=1` (or `true` / `yes`) shows tracebacks of internal errors."""
    return os.environ.get("BUILD_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    try:
        return int(args.func(args))
    except ValidationError as exc:
        for message in exc.errors:
            _stderr(f"error: {_redact_paths(message)}")
        _stderr(f"{command}: failed with {len(exc.errors)} error(s)")
        return EXIT_ERROR
    except BuildError as exc:
        _stderr(f"error: {_redact_paths(exc)}")
        _stderr(f"{command}: failed")
        return EXIT_ERROR
    except OSError as exc:
        where = f": {display_path(exc.filename)}" if exc.filename else ""
        _stderr(f"error: {_redact_paths(f'{exc.strerror or type(exc).__name__}{where}')}")
        _stderr(f"{command}: failed")
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        _stderr(f"{command}: interrupted")
        return EXIT_ERROR
    except Exception as exc:  # unexpected: report without leaking data
        if debug_enabled():
            traceback.print_exc()
        else:
            # The exception message may quote the data, so only the type and
            # the code location are shown.
            frames = traceback.extract_tb(exc.__traceback__)
            where = (
                f" at {os.path.basename(frames[-1].filename)}:{frames[-1].lineno}"
                if frames
                else ""
            )
            _stderr(
                f"error: internal error: {type(exc).__name__}{where} "
                "(set BUILD_DEBUG=1 for the traceback)"
            )
        _stderr(f"{command}: failed")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
