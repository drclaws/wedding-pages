"""Read the design tokens (CSS custom properties on `:root`) of `app.css`.

Only what the image generators and the build need is supported: the
top-level `:root` rules, `var(--name)` references and the colour notations `#rgb`, `#rgba`,
`#rrggbb`, `#rrggbbaa`, `rgb()` and `rgba()`.  Anything else is reported with
the name of the token, so that a change of the tokens that the generators do
not understand fails the build instead of producing wrong colours.

Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Mapping, NamedTuple, Tuple

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, float]

#: Colour roles used by the generated images and by the colour of the
#: browser interface (`cover_veil`, the `theme-color` of the pages):
#: attribute -> token name.
PALETTE_TOKENS: Dict[str, str] = {
    "bg": "--color-bg",
    "surface": "--color-surface",
    "surface_sunken": "--color-surface-sunken",
    "text": "--color-text",
    "text_muted": "--color-text-muted",
    "accent": "--color-accent",
    "on_accent": "--color-on-accent",
    "line": "--color-line",
    "cover_veil": "--color-cover-veil",
}


class TokenError(ValueError):
    """The tokens cannot be read; the message names the token."""


class Palette(NamedTuple):
    """Opaque colours by role (translucent tokens are flattened, see `load_palette`)."""

    bg: RGB
    surface: RGB
    surface_sunken: RGB
    text: RGB
    text_muted: RGB
    accent: RGB
    on_accent: RGB
    line: RGB
    cover_veil: RGB


_COMMENT_RE = re.compile(r"/\*.*?(?:\*/|\Z)", re.DOTALL)
_ROOT_RE = re.compile(r"(?<![\w-]):root\s*\{")
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,(.*))?\)\Z", re.DOTALL)
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\Z")
_RGB_RE = re.compile(r"rgba?\((.*)\)\Z", re.IGNORECASE | re.DOTALL)
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)\Z")


def _short(value: str, limit: int = 40) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _split_declarations(body: str) -> list[str]:
    """Split a rule body at the ';' that are outside brackets and strings."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for index, char in enumerate(body):
        if quote:
            if char == quote and body[index - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return parts


def parse_tokens(css: str) -> Dict[str, str]:
    """`--name` -> raw value for the top-level `:root` rules of a style sheet.

    Rules nested in `@media` and other blocks are ignored: the images are made
    from the base values.  When a token is declared twice the last one wins.
    """
    text = _COMMENT_RE.sub(" ", css)
    tokens: Dict[str, str] = {}
    depth = 0
    position = 0
    while position < len(text):
        char = text[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            match = _ROOT_RE.match(text, position)
            if match:
                end = position = match.end()
                inner = 1
                while end < len(text) and inner:
                    inner += {"{": 1, "}": -1}.get(text[end], 0)
                    end += 1
                body = text[position : end - 1] if not inner else text[position:end]
                for declaration in _split_declarations(body):
                    name, colon, value = declaration.partition(":")
                    name = name.strip()
                    if colon and name.startswith("--"):
                        tokens[name] = " ".join(value.split())
                position = end
                continue
        position += 1
    return tokens


def resolve(tokens: Mapping[str, str], name: str) -> str:
    """The value of a token with `var(--other)` references followed."""
    seen = [name]
    while True:
        if name not in tokens:
            via = f" (referenced by '{seen[-2]}')" if len(seen) > 1 else ""
            raise TokenError(f"design token '{name}' is not defined on :root{via}")
        value = tokens[name].strip()
        match = _VAR_RE.match(value)
        if not match:
            return value
        target, fallback = match.group(1), match.group(2)
        if target not in tokens and fallback is not None and fallback.strip():
            return fallback.strip()
        if target in seen:
            raise TokenError(f"design token '{seen[0]}' refers to itself through var()")
        seen.append(target)
        name = target


def _channel(text: str, token: str, value: str) -> int:
    text = text.strip()
    percent = text.endswith("%")
    number = text[:-1] if percent else text
    if not _NUMBER_RE.match(number):
        raise _unsupported(token, value)
    amount = float(number) * 255 / 100 if percent else float(number)
    return max(0, min(255, int(amount + 0.5)))


def _alpha(text: str, token: str, value: str) -> float:
    text = text.strip()
    percent = text.endswith("%")
    number = text[:-1] if percent else text
    if not _NUMBER_RE.match(number):
        raise _unsupported(token, value)
    return max(0.0, min(1.0, float(number) / (100 if percent else 1)))


def _unsupported(token: str, value: str) -> TokenError:
    return TokenError(
        f"design token '{token}': unsupported colour '{_short(value)}' "
        "(expected #rgb, #rrggbb, #rrggbbaa, rgb() or rgba())"
    )


def parse_color(value: str, token: str = "?") -> RGBA:
    """A CSS colour as (red, green, blue, alpha); `token` is used in messages."""
    text = value.strip()
    match = _HEX_RE.match(text)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits)
        if len(digits) not in (6, 8):
            raise _unsupported(token, value)
        red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return red, green, blue, alpha
    match = _RGB_RE.match(text)
    if match:
        inner = match.group(1)
        if "," in inner:
            parts = inner.split(",")
        else:  # rgb(14 16 18 / 78%)
            colors, _slash, opacity = inner.partition("/")
            parts = colors.split() + ([opacity] if opacity.strip() else [])
        if len(parts) not in (3, 4):
            raise _unsupported(token, value)
        red, green, blue = (_channel(part, token, value) for part in parts[:3])
        alpha = _alpha(parts[3], token, value) if len(parts) == 4 else 1.0
        return red, green, blue, alpha
    raise _unsupported(token, value)


def flatten(color: RGBA, background: RGB) -> RGB:
    """A translucent colour as it looks on an opaque background."""
    alpha = color[3]
    return tuple(  # type: ignore[return-value]
        int(background[i] + (color[i] - background[i]) * alpha + 0.5) for i in range(3)
    )


def color_token(tokens: Mapping[str, str], name: str) -> RGBA:
    return parse_color(resolve(tokens, name), name)


def palette_from_tokens(tokens: Mapping[str, str]) -> Palette:
    """The colours of `PALETTE_TOKENS`; every one of them is required.

    A translucent background is flattened on white, the other colours on the
    background, so the result is always opaque.
    """
    background = flatten(color_token(tokens, PALETTE_TOKENS["bg"]), (255, 255, 255))
    return Palette(
        **{
            role: flatten(color_token(tokens, name), background)
            for role, name in PALETTE_TOKENS.items()
        }
    )


def load_palette(css_path: Path | str) -> Palette:
    """Read the palette from a style sheet; `TokenError` on any problem."""
    path = Path(css_path)
    try:
        css = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TokenError(f"{path.name}: not valid UTF-8 (at byte {exc.start})") from None
    except OSError as exc:
        raise TokenError(
            f"{path.name}: cannot read the design tokens ({exc.strerror or type(exc).__name__})"
        ) from None
    try:
        return palette_from_tokens(parse_tokens(css))
    except TokenError as exc:
        raise TokenError(f"{path.name}: {exc}") from None


def hex_color(color: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)
