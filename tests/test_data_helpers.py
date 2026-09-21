"""Tests for the shared data helpers (`tools/_data.py`): repeated JSON keys,
field paths, "did you mean" hints, identifiers and texts with the two forms of
address and placeholders.

Values that must never reach a message are marked with `SECRET`.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from tests.support import CliTestCase, TempDirTestCase, build, invitations_data, site_data

from tools import _data as data_tools

#: A fictional value that stands for personal data in keys, ids and texts.
SECRET = "Секрет-Метка"
DUPLICATE_TAIL = "(JSON keeps only the last one; merge or rename them)"
KNOWN = ("coupleNames", "greeting", "rsvpDeadline")
AVAILABLE = "available: {coupleNames}, {greeting}, {rsvpDeadline} (write '{{' for a literal brace)"


def duplicates_of(text: str) -> list[tuple[str, str]]:
    """(path, key) of every repeated key found in a document."""
    _value, found = data_tools.parse_json(text)
    return [(data_tools.format_path(item.path), item.key) for item in found]


class DuplicateKeyTests(unittest.TestCase):
    def test_a_document_without_repeats_is_parsed_like_json_loads(self):
        text = json.dumps(
            {
                "a": {"id": 1, "b": [{"id": 2}, {"id": 3, "c": {"id": 4}}]},
                "list": [[{"x": 1}], []],
                "text": "Ёж",
                "id": None,
            },
            ensure_ascii=False,
        )
        value, found = data_tools.parse_json(text)
        self.assertEqual(found, [])
        self.assertEqual(value, json.loads(text))
        self.assertEqual(list(value), ["a", "list", "text", "id"])
        for scalar in ("1", '"x"', "null", "[]"):
            self.assertEqual(data_tools.parse_json(scalar), (json.loads(scalar), []))

    def test_repeat_at_the_top_level(self):
        value, found = data_tools.parse_json('{"a": 1, "b": 2, "a": 3}')
        self.assertEqual(value, {"a": 3, "b": 2})  # as json.loads: the last one wins
        self.assertEqual(found, [data_tools.DuplicateKey((), "a")])
        self.assertEqual(
            found[0].describe(), f"duplicate key 'a' at the top level {DUPLICATE_TAIL}"
        )

    def test_repeat_in_a_nested_object(self):
        text = '{"locations": {"hotel": {"name": "x"}, "manor": {}, "hotel": {}}}'
        _value, found = data_tools.parse_json(text)
        self.assertEqual(found, [data_tools.DuplicateKey(("locations",), "hotel")])
        self.assertEqual(
            found[0].describe(), f"duplicate key 'hotel' in 'locations' {DUPLICATE_TAIL}"
        )

    def test_repeat_in_an_object_inside_an_array(self):
        text = (
            '{"sections": [{"id": "a"}, {"id": "b", "widgets": '
            '[{"type": "text"}, {"type": "date", "event": "x", "type": "events"}]}]}'
        )
        _value, found = data_tools.parse_json(text)
        self.assertEqual(found, [data_tools.DuplicateKey(("sections", 1, "widgets", 1), "type")])
        self.assertEqual(
            found[0].describe(),
            f"duplicate key 'type' in 'sections[1].widgets[1]' {DUPLICATE_TAIL}",
        )
        # an array at the top level
        self.assertEqual(
            duplicates_of('[{"token": "a"}, {"token": "b", "note": "", "token": "c"}]'),
            [("[1]", "token")],
        )

    def test_every_repeat_is_listed_once(self):
        text = (
            '{"a": 1, "a": 2, "a": 3, "b": {"c": 1, "d": 2, "c": 3, "d": 4},'
            ' "e": [{"f": 1, "f": 2}], "g": {"h": {"i": 1, "i": 2}}}'
        )
        self.assertEqual(
            duplicates_of(text),
            [("top level", "a"), ("b", "c"), ("b", "d"), ("e[0]", "f"), ("g.h", "i")],
        )

    def test_repeats_inside_a_replaced_value_are_found_too(self):
        text = '{"a": {"b": 1, "b": 2}, "a": {"c": [{"d": 1, "d": 2}]}}'
        self.assertEqual(
            duplicates_of(text), [("top level", "a"), ("a", "b"), ("a.c[0]", "d")]
        )

    def test_equal_keys_in_different_objects_are_not_repeats(self):
        text = (
            '{"id": 0, "a": {"id": 1, "a": 2}, "b": {"id": 3}, '
            '"c": [{"id": 4}, {"id": 5, "c": [{"id": 6}]}]}'
        )
        self.assertEqual(duplicates_of(text), [])

    def test_deep_nesting(self):
        depth = 300
        text = '{"k": ' * depth + '{"x": 1, "x": 2}' + "}" * depth
        _value, found = data_tools.parse_json(text)
        self.assertEqual(found, [data_tools.DuplicateKey(("k",) * depth, "x")])

    def test_syntax_errors_are_raised_by_the_parser(self):
        with self.assertRaises(json.JSONDecodeError):
            data_tools.parse_json('{"a": 1, "a": ')

        def reject(name):
            raise ValueError(f"{name} is not allowed")

        with self.assertRaisesRegex(ValueError, "NaN is not allowed"):
            data_tools.parse_json('{"a": NaN}', parse_constant=reject)

    def test_a_key_that_may_hold_a_name_is_not_shown(self):
        text = json.dumps(
            {"guests": {SECRET: 1, "x": 2}}, ensure_ascii=False
        ).replace('"x"', json.dumps(SECRET, ensure_ascii=False))
        _value, found = data_tools.parse_json(text)
        self.assertEqual(len(found), 1)
        message = found[0].describe()
        self.assertEqual(
            message, f"duplicate key <non-identifier key> in 'guests' {DUPLICATE_TAIL}"
        )
        # the path hides such keys as well
        _value, found = data_tools.parse_json(
            json.dumps({SECRET: {"a": 1}}, ensure_ascii=False).replace('"a": 1', '"a": 1, "a": 2')
        )
        self.assertEqual(
            found[0].describe(), f"duplicate key 'a' in '<non-identifier key>' {DUPLICATE_TAIL}"
        )
        self.assertNotIn(SECRET, found[0].describe())

    def test_strict_mode_shows_only_the_known_keys(self):
        # a key of an invitation may be a name even when it looks like an id
        text = (
            '[{"token": "t", "events": {"IvanPetrov": {}, "IvanPetrov": {}},'
            ' "sections": {"ivan": {"note": "a", "note": "b"}}}]'
        )
        _value, found = data_tools.parse_json(text)
        known = {"events", "sections", "note"}
        self.assertEqual(
            [item.describe(known) for item in found],
            [
                f"duplicate key <unknown key> in '[0].events' {DUPLICATE_TAIL}",
                f"duplicate key 'note' in '[0].sections.<unknown key>' {DUPLICATE_TAIL}",
            ],
        )
        self.assertIn("'IvanPetrov'", found[0].describe())  # the lenient mode shows it
        self.assertEqual(
            found[1].describe(known | {"ivan"}),
            f"duplicate key 'note' in '[0].sections.ivan' {DUPLICATE_TAIL}",
        )


class KeyAndPathTests(unittest.TestCase):
    def test_keys_that_look_like_names_of_fields_are_shown(self):
        for key in ("hotel", "googlePlaceId", "hotel-booked", "_private", "a1", "x" * 64):
            with self.subTest(key=key):
                self.assertTrue(data_tools.is_plain_key(key))
                self.assertEqual(data_tools.show_key(key), f"'{key}'")

    def test_any_other_key_is_hidden(self):
        for key in (
            SECRET,
            "Анна",
            "two words",
            "it's",
            'say "hi"',
            "line\nbreak",
            "tab\there",
            "bell\x07",
            "trailing\n",
            "x" * 65,
            "a" * 1000,
            "",
            "1st",
            "-dash",
            "a.b",
            "a[0]",
            "émile",
            None,
            1,
        ):
            with self.subTest(key=key):
                self.assertFalse(data_tools.is_plain_key(key))
                self.assertEqual(data_tools.show_key(key), "<non-identifier key>")

    def test_format_path(self):
        self.assertEqual(
            data_tools.format_path(("sections", 3, "widgets", 1, "location")),
            "sections[3].widgets[1].location",
        )
        self.assertEqual(
            data_tools.format_path(("events", "dinner", "start")), "events.dinner.start"
        )
        self.assertEqual(data_tools.format_path(()), "top level")
        self.assertEqual(data_tools.format_path((3, "token")), "[3].token")
        self.assertEqual(data_tools.format_path(["a", 0, 1]), "a[0][1]")

    def test_format_path_hides_keys_that_are_not_plain(self):
        path = data_tools.format_path(("locations", SECRET, "name"))
        self.assertEqual(path, "locations.<non-identifier key>.name")
        self.assertEqual(
            data_tools.format_path(("a", "x\ny", True)),
            "a.<non-identifier key>.<non-identifier key>",
        )


class SuggestionTests(unittest.TestCase):
    def test_the_closest_known_name_is_suggested(self):
        known = ("visible", "width", "widgets")
        self.assertEqual(data_tools.did_you_mean("visibel", known), " (did you mean 'visible'?)")
        self.assertEqual(data_tools.did_you_mean("widget", known), " (did you mean 'widgets'?)")
        self.assertEqual(data_tools.closest_name("widht", known), "width")

    def test_no_suggestion(self):
        self.assertEqual(data_tools.did_you_mean("colour", ("visible", "width")), "")
        self.assertEqual(data_tools.did_you_mean("visible", ()), "")
        self.assertEqual(data_tools.did_you_mean(None, ("visible",)), "")
        self.assertEqual(data_tools.did_you_mean(7, ("visible",)), "")

    def test_the_unknown_name_is_never_shown(self):
        hint = data_tools.did_you_mean("visible" + SECRET[:2], ("visible",))
        self.assertEqual(hint, " (did you mean 'visible'?)")
        self.assertEqual(data_tools.did_you_mean(SECRET, ("visible",)), "")

    def test_only_known_names_that_may_be_shown_are_suggested(self):
        self.assertEqual(data_tools.did_you_mean(SECRET + "!", (SECRET,)), "")


class IdentifierTests(unittest.TestCase):
    def test_valid_identifiers(self):
        for value in ("a", "hotel", "hotel-booked", "a1-b2-c3", "x1", "a" * 32):
            with self.subTest(value=value):
                self.assertTrue(data_tools.is_identifier(value))
                self.assertEqual(data_tools.show_id(value), f"'{value}'")
                self.assertIsNone(data_tools.identifier_problem(value, ("sections", 0, "id")))

    def test_invalid_identifiers(self):
        for value in (
            "Hotel",
            "hoTel",
            "1hotel",
            "hotel--booked",
            "hotel-",
            "-hotel",
            "hotel_booked",
            "hotel booked",
            "hotel\n",
            "отель",
            "a" * 33,
            "",
            5,
            None,
            True,
            ["hotel"],
        ):
            with self.subTest(value=value):
                self.assertFalse(data_tools.is_identifier(value))
                self.assertEqual(data_tools.show_id(value), "(not a valid identifier)")

    def test_message_for_a_field_with_an_invalid_id(self):
        message = data_tools.identifier_problem(SECRET, ("sections", 2, "id"))
        self.assertEqual(
            message,
            "field 'sections[2].id' must be an identifier: 1-32 characters, "
            "lowercase a-z, 0-9 and single hyphens, starting with a letter",
        )
        self.assertNotIn(SECRET, data_tools.show_id(SECRET))


class TextShapeTests(unittest.TestCase):
    path = ("sections", 1, "widgets", 0, "text")
    field = "sections[1].widgets[0].text"

    def problems(self, value):
        return data_tools.text_value_problems(value, self.path)

    def test_a_string_or_both_forms(self):
        self.assertEqual(self.problems(SECRET), [])
        self.assertEqual(self.problems({"ty": SECRET, "vy": SECRET}), [])
        self.assertEqual(self.problems(""), [])

    def test_a_missing_form(self):
        self.assertEqual(
            self.problems({"ty": SECRET}),
            [f"field '{self.field}' must have both 'ty' and 'vy' (missing 'vy')"],
        )
        self.assertEqual(
            self.problems({}),
            [f"field '{self.field}' must have both 'ty' and 'vy' (missing 'ty' and 'vy')"],
        )

    def test_an_extra_key(self):
        self.assertEqual(
            self.problems({"ty": SECRET, "vy": SECRET, "vyy": SECRET}),
            [f"unknown field '{self.field}.vyy' (did you mean 'vy'?)"],
        )
        problems = self.problems({"ty": "a", "vy": "b", SECRET: "c"})
        self.assertEqual(problems, [f"unknown field '{self.field}.<non-identifier key>'"])

    def test_wrong_types(self):
        for value in (1, None, True, [SECRET]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.problems(value),
                    [
                        f"field '{self.field}' must be a string or an object "
                        "with the strings 'ty' and 'vy'"
                    ],
                )
        self.assertEqual(
            self.problems({"ty": 1, "vy": [SECRET]}),
            [
                f"field '{self.field}.ty' must be a string",
                f"field '{self.field}.vy' must be a string",
            ],
        )

    def test_values_are_never_shown(self):
        for value in ({"ty": SECRET}, {"ty": 5, "vy": SECRET, "extra": SECRET}, [SECRET]):
            for problem in self.problems(value):
                self.assertNotIn(SECRET, problem)


class FormTests(unittest.TestCase):
    def test_pick_form(self):
        self.assertEqual(data_tools.pick_form("Общий текст", "ty"), "Общий текст")
        self.assertEqual(data_tools.pick_form("Общий текст", "vy"), "Общий текст")
        value = {"ty": "Приходи", "vy": "Приходите"}
        self.assertEqual(data_tools.pick_form(value, "ty"), "Приходи")
        self.assertEqual(data_tools.pick_form(value, "vy"), "Приходите")

    def test_an_invalid_form(self):
        self.assertIsNone(data_tools.form_problem("ty"))
        self.assertIsNone(data_tools.form_problem("vy"))
        for form in ("tu", "TY", "", None, 1, SECRET):
            with self.subTest(form=form):
                self.assertEqual(data_tools.form_problem(form), "must be one of: ty, vy")
                with self.assertRaises(data_tools.TextError) as caught:
                    data_tools.pick_form({"ty": "a", "vy": "b"}, form)
                self.assertEqual(str(caught.exception), "must be one of: ty, vy")

    def test_a_value_that_is_not_a_text(self):
        with self.assertRaises(data_tools.TextError):
            data_tools.pick_form({"ty": "a"}, "vy")
        with self.assertRaises(data_tools.TextError):
            data_tools.pick_form(5, "ty")


class PlaceholderSyntaxTests(unittest.TestCase):
    def assertTextError(self, text, message):
        with self.assertRaises(data_tools.TextError) as caught:
            data_tools.parse_text(text)
        self.assertEqual(str(caught.exception), message)
        return str(caught.exception)

    def test_parts(self):
        Placeholder = data_tools.Placeholder
        self.assertEqual(
            data_tools.parse_text("Ждём {greeting}!"),
            ["Ждём ", Placeholder("greeting", 6), "!"],
        )
        self.assertEqual(
            data_tools.parse_text("{coupleNames}{greeting}"),
            [Placeholder("coupleNames", 1), Placeholder("greeting", 14)],
        )
        self.assertEqual(data_tools.parse_text(""), [])
        self.assertEqual(data_tools.parse_text("Просто текст"), ["Просто текст"])

    def test_literal_braces(self):
        self.assertEqual(data_tools.parse_text("{{a}} и {{"), ["{a} и {"])
        self.assertEqual(
            data_tools.parse_text("{{{greeting}}}"),
            ["{", data_tools.Placeholder("greeting", 3), "}"],
        )

    def test_unclosed_brace(self):
        self.assertTextError(
            "Ждём вас в 16:00 {и",
            "has an unclosed '{' at character 18 (write '{{' for a literal brace)",
        )

    def test_unmatched_closing_brace(self):
        self.assertTextError(
            "Смайлик :} тут",
            "has an unmatched '}' at character 10 (write '}}' for a literal brace)",
        )
        self.assertTextError(
            "{greeting}}", "has an unmatched '}' at character 11 (write '}}' for a literal brace)"
        )

    def test_empty_placeholder(self):
        self.assertTextError(
            "Текст {}", "has an empty placeholder at character 7 (write '{{}}' for literal braces)"
        )

    def test_malformed_placeholder(self):
        for text in ("{ greeting}", "{greeting }", "{имя}", "{1st}", "{a-b}", "{a\nb}", "{a{b}"):
            with self.subTest(text=text):
                self.assertTextError(
                    text,
                    "has a malformed placeholder at character 1 (a name has only latin "
                    "letters and digits and starts with a letter; write '{{' for a literal brace)",
                )

    def test_the_contents_of_the_braces_are_never_shown(self):
        for text in (f"{{{SECRET}}}", f"{{{SECRET}", f"x}} {SECRET}", f"{{ {SECRET} }}"):
            with self.subTest(text=text):
                with self.assertRaises(data_tools.TextError) as caught:
                    data_tools.parse_text(text)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_positions_count_characters_not_bytes(self):
        self.assertEqual(
            data_tools.parse_text("Привет, {greeting}")[1], data_tools.Placeholder("greeting", 9)
        )
        self.assertTextError(
            "Ёлка\n{", "has an unclosed '{' at character 6 (write '{{' for a literal brace)"
        )


class PlaceholderCheckTests(unittest.TestCase):
    def test_an_unknown_placeholder_is_reported_by_position(self):
        problems = data_tools.placeholder_problems("Привет, {coupleName}!", KNOWN)
        self.assertEqual(
            problems,
            [
                "has an unknown placeholder at character 9 (did you mean {coupleNames}?); "
                + AVAILABLE
            ],
        )

    def test_the_unknown_name_is_never_shown(self):
        problems = data_tools.placeholder_problems("A {Zqxsecretname} and {greetingz}", KNOWN)
        self.assertEqual(
            problems,
            [
                "has an unknown placeholder at character 3; " + AVAILABLE,
                "has an unknown placeholder at character 23 (did you mean {greeting}?); "
                + AVAILABLE,
            ],
        )
        self.assertNotIn("Zqxsecretname", "\n".join(problems))
        self.assertNotIn("greetingz", "\n".join(problems))

    def test_no_placeholders_known(self):
        self.assertEqual(
            data_tools.placeholder_problems("{greeting}", ()),
            [
                "has an unknown placeholder at character 1; no placeholders are available "
                "here (write '{{' for a literal brace)"
            ],
        )

    def test_a_known_name_without_a_value(self):
        text = "До {rsvpDeadline}, то есть {rsvpDeadline}, {greeting}"
        self.assertEqual(
            data_tools.placeholder_problems(text, KNOWN, available={"greeting"}),
            ["uses {rsvpDeadline}, but 'rsvpDeadline' is not set"],
        )
        self.assertEqual(data_tools.placeholder_problems(text, KNOWN), [])

    def test_a_syntax_problem_ends_the_check(self):
        self.assertEqual(
            data_tools.placeholder_problems("{nope} {", KNOWN),
            ["has an unclosed '{' at character 8 (write '{{' for a literal brace)"],
        )

    def test_check_text_of_a_field_with_two_forms(self):
        path = ("sections", 1, "widgets", 0, "text")
        value = {"ty": "Привет, {coupleName}!", "vy": "До {rsvpDeadline} {"}
        self.assertEqual(
            data_tools.check_text(value, path, KNOWN, available={"coupleNames"}),
            [
                "field 'sections[1].widgets[0].text.ty' has an unknown placeholder at "
                "character 9 (did you mean {coupleNames}?); " + AVAILABLE,
                "field 'sections[1].widgets[0].text.vy' has an unclosed '{' at character 19 "
                "(write '{{' for a literal brace)",
            ],
        )

    def test_check_text_reports_the_shape_and_the_placeholders(self):
        path = ("sections", 0, "eyebrow")
        self.assertEqual(
            data_tools.check_text({"ty": "{greeting}", "vy": 1}, path, KNOWN, available=()),
            [
                "field 'sections[0].eyebrow.vy' must be a string",
                "field 'sections[0].eyebrow.ty' uses {greeting}, but 'greeting' is not set",
            ],
        )
        self.assertEqual(data_tools.check_text("{greeting}", ("title",), KNOWN), [])
        self.assertEqual(
            data_tools.check_text(5, ("title",), KNOWN),
            ["field 'title' must be a string or an object with the strings 'ty' and 'vy'"],
        )

    def test_texts_are_never_shown(self):
        value = {"ty": f"{SECRET} {{{SECRET}}}", "vy": f"{SECRET} {{greeting", SECRET: SECRET}
        problems = data_tools.check_text(value, ("text",), KNOWN)
        self.assertEqual(len(problems), 3)
        self.assertNotIn(SECRET, "\n".join(problems))


class SubstitutionTests(unittest.TestCase):
    values = {"coupleNames": "Алиса и Боб", "greeting": "Дорогая Ева!"}

    def test_values_are_inserted(self):
        self.assertEqual(
            data_tools.substitute("{greeting} Ждём тебя. {coupleNames}", self.values, KNOWN),
            "Дорогая Ева! Ждём тебя. Алиса и Боб",
        )
        self.assertEqual(
            data_tools.substitute("{{greeting}} {greeting}", self.values, KNOWN),
            "{greeting} Дорогая Ева!",
        )

    def test_values_are_not_parsed_again(self):
        values = {"greeting": "{coupleNames} {{ } {", "coupleNames": "X"}
        self.assertEqual(
            data_tools.substitute("[{greeting}]", values, KNOWN), "[{coupleNames} {{ } {]"
        )

    def test_line_breaks_are_kept(self):
        values = {"greeting": "две\nстроки"}
        self.assertEqual(
            data_tools.substitute("Абзац\n\n{greeting}\r\nконец", values, KNOWN),
            "Абзац\n\nдве\nстроки\r\nконец",
        )

    def test_an_empty_string_is_a_value(self):
        self.assertEqual(data_tools.substitute("[{greeting}]", {"greeting": ""}, KNOWN), "[]")

    def test_a_placeholder_without_a_value(self):
        with self.assertRaises(data_tools.TextError) as caught:
            data_tools.substitute("До {rsvpDeadline}", self.values, KNOWN)
        self.assertEqual(
            str(caught.exception), "uses {rsvpDeadline}, but 'rsvpDeadline' is not set"
        )

    def test_unknown_placeholders_and_syntax_errors(self):
        with self.assertRaises(data_tools.TextError) as caught:
            data_tools.substitute("{Zqxsecretname}", self.values, KNOWN)
        self.assertNotIn("Zqxsecretname", str(caught.exception))
        self.assertIn("unknown placeholder at character 1", str(caught.exception))
        # a value for a name that is not known does not make it known
        with self.assertRaises(data_tools.TextError):
            data_tools.substitute("{other}", {"other": "x"}, KNOWN)
        with self.assertRaises(data_tools.TextError):
            data_tools.substitute("{greeting", self.values, KNOWN)

    def test_resolve_text(self):
        value = {"ty": "Приходи, {greeting}", "vy": "Приходите, {greeting}"}
        self.assertEqual(
            data_tools.resolve_text(value, "vy", self.values, KNOWN),
            "Приходите, Дорогая Ева!",
        )
        self.assertEqual(
            data_tools.resolve_text("{coupleNames}", "ty", self.values, KNOWN), "Алиса и Боб"
        )
        with self.assertRaises(data_tools.TextError):
            data_tools.resolve_text(value, "you", self.values, KNOWN)


def with_repeated_venue_name(site: dict) -> str:
    """`site.json` text whose `venue` object has the key `name` twice."""
    text = json.dumps(site, ensure_ascii=False, indent=2)
    marker = '"venue": {'
    assert marker in text
    return text.replace(marker, marker + f'\n    "name": "{SECRET}",', 1)


def with_repeated_greeting(invitations: list) -> str:
    """`invitations.json` text whose second invitation has `greeting` twice."""
    text = json.dumps(invitations, ensure_ascii=False)
    token = json.dumps(invitations[1]["token"])
    return text.replace(f'"token": {token}', f'"greeting": "{SECRET}", "token": {token}', 1)


class ReadJsonTests(TempDirTestCase):
    def read(self, text: str):
        path = self.tmp / "site.json"
        path.write_text(text, encoding="utf-8")
        report = build.Report()
        return build.read_json(path, report), report.errors, build.display_path(path)

    def test_a_document_without_repeats_is_read_as_before(self):
        text = json.dumps(site_data(), ensure_ascii=False, indent=2)
        value, errors, _shown = self.read(text)
        self.assertEqual(errors, [])
        self.assertEqual(value, json.loads(text))

    def test_repeated_keys_are_errors(self):
        value, errors, shown = self.read(
            '{"locations": {"hotel": {}, "hotel": {}}, "a": [{"b": 1, "b": 2}], "c": 1, "c": 2}'
        )
        self.assertIs(value, build.MISSING)
        self.assertEqual(
            errors,
            [
                f"{shown}: duplicate key 'c' at the top level {DUPLICATE_TAIL}",
                f"{shown}: duplicate key 'hotel' in 'locations' {DUPLICATE_TAIL}",
                f"{shown}: duplicate key 'b' in 'a[0]' {DUPLICATE_TAIL}",
            ],
        )
        self.assertTrue(shown.endswith("site.json"))

    def test_other_problems_are_reported_as_before(self):
        value, errors, shown = self.read('{"a": 1, "a": ')
        self.assertIs(value, build.MISSING)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith(f"{shown}: invalid JSON: "), errors)
        value, errors, shown = self.read('{"a": NaN, "a": 1}')
        self.assertEqual(errors, [f"{shown}: invalid JSON: NaN is not allowed in JSON data"])

    def test_load_data_stops_at_repeated_keys(self):
        data_dir = support.write_data(self.tmp / "data")
        (data_dir / "site.json").write_text(
            with_repeated_venue_name(site_data()), encoding="utf-8"
        )
        (data_dir / "invitations.json").write_text(
            with_repeated_greeting(invitations_data()), encoding="utf-8"
        )
        with self.assertRaises(build.ValidationError) as caught:
            build.load_data(data_dir)
        errors = caught.exception.errors
        # the files are not checked any further: no other messages
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(
            errors[0].endswith(f"site.json: duplicate key 'name' in 'venue' {DUPLICATE_TAIL}")
        )
        self.assertTrue(
            errors[1].endswith(
                f"invitations.json: duplicate key 'greeting' in '[1]' {DUPLICATE_TAIL}"
            )
        )
        joined = "\n".join(errors)
        self.assertNotIn(SECRET, joined)
        for secret in support.PRIVATE_STRINGS:
            self.assertNotIn(secret, joined)


class RepeatedKeyCommandTests(CliTestCase):
    def write_repeats(self):
        (self.data / "site.json").write_text(
            with_repeated_venue_name(site_data()), encoding="utf-8"
        )
        (self.data / "invitations.json").write_text(
            with_repeated_greeting(invitations_data()), encoding="utf-8"
        )

    def test_validate_fails(self):
        self.write_repeats()
        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"site.json: duplicate key 'name' in 'venue' {DUPLICATE_TAIL}", result.stderr
        )
        self.assertIn(
            f"invitations.json: duplicate key 'greeting' in '[1]' {DUPLICATE_TAIL}", result.stderr
        )
        self.assertIn("failed with 2 error(s)", result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_build_fails_and_writes_nothing(self):
        self.write_repeats()
        result = self.run_build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate key 'name' in 'venue'", result.stderr)
        self.assertFalse(self.out.exists())
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_equal_keys_in_different_objects_pass(self):
        # every invitation has the same keys as the others: not a repeat
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
