"""Tests for `links`: the one command that prints personal data (locally)."""

from __future__ import annotations

import unittest

from tests import support
from tests.support import CliTestCase, build, invitations_data, run_cli, run_main, write_data

#: Where the output directory of the build is published: a site and the
#: `--base-path` of the build.
URL = "https://site.example.invalid/invite"


class LinksCommandTests(CliTestCase):
    def links(self, *args: str, env: dict | None = None, url: str = URL):
        return run_main(self.code, "links", "--url", url, "--data", self.data, *args, env=env)

    def test_output_format(self):
        result = self.links()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"{support.GREETING_TY}\t{URL}/{support.TOKEN_A}/\n"
            f"{support.GREETING_VY}\t{URL}/{support.TOKEN_B}/\n"
            f"{support.GREETING_THIRD}\t{URL}/{support.TOKEN_C}/\n",
        )
        self.assertEqual(result.stderr, "")

    def test_real_subprocess(self):
        result = run_cli(
            self.code, "links", "--url", URL + "/", "--data", str(self.data), cwd=self.work
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], f"{support.GREETING_TY}\t{URL}/{support.TOKEN_A}/")

    def test_url_is_normalised(self):
        expected = f"{URL}/{support.TOKEN_A}/"
        for url in (URL, URL + "/", URL + "///", f"  {URL}/  "):
            with self.subTest(url=url):
                result = self.links(url=url)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[0].split("\t")[1], expected)

    def test_url_of_a_site_root_and_a_port(self):
        # an output published at the root of a site, e.g. the examples
        result = self.links(url="http://localhost:8000/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0].split("\t")[1],
            f"http://localhost:8000/{support.TOKEN_A}/",
        )

    def test_the_links_follow_the_base_path_of_the_build(self):
        """`links --url <site><base path>` gives the addresses of the pages that
        `build --base-path <base path>` writes."""
        site = "https://site.example.invalid"
        result = self.links(url=site + "/invite")
        self.assertEqual(result.returncode, 0, result.stderr)
        built = self.run_build("--base-path", "/invite")
        self.assertEqual(built.returncode, 0, built.stderr)
        for line, invitation in zip(result.stdout.splitlines(), invitations_data()):
            address = line.split("\t")[1]
            self.assertEqual(address, f"{site}/invite/{invitation['token']}/")
            # the page lies at the path of the address inside the output
            path = address[len(site + "/invite/") :]
            self.assertTrue((self.out / path / "index.html").is_file(), path)

    def test_url_must_be_an_http_url(self):
        for url in (
            "invite.example.invalid",
            "ftp://invite.example.invalid",
            "https://",
            "https:///path",
            "//invite.example.invalid",
            "https://invite.example.invalid/?x=1",
            "https://invite.example.invalid/#top",
            "https://invite.example.invalid/a b",
            "https://invite.example.invalid:port",
            "https://user:secret@invite.example.invalid/invite",
            "",
        ):
            with self.subTest(url=url):
                result = self.links(url=url)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("--url", result.stderr)
                self.assertIn(
                    "expected the address where the output directory is published", result.stderr
                )
                self.assertNotIn("secret", result.stderr)

    def test_url_is_required(self):
        result = run_main(self.code, "links", "--data", self.data)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--url", result.stderr)

    def test_the_old_option_fails_loudly(self):
        """`--base` gave the address of the site: its meaning is not changed quietly."""
        for args in (
            ("--base", "https://site.example.invalid"),
            ("--base=https://site.example.invalid",),
            ("--base",),
            ("--url", URL, "--base", "https://site.example.invalid"),
            ("--base", "https://site.example.invalid", "--url", URL),
        ):
            with self.subTest(args=args):
                result = run_main(self.code, "links", *args, "--data", self.data)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    "error: --base is replaced by --url: the address where the output directory "
                    "is published, e.g. --url https://example.org/invite; the links are "
                    "<url>/<token>/\n",
                    result.stderr,
                )
                self.assertNotIn("site.example.invalid", result.stderr)
        result = run_cli(
            self.code, "links", "--base", "https://site.example.invalid", "--data",
            str(self.data), cwd=self.work,
        )  # fmt: skip
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--base is replaced by --url", result.stderr)
        # and it is not offered in the help
        result = run_cli(self.code, "links", "--help", cwd=self.work)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--url", result.stdout)
        self.assertNotIn("--base", result.stdout)

    def test_refuses_to_run_in_ci(self):
        for env in ({"CI": "true"}, {"CI": "1"}, {"GITHUB_ACTIONS": "true"}, {"CI": "yes"}):
            with self.subTest(env=env):
                result = self.links(env=env)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("meant for local use only", result.stderr)
                self.assertIn("links: failed", result.stderr)
                self.assertNoPrivateData(result.stderr)

    def test_refuses_to_run_in_ci_as_a_subprocess(self):
        result = run_cli(
            self.code, "links", "--url", URL, "--data", str(self.data),
            cwd=self.work, env={"CI": "true"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("meant for local use only", result.stderr)

    def test_falsy_ci_values_do_not_count(self):
        for env in ({"CI": ""}, {"CI": "0"}, {"CI": "false"}, {"CI": "False", "GITHUB_ACTIONS": ""}):
            with self.subTest(env=env):
                result = self.links(env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_running_in_ci_helper(self):
        self.assertFalse(build.running_in_ci({}))
        self.assertFalse(build.running_in_ci({"CI": " 0 "}))
        self.assertTrue(build.running_in_ci({"CI": "true"}))
        self.assertTrue(build.running_in_ci({"GITHUB_ACTIONS": "true"}))

    def test_invalid_data_prints_nothing(self):
        invitations = invitations_data()
        invitations[2]["token"] = "tiny"
        write_data(self.data, invitations=invitations)
        result = self.links()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")  # not even the valid invitations
        self.assertIn("invitation #3: 'token' is too short", result.stderr)
        self.assertIn("links: failed with 1 error(s)", result.stderr)
        self.assertNoPrivateData(result.stderr)

    def test_missing_data_directory(self):
        result = run_main(self.code, "links", "--url", URL, "--data", self.tmp / "nowhere")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("data directory not found", result.stderr)

    def test_media_is_not_needed(self):
        import shutil

        shutil.rmtree(self.media)
        self.assertEqual(self.links().returncode, 0)

    def test_greeting_stays_on_one_line(self):
        invitations = invitations_data(1)
        invitations[0]["greeting"] = "Дорогая\tЕва,   здравствуй!  "
        write_data(self.data, invitations=invitations)
        result = self.links()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, f"Дорогая Ева, здравствуй!\t{URL}/{support.TOKEN_A}/\n"
        )

    def test_no_invitations(self):
        write_data(self.data, invitations=[])
        result = self.links()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
