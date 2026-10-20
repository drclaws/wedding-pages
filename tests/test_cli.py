"""End-to-end tests of the command line interface (real subprocesses)."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
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

    def test_the_name_of_the_shared_directory_is_not_a_token(self):
        # the page would be the directory of the files the pages share
        for token in ("assets", "Assets", "ASSETS"):
            with self.subTest(token=token):
                invitations = invitations_data()
                invitations[1]["token"] = token
                write_data(self.data, invitations=invitations)
                for result in (self.run_validate(), self.build_in_process()):
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(
                        "error: invitation #2: 'token' is a reserved name: the directory of the "
                        "page would be the directory of the files the pages share (letter case "
                        "aside); choose another token\n",
                        result.stderr,
                    )
                    self.assertNotIn("ssets", result.stderr)
                    self.assertNotIn("SSETS", result.stderr)
                self.assertFalse(self.out.exists())
        self.assertEqual(data_tools.RESERVED_TOKENS, {build.ASSETS_DIRNAME})
        self.assertIsNone(data_tools.check_token("assets-x"))
        self.assertIsNone(data_tools.check_token("my-assets"))

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
            page = self.out / invitation["token"] / "index.html"
            self.assertTrue(page.is_file(), page)
        page = (self.out / support.TOKEN_A / "index.html").read_text(encoding="utf-8")
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
        self.assertIn("3 asset file(s)", result.stdout)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_paragraphs_in_multiline_fields(self):
        self.run_build()
        page = (self.out / support.TOKEN_C / "index.html").read_text(encoding="utf-8")
        self.assertIn("</p><p>", page)

    def test_previous_build_output_is_replaced(self):
        # what a build creates, plus files left by the OS
        self.out.mkdir(parents=True)
        (self.out / ".DS_Store").write_text("old", encoding="utf-8")
        (self.out / "assets").mkdir()
        for name in ("app.css", "app.js", "stale.css"):
            (self.out / "assets" / name).write_text("old", encoding="utf-8")
        stale_page = self.out / "StaleToken-000000000000" / "index.html"
        stale_page.parent.mkdir(parents=True)
        stale_page.write_text("old", encoding="utf-8")
        (stale_page.parent / "Thumbs.db").write_text("old", encoding="utf-8")
        own_page = self.out / support.TOKEN_A / "index.html"
        own_page.parent.mkdir()
        own_page.write_text("old", encoding="utf-8")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale_page.parent.exists())
        self.assertFalse((self.out / "assets" / "stale.css").exists())
        self.assertFalse((self.out / ".DS_Store").exists())
        self.assertNotEqual(own_page.read_text(encoding="utf-8"), "old")

    def test_pages_without_assets_are_not_replaced(self):
        # e.g. the pages directory of a whole site of the old layout, or
        # somebody's site of plain pages: removing it is never undone
        for name in ("about", "contact", "OldToken-0123456789"):
            (self.out / name).mkdir(parents=True)
            (self.out / name / "index.html").write_text("precious", encoding="utf-8")
        result = run_cli(self.code, *self.build_args(), cwd=self.work)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "error: refusing to replace --out: it does not look like a previous build output "
            "(no assets/ with app.css and app.js, which every build writes; the pages alone "
            "are not replaced either); remove it manually or choose another --out\n",
            result.stderr,
        )
        self.assertEqual((self.out / "about" / "index.html").read_text(encoding="utf-8"), "precious")
        self.assertNotIn("about", result.stderr)

    def test_assets_of_somebody_else_are_not_replaced(self):
        assets = self.out / "assets"
        (assets / "photos").mkdir(parents=True)
        (assets / "notes.md").write_text("precious", encoding="utf-8")
        result = run_cli(self.code, *self.build_args(), cwd=self.work)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no assets/ with app.css and app.js", result.stderr)
        self.assertEqual((assets / "notes.md").read_text(encoding="utf-8"), "precious")

    def test_a_build_of_no_invitation_is_built_again(self):
        # no pages, only assets/ as a build writes it: built over again
        write_data(self.data, invitations=[])
        for _attempt in range(2):
            result = run_cli(self.code, *self.build_args(), cwd=self.work)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("(0 page(s), ", result.stdout)
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["assets"])
        self.assertTrue((self.out / "assets" / "app.js").is_file())

    def test_empty_output_directory_is_replaced(self):
        self.out.mkdir(parents=True)
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / support.TOKEN_A / "index.html").is_file())

    def test_no_temporary_directories_are_left_behind(self):
        self.run_build()
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_invalid_data_keeps_the_previous_output(self):
        self.run_build()
        marker = self.out / "assets" / "app.css"  # a name that belongs to a build output
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
            (directory / "SomeToken").mkdir(parents=True)
            (directory / "SomeToken" / "index.html").write_text("x", encoding="utf-8")

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
        self.assertTrue((self.work / "dist" / support.TOKEN_A / "index.html").is_file())

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
        self.assertTrue((self.work / "output" / support.TOKEN_A).is_dir())

    def test_base_path(self):
        result = self.run_build("--base-path", "/invite/")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.out / support.TOKEN_A / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/invite/assets/app.css"', page)
        self.assertIn(
            "build: base path /invite: the pages are at /invite/<token>/, the shared files "
            "at /invite/assets/",
            result.stdout,
        )
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_invalid_base_path_is_a_usage_error(self):
        for value in ("invite", "/invite//x", "/../x", "/a b", "/%69", "https://example.invalid/x",
                      "/x?y", "/" + "x" * 200, ""):  # fmt: skip
            with self.subTest(value=value[:12]):
                result = run_main(self.code, *self.build_args("--base-path", value))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("argument --base-path: must be", result.stderr)
                self.assertFalse(self.out.exists())
        # a value that starts with a dash is read as an option: still a usage error
        result = run_main(self.code, *self.build_args("--base-path=-x"))
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_allow_path_may_be_repeated(self):
        site = site_data()
        site["events"]["dinner"]["links"] = {
            "menu": {"text": "Меню", "href": "/zmenu/{token}/", "visible": True},
            "seating": {"text": "Рассадка", "href": "/zseating/", "visible": True},
        }
        write_data(self.data, site=site)
        result = self.run_build("--allow-path", "/zmenu", "--allow-path", "/zseating/")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.out / support.TOKEN_A / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'href="/zmenu/{support.TOKEN_A}/">Меню</a>', page)
        self.assertIn('href="/zseating/">Рассадка</a>', page)
        # the log is the same as without it: no counter of its own
        self.assertNotIn("zmenu", result.stdout + result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)
        # one of the two is not enough
        result = run_main(self.code, *self.build_args("--allow-path", "/zmenu"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("<a href>: '/…' does not exist in the output", result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_invalid_allow_path_is_a_usage_error(self):
        for args, message in (
            (("--allow-path", "/"), "must not be '/'"),
            (("--allow-path", "zmenu"), "must be a path from the root of the site"),
            (("--allow-path", "/zmenu//x"), "must be a path from the root of the site"),
            (("--allow-path", "/zm%65nu"), "must be a path from the root of the site"),
            (("--allow-path", "/" + "z" * 200), "must be at most 200 characters long"),
            (("--base-path", "/invite", "--allow-path", "/invite"), "must not be the --base-path"),
            (("--base-path", "/invite", "--allow-path", "/invite/zmenu"),
             "must not lie under the --base-path"),
            (("--base-path", "/zmenu/x", "--allow-path", "/zmenu"),
             "must not contain the --base-path"),
            (("--allow-path", "/zmenu", "--allow-path", "zother"),
             "must be a path from the root of the site"),
        ):  # fmt: skip
            with self.subTest(args=args):
                result = run_main(self.code, *self.build_args(*args))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"argument --allow-path: {message}", result.stderr)
                self.assertNotIn("zm", result.stderr.replace("--", ""))
                self.assertNotIn("zz", result.stderr)
                self.assertFalse(self.out.exists())

    def test_validate_knows_no_allow_path(self):
        result = run_main(self.code, "validate", "--data", str(self.data), "--allow-path", "/zmenu")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unrecognized arguments: --allow-path", result.stderr)


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
        self.assertFalse((self.code / "assets" / support.TOKEN_A).exists())

    def test_unrelated_directory_is_not_replaced(self):
        for directory in (self.code / "tests", self.work / "notes"):
            directory.mkdir()
            keep = directory / "test_something.py"
            keep.write_text("# keep me\n", encoding="utf-8")
            result = self.assertRefused(
                directory,
                fragment="error: refusing to replace --out: it does not look like a previous "
                "build output (unexpected at the top: 1 other entry; their names are not "
                "shown, they may be tokens); remove it manually or choose another --out\n",
            )
            self.assertNotIn("test_something", result.stderr)
            self.assertEqual(keep.read_text(encoding="utf-8"), "# keep me\n")

    def test_mixed_directory_is_not_replaced(self):
        # known names do not help when something else lives there as well
        self.out.mkdir()
        (self.out / "assets").mkdir()
        for name in ("app.css", "app.js"):
            (self.out / "assets" / name).write_text("x", encoding="utf-8")
        (self.out / "thesis.txt").write_text("precious", encoding="utf-8")
        (self.out / "drafts").mkdir()
        self.assertRefused(self.out, fragment="(unexpected at the top: 1 directory and 1 other entry;")
        self.assertEqual((self.out / "thesis.txt").read_text(encoding="utf-8"), "precious")

    def test_an_unreadable_parent_of_out(self):
        """Before the data is read no file of an error is named."""
        locked = self.work / "locked"
        (locked / "zyzzyva-token").mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        if os.access(locked, os.R_OK):  # pragma: no cover - e.g. run as root
            self.skipTest("permissions are not enforced")
        # "cannot inspect --out" or, where Path.exists() swallows the error,
        # "cannot read --out"
        result = self.assertRefused(locked / "zyzzyva-token", fragment="--out: Permission denied\n")
        self.assertRegex(result.stderr, r"error: cannot (inspect|read) --out: Permission denied\n")
        self.assertNotIn("zyzzyva", result.stderr)
        self.assertNotIn("locked", result.stderr)

    OLD_LAYOUT = (
        "error: refusing to replace --out: it looks like a whole site (index.html, 404.html, "
        "_headers or robots.txt at the top, or the pages one directory deeper); the build "
        "now writes the invitations only, straight into --out - remove the directory or "
        "choose another --out (e.g. a directory inside the site, see README 5)\n"
    )

    def make_old_site(self, pages: str = "pages") -> dict[str, str]:
        """A whole site as the build wrote it before: the files of the site at
        the top, the pages one directory deeper."""
        self.out.mkdir()
        for name in ("index.html", "404.html", "_headers", "robots.txt"):
            (self.out / name).write_text("old", encoding="utf-8")
        (self.out / "assets").mkdir()
        for token in (support.TOKEN_A, support.TOKEN_B):
            (self.out / pages / token).mkdir(parents=True)
            (self.out / pages / token / "index.html").write_text("old", encoding="utf-8")
        return {path.relative_to(self.out).as_posix(): "old" for path in self.out.rglob("*")}

    def test_a_site_of_the_old_layout_is_not_replaced(self):
        before = self.make_old_site()
        result = self.assertRefused(self.out, fragment=self.OLD_LAYOUT)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            {path.relative_to(self.out).as_posix(): "old" for path in self.out.rglob("*")}, before
        )
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_old_layout_without_the_files_of_the_site(self):
        # the pages one directory deeper are enough to tell the layout
        self.make_old_site(pages="x")
        for name in ("index.html", "404.html", "_headers", "robots.txt"):
            (self.out / name).unlink()
        self.assertRefused(self.out, fragment=self.OLD_LAYOUT)
        # and one file of the site is enough as well
        (self.out / "x").rename(self.tmp / "moved")
        (self.out / "robots.txt").write_text("old", encoding="utf-8")
        self.assertRefused(self.out, fragment=self.OLD_LAYOUT)

    def test_the_directory_of_a_page_is_not_replaced(self):
        self.make_old_site()
        page = self.out / "pages" / support.TOKEN_A
        result = self.assertRefused(
            page,
            fragment="error: refusing to replace --out: it holds index.html alone, like the "
            "directory of one page; point --out at the directory with the pages and assets/\n",
        )
        self.assertNoPrivateData(result.stdout, result.stderr)
        self.assertEqual((page / "index.html").read_text(encoding="utf-8"), "old")
        # the page itself
        result = self.assertRefused(page / "index.html", fragment="--out exists and is not a directory\n")
        self.assertNoPrivateData(result.stdout, result.stderr)

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

    def test_links_requires_url(self):
        result = run_cli(self.code, "links", cwd=self.work)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--url", result.stderr)


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
            output = output.replace(build.display_path(self.tmp), "<tmp>")
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

        # a problem of the finished output in the pages <token>/
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
        self.assertIn("first: …/index.html line ", result.stderr)
        cases.append(result)

        for result in cases:
            self.assertNoPieceOfTheTokens(result.stdout, result.stderr)
            self.assertNoPrivateData(result.stdout, result.stderr)

    def test_paths_in_warnings_are_redacted(self):
        # e.g. a leftover directory next to an --out inside a page directory
        stderr = io.StringIO()
        report = build._cli_report()
        report.remember_tokens([{"token": "zyzzyva"}, {"token": "kumquat_jx"}, {"token": "abc"}])
        with contextlib.redirect_stderr(stderr):
            report.warn(
                "could not remove dist/zyzzyva/.old; delete pages\\KUMQUAT_JX and "
                "dist/.zyzzyva.tmp-0123 by hand; zyzzyva-x, zyzzyva and abc stay"
            )
        # paths only: a word of the message that equals a token stays, so the
        # mask never tells which word the token is
        self.assertEqual(
            stderr.getvalue(),
            "warning: could not remove dist/…/.old; delete pages\\… and dist/.….tmp-0123 by "
            "hand; zyzzyva-x, zyzzyva and abc stay\n",
        )

    def test_redaction_helpers(self):
        tokens = {"zyzzyva", "kumquat_jx"}
        self.assertEqual(build.redact_tokens("a/ZyZzYvA/b", tokens), "a/…/b")
        self.assertEqual(build.redact_tokens("./zyzzyva (3 pages)", tokens), "./… (3 pages)")
        self.assertEqual(build.redact_tokens("'a/zyzzyva.zip'", tokens), "'a/….zip'")
        self.assertEqual(build.redact_tokens("C:\\x\\Kumquat_JX\\y", tokens), "C:\\x\\…\\y")
        # a word, a longer name: not a component of a path that is a token
        self.assertEqual(build.redact_tokens("zyzzyva: OK", tokens), "zyzzyva: OK")
        self.assertEqual(build.redact_tokens("x/zyzzyva_old", tokens), "x/zyzzyva_old")
        self.assertEqual(build.redact_tokens("xzyzzyva/zyzzyvax", tokens), "xzyzzyva/zyzzyvax")
        self.assertEqual(build.redact_tokens("nothing", set()), "nothing")
        # a path of one component still has a separator
        self.assertEqual(build.display_path(Path.cwd() / "zyzzyva"), os.path.join(".", "zyzzyva"))
        self.assertEqual(build.display_path(Path.cwd()), ".")
        self.assertEqual(build._output_path("zyzzyva/index.html"), "…/index.html")
        self.assertEqual(build._output_path("assets/app.css"), "assets/app.css")
        self.assertEqual(build._output_path("robots.txt"), "robots.txt")
        self.assertEqual(build._output_path("quokka"), "…")
        # only valid tokens are remembered: a short value would hide words
        report = build.Report()
        report.remember_tokens([{"token": "a"}, {"token": "assets"}, "x", {"token": 5}])
        report.remember_tokens("not a list")
        self.assertEqual(report.tokens, frozenset())

    def test_output_directory_named_like_a_token_stays_out_of_the_log(self):
        self.write_short_tokens()
        self.out = self.work / "fresh" / "pages" / "zyzzyva"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"to {Path('fresh', 'pages', '…')}/<token>/", result.stdout)
        self.assertIn(f"build: OK -> {Path('fresh', 'pages', '…')} ", result.stdout)
        self.assertNoPieceOfTheTokens(result.stdout, result.stderr)
        result = run_cli(
            self.code, "validate", "--data", str(self.data), "--media",
            str(self.work / "media" / "pages" / "kumquat_jx"), cwd=self.work,
        )  # fmt: skip
        self.assertNoPieceOfTheTokens(result.stdout, result.stderr)

    def test_failures_around_a_previous_build_stay_out_of_the_log(self):
        """`--out` with any name: the refusals and the errors name no token."""
        self.write_short_tokens()
        pages = self.work / "pages"

        def build_into(out: Path):
            return run_cli(self.code, *self.build_args()[:-1], str(out), cwd=self.work)

        self.assertEqual(build_into(pages).returncode, 0)
        cases = []
        # a file next to a page of the previous build
        (pages / "zyzzyva" / "notes.txt").write_text("x", encoding="utf-8")
        cases.append(build_into(pages))
        self.assertIn("(unexpected at the top: 1 directory;", cases[-1].stderr)
        (pages / "zyzzyva" / "notes.txt").unlink()
        # copies of a page or of its directory next to the pages: an archive,
        # the backup of an editor, the copy of a file manager, a short token
        # with a dot; the data is not read yet, so no name is shown at all
        extras = [pages / "zyzzyva.zip", pages / "zyzzyva~", pages / "kumquat_jx.bak"]
        for path in extras:
            path.write_text("x", encoding="utf-8")
        (pages / "zyzzyva copy").mkdir()
        (pages / "zyzzyva copy" / "index.html").write_text("x", encoding="utf-8")
        (pages / "zyzzyva copy" / "notes.txt").write_text("x", encoding="utf-8")
        cases.append(build_into(pages))
        self.assertIn(
            "error: refusing to replace --out: it does not look like a previous build output "
            "(unexpected at the top: 1 directory and 3 other entries; their names are not "
            "shown, they may be tokens); remove it manually or choose another --out\n",
            cases[-1].stderr,
        )
        for path in extras:
            path.unlink()
        shutil.rmtree(pages / "zyzzyva copy")
        # --out at the directory of a page and at a page
        for out in (pages / "kumquat_jx", pages / "kumquat_jx" / "index.html"):
            cases.append(build_into(out))
            self.assertEqual(cases[-1].returncode, 1)
        # an error while a page is written: the file of the error is a page
        failure = PermissionError(13, "Permission denied", str(pages / "zyzzyva" / "index.html"))
        with mock.patch.object(build, "write_pages", side_effect=failure):
            cases.append(run_main(self.code, *self.build_args()[:-1], str(pages)))
        self.assertEqual(cases[-1].returncode, 1)
        self.assertIn("error: Permission denied: ", cases[-1].stderr)
        self.assertIn(f"{os.sep}{Path('pages', '…', 'index.html')}\n", cases[-1].stderr)
        # --out below a page, in another letter case
        cases.append(build_into(pages / "Kumquat_JX" / "sub"))
        self.assertEqual(cases[-1].returncode, 0, cases[-1].stderr)
        self.assertIn(f"build: OK -> {Path('pages', '…', 'sub')} ", cases[-1].stdout)
        for result in cases:
            self.assertNoPieceOfTheTokens(result.stdout, result.stderr)
            self.assertNoPrivateData(result.stdout, result.stderr)

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
