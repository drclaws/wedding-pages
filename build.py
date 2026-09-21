#!/usr/bin/env python3
"""Static builder for personal invitation pages.

    python build.py build    [--data DIR] [--media DIR] [--out DIR] [--base-url URL]
    python build.py validate [--data DIR] [--media DIR]
    python build.py token    [--prefix NAME]
    python build.py links    --base URL [--data DIR]

Only the Python 3 standard library is used (Python >= 3.10).

The data is in the format 2 (`site.json` with `"schemaVersion": 2` and
`invitations.json`); it is checked by `tools/_schema.py`, and the page of every
invitation is rendered from its page tree (`tools/_page.py`) with the page
template and its fragments.

`build` writes the pages (`i/<token>/index.html`), the stub (`index.html`
and `404.html`), `assets/` (including `assets/<mediaDir>/` with the media files
that at least one page shows and one calendar file for every event that at
least one invitation sees, all published under the hash of their contents, and
the icon and the link preview image, which are generated from the design
tokens of `assets/app.css` unless the media directory provides them),
`_headers` and `robots.txt`, and then checks the finished output: nothing but
the expected files, no external resources, no inline scripts, every local link
resolves, no file above the hosting size limit.

Importing this module has no side effects: everything happens inside `main()`,
so other tools (tests, preview scripts) can reuse the public helpers:

    load_data(data_dir)                -> Data (site, invitations, usage, media)
    page_trees(data, images)           -> list of page trees
    render(template_source, context)   -> str
    parse_template(source).render(ctx) -> str
    event_calendar(media_dir, event)   -> bytes
    calendar_files(data)               -> {event id: CalendarFile}
    check_output(out_dir, pages)       -> OutputStats

Exit codes: 0 - success, 1 - data/build error, 2 - bad command line.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import hmac
import html
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import sys
import traceback
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Mapping, NamedTuple, Sequence
from urllib.parse import unquote, urlsplit

# `tools/` lives next to this file; the directory of the script is on the path
# when it is run as `python path/to/build.py`, this covers every other import.
if str(Path(__file__).resolve().parent) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import _data as data_tools  # noqa: E402
from tools import _media as media_tools  # noqa: E402
from tools import _page as page_tools  # noqa: E402
from tools import _png as png_tools  # noqa: E402
from tools import _schema as schema  # noqa: E402
from tools import gen_assets  # noqa: E402
from tools._data import (  # noqa: E402
    IMAGE_EXTENSIONS,
    MIN_TOKEN_LENGTH,
    _extension_list,
    check_token_prefix,
    format_size,
    generate_prefixed_token,
    generate_token,
    invitation_label,
    json_type,
    short_token_summary,
)
from tools._dates import DEFAULT_EVENT_DURATION, parse_date_iso  # noqa: E402
from tools._maps import _url_component, media_url  # noqa: E402

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
#: The calendar file of an event, in the media directory:
#: `assets/<mediaDir>/<sha256[:16]>.ics` of its contents.
CALENDAR_SUFFIX = ".ics"
#: The stub is published under both names, byte for byte the same.
STUB_OUTPUTS = ("index.html", "404.html")
HEADERS_FILE = "_headers"
ROBOTS_FILE = "robots.txt"

#: Style sheet inside `assets/` whose `:root` tokens colour the generated images.
TOKENS_FILE = "app.css"
#: Site images: `assets/favicon.<ext>` and `assets/og.<ext>`.  They are
#: generated from the design tokens (`.svg` and `.png`); a file with one of
#: these names in the media directory replaces the generated one (the first
#: name that exists wins).
FAVICON_NAMES = ("favicon.svg", "favicon.png", "favicon.ico")
OG_IMAGE_NAMES = ("og.png", "og.jpg")
SITE_IMAGE_NAMES = {"favicon": FAVICON_NAMES, "og": OG_IMAGE_NAMES}
SITE_IMAGE_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
}
#: Environment variable with the address of the published site (`--base-url`).
BASE_URL_VARIABLE = "SITE_BASE_URL"

#: Defaults relative to CODE_DIR.
DEFAULT_DATA_DIR = Path("examples") / "data"
DEFAULT_MEDIA_DIR = Path("examples") / "media"
#: Default relative to the current working directory.
DEFAULT_OUT_DIR = Path("dist")

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
#: Everything that may live below `assets/` in the output (`.txt` is meant for
#: the licences of fonts and the like).
ASSET_EXTENSIONS = frozenset(
    {".css", ".js", ".woff2", ".ico", ".txt"} | IMAGE_EXTENSIONS | schema.VIDEO_EXTENSIONS
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
  Content-Security-Policy: default-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
/i/*
  Cache-Control: private, no-cache
"""
ROBOTS_TEXT = "User-agent: *\nDisallow: /\n"

#: How long an event without an end lasts (in its calendar file and for the
#: past/now marks of the page); `site.json` gets a warning for such an event.
ICS_DEFAULT_DURATION = DEFAULT_EVENT_DURATION
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

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class BuildError(Exception):
    """Expected failure: reported as a plain message, never as a traceback."""


class TemplateError(BuildError):
    """Malformed template or a value that cannot be rendered.

    An error inside a fragment names the fragment file and line, followed by
    the places it was included from (`chain`, innermost first).
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        name: str = "template",
        chain: Sequence[str] = (),
    ):
        self.reason = message
        self.line = line
        self.name = name
        self.chain = tuple(chain)
        where = f"{name} line {line}" if line else name
        text = f"{where}: {message}"
        if self.chain:
            text += f" (included from {' <- '.join(self.chain)})"
        super().__init__(text)

    def via(self, name: str, line: int) -> "TemplateError":
        """The same error, seen from the include at `name` line `line`."""
        return TemplateError(
            self.reason, self.line, self.name, self.chain + (f"{name} line {line}",)
        )


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
        #: Number of distinct media files of the items that the pages show.
        self.media_files = 0
        #: Icon and link preview image, see `find_site_images`.
        self.site_images: SiteImages = DEFAULT_SITE_IMAGES
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
#: `field`, `event.title`, `.field` (field of the current item), `.` (the item)
_PATH_RE = re.compile(rf"(?:\.|\.?{_NAME}(?:\.{_NAME})*)\Z")
_PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}")
_BRACES_RE = re.compile(r"\{\{|\}\}")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
#: Words that open a directive: `<!-- if:… -->`, `<!-- endif -->` and so on.
_DIRECTIVE_WORDS = r"(?:if|each|with|include)\s*:|(?:endif|endeach|endwith)\b"
#: A comment that is meant to be a directive (anything else stays literal text).
_DIRECTIVE_LIKE_RE = re.compile(_DIRECTIVE_WORDS, re.IGNORECASE)
_IF_RE = re.compile(r"if\s*:\s*(!?)\s*(\S*)\Z")
_EACH_RE = re.compile(r"each\s*:\s*(\S*)\Z")
_WITH_RE = re.compile(r"with\s*:\s*(\S*)\Z")
#: Template syntax that must not survive rendering.
_LEFTOVER_RE = re.compile(rf"\{{\{{|\}}\}}|<!--\s*(?:{_DIRECTIVE_WORDS})", re.IGNORECASE)

#: Fragments of the template live in this directory next to `template.html`.
#: They are code: read once before rendering and never copied to the output.
FRAGMENTS_DIRNAME = "fragments"
FRAGMENT_SUFFIX = ".html"
#: One part of a fragment name: `widgets/text` is the file `widgets/text.html`.
#: A value that chooses a fragment must be exactly one such part.
_SEGMENT = r"[a-z][a-z0-9-]{0,39}"
_SEGMENT_RE = re.compile(rf"{_SEGMENT}\Z")
_FRAGMENT_NAME_RE = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*\Z")
#: `include:dir/name` (a fixed fragment) or `include:dir/{{.field}}` (the
#: value of the field names a fragment in `dir/`).
_INCLUDE_RE = re.compile(
    rf"include\s*:\s*(?:({_SEGMENT}(?:/{_SEGMENT})*)"
    rf"|((?:{_SEGMENT}/)+)\{{\{{\s*(\S+?)\s*\}}\}})\Z"
)
#: Fragments nested deeper than this are a runaway recursion through the data.
MAX_INCLUDE_DEPTH = 12

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
    """`<!-- if:path -->…<!-- endif -->`, `<!-- each:path -->…<!-- endeach -->`
    or `<!-- with:path -->…<!-- endwith -->`."""

    __slots__ = ("kind", "path", "negate", "line", "children")

    def __init__(self, kind: str, path: str, negate: bool, line: int):
        self.kind = kind  # "if" | "each" | "with"
        self.path = path
        self.negate = negate
        self.line = line
        self.children: list[Any] = []


class _Include:
    """`<!-- include:dir/name -->` or `<!-- include:dir/{{.field}} -->`."""

    __slots__ = ("name", "directory", "path", "line", "in_scope")

    def __init__(self, name: str, directory: str, path: str, line: int, in_scope: bool):
        self.name = name  # a fixed fragment, or "" when the data chooses it
        self.directory = directory  # the data chooses a fragment in this directory
        self.path = path  # ... by the value of this field of the current item
        self.line = line
        #: Inside `each`/`with` of the same file: the item there is its own.
        self.in_scope = in_scope

    def shown(self) -> str:
        target = self.name or f"{self.directory}/{{{{{self.path}}}}}"
        return f"<!-- include:{target} -->"


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
    the current item (of `each` or `with`) and `.` is the item itself.
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


def _walk(nodes: Sequence[Any]) -> Iterable[Any]:
    """Every node, depth first, including the contents of blocks."""
    for node in nodes:
        yield node
        if isinstance(node, _Block):
            yield from _walk(node.children)


class _Fragment:
    """One parsed fragment file."""

    __slots__ = ("nodes", "shown", "needs_item")

    def __init__(self, nodes: list[Any], shown: str, needs_item: bool):
        self.nodes = nodes
        #: The file as messages name it: `fragments/widgets/text.html`.
        self.shown = shown
        #: Uses the item of the place it is included from: `.field` outside its
        #: own `each`/`with`, directly or through a fragment it includes.
        self.needs_item = needs_item


class Fragments:
    """The fragments of the template by name (`widgets/text`).

    Every fragment is parsed and checked once, before any page is rendered:
    a fixed include names an existing fragment, fixed includes never form a
    cycle, and a directory that the data chooses from is not empty.  The set
    is fixed from then on, so a value from the data can only pick one of
    these files and never becomes a path.
    """

    def __init__(self, sources: dict[str, str] | None = None):
        self._items: dict[str, _Fragment] = {}
        for name, source in sorted((sources or {}).items()):
            shown = f"{FRAGMENTS_DIRNAME}/{name}{FRAGMENT_SUFFIX}"
            if not _FRAGMENT_NAME_RE.match(name):
                raise TemplateError(
                    "a fragment name uses a-z, 0-9 and '-' and starts with a letter "
                    "(at most 40 characters), '/' separates directories",
                    None,
                    shown,
                )
            nodes, needs_item = _parse(source, shown, fragment=True)
            self._items[name] = _Fragment(nodes, shown, needs_item)
        for fragment in self._items.values():
            self._check_includes(fragment.nodes, fragment.shown)
        self._check_cycles()
        self._propagate_needs_item()

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)

    def get(self, name: str) -> _Fragment:
        return self._items[name]

    def names(self, directory: str) -> list[str]:
        """Names a value may choose in `directory` (its files, not subdirectories)."""
        prefix = directory + "/"
        return sorted(
            name[len(prefix) :]
            for name in self._items
            if name.startswith(prefix) and "/" not in name[len(prefix) :]
        )

    def check_page(self, nodes: Sequence[Any], name: str) -> None:
        """Check the includes of the page template (`name`) before rendering.

        On top of the checks every fragment passes, a fragment that uses the
        current item may only be included where there is one.
        """
        self._check_includes(nodes, name)
        for node in _walk(nodes):
            if (
                isinstance(node, _Include)
                and node.name
                and not node.in_scope
                and self._items[node.name].needs_item
            ):
                raise TemplateError(
                    f"{node.shown()}: the fragment uses the current item ('.'), "
                    "include it inside <!-- each:… --> or <!-- with:… -->",
                    node.line,
                    name,
                )

    def _check_includes(self, nodes: Sequence[Any], name: str) -> None:
        for node in _walk(nodes):
            if not isinstance(node, _Include):
                continue
            if node.name:
                if node.name not in self._items:
                    raise TemplateError(
                        f"{node.shown()}: no fragment "
                        f"{FRAGMENTS_DIRNAME}/{node.name}{FRAGMENT_SUFFIX}",
                        node.line,
                        name,
                    )
            elif not self.names(node.directory):
                raise TemplateError(
                    f"{node.shown()}: no fragments in "
                    f"{FRAGMENTS_DIRNAME}/{node.directory}/",
                    node.line,
                    name,
                )

    def _check_cycles(self) -> None:
        done: set[str] = set()
        stack: list[str] = []

        def visit(current: str) -> None:
            stack.append(current)
            fragment = self._items[current]
            for node in _walk(fragment.nodes):
                if not isinstance(node, _Include) or not node.name or node.name in done:
                    continue
                if node.name in stack:
                    cycle = stack[stack.index(node.name) :] + [node.name]
                    raise TemplateError(
                        f"{node.shown()}: fragments include each other in a cycle: "
                        + " -> ".join(cycle),
                        node.line,
                        fragment.shown,
                    )
                visit(node.name)
            stack.pop()
            done.add(current)

        for name in self._items:
            if name not in done:
                visit(name)

    def _propagate_needs_item(self) -> None:
        # a fragment that includes one needing the item outside its own
        # each/with passes its item on, so it needs one as well
        changed = True
        while changed:
            changed = False
            for fragment in self._items.values():
                if fragment.needs_item:
                    continue
                if any(
                    isinstance(node, _Include)
                    and node.name
                    and not node.in_scope
                    and self._items[node.name].needs_item
                    for node in _walk(fragment.nodes)
                ):
                    fragment.needs_item = True
                    changed = True


class Template:
    """A parsed template; parse once, render for every invitation."""

    def __init__(
        self, source: str, name: str = "template", fragments: Fragments | None = None
    ):
        self.name = name
        self.fragments = fragments if fragments is not None else Fragments()
        self.nodes, _needs_item = _parse(source, name)
        self.fragments.check_page(self.nodes, name)

    def render(self, context: dict) -> str:
        out: list[str] = []
        _Renderer(context, self.fragments, out).nodes(self.nodes, MISSING, self.name, 0)
        result = "".join(out)
        check_rendered(result, self.name)
        return result


def parse_template(
    source: str, name: str = "template", fragments: Fragments | None = None
) -> Template:
    """Parse a template, raising TemplateError with a line number on failure."""
    return Template(source, name, fragments)


def render(
    template_source: str,
    context: dict,
    name: str = "template",
    fragments: dict[str, str] | None = None,
) -> str:
    """Parse and render a template in one step (`fragments`: name -> source)."""
    return Template(template_source, name, Fragments(fragments)).render(context)


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


def _parse(source: str, name: str, fragment: bool = False) -> tuple[list[Any], bool]:
    """Parse a template or a fragment into (nodes, needs_item).

    In the page template `.field` is only allowed inside `each`/`with`.  A
    fragment may use it anywhere: outside its own `each`/`with` it means the
    item of the place the fragment is included from, and `needs_item` says so.
    """
    newlines = [m.start() for m in re.finditer("\n", source)]

    def line_at(pos: int) -> int:
        return bisect.bisect_left(newlines, pos) + 1

    root: list[Any] = []
    stack: list[_Block] = []
    needs_item = False

    def children() -> list[Any]:
        return stack[-1].children if stack else root

    def in_scope() -> bool:
        return any(block.kind in ("each", "with") for block in stack)

    def check_syntax(path: str, line: int, shown: str) -> None:
        if not path:
            raise TemplateError(f"{shown} has no field path", line, name)
        if not _PATH_RE.match(path):
            raise TemplateError(f"{shown} has an invalid field path", line, name)

    def check_path(path: str, line: int, shown: str) -> None:
        nonlocal needs_item
        check_syntax(path, line, shown)
        if path.startswith(".") and not in_scope():
            if not fragment:
                raise TemplateError(
                    f"{shown} refers to the current item but is not inside "
                    "<!-- each:… --> or <!-- with:… -->",
                    line,
                    name,
                )
            needs_item = True

    def add_include(match: re.Match, line: int, shown: str) -> None:
        fixed, directory, path = match.groups()
        if fixed:
            children().append(_Include(fixed, "", "", line, in_scope()))
            return
        check_syntax(path, line, shown)
        if not path.startswith(".") or path == ".":
            raise TemplateError(
                f"{shown}: a fragment is chosen by a field of the current item, "
                "write {{.field}}",
                line,
                name,
            )
        if not in_scope():
            # the item changes before every choice, so a fragment can never
            # choose itself again by the item it was chosen for
            raise TemplateError(
                f"{shown} must be inside <!-- each:… --> or <!-- with:… -->", line, name
            )
        children().append(_Include("", directory.rstrip("/"), path, line, True))

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

        if inner in ("endif", "endeach", "endwith"):
            kind = inner[len("end") :]
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

        include_match = _INCLUDE_RE.match(inner)
        if include_match:
            add_include(include_match, line, shown)
            continue
        if inner[: len("include")].lower() == "include":
            raise TemplateError(
                f"malformed directive {shown} (expected <!-- include:dir/name --> or "
                "<!-- include:dir/{{.field}} -->, all lowercase; a name uses a-z, "
                "0-9 and '-' and starts with a letter)",
                line,
                name,
            )

        if_match = _IF_RE.match(inner)
        each_match = _EACH_RE.match(inner)
        with_match = _WITH_RE.match(inner)
        if if_match:
            kind, negate, path = "if", bool(if_match.group(1)), if_match.group(2)
        elif each_match:
            kind, negate, path = "each", False, each_match.group(1)
        elif with_match:
            kind, negate, path = "with", False, with_match.group(1)
            if path == ".":
                raise TemplateError(f"{shown}: '.' is the current item already", line, name)
        else:
            raise TemplateError(
                f"malformed directive {shown} (expected <!-- if:path -->, "
                "<!-- if:!path -->, <!-- endif -->, <!-- each:path -->, "
                "<!-- endeach -->, <!-- with:path -->, <!-- endwith --> or "
                "<!-- include:dir/name -->, all lowercase)",
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
    return root, needs_item


class _Renderer:
    """Renders parsed nodes of the template and its fragments into `out`."""

    __slots__ = ("context", "fragments", "out")

    def __init__(self, context: dict, fragments: Fragments, out: list[str]):
        self.context = context
        self.fragments = fragments
        self.out = out

    def nodes(self, nodes: Sequence[Any], item: Any, name: str, depth: int) -> None:
        """Render `nodes` of the file `name`; `depth` counts nested fragments."""
        context, out = self.context, self.out
        for node in nodes:
            if isinstance(node, str):
                out.append(node)
            elif isinstance(node, _Var):
                value = lookup(node.path, context, item)
                if value is MISSING:
                    raise TemplateError(f"field '{node.path}' not found", node.line, name)
                out.append(format_value(value, node.path, node.line, name))
            elif isinstance(node, _Include):
                self.include(node, item, name, depth)
            elif node.kind == "if":
                if is_truthy(lookup(node.path, context, item)) is not node.negate:
                    self.nodes(node.children, item, name, depth)
            elif node.kind == "with":
                value = lookup(node.path, context, item)
                if value is MISSING:
                    raise TemplateError(
                        f"<!-- with:{node.path} -->: field '{node.path}' not found",
                        node.line,
                        name,
                    )
                if value is None or value is False:
                    continue
                if not isinstance(value, dict):
                    raise TemplateError(
                        f"<!-- with:{node.path} -->: field '{node.path}' is "
                        f"{json_type(value)}, expected an object",
                        node.line,
                        name,
                    )
                if value:
                    self.nodes(node.children, value, name, depth)
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
                    self.nodes(node.children, element, name, depth)

    def include(self, node: _Include, item: Any, name: str, depth: int) -> None:
        target = node.name or self.choose(node, item, name)
        if depth >= MAX_INCLUDE_DEPTH:
            raise TemplateError(
                f"{node.shown()}: fragments are nested deeper than "
                f"{MAX_INCLUDE_DEPTH} levels",
                node.line,
                name,
            )
        fragment = self.fragments.get(target)
        try:
            self.nodes(fragment.nodes, item, fragment.shown, depth + 1)
        except TemplateError as exc:
            raise exc.via(name, node.line) from None

    def choose(self, node: _Include, item: Any, name: str) -> str:
        """The fragment named by the value of `node.path`.

        The value comes from the data: it is only compared with the names of
        the loaded files (one part, no '/') and never quoted in a message.
        """
        value = lookup(node.path, self.context, item)
        if value is MISSING:
            raise TemplateError(
                f"{node.shown()}: field '{node.path}' not found", node.line, name
            )
        if not isinstance(value, str) or not _SEGMENT_RE.match(value):
            raise TemplateError(
                f"{node.shown()}: the value of '{node.path}' is not a fragment name",
                node.line,
                name,
            )
        target = f"{node.directory}/{value}"
        if target not in self.fragments:
            raise TemplateError(
                f"{node.shown()}: no fragment in {FRAGMENTS_DIRNAME}/{node.directory}/ "
                f"for the value of '{node.path}' "
                f"(known: {', '.join(self.fragments.names(node.directory))})",
                node.line,
                name,
            )
        return target


# --------------------------------------------------------------------------
# Data loading and validation
# --------------------------------------------------------------------------

class MediaFile(NamedTuple):
    """A media file that at least one page shows."""

    #: The name of the file in the data (and in the media directory).
    name: str
    #: The name it is published under: `<sha256[:16]>.<ext>` of its contents.
    published: str
    #: What `tools._media.inspect` found out about it.
    info: media_tools.MediaInfo


class Data(NamedTuple):
    """Data that passed every check, and what the pages show of it."""

    site: dict
    invitations: list
    #: Ids of the parts of `site.json` that at least one page shows
    #: (`tools._page.Usage`).
    usage: page_tools.Usage
    #: Name in the data -> `MediaFile`, for every file of the media items that
    #: the pages show; empty when the media directory was not looked at.
    media: dict[str, MediaFile]


def check_media(
    site: dict,
    usage: page_tools.Usage,
    media_dir: Path | str,
    report: Report,
    where: str = SITE_FILE,
) -> dict[str, MediaFile]:
    """Look at the files of the media items that the pages show.

    A file must exist with exactly the name of the data, be a regular file
    (not a symbolic link) and fit the size limit of the hosting; then its
    facts are read (`tools._media.inspect`), checked
    (`tools._schema.check_media_files`) and its published name is computed.
    Messages name the field and the media directory, never the file name.
    Items that no page shows are not looked at: they are not published.
    """
    files = page_tools.media_files(site, usage)
    if not files:
        return {}
    media_dir = Path(media_dir)
    shown_dir = display_path(media_dir)
    errors = len(report.errors)
    listing: dict[str, os.DirEntry] = {}
    try:
        with os.scandir(media_dir) as entries:
            listing = {entry.name: entry for entry in entries}
    except FileNotFoundError:
        report.error(f"media directory not found: {shown_dir}")
    except NotADirectoryError:
        report.error(f"media path is not a directory: {shown_dir}")
    except OSError as exc:
        report.error(f"cannot read the media directory {shown_dir}: {exc.strerror}")
    if len(report.errors) > errors:
        return {}

    lowercase = {name.lower() for name in listing}
    found: dict[str, Path] = {}
    sizes: dict[str, int] = {}
    for path, name in files:
        entry = listing.get(name)
        problem = None
        if entry is None:
            problem = f"is missing from {shown_dir}"
            if name.lower() in lowercase:
                problem += " (a file with a different letter case exists; names are case-sensitive)"
        elif entry.is_symlink():
            problem = f"is a symbolic link in {shown_dir}; media must be regular files"
        elif not entry.is_file():
            problem = f"is not a regular file in {shown_dir}"
        else:
            try:
                size = entry.stat().st_size
            except OSError as exc:
                problem = f"cannot be read ({exc.strerror or type(exc).__name__})"
            else:
                if size > MAX_FILE_BYTES:
                    problem = (
                        f"is {format_size(size)}, the limit for a single file is "
                        f"{format_size(MAX_FILE_BYTES)}"
                    )
        if problem is None:
            found[name] = Path(entry.path)
            sizes[name] = size
        else:
            report.error(f"{where}: field '{path}': the file {problem}")
    if len(report.errors) > errors:
        return {}

    infos = {name: media_tools.inspect(source, name) for name, source in found.items()}
    schema.check_media_files(site, infos, report, shown=usage.media, where=where, sizes=sizes)
    if len(report.errors) > errors:
        return {}
    media: dict[str, MediaFile] = {}
    for name, source in sorted(found.items()):
        try:
            published = media_tools.hashed_name(source, name)
        except OSError as exc:
            path = next(path for path, file in files if file == name)
            report.error(
                f"{where}: field '{path}': the file cannot be read "
                f"({exc.strerror or type(exc).__name__})"
            )
            continue
        media[name] = MediaFile(name, published, infos[name])
    return media


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


class SiteImage(NamedTuple):
    """The icon or the link preview image of the site."""

    #: File name inside `assets/`.
    name: str
    mime: str
    #: The file in the media directory that replaces the generated image.
    source: Path | None = None
    #: Size in pixels (the link preview image only).
    width: int = 0
    height: int = 0


class SiteImages(NamedTuple):
    favicon: SiteImage
    og: SiteImage


#: What the build generates when the media directory provides nothing.
DEFAULT_SITE_IMAGES = SiteImages(
    favicon=SiteImage(gen_assets.FAVICON_FILE, SITE_IMAGE_TYPES[".svg"]),
    og=SiteImage(gen_assets.OG_FILE, SITE_IMAGE_TYPES[".png"], None, *gen_assets.OG_SIZE),
)

#: A link preview image should be about 1200x630 (a ratio of about 1.91:1);
#: a picture whose ratio differs by more than this fraction gets a warning.
#: 0.1 leaves room for the neighbouring shapes (2:1 and 16:9) that the
#: services crop without visible loss.
OG_RATIO_TOLERANCE = 0.1

#: How much of a file is read to find the size of an image.
_IMAGE_HEADER_BYTES = 1024 * 1024
_IMAGE_SIGNATURES = {
    ".png": (png_tools.PNG_SIGNATURE,),
    ".jpg": (b"\xff\xd8\xff",),
    ".ico": (b"\x00\x00\x01\x00",),
}


def _inspect_site_image(path: Path, role: str) -> tuple[str | None, tuple[int, int]]:
    """(problem or None, size in pixels) of a site image from the media directory."""
    extension = path.suffix.lower()
    signatures = _IMAGE_SIGNATURES.get(extension)
    if signatures is None:  # SVG: looked at by the output checks
        return None, (0, 0)
    try:
        with open(path, "rb") as handle:
            head = handle.read(_IMAGE_HEADER_BYTES)
    except OSError as exc:
        return f"cannot be read ({exc.strerror or type(exc).__name__})", (0, 0)
    if not head.startswith(signatures):
        return f"is not a {extension[1:].upper()} file", (0, 0)
    if role != "og":
        return None, (0, 0)
    size = png_tools.png_size(head) if extension == ".png" else png_tools.jpeg_size(head)
    if size is None:
        return "has no readable image size", (0, 0)
    return None, size


def find_site_images(media_dir: Path | str | None, report: Report) -> SiteImages:
    """The icon and the link preview image: generated, or taken from `media_dir`.

    `favicon.svg` / `favicon.png` / `favicon.ico` and `og.png` / `og.jpg` in the
    media directory replace the generated images (the first name that exists
    wins).  They are not mentioned in `site.json`; the same rules apply as to
    the other media: regular files, no symbolic links, the size limit.

    A file that only looks like one of them - another spelling (`OG.PNG`),
    another file type (`og.jpeg`) or the name that lost to a better one
    (`favicon.png` next to `favicon.svg`) - is a warning: silently ignoring it
    would leave the author waiting for a picture that never appears.  A
    picture of one's own whose proportions are not those of a link preview
    (about 1.91:1) is a warning too.
    """
    chosen: dict[str, SiteImage] = DEFAULT_SITE_IMAGES._asdict()
    if media_dir is None:
        return DEFAULT_SITE_IMAGES
    media_dir = Path(media_dir)
    try:
        with os.scandir(media_dir) as entries:
            listing = {entry.name: entry for entry in entries}
    except OSError:
        return DEFAULT_SITE_IMAGES  # a missing directory is reported with the media
    for role, names in SITE_IMAGE_NAMES.items():
        entry = next((listing[name] for name in names if name in listing), None)
        if entry is None:
            continue
        shown = display_path(media_dir / entry.name)
        if entry.is_symlink():
            report.error(f"{shown} is a symbolic link; media must be regular files")
            continue
        if not entry.is_file():
            report.error(f"{shown} is not a regular file")
            continue
        try:
            size = entry.stat().st_size
        except OSError as exc:
            report.error(f"{shown} cannot be read ({exc.strerror or type(exc).__name__})")
            continue
        if size > MAX_FILE_BYTES:
            report.error(
                f"{shown} is {format_size(size)}, the limit for a single file is "
                f"{format_size(MAX_FILE_BYTES)}"
            )
            continue
        problem, (width, height) = _inspect_site_image(Path(entry.path), role)
        if problem:
            report.error(f"{shown} {problem}")
            continue
        if role == "og" and width and height:
            wanted = DEFAULT_SITE_IMAGES.og.width / DEFAULT_SITE_IMAGES.og.height
            if abs(width / height - wanted) > wanted * OG_RATIO_TOLERANCE:
                report.warn(
                    f"{shown} is {width}x{height}; services that show link previews "
                    f"expect about {DEFAULT_SITE_IMAGES.og.width}x"
                    f"{DEFAULT_SITE_IMAGES.og.height} (a ratio of about 1.91:1), "
                    "so the picture will be cropped"
                )
        extension = os.path.splitext(entry.name)[1].lower()
        chosen[role] = SiteImage(
            entry.name, SITE_IMAGE_TYPES[extension], media_dir / entry.name, width, height
        )
    _warn_site_image_lookalikes(listing, chosen, media_dir, report)
    return SiteImages(**chosen)


def _warn_site_image_lookalikes(
    listing: dict, chosen: dict, media_dir: Path, report: Report
) -> None:
    """Warn about files that look like an icon or a link preview image.

    `chosen` is what `find_site_images` settled on; anything else in the media
    directory whose name differs only in case or in the file type never
    reaches the output, and neither does the loser of two valid names.
    """
    roles = {
        os.path.splitext(name)[0].lower(): role
        for role, names in SITE_IMAGE_NAMES.items()
        for name in names
    }
    for name in sorted(listing):
        role = roles.get(os.path.splitext(name)[0].lower())
        if role is None:
            continue
        used = chosen[role].source
        if used is not None and used.name == name:
            continue
        instead = (
            f"{display_path(used)} is used instead"
            if used is not None
            else f"the {role} image generated from the design tokens is used"
        )
        expected = _extension_list(
            os.path.splitext(known)[1] for known in SITE_IMAGE_NAMES[role]
        )
        detail = (
            ""
            if name in SITE_IMAGE_NAMES[role]
            else f" (the name must be exactly '{role}' with one of: {expected})"
        )
        report.warn(
            f"{display_path(media_dir / name)} is not published{detail}: {instead}"
        )


def site_images_context(
    images: SiteImages = DEFAULT_SITE_IMAGES, base_url: str = ""
) -> dict:
    """The computed fields of the icon and the link preview image.

    `ogImage` is absolute when the address of the site is known (`base_url`,
    without a trailing '/'), and a path from the site root otherwise.  None of
    the fields depends on the data: the stub may use them as well.
    """
    return {
        "faviconPath": f"/{ASSETS_DIRNAME}/{_url_component(images.favicon.name)}",
        "faviconType": images.favicon.mime,
        "ogImage": f"{base_url}/{ASSETS_DIRNAME}/{_url_component(images.og.name)}",
        "ogImageType": images.og.mime,
        "ogImageWidth": images.og.width,
        "ogImageHeight": images.og.height,
    }


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON data")


def read_json(path: Path, report: Report, known: Collection[str] | None = None) -> Any:
    """Read a JSON document, reporting I/O and syntax problems (never values).

    A key repeated within one object is an error as well: the standard parser
    would silently keep the last value only.  With `known` the message shows
    a repeated key only when it is one of these names (the keys of the
    invitations may be somebody's name).
    """
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
        value, duplicates = data_tools.parse_json(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        report.error(
            f"{shown}: invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        )
        return MISSING
    except ValueError as exc:
        report.error(f"{shown}: invalid JSON: {exc}")
        return MISSING
    for duplicate in duplicates:
        report.error(f"{shown}: {duplicate.describe(known)}")
    return MISSING if duplicates else value


def load_data(
    data_dir: Path | str,
    media_dir: Path | str | None = None,
    report: Report | None = None,
    assets_dir: Path | str | None = None,
) -> Data:
    """Load and check `site.json` and `invitations.json` (the format 2).

    The order matters: `site.json` first, because the repeated keys of
    `invitations.json` are named only when they are field names of the format
    or ids declared in `site.json`; then the invitations; then the page trees
    are built once, which reports what needs the finished trees (warnings
    about parts no page shows, a repeated DOM id).  The media files of the
    items that the pages show are looked at when `media_dir` is given, and
    `mediaDir` is compared with the contents of `assets_dir` when that is
    given.  All problems are collected and raised together as a
    `ValidationError`.
    """
    report = report if report is not None else Report()
    data_path = Path(data_dir)
    if not data_path.is_dir():
        report.error(f"data directory not found: {display_path(data_path)}")
        report.raise_if_failed()

    site = read_json(data_path / SITE_FILE, report)
    index = schema.check_site(site, report) if site is not MISSING else schema.SiteIndex()
    known = schema.GUEST_FIELD_NAMES | index.declared_ids()
    invitations = read_json(data_path / INVITATIONS_FILE, report, known)
    if invitations is not MISSING:
        schema.check_invitations(invitations, index, report)
    if media_dir is not None:
        report.site_images = find_site_images(media_dir, report)
    if site is not MISSING and assets_dir is not None:
        check_media_dir_collision(site, assets_dir, report)
    report.raise_if_failed()

    # the trees tell what the pages show; their media addresses do not matter here
    _trees, usage = page_tools.build_pages(site, invitations, page_settings(site), report)
    report.raise_if_failed()
    media = check_media(site, usage, media_dir, report) if media_dir is not None else {}
    report.raise_if_failed()
    report.media_files = len(media)
    return Data(site, invitations, usage, media)


def page_settings(
    site: dict,
    images: dict | None = None,
    media: dict[str, MediaFile] | None = None,
    calendars: Mapping[str, CalendarFile] | None = None,
) -> page_tools.PageSettings:
    """What the page trees need from the build.

    `images` are the fields of the site images (`site_images_context`),
    `media` the files that `check_media` found: a media file is addressed by
    its published name; `calendars` are the calendar files of the events
    (`calendar_files`), addressed the same way.
    """
    media = media or {}
    media_path = f"/{ASSETS_DIRNAME}/{site['mediaDir']}"

    def media_src(name: str) -> str:
        entry = media.get(name)
        return media_url(media_path, entry.published if entry is not None else name)

    def calendar_src(event_id: str) -> str:
        # the build never names a calendar file after its event: without the
        # files (trees that are only looked at, never rendered) there is no
        # address, and an event without a file is a bug
        if calendars is None:
            return ""
        entry = calendars.get(event_id)
        if entry is None:
            raise BuildError(
                f"the event {data_tools.show_id(event_id)} is on a page, but has no calendar file"
            )
        return media_url(media_path, entry.name)

    return page_tools.PageSettings(
        default_duration=ICS_DEFAULT_DURATION,
        site_images=images if images is not None else {},
        media_src=media_src,
        calendar_src=calendar_src,
        media_info={name: entry.info for name, entry in media.items()},
        pages_path=f"/{PAGES_DIRNAME}",
        assets_path=f"/{ASSETS_DIRNAME}",
    )


def page_trees(data: Data, images: dict | None = None) -> list[dict]:
    """The page tree of every invitation, in the order of `invitations.json`."""
    settings = page_settings(data.site, images, data.media, calendar_files(data))
    trees, _usage = page_tools.build_pages(data.site, data.invitations, settings)
    return trees


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


#: The part of a `UID` before the `@`: letters, digits, `_` and `-` only.
_ICS_UID_RE = re.compile(r"[A-Za-z0-9_-]+")


def build_event_ics(
    *, uid: str, start: datetime, end: datetime, summary: str, location: str = ""
) -> bytes:
    """A calendar file with one event: times in UTC, `uid@ICS_UID_DOMAIN`.

    `summary` and `location` are escaped and folded; an empty `location`
    leaves the `LOCATION` line out.  The result depends on the arguments only
    (no clock, no random values), so equal inputs give identical bytes.
    """
    if not isinstance(uid, str) or _ICS_UID_RE.fullmatch(uid) is None:
        raise ValueError("the calendar uid may only contain A-Z, a-z, 0-9, '_' and '-'")
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("the start and the end of an event need a UTC offset")
    if end <= start:
        raise ValueError("the end of an event must be later than its start")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}@{ICS_UID_DOMAIN}",
        f"DTSTAMP:{ICS_DTSTAMP}",
        f"DTSTART:{_ics_timestamp(start)}",
        f"DTEND:{_ics_timestamp(end)}",
        # the revision grows when the location is announced, so that importing
        # the file again updates an event that was saved without it
        f"SEQUENCE:{1 if location else 0}",
        f"SUMMARY:{ics_escape(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(ics_fold(line) + "\r\n" for line in lines).encode("utf-8")


def event_calendar(media_dir: str, event: dict) -> bytes:
    """The calendar file of one event, the same for every invitation.

    `event` is an event of `tools._page.calendar_events` (an event of a page
    tree has the same fields).  Nothing of an invitation goes into the file:
    the notes stay on the page.  The uid depends on the media directory, the
    id and the start of the event, so a new time gives a new entry, while a
    new title, end or place updates the entry saved before.  The place is
    included once it is announced.
    """
    uid = hashlib.sha256(
        f"{media_dir}\n{event['id']}\n{event['startISO']}".encode("utf-8")
    ).hexdigest()[:32]
    place = event["location"]
    location = ""
    if place["ready"]:
        location = ", ".join(part for part in (place["name"], place["address"]) if part)
    return build_event_ics(
        uid=uid,
        start=parse_date_iso(event["startISO"]),
        end=parse_date_iso(event["endISO"]),
        # default: the title of the event is the title of the calendar entry
        summary=event["title"],
        location=location,
    )


class CalendarFile(NamedTuple):
    """The calendar file of an event that at least one invitation sees."""

    #: `<HMAC-SHA256(calendar_key(tokens), contents)[:16]>.ics` (see
    #: `calendar_name`).
    name: str
    content: bytes


#: Hex digits of the name of a calendar file.
CALENDAR_NAME_DIGITS = 16


def calendar_key(tokens: Iterable[str]) -> bytes:
    """The key of the names of the calendar files: sha256 of the tokens of
    all invitations, sorted and joined with a line feed (a token never holds
    one).

    The contents of a calendar file are easy to guess (a title, a time, a
    place), so a plain hash of them could be found by trying; with this key,
    working a name out takes every token, and whoever knows them all can
    open every page anyway.  The file of an event is thus exactly as safe as
    the pages of the guests.  A new invitation renames every calendar file,
    which is harmless: the pages are built together with them.
    """
    return hashlib.sha256("\n".join(sorted(tokens)).encode("utf-8")).digest()


def calendar_name(key: bytes, content: bytes) -> str:
    """`<HMAC-SHA256(key, content)[:16]>.ics`: the published name of a calendar file."""
    digest = hmac.new(key, content, hashlib.sha256).hexdigest()
    return f"{digest[:CALENDAR_NAME_DIGITS]}{CALENDAR_SUFFIX}"


def calendar_files(data: Data) -> dict[str, CalendarFile]:
    """Event id -> its calendar file, for every event that at least one
    invitation sees, in the order of their start.  An event nobody sees gets
    no file."""
    settings = page_settings(data.site)
    key = calendar_key(invitation["token"] for invitation in data.invitations)
    files = {}
    for event in page_tools.calendar_events(data.site, data.invitations, settings):
        content = event_calendar(data.site["mediaDir"], event)
        files[event["id"]] = CalendarFile(calendar_name(key, content), content)
    return files


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


#: Markup that `html.parser` and browsers read differently (a browser ends
#: `<![CDATA[ … ]]>` and `<? … ?>` at the first `>`, the parser may not).
_UNSAFE_MARKUP_RE = re.compile(r"<!\[|<\?")


def find_unsafe_markup(document: str) -> tuple[int, str] | None:
    """(line, message) for the first `<![…` or `<?…`, which are not allowed."""
    match = _UNSAFE_MARKUP_RE.search(document)
    if match is None:
        return None
    line = document.count("\n", 0, match.start()) + 1
    return line, f"'{match.group(0)}…' (CDATA sections, processing instructions) is not allowed"


def strip_html_comments(document: str, name: str = "template") -> str:
    """Remove every HTML comment, so that template notes are never published."""
    unsafe = find_unsafe_markup(document)
    if unsafe:
        raise TemplateError(unsafe[1], unsafe[0], name)
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
_SVG_ANIMATION_ELEMENTS = frozenset(
    {"set", "animate", "animatemotion", "animatetransform", "animatecolor"}
)
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
#: The one of them that may hold an absolute URL of the site itself.
_META_SITE_URL_KEY = "og:image"
_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
#: Schemes that are named in messages; anything else before a ':' may be text.
_KNOWN_SCHEMES = frozenset(
    {"http", "https", "mailto", "tel", "sms", "ftp", "blob", "file", "javascript",
     "vbscript", "data", "about", "ws", "wss"}
)
#: Text that a browser may read as markup where the parser sees plain text
#: (`<svg><title><img …>`): a '<' followed by a letter, '/', '!' or '?'.
_MARKUP_LIKE_RE = re.compile(r"<[A-Za-z/!?]")
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
        # e.g. mailto: - the rest is data, and so is an unknown "scheme"
        name = scheme.group(1).lower()
        return f"{name}:…" if name in _KNOWN_SCHEMES else "…:"
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
    path = parts.path.lower()
    if "\\" in url or any(code in path for code in ("%2e", "%2f", "%5c")):
        return False  # nothing that a browser may normalise into another path
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
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

    def __init__(
        self,
        exists: Callable[[str], bool],
        is_dir: Callable[[str], bool] | None = None,
        base: str | None = None,
    ):
        self.exists = exists
        self.is_dir = is_dir if is_dir is not None else (lambda _path: False)
        #: Directory of the style sheet being checked ("" is the root); None
        #: for HTML, where relative URLs are not allowed at all.
        self.base = base
        self.problems: list[tuple[int, str]] = []

    def show_path(self, url: str) -> str:
        """A local or relative URL for a message.

        Only the directories that exist in the output and a known file
        extension are shown: the rest may be text that a template put into a
        path by mistake.
        """
        path = _clean_url(url).split("#", 1)[0].split("?", 1)[0]
        absolute = path.startswith("/")
        segments = [segment for segment in path.split("/") if segment]
        kept: list[str] = []
        if absolute:
            for segment in segments:
                candidate = "/".join([*kept, unquote(segment)])
                if not _PLAIN_URL_RE.match(segment) or not self.is_dir(candidate):
                    break
                kept.append(segment)
        lead = "/" if absolute else ""
        if len(kept) == len(segments):
            return lead + "/".join(kept) + ("/" if kept else "")
        extension = os.path.splitext(segments[-1])[1].lower()
        if extension not in ASSET_EXTENSIONS and extension != ".html":
            extension = ""
        return lead + "/".join([*kept, "…"]) + extension

    def _relative_to_stylesheet(self, line: int, where: str, url: str) -> None:
        """A relative `url()` of a CSS file: resolved against the file itself."""
        path = unquote(url.split("#", 1)[0].split("?", 1)[0])
        target = posixpath.normpath(posixpath.join(self.base or "", path))
        if target == ".." or target.startswith(("../", "/")):
            self.problem(line, f"{where}: '{self.show_path(url)}' points outside the output")
        elif not self.exists(target):
            self.problem(
                line,
                f"{where}: '{self.show_path(url)}' (relative to the style sheet) "
                "does not exist in the output",
            )

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
                    line, f"{where}: '{self.show_path(url)}' is not a plain absolute path"
                )
            elif not self.exists(target):
                self.problem(
                    line, f"{where}: '{self.show_path(url)}' does not exist in the output"
                )
        elif kind == "empty":
            self.problem(line, f"{where}: the URL is empty")
        elif kind == "relative" and self.base is not None:
            self._relative_to_stylesheet(line, where, url)
        elif kind == "relative":
            self.problem(
                line,
                f"{where}: relative path '{self.show_path(url)}'; {what} must be "
                "absolute from the site root (start with '/')",
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
            shown = _show_url(url) if kind == "scheme" else self.show_path(url)
            self.problem(line, f"{where}: URL '{shown}' is not allowed")

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
            shown = _show_url(url) if kind in ("scheme", "data") else self.show_path(url)
            self.problem(line, f"{where}: link '{shown}' is not allowed")


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


_CSS_CONTINUATION_RE = re.compile(r"\\[\n\r\f]")
#: A string that starts with a URL scheme (`image-set('http:host/x')`); "12:30"
#: and "Note: text" are not URLs, `data:` is judged where it is used.
_CSS_SCHEME_STRING_RE = re.compile(r"(?!data:)[a-z][a-z0-9+.-]*:(?=\S)", re.IGNORECASE)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\([^\n])")
_CSS_NAME = r"(?:[\w-]|\\[0-9a-fA-F]{1,6}\s?|\\[^\n0-9a-fA-F])+"
_CSS_FUNCTION_RE = re.compile(rf"({_CSS_NAME})\(")
_CSS_AT_RULE_RE = re.compile(rf"@({_CSS_NAME})")
_CSS_PLAIN_NAME_RE = re.compile(r"[\w-]+\Z")


def _css_unescape(text: str) -> str:
    def decode(match: re.Match) -> str:
        if match.group(1):
            code = int(match.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF else "\ufffd"
        return match.group(2)

    return _CSS_ESCAPE_RE.sub(decode, text)


def check_css(
    text: str, policy: _UrlPolicy, where: str = "CSS", first_line: int = 1
) -> None:
    """`url()`, `@import` and URL-like strings of a style sheet (or of a
    `style` attribute) must point at local files.

    A browser decodes CSS escapes (`u\\72l(…)` is `url(…)`), so escapes are
    refused wherever they could hide a URL: in function and at-rule names and
    inside `url()` / `@import`; other strings are checked decoded as well.
    """
    # first of all: a backslash before a line break continues a string, which
    # would split a URL across lines and hide it from everything below
    continuation = _CSS_CONTINUATION_RE.search(text)
    if continuation:
        policy.problem(
            first_line + text.count("\n", 0, continuation.start()),
            f"{where}: a backslash before a line break is not allowed in CSS",
        )
        return
    text = _CSS_NAMESPACE_RE.sub(_blank, strip_css_comments(text))

    def line_at(offset: int) -> int:
        return first_line + text.count("\n", 0, offset)

    def check_url(offset: int, what: str, value: str, images: bool) -> None:
        if "\\" in value and not _clean_url(value).lower().startswith("data:"):
            policy.problem(
                line_at(offset), f"{where} {what}: CSS escapes are not allowed in URLs"
            )
        else:
            policy.check_resource(line_at(offset), f"{where} {what}", value, data_images=images)

    without_strings = _CSS_STRING_RE.sub(lambda match: " " * len(match.group(0)), text)
    for match in _CSS_AT_RULE_RE.finditer(without_strings):
        if "\\" in match.group(1):
            policy.problem(
                line_at(match.start()), f"{where}: CSS escapes are not allowed in at-rule names"
            )
    for match in _CSS_FUNCTION_RE.finditer(without_strings):
        name = match.group(1)
        if "\\" in name and _CSS_PLAIN_NAME_RE.match(_css_unescape(name)):
            policy.problem(
                line_at(match.start()),
                f"{where}: CSS escapes are not allowed in function names",
            )

    covered: list[tuple[int, int]] = []
    for match in _CSS_IMPORT_RE.finditer(text):
        covered.append(match.span())
        check_url(match.start(), "@import", _css_string_value(match.group(1)), False)
    for match in _CSS_URL_RE.finditer(text):
        covered.append(match.span())
        before = text[max(0, match.start() - 40) : match.start()]
        is_import = _CSS_IMPORT_BEFORE_RE.search(before) is not None
        check_url(
            match.start(),
            "@import" if is_import else "url()",
            _css_string_value(match.group(1)),
            not is_import,
        )
    for match in _CSS_STRING_RE.finditer(text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        value = _css_string_value(match.group(0))
        if any(
            _EXTERNAL_TEXT_RE.match(candidate.strip())
            or _CSS_SCHEME_STRING_RE.match(candidate.strip())
            for candidate in (value, _css_unescape(value))
        ):
            policy.problem(
                line_at(match.start()),
                f"{where}: external URL '{_show_url(_css_unescape(value))}' in a string; "
                "every resource must be a local file",
            )


class _HtmlChecker(HTMLParser):
    """Walks one HTML document and applies the URL and script rules."""

    def __init__(self, policy: _UrlPolicy, base_url: str = ""):
        # text is reported as written: an escaped "&lt;!--" in a guest's note
        # is text, not the start of a comment (attribute values are decoded)
        super().__init__(convert_charrefs=False)
        self.policy = policy
        #: Address of the site: `og:image` may be an absolute URL below it.
        self.base_url = base_url
        self._in_script = False

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
        if tag == "style":
            # also closes a gap: inside <svg> a browser parses the content of
            # <style> as markup, while the parser takes it for text
            policy.problem(
                line, "<style> elements are not allowed; styles must be files in /assets/"
            )
        if tag == "script":
            self._in_script = bool(values.get("src", "").strip())
            if not self._in_script:
                policy.problem(
                    line, "inline <script> is not allowed; scripts must be files in /assets/"
                )
            if "href" in values or "xlink:href" in values:
                policy.problem(line, "<script href> (an SVG script) is not allowed")
        if tag in _SVG_ANIMATION_ELEMENTS and values.get(
            "attributename", ""
        ).strip().lower() in ("href", "xlink:href"):
            policy.problem(line, f"<{tag}>: animating 'href' is not allowed")

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
        if values.get("http-equiv", "").strip().lower() == "refresh":
            self.policy.problem(line, '<meta http-equiv="refresh"> is not allowed')
            return
        if key in _META_URL_KEYS:
            if key == _META_SITE_URL_KEY and self.base_url:
                # services that show link previews want an absolute URL; it is
                # the one place where the address of the site itself may appear,
                # and what follows it must be a file of the output all the same
                prefix = f"{self.base_url}/"
                if _clean_url(content).startswith(prefix):
                    content = _clean_url(content)[len(prefix) - 1 :]
            self.policy.check_resource(line, f"<meta {key}>", content)
            return
        external = _EXTERNAL_TEXT_RE.search(content)
        if external:
            shown = _show_url(content[external.start() :].split()[0])
            self.policy.problem(
                line, f"<meta content>: external URL '{shown}' is not allowed"
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            # a browser may read this as markup (inside <svg>), the parser does not
            if data.strip():
                self.policy.problem(self.line, "<script src> must be empty")
        elif _MARKUP_LIKE_RE.search(data):
            # Escaped text ("&lt;img") never gets here as '<'.  A literal '<'
            # in text means that the parser took something for text - the
            # content of <title>/<textarea> inside <svg>, an unterminated
            # comment - which a browser may read as elements.
            self.policy.problem(
                self.line, "markup-like text is not allowed (write '<' as '&lt;')"
            )

    def handle_decl(self, decl: str) -> None:
        if not decl.strip().lower().startswith("doctype"):
            self.policy.problem(self.line, "'<!…>' declarations are not allowed")

    def handle_comment(self, data: str) -> None:
        self.policy.problem(self.line, "HTML comment left in the output")


def check_html(
    document: str,
    exists: Callable[[str], bool],
    is_dir: Callable[[str], bool] | None = None,
    base_url: str = "",
) -> list[tuple[int, str]]:
    """Problems of one HTML document as (line, message) pairs.

    `exists` tells whether a file (relative path with '/') is in the output,
    `is_dir` does the same for directories (only used to word the messages).
    Whatever the parser may read differently from a browser is an error.

    With `base_url` (the address of the site, no trailing '/') the content of
    `<meta property="og:image">` may be an absolute URL that starts with
    exactly that address; every other external URL stays an error.
    """
    policy = _UrlPolicy(exists, is_dir)
    leftover = _LEFTOVER_RE.search(document)
    if leftover:
        policy.problem(
            document.count("\n", 0, leftover.start()) + 1,
            f"template syntax left in the output: '{_shorten(leftover.group(0), 20)}'",
        )
    unsafe = find_unsafe_markup(document)
    if unsafe:
        policy.problem(*unsafe)
    checker = _HtmlChecker(policy, base_url)
    checker.feed(document)
    checker.close()
    return policy.problems


def check_stylesheet(
    text: str,
    exists: Callable[[str], bool],
    is_dir: Callable[[str], bool] | None = None,
    base: str = "",
) -> list[tuple[int, str]]:
    """Problems of one CSS file as (line, message) pairs.

    `base` is the directory of the file inside the output: relative `url()`s
    are resolved against it and must stay inside the output.
    """
    policy = _UrlPolicy(exists, is_dir, base)
    check_css(text, policy)
    return policy.problems


#: Markup of an SVG file that pulls in other documents or defines entities
#: (an entity can hide anything from the checks below).
_SVG_UNSAFE_MARKUP = (
    (re.compile(r"<!ENTITY", re.IGNORECASE), "'<!ENTITY' declarations are not allowed"),
    (re.compile(r"<\?xml-stylesheet", re.IGNORECASE), "'<?xml-stylesheet' is not allowed"),
)
_SVG_URL_ATTRIBUTES = frozenset({"href", "xlink:href", "src"})
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
#: Prefixes that mean SVG itself even when the file declares nothing.
_SVG_OWN_PREFIXES = frozenset({"svg", "xlink"})
#: Prefixes that spell HTML by habit; the namespace they are bound to decides,
#: these are only what an undeclared prefix is read as.
_SVG_HTML_PREFIXES = frozenset({"xhtml", "html"})
#: Attributes that a browser acts on but the URL rules here cannot follow:
#: a list of candidate URLs, and a different base for every relative one.
_SVG_REFUSED_ATTRIBUTES = frozenset({*_SRCSET_ATTRIBUTES, "xml:base"})


def _local_name(tag: str) -> str:
    """`svg:script` -> `script`: a namespace prefix does not change the element."""
    return tag.rsplit(":", 1)[-1]


class _SvgChecker(HTMLParser):
    """A cheap look at an SVG file: nothing active, nothing external."""

    def __init__(self, policy: _UrlPolicy) -> None:
        super().__init__(convert_charrefs=True)
        self.policy = policy
        #: `xmlns:<prefix>` seen so far -> namespace URI.  Scopes are ignored:
        #: a prefix bound twice to different namespaces in one file is not
        #: something an honest picture does.
        self.namespaces: dict[str, str] = {}

    def _foreign_element(self, tag: str) -> str | None:
        """Message when the element is not SVG, judged by its namespace.

        The prefix alone means nothing (`<x:div xmlns:x="…/xhtml">` is HTML),
        so the declared namespace decides; an undeclared prefix is read by its
        spelling and, when that says nothing either, refused all the same -
        whatever namespace it turns out to be, it is not described here.
        """
        if ":" not in tag:
            return None
        prefix = tag.rsplit(":", 1)[0].lower()
        uri = self.namespaces.get(prefix, "").strip().rstrip("/")
        if uri == _XHTML_NAMESPACE or (not uri and prefix in _SVG_HTML_PREFIXES):
            return f"<{tag}>: HTML elements are not allowed in SVG files"
        if uri in (_SVG_NAMESPACE, _XLINK_NAMESPACE):
            return None
        if not uri and prefix in _SVG_OWN_PREFIXES:
            return None
        return f"<{tag}>: elements of another namespace are not allowed in SVG files"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        problem = self.policy.problem
        local = _local_name(tag)
        values = {name: value or "" for name, value in attrs}
        for name, value in values.items():
            if name.startswith("xmlns:") and len(name) > 6:
                self.namespaces[name[6:].lower()] = value
        if local in ("script", "foreignobject", "style"):
            problem(line, f"<{tag}> is not allowed in SVG files")
        else:
            foreign = self._foreign_element(tag)
            if foreign:
                problem(line, foreign)
        if local in _SVG_ANIMATION_ELEMENTS and values.get(
            "attributename", ""
        ).strip().lower() in ("href", "xlink:href"):
            problem(line, f"<{tag}>: animating 'href' is not allowed")
        for name, value in values.items():
            if name.startswith("on") and len(name) > 2:
                problem(line, f"<{tag} {name}>: event handlers are not allowed")
            elif name in _SVG_REFUSED_ATTRIBUTES:
                problem(line, f"<{tag} {name}>: '{name}' is not allowed in SVG files")
            elif name in _SVG_URL_ATTRIBUTES:
                kind, url = _classify_url(value)
                if kind in ("external", "script", "malformed"):
                    problem(line, f"<{tag} {name}>: external URL '{_show_url(url)}' is not allowed")
            elif name == "style" or "(" in value or "\\" in value:
                # `style` and the presentation attributes (fill="url(…)") are CSS
                check_css(value, self.policy, f"<{tag} {name}>", line)


def check_svg(
    text: str,
    exists: Callable[[str], bool] | None = None,
    is_dir: Callable[[str], bool] | None = None,
    base: str = "",
) -> list[tuple[int, str]]:
    """Problems of one SVG file as (line, message) pairs.

    Not allowed: scripts, `<style>` elements, event handlers, `<foreignObject>`,
    elements of the XHTML namespace, `<!ENTITY` declarations,
    `<?xml-stylesheet`, animation of `href`, `srcset` and `xml:base`, external
    references in `href` / `src` and in the CSS of `style` and presentation
    attributes.  Elements are recognised by their local name (`svg:script` is a
    script); namespace URIs in `xmlns` are not references.

    `exists` and `is_dir` describe the output as for `check_html`, `base` is the
    directory of the file; without `exists` local URLs are not looked up.
    """
    policy = _UrlPolicy(exists if exists is not None else (lambda _path: True), is_dir, base)
    for pattern, message in _SVG_UNSAFE_MARKUP:
        match = pattern.search(text)
        if match:
            policy.problem(text.count("\n", 0, match.start()) + 1, message)
    checker = _SvgChecker(policy)
    checker.feed(text)
    checker.close()
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


#: Directories that `assets/` may hold besides the media directory.
ASSET_DIRECTORIES = frozenset({"fonts"})
#: The published name of a media file: `<sha256[:16]>.<ext>`.
_PUBLISHED_MEDIA_RE = re.compile(r"[0-9a-f]{16}\.[a-z0-9]+\Z")


def _media_path(name: str) -> str:
    """A file of the media directory for a message: a published name as it
    is, any other name (it may be a name from the data) cut to its type."""
    shown = name if _PUBLISHED_MEDIA_RE.match(name) else "…" + os.path.splitext(name)[1].lower()
    return f"{ASSETS_DIRNAME}/<mediaDir>/{shown}"


def _check_output_tree(
    files: dict[str, int],
    directories: Sequence[str],
    pages: Iterable[str],
    media_dir: str = "",
    media: Collection[str] = (),
    calendars: Collection[str] = (),
) -> list[str]:
    """Allow-list of the output: only what the site needs is published.

    `pages` are the tokens of the invitations: `i/<token>/` holds the page
    and nothing else.  `assets/<media_dir>/` holds exactly the published
    names `media` and the calendar files `calendars` (and does not exist
    without them); besides it `assets/` may only hold the files of the code
    and the directories `ASSET_DIRECTORIES`.
    """
    problems: list[str] = []
    media_prefix = f"{ASSETS_DIRNAME}/{media_dir}/" if media_dir else None
    expected_media = (
        {f"{media_prefix}{name}" for name in (*media, *calendars)} if media_prefix else set()
    )
    allowed_asset_dirs = {f"{ASSETS_DIRNAME}/{name}" for name in ASSET_DIRECTORIES}
    tokens = list(pages)
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
        elif parts[0] == ASSETS_DIRNAME:
            media_path = media_prefix.rstrip("/") if media_prefix else None
            if relative == media_path:
                allowed = bool(expected_media)
                shown = f"{ASSETS_DIRNAME}/<mediaDir>"
            elif media_path and relative.startswith(media_prefix):
                allowed, shown = False, f"{ASSETS_DIRNAME}/<mediaDir>/…"
            else:
                allowed = "/".join(parts[:2]) in allowed_asset_dirs
                shown = relative
            if not allowed:
                problems.append(
                    f"{shown}: unexpected directory (only "
                    f"{', '.join(sorted(ASSET_DIRECTORIES))}/ and the media directory with "
                    f"the media the pages show are allowed in {ASSETS_DIRNAME}/)"
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
                    f"{relative}: unexpected file (only <token>/{PAGE_FILE} is allowed in "
                    f"{PAGES_DIRNAME}/)"
                )
        elif media_prefix and relative.startswith(media_prefix):
            if relative not in expected_media:
                problems.append(
                    f"{_media_path(relative[len(media_prefix):])}: unexpected file (only the "
                    "media the pages show and the calendar files of the events are "
                    "published, under the hash of their contents)"
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

    required = [
        *STUB_OUTPUTS,
        HEADERS_FILE,
        ROBOTS_FILE,
        *sorted(expected_pages),
    ]
    for relative in required:
        if relative not in files:
            problems.append(f"{relative}: missing from the output")
    for relative in sorted(expected_media):
        if relative not in files:
            problems.append(f"{_media_path(relative[len(media_prefix):])}: missing from the output")
    return problems


def check_output(
    out_dir: Path | str,
    pages: Iterable[str],
    base_url: str = "",
    media_dir: str = "",
    media: Collection[str] = (),
    calendars: Collection[str] = (),
) -> OutputStats:
    """Check a finished output directory; raises `OutputError` with every problem.

    * only the expected files are present (see `_check_output_tree`: `pages`
      are the tokens, `media` and `calendars` the published names in the
      media directory), none of them larger than `MAX_FILE_BYTES`;
    * HTML: no template syntax or comments left, no external resources, no
      inline scripts or event handlers, no embedded documents; external links
      only to the map services and with `target`/`rel`; every local URL
      resolves to a file of the output;
    * CSS: `url()` and `@import` point at local files that exist (relative
      URLs of a CSS file are resolved against that file); no CSS escapes that
      could hide a URL;
    * SVG: no scripts, styles sheets, event handlers, `<foreignObject>`,
      entities or external references (see `check_svg`).

    `base_url` is passed on to `check_html`.  Messages never quote page text,
    tokens in paths are hidden (`i/…`) and the address of the site is masked.
    """
    root = Path(out_dir)
    try:
        files, directories, problems = _scan_output(root)
    except OSError as exc:
        raise BuildError(
            f"cannot read the output directory {display_path(root)}: {exc.strerror}"
        ) from None
    problems += _check_output_tree(files, directories, pages, media_dir, media, calendars)
    problems = [_redact_paths(problem) for problem in problems]

    known_dirs = set(directories)
    # problem -> [(file as shown, line, file)], in order of appearance; the
    # shown names of the pages are all alike (`i/…/index.html`)
    found: dict[str, list[tuple[str, int, str]]] = {}
    for relative, size in files.items():
        extension = os.path.splitext(relative)[1].lower()
        if extension not in (".html", ".css", ".svg") or size > MAX_FILE_BYTES:
            continue
        shown = _redact_paths(relative)
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{shown}: not valid UTF-8 (at byte {exc.start})")
            continue
        except OSError as exc:
            raise BuildError(f"cannot read {shown}: {exc.strerror}") from None
        if extension == ".html":
            file_problems = check_html(
                text, files.__contains__, known_dirs.__contains__, base_url
            )
        elif extension == ".css":
            file_problems = check_stylesheet(
                text, files.__contains__, known_dirs.__contains__, posixpath.dirname(relative)
            )
        else:
            file_problems = check_svg(
                text, files.__contains__, known_dirs.__contains__, posixpath.dirname(relative)
            )
        for line, message in file_problems:
            found.setdefault(_redact_paths(message), []).append((shown, line, relative))

    for message, places in found.items():
        first_file, first_line, _first = places[0]
        distinct = len({name for _shown, _line, name in places})
        if distinct == 1:
            problems.append(f"{first_file} line {first_line}: {message}")
        else:
            problems.append(
                f"{distinct} files: {message}; first: {first_file} line {first_line}"
            )

    if problems:
        # the address of the site may appear in a message about og:image
        problems = [mask_site(problem, base_url) for problem in problems]
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
#: name with a dot (`i/index.html`) is a file, not a token.  Tokens may be as
#: short as a word, so the whole component is hidden, whatever its length.
#: `I/` counts as well: on a file system that ignores letter case it is the
#: same directory.
_TOKEN_IN_PATH_RE = re.compile(
    rf"(?<![A-Za-z0-9_.-])((?i:{PAGES_DIRNAME})[/\\])[A-Za-z0-9_-]+(?![A-Za-z0-9_.-])"
)
#: A directory entry that may be a token or a media directory: token
#: characters only (no dot, so not a file name), at least as long as a token.
_TOKEN_LIKE_RE = re.compile(rf"[A-Za-z0-9_-]{{{MIN_TOKEN_LENGTH},}}\Z")
#: What takes the place of a hidden name.
NAME_MASK = "…"


def _redact_paths(text: Any) -> str:
    """Hide invitation tokens that may appear inside file system paths:
    `i/<token>/index.html` becomes `i/…/index.html`."""
    return _TOKEN_IN_PATH_RE.sub(rf"\1{NAME_MASK}", str(text))


#: What takes the place of the address of the site in a message.
SITE_MASK = "<site>"


def mask_site(text: str, base_url: str) -> str:
    """Hide the address of the site (`--base-url`) in a message.

    The address is the one external URL the output may legitimately carry
    (`og:image`), so a problem message can quote it - and with it the host,
    which is exactly what the log must not repeat.  The whole address and the
    bare host (with or without a scheme and a port) become `<site>`; long URLs
    reach messages shortened by `_show_url`, so that form is masked as well.
    """
    if not base_url:
        return text
    address = base_url.rstrip("/")
    host = urlsplit(address).hostname or ""
    forms = {address, _shorten(address)}
    patterns = [re.escape(form) for form in sorted(forms, key=len, reverse=True)]
    if host:
        patterns.append(
            rf"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//{re.escape(host)}(?::[0-9]+)?"
        )
        patterns.append(rf"{re.escape(host)}(?::[0-9]+)?")
    return re.sub("|".join(patterns), SITE_MASK, text, flags=re.IGNORECASE)


def _mask_name(name: str, *, pages: bool = False) -> str:
    """A directory entry for an error message.

    Any name made of token characters only (no dot: not a file) may be a token
    or a media directory, so it is hidden completely, and so is every entry of
    a pages directory (`pages`: the directory is named `i` in any letter case),
    even one that is also a known name of the output.
    """
    if pages or _TOKEN_LIKE_RE.match(name):
        return NAME_MASK
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

    The message never shows a name that may be a token or a media directory
    (`--out` may point inside a previous build by mistake): the entries of a
    directory named `i` (in any letter case) and every name of 5 or more token
    characters without a dot are shown as `…` (see `_mask_name`).
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
    pages = Path(os.path.abspath(out)).name.casefold() == PAGES_DIRNAME
    if unexpected:
        listed = ", ".join(f"'{_mask_name(name, pages=pages)}'" for name in unexpected[:3])
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
                # a known name of the output, unless it is inside a pages directory
                f"previous build output ('{NAME_MASK if pages else entry.name}' is not "
                f"{expected}); {advice}"
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


def _make_media_dir(destination: Path) -> None:
    """Create `assets/<mediaDir>/`; it must not exist yet."""
    try:
        destination.mkdir(parents=True)
    except FileExistsError:
        raise BuildError(
            f"{SITE_FILE}: field 'mediaDir' collides with an entry of "
            f"{ASSETS_DIRNAME}/; the media directory needs a name of its own"
        ) from None
    except OSError as exc:
        raise BuildError(f"cannot create the media directory: {exc.strerror}") from None


def copy_media(
    media: Mapping[str, MediaFile], media_dir: Path | str, destination: Path
) -> int:
    """Copy the media files that the pages show - and nothing else - into
    `assets/<mediaDir>/` under their published names; returns the number of
    files written.  Files with equal contents and type share one name and are
    written once; without files the directory is not created at all."""
    names = {entry.published: entry.name for entry in media.values()}
    if not names:
        return 0
    _make_media_dir(destination)
    for published, name in sorted(names.items()):
        source = Path(media_dir) / name
        if source.is_symlink():
            raise BuildError(
                f"symbolic links are not allowed in media: {display_path(source)}"
            )
        try:
            shutil.copyfile(source, destination / published)
        except OSError as exc:
            raise BuildError(
                f"cannot copy the media file {display_path(source)}: "
                f"{exc.strerror or type(exc).__name__}"
            ) from None
    return len(names)


def load_palette(assets_dir: Path) -> "gen_assets.Palette":
    """The colours of the generated images, from the tokens of `app.css`."""
    path = Path(assets_dir) / TOKENS_FILE
    if not Path(assets_dir).is_dir():
        raise BuildError(f"assets directory not found: {display_path(assets_dir)}")
    if path.is_symlink():
        raise BuildError(
            f"the style sheet must not be a symbolic link: {display_path(path)}"
        )
    try:
        return gen_assets.load_palette(path)
    except gen_assets.TokenError as exc:
        raise BuildError(
            f"{ASSETS_DIRNAME}/{exc} (the icon and the link preview image are "
            "generated from the design tokens)"
        ) from None


def check_reserved_asset_names(assets_dir: Path) -> None:
    """`assets/favicon.*` and `assets/og.*` belong to the build."""
    reserved = {name for names in SITE_IMAGE_NAMES.values() for name in names}
    try:
        with os.scandir(assets_dir) as entries:
            taken = sorted(entry.name for entry in entries if entry.name.lower() in reserved)
    except OSError:
        return  # reported when the assets are copied
    if taken:
        raise BuildError(
            f"'{taken[0]}' in {display_path(assets_dir)} collides with an image that the "
            "build writes; to replace a generated image put the file into the media "
            "directory instead"
        )


def write_site_images(
    images: SiteImages, destination: Path, palette: "gen_assets.Palette | None"
) -> int:
    """Write the icon and the link preview image into `assets/`; returns how
    many of them came from the media directory (the rest is generated)."""
    generators = {"favicon": gen_assets.favicon_svg, "og": gen_assets.og_png}
    copied = 0
    for role, image in images._asdict().items():
        target = destination / image.name
        if image.source is None:
            if palette is None:  # pragma: no cover - the caller loads it when needed
                raise BuildError("the design tokens were not loaded")
            _write_file(target, generators[role](palette))
            continue
        if image.source.is_symlink():
            raise BuildError(
                f"symbolic links are not allowed in media: {display_path(image.source)}"
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image.source, target)
        except OSError as exc:
            raise BuildError(
                f"cannot copy the media file {display_path(image.source)}: "
                f"{exc.strerror or type(exc).__name__}"
            ) from None
        copied += 1
    return copied


def mask_base_url(base_url: str) -> str:
    """The address of the site for the log: the scheme only."""
    return f"{base_url.split(':', 1)[0].lower()}://…"


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def _read_source(path: Path, what: str) -> str:
    if path.is_symlink():
        raise BuildError(
            f"the {what} must not be a symbolic link: {display_path(path)}"
        )
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


def load_fragments(directory: Path) -> Fragments:
    """Read and parse every fragment below `directory` (`fragments/`).

    A missing directory means that there are no fragments.  Dot-files and
    dot-directories are skipped; symbolic links, other kinds of files and any
    extension but `.html` are an error.  Nothing is read after this.
    """
    directory = Path(directory)
    if directory.is_symlink():
        raise BuildError(
            f"the fragments directory must not be a symbolic link: {display_path(directory)}"
        )
    if not directory.exists():
        return Fragments()
    if not directory.is_dir():
        raise BuildError(f"not a directory: {display_path(directory)}")
    sources: dict[str, str] = {}
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError as exc:
            raise BuildError(
                f"cannot read the fragments directory {display_path(current)}: {exc.strerror}"
            ) from None
        for path in entries:
            if path.name.startswith("."):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise BuildError(
                    f"symbolic links are not allowed in fragments: {display_path(path)}"
                )
            if stat.S_ISDIR(mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(mode):
                raise BuildError(f"not a regular file in fragments: {display_path(path)}")
            relative = path.relative_to(directory).as_posix()
            if path.suffix != FRAGMENT_SUFFIX:
                raise BuildError(
                    f"{FRAGMENTS_DIRNAME}/{relative}: only {FRAGMENT_SUFFIX} files "
                    "are allowed in fragments"
                )
            sources[relative[: -len(FRAGMENT_SUFFIX)]] = _read_source(path, "fragment")
    return Fragments(sources)


def load_template(path: Path) -> Template:
    """Read and parse the page template with the fragments next to it."""
    fragments = load_fragments(path.parent / FRAGMENTS_DIRNAME)
    return parse_template(_read_source(path, "template"), path.name, fragments)


#: The only fields the stub may use: they say nothing about the site.
STUB_FIELDS = ("faviconPath", "faviconType")


def load_stub(path: Path, images: dict | None = None) -> str:
    """Read the stub page (published as `index.html` and `404.html`).

    The stub is the same for everybody and gets no data: the only template
    syntax allowed in it are the placeholders of `STUB_FIELDS` (the icon, see
    `site_images_context`).  HTML comments are removed, as on the pages.
    """
    source = _read_source(path, "stub page")
    problem = (
        "must not contain template syntax other than "
        + " and ".join("{{" + field + "}}" for field in STUB_FIELDS)
        + " (the stub gets no data)"
    )
    try:
        nodes, _needs_item = _parse(source, path.name)
    except TemplateError as exc:
        raise TemplateError(f"{problem}: {exc.reason}", exc.line, path.name) from None

    def shown(node: Any) -> str:
        if isinstance(node, _Var):
            return f"'{{{{{node.path}}}}}'"
        if isinstance(node, _Include):
            return "'<!-- include:… -->'"
        return f"'<!-- {node.kind}:… -->'"

    found = [
        f"line {node.line}: {shown(node)}"
        for node in nodes
        if isinstance(node, (_Block, _Include))
        or (isinstance(node, _Var) and node.path not in STUB_FIELDS)
    ]
    if found:
        raise TemplateError(f"{problem}: " + "; ".join(found[:5]), None, path.name)
    template = parse_template(source, path.name)  # no includes: no fragments needed
    fields = images if images is not None else site_images_context()
    rendered = template.render({field: fields[field] for field in STUB_FIELDS})
    return strip_html_comments(rendered, path.name)


def render_pages(
    template: Template,
    invitations: Sequence[dict],
    trees: Sequence[dict],
) -> list[tuple[str, str]]:
    """Render the page tree of every invitation; returns (token, html) pairs.

    `trees` are the page trees in the order of `invitations` (`page_trees`).
    Rendering happens entirely in memory: a broken template must not leave a
    half-written output directory behind.  HTML comments are removed from the
    result, so that notes in the template are never published.
    """
    pages: list[tuple[str, str]] = []
    failures: dict[str, list[str]] = {}  # message -> labels, in order of appearance
    for index, (invitation, tree) in enumerate(zip(invitations, trees), start=1):
        label = invitation_label(index)
        try:
            page = template.render(tree)
            pages.append((invitation["token"], strip_html_comments(page, template.name)))
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


def write_calendars(
    destination: Path, calendars: Mapping[str, CalendarFile], media: Collection[str] = ()
) -> int:
    """Write the calendar file of every event into the media directory
    `destination` (`assets/<mediaDir>/`), under its published name; `media`
    are the published names of the media files already there.  Returns the
    number of files written."""
    names = [entry.name for entry in calendars.values()]
    taken = set(media)
    for name in names:
        # a safeguard: the types differ, but a clash would lose a file
        if name in taken:
            raise BuildError(
                f"the calendar file {_media_path(name)} has the name of another file of "
                "the media directory"
            )
        taken.add(name)
    if names and not media:
        # no media file is published: `copy_media` did not create it
        _make_media_dir(destination)
    for entry in calendars.values():
        _write_file(destination / entry.name, entry.content)
    return len(names)


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
    base_url: str = "",
) -> OutputStats:
    """Validate the data, write the output directory and check the result.

    The output is assembled in a temporary directory and only replaces
    `out_dir` when every check has passed.  The log gets counters and paths,
    never data.

    `base_url` is the address of the published site (see `parse_base_url`).
    It makes the URL of the link preview image absolute, which is what most
    services that show previews need; it goes into that `<meta>` tag only and
    is never logged in full.
    """
    report = report if report is not None else Report()
    assets_dir = code_dir / ASSETS_DIRNAME
    check_out_dir(out_dir, code_dir=code_dir, data_dir=data_dir, media_dir=media_dir)

    data = load_data(data_dir, media_dir, report, assets_dir=assets_dir)
    log(
        f"build: {len(data.invitations)} invitation(s), {report.media_files} media "
        f"file(s) validated (data: {display_path(data_dir)}, media: {display_path(media_dir)})"
    )
    summary = short_token_summary(data.invitations)
    if summary:
        log(f"build: {summary}")

    images = report.site_images
    check_reserved_asset_names(assets_dir)
    generated = [image.name for image in images if image.source is None]
    palette = load_palette(assets_dir) if generated else None
    images_context = site_images_context(images, base_url)

    template = load_template(code_dir / TEMPLATE_FILE)
    stub = load_stub(code_dir / STUB_FILE, images_context).encode("utf-8")
    calendars = calendar_files(data)
    settings = page_settings(data.site, images_context, data.media, calendars)
    trees, _usage = page_tools.build_pages(data.site, data.invitations, settings)
    pages = render_pages(template, data.invitations, trees)
    media_name = data.site["mediaDir"]
    published = {entry.published for entry in data.media.values()}

    with _StagedOutput(out_dir, warn=report.warn) as stage:
        written = write_pages(stage, pages)
        for name in STUB_OUTPUTS:
            _write_file(stage / name, stub)
        assets = copy_assets(assets_dir, stage / ASSETS_DIRNAME, ignore=is_styleguide_asset)
        write_site_images(images, stage / ASSETS_DIRNAME, palette)
        media = copy_media(data.media, media_dir, stage / ASSETS_DIRNAME / media_name)
        events = write_calendars(stage / ASSETS_DIRNAME / media_name, calendars, published)
        _write_file(stage / HEADERS_FILE, HEADERS_TEXT.encode("utf-8"))
        _write_file(stage / ROBOTS_FILE, ROBOTS_TEXT.encode("utf-8"))
        stats = check_output(
            stage,
            [invitation["token"] for invitation in data.invitations],
            base_url=base_url,
            media_dir=media_name,
            media=published,
            calendars=[entry.name for entry in calendars.values()],
        )

    out_shown = display_path(out_dir)
    log(f"build: wrote {written} page(s) to {out_shown}/{PAGES_DIRNAME}/")
    log(f"build: copied {assets} asset file(s) to {out_shown}/{ASSETS_DIRNAME}/")
    log(
        f"build: copied {media} media file(s) under the hash of their contents to "
        f"{out_shown}/{ASSETS_DIRNAME}/<mediaDir>/"
        if media
        else "build: no media file is shown on the pages, none is published"
    )
    log(
        f"build: wrote {events} calendar file(s), one per event the invitations see, "
        f"under the hash of their contents to {out_shown}/{ASSETS_DIRNAME}/<mediaDir>/"
    )
    for image in images:
        origin = "generated from the design tokens" if image.source is None else (
            f"copied from {display_path(media_dir)}"
        )
        log(f"build: {ASSETS_DIRNAME}/{image.name} {origin}")
    log(
        "build: link preview image URL is "
        + (f"absolute ({mask_base_url(base_url)})" if base_url else "a path from the site root")
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
    return Report(on_warn=lambda message: _stderr(f"warning: {_redact_paths(message)}"))


def _data_dir(args: argparse.Namespace, code_dir: Path) -> Path:
    return Path(args.data) if args.data is not None else code_dir / DEFAULT_DATA_DIR


def _media_dir(args: argparse.Namespace, code_dir: Path) -> Path:
    return Path(args.media) if args.media is not None else code_dir / DEFAULT_MEDIA_DIR


def base_url_setting(args: argparse.Namespace, environ: Any = None) -> str:
    """`--base-url`, else the `SITE_BASE_URL` variable, else ""."""
    if getattr(args, "base_url", None):
        return args.base_url
    environ = os.environ if environ is None else environ
    value = environ.get(BASE_URL_VARIABLE, "").strip()
    if not value:
        return ""
    try:
        return parse_base_url(value)
    except argparse.ArgumentTypeError as exc:
        # the value is not echoed: it may be the private address of the site
        raise BuildError(f"{BASE_URL_VARIABLE}: {exc}") from None


def cmd_build(args: argparse.Namespace) -> int:
    code_dir = CODE_DIR
    base_url = base_url_setting(args)
    build_site(
        _data_dir(args, code_dir),
        _media_dir(args, code_dir),
        Path(args.out) if args.out is not None else DEFAULT_OUT_DIR,
        code_dir=code_dir,
        report=_cli_report(),
        # the log shows the paths of --data, --media and --out, and --out may
        # point at a directory named like a token (`dist/i/<token>`)
        log=lambda message: _stdout(_redact_paths(message)),
        base_url=base_url,
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    code_dir = CODE_DIR
    data_dir = _data_dir(args, code_dir)
    media_dir = _media_dir(args, code_dir)
    report = _cli_report()
    data = load_data(data_dir, media_dir, report, assets_dir=code_dir / ASSETS_DIRNAME)
    _stdout(
        _redact_paths(
            f"validate: OK - {len(data.invitations)} invitation(s), "
            f"{len(data.site['events'])} event(s), {len(data.site['sections'])} section(s), "
            f"{report.media_files} media file(s) (data: {display_path(data_dir)}, "
            f"media: {display_path(media_dir)})"
        )
    )
    summary = short_token_summary(data.invitations)
    if summary:
        _stdout(f"validate: {summary}")
    return EXIT_OK


def token_prefix(value: str) -> str:
    """`token --prefix NAME`: the characters of a token; the value is not
    repeated in the message."""
    problem = check_token_prefix(value)
    if problem:
        raise argparse.ArgumentTypeError(problem)
    return value


def cmd_token(args: argparse.Namespace) -> int:
    prefix = getattr(args, "prefix", None)
    _stdout(generate_token() if prefix is None else generate_prefixed_token(prefix))
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


#: '%40' is '@', '%3A' is ':' - both would pass urlsplit as part of the host.
_PERCENT_CREDENTIALS_RE = re.compile(r"%(?:40|3a)", re.IGNORECASE)


def parse_base_url(value: str) -> str:
    """`--base` / `--base-url`: an http(s) URL of the site; the trailing '/'
    is dropped."""
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
        or parts.username  # credentials in the URL would end up in the pages
        or parts.password
        or "@" in parts.netloc
        # percent-encoded '@' and ':' hide credentials from urlsplit, and the
        # address would silently build a broken absolute og:image
        or _PERCENT_CREDENTIALS_RE.search(parts.netloc)
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
        or any(char.isspace() for char in value)
    ):
        # the value itself is never repeated: it may carry credentials
        raise argparse.ArgumentTypeError(
            "expected the site address, e.g. https://example.org "
            "(http or https, no user information, query string or fragment)"
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
    data = load_data(_data_dir(args, CODE_DIR), report=_cli_report())
    # Nothing is printed unless the whole data set is valid.
    for line in invitation_links(args.base, data.invitations):
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
    build_cmd.add_argument(
        "--base-url",
        metavar="URL",
        type=parse_base_url,
        help="address of the published site, e.g. https://example.org; makes the URL "
        "of the link preview image absolute (default: the "
        f"{BASE_URL_VARIABLE} environment variable; without either the URL is a path "
        "from the site root)",
    )
    build_cmd.set_defaults(func=cmd_build)

    validate_cmd = subparsers.add_parser(
        "validate", help="check the data and media without writing anything"
    )
    add_data_options(validate_cmd, media=True)
    validate_cmd.set_defaults(func=cmd_validate)

    token_cmd = subparsers.add_parser("token", help="print a new invitation token")
    token_cmd.add_argument(
        "--prefix",
        metavar="NAME",
        type=token_prefix,
        help="print a readable token NAME-xxxx with a random tail of "
        f"{data_tools.TOKEN_SUFFIX_LENGTH} characters (NAME: A-Z, a-z, 0-9, '_' and '-'); "
        "without it the token is fully random",
    )
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
