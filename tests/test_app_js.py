"""Static checks of the page script source (no JavaScript engine required)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "assets" / "app.js"
APP_CSS = ROOT / "assets" / "app.css"


def strip_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(^|[^:'\"])//[^\n]*", r"\1", source)


class AppJsSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = APP_JS.read_text(encoding="utf-8")
        cls.code = strip_comments(cls.raw)

    def test_first_action_adds_js_class(self):
        statements = [line.strip() for line in self.code.splitlines() if line.strip()]
        self.assertEqual(statements[0], "(function () {")
        self.assertEqual(statements[1], "'use strict';")
        self.assertEqual(statements[2], "document.documentElement.classList.add('js');")

    def test_single_iife_without_globals(self):
        self.assertTrue(self.code.strip().endswith("}());"))
        self.assertNotRegex(self.code, r"\bwindow\.\w+\s*=[^=]")
        self.assertNotRegex(self.code, r"\bglobalThis\b")

    def test_no_constructs_forbidden_by_csp(self):
        for pattern in (r"\beval\s*\(", r"\bnew\s+Function\b", r"\bFunction\s*\(",
                        r"\binnerHTML\b", r"\bouterHTML\b", r"\binsertAdjacentHTML\b",
                        r"\bdocument\.write", r"\bimport\s*\(",
                        r"set(?:Timeout|Interval)\s*\(\s*['\"`]"):
            self.assertNotRegex(self.code, pattern)

    def test_no_external_urls_or_network_calls(self):
        self.assertNotRegex(self.raw, r"(?i)\b(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}")
        for pattern in (r"\bfetch\s*\(", r"\bXMLHttpRequest\b", r"\bsendBeacon\b",
                        r"\bWebSocket\b", r"\bEventSource\b", r"\blocalStorage\b",
                        r"\bsessionStorage\b", r"\bdocument\.cookie\b"):
            self.assertNotRegex(self.code, pattern)

    def test_modules_go_through_the_registry(self):
        names = re.findall(r"\bregister\('([a-z-]+)',", self.code)
        for expected in ("cover", "reveal", "countdown"):
            self.assertIn(expected, names)
        self.assertEqual(len(names), len(set(names)), "duplicate module names")
        # every init runs inside try/catch and failures are reported as warnings
        runner = self.code[self.code.index("function start()"):]
        runner = runner[:runner.index("document.addEventListener")]
        self.assertRegex(runner, r"try\s*\{\s*module\.init\(document\);\s*\}\s*catch")
        self.assertIn("console.warn", runner)
        self.assertNotRegex(self.code, r"console\.(?:log|error|info|debug)\b")

    def test_selectors_use_data_attributes_only(self):
        selectors = re.findall(r"querySelector(?:All)?\(\s*([^)]*)\)", self.code)
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertIn("[data-", selector)
            self.assertNotRegex(selector, r"['\"]\s*[.#]")
        self.assertNotRegex(self.code, r"getElementsByClassName|getElementById")

    def test_es2017_compatible_syntax(self):
        for pattern in (r"\?\.", r"\?\?", r"\bawait\b.*\bfor\b", r"#\w+\s*[=(;]",
                        r"\bstatic\s*\{", r"\.replaceAll\(", r"\.at\("):
            self.assertNotRegex(self.code, pattern)

    def test_size_is_a_few_kilobytes(self):
        self.assertLess(len(self.code.encode("utf-8")), 12 * 1024)


class MotionStylesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        css = APP_CSS.read_text(encoding="utf-8")
        start = css.index("\n   7. Motion & reveal")
        end = css.index("\n   8. Gallery & lightbox")
        cls.section = strip_comments("/*" + css[start:end] + "*/")

    def test_hidden_states_require_js_class_and_marker(self):
        rules = re.findall(r"([^{}]+)\{[^{}]*\bopacity:\s*0\b[^{}]*\}", self.section)
        self.assertTrue(rules)
        for selector in rules:
            if selector.strip() == "from":
                continue
            for part in selector.split(","):
                self.assertRegex(part.strip(), r"^html\.js\.[a-z-]+ ")

    def test_print_and_reduced_motion_fallbacks(self):
        self.assertRegex(self.section, r"@media print\s*\{")
        self.assertRegex(self.section, r"@media \(prefers-reduced-motion: reduce\)\s*\{")

    def test_only_opacity_and_vertical_transform_are_animated(self):
        self.assertNotRegex(self.section, r"translateX|translate\(|translate3d|scale|margin|inset|width|height")


if __name__ == "__main__":
    unittest.main()
