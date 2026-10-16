"""In-process tests: output staging, asset copying, debugging switch, import."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support import (
    MEDIA_FILES,
    TempDirTestCase,
    build,
    make_code_dir,
    published_name,
    write_assets,
    write_media,
)


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

    def test_stage_directory_error_names_the_option(self):
        blocker = self.tmp / "file"
        blocker.write_text("x", encoding="utf-8")
        with self.assertRaises(build.BuildError) as caught:
            with build._StagedOutput(blocker / "sub" / "QuokkaToken"):
                pass  # pragma: no cover
        self.assertIn("cannot create a temporary directory next to --out", str(caught.exception))
        # the path of --out is not repeated: it may lead through a page directory
        self.assertNotIn("Quokka", str(caught.exception))


class ReplaceableOutputTests(TempDirTestCase):
    def test_missing_and_empty_directories(self):
        build.check_replaceable(self.tmp / "missing")
        (self.tmp / "empty").mkdir()
        build.check_replaceable(self.tmp / "empty")

    TOKENS = ("EveToken-0123456789abcdefg", "KarlKlaraToken-zyxwvutsrq98", "quokka")

    def make_previous_build(self) -> Path:
        """What a build writes, plus the files of the operating system."""
        out = self.tmp / "dist"
        (out / "assets" / "m3d1a-f1xtur3-dir").mkdir(parents=True)
        for token in self.TOKENS:
            (out / token).mkdir()
            (out / token / "index.html").write_text("x", encoding="utf-8")
        for name in build.OS_JUNK_FILES:
            (out / name).write_text("x", encoding="utf-8")
            (out / self.TOKENS[0] / name).write_text("x", encoding="utf-8")
        return out

    def refusal(self, out: Path) -> str:
        with self.assertRaises(build.BuildError) as caught:
            build.check_replaceable(out)
        message = str(caught.exception)
        self.assertTrue(message.startswith("refusing to replace --out: "), message)
        # the path of --out is never repeated: it may lead through a page directory
        self.assertNotIn(str(out), message)
        self.assertNotIn(out.name, message)
        for token in self.TOKENS:
            self.assertNotIn(token[:4], message)
        return message

    def test_a_previous_build_is_accepted(self):
        build.check_replaceable(self.make_previous_build())
        # without assets/ (the pages a site of the old layout kept one level down)
        only_pages = self.tmp / "only-pages"
        (only_pages / "quokka").mkdir(parents=True)
        (only_pages / "quokka" / "index.html").write_text("x", encoding="utf-8")
        build.check_replaceable(only_pages)
        # with assets/ alone
        (self.tmp / "only-assets" / "assets").mkdir(parents=True)
        build.check_replaceable(self.tmp / "only-assets")

    def test_the_entries_must_have_the_expected_type(self):
        out = self.make_previous_build()
        shutil.rmtree(out / "assets")
        (out / "assets").write_text("not a directory", encoding="utf-8")
        self.assertIn("('assets' is not a directory)", self.refusal(out))

        shutil.rmtree(out)
        out = self.make_previous_build()
        (out / self.TOKENS[1] / "index.html").unlink()
        (out / self.TOKENS[1] / "index.html").mkdir()
        self.assertIn("(unexpected: '…')", self.refusal(out))

    def test_a_page_directory_holds_its_page_alone(self):
        out = self.make_previous_build()
        (out / self.TOKENS[2] / "notes.txt").write_text("x", encoding="utf-8")
        self.assertIn("(unexpected: '…')", self.refusal(out))
        (out / self.TOKENS[2] / "notes.txt").unlink()
        (out / self.TOKENS[2] / "index.html").unlink()
        self.assertIn("(unexpected: '…')", self.refusal(out))  # an empty directory

    def test_symlinks_are_refused(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        (target / "index.html").write_text("precious", encoding="utf-8")
        out = self.tmp / "dist"
        out.mkdir()
        try:
            os.symlink(target, out / "assets", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        self.assertIn("('assets' is not a directory)", self.refusal(out))
        os.remove(out / "assets")
        os.symlink(target, out / "quokka", target_is_directory=True)
        self.assertIn("(unexpected: '…')", self.refusal(out))
        os.remove(out / "quokka")
        (out / "quokka").mkdir()
        os.symlink(target / "index.html", out / "quokka" / "index.html")
        self.assertIn("(unexpected: '…')", self.refusal(out))
        self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "precious")

    OLD_LAYOUT = (
        "refusing to replace --out: it looks like a whole site (index.html, 404.html, "
        "_headers or robots.txt at the top, or the pages one directory deeper); the build "
        "now writes the invitations only, straight into --out - remove the directory or "
        "choose another --out (e.g. a directory inside the site, see README 5)"
    )

    def test_a_site_of_the_old_layout_is_refused_with_a_hint(self):
        for name in ("index.html", "404.html", "_headers", "robots.txt"):
            with self.subTest(name=name):
                out = self.make_previous_build()
                (out / name).write_text("x", encoding="utf-8")
                self.assertEqual(self.refusal(out), self.OLD_LAYOUT)
                shutil.rmtree(out)
        # the pages one directory deeper, whatever the name of that directory
        out = self.make_previous_build()
        (out / "pages").mkdir()
        os.rename(out / self.TOKENS[0], out / "pages" / self.TOKENS[0])
        self.assertEqual(self.refusal(out), self.OLD_LAYOUT)

    def test_the_directory_of_a_page_is_refused(self):
        out = self.make_previous_build()
        self.assertEqual(
            self.refusal(out / self.TOKENS[0]),
            "refusing to replace --out: it holds index.html alone, like the directory of one "
            "page; point --out at the directory with the pages and assets/",
        )

    def test_names_that_look_like_tokens_are_masked(self):
        # --out pointing inside a previous build by mistake: <out>/assets
        # holds the media directory
        assets = self.make_previous_build() / "assets"
        (assets / "app.css").write_text("x", encoding="utf-8")
        message = self.refusal(assets)
        self.assertIn("'…'", message)
        self.assertIn("'app.css'", message)
        self.assertNotIn("m3d1", message)
        # names of token characters are hidden, file names and names too
        # short for a token are shown
        other = self.tmp / "notes"
        for name in ("quokka", "otter_king", "abcd"):
            (other / name / "sub").mkdir(parents=True)
        (other / "todo.txt").write_text("x", encoding="utf-8")
        message = self.refusal(other)
        self.assertIn("(unexpected: 'abcd', '…', '…' and 1 more)", message)
        self.assertNotIn("quo", message)
        self.assertNotIn("ott", message)
        (self.tmp / "notes-files").mkdir()
        (self.tmp / "notes-files" / "todo.txt").write_text("x", encoding="utf-8")
        self.assertIn("'todo.txt'", self.refusal(self.tmp / "notes-files"))

    def test_an_unreadable_directory(self):
        with mock.patch.object(build.os, "scandir", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(build.BuildError) as caught:
                build.check_replaceable(self.tmp / "QuokkaToken")
        self.assertEqual(str(caught.exception), "cannot read --out: Permission denied")

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
            source, self.tmp / "out" / "assets", ignore=lambda name: name == "fonts"
        )
        self.assertEqual(copied, 1)
        self.assertFalse((self.tmp / "out" / "assets" / "fonts").exists())

    def test_ignore_receives_the_relative_path(self):
        source = write_assets(self.tmp / "assets")
        (source / "fonts" / "deep").mkdir()
        (source / "fonts" / "deep" / "lib.js").write_text("// deep\n", encoding="utf-8")
        seen: list[str] = []

        def ignore(relative: str) -> bool:
            seen.append(relative)
            return relative == "fonts/sans.woff2"

        copied = build.copy_assets(source, self.tmp / "out" / "assets", ignore=ignore)
        self.assertEqual(copied, 2)
        self.assertEqual(
            sorted(seen), ["app.css", "fonts", "fonts/deep", "fonts/deep/lib.js", "fonts/sans.woff2"]
        )
        self.assertFalse((self.tmp / "out" / "assets" / "fonts" / "sans.woff2").exists())
        self.assertTrue((self.tmp / "out" / "assets" / "fonts" / "deep" / "lib.js").is_file())

    def test_styleguide_files_are_recognised_at_any_depth(self):
        for relative in ("styleguide.css", "styleguide.js", "vendor/Styleguide.min.js"):
            self.assertTrue(build.is_styleguide_asset(relative), relative)
        for relative in ("app.css", "styleguide/app.css", "my-styleguide.css", "styleguide"):
            self.assertFalse(build.is_styleguide_asset(relative), relative)

    def test_empty_directories_are_not_created(self):
        source = write_assets(self.tmp / "assets")  # fonts/ holds a dot-file only
        (source / "a" / "b" / "c").mkdir(parents=True)
        destination = self.tmp / "out" / "assets"
        build.copy_assets(source, destination)
        self.assertEqual(
            sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*")),
            ["app.css", "fonts", "fonts/sans.woff2"],
        )

    def test_empty_assets_directory(self):
        (self.tmp / "assets").mkdir()
        destination = self.tmp / "out" / "assets"
        self.assertEqual(build.copy_assets(self.tmp / "assets", destination), 0)
        self.assertTrue(destination.is_dir())

    def test_symlinks_are_an_error_and_are_not_followed(self):
        source = write_assets(self.tmp / "assets")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.css").write_text("a{}", encoding="utf-8")
        try:
            os.symlink(outside, source / "fonts" / "linked", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        with self.assertRaises(build.BuildError) as caught:
            build.copy_assets(source, self.tmp / "out" / "assets")
        self.assertIn("symbolic links are not allowed in assets", str(caught.exception))
        self.assertIn(os.path.join("fonts", "linked"), str(caught.exception))

    def test_ignored_and_hidden_symlinks_are_skipped(self):
        source = write_assets(self.tmp / "assets")
        try:
            os.symlink(source / "app.css", source / ".hidden-link")
            os.symlink(source / "app.css", source / "styleguide.css")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        copied = build.copy_assets(
            source, self.tmp / "out" / "assets", ignore=build.is_styleguide_asset
        )
        self.assertEqual(copied, 2)

    def test_assets_directory_that_is_a_symlink(self):
        source = write_assets(self.tmp / "real-assets")
        try:
            os.symlink(source, self.tmp / "assets", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        with self.assertRaises(build.BuildError) as caught:
            build.copy_assets(self.tmp / "assets", self.tmp / "out" / "assets")
        self.assertIn("must not be a symbolic link", str(caught.exception))

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
        failure = PermissionError(13, "Permission denied", str(source / "fonts"))
        with mock.patch.object(build.shutil, "copytree", side_effect=failure):
            with self.assertRaises(build.BuildError) as caught:
                build.copy_assets(source, self.tmp / "out" / "assets")
        self.assertIn("Permission denied", str(caught.exception))
        self.assertIn("fonts", str(caught.exception))

    def test_missing_source(self):
        with self.assertRaises(build.BuildError):
            build.copy_assets(self.tmp / "nope", self.tmp / "out")


def media_entries(media: Path, names=MEDIA_FILES) -> dict:
    """What `check_media` returns for the fixture files in `media`."""
    return {
        name: build.MediaFile(
            name, build.media_tools.hashed_name(media / name), build.media_tools.inspect(media / name)
        )
        for name in names
    }


class CopyMediaTests(TempDirTestCase):
    def test_only_the_given_files_are_copied_under_their_published_names(self):
        media = write_media(self.tmp / "media", (*MEDIA_FILES, "unused.webp", "notes.txt"))
        destination = self.tmp / "out" / "assets" / "media-dir"
        copied = build.copy_media(media_entries(media), media, destination)
        self.assertEqual(copied, len(MEDIA_FILES))
        self.assertEqual(
            sorted(p.name for p in destination.iterdir()),
            sorted(published_name(name) for name in MEDIA_FILES),
        )
        self.assertEqual(
            (destination / published_name("clip.mp4")).read_bytes(), (media / "clip.mp4").read_bytes()
        )

    def test_equal_contents_are_copied_once(self):
        media = write_media(self.tmp / "media", ["venue-1.png"])
        (media / "copy.png").write_bytes((media / "venue-1.png").read_bytes())
        entries = media_entries(media, ["venue-1.png", "copy.png"])
        self.assertEqual(entries["copy.png"].published, entries["venue-1.png"].published)
        destination = self.tmp / "out" / "m"
        self.assertEqual(build.copy_media(entries, media, destination), 1)

    def test_no_files_gives_no_directory(self):
        destination = self.tmp / "out" / "m"
        self.assertEqual(build.copy_media({}, self.tmp / "no-media", destination), 0)
        self.assertFalse(destination.exists())

    def test_existing_destination_is_a_collision(self):
        media = write_media(self.tmp / "media")
        destination = self.tmp / "out" / "vendor"
        destination.mkdir(parents=True)
        with self.assertRaises(build.BuildError) as caught:
            build.copy_media(media_entries(media), media, destination)
        self.assertIn("'mediaDir' collides", str(caught.exception))

    def test_symlink_is_refused(self):
        media = write_media(self.tmp / "media")
        entries = media_entries(media)
        os.remove(media / "poster.png")
        try:
            os.symlink(media / "route.png", media / "poster.png")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are not available: {exc}")
        with self.assertRaises(build.BuildError) as caught:
            build.copy_media(entries, media, self.tmp / "out" / "m")
        self.assertIn("symbolic links are not allowed in media", str(caught.exception))

    def test_unreadable_file(self):
        media = write_media(self.tmp / "media")
        entries = media_entries(media)
        os.remove(media / "poster.png")
        with self.assertRaises(build.BuildError) as caught:
            build.copy_media(entries, media, self.tmp / "out" / "m")
        self.assertIn("cannot copy the media file", str(caught.exception))


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
