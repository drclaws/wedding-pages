"""In-process tests: output staging, asset copying, debugging switch, import."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support import TempDirTestCase, build, make_code_dir, write_assets


def hidden_entries(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith("."))


class StagedOutputTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = self.tmp / "dist"
        self.out.mkdir()
        (self.out / "index.html").write_text("old", encoding="utf-8")
        self.warnings: list[str] = []

    def stage_new_output(self):
        staged = build._StagedOutput(self.out, warn=self.warnings.append)
        with staged as stage:
            (stage / "index.html").write_text("new", encoding="utf-8")

    def test_swap(self):
        self.stage_new_output()
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), "new")
        self.assertEqual(hidden_entries(self.tmp), [])
        self.assertEqual(self.warnings, [])

    def test_exception_inside_the_block_discards_the_stage(self):
        with self.assertRaises(RuntimeError):
            with build._StagedOutput(self.out) as stage:
                (stage / "index.html").write_text("new", encoding="utf-8")
                raise RuntimeError("boom")
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), "old")
        self.assertEqual(hidden_entries(self.tmp), [])

    def test_failure_to_move_the_previous_output_away(self):
        failure = OSError(13, "Permission denied")
        with mock.patch.object(build.os, "rename", side_effect=failure):
            with self.assertRaises(build.BuildError) as caught:
                self.stage_new_output()
        self.assertIn("cannot replace the output directory", str(caught.exception))
        self.assertIn("Permission denied", str(caught.exception))
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), "old")
        self.assertEqual(hidden_entries(self.tmp), [])  # no rendered pages left

    def test_failure_to_move_the_new_output_in_rolls_back(self):
        real_rename = os.rename
        calls: list[tuple] = []

        def flaky_rename(source, target):
            calls.append((source, target))
            if len(calls) == 2:  # stage -> out
                raise OSError(28, "No space left on device")
            return real_rename(source, target)

        with mock.patch.object(build.os, "rename", side_effect=flaky_rename):
            with self.assertRaises(build.BuildError) as caught:
                self.stage_new_output()
        self.assertEqual(len(calls), 3)  # away, in (fails), back
        self.assertIn("No space left on device", str(caught.exception))
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), "old")
        self.assertEqual(hidden_entries(self.tmp), [])

    def test_warning_when_the_previous_output_cannot_be_removed(self):
        with mock.patch.object(build, "_remove_tree", return_value=False):
            self.stage_new_output()
        self.assertEqual((self.out / "index.html").read_text(encoding="utf-8"), "new")
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("could not remove", self.warnings[0])
        self.assertIn(".dist.old-", self.warnings[0])

    def test_stage_directory_error_names_the_output(self):
        blocker = self.tmp / "file"
        blocker.write_text("x", encoding="utf-8")
        with self.assertRaises(build.BuildError) as caught:
            with build._StagedOutput(blocker / "sub" / "dist"):
                pass  # pragma: no cover
        self.assertIn("cannot create a temporary directory", str(caught.exception))
        self.assertIn("dist", str(caught.exception))


class ReplaceableOutputTests(TempDirTestCase):
    def test_missing_and_empty_directories(self):
        build.check_replaceable(self.tmp / "missing")
        (self.tmp / "empty").mkdir()
        build.check_replaceable(self.tmp / "empty")

    def test_every_known_name_is_accepted(self):
        out = self.tmp / "dist"
        out.mkdir()
        for name in build.OUTPUT_TOP_LEVEL | build.OS_JUNK_FILES:
            (out / name).write_text("x", encoding="utf-8")
        build.check_replaceable(out)

    def test_known_names(self):
        self.assertEqual(
            build.OUTPUT_TOP_LEVEL,
            {"i", "assets", "index.html", "404.html", "_headers", "robots.txt"},
        )

    def test_unexpected_entries_are_listed_but_capped(self):
        out = self.tmp / "dist"
        out.mkdir()
        for index in range(5):
            (out / f"file-{index}.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(build.BuildError) as caught:
            build.check_replaceable(out)
        message = str(caught.exception)
        self.assertIn("'file-0.txt'", message)
        self.assertIn("and 2 more", message)
        self.assertNotIn("file-4.txt", message)


class CopyAssetsTests(TempDirTestCase):
    def test_ignore_callback(self):
        source = write_assets(self.tmp / "assets")
        (source / "styleguide.js").write_text("// sg\n", encoding="utf-8")
        copied = build.copy_assets(
            source, self.tmp / "out" / "assets", ignore=lambda name: name == "styleguide.js"
        )
        self.assertEqual(copied, 2)
        self.assertFalse((self.tmp / "out" / "assets" / "styleguide.js").exists())
        self.assertTrue((self.tmp / "out" / "assets" / "app.css").is_file())
        self.assertFalse((self.tmp / "out" / "assets" / ".gitkeep").exists())

    def test_ignore_applies_to_directories(self):
        source = write_assets(self.tmp / "assets")
        copied = build.copy_assets(
            source, self.tmp / "out" / "assets", ignore=lambda name: name == "vendor"
        )
        self.assertEqual(copied, 1)
        self.assertFalse((self.tmp / "out" / "assets" / "vendor").exists())

    def test_copy_errors_are_readable(self):
        source = write_assets(self.tmp / "assets")
        failures = [(str(source / "app.css"), "somewhere", "Permission denied")]
        with mock.patch.object(build.shutil, "copytree", side_effect=build.shutil.Error(failures)):
            with self.assertRaises(build.BuildError) as caught:
                build.copy_assets(source, self.tmp / "out" / "assets")
        message = str(caught.exception)
        self.assertIn("cannot copy assets", message)
        self.assertIn("1 file(s) could not be copied", message)
        self.assertIn("app.css: Permission denied", message)
        self.assertNotIn("[(", message)

    def test_os_error_names_the_path(self):
        source = write_assets(self.tmp / "assets")
        failure = PermissionError(13, "Permission denied", str(source / "vendor"))
        with mock.patch.object(build.shutil, "copytree", side_effect=failure):
            with self.assertRaises(build.BuildError) as caught:
                build.copy_assets(source, self.tmp / "out" / "assets")
        self.assertIn("Permission denied", str(caught.exception))
        self.assertIn("vendor", str(caught.exception))

    def test_missing_source(self):
        with self.assertRaises(build.BuildError):
            build.copy_assets(self.tmp / "nope", self.tmp / "out")


class DebugSwitchTests(unittest.TestCase):
    SECRET = "SECRET-VALUE-FROM-THE-DATA"

    def run_main(self, debug: str | None) -> str:
        environ = {k: v for k, v in os.environ.items() if k != "BUILD_DEBUG"}
        if debug is not None:
            environ["BUILD_DEBUG"] = debug
        stderr = io.StringIO()
        failure = RuntimeError(self.SECRET)
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch.object(
            build, "cmd_token", side_effect=failure
        ), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = build.main(["token"])
        self.assertEqual(code, 1)
        return stderr.getvalue()

    def test_internal_errors_hide_the_message_by_default(self):
        for value in (None, "", "0", "false", "no", "off"):
            with self.subTest(BUILD_DEBUG=value):
                output = self.run_main(value)
                self.assertIn("internal error: RuntimeError", output)
                self.assertNotIn(self.SECRET, output)
                self.assertNotIn("Traceback", output)

    def test_traceback_when_enabled(self):
        for value in ("1", "true", "YES"):
            with self.subTest(BUILD_DEBUG=value):
                output = self.run_main(value)
                self.assertIn("Traceback", output)
                self.assertIn(self.SECRET, output)


class ImportTests(TempDirTestCase):
    def test_import_prints_and_creates_nothing(self):
        code = make_code_dir(self.tmp, assets=False)
        before = sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*"))
        environ = dict(os.environ)
        # the bytecode cache is written by the interpreter, not by the module
        environ["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", "import build"],
            cwd=str(code),
            env=environ,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        after = sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
