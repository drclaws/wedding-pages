"""The link preview image: the cover of the pages, rendered by headless Chrome.

`link_preview_image` finds the browser (`find_chrome`), serves the preview
page and its files from a temporary directory on `127.0.0.1` and takes a
JPEG screenshot of it through the DevTools protocol over a pipe
(`--remote-debugging-pipe`: no websocket, no third-party package).  The
page is shown with scripts off, `prefers-reduced-motion: reduce` and hidden
scroll bars.  The CSS window is the first of `VIEWPORTS` that the cover fits
into, scaled to `IMAGE_SIZE` device pixels; the quality goes down from
`QUALITIES[0]` while the file is larger than `MAX_IMAGE_BYTES`.

Everything temporary (the page, the profile of the browser) lives under
`tempfile` (`TMPDIR`) and is removed; the browser is closed whatever
happens.  Messages never name the files of the data nor the path of the
browser.  On one machine with one browser and one set of fonts two renders
give the same bytes.

Standard library only.
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Size of the image in device pixels (1.91:1, what link previews expect).
IMAGE_SIZE = (1200, 630)
#: CSS windows tried in turn: the first one the cover fits into is taken.
#: The narrow one lays the cover out as a phone does, with the largest text.
VIEWPORTS = ((600, 315), (800, 420), (1200, 630))
#: Link previews lose the picture above this size (WhatsApp: about 300 KB).
MAX_IMAGE_BYTES = 300 * 1024
#: JPEG qualities tried in turn while the image is too large.
QUALITIES = (85, 80, 75, 70, 65, 60)
#: How long the whole render may take.
TIMEOUT_SECONDS = 60.0
#: Screenshots taken until two in a row are the same.
MAX_CAPTURES = 5
#: The text of the cover stays inside the middle square of the image (what a
#: square crop keeps): x from 285 to 915 of 1200.
SAFE_LEFT = (IMAGE_SIZE[0] - IMAGE_SIZE[1]) / 2
SAFE_RIGHT = SAFE_LEFT + IMAGE_SIZE[1]

#: The environment variable with the path of the browser (`--chrome`).
CHROME_VARIABLE = "CHROME"
#: Where the browser usually is: macOS applications, then names in `PATH`.
DEFAULT_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

CHROME_FLAGS = (
    "--headless=new",
    "--remote-debugging-pipe",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-gpu",
    "--force-color-profile=srgb",
    # the screenshot waits until every stage of the frame is drawn
    "--run-all-compositor-stages-before-draw",
)

NO_BROWSER_HINT = (
    "the link preview image is rendered by Chrome or Chromium; install one of them, "
    "point --chrome or the CHROME variable at it, or build without the image "
    "(--no-link-preview-image or LINK_PREVIEW_IMAGE=off: a neutral picture is used)"
)

#: Waits for the fonts and the pictures, then for two frames.
_SETTLE_SCRIPT = (
    "Promise.all([document.fonts.ready, ...Array.from(document.images, "
    "(image) => image.decode().catch(() => null))]).then(() => new Promise("
    "(done) => requestAnimationFrame(() => requestAnimationFrame(() => done(true)))))"
)
#: Whether the cover fits into the window: nothing sticks out.
_FIT_SCRIPT = (
    "(() => { const root = document.documentElement;"
    " const inner = document.querySelector('.cover__inner');"
    " if (!inner) return false;"
    " const box = inner.getBoundingClientRect();"
    " return box.top >= 0 && box.bottom <= innerHeight && box.left >= 0"
    " && box.right <= innerWidth && root.scrollWidth <= innerWidth"
    " && root.scrollHeight <= innerHeight; })()"
)
#: Where the text of the cover is: the union of the boxes of its lines.
_TEXT_BOX_SCRIPT = (
    "(() => { let box = null;"
    " for (const element of document.querySelectorAll("
    "'.cover__eyebrow, .cover__title, .cover__date')) {"
    " const range = document.createRange(); range.selectNodeContents(element);"
    " const r = range.getBoundingClientRect();"
    " if (!r.width) continue;"
    " box = box ? [Math.min(box[0], r.left), Math.min(box[1], r.top),"
    " Math.max(box[2], r.right), Math.max(box[3], r.bottom)]"
    " : [r.left, r.top, r.right, r.bottom]; }"
    " return box; })()"
)


class PreviewError(Exception):
    """The image cannot be made; the message names no file of the data."""


@dataclass(frozen=True)
class PreviewImage:
    content: bytes
    width: int
    height: int
    quality: int
    #: `Chrome/153.0.8010.53`: the product of `Browser.getVersion`.
    browser: str
    #: The CSS window the cover was laid out in.
    viewport: tuple[int, int]
    #: False when even the lowest quality is larger than `MAX_IMAGE_BYTES`.
    fits: bool = True
    #: (left, top, right, bottom) of the text of the cover, in image pixels.
    text_box: tuple[float, float, float, float] | None = None
    #: False when the cover fitted into none of `VIEWPORTS` (the widest is used).
    window_fits: bool = True

    @property
    def in_safe_zone(self) -> bool:
        """The text of the cover is inside the image and its middle square."""
        if self.text_box is None:
            return False
        left, top, right, bottom = self.text_box
        return (
            left >= SAFE_LEFT and right <= SAFE_RIGHT and top >= 0 and bottom <= self.height
        )


# --------------------------------------------------------------------------
# The browser
# --------------------------------------------------------------------------


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_chrome(
    explicit: str | None = None,
    environ: Mapping[str, str] | None = None,
    defaults: Sequence[str] = DEFAULT_CHROME_PATHS,
    commands: Sequence[str] = CHROME_COMMANDS,
) -> str | None:
    """The browser: `explicit` (`--chrome`), else `CHROME`, else the usual
    places; None when there is none.  A path that is given but is not an
    executable file raises `PreviewError` (the path is not repeated)."""
    environ = os.environ if environ is None else environ
    for given, what in ((explicit, "--chrome"), (environ.get(CHROME_VARIABLE), CHROME_VARIABLE)):
        if given and given.strip():
            path = given.strip()
            found = path if os.sep in path else shutil.which(path, path=environ.get("PATH"))
            if found is None or not _executable(found):
                raise PreviewError(
                    f"{what} does not name an executable file of a browser; {NO_BROWSER_HINT}"
                )
            return found
    for path in defaults:
        if _executable(path):
            return path
    for command in commands:
        found = shutil.which(command, path=environ.get("PATH"))
        if found:
            return found
    return None


class _Browser:
    """Chrome with the DevTools protocol on the file descriptors 3 and 4."""

    def __init__(self, chrome: str, profile: str, deadline: float) -> None:
        self.deadline = deadline
        commands_read, self._commands = os.pipe()
        self._events, events_write = os.pipe()

        def child() -> None:  # pragma: no cover - runs in the child process
            # the protocol lives on exactly these descriptors
            first, second = os.dup(commands_read), os.dup(events_write)
            os.dup2(first, 3)
            os.dup2(second, 4)

        arguments = [chrome, *CHROME_FLAGS, f"--user-data-dir={profile}"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            arguments.append("--no-sandbox")  # Chrome refuses to run as root otherwise
        try:
            self.process = subprocess.Popen(
                [*arguments, "about:blank"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(3, 4),
                preexec_fn=child,
            )
        except OSError as exc:
            os.close(self._commands)
            os.close(self._events)
            raise PreviewError(f"the browser cannot be started ({exc.strerror})") from None
        finally:
            os.close(commands_read)
            os.close(events_write)
        self._buffer = b""
        self._next = 0
        self._pending: list[dict] = []

    def call(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self._next += 1
        message: dict[str, Any] = {"id": self._next, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        data = json.dumps(message).encode("utf-8") + b"\0"
        try:
            while data:
                written = os.write(self._commands, data)
                data = data[written:]
        except OSError:
            raise PreviewError("the browser closed the connection") from None
        wanted = self._next
        while True:
            reply = self._read()
            if reply.get("id") == wanted:
                if "error" in reply:
                    raise PreviewError(f"the browser refused {method}")
                return reply.get("result", {})
            self._pending.append(reply)

    def wait_for(self, method: str, session: str, match: Any = None) -> dict:
        """The first event `method` of the session (for which `match(params)`
        is true, when given); other events are kept for later."""

        def wanted(event: dict) -> bool:
            return (
                event.get("method") == method
                and event.get("sessionId") == session
                and (match is None or match(event.get("params", {})))
            )

        for position, event in enumerate(self._pending):
            if wanted(event):
                return self._pending.pop(position)
        while True:
            event = self._read()
            if wanted(event):
                return event
            self._pending.append(event)

    def _read(self) -> dict:
        while b"\0" not in self._buffer:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise PreviewError(f"the browser did not answer within {TIMEOUT_SECONDS:g} s")
            ready, _, _ = select.select([self._events], [], [], left)
            if not ready:
                continue
            chunk = os.read(self._events, 1 << 20)
            if not chunk:
                raise PreviewError("the browser closed the connection")
            self._buffer += chunk
        raw, self._buffer = self._buffer.split(b"\0", 1)
        return json.loads(raw)

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.deadline = time.monotonic() + 5
                self.call("Browser.close")
        except (PreviewError, OSError, ValueError):
            pass
        finally:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            for descriptor in (self._commands, self._events):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def _write_site(root: Path, page: str, files: Mapping[str, Path | bytes]) -> None:
    """The preview page as `index.html` and its files at their URL paths."""
    (root / "index.html").write_text(page, encoding="utf-8")
    for url_path, source in files.items():
        target = root / url_path.lstrip("/")
        if root.resolve() not in target.resolve().parents:
            raise PreviewError("a file of the preview page lies outside of it")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, (bytes, bytearray)):
            target.write_bytes(source)
        else:
            shutil.copyfile(source, target)


def _serve(root: Path) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _capture(browser: _Browser, session: str, quality: int) -> bytes:
    """A screenshot of the window; taken until two in a row are the same."""
    previous = None
    for _attempt in range(MAX_CAPTURES):
        result = browser.call(
            "Page.captureScreenshot",
            {"format": "jpeg", "quality": quality, "fromSurface": True},
            session,
        )
        content = base64.b64decode(result.get("data", ""))
        if content and content == previous:
            return content
        previous = content
    raise PreviewError("the browser gave a different picture every time")


def _render(browser: _Browser, url: str) -> PreviewImage:
    product = browser.call("Browser.getVersion").get("product", "Chrome")
    target = browser.call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session = browser.call(
        "Target.attachToTarget", {"targetId": target, "flatten": True}
    )["sessionId"]
    browser.call("Page.enable", session=session)
    browser.call("Page.setLifecycleEventsEnabled", {"enabled": True}, session)
    browser.call(
        "Emulation.setEmulatedMedia",
        {"media": "screen", "features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
        session,
    )
    chosen = None
    window_fits = False
    for width, height in VIEWPORTS:
        browser.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": IMAGE_SIZE[0] / width,
             "mobile": False},  # fmt: skip
            session,
        )
        # the scripts of the page never run; the measuring script runs after
        browser.call("Emulation.setScriptExecutionDisabled", {"value": True}, session)
        navigation = browser.call("Page.navigate", {"url": url}, session)
        if navigation.get("errorText"):
            raise PreviewError("the browser could not open the preview page")
        loader = navigation.get("loaderId")
        # the load of this very navigation, not of an earlier one
        browser.wait_for(
            "Page.lifecycleEvent",
            session,
            lambda params: params.get("name") == "load" and params.get("loaderId") == loader,
        )
        browser.call("Emulation.setScriptExecutionDisabled", {"value": False}, session)
        browser.call(
            "Runtime.evaluate",
            {"expression": _SETTLE_SCRIPT, "awaitPromise": True, "returnByValue": True},
            session,
        )
        fits = browser.call(
            "Runtime.evaluate", {"expression": _FIT_SCRIPT, "returnByValue": True}, session
        ).get("result", {}).get("value")
        chosen = (width, height)
        window_fits = fits is True
        if window_fits:
            break
    assert chosen is not None
    box = browser.call(
        "Runtime.evaluate", {"expression": _TEXT_BOX_SCRIPT, "returnByValue": True}, session
    ).get("result", {}).get("value")
    scale = IMAGE_SIZE[0] / chosen[0]
    text_box = tuple(round(value * scale, 1) for value in box) if box else None
    content, quality = b"", QUALITIES[0]
    for quality in QUALITIES:
        content = _capture(browser, session, quality)
        if len(content) <= MAX_IMAGE_BYTES:
            break
    return PreviewImage(
        content=content,
        width=IMAGE_SIZE[0],
        height=IMAGE_SIZE[1],
        quality=quality,
        browser=str(product),
        viewport=chosen,
        fits=len(content) <= MAX_IMAGE_BYTES,
        text_box=text_box,
        window_fits=window_fits,
    )


def render_page(page: str, files: Mapping[str, Path | bytes], chrome: str) -> PreviewImage:
    """Render the preview page `page` (HTML) with its `files` (URL path ->
    a file or its contents) in the browser `chrome`."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    # a helper process of the browser may still write into the profile while
    # it is removed: that must not fail a finished build
    with tempfile.TemporaryDirectory(prefix="link-preview-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary) / "site"
        profile = Path(temporary) / "profile"
        root.mkdir()
        profile.mkdir()
        _write_site(root, page, files)
        # the browser starts before the server thread: no thread may hold a
        # lock while the child process is forked
        browser = _Browser(chrome, str(profile), deadline)
        try:
            server = _serve(root)
            try:
                return _render(browser, f"http://127.0.0.1:{server.server_address[1]}/")
            except (ValueError, KeyError, TypeError):
                # an answer that is not JSON or lacks a field the protocol promises
                raise PreviewError("unexpected answer of the browser") from None
            finally:
                server.shutdown()
                server.server_close()
        finally:
            browser.close()


def link_preview_image(
    page: str,
    files: Mapping[str, Path | bytes],
    chrome: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> PreviewImage:
    """Find the browser (`find_chrome`) and render the preview page.

    `PreviewError` when there is no browser (with the hint `NO_BROWSER_HINT`)
    or the render fails."""
    found = find_chrome(chrome, environ)
    if found is None:
        raise PreviewError(f"no browser found: {NO_BROWSER_HINT}")
    if sys.platform == "win32":  # pragma: no cover - the pipe needs POSIX descriptors
        raise PreviewError(f"rendering needs a POSIX system: {NO_BROWSER_HINT}")
    return render_page(page, files, found)
