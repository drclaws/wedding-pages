"""Tests for tools/build_examples.py: the example sets with one command.

The script writes `examples/media/` of its repository, so every test runs a
throwaway copy of the code and of the examples in a temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import ROOT, TempDirTestCase

FFMPEG = shutil.which("ffmpeg")
#: What the script needs of the repository.
PARTS = ("build.py", "template.html", "stub.html", "tools", "fragments", "assets",
         "examples/data", "examples/data-venue-pending")  # fmt: skip


def tokens(name: str) -> list[tuple[str, str]]:
    invitations = json.loads((ROOT / "examples" / name / "invitations.json").read_text(encoding="utf-8"))
    return [(item["token"], item["greeting"]) for item in invitations]


class BuildExamplesTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "repo"
        for part in PARTS:
            source, target = ROOT / part, self.repo / part
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.before = self.listing()

    def listing(self) -> set[str]:
        return {path.relative_to(self.tmp).as_posix() for path in self.tmp.rglob("*")}

    def run_script(self, *args: str, env: dict | None = None, path: str | None = None):
        environ = {key: value for key, value in os.environ.items() if key not in ("CI", "GITHUB_ACTIONS")}
        environ["PYTHONDONTWRITEBYTECODE"] = "1"
        environ["TMPDIR"] = str(self.tmp)
        if path is not None:
            environ["PATH"] = path
        environ.update(env or {})
        return subprocess.run(
            [sys.executable, str(self.repo / "tools" / "build_examples.py"), *args],
            cwd=str(self.work), env=environ, capture_output=True, text=True, encoding="utf-8",
        )

    def pages(self, name: str) -> list[Path]:
        return sorted((self.work / "out" / name / "i").glob("*/index.html"))

    def assertOnlyOutputAndMedia(self) -> None:
        new = self.listing() - self.before
        stray = [
            path for path in new
            if not path.startswith(("work/out", "repo/examples/media"))
        ]  # fmt: skip
        self.assertEqual(stray, [])

    def test_the_set_without_places(self):
        result = self.run_script("--set", "data-venue-pending", "--out", "out", "--port", "8123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.pages("data-venue-pending")), 3)
        self.assertIn("build: OK -> ", result.stdout)
        self.assertIn(f"python3 -m http.server -d {Path('out') / 'data-venue-pending'} 8123", result.stdout)
        for token, greeting in tokens("data-venue-pending"):
            self.assertIn(f"  http://localhost:8123/i/{token}/  {greeting}", result.stdout)
        self.assertFalse((self.repo / "examples" / "media").exists())
        self.assertOnlyOutputAndMedia()

    def test_no_pages_and_greetings_in_ci(self):
        result = self.run_script("--set", "data-venue-pending", "--out", "out", env={"CI": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.pages("data-venue-pending")), 3)
        self.assertIn("pages: not printed in CI", result.stdout)
        for token, greeting in tokens("data-venue-pending"):
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertNotIn(greeting, result.stdout + result.stderr)

    def test_only_the_example_sets_are_accepted(self):
        for args in (["--set", "private"], ["--set", "../data"], ["--port", "x"], ["--nope"]):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.work / "dist").exists())

    def test_without_ffmpeg_the_main_set_is_not_built(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        self.before = self.listing()
        result = self.run_script("--out", "out", path=str(empty))
        self.assertEqual(result.returncode, 1)
        errors = [line for line in result.stderr.splitlines() if line.startswith("error:")]
        self.assertEqual(
            errors,
            ["error: the set 'data' shows videos and needs ffmpeg to generate them; "
             "install ffmpeg and run again"],
        )  # fmt: skip
        self.assertEqual(self.pages("data"), [])
        # the other set is built all the same
        self.assertEqual(len(self.pages("data-venue-pending")), 3)
        self.assertOnlyOutputAndMedia()

    @unittest.skipUnless(FFMPEG, "ffmpeg is not installed")
    def test_the_main_set(self):
        result = self.run_script("--set", "data", "--out", "out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.pages("data")), 8)
        # one calendar file per event, in the media directory; none next to the pages
        self.assertEqual(list((self.work / "out" / "data" / "i").rglob("*.ics")), [])
        self.assertEqual(len(list((self.work / "out" / "data" / "assets").rglob("*.ics"))), 3)
        self.assertTrue((self.repo / "examples" / "media" / "walk.mp4").is_file())
        for token, greeting in tokens("data"):
            self.assertIn(f"  http://localhost:8000/i/{token}/  {greeting}", result.stdout)
        self.assertOnlyOutputAndMedia()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
