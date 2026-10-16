"""Static checks of the CI workflow text (no YAML parser required)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"

ALLOWED_ACTIONS = {"actions/checkout", "actions/setup-python"}
ALLOWED_EXPRESSIONS = re.compile(
    r"^(matrix\.[\w-]+|github\.ref|github\.workflow|github\.ref != 'refs/heads/main')$")


def strip_comments(text):
    """Drop whole-line comments and trailing ``# ...`` comments."""
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(lines) + "\n"


def run_blocks(text):
    """Return the bodies of all ``run: |`` blocks."""
    blocks = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[index])
        index += 1
        if not match:
            continue
        indent = len(match.group(1))
        body = [match.group(2)]
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
            index += 1
        blocks.append("\n".join(body))
    return blocks


class CiWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = CI.read_text(encoding="utf-8")
        cls.text = strip_comments(cls.raw)
        cls.runs = run_blocks(cls.raw)

    def test_single_workflow_file(self):
        names = sorted(path.name for path in WORKFLOWS.iterdir()
                       if path.suffix in (".yml", ".yaml"))
        self.assertEqual(names, ["ci.yml"])

    def test_actions_are_allowed_and_pinned_by_full_sha(self):
        uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", self.raw, flags=re.M)
        self.assertTrue(uses)
        for reference, rest in uses:
            action, _, revision = reference.partition("@")
            self.assertIn(action, ALLOWED_ACTIONS)
            self.assertRegex(revision, r"^[0-9a-f]{40}$")
            self.assertRegex(rest, r"^\s+#\s+v\d+\.\d+\.\d+\s*$")

    def test_checkout_does_not_persist_credentials(self):
        self.assertRegex(
            self.text,
            r"uses:\s*actions/checkout@[0-9a-f]{40}\n\s+with:\n\s+persist-credentials:\s*false\n",
        )

    def test_triggers(self):
        self.assertRegex(self.text, r"(?m)^on:\n  push:\n  pull_request:\n")
        self.assertNotIn("pull_request_target", self.raw)
        self.assertNotIn("workflow_run", self.raw)

    def test_read_only_permissions(self):
        self.assertEqual(len(re.findall(r"(?m)^[ \t]*permissions:", self.text)), 1)
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read\n\n")
        self.assertNotRegex(self.text, r":\s*write\b")

    def test_no_secrets_deploy_or_uploads(self):
        self.assertNotIn("secrets.", self.raw)
        self.assertNotRegex(self.raw, r"(?i)github_token|upload-artifact|deploy-pages|BUILD_DEBUG")

    def test_expressions_are_limited_to_safe_contexts(self):
        expressions = re.findall(r"\$\{\{\s*(.*?)\s*\}\}", self.raw)
        self.assertTrue(expressions)
        for expression in expressions:
            self.assertRegex(expression, ALLOWED_EXPRESSIONS)

    def test_run_blocks_contain_no_expressions(self):
        self.assertGreaterEqual(len(self.runs), 8)
        for block in self.runs:
            self.assertNotIn("${{", block)
            self.assertNotIn("github.event.", block)

    def test_links_command_is_not_used(self):
        for block in self.runs:
            self.assertNotRegex(block, r"build\.py\s+links\b")

    def test_matrix_and_runner(self):
        matrix = re.search(r"(?m)^\s+python:\s*\[(.*)\]\s*$", self.text)
        self.assertIsNotNone(matrix)
        versions = [item.strip() for item in matrix.group(1).split(",")]
        self.assertEqual(versions, ['"3.10"', '"3.11"', '"3.12"', '"3.13"'])
        self.assertRegex(self.text, r"(?m)^\s+fail-fast:\s*false$")
        self.assertRegex(self.text, r"(?m)^\s+runs-on:\s*ubuntu-\d+\.\d+$")
        self.assertRegex(self.text, r"(?m)^\s+timeout-minutes:\s*\d+$")

    def test_concurrency_cancels_stale_runs(self):
        self.assertRegex(
            self.text,
            r"(?m)^concurrency:\n  group: .*github\.ref.*\n"
            r"  cancel-in-progress: \$\{\{ github\.ref != 'refs/heads/main' \}\}$",
        )

    def test_hygiene_checks_every_tracked_file_for_media(self):
        hygiene = [block for block in self.runs if "ls-files" in block]
        self.assertEqual(len(hygiene), 1)
        block = hygiene[0]
        listings = re.findall(r"git\b.*?\bls-files\b[^|)]*", block)
        self.assertGreaterEqual(len(listings), 2)
        for listing in listings:
            self.assertRegex(listing, r"^git -c core\.quotePath=false ls-files -z\b")
        self.assertIn("must not contain line breaks or carriage returns", block)
        # the whole tree: no path given to the listing, no directory filtered out
        self.assertIn("media=\"$(tracked | grep -aEi "
                      "'\\.(png|jpe?g|webp|gif|avif|mp4|mov|webm|ico|svg)$' || true)\"", block)
        self.assertNotRegex(block, r"grep\s+-[A-Za-z]*v")
        self.assertNotIn("assets/vendor", self.raw)
        self.assertIn('tracked dist examples/media', block)
        self.assertEqual(block.count("exit 1"), 3)

    def test_link_preview_tags_are_checked_without_a_site_address(self):
        self.assertNotIn("--base-url", self.raw)
        self.assertNotIn("dist-abs", self.raw)
        blocks = [block for block in self.runs if "Link preview tags" in block or "og:url" in block]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        # the build at the root and the one under a base path
        self.assertIn('"$RUNNER_TEMP/dist" / "$RUNNER_TEMP/site/invite" /invite', block)
        for text in (r're.escape(prefix) + r"/assets/[A-Za-z0-9_-]+/[0-9a-f]{16}\.jpg"',
                     '"og:url" not in tags', '"://" not in value', "300 * 1024",
                     "(1200, 630)", '"assets/og.*"', '"Example"',
                     'out.glob("*/index.html")', "images.pop()[len(prefix) + 1:]"):  # fmt: skip
            self.assertIn(text, block)
        self.assertNotIn("stub", block)
        self.assertNotIn('"i/', block)

    def test_a_build_under_a_base_path(self):
        """The layout a real site uses: the invitations in a directory of it."""
        builds = [block for block in self.runs if "--base-path" in block]
        self.assertEqual(len(builds), 1)
        self.assertIn('python build.py build --base-path /invite --out "$RUNNER_TEMP/site/invite"',
                      builds[0])
        checks = [block for block in self.runs if "check_out()" in block]
        self.assertEqual(len(checks), 1)
        block = checks[0]
        for text in ('check_out examples/data "$RUNNER_TEMP/dist"',
                     'check_out examples/data-venue-pending "$RUNNER_TEMP/dist-pending"',
                     'check_out examples/data "$RUNNER_TEMP/site/invite"',
                     "for name in index.html 404.html _headers robots.txt; do",
                     '-not -path "$out/assets/*"', "-iname 'styleguide*'", "-iname 'preview.*'",
                     "-iname '*.json'", "-iname '*.map'"):  # fmt: skip
            self.assertIn(text, block)
        # the layout of the pages is not written into the workflow
        self.assertNotIn('"$out/i"', self.raw)
        self.assertNotIn("i/*", self.raw)

    def test_chrome_is_checked_and_the_site_name_is_set(self):
        blocks = [block for block in self.runs if "google-chrome --version" in block]
        self.assertEqual(len(blocks), 1)
        self.assertIn("exit 1", blocks[0])
        self.assertEqual(self.raw.count("SITE_NAME: Example"), 3)
        self.assertNotIn("LINK_PREVIEW_IMAGE", self.raw)

    def test_chrome_starts_before_the_unit_tests(self):
        """The tests with a real browser run in CI, never skipped for want of one."""
        chrome = [i for i, block in enumerate(self.runs) if "google-chrome --version" in block]
        tests = [i for i, block in enumerate(self.runs) if "python -m unittest" in block]
        self.assertEqual(len(tests), 1)
        self.assertLess(chrome[0], tests[0])
        block = self.runs[chrome[0]]
        self.assertIn("google-chrome --headless=new", block)
        # the sandbox stays on, as in the build
        self.assertNotIn("--no-sandbox", self.raw)

    def test_build_output_goes_to_runner_temp(self):
        outs = re.findall(r"--out\s+(\S+)", "\n".join(self.runs))
        self.assertGreaterEqual(len(outs), 3)
        for out in outs:
            self.assertTrue(out.startswith('"$RUNNER_TEMP/'), out)


if __name__ == "__main__":
    unittest.main()
