#!/usr/bin/env python3
"""Static builder for personal invitation pages.

    python build.py build    [--data DIR] [--media DIR] [--out DIR]
    python build.py validate [--data DIR] [--media DIR]
    python build.py token
    python build.py links    --base URL [--data DIR]

Only the Python 3 standard library is used (Python >= 3.10).

Importing this module has no side effects: everything happens inside `main()`,
so other tools (tests, preview scripts) can reuse the public helpers:

    load_data(data_dir)                -> (site, invitations)
    build_context(site, invitation)    -> dict
    render(template_source, context)   -> str
    parse_template(source).render(ctx) -> str

Exit codes: 0 - success, 1 - data/build error, 2 - bad command line.
"""

from __future__ import annotations

import argparse
import bisect
import html
import json
import math
import os
import re
import secrets
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# --------------------------------------------------------------------------
# Layout constants
# --------------------------------------------------------------------------

#: Directory that holds the code (template, assets).  Data/media/output
#: directories are resolved against the current working directory instead.
CODE_DIR = Path(__file__).resolve().parent

TEMPLATE_FILE = "template.html"
ASSETS_DIRNAME = "assets"
SITE_FILE = "site.json"
INVITATIONS_FILE = "invitations.json"

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
#: used to recognise a previous build before replacing `--out`, and is meant to
#: be the allow-list for the contents of the finished output as well.
OUTPUT_TOP_LEVEL = frozenset(
    {"i", ASSETS_DIRNAME, "index.html", "404.html", "_headers", "robots.txt"}
)
#: Files created by the operating system; ignored when `--out` is inspected.
OS_JUNK_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

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


def check_rendered(output: str, name: str = "template") -> None:
    """Fail if the rendered page still contains template syntax."""
    problems: list[str] = []
    newlines = [m.start() for m in re.finditer("\n", output)]
    for match in _LEFTOVER_RE.finditer(output):
        line = bisect.bisect_left(newlines, match.start()) + 1
        problems.append(f"line {line}: '{_shorten(match.group(0), 20)}'")
        if len(problems) == 5:
            break
    if problems:
        raise TemplateError(
            "rendered output still contains template syntax: " + "; ".join(problems),
            None,
            name,
        )


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


def check_media_name(name: Any) -> str | None:
    """Problem description for a media file reference, or None when it is fine."""
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
    return None


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
            problem = check_media_name(name)
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
            problem = check_media_name(photo)
            if problem:
                report.error(f"{where}: field 'venue.photos[{index}]' {problem}")

    directions = _field(
        venue, "directionsImage", "string", where, report, prefix="venue.",
        required=False, nullable=True,
    )
    if isinstance(directions, str) and directions.strip():
        problem = check_media_name(directions)
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
    return [(path, name) for path, name in references if check_media_name(name) is None]


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
        elif not entry.is_file():
            report.error(
                f"{where}: field '{path}': '{name}' in {display_path(media_dir)} "
                "is not a regular file"
            )
    return len({name for _, name in references})


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
) -> tuple[dict, list]:
    """Load and validate `site.json` and `invitations.json`.

    Media files are checked as well when `media_dir` is given.  All problems are
    collected and raised together as a `ValidationError`.
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


def site_context(site: dict) -> dict:
    """Template-ready copy of `site.json` (the shared part of every page).

    Every optional field is normalised so that `{{field}}` inside
    `<!-- if:field -->` always resolves.  Expects data that passed validation.

    Extension point for the full build (computed fields such as `mediaPath`,
    `icsPath`, `video.src`/`posterSrc`, `venue.photos[] -> {src}`,
    `venue.directionsSrc`, `venue.mapLinks.*`) - add them here so that the
    template sees them as ordinary fields.
    """
    video = site.get("video")
    schedule = site.get("schedule")
    venue = site.get("venue")
    venue = venue if isinstance(venue, dict) else {}
    geo = venue.get("geo")
    maps = venue.get("maps")
    maps = maps if isinstance(maps, dict) else {}
    photos = venue.get("photos")

    return {
        "coupleNames": _optional_text(site.get("coupleNames")),
        "dateISO": _optional_text(site.get("dateISO")),
        "dateText": _optional_text(site.get("dateText")),
        "rsvpDeadline": _optional_text(site.get("rsvpDeadline")),
        "outOfTownText": _optional_text(site.get("outOfTownText")),
        "mediaDir": _optional_text(site.get("mediaDir")),
        "video": (
            {
                "file": _optional_text(video.get("file")),
                "poster": _optional_text(video.get("poster")),
            }
            if isinstance(video, dict)
            else None
        ),
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
            "name": _optional_text(venue.get("name")),
            "description": _optional_text(venue.get("description")),
            "address": _optional_text(venue.get("address")),
            "photos": [
                photo
                for photo in (photos if isinstance(photos, list) else [])
                if isinstance(photo, str) and photo.strip()
            ],
            "directionsImage": _optional_text(venue.get("directionsImage")),
            "geo": (
                {"lat": geo.get("lat"), "lng": geo.get("lng")}
                if isinstance(geo, dict)
                else None
            ),
            "maps": {
                "googlePlaceId": _optional_text(maps.get("googlePlaceId")),
                "yandexOrgId": _optional_text(maps.get("yandexOrgId")),
            },
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
# Output directory handling
# --------------------------------------------------------------------------


def display_path(path: Path | str) -> str:
    """Path for logs: relative to the working directory when possible."""
    path = Path(path)
    try:
        return str(path.absolute().relative_to(Path.cwd())) or "."
    except ValueError:
        return str(path)


def _redact_paths(text: str) -> str:
    """Hide invitation tokens that may appear inside file system paths."""
    return re.sub(
        r"([/\\]i[/\\])([A-Za-z0-9_-]{4})[A-Za-z0-9_-]+", r"\1\2…", str(text)
    )


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
    of `OUTPUT_TOP_LEVEL` (files created by the operating system are ignored).
    No marker file is written into the output: its contents stay limited to
    what the site needs.
    """
    out = Path(out_dir)
    try:
        names = sorted(entry.name for entry in os.scandir(out))
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BuildError(
            f"cannot read the output directory {display_path(out)}: {exc.strerror}"
        ) from None
    unexpected = [
        name for name in names if name not in OUTPUT_TOP_LEVEL and name not in OS_JUNK_FILES
    ]
    if unexpected:
        listed = ", ".join(f"'{_shorten(name, 40)}'" for name in unexpected[:3])
        more = f" and {len(unexpected) - 3} more" if len(unexpected) > 3 else ""
        raise BuildError(
            f"refusing to replace {display_path(out)}: it does not look like a "
            f"previous build output (unexpected: {listed}{more}); "
            "remove it manually or choose another --out"
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


def copy_assets(
    source: Path,
    destination: Path,
    ignore: Callable[[str], bool] | None = None,
) -> int:
    """Copy `assets/` into the output, skipping dot-files; returns file count.

    `ignore` receives a file or directory name and returns True for entries
    that must not reach the output (extension point for later build steps).
    """
    if not source.is_dir():
        raise BuildError(f"assets directory not found: {display_path(source)}")

    def skip(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name.startswith(".") or (ignore is not None and ignore(name))
        }

    shown = display_path(source)
    try:
        shutil.copytree(source, destination, ignore=skip)
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


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def load_template(path: Path) -> Template:
    """Read and parse the page template."""
    try:
        source = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise BuildError(f"template not found: {display_path(path)}") from None
    except UnicodeDecodeError as exc:
        raise BuildError(
            f"{display_path(path)}: not valid UTF-8 (at byte {exc.start})"
        ) from None
    except OSError as exc:
        raise BuildError(
            f"cannot read the template {display_path(path)}: {exc.strerror}"
        ) from None
    return parse_template(source, path.name)


def render_pages(
    template: Template, site: dict, invitations: Sequence[dict]
) -> list[tuple[str, str]]:
    """Render every invitation; returns (token, html) pairs.

    Rendering happens entirely in memory: a broken template must not leave a
    half-written output directory behind.
    """
    pages: list[tuple[str, str]] = []
    failures: dict[str, list[str]] = {}  # message -> labels, in order of appearance
    for index, invitation in enumerate(invitations, start=1):
        label = invitation_label(index, invitation.get("token"))
        try:
            pages.append(
                (invitation["token"], template.render(build_context(site, invitation)))
            )
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
        path = stage / "i" / token / "index.html"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(page)
        except OSError as exc:
            raise BuildError(f"cannot write a page: {exc.strerror}") from None
        count += 1
    return count


def build_site(
    data_dir: Path,
    media_dir: Path,
    out_dir: Path,
    code_dir: Path = CODE_DIR,
    report: Report | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Validate the data and write the output directory."""
    report = report if report is not None else Report()
    check_out_dir(out_dir, code_dir=code_dir, data_dir=data_dir, media_dir=media_dir)

    site, invitations = load_data(data_dir, media_dir, report)
    log(
        f"build: {len(invitations)} invitation(s), {report.media_files} media file(s) "
        f"validated (data: {display_path(data_dir)}, media: {display_path(media_dir)})"
    )

    template = load_template(code_dir / TEMPLATE_FILE)
    pages = render_pages(template, site, invitations)

    with _StagedOutput(out_dir, warn=report.warn) as stage:
        written = write_pages(stage, pages)
        assets = copy_assets(code_dir / ASSETS_DIRNAME, stage / ASSETS_DIRNAME)
        # Extension point for the full build: stub pages (index.html, 404.html),
        # the .ics file, _headers, robots.txt, the media copy into
        # assets/<mediaDir>/ and the checks on the finished output.

    out_shown = display_path(out_dir)
    log(f"build: wrote {written} page(s) to {out_shown}/i/")
    log(f"build: copied {assets} asset file(s) to {out_shown}/{ASSETS_DIRNAME}/")
    log(f"build: OK -> {out_shown}")


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
    _site, invitations = load_data(data_dir, media_dir, report)
    _stdout(
        f"validate: OK - {len(invitations)} invitation(s), "
        f"{report.media_files} media file(s) (data: {display_path(data_dir)}, "
        f"media: {display_path(media_dir)})"
    )
    return EXIT_OK


def cmd_token(_args: argparse.Namespace) -> int:
    _stdout(generate_token())
    return EXIT_OK


def cmd_links(_args: argparse.Namespace) -> int:
    # Implemented by a later build stage; the subcommand exists so that the
    # command line stays stable.
    _stderr("links: not implemented yet")
    return EXIT_ERROR


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
    links_cmd.add_argument("--base", metavar="URL", required=True, help="site base URL")
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
            _stderr(f"error: {message}")
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
