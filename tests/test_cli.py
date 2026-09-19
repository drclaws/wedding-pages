"""End-to-end tests of the command line interface (real subprocesses)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests import support
from tests.support import (
    TempDirTestCase,
    build,
    invitations_data,
    make_code_dir,
    run_cli,
    site_data,
    write_data,
    write_json,
    write_media,
)


class CliTestCase(TempDirTestCase):
    """Fixture: code directory, data directory and placeholder media."""

    template = support.TEMPLATE

    def setUp(self) -> None:
        super().setUp()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.code = make_code_dir(self.tmp, template=self.template)
        self.data = write_data(self.tmp / "data")
        self.media = write_media(self.tmp / "media")
        self.out = self.work / "dist"

    def run_build(self, *extra: str, cwd: Path | None = None, env: dict | None = None):
        return run_cli(
            self.code,
            "build",
            "--data",
            str(self.data),
            "--media",
            str(self.media),
            "--out",
            str(self.out),
            *extra,
            cwd=cwd or self.work,
            env=env,
        )

    def run_validate(self, *extra: str):
        return run_cli(
            self.code,
            "validate",
            "--data",
            str(self.data),
            "--media",
            str(self.media),
            *extra,
            cwd=self.work,
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

    def test_output_directory_is_replaced(self):
        self.out.mkdir(parents=True)
        (self.out / "stale.txt").write_text("old", encoding="utf-8")
        stale_page = self.out / "i" / "StaleToken-000000000000" / "index.html"
        stale_page.parent.mkdir(parents=True)
        stale_page.write_text("old", encoding="utf-8")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "stale.txt").exists())
        self.assertFalse(stale_page.parent.exists())

    def test_no_temporary_directories_are_left_behind(self):
        self.run_build()
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_invalid_data_keeps_the_previous_output(self):
        self.run_build()
        marker = self.out / "marker.txt"
        marker.write_text("keep", encoding="utf-8")
        invitations = invitations_data()
        del invitations[0]["greeting"]
        write_data(self.data, invitations=invitations)

        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("greeting", result.stderr)
        self.assertTrue(marker.is_file())
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_template_error_is_reported_per_invitation(self):
        (self.code / "template.html").write_text(
            "<p>{{greeting}}</p>\n<p>{{missingField}}</p>\n", encoding="utf-8"
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missingField", result.stderr)
        self.assertIn("template.html line 2", result.stderr)
        self.assertIn(f"invitation #1 ({support.TOKEN_A[:4]}…)", result.stderr)
        self.assertFalse(self.out.exists())
        self.assertNoPrivateData(result.stdout, result.stderr)

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

    def test_links_is_registered_but_not_implemented(self):
        result = run_cli(self.code, "links", "--base", "https://example.invalid", cwd=self.work)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not implemented yet", result.stderr)
        self.assertEqual(result.stdout, "")

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
