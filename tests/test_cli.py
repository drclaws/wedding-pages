"""End-to-end tests of the command line interface (real subprocesses)."""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tools import _data as data_tools
from tests.support import (
    CliTestCase,
    build,
    invitations_data,
    run_cli,
    run_main,
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
        self.assertIsNone(data_tools.check_token(token))
        self.assertGreaterEqual(len(token), 20)  # fully random, as before

    def test_prefix_prints_a_readable_token_with_a_random_tail(self):
        result = run_cli(self.code, "token", "--prefix", "otter", cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        token = result.stdout.strip()
        self.assertRegex(token, r"\Aotter-[23456789abcdefghjkmnpqrstuvwxyz]{4}\Z")
        self.assertIsNone(data_tools.check_token(token))

    def test_prefixed_tokens_differ(self):
        tokens = {data_tools.generate_prefixed_token("otter") for _ in range(100)}
        self.assertEqual(len(tokens), 100)
        for token in tokens:
            self.assertTrue(token.startswith("otter-"))
            self.assertTrue(set(token[6:]) <= set(data_tools.TOKEN_SUFFIX_ALPHABET))
            self.assertIsNone(data_tools.check_token(token))
        # the tail has no characters that are easy to confuse
        self.assertFalse(set("0o1li") & set(data_tools.TOKEN_SUFFIX_ALPHABET))

    def test_prefix_with_the_longest_name(self):
        name = "x" * data_tools.MAX_TOKEN_PREFIX_LENGTH
        self.assertIsNone(data_tools.check_token(data_tools.generate_prefixed_token(name)))
        with self.assertRaises(ValueError):
            data_tools.generate_prefixed_token(name + "x")

    def test_invalid_prefix_is_a_usage_error(self):
        for name in ("", "otter king", "otter.king", "выдра", "otter/king", "x" * 300):
            with self.subTest(name=name[:10]):
                result = run_main(self.code, "token", "--prefix", name)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("--prefix", result.stderr)
                if name:
                    self.assertNotIn(name, result.stderr)


class ShortTokenSummaryTests(CliTestCase):
    SUMMARY = "tokens: 3 (2 shorter than 12 characters, see README 3.3)"

    def test_summary_counts_the_short_tokens(self):
        invitations = invitations_data()
        invitations[0]["token"] = "zyzzyva"
        invitations[1]["token"] = "kumquat_jx9"  # 11 characters
        write_data(self.data, invitations=invitations)
        for command, result in (("validate", self.run_validate()), ("build", self.run_build())):
            with self.subTest(command=command):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"{command}: {self.SUMMARY}\n", result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertNotIn("zyz", result.stdout)
                self.assertNotIn("kumq", result.stdout)

    def test_no_summary_without_short_tokens(self):
        invitations = invitations_data()
        invitations[0]["token"] = "zyzzyva-kumquat"  # 15 characters
        write_data(self.data, invitations=invitations)
        for result in (self.run_validate(), self.run_build()):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("tokens:", result.stdout)

    def test_summary_helper(self):
        summary = data_tools.short_token_summary
        self.assertIsNone(summary([]))
        self.assertIsNone(summary([{"token": "x" * 12}]))
        self.assertEqual(
            summary([{"token": "x" * 11}, {"token": "x" * 12}, {"token": "x" * 5}]),
            self.SUMMARY,
        )


class ValidateCommandTests(CliTestCase):
    def test_valid_data(self):
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"validate: OK - 3 invitation(s), 2 event(s), 8 section(s), "
            f"{len(support.MEDIA_FILES)} media file(s)",
            result.stdout,
        )
        self.assertEqual(result.stderr, "")
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_writes_nothing(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        self.run_validate()
        self.assertEqual(before, sorted(p.name for p in self.tmp.iterdir()))

    def test_invalid_data_reports_every_error(self):
        invitations = invitations_data()
        invitations[1]["token"] = invitations[0]["token"]
        invitations[2]["form"] = "tu"
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate token", result.stderr)
        self.assertIn("invitation #3: field 'form' must be one of: ty, vy", result.stderr)
        self.assertIn("failed with 2 error(s)", result.stderr)
        self.assertNotIn("'tu'", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_missing_media_file(self):
        os.remove(self.media / "venue-1.png")
        route = (self.media / "route.png").read_bytes()
        os.remove(self.media / "route.png")
        (self.media / "Route.png").write_bytes(route)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "error: site.json: field 'media.venue-1.file': the file is missing from ", result.stderr
        )
        self.assertRegex(
            result.stderr,
            r"field 'media\.route\.file': the file is missing from \S*media \(a file with a "
            r"different letter case exists; names are case-sensitive\)",
        )
        # file names are data: the field tells which one it is
        self.assertNotIn("venue-1.png", result.stderr)
        self.assertNotIn("oute.png", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_media_that_no_page_shows_is_not_looked_at(self):
        site = site_data()
        site["media"]["spare"] = {"type": "image", "file": "spare.png", "alt": "Запасное"}
        write_data(self.data, site=site)
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "warning: site.json: media item 'spare' is not shown on any page and its files "
            "are not published",
            result.stderr,
        )

    def test_missing_media_directory_without_references(self):
        site = site_data()
        site["locations"]["manor"] = {"ready": False}
        del site["media"]
        site["sections"] = [section for section in site["sections"] if section["id"] != "video"]
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
        self.assertIn("invitation #1: 'token' is too long", result.stderr)
        self.assertNotIn("Z" * 10, result.stderr)

    def test_repeated_ids_of_the_markup_stop_the_build(self):
        # a safeguard: the ids are unique by construction, unless joined ambiguously
        site = site_data()
        site["sections"].append(
            {"id": "invite-w1", "title": "Ещё", "widgets": [{"type": "text", "text": "x"}]}
        )
        write_data(self.data, site=site)
        with mock.patch.object(build.page_tools, "DOM_SEPARATOR", "-"):
            result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "error: invitation #1: ids of the markup repeat on the page ('s-invite-w1')",
            result.stderr,
        )
        self.assertFalse(self.out.exists())
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_warnings_go_to_stderr(self):
        invitations = invitations_data()
        invitations[1]["events"]["brunch"] = {"note": support.EVENT_NOTE + " (2)"}
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "warning: invitation #2: field 'events.brunch.note' is set, but the event "
            "is hidden for this invitation",
            result.stderr,
        )
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_unknown_field_is_an_error_without_its_value(self):
        site = site_data()
        site["coupleName"] = site.pop("coupleNames")
        invitations = invitations_data()
        invitations[0]["surpriseField"] = "VALUE-MUST-NOT-LEAK"
        write_data(self.data, site=site, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "site.json: unknown field 'coupleName' (did you mean 'coupleNames'?)", result.stderr
        )
        # a key of an invitation may be somebody's name: it is not shown
        self.assertIn("invitation #1: unknown field '<unknown key>'", result.stderr)
        self.assertNotIn("surpriseField", result.stderr)
        self.assertNotIn("VALUE-MUST-NOT-LEAK", result.stderr)

    def test_old_data_is_one_message_that_points_to_the_readme(self):
        write_json(
            self.data / "site.json",
            {"coupleNames": "x", "dateISO": "2030-06-01T16:00:00+03:00", "venue": {}},
        )
        write_json(
            self.data / "invitations.json",
            [{"token": support.TOKEN_A, "greeting": "x", "ty": True, "vy": False}],
        )
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        errors = [line for line in result.stderr.splitlines() if line.startswith("error:")]
        self.assertEqual(len(errors), 2, result.stderr)
        self.assertIn("site.json: is in the old data format", errors[0])
        self.assertIn("README", errors[0])
        self.assertIn("invitations.json: all 1 invitations are in the old data format", errors[1])


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
        self.assertTrue((self.out / "assets" / "fonts" / "sans.woff2").is_file())
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
        self.assertIn("first: invitation #1\n", result.stderr)
        errors = [line for line in result.stderr.splitlines() if line.startswith("error:")]
        self.assertEqual(len(errors), 1, result.stderr)
        self.assertFalse(self.out.exists())
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_template_error_for_a_single_invitation(self):
        # Only the second fixture invitation is addressed formally, so only its
        # page reaches the failing placeholder (an array inside {{…}}).
        (self.code / "template.html").write_text(
            "<!-- if:vy -->{{sections}}<!-- endif -->\n", encoding="utf-8"
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: invitation #2: template.html", result.stderr)
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
        invitations[0]["token"] = "tiny"
        write_data(self.data, invitations=invitations)
        cases.append(self.run_build())
        cases.append(self.run_validate())

        site = site_data()
        site["events"]["dinner"]["start"] = "2030-06-01T16:00:00"
        write_data(self.data, site=site, invitations=invitations_data())
        cases.append(self.run_validate())

        write_json(self.data / "invitations.json", "not a list")
        cases.append(self.run_validate())

        (self.data / "site.json").write_text("{oops", encoding="utf-8")
        cases.append(self.run_build())

        for result in cases:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertNoPrivateData(result.stdout, result.stderr)

    #: Short readable tokens: no piece of three characters may reach the log.
    SHORT_TOKENS = ("zyzzyva", "kumquat_jx")

    def assertNoPieceOfTheTokens(self, *outputs: str) -> None:
        for output in outputs:
            # the random name of the temporary directory is not a token
            output = output.replace(str(self.tmp), "<tmp>")
            for token in self.SHORT_TOKENS:
                for start in range(len(token) - 2):
                    self.assertNotIn(token[start : start + 3], output, token)

    def write_short_tokens(self, **changes) -> None:
        invitations = invitations_data()
        for invitation, token in zip(invitations, self.SHORT_TOKENS):
            invitation["token"] = token
            invitation.update(changes)
        write_data(self.data, invitations=invitations)

    def test_short_tokens_stay_out_of_the_log(self):
        cases = []
        # an error in the data of the guests with short tokens
        self.write_short_tokens(form="tu")
        cases += [self.run_validate(), self.run_build()]
        for result in cases:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("error: invitation #1: field 'form' must be one of", result.stderr)
            self.assertIn("error: invitation #2: field 'form' must be one of", result.stderr)

        # the same token in another letter case
        invitations = invitations_data()
        invitations[0]["token"] = "Zyzzyva"
        invitations[2]["token"] = "zyzzyva"
        write_data(self.data, invitations=invitations)
        result = self.run_validate()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("error: invitation #3: duplicate token (same as invitation #1;", result.stderr)
        cases.append(result)

        # a problem of the finished output in the pages i/<token>/
        self.write_short_tokens()
        (self.code / "template.html").write_text(
            support.TEMPLATE.replace(
                "</body>", '<img src="https://cdn.example.invalid/x.png" alt="">\n</body>'
            ),
            encoding="utf-8",
        )
        result = self.run_build()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("3 files: ", result.stderr)
        self.assertIn("first: i/…/index.html line ", result.stderr)
        cases.append(result)

        for result in cases:
            self.assertNoPieceOfTheTokens(result.stdout, result.stderr)
            self.assertNoPrivateData(result.stdout, result.stderr)

    def test_paths_in_warnings_are_redacted(self):
        # e.g. a leftover directory next to an --out inside a pages directory
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            build._cli_report().warn(
                "could not remove dist/i/zyzzyva/.old; delete dist/I/kumquat_jx by hand"
            )
        self.assertEqual(
            stderr.getvalue(),
            "warning: could not remove dist/i/…/.old; delete dist/I/… by hand\n",
        )

    def test_output_directory_named_like_a_token_stays_out_of_the_log(self):
        self.write_short_tokens()
        self.out = self.work / "fresh" / "i" / "zyzzyva"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"to {Path('fresh', 'i', '…', 'i')}{os.sep}", result.stdout)
        self.assertIn(f"build: OK -> {Path('fresh', 'i', '…')} ", result.stdout)
        self.assertNoPieceOfTheTokens(result.stdout, result.stderr)
        result = run_cli(
            self.code, "validate", "--data", str(self.data), "--media",
            str(self.work / "media" / "i" / "kumquat_jx"), cwd=self.work,
        )  # fmt: skip
        self.assertNoPieceOfTheTokens(result.stdout, result.stderr)

    def test_successful_runs_with_short_tokens_stay_clean(self):
        self.write_short_tokens()
        for result in (self.run_validate(), self.run_build()):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("tokens: 3 (2 shorter than 12 characters", result.stdout)
            self.assertNoPieceOfTheTokens(result.stdout, result.stderr)

    def test_successful_runs_stay_clean(self):
        site = site_data()
        del site["media"]["venue-1"]["alt"]  # a warning, not an error
        invitations = invitations_data()
        invitations[1]["events"]["brunch"] = {"note": support.NOTE}
        write_data(self.data, site=site, invitations=invitations)
        for result in (self.run_build(), self.run_validate()):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("field 'media.venue-1.alt' is not set", result.stderr)
            self.assertIn("field 'events.brunch.note' is set", result.stderr)
            self.assertNoPrivateData(result.stdout, result.stderr)

    def test_failed_output_checks_stay_clean(self):
        # the broken links carry the name of the place and guest data in their URLs
        links = self.code / "fragments" / "partials" / "map-links.html"
        links.write_text(
            links.read_text(encoding="utf-8").replace(' rel="noopener noreferrer"', ""),
            encoding="utf-8",
        )
        (self.code / "template.html").write_text(
            support.TEMPLATE.replace(
                "</body>",
                '<img src="https://cdn.example.invalid/{{greeting}}.png" alt="">\n'
                '<img src="{{greeting}}.png" alt="">\n'
                '<img src="/assets/{{greeting}}" alt="">\n'
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
        self.assertIn("relative path '….png'", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)
        for fragment in ("%D0%", "Ева", "Алиса"):
            self.assertNotIn(fragment, result.stderr)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
