"""Tests for template fragments: include, choosing a fragment by type, with."""

from __future__ import annotations

import os
import shutil
import unittest
from unittest import mock

from tests.support import (
    TEMPLATE,
    CliTestCase,
    TempDirTestCase,
    build,
    invitations_data,
    tree_files,
)

render = build.render
TemplateError = build.TemplateError

#: The page template chooses widgets by type (each widget is an item).
WIDGETS_PAGE = (
    "<main>\n<!-- each:widgets -->\n<!-- include:widgets/{{.type}} -->\n<!-- endeach -->\n</main>"
)
WIDGETS = {"widgets/text": "<p>{{.text}}</p>", "widgets/pic": '<img alt="{{.alt}}">'}


def parse(source: str, fragments: dict[str, str] | None = None, name: str = "template.html"):
    return build.parse_template(source, name, build.Fragments(fragments))


def error_of(call, *args, **kwargs) -> str:
    """The message of the TemplateError that `call(*args, **kwargs)` raises."""
    try:
        call(*args, **kwargs)
    except TemplateError as exc:
        return str(exc)
    raise AssertionError("TemplateError not raised")


class IncludeTests(unittest.TestCase):
    def test_include_sees_the_fields_of_the_page(self):
        self.assertEqual(
            render("<p><!-- include:parts/hello --></p>", {"name": "Ева"},
                   fragments={"parts/hello": "Привет, {{name}}!"}),
            "<p>Привет, Ева!</p>",
        )

    def test_include_inside_each_sees_the_item(self):
        self.assertEqual(
            render("<!-- each:items --><!-- include:parts/item --><!-- endeach -->",
                   {"items": [{"t": "a"}, {"t": "b"}]},
                   fragments={"parts/item": "[{{.t}}]"}),
            "[a][b]",
        )

    def test_include_inside_if_of_the_page(self):
        template = "<!-- if:flag --><!-- include:parts/a --><!-- endif -->"
        fragments = {"parts/a": "A{{name}}"}
        self.assertEqual(render(template, {"flag": True, "name": "!"}, fragments=fragments), "A!")
        self.assertEqual(render(template, {"flag": False}, fragments=fragments), "")
        # `if` gives no item: a fragment that needs one is refused
        message = error_of(parse, "<!-- if:flag -->\n<!-- include:parts/b --><!-- endif -->",
                           {"parts/b": "{{.t}}"})
        self.assertIn("template.html line 2", message)
        self.assertIn("uses the current item", message)

    def test_nested_directories_and_includes_between_fragments(self):
        fragments = {
            "sections/card": "<section><!-- include:partials/cards/title --></section>",
            "partials/cards/title": "<h2>{{title}}</h2>",
        }
        self.assertEqual(
            render("<!-- include:sections/card -->", {"title": "T"}, fragments=fragments),
            "<section><h2>T</h2></section>",
        )

    def test_a_fragment_that_needs_the_item_outside_each_or_with_is_a_parse_error(self):
        message = error_of(parse, "x\n<!-- include:parts/item -->", {"parts/item": "{{.t}}"})
        self.assertTrue(message.startswith("template.html line 2: <!-- include:parts/item -->"),
                        message)
        self.assertIn("include it inside <!-- each:… --> or <!-- with:… -->", message)

    def test_needing_the_item_is_transitive_and_found_at_parse_time(self):
        fragments = {
            "a/top": "<div>\n<!-- include:a/outer --></div>",
            "a/outer": "<p>\n<!-- if:flag --><!-- include:a/inner --><!-- endif --></p>",
            "a/inner": "{{.t}}",
        }
        # the fragments on their own are fine: they pass their item on
        build.Fragments(fragments)
        for name in ("a/top", "a/outer"):
            with self.subTest(name=name):
                message = error_of(parse, f"x\n<!-- include:{name} -->", fragments)
                self.assertIn("template.html line 2", message)
                self.assertIn("include it inside", message)
        self.assertEqual(
            parse("<!-- with:x --><!-- include:a/top --><!-- endwith -->", fragments)
            .render({"x": {"t": 1}, "flag": True}),
            "<div>\n<p>\n1</p></div>",
        )

    def test_a_fragment_with_its_own_each_or_with_needs_no_item(self):
        fragments = {
            "a/own": "<!-- with:x --><!-- include:a/inner --><!-- endwith -->"
                     "<!-- each:list --><!-- include:a/inner --><!-- endeach -->",
            "a/inner": "{{.t}}",
        }
        self.assertEqual(
            render("<!-- include:a/own -->", {"x": {"t": 1}, "list": [{"t": 2}, {"t": 3}]},
                   fragments=fragments),
            "123",
        )

    def test_a_missing_fragment_is_a_parse_error(self):
        message = error_of(parse, "a\n\n<!-- include:parts/nope -->")
        self.assertEqual(
            message,
            "template.html line 3: <!-- include:parts/nope -->: "
            "no fragment fragments/parts/nope.html",
        )
        message = error_of(build.Fragments, {"w/a": "x\n<!-- include:w/typo -->"})
        self.assertTrue(message.startswith("fragments/w/a.html line 2:"), message)
        self.assertIn("no fragment fragments/w/typo.html", message)

    def test_cycles_of_fixed_includes_are_found_when_loading(self):
        cases = (
            ({"p/a": "<!-- include:p/a -->"}, "p/a -> p/a"),
            ({"p/a": "<!-- include:p/b -->",
              "p/b": "\n<!-- if:x --><!-- include:p/a --><!-- endif -->"}, "p/a -> p/b -> p/a"),
            ({"p/a": "<!-- include:p/b -->", "p/b": "<!-- include:p/c -->",
              "p/c": "<!-- each:x --><!-- include:p/b --><!-- endeach -->"}, "p/b -> p/c -> p/b"),
        )
        for fragments, cycle in cases:
            with self.subTest(cycle=cycle):
                message = error_of(build.Fragments, fragments)
                self.assertIn("include each other in a cycle: " + cycle, message)
        message = error_of(build.Fragments, cases[1][0])
        self.assertTrue(message.startswith("fragments/p/b.html line 2:"), message)

    def test_malformed_includes(self):
        for source in (
            "<!-- Include:p/a -->",
            "<!-- include:P/a -->",
            "<!-- include:p//a -->",
            "<!-- include:p/a.html -->",
            "<!-- include:../p/a -->",
            "<!-- include:p/" + "a" * 41 + " -->",
            "<!-- include: -->",
            "<!-- include:{{.type}} -->",
            "<!-- include:w/x-{{.type}} -->",
            "<!-- include:w/{{.type}}/x -->",
        ):
            with self.subTest(source=source):
                self.assertIn("malformed directive", error_of(parse, source, {"p/a": "A"}))

    def test_the_fragments_are_not_read_while_rendering(self):
        fragments = build.Fragments({"w/a": "A{{.v}}"})
        template = build.parse_template(
            "<!-- each:x --><!-- include:w/{{.type}} --><!-- endeach -->", "t", fragments
        )
        # nothing but the parsed fragments is used: no file system access
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
                mock.patch("os.stat", side_effect=AssertionError("stat")):
            self.assertEqual(template.render({"x": [{"type": "a", "v": 1}]}), "A1")


class ChooseByTypeTests(unittest.TestCase):
    def test_choose_by_type(self):
        context = {"widgets": [{"type": "text", "text": "a"}, {"type": "pic", "alt": "b"}]}
        self.assertEqual(
            render(WIDGETS_PAGE, context, fragments=WIDGETS),
            '<main>\n\n<p>a</p>\n\n<img alt="b">\n\n</main>',
        )

    def test_choose_by_a_field_of_a_nested_object(self):
        # the field chooses the fragment; the item stays the same
        fragments = {"media/image": '<img src="{{.src}}">', "media/video": '<video src="{{.src}}">'}
        template = "<!-- each:items --><!-- include:media/{{.media.type}} --><!-- endeach -->"
        context = {"items": [{"media": {"type": "video"}, "src": "a"},
                             {"media": {"type": "image"}, "src": "b"}]}
        self.assertEqual(render(template, context, fragments=fragments),
                         '<video src="a"><img src="b">')
        message = error_of(render, template, {"items": [{"media": {"type": 1}}]},
                           fragments=fragments)
        self.assertIn("the value of '.media.type' is not a fragment name", message)

    def test_choose_inside_with(self):
        self.assertEqual(
            render("<!-- with:hero --><!-- include:widgets/{{.type}} --><!-- endwith -->",
                   {"hero": {"type": "text", "text": "x"}}, fragments=WIDGETS),
            "<p>x</p>",
        )

    def test_an_unknown_type_is_an_error_that_lists_the_known_ones(self):
        message = error_of(render, WIDGETS_PAGE, {"widgets": [{"type": "slideshow"}]},
                           fragments=WIDGETS)
        self.assertEqual(
            message,
            "template line 3: <!-- include:widgets/{{.type}} -->: no fragment in "
            "fragments/widgets/ for the value of '.type' (known: pic, text)",
        )
        self.assertNotIn("slideshow", message)

    def test_a_value_that_is_not_a_fragment_name_is_refused_and_never_quoted(self):
        fragments = dict(WIDGETS, **{"widgets/con": "c", "widgets/" + "a" * 40: "long"})
        for value in (
            "../secret", "../../build", "widgets/text", "x/y", "text/", "/text",
            "Text", "TEXT", "CON", "a b", " text", "text\n", ".text", "-text", "1text",
            "te_xt", "%2e%2e", "text%00", "tеxt",  # Cyrillic "е"
            "ｔｅｘｔ",  # fullwidth letters
            "a" * 41, "a" * 10000, "",
            5, 0, None, True, False, ["text"], {"type": "text"},
        ):
            with self.subTest(value=repr(value)[:40]):
                message = error_of(render, WIDGETS_PAGE, {"widgets": [{"type": value}]},
                                   fragments=fragments)
                self.assertEqual(
                    message,
                    "template line 3: <!-- include:widgets/{{.type}} -->: "
                    "the value of '.type' is not a fragment name",
                )
        # names that do exist among the files are fine, whatever they look like
        self.assertEqual(render(WIDGETS_PAGE, {"widgets": [{"type": "con"}]},
                                fragments=fragments), "<main>\n\nc\n\n</main>")

    def test_the_data_cannot_leave_the_directory_of_the_template(self):
        fragments = dict(WIDGETS, **{"partials/secret": "S", "widgets/deep/secret": "D"})
        for value in ("secret", "partials/secret", "deep/secret", "../partials/secret"):
            with self.subTest(value=value):
                message = error_of(render, WIDGETS_PAGE, {"widgets": [{"type": value}]},
                                   fragments=fragments)
                self.assertNotIn(value, message.replace("fragments/widgets/", ""))
                self.assertRegex(message, "is not a fragment name|known: pic, text\\)$")

    def test_a_missing_field_is_an_error(self):
        message = error_of(render, WIDGETS_PAGE, {"widgets": [{"text": "a"}]}, fragments=WIDGETS)
        self.assertIn("line 3", message)
        self.assertIn("field '.type' not found", message)

    def test_choosing_outside_each_or_with_is_a_parse_error(self):
        for source in ("<!-- include:widgets/{{.type}} -->",
                       "<!-- if:x --><!-- include:widgets/{{.type}} --><!-- endif -->"):
            with self.subTest(source=source):
                self.assertIn("must be inside <!-- each:… --> or <!-- with:… -->",
                              error_of(parse, source, WIDGETS))
        # also at the top of a fragment: it cannot choose again by its own item
        box = "\n<!-- include:widgets/{{.type}} -->"
        message = error_of(build.Fragments, dict(WIDGETS, **{"widgets/box": box}))
        self.assertTrue(message.startswith("fragments/widgets/box.html line 2:"), message)
        self.assertIn("must be inside", message)
        # only a field of the current item chooses
        message = error_of(
            parse, "<!-- each:w --><!-- include:widgets/{{kind}} --><!-- endeach -->", WIDGETS
        )
        self.assertIn("chosen by a field of the current item", message)

    def test_choosing_from_an_empty_or_missing_directory_is_a_parse_error(self):
        template = "<!-- each:w -->\n<!-- include:widgets/{{.type}} --><!-- endeach -->"
        for fragments in (None, {"parts/a": "a"}, {"widgets/deep/a": "a"}):
            with self.subTest(fragments=fragments):
                message = error_of(parse, template, fragments)
                self.assertIn("template.html line 2", message)
                self.assertIn("no fragments in fragments/widgets/", message)

    def test_recursion_through_the_data_is_bounded(self):
        # a container widget renders its children with the same choice
        fragments = {
            "w/box": "[<!-- each:.children --><!-- include:w/{{.type}} --><!-- endeach -->]",
            "w/leaf": "{{.v}}",
        }
        template = "<!-- each:top --><!-- include:w/{{.type}} --><!-- endeach -->"

        def tree(boxes: int) -> dict:
            node = {"type": "leaf", "v": "x"}
            for _ in range(boxes):
                node = {"type": "box", "children": [node]}
            return node

        limit = build.MAX_INCLUDE_DEPTH
        self.assertEqual(limit, 12)
        # the leaf and the boxes around it: exactly `limit` nested fragments
        self.assertEqual(render(template, {"top": [tree(limit - 1)]}, fragments=fragments),
                         "[" * (limit - 1) + "x" + "]" * (limit - 1))
        message = error_of(render, template, {"top": [tree(limit)]}, fragments=fragments)
        self.assertTrue(message.startswith(
            "fragments/w/box.html line 1: <!-- include:w/{{.type}} -->: "
            "fragments are nested deeper than 12 levels (included from "
            "fragments/w/box.html line 1 <- "), message)
        self.assertTrue(message.endswith(" <- template line 1)"), message)
        self.assertEqual(message.count(" <- "), limit - 1)

    def test_recursion_through_a_field_of_the_page_is_bounded(self):
        # the fragment enters the same field of the page again and again
        loop = "<!-- with:again --><!-- include:w/{{.type}} --><!-- endwith -->"
        message = error_of(render, loop, {"again": {"type": "loop"}}, fragments={"w/loop": loop})
        self.assertIn("nested deeper than 12 levels", message)


class MessageTests(unittest.TestCase):
    def test_an_error_names_the_fragment_the_line_and_the_chain(self):
        fragments = {
            "sections/custom": (
                "<section>\n<!-- each:.widgets -->\n"
                "<!-- include:widgets/{{.type}} --><!-- endeach -->\n</section>"
            ),
            "widgets/text": "<p>\n\n\n\n{{.txt}}</p>",
        }
        template = "<main>\n" * 32 + (
            "<!-- each:sections --><!-- include:sections/{{.type}} --><!-- endeach --></main>"
        )
        widget = {"type": "text", "text": "Ева"}
        context = {"sections": [{"type": "custom", "widgets": [widget]}]}
        message = error_of(parse(template, fragments).render, context)
        self.assertEqual(
            message,
            "fragments/widgets/text.html line 5: field '.txt' not found "
            "(included from fragments/sections/custom.html line 3 <- template.html line 33)",
        )

    def test_the_chain_goes_through_fixed_includes(self):
        fragments = {
            "sections/custom": "<section>\n<!-- include:partials/head -->\n</section>",
            "partials/head": "<h2>\n{{.title}}</h2><!-- with:.extra -->{{.x}}<!-- endwith -->",
        }
        template = "<!-- each:s -->\n<!-- include:sections/{{.type}} --><!-- endeach -->"
        message = error_of(parse(template, fragments).render,
                           {"s": [{"type": "custom", "title": "Ева", "extra": ["Боб"]}]})
        self.assertEqual(
            message,
            "fragments/partials/head.html line 2: <!-- with:.extra -->: field '.extra' is "
            "an array, expected an object (included from fragments/sections/custom.html "
            "line 2 <- template.html line 2)",
        )
        self.assertNotIn("Ева", message)
        self.assertNotIn("Боб", message)

    def test_parse_errors_in_fragments_name_the_file_and_the_line(self):
        for source, expected in (
            ("<!-- if:x -->", "is not closed"),
            ("}}", "unmatched"),
            ("<!-- endwith -->", "without a matching"),
            ("<!-- with:x --><!-- endif -->", "does not match"),
            ("<!-- include:Bad -->", "malformed directive"),
            ("{{a-b}}", "invalid field path"),
        ):
            with self.subTest(source=source):
                message = error_of(build.Fragments, {"w/x": "\n" + source})
                self.assertTrue(message.startswith("fragments/w/x.html line 2:"), message)
                self.assertIn(expected, message)

    def test_bad_fragment_names(self):
        for name in ("W/x", "w/x.y", "w//x", "../x", "w/../x", "w/1x", "w/x_y", "w/-x", "",
                     "/w/x", "w/x/", "w/" + "x" * 41, "w/ｘ"):
            with self.subTest(name=name):
                self.assertIn("a fragment name uses a-z", error_of(build.Fragments, {name: "x"}))


class EscapingTests(unittest.TestCase):
    def test_values_in_fragments_are_escaped_as_everywhere(self):
        self.assertEqual(
            render("<!-- each:w --><!-- include:w/{{.type}} --><!-- endeach -->",
                   {"w": [{"type": "t", "text": "<b>{{x}}</b>\n\nb\nc"}]},
                   fragments={"w/t": "<p>{{.text}}</p>"}),
            "<p>&lt;b&gt;&#123;&#123;x&#125;&#125;&lt;/b&gt;</p><p>b<br>c</p>",
        )

    def test_a_value_never_becomes_a_directive(self):
        fragments = {"w/a": "A"}
        for value in ("<!-- include:w/a -->", "<!-- with:x -->", "<!-- endwith -->"):
            with self.subTest(value=value):
                rendered = render("<p>{{v}}</p>", {"v": value}, fragments=fragments)
                self.assertTrue(rendered.startswith("<p>&lt;!-- "), rendered)
                build.check_rendered(rendered)

    def test_comments_in_fragments_are_removed_from_the_page(self):
        rendered = render("<!-- include:p/a -->", {},
                          fragments={"p/a": "<!-- a note for the reader -->A"})
        self.assertEqual(rendered, "<!-- a note for the reader -->A")
        self.assertEqual(build.strip_html_comments(rendered), "A")

    def test_blocks_do_not_cross_fragment_boundaries(self):
        self.assertIn("not closed", error_of(build.Fragments, {"p/open": "<!-- if:x -->"}))
        self.assertIn("without a matching",
                      error_of(build.Fragments, {"p/close": "<!-- endif -->"}))
        self.assertIn("not closed", error_of(
            parse, "<!-- if:x --><!-- include:p/a -->", {"p/a": "A"}))


class WithTests(unittest.TestCase):
    def test_with_makes_an_object_the_current_item(self):
        self.assertEqual(
            render("<!-- with:venue -->{{.name}}/{{coupleNames}}<!-- endwith -->",
                   {"venue": {"name": "V"}, "coupleNames": "C"}),
            "V/C",
        )

    def test_with_skips_null_false_and_an_empty_object(self):
        for value in (None, False, {}):
            with self.subTest(value=value):
                self.assertEqual(render("<!-- with:v -->x{{.a}}<!-- endwith -->", {"v": value}), "")

    def test_with_over_a_missing_field_or_not_an_object_is_an_error(self):
        message = error_of(render, "\n<!-- with:v -->x<!-- endwith -->", {})
        self.assertEqual(message, "template line 2: <!-- with:v -->: field 'v' not found")
        for value, kind in (("", "a string"), ("Ева", "a string"), (0, "a number"),
                            (True, "a boolean"), ([], "an array"), ([{"a": 1}], "an array")):
            with self.subTest(value=value):
                message = error_of(render, "<!-- with:v -->x<!-- endwith -->", {"v": value})
                self.assertIn(f"field 'v' is {kind}, expected an object", message)
                self.assertNotIn("Ева", message)

    def test_with_inside_each_and_nested_item_paths(self):
        context = {"events": [{"title": "E", "location": {"name": "L", "geo": {"lat": 1}}},
                              {"title": "F", "location": None}]}
        self.assertEqual(
            render("<!-- each:events -->{{.title}}<!-- with:.location -->@{{.name}}"
                   "<!-- with:.geo -->:{{.lat}}<!-- endwith --><!-- endwith -->;<!-- endeach -->",
                   context),
            "E@L:1;F;",
        )

    def test_with_inside_a_fragment(self):
        fragments = {
            "partials/card": "<!-- with:.location --><b>{{.name}}</b><!-- endwith -->"
                             "<!-- with:site --><i>{{.title}}</i><!-- endwith -->",
        }
        context = {"site": {"title": "S"}, "events": [{"location": {"name": "L"}},
                                                     {"location": None}]}
        self.assertEqual(
            render("<!-- each:events --><!-- include:partials/card --><!-- endeach -->",
                   context, fragments=fragments),
            "<b>L</b><i>S</i><i>S</i>",
        )

    def test_with_dot_is_refused(self):
        template = "<!-- each:a --><!-- with:. -->x<!-- endwith --><!-- endeach -->"
        self.assertIn("'.' is the current item already", error_of(render, template, {"a": []}))

    def test_crossed_and_unclosed_with(self):
        crossed = "<!-- with:a -->\n<!-- endif -->"
        self.assertIn("does not match", error_of(render, crossed, {"a": {}}))
        self.assertIn("missing <!-- endwith -->", error_of(render, "<!-- with:a -->x", {"a": {}}))
        self.assertIn("without a matching", error_of(render, "<!-- endwith -->", {}))

    def test_item_paths_in_the_page_need_each_or_with(self):
        message = error_of(render, "{{.a}}", {})
        self.assertIn("not inside <!-- each:… --> or <!-- with:… -->", message)


class ReservedWordsTests(unittest.TestCase):
    def test_new_directives_count_as_leftovers(self):
        for leftover in ("<!-- include:a/b -->", "<!--with:a-->", "<!-- endwith -->",
                         "<!-- INCLUDE:x -->"):
            with self.subTest(leftover=leftover):
                with self.assertRaises(TemplateError):
                    build.check_rendered(leftover)
                self.assertTrue(build.check_html(leftover, lambda _path: True))

    def test_comments_that_start_with_the_new_words_are_directives(self):
        for comment in ("<!-- with: love -->", "<!-- include: the map -->", "<!-- endwith -->"):
            with self.subTest(comment=comment):
                with self.assertRaises(TemplateError):
                    render(comment, {})
        # other comments stay comments
        for comment in ("<!-- without a word -->", "<!-- includes: nothing -->"):
            with self.subTest(comment=comment):
                self.assertEqual(render(comment, {}), comment)


class LoadFragmentsTests(TempDirTestCase):
    def write(self, relative: str, text: str = "x") -> None:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_loads_nested_directories_and_skips_dot_files(self):
        self.write("fragments/widgets/text.html", "<p>{{.text}}</p>")
        self.write("fragments/widgets/cards/venue.html", "<b>{{.name}}</b>")
        self.write("fragments/sections/cover.html", "﻿<h1>{{title}}</h1>")
        (self.tmp / "fragments" / ".DS_Store").write_bytes(b"\x00\x01")
        self.write("fragments/widgets/.text.html.swp", "not a fragment")
        self.write("fragments/.drafts/notes.md", "skipped with its directory")
        fragments = build.load_fragments(self.tmp / "fragments")
        self.assertEqual(len(fragments), 3)
        self.assertEqual(fragments.names("widgets"), ["text"])
        self.assertIn("widgets/cards/venue", fragments)
        self.assertEqual(fragments.get("sections/cover").shown, "fragments/sections/cover.html")
        self.assertEqual(
            render("<!-- include:sections/cover -->", {"title": "T"},
                   fragments={"sections/cover": "<h1>{{title}}</h1>"}),
            build.parse_template("<!-- include:sections/cover -->", "t", fragments)
            .render({"title": "T"}),
        )

    def test_a_missing_directory_means_no_fragments(self):
        fragments = build.load_fragments(self.tmp / "nope")
        self.assertEqual(len(fragments), 0)

    def test_other_extensions_are_refused(self):
        for name in ("notes.md", "widgets/text.htm", "widgets/text.HTML", "widgets/text"):
            with self.subTest(name=name):
                root = self.tmp / name.replace("/", "-")
                path = root / "fragments" / name
                path.parent.mkdir(parents=True)
                path.write_text("x", encoding="utf-8")
                with self.assertRaises(build.BuildError) as caught:
                    build.load_fragments(root / "fragments")
                self.assertIn("only .html files are allowed", str(caught.exception))

    def test_symbolic_links_are_refused(self):
        self.write("outside/secret.html", "secret")
        root = self.tmp / "fragments"
        (root / "w").mkdir(parents=True)
        os.symlink(self.tmp / "outside" / "secret.html", root / "w" / "link.html")
        with self.assertRaises(build.BuildError) as caught:
            build.load_fragments(root)
        self.assertIn("symbolic links are not allowed", str(caught.exception))
        os.remove(root / "w" / "link.html")
        os.symlink(self.tmp / "outside", root / "w" / "dir")
        with self.assertRaises(build.BuildError):
            build.load_fragments(root)
        os.remove(root / "w" / "dir")
        os.symlink(self.tmp / "outside", self.tmp / "linked")
        with self.assertRaises(build.BuildError) as caught:
            build.load_fragments(self.tmp / "linked")
        self.assertIn("must not be a symbolic link", str(caught.exception))

    def test_load_template_reads_the_fragments_next_to_it(self):
        self.write("template.html", "<!-- with:page -->\n<!-- include:parts/a --><!-- endwith -->")
        self.write("fragments/parts/a.html", "\n\n<p>{{.x}}</p>")
        template = build.load_template(self.tmp / "template.html")
        # everything is read before rendering: the files may go away
        shutil.rmtree(self.tmp / "fragments")
        self.assertEqual(template.render({"page": {"x": 1}}), "\n\n\n<p>1</p>")
        message = error_of(template.render, {"page": {"y": 1}})
        self.assertEqual(
            message,
            "fragments/parts/a.html line 3: field '.x' not found "
            "(included from template.html line 2)",
        )

    def test_the_stub_may_not_include(self):
        self.write("fragments/p/a.html", "A")
        for source in ("<p><!-- include:p/a --></p>", "<!-- include:w/{{.type}} -->"):
            with self.subTest(source=source):
                self.write("stub.html", source)
                with self.assertRaises(TemplateError) as caught:
                    build.load_stub(self.tmp / "stub.html")
                self.assertIn("include", str(caught.exception))
                self.assertIn("must not contain template syntax", str(caught.exception))


#: The fixture page with the venue and a footer moved into fragments.
FRAGMENT_TEMPLATE = TEMPLATE.replace(
    "<h2>{{venue.name}}</h2>", "<!-- with:venue --><!-- include:venue/card --><!-- endwith -->"
).replace("</body>", "<!-- include:parts/footer -->\n</body>")
FRAGMENT_FILES = {
    "venue/card.html": "<!-- a note in a fragment: never published -->\n"
                       '<h2 class="venue-card">{{.name}}</h2>\n',
    "parts/footer.html": "<footer><!-- include:parts/deep/sign --></footer>\n",
    "parts/deep/sign.html": "<p class=\"sign\">{{coupleNames}}</p>",
}


class BuildWithFragmentsTests(CliTestCase):
    template = FRAGMENT_TEMPLATE

    def setUp(self) -> None:
        super().setUp()
        fragments = self.code / build.FRAGMENTS_DIRNAME
        for name, text in FRAGMENT_FILES.items():
            (fragments / name).parent.mkdir(parents=True, exist_ok=True)
            (fragments / name).write_text(text, encoding="utf-8")
        (fragments / ".DS_Store").write_bytes(b"\x00")

    def test_fragments_are_rendered_but_never_published(self):
        self.assertIn("<!-- include:venue/card -->", FRAGMENT_TEMPLATE)
        result = self.build_in_process()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = tree_files(self.out)
        pages = sorted(f"i/{item['token']}/index.html" for item in invitations_data())
        self.assertEqual(
            sorted(name for name in files if name.endswith(".html")),
            sorted(["404.html", "index.html", *pages]),
        )
        for name in files:
            self.assertNotIn("fragment", name)
            self.assertNotIn("card", name)
            self.assertNotIn("footer", name)
            self.assertNotIn("DS_Store", name)
        page = (self.out / pages[0]).read_text(encoding="utf-8")
        self.assertIn('<h2 class="venue-card">', page)
        self.assertIn('<footer><p class="sign">', page)
        self.assertNotIn("never published", page)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_an_error_in_a_fragment_stops_the_build_without_data_in_the_log(self):
        (self.code / "fragments" / "venue" / "card.html").write_text(
            "<h2>\n{{.nme}}</h2>", encoding="utf-8"
        )
        line = FRAGMENT_TEMPLATE.splitlines().index(
            "<!-- with:venue --><!-- include:venue/card --><!-- endwith -->"
        ) + 1
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "fragments/venue/card.html line 2: field '.nme' not found "
            f"(included from template.html line {line})",
            result.stderr,
        )
        self.assertFalse(self.out.exists())
        self.assertNoPrivateData(result.stdout, result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
