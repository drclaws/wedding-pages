"""Shared helpers of the data layer: JSON, field paths, hints, ids and texts.

- `parse_json` reads a document like `json.loads` and also lists every key
  that is repeated within one object, at any depth: plain `json.loads`
  silently keeps the last value only, and the rest of the data is lost.
- `format_path` writes a field path: `sections[3].widgets[1].location`.
- `did_you_mean` suggests the closest of the known names.
- `is_identifier`, `show_id`, `identifier_problem`: the rule for ids.
- Texts: a string, or an object with exactly the keys `ty` and `vy` (the
  informal and the formal form of address).  `{name}` inserts a value, `{{`
  and `}}` are literal braces; see `check_text` and `resolve_text`.

Messages never quote the data.  A key is shown only when it looks like a
field name or an id (`is_plain_key`), an id only when it follows the id rule,
and the name of an unknown placeholder never, because it is a part of the
text.  Messages have the form `field '<path>' <problem>`; the caller puts the
name of the file or the invitation in front.

Pure functions: no input/output and no printing.  Problems are returned as
messages or raised as `TextError`.  Standard library only.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Callable, Collection, Iterable, Mapping, NamedTuple, Sequence, Union

#: One step of a field path: the key of an object or an index in an array.
PathPart = Union[str, int]

# --------------------------------------------------------------------------
# Keys and field paths
# --------------------------------------------------------------------------

#: A key that looks like a field name or an id is shown as it is.
_PLAIN_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
MAX_PLAIN_KEY_LENGTH = 64
#: Shown instead of any other key: such a key may hold somebody's name.
HIDDEN_KEY = "<non-identifier key>"


def is_plain_key(key: Any) -> bool:
    """True for a key that may appear in a message as it is."""
    return (
        isinstance(key, str)
        and len(key) <= MAX_PLAIN_KEY_LENGTH
        and _PLAIN_KEY_RE.fullmatch(key) is not None
    )


def show_key(key: Any) -> str:
    """A key for a message: `'hotel'`, or `HIDDEN_KEY` for any other key."""
    return f"'{key}'" if is_plain_key(key) else HIDDEN_KEY


def format_path(parts: Iterable[PathPart]) -> str:
    """`("sections", 3, "id")` -> `sections[3].id`; no parts -> `top level`."""
    text = ""
    for part in parts:
        if isinstance(part, int) and not isinstance(part, bool):
            text += f"[{part}]"
        else:
            name = part if is_plain_key(part) else HIDDEN_KEY
            text += f".{name}" if text else name
    return text or "top level"


# --------------------------------------------------------------------------
# "Did you mean"
# --------------------------------------------------------------------------

#: Minimal similarity (`difflib`) for a known name to be suggested.
SUGGESTION_CUTOFF = 0.6


def closest_name(name: Any, known: Iterable[str]) -> str | None:
    """The known name most similar to `name`, if any is similar enough."""
    if not isinstance(name, str):
        return None
    candidates = [item for item in known if isinstance(item, str)]
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=SUGGESTION_CUTOFF)
    return matches[0] if matches else None


def did_you_mean(name: Any, known: Iterable[str]) -> str:
    """` (did you mean 'visible'?)` or an empty string.

    Only a known name is ever shown (and only one that passes `is_plain_key`),
    never `name` itself.
    """
    match = closest_name(name, known)
    if match is None or not is_plain_key(match):
        return ""
    return f" (did you mean {show_key(match)}?)"


# --------------------------------------------------------------------------
# JSON with repeated keys
# --------------------------------------------------------------------------


class DuplicateKey(NamedTuple):
    """A key that appears more than once in one JSON object."""

    #: Path to the object, from the top of the document.
    path: tuple[PathPart, ...]
    key: str

    def describe(self) -> str:
        where = f"in '{format_path(self.path)}'" if self.path else "at the top level"
        return (
            f"duplicate key {show_key(self.key)} {where} "
            "(JSON keeps only the last one; merge or rename them)"
        )


def parse_json(
    text: str, *, parse_constant: Callable[[str], Any] | None = None
) -> tuple[Any, list[DuplicateKey]]:
    """Parse a JSON document and list the keys repeated within an object.

    The value is exactly what `json.loads` returns (of repeated keys the last
    value wins).  Every repeated key is listed once per object, depth first;
    objects inside a value that a repeated key replaced are searched as well.
    Syntax errors are raised by `json.loads` (`json.JSONDecodeError`, or what
    `parse_constant` raises).
    """
    # id(object) -> (object, repeated keys, replaced (key, value) pairs); the
    # object is kept here so that its id cannot be reused while this runs
    repeated: dict[int, tuple[dict, list[str], list[tuple[str, Any]]]] = {}

    def make_object(pairs: list[tuple[str, Any]]) -> dict:
        obj: dict = {}
        keys: list[str] = []
        replaced: list[tuple[str, Any]] = []
        for key, value in pairs:
            if key in obj:
                if key not in keys:
                    keys.append(key)
                replaced.append((key, obj[key]))
            obj[key] = value
        if keys:
            repeated[id(obj)] = (obj, keys, replaced)
        return obj

    value = json.loads(text, object_pairs_hook=make_object, parse_constant=parse_constant)
    return value, (_find_duplicates(value, repeated) if repeated else [])


def _find_duplicates(
    root: Any, repeated: Mapping[int, tuple[dict, list[str], list[tuple[str, Any]]]]
) -> list[DuplicateKey]:
    # an explicit stack: the parser has already accepted the nesting depth,
    # a recursive walk might not
    found: list[DuplicateKey] = []
    stack: list[tuple[tuple[PathPart, ...], Any]] = [((), root)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, dict):
            members = list(value.items())
            record = repeated.get(id(value))
            if record is not None and record[0] is value:
                found.extend(DuplicateKey(path, key) for key in record[1])
                members = record[2] + members
            stack.extend(((*path, key), item) for key, item in reversed(members))
        elif isinstance(value, list):
            indexed = list(enumerate(value))
            stack.extend(((*path, index), item) for index, item in reversed(indexed))
    return found


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

#: Lowercase latin letters and digits in words joined by single hyphens,
#: starting with a letter (`--` stays free as a separator of composite ids).
_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
MAX_ID_LENGTH = 32
ID_RULE = (
    f"1-{MAX_ID_LENGTH} characters, lowercase a-z, 0-9 and single hyphens, "
    "starting with a letter"
)


def is_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_ID_LENGTH
        and _ID_RE.fullmatch(value) is not None
    )


def show_id(value: Any) -> str:
    """An id for a message: `'hotel'`; any other value is not shown."""
    return f"'{value}'" if is_identifier(value) else "(not a valid identifier)"


def identifier_problem(value: Any, path: Sequence[PathPart]) -> str | None:
    """The message for a field that must hold an id, or None when it does."""
    if is_identifier(value):
        return None
    return f"field '{format_path(path)}' must be an identifier: {ID_RULE}"


# --------------------------------------------------------------------------
# Texts: forms of address and placeholders
# --------------------------------------------------------------------------

#: The informal and the formal form of address.
FORMS = ("ty", "vy")
_PLACEHOLDER_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_BRACE_RE = re.compile(r"\{\{|\}\}|[{}]")
_TEXT_SHAPE = "must be a string or an object with the strings 'ty' and 'vy'"


class TextError(ValueError):
    """A text that cannot be used; the message follows `field '<path>'`."""


class Placeholder(NamedTuple):
    name: str
    #: Position of its `{` in the text: the number of the character, from 1.
    position: int


def form_problem(form: Any) -> str | None:
    """The problem of a form of address, or None; the value is never shown."""
    return None if form in FORMS else f"must be one of: {', '.join(FORMS)}"


def text_value_problems(value: Any, path: Sequence[PathPart]) -> list[str]:
    """Problems of the shape of a text: a string or `{"ty": …, "vy": …}`."""
    field = format_path(path)
    if isinstance(value, str):
        return []
    if not isinstance(value, dict):
        return [f"field '{field}' {_TEXT_SHAPE}"]
    problems = [
        f"unknown field '{format_path((*path, key))}'{did_you_mean(key, FORMS)}"
        for key in value
        if key not in FORMS
    ]
    missing = [form for form in FORMS if form not in value]
    if missing:
        listed = " and ".join(f"'{form}'" for form in missing)
        problems.append(f"field '{field}' must have both 'ty' and 'vy' (missing {listed})")
    for form in FORMS:
        if form in value and not isinstance(value[form], str):
            problems.append(f"field '{format_path((*path, form))}' must be a string")
    return problems


def parse_text(text: str) -> list[str | Placeholder]:
    """Split a text into literal parts and placeholders.

    `{{` and `}}` become literal braces.  The first syntax problem raises
    `TextError` with its position; the contents of the braces are never shown.
    """
    parts: list[str | Placeholder] = []
    literal: list[str] = []
    position = 0
    while True:
        match = _BRACE_RE.search(text, position)
        if match is None:
            literal.append(text[position:])
            break
        literal.append(text[position : match.start()])
        brace = match.group()
        where = match.start() + 1
        position = match.end()
        if brace in ("{{", "}}"):
            literal.append(brace[0])
            continue
        if brace == "}":
            raise TextError(
                f"has an unmatched '}}' at character {where} "
                "(write '}}' for a literal brace)"
            )
        end = text.find("}", position)
        if end < 0:
            raise TextError(
                f"has an unclosed '{{' at character {where} "
                "(write '{{' for a literal brace)"
            )
        name = text[position:end]
        if not name:
            raise TextError(
                f"has an empty placeholder at character {where} "
                "(write '{{}}' for literal braces)"
            )
        if _PLACEHOLDER_NAME_RE.fullmatch(name) is None:
            raise TextError(
                f"has a malformed placeholder at character {where} (a name has only "
                "latin letters and digits and starts with a letter; write '{{' for a "
                "literal brace)"
            )
        if any(literal):
            parts.append("".join(literal))
        literal = []
        parts.append(Placeholder(name, where))
        position = end + 1
    if any(literal):
        parts.append("".join(literal))
    return parts


def _placeholder_names(known: Iterable[str]) -> tuple[str, ...]:
    """Known names in their order, without repeats and without anything that
    could never be a placeholder (such a name is never matched nor shown)."""
    return tuple(
        dict.fromkeys(
            name
            for name in known
            if isinstance(name, str) and _PLACEHOLDER_NAME_RE.fullmatch(name)
        )
    )


def _placeholder_problem(
    placeholder: Placeholder, known: tuple[str, ...], available: Collection[str]
) -> str | None:
    name = placeholder.name
    if name not in known:
        # the name is a part of the text: only its position and a known name
        # are shown
        match = closest_name(name, known)
        hint = f" (did you mean {{{match}}}?)" if match else ""
        listed = (
            "available: " + ", ".join(f"{{{item}}}" for item in known)
            if known
            else "no placeholders are available here"
        )
        return (
            f"has an unknown placeholder at character {placeholder.position}{hint}; "
            f"{listed} (write '{{{{' for a literal brace)"
        )
    if name not in available:
        return f"uses {{{name}}}, but '{name}' is not set"
    return None


def placeholder_problems(
    text: str, known: Iterable[str], available: Collection[str] | None = None
) -> list[str]:
    """Problems of the placeholders of one text (a string).

    `known` are the names a text may use; `available` are those that have a
    value (all of the known names by default).  A syntax problem ends the
    check; unknown placeholders are reported one by one, a known name without
    a value once.
    """
    names = _placeholder_names(known)
    try:
        parts = parse_text(text)
    except TextError as exc:
        return [str(exc)]
    have = names if available is None else available
    problems: list[str] = []
    for part in parts:
        if isinstance(part, Placeholder):
            problem = _placeholder_problem(part, names, have)
            if problem is not None and problem not in problems:
                problems.append(problem)
    return problems


def check_text(
    value: Any,
    path: Sequence[PathPart],
    known: Iterable[str],
    available: Collection[str] | None = None,
) -> list[str]:
    """Every problem of a text field: its shape, then the placeholders of
    each form (see `placeholder_problems`), as `field '<path>' …` messages."""
    problems = text_value_problems(value, path)
    if isinstance(value, str):
        variants = [(tuple(path), value)]
    elif isinstance(value, dict):
        variants = [
            ((*path, form), value[form])
            for form in FORMS
            if isinstance(value.get(form), str)
        ]
    else:
        variants = []
    names = _placeholder_names(known)
    for where, text in variants:
        problems.extend(
            f"field '{format_path(where)}' {problem}"
            for problem in placeholder_problems(text, names, available)
        )
    return problems


def pick_form(value: Any, form: str) -> str:
    """The variant of a text for a form of address (`ty` or `vy`)."""
    problem = form_problem(form)
    if problem is not None:
        raise TextError(problem)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get(form), str):
        return value[form]
    raise TextError(_TEXT_SHAPE)


def substitute(text: str, values: Mapping[str, str], known: Iterable[str]) -> str:
    """Insert the values into a text; the first problem raises `TextError`.

    `values` maps a name to its string; a known name missing from `values` is
    not set (an empty string is a value).  The values are inserted as they
    are and never parsed again: `{…}` inside a value stays text.  Line breaks
    are kept.
    """
    names = _placeholder_names(known)
    result: list[str] = []
    for part in parse_text(text):
        if isinstance(part, Placeholder):
            problem = _placeholder_problem(part, names, values)
            if problem is not None:
                raise TextError(problem)
            result.append(values[part.name])
        else:
            result.append(part)
    return "".join(result)


def resolve_text(
    value: Any, form: str, values: Mapping[str, str], known: Iterable[str]
) -> str:
    """The finished text for a form of address: `pick_form`, then `substitute`."""
    return substitute(pick_form(value, form), values, known)
