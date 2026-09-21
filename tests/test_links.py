"""Tests for `links`: the one command that prints personal data (locally)."""

from __future__ import annotations

import unittest

from tests import support
from tests.support import CliTestCase, build, invitations_data, run_cli, run_main, write_data

BASE = "https://invite.example.invalid"


class LinksCommandTests(CliTestCase):
    def links(self, *args: str, env: dict | None = None, base: str = BASE):
        return run_main(self.code, "links", "--base", base, "--data", self.data, *args, env=env)

    def test_output_format(self):
        result = self.links()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"{support.GREETING_TY}\t{BASE}/i/{support.TOKEN_A}/\n"
            f"{support.GREETING_VY}\t{BASE}/i/{support.TOKEN_B}/\n"
            f"{support.GREETING_THIRD}\t{BASE}/i/{support.TOKEN_C}/\n",
        )
        self.assertEqual(result.stderr, "")

    def test_real_subprocess(self):
        result = run_cli(
            self.code, "links", "--base", BASE + "/", "--data", str(self.data), cwd=self.work
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], f"{support.GREETING_TY}\t{BASE}/i/{support.TOKEN_A}/")

    def test_base_is_normalised(self):
        expected = f"{BASE}/i/{support.TOKEN_A}/"
        for base in (BASE, BASE + "/", BASE + "///", f"  {BASE}/  "):
            with self.subTest(base=base):
                result = self.links(base=base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[0].split("\t")[1], expected)

    def test_base_with_a_path_and_a_port(self):
        result = self.links(base="http://localhost:8000/preview/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0].split("\t")[1],
            f"http://localhost:8000/preview/i/{support.TOKEN_A}/",
        )

    def test_base_must_be_an_http_url(self):
        for base in (
            "invite.example.invalid",
            "ftp://invite.example.invalid",
            "https://",
            "https:///path",
            "//invite.example.invalid",
            "https://invite.example.invalid/?x=1",
            "https://invite.example.invalid/#top",
            "https://invite.example.invalid/a b",
            "https://invite.example.invalid:port",
            "",
        ):
            with self.subTest(base=base):
                result = self.links(base=base)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("--base", result.stderr)

    def test_base_is_required(self):
        result = run_main(self.code, "links", "--data", self.data)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

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
            self.code, "links", "--base", BASE, "--data", str(self.data),
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
        result = run_main(self.code, "links", "--base", BASE, "--data", self.tmp / "nowhere")
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
            result.stdout, f"Дорогая Ева, здравствуй!\t{BASE}/i/{support.TOKEN_A}/\n"
        )

    def test_no_invitations(self):
        write_data(self.data, invitations=[])
        result = self.links()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
