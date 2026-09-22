"""The title and the link preview tags of the pages: the same for every
guest, the description from the named texts in their common form, the name
of the site from `--site-name` / `SITE_NAME`, no address of the site."""

from __future__ import annotations

from html.parser import HTMLParser

from tests import fixtures_v2 as F
from tests import support
from tests.support import CliTestCase, build
from tools import _page, _schema

REAL_TEMPLATE = (support.ROOT / build.TEMPLATE_FILE).read_text(encoding="utf-8")
TITLE = f"Приглашение · {support.COUPLE_NAMES}"
DESCRIPTION = f"Мы, {support.COUPLE_NAMES}, женимся! Ждём вас 1 июня 2030 в 16:00."
SITE_NAME = "Приглашения"


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.in_title = False
        self.meta: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and ("property" in values or "name" in values):
            self.meta.append((values.get("property") or values["name"], values.get("content", "")))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def head(document: str) -> _Head:
    parser = _Head()
    parser.feed(document)
    return parser


def site_with_preview(**changes) -> dict:
    site = support.site_data(
        texts={
            "announce": "Мы, {coupleNames}, женимся!",
            "invite": {
                "ty": "Ждём тебя {eventDate} в {eventTime}.",
                "vy": "Ждём вас {eventDate} в {eventTime}.",
                "all": "Ждём вас {eventDate} в {eventTime}.",
            },
        },
        linkPreview={"description": "{text:announce}\n\n{text:invite}"},
    )
    site["sections"][1]["widgets"][0]["text"] = "{text:invite}"
    site.update(changes)
    return site


class LinkPreviewBuildTests(CliTestCase):
    template = REAL_TEMPLATE

    def setUp(self) -> None:
        super().setUp()
        # the real template loads the script of the pages
        (self.code / "assets" / "app.js").write_text("", encoding="utf-8")
        support.write_data(self.data, site=site_with_preview())

    def heads(self) -> list[_Head]:
        return [
            head((self.out / "i" / token / "index.html").read_text(encoding="utf-8"))
            for token in (support.TOKEN_A, support.TOKEN_B, support.TOKEN_C)
        ]

    def build(self, *extra, env=None):
        result = self.build_in_process(*extra, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_the_tags_are_the_same_for_every_guest(self):
        result = self.build()
        heads = self.heads()
        for page in heads:
            meta = dict(page.meta)
            self.assertEqual(page.title, TITLE)
            self.assertEqual(meta["og:title"], TITLE)
            self.assertEqual(meta["og:image:alt"], TITLE)
            self.assertEqual(meta["description"], DESCRIPTION)
            self.assertEqual(meta["og:description"], DESCRIPTION)
            self.assertEqual(meta["og:type"], "website")
            self.assertEqual(meta["og:locale"], "ru_RU")
            self.assertEqual(meta["twitter:card"], "summary_large_image")
            self.assertTrue(meta["og:image"].startswith("/assets/"))
            self.assertNotIn("og:site_name", meta)
            self.assertNotIn("og:url", meta)
            for _key, value in page.meta:
                self.assertNotIn("://", value)
        self.assertEqual(heads[0].meta, heads[1].meta)
        self.assertEqual(heads[0].meta, heads[2].meta)
        self.assertIn("build: link preview site name: not set", result.stdout)
        self.assertNoPrivateData(result.stdout, result.stderr)

    def test_no_data_of_a_guest_in_the_head(self):
        self.build()
        for page in self.heads():
            text = page.title + repr(page.meta)
            for secret in (support.GREETING_TY, support.GREETING_VY, support.GREETING_THIRD,
                           support.NOTE, support.TRAVEL_NOTE, support.EVENT_NOTE,
                           support.HIDDEN_EVENT_TITLE, support.TOKEN_A):  # fmt: skip
                self.assertNotIn(secret, text)

    def test_the_stub_has_no_preview_tags(self):
        self.build("--site-name", SITE_NAME)
        stub = (self.out / "index.html").read_text(encoding="utf-8")
        for text in ("og:", "description", SITE_NAME, support.COUPLE_NAMES, "twitter:"):
            self.assertNotIn(text, stub)

    def test_the_site_name(self):
        result = self.build("--site-name", f"  {SITE_NAME} ")
        for page in self.heads():
            self.assertEqual(dict(page.meta)["og:site_name"], SITE_NAME)
        self.assertIn("build: link preview site name: set", result.stdout)
        self.assertNotIn(SITE_NAME, result.stdout + result.stderr)
        self.build(env={"SITE_NAME": SITE_NAME})
        self.assertEqual(dict(self.heads()[0].meta)["og:site_name"], SITE_NAME)
        # the option wins, an empty variable is no name
        self.build("--site-name", "Другое", env={"SITE_NAME": SITE_NAME})
        self.assertEqual(dict(self.heads()[0].meta)["og:site_name"], "Другое")
        self.build(env={"SITE_NAME": "  "})
        self.assertNotIn("og:site_name", dict(self.heads()[0].meta))

    def test_a_bad_site_name(self):
        for value, problem in (
            ("Секрет\nвторая", "one line without control characters"),
            ("Секрет" + "я" * 80, "at most 80 characters"),
            ("Секрет //host", "must not contain '//'"),
        ):
            with self.subTest(problem=problem):
                result = self.build_in_process("--site-name", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn(problem, result.stderr)
                self.assertNotIn("Секрет", result.stdout + result.stderr)
                result = self.build_in_process(env={"SITE_NAME": value})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"SITE_NAME: must", result.stderr)
                self.assertIn(problem, result.stderr)
                self.assertNotIn("Секрет", result.stdout + result.stderr)
                self.assertFalse(self.out.exists())

    def test_without_a_description(self):
        site = site_with_preview()
        site.pop("linkPreview")
        site["texts"].pop("announce")
        support.write_data(self.data, site=site)
        self.build()
        meta = dict(self.heads()[0].meta)
        self.assertNotIn("description", meta)
        self.assertNotIn("og:description", meta)
        self.assertEqual(meta["og:title"], TITLE)

    def test_an_absolute_og_image_fails_the_output_check(self):
        template = REAL_TEMPLATE.replace(
            'content="{{ogImage}}"', 'content="https://invite.example.invalid{{ogImage}}"'
        )
        (self.code / "template.html").write_text(template, encoding="utf-8")
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("<meta og:image>: external URL", result.stderr)
        self.assertFalse(self.out.exists())

    def test_an_address_in_the_description_fails_the_output_check(self):
        support.write_data(
            self.data, site=site_with_preview(linkPreview={"description": "Смотри https://x"})
        )
        result = self.build_in_process()
        self.assertEqual(result.returncode, 1)
        self.assertIn("<meta content>: external URL", result.stderr)

    def test_the_real_template_has_the_tags_once(self):
        for text in (
            "<title>Приглашение · {{coupleNames}}</title>",
            '<meta property="og:title" content="Приглашение · {{coupleNames}}">',
            '<meta property="og:image:alt" content="Приглашение · {{coupleNames}}">',
            '<meta name="twitter:card" content="summary_large_image">',
            '<meta property="og:locale" content="ru_RU">',
            '<meta property="og:site_name" content="{{siteName}}">',
        ):
            self.assertEqual(REAL_TEMPLATE.count(text), 1, text)
        self.assertNotIn('property="og:url"', REAL_TEMPLATE)


class LinkDescriptionTreeTests(support.unittest.TestCase):
    def build(self, site=None, invitations=None):
        site = F.site() if site is None else site
        invitations = F.invitations() if invitations is None else invitations
        report = F.Collector()
        self.assertTrue(_schema.check_data(site, invitations, report).ok, report.errors)
        pages, _usage = _page.build_pages(site, invitations, F.settings(), report)
        return pages, report

    def test_the_main_event_and_the_common_form(self):
        pages, report = self.build()
        expected = "Мы, Алиса и Боб, женимся! Ждём вас 15 июня 2030 в 16:00."
        # invitation #3 and #5 have the registration as their primary event
        self.assertEqual({page["linkDescription"] for page in pages}, {expected})
        self.assertEqual(pages[2]["primaryEvent"]["id"], "ceremony")
        self.assertEqual(report.messages, [])

    def test_white_space_becomes_single_spaces(self):
        site = F.site()
        site["linkPreview"]["description"] = " {text:announce}\n\n  {text:invite} "
        pages, _report = self.build(site)
        self.assertEqual(
            pages[0]["linkDescription"], "Мы, Алиса и Боб, женимся! Ждём вас 15 июня 2030 в 16:00."
        )

    def test_a_long_description_is_a_warning(self):
        site = F.site()
        site["texts"]["long"] = "Очень длинный текст. " * 10
        site["linkPreview"]["description"] = "{text:announce} {text:long}"
        pages, report = self.build(site)
        self.assertEqual(len(pages[0]["linkDescription"]), 235)
        self.assertEqual(
            report.warnings,
            [
                "site.json: field 'linkPreview.description' is 235 characters long once filled "
                "in (more than 200); link previews show one or two sentences - put the main "
                "thing into the first 80 characters"
            ],
        )

    def test_a_text_used_only_in_the_description_is_used(self):
        site = F.site()
        site["texts"]["tail"] = "Приходите!"
        site["linkPreview"]["description"] = "{text:announce} {text:tail}"
        site["sections"][1]["widgets"][2]["text"] = "{text:invite}"
        _pages, report = self.build(site)
        self.assertEqual(report.warnings, [])

    def test_without_the_field_there_is_no_description(self):
        site = F.site()
        site.pop("linkPreview")
        pages, _report = self.build(site)
        self.assertEqual(pages[0]["linkDescription"], "")
