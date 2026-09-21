"""Build the example data sets of the repository with one command.

    python3 tools/build_examples.py [--set data|data-venue-pending|all]
                                    [--out DIR] [--port N] [--base-url URL]

For every set it generates the placeholder media (the main set only, with
``tools/gen_example_media.py`` into ``examples/media/``, which git ignores),
builds the site into ``<out>/<set>/`` with ``build.py build`` and prints
where the site is, how to look at it and the address of every page with its
greeting (the data of the examples is fictional).  In CI (``CI`` or
``GITHUB_ACTIONS`` set) the pages and greetings are not printed, as with
``build.py links``.

Only the sets in ``examples/`` are accepted: for real data use ``build.py
build`` and ``build.py links``.  The main set shows videos, so it needs
``ffmpeg``; without it the set is not built (one line says why), the other
set is built all the same and the exit code is 1.  The exit codes of the
tools are passed on; 2 means a bad command line.  Nothing is written outside
``--out`` and ``examples/media/``.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD = REPO_ROOT / "build.py"
GENERATOR = REPO_ROOT / "tools" / "gen_example_media.py"
EXAMPLES = REPO_ROOT / "examples"
#: Set name -> the media directory it is built with (the second one has no
#: media: the directory does not exist and does not have to).
SETS = {
    "data": EXAMPLES / "media",
    "data-venue-pending": EXAMPLES / "media-none",
}
#: The sets that show videos: their media are generated with ffmpeg.
NEEDS_FFMPEG = ("data",)
CI_ENVIRONMENT_VARIABLES = ("CI", "GITHUB_ACTIONS")


def running_in_ci() -> bool:
    return any(
        os.environ.get(name, "").strip().lower() not in ("", "0", "false")
        for name in CI_ENVIRONMENT_VARIABLES
    )


def run(args: list[str]) -> int:
    """Run a tool of the repository with this interpreter; its output goes through."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, *args], env=env).returncode


def generate_media(data: Path, media: Path) -> int:
    """The placeholder media of a set; the output is shown on failure only."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--data", str(data), "--out", str(media)],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode


def pages(data: Path) -> list[tuple[str, str]]:
    """(token, greeting) of every invitation of a set."""
    invitations = json.loads((data / "invitations.json").read_text(encoding="utf-8"))
    return [(item["token"], " ".join(item["greeting"].split())) for item in invitations]


def build_set(name: str, out: Path, port: int, base_url: str | None) -> int:
    data = EXAMPLES / name
    media = SETS[name]
    print(f"== {name}", flush=True)
    if name in NEEDS_FFMPEG:
        if shutil.which("ffmpeg") is None:
            print(
                f"error: the set '{name}' shows videos and needs ffmpeg to generate them; "
                "install ffmpeg and run again",
                file=sys.stderr,
                flush=True,
            )
            return 1
        code = generate_media(data, media)
        if code != 0:
            return code
    target = out / name
    args = [str(BUILD), "build", "--data", str(data), "--media", str(media), "--out", str(target)]
    if base_url:
        args += ["--base-url", base_url]
    code = run(args)
    if code != 0:
        return code
    print(f"site: {target}")
    print(f"look at it: python3 -m http.server -d {target} {port}")
    if running_in_ci():
        print("pages: not printed in CI")
    else:
        for token, greeting in pages(data):
            print(f"  http://localhost:{port}/i/{token}/  {greeting}")
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_examples.py",
        description="Build the example data sets (examples/) with one command.",
    )
    parser.add_argument(
        "--set",
        choices=[*SETS, "all"],
        default="all",
        help="which example set to build (default: all)",
    )
    parser.add_argument(
        "--out",
        default="dist",
        metavar="DIR",
        help="the sets go into DIR/<set>/ (default: dist, relative to the current directory)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        metavar="N",
        help="port of the addresses that are printed (default: 8000)",
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        help="address of the published site, passed on to build.py",
    )
    args = parser.parse_args(argv)
    names = list(SETS) if args.set == "all" else [args.set]
    failed = 0
    for name in names:
        code = build_set(name, Path(args.out), args.port, args.base_url)
        failed = failed or code
    return failed


if __name__ == "__main__":
    sys.exit(main())
