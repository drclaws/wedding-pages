"""Tests for the template engine (values, conditions, loops, errors)."""

from __future__ import annotations

import unittest

from tests.support import build

render = build.render
TemplateError = build.TemplateError


class ValueTests(unittest.TestCase):
    def test_simple_substitution(self):
        self.assertEqual(render("<p>{{greeting}}</p>", {"greeting": "Привет"}), "<p>Привет</p>")

    def test_spaces_inside_placeholder(self):
        self.assertEqual(render("{{  greeting  }}", {"greeting": "x"}), "x")

    def test_nested_field(self):
        context = {"venue": {"name": "Место", "geo": {"lat": 1.5}}}
        self.assertEqual(render("{{venue.name}}/{{venue.geo.lat}}", context), "Место/1.5")

    def test_html_is_escaped(self):
        rendered = render("{{note}}", {"note": "<b>&\"x\"'y'</b>"})
        self.assertEqual(rendered, "&lt;b&gt;&amp;&quot;x&quot;&#x27;y&#x27;&lt;/b&gt;")
        self.assertNotIn("<b>", rendered)

    def test_braces_in_values_are_escaped(self):
        # A value may legitimately contain {{ }}; it must not look like a
        # placeholder in the result.
        self.assertEqual(
            render("{{note}}", {"note": "{{greeting}}"}),
            "&#123;&#123;greeting&#125;&#125;",
        )

    def test_comment_syntax_in_values_is_escaped(self):
        self.assertEqual(
            render("{{note}}", {"note": "<!-- if:x -->"}),
            "&lt;!-- if:x --&gt;",
        )

    def test_single_line_break_becomes_br(self):
        self.assertEqual(render("{{note}}", {"note": "a\nb"}), "a<br>b")

    def test_blank_line_becomes_paragraph(self):
        self.assertEqual(render("{{note}}", {"note": "a\n\nb\nc"}), "a</p><p>b<br>c")

    def test_crlf_and_multiple_blank_lines(self):
        self.assertEqual(
            render("{{note}}", {"note": "a\r\n\r\n\r\nb\r\nc"}), "a</p><p>b<br>c"
        )

    def test_leading_and_trailing_blank_lines_are_dropped(self):
        self.assertEqual(render("{{note}}", {"note": "\n\na\n\n"}), "a")

    def test_escaping_happens_before_line_breaks(self):
        self.assertEqual(render("{{note}}", {"note": "<i>\n</i>"}), "&lt;i&gt;<br>&lt;/i&gt;")

    def test_booleans_and_numbers(self):
        context = {"flag": True, "off": False, "count": 3, "ratio": 2.5}
        self.assertEqual(
            render("{{flag}}|{{off}}|{{count}}|{{ratio}}", context), "True|False|3|2.5"
        )

    def test_null_and_empty_string(self):
        self.assertEqual(render("[{{a}}][{{b}}]", {"a": None, "b": ""}), "[][]")

    def test_missing_field_is_an_error(self):
        with self.assertRaises(TemplateError) as caught:
            render("line1\n{{venue.nme}}", {"venue": {"name": "x"}})
        self.assertIn("venue.nme", str(caught.exception))
        self.assertIn("line 2", str(caught.exception))

    def test_missing_nested_parent_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{{video.file}}", {"video": None})

    def test_array_value_is_an_error(self):
        with self.assertRaises(TemplateError) as caught:
            render("{{photos}}", {"photos": ["a"]})
        self.assertIn("each:photos", str(caught.exception))

    def test_object_value_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{{venue}}", {"venue": {"name": "x"}})


class ConditionTests(unittest.TestCase):
    def test_if_true_and_false(self):
        template = "<!-- if:flag -->yes<!-- endif -->"
        self.assertEqual(render(template, {"flag": True}), "yes")
        self.assertEqual(render(template, {"flag": False}), "")

    def test_if_not(self):
        template = "<!-- if:!flag -->no<!-- endif -->"
        self.assertEqual(render(template, {"flag": False}), "no")
        self.assertEqual(render(template, {"flag": True}), "")

    def test_missing_field_is_falsy_and_not_an_error(self):
        self.assertEqual(render("<!-- if:nope -->x<!-- endif -->", {}), "")
        self.assertEqual(render("<!-- if:!nope -->x<!-- endif -->", {}), "x")

    def test_truthiness_table(self):
        cases = [
            (True, True),
            (False, False),
            (None, False),
            ("", False),
            ("text", True),
            (" ", True),
            (0, False),
            (0.0, False),
            (1, True),
            (-1, True),
            ([], False),
            (["a"], True),
            ({}, False),
            ({"a": 1}, True),
        ]
        for value, expected in cases:
            with self.subTest(value=repr(value)):
                rendered = render("<!-- if:v -->x<!-- endif -->", {"v": value})
                self.assertEqual(rendered, "x" if expected else "")
                negated = render("<!-- if:!v -->x<!-- endif -->", {"v": value})
                self.assertEqual(negated, "" if expected else "x")

    def test_nested_conditions(self):
        template = (
            "<!-- if:a -->A<!-- if:b -->B<!-- endif -->"
            "<!-- if:!c -->C<!-- endif --><!-- endif -->"
        )
        self.assertEqual(render(template, {"a": True, "b": True, "c": False}), "ABC")
        self.assertEqual(render(template, {"a": True, "b": False, "c": True}), "A")
        self.assertEqual(render(template, {"a": False, "b": True, "c": False}), "")

    def test_nested_path_in_condition(self):
        template = "<!-- if:venue.ready -->ready<!-- endif -->"
        self.assertEqual(render(template, {"venue": {"ready": True}}), "ready")
        self.assertEqual(render(template, {"venue": {"ready": False}}), "")

    def test_spaces_around_directives(self):
        self.assertEqual(render("<!--  if:flag  -->x<!--  endif  -->", {"flag": True}), "x")
        self.assertEqual(render("<!--if:flag-->x<!--endif-->", {"flag": True}), "x")
        self.assertEqual(render("<!-- if: ! flag -->x<!-- endif -->", {"flag": False}), "x")

    def test_ordinary_comments_are_kept(self):
        template = "<!-- a note for the reader --><!-- if:flag -->x<!-- endif -->"
        self.assertEqual(
            render(template, {"flag": True}), "<!-- a note for the reader -->x"
        )


class LoopTests(unittest.TestCase):
    def test_each_over_objects(self):
        template = "<!-- each:schedule -->[{{.time}} {{.title}}]<!-- endeach -->"
        context = {"schedule": [{"time": "16:00", "title": "A"}, {"time": "17:00", "title": "B"}]}
        self.assertEqual(render(template, context), "[16:00 A][17:00 B]")

    def test_each_over_scalars(self):
        template = "<!-- each:photos -->({{.}})<!-- endeach -->"
        self.assertEqual(render(template, {"photos": ["a.webp", "b.webp"]}), "(a.webp)(b.webp)")

    def test_outer_context_inside_each(self):
        template = "<!-- each:photos -->{{mediaDir}}/{{.}} <!-- endeach -->"
        context = {"mediaDir": "dir", "photos": ["a", "b"]}
        self.assertEqual(render(template, context), "dir/a dir/b ")

    def test_condition_on_item_field(self):
        template = "<!-- each:schedule -->{{.title}}<!-- if:.text -->:{{.text}}<!-- endif -->|<!-- endeach -->"
        context = {"schedule": [{"title": "A", "text": "x"}, {"title": "B", "text": ""}]}
        self.assertEqual(render(template, context), "A:x|B|")

    def test_negated_condition_on_item_field(self):
        template = "<!-- each:items --><!-- if:!.done -->{{.name}}<!-- endif --><!-- endeach -->"
        context = {"items": [{"name": "a", "done": True}, {"name": "b", "done": False}]}
        self.assertEqual(render(template, context), "b")

    def test_nested_each(self):
        template = "<!-- each:groups -->[<!-- each:.items -->{{.}}<!-- endeach -->]<!-- endeach -->"
        context = {"groups": [{"items": ["a", "b"]}, {"items": []}]}
        self.assertEqual(render(template, context), "[ab][]")

    def test_each_over_empty_and_null(self):
        template = "<!-- each:items -->x<!-- endeach -->"
        self.assertEqual(render(template, {"items": []}), "")
        self.assertEqual(render(template, {"items": None}), "")

    def test_each_over_missing_field_is_an_error(self):
        with self.assertRaises(TemplateError) as caught:
            render("<!-- each:items -->x<!-- endeach -->", {})
        self.assertIn("items", str(caught.exception))

    def test_each_over_non_array_is_an_error(self):
        with self.assertRaises(TemplateError) as caught:
            render("<!-- each:items -->x<!-- endeach -->", {"items": "text"})
        self.assertIn("expected an array", str(caught.exception))


class SyntaxErrorTests(unittest.TestCase):
    def assertTemplateError(self, template, *fragments, context=None):
        with self.assertRaises(TemplateError) as caught:
            render(template, {} if context is None else context)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def test_unclosed_if(self):
        self.assertTemplateError("a\nb\n<!-- if:flag -->x\n", "line 3", "not closed")

    def test_unclosed_each(self):
        self.assertTemplateError("<!-- each:items -->x", "line 1", "not closed")

    def test_extra_endif(self):
        self.assertTemplateError("x\n<!-- endif -->", "line 2", "without a matching")

    def test_extra_endeach(self):
        self.assertTemplateError("<!-- endeach -->", "line 1", "without a matching")

    def test_crossed_blocks(self):
        self.assertTemplateError(
            "<!-- if:a -->\n<!-- each:items -->\n<!-- endif -->\n<!-- endeach -->",
            "line 3",
            "does not match",
        )

    def test_malformed_directive(self):
        self.assertTemplateError("<!-- if: -->x<!-- endif -->", "no field path")
        self.assertTemplateError("<!-- IF:flag -->x<!-- endif -->", "malformed directive")
        self.assertTemplateError("<!-- if:a b -->x<!-- endif -->", "malformed directive")
        self.assertTemplateError("<!-- each: -->x<!-- endeach -->", "no field path")

    def test_invalid_field_path(self):
        self.assertTemplateError("<!-- if:a..b -->x<!-- endif -->", "invalid field path")
        self.assertTemplateError("{{a-b}}", "invalid field path")
        self.assertTemplateError("{{}}", "no field path")

    def test_item_path_outside_each(self):
        self.assertTemplateError("{{.time}}", "not inside")
        self.assertTemplateError("<!-- if:.text -->x<!-- endif -->", "not inside")
        self.assertTemplateError(
            "<!-- if:flag -->{{.}}<!-- endif -->", "not inside", context={"flag": True}
        )

    def test_stray_braces(self):
        self.assertTemplateError("ok\n{{a", "line 2", "unmatched '{{'")
        self.assertTemplateError("a}}", "unmatched '}}'")
        self.assertTemplateError("{{{a}}}", "invalid field path")

    def test_line_numbers_point_at_the_directive(self):
        message = self.assertTemplateError(
            "1\n2\n3\n<!-- each:items -->\n<!-- endif -->", "line 5"
        )
        self.assertIn("opened at line 4", message)


class RenderedOutputCheckTests(unittest.TestCase):
    def test_clean_output_passes(self):
        build.check_rendered("<p>ok</p>\n<!-- plain comment -->")

    def test_leftover_placeholder(self):
        with self.assertRaises(TemplateError) as caught:
            build.check_rendered("<p>a</p>\n<p>{{name}}</p>", "page.html")
        self.assertIn("line 2", str(caught.exception))
        self.assertIn("page.html", str(caught.exception))

    def test_leftover_closing_braces(self):
        with self.assertRaises(TemplateError):
            build.check_rendered("a }} b")

    def test_leftover_directives(self):
        for leftover in ("<!-- if:a -->", "<!-- endif -->", "<!--each:a-->", "<!-- endeach -->"):
            with self.subTest(leftover=leftover):
                with self.assertRaises(TemplateError):
                    build.check_rendered(leftover)


class TemplateReuseTests(unittest.TestCase):
    def test_parsed_template_renders_many_contexts(self):
        template = build.parse_template("<!-- if:note -->{{note}}<!-- endif -->", "t.html")
        self.assertEqual(template.render({"note": "a"}), "a")
        self.assertEqual(template.render({"note": ""}), "")
        self.assertEqual(template.render({}), "")

    def test_error_message_uses_the_template_name(self):
        with self.assertRaises(TemplateError) as caught:
            build.parse_template("<!-- endif -->", "template.html")
        self.assertTrue(str(caught.exception).startswith("template.html line 1:"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
