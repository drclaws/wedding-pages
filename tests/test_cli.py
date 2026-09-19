"""End-to-end tests of the command line interface (real subprocesses)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests import support
from tests.support import (
    CliTestCase,
    build,
    invitations_data,
    run_cli,
    site_data,
    write_data,
    write_json,
    write_media,
)


class TokenCommandTests(CliTestCase):
    def test_token_prints_a_valid_token(self):
        result = run_cli(self.code, "token", cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        token = result.stdout.strip()
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIsNone(build.check_token(token))


class ValidateCommandTests(CliTestCase):
    def test_valid_data(self):
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3 invitation(s)", result.stdout)
        self.assertIn(f"{len(support.MEDIA_FILES)} media file(s)", result.stdout)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_writes_nothing(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        self.run_validate()
        self.assertEqual(before, sorted(p.name for p in self.tmp.iterdir()))

    def test_invalid_data_reports_every_error(self):
        invitations = invitations_data()
        invitations[1]["token"] = invitations[0]["token"]
        invitations[2]["ty"] = False
        invitations[2]["vy"] = False
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate token", result.stderr)
        self.assertIn("exactly one of 'ty' and 'vy'", result.stderr)
        self.assertIn("failed with 2 error(s)", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_missing_media_file(self):
        os.remove(self.media / "venue-1.webp")
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("venue-1.webp", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_missing_media_directory_without_references(self):
        site = site_data(video=None)
        site["venue"] = {"ready": False, "photos": [], "directionsImage": ""}
        write_data(self.data, site=site)
        result = run_cli(
            self.code,
            "validate",
            "--data",
            str(self.data),
            "--media",
            str(self.tmp / "no-such-media"),
            cwd=self.work,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 media file(s)", result.stdout)

    def test_data_that_is_not_utf8(self):
        (self.data / "site.json").write_bytes(b'{"coupleNames": "\xff\xfe"}')
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("site.json: not valid UTF-8", result.stderr)
        self.assertNotIn("internal error", result.stderr)

    def test_oversized_token_is_rejected_by_validate(self):
        invitations = invitations_data()
        invitations[0]["token"] = "Z" * 5000
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("invitation #1 (ZZZZ…): 'token' is too long", result.stderr)
        self.assertNotIn("Z" * 10, result.stderr)

    def test_unknown_field_warning_goes_to_stderr(self):
        invitations = invitations_data()
        invitations[0]["surpriseField"] = "VALUE-MUST-NOT-LEAK"
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("surpriseField", result.stderr)
        self.assertNotIn("VALUE-MUST-NOT-LEAK", result.stderr)


class BuildCommandTests(CliTestCase):
    def test_pages_and_assets(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)

        for invitation in invitations_data():
            page = self.out / "i" / invitation["token"] / "index.html"
            self.assertTrue(page.is_file(), page)
        page = (self.out / "i" / support.TOKEN_A / "index.html").read_text(encoding="utf-8")
        self.assertIn(support.GREETING_TY, page)
        self.assertIn(support.NOTE, page)
        self.assertNotIn(support.GREETING_VY, page)
        self.assertIn("Приходи", page)
        self.assertNotIn("{{", page)

        self.assertTrue((self.out / "assets" / "app.css").is_file())
        self.assertTrue((self.out / "assets" / "vendor" / "lib.js").is_file())
        copied = [
            name
            for root, dirs, files in os.walk(self.out / "assets")
            for name in list(dirs) + list(files)
        ]
        self.assertTrue(copied)
        self.assertFalse([name for name in copied if name.startswith(".")], copied)

        self.assertIn("3 page(s)", result.stdout)
        self.assertIn("2 asset file(s)", result.stdout)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_paragraphs_in_multiline_fields(self):
        self.run_build()
        page = (self.out / "i" / support.TOKEN_C / "index.html").read_text(encoding="utf-8")
        self.assertIn("</p><p>", page)

    def test_previous_build_output_is_replaced(self):
        # every name a build may create, plus a file left by the OS
        self.out.mkdir(parents=True)
        for name in ("index.html", "404.html", "_headers", "robots.txt", ".DS_Store"):
            (self.out / name).write_text("old", encoding="utf-8")
        (self.out / "assets").mkdir()
        (self.out / "assets" / "stale.css").write_text("old", encoding="utf-8")
        stale_page = self.out / "i" / "StaleToken-000000000000" / "index.html"
        stale_page.parent.mkdir(parents=True)
        stale_page.write_text("old", encoding="utf-8")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale_page.parent.exists())
        self.assertFalse((self.out / "assets" / "stale.css").exists())
        for name in ("index.html", "404.html", "_headers", "robots.txt"):
            self.assertNotEqual((self.out / name).read_text(encoding="utf-8"), "old", name)
        self.assertFalse((self.out / ".DS_Store").exists())
        self.assertTrue((self.out / "i" / support.TOKEN_A / "index.html").is_file())

    def test_empty_output_directory_is_replaced(self):
        self.out.mkdir(parents=True)
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "i" / support.TOKEN_A / "index.html").is_file())

    def test_no_temporary_directories_are_left_behind(self):
        self.run_build()
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_invalid_data_keeps_the_previous_output(self):
        self.run_build()
        marker = self.out / "robots.txt"  # a name that belongs to a build output
        marker.write_text("keep", encoding="utf-8")
        invitations = invitations_data()
        del invitations[0]["greeting"]
        write_data(self.data, invitations=invitations)

        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("greeting", result.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_template_error_is_reported_once(self):
        (self.code / "template.html").write_text(
            "<p>{{greeting}}</p>\n<p>{{missingField}}</p>\n", encoding="utf-8"
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missingField", result.stderr)
        self.assertIn("template.html line 2", result.stderr)
        self.assertIn("3 invitation(s):", result.stderr)
        self.assertIn(f"first: invitation #1 ({support.TOKEN_A[:4]}…)", result.stderr)
        errors = [line for line in result.stderr.splitlines() if line.startswith("error:")]
        self.assertEqual(len(errors), 1, result.stderr)
        self.assertFalse(self.out.exists())
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_template_error_for_a_single_invitation(self):
        # Only the second fixture invitation has plusOne, so only its page
        # reaches the failing placeholder (an array inside {{…}}).
        (self.code / "template.html").write_text(
            "<!-- if:plusOne -->{{schedule}}<!-- endif -->\n", encoding="utf-8"
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"error: invitation #2 ({support.TOKEN_B[:4]}…): template.html", result.stderr)
        self.assertNotIn("invitation(s):", result.stderr)

    def test_template_that_is_not_utf8(self):
        (self.code / "template.html").write_bytes(b"<p>\xff\xfe{{greeting}}</p>")
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid UTF-8", result.stderr)
        self.assertIn("template.html", result.stderr)
        self.assertNotIn("internal error", result.stderr)
        self.assertFalse(self.out.exists())

    def test_leftovers_of_an_interrupted_build_are_removed(self):
        stale_new = self.work / ".dist.tmp-0123456789ab"
        stale_old = self.work / ".dist.old-ba9876543210"
        unrelated = self.work / ".dist.tmp-notes"
        for directory in (stale_new, stale_old, unrelated):
            (directory / "i" / "SomeToken").mkdir(parents=True)
            (directory / "i" / "SomeToken" / "index.html").write_text("x", encoding="utf-8")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale_new.exists())
        self.assertFalse(stale_old.exists())
        self.assertTrue(unrelated.is_dir())  # not a name this tool creates

    def test_broken_template_syntax(self):
        (self.code / "template.html").write_text(
            "<p>{{greeting}}</p>\n<!-- if:ty -->\n", encoding="utf-8"
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not closed", result.stderr)
        self.assertFalse(self.out.exists())

    def test_missing_template(self):
        os.remove(self.code / "template.html")
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("template not found", result.stderr)

    def test_missing_assets_directory(self):
        import shutil

        shutil.rmtree(self.code / "assets")
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("assets directory not found", result.stderr)
        self.assertFalse(self.out.exists())

    def test_default_directories_are_relative_to_build_py(self):
        write_data(self.code / "examples" / "data")
        write_media(self.code / "examples" / "media")
        result = run_cli(self.code, "build", cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "dist" / "i" / support.TOKEN_A / "index.html").is_file())

    def test_relative_arguments_are_relative_to_the_working_directory(self):
        result = run_cli(
            self.code,
            "build",
            "--data",
            os.path.relpath(self.data, self.work),
            "--media",
            os.path.relpath(self.media, self.work),
            "--out",
            "output",
            cwd=self.work,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "output" / "i" / support.TOKEN_A).is_dir())


class OutputDirectoryGuardTests(CliTestCase):
    def assertRefused(self, out: Path, *, env: dict | None = None, fragment: str = "--out"):
        result = run_cli(
            self.code,
            "build",
            "--data",
            str(self.data),
            "--media",
            str(self.media),
            "--out",
            str(out),
            cwd=self.work,
            env=env,
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(fragment, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_code_data_and_media_directories(self):
        self.assertRefused(self.code)
        self.assertTrue((self.code / "build.py").is_file())
        self.assertRefused(self.data)
        self.assertTrue((self.data / "site.json").is_file())
        self.assertRefused(self.media)
        self.assertTrue((self.media / "clip.mp4").is_file())

    def test_parent_of_the_data_directory(self):
        self.assertRefused(self.tmp)
        self.assertTrue((self.data / "site.json").is_file())

    def test_working_directory(self):
        self.assertRefused(self.work)

    def test_inside_the_assets_directory(self):
        self.assertRefused(self.code / "assets" / "sub")

    def test_the_assets_directory_itself(self):
        before = sorted(str(p.relative_to(self.code)) for p in (self.code / "assets").rglob("*"))
        self.assertRefused(self.code / "assets", fragment="assets directory")
        after = sorted(str(p.relative_to(self.code)) for p in (self.code / "assets").rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue((self.code / "assets" / ".gitkeep").is_file())
        self.assertFalse((self.code / "assets" / "i").exists())

    def test_unrelated_directory_is_not_replaced(self):
        for directory in (self.code / "tests", self.work / "notes"):
            directory.mkdir()
            keep = directory / "test_something.py"
            keep.write_text("# keep me\n", encoding="utf-8")
            result = self.assertRefused(
                directory, fragment="does not look like a previous build output"
            )
            self.assertIn("test_something.py", result.stderr)
            self.assertIn("remove it manually or choose another --out", result.stderr)
            self.assertEqual(keep.read_text(encoding="utf-8"), "# keep me\n")

    def test_mixed_directory_is_not_replaced(self):
        # known names do not help when something else lives there as well
        self.out.mkdir()
        (self.out / "index.html").write_text("old", encoding="utf-8")
        (self.out / "thesis.txt").write_text("precious", encoding="utf-8")
        self.assertRefused(self.out, fragment="'thesis.txt'")
        self.assertEqual((self.out / "thesis.txt").read_text(encoding="utf-8"), "precious")

    def symlink(self, link: Path, target: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:  # e.g. no privilege
            self.skipTest(f"symbolic links are not available: {exc}")

    def test_symlink_to_a_protected_directory(self):
        link = self.work / "out-link"
        self.symlink(link, self.data)
        self.assertRefused(link, fragment="data directory")
        self.assertTrue((self.data / "site.json").is_file())
        self.assertTrue(link.is_symlink())

    def test_symlink_to_another_directory(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        link = self.work / "out-link"
        self.symlink(link, target)
        self.assertRefused(link, fragment="symbolic link")
        self.assertTrue(link.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_home_directory(self):
        home = self.tmp / "home"
        home.mkdir()
        self.assertRefused(home, env={"HOME": str(home)}, fragment="home directory")

    def test_existing_file(self):
        target = self.work / "file.txt"
        target.write_text("x", encoding="utf-8")
        self.assertRefused(target, fragment="not a directory")

    def test_file_system_root_is_refused(self):
        # checked in-process: no build must ever be started for "/"
        with self.assertRaises(build.BuildError) as caught:
            build.check_out_dir(
                Path(os.path.abspath(os.sep)),
                code_dir=self.code,
                data_dir=self.data,
                media_dir=self.media,
            )
        self.assertIn("root", str(caught.exception))


class ExitCodeTests(CliTestCase):
    def test_no_command(self):
        result = run_cli(self.code, cwd=self.work)
        self.assertEqual(result.returncode, 2)

    def test_unknown_command_and_option(self):
        self.assertEqual(run_cli(self.code, "nope", cwd=self.work).returncode, 2)
        self.assertEqual(
            run_cli(self.code, "build", "--nope", cwd=self.work).returncode, 2
        )

    def test_help(self):
        result = run_cli(self.code, "--help", cwd=self.work)
        self.assertEqual(result.returncode, 0)
        for command in ("build", "validate", "token", "links"):
            self.assertIn(command, result.stdout)

    def test_links_requires_base(self):
        self.assertEqual(run_cli(self.code, "links", cwd=self.work).returncode, 2)


class NoPersonalDataInLogsTests(CliTestCase):
    def test_every_failure_mode_stays_clean(self):
        cases = []

        invitations = invitations_data()
        invitations[0]["token"] = "short"
        write_data(self.data, invitations=invitations)
        cases.append(self.run_build())
        cases.append(self.run_validate())

        write_data(self.data, site=site_data(dateISO="2030-06-01T16:00:00"))
        cases.append(self.run_validate())

        write_json(self.data / "invitations.json", "not a list")
        cases.append(self.run_validate())

        (self.data / "site.json").write_text("{oops", encoding="utf-8")
        cases.append(self.run_build())

        for result in cases:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertNoPrivateData(result.stdout, result.stderr)

    def test_successful_runs_stay_clean(self):
        invitations = invitations_data()
        invitations[0]["unknownField"] = support.NOTE  # a warning, not an error
        write_data(self.data, invitations=invitations)
        for result in (self.run_build(), self.run_validate()):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("unknownField", result.stderr)
            self.assertNoPrivateData(result.stdout, result.stderr)

    def test_failed_output_checks_stay_clean(self):
        # the broken links carry the venue name and guest data in their URLs
        (self.code / "template.html").write_text(
            support.TEMPLATE.replace(' rel="noopener noreferrer"', "").replace(
                "</body>",
                '<img src="https://cdn.example.invalid/{{greeting}}.png" alt="">\n'
                '<img src="{{greeting}}.png" alt="">\n'
                '<img src="/assets/{{note}}" alt="">\n'
                '<img src="//{{greeting}}/x.png" alt="">\n'
                '<a href="mailto:{{coupleNames}}@example.invalid">x</a>\n</body>',
            ),
            encoding="utf-8",
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("needs rel=", result.stderr)
        self.assertIn("external URL 'https://cdn.example.invalid/…'", result.stderr)
        self.assertIn("'mailto:…'", result.stderr)
        self.assertIn("relative path '…'", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)
        for fragment in ("%D0%", "Ева", "Алиса"):
            self.assertNotIn(fragment, result.stderr)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
