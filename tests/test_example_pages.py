"""The finished pages of the example data sets: privacy, ids and reveal marks.

Both example sets are built with the real template, fragments and assets
into a temporary directory (the media files are placeholders made from the
media registry).  The checks look at the published files only: what a guest
does not see must be absent from the HTML of the page and from the calendar
files next to it.
"""

from __future__ import annotations

import collections
import json
import re
import shutil
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tests import support
from tests.support import build

ROOT = support.ROOT
DATA_SETS = ("data", "data-venue-pending")

#: Script hooks whose elements never carry the reveal mark.
NO_REVEAL_HOOKS = ("data-gallery", "data-lightbox", "data-countdown")


def load(name: str) -> tuple[dict, list]:
    directory = ROOT / "examples" / name
    return tuple(
        json.loads((directory / file).read_text(encoding="utf-8"))
        for file in ("site.json", "invitations.json")
    )


def visible_events(site: dict, invitation: dict) -> set[str]:
    overrides = invitation.get("events", {})
    return {
        event_id
        for event_id, event in site["events"].items()
        if overrides.get(event_id, {}).get("visible", event.get("visible", True))
    }


def hidden_sections(site: dict, invitation: dict) -> set[str]:
    overrides = invitation.get("sections", {})
    return {
        section["id"]
        for section in site["sections"]
        if not overrides.get(section["id"], {}).get("visible", section.get("visible", True))
    }


def notes(invitation: dict) -> list[str]:
    return [
        override["note"]
        for part in ("sections", "events")
        for override in invitation.get(part, {}).values()
        if override.get("note")
    ]


class Page(HTMLParser):
    """Ids, references and reveal marks of a finished page."""

    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                      "meta", "source", "track", "wbr"})  # fmt: skip

    def __init__(self, document: str):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.references: list[tuple[str, str]] = []
        self.reveals: list[tuple[str, dict]] = []
        self.nested_reveals = 0
        self.open: list[tuple[str, bool]] = []
        self.feed(document)
        self.close()

    def element(self, tag: str, attrs, void: bool) -> None:
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"] or "")
        for name in ("aria-labelledby", "aria-controls", "aria-describedby"):
            for target in (attributes.get(name) or "").split():
                self.references.append((name, target))
        if (attributes.get("href") or "").startswith("#"):
            self.references.append(("href", attributes["href"][1:]))
        marked = "data-reveal" in attributes
        if marked:
            self.reveals.append((tag, attributes))
            if any(open_marked for _tag, open_marked in self.open):
                self.nested_reveals += 1
        if not void:
            self.open.append((tag, marked))

    def handle_starttag(self, tag, attrs):
        self.element(tag, attrs, tag in self.VOID)

    def handle_startendtag(self, tag, attrs):
        self.element(tag, attrs, True)

    def handle_endtag(self, tag):
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                del self.open[index:]
                break


class ExamplePagesTestCase(unittest.TestCase):
    #: data set -> (site, invitations, {relative path: text})
    built: dict[str, tuple[dict, list, dict[str, str]]]

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="invite-example-pages-"))
        cls.built = {}
        for name in DATA_SETS:
            site, invitations = load(name)
            names = sorted(
                {item[key] for item in site.get("media", {}).values()
                 for key in ("file", "poster", "thumb") if key in item}
            )  # fmt: skip
            media = support.write_media(cls.tmp / f"media-{name}", names)
            out = cls.tmp / f"dist-{name}"
            build.build_site(ROOT / "examples" / name, media, out, code_dir=ROOT, log=lambda _l: None)
            files = {
                path.relative_to(out).as_posix(): path.read_bytes().decode("utf-8", "replace")
                for path in sorted(out.rglob("*"))
                if path.is_file()
            }
            cls.built[name] = (site, invitations, files)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sets(self):
        for name, (site, invitations, files) in self.built.items():
            yield name, site, invitations, files

    @staticmethod
    def own(invitation: dict, files: dict[str, str]) -> dict[str, str]:
        """The page and the calendar files of one invitation."""
        prefix = f"i/{invitation['token']}/"
        return {path: text for path, text in files.items() if path.startswith(prefix)}


class PrivacyTests(ExamplePagesTestCase):
    def test_every_note_and_greeting_is_on_its_own_page_only(self):
        checked = 0
        for name, _site, invitations, files in self.sets():
            for invitation in invitations:
                page = f"i/{invitation['token']}/index.html"
                for value in [invitation["greeting"], *notes(invitation)]:
                    # the whole text as the page writes it: escaped, with paragraphs
                    text = build.text_to_html(value)
                    with self.subTest(data=name, value=value[:20]):
                        found = [path for path, content in files.items() if text in content]
                        self.assertEqual(found, [page])
                        for line in filter(None, (part.strip() for part in value.split("\n"))):
                            html_line = build.text_to_html(line)
                            found = [path for path, content in files.items() if html_line in content]
                            self.assertEqual(found, [page], line)
                        checked += 1
        # 8 + 3 greetings; notes: 9 in the main set, 2 in the other
        self.assertEqual(checked, 11 + 11)

    def test_hidden_events_leave_no_trace(self):
        checked = 0
        for name, site, invitations, files in self.sets():
            for invitation in invitations:
                seen = visible_events(site, invitation)
                own = self.own(invitation, files)
                calendars = sorted(path.rsplit("/", 1)[1] for path in own if path.endswith(".ics"))
                self.assertEqual(calendars, sorted(f"{event_id}.ics" for event_id in seen))
                for event_id, event in site["events"].items():
                    if event_id in seen:
                        continue
                    for path, content in own.items():
                        with self.subTest(data=name, token=invitation["token"][:4], event=event_id):
                            self.assertNotIn(event["title"], content, path)
                            self.assertNotIn(event["start"], content, path)
                            self.assertNotIn(f"e-{event_id}", content, path)
                            self.assertNotIn(f"/{event_id}.ics", content, path)
                            checked += 1
        self.assertGreater(checked, 0)

    def test_hidden_events_are_where_they_are_shown(self):
        site, invitations, files = self.built["data"]
        title = site["events"]["brunch"]["title"]
        guests = [invitation for invitation in invitations if "brunch" in visible_events(site, invitation)]
        self.assertEqual(len(guests), 2)
        self.assertEqual(
            sorted(path for path, content in files.items() if title in content),
            sorted(
                f"i/{invitation['token']}/{file}"
                for invitation in guests
                for file in ("brunch.ics", "index.html")
            ),
        )

    def test_hidden_sections_and_widgets_are_not_on_the_page(self):
        checked = 0
        for name, site, invitations, files in self.sets():
            for invitation in invitations:
                page = files[f"i/{invitation['token']}/index.html"]
                for section_id in hidden_sections(site, invitation):
                    with self.subTest(data=name, token=invitation["token"][:4], section=section_id):
                        self.assertNotIn(f'id="s-{section_id}"', page)
                        self.assertNotIn(f"s-{section_id}--", page)
                        checked += 1
        self.assertGreater(checked, 0)
        # the widget "hotel-booked" is switched on for one guest only
        site, invitations, files = self.built["data"]
        text = site["sections"][6]["widgets"][2]["text"]
        pages = [
            path for path, content in files.items()
            if text["ty"] in content or text["vy"] in content
        ]  # fmt: skip
        self.assertEqual(pages, ["i/IBAhKaLEOj1DHuOUdUMzQA/index.html"])

    def test_calendar_files_carry_nothing_of_the_invitation(self):
        for name, _site, invitations, files in self.sets():
            for invitation in invitations:
                for path, content in self.own(invitation, files).items():
                    if not path.endswith(".ics"):
                        continue
                    with self.subTest(data=name, file=path):
                        for value in [invitation["greeting"], invitation["token"], *notes(invitation)]:
                            for line in filter(None, (part.strip() for part in value.split("\n"))):
                                self.assertNotIn(line, content)

    def test_a_token_is_only_in_its_own_directory_and_page(self):
        for name, _site, invitations, files in self.sets():
            for invitation in invitations:
                token = invitation["token"]
                page = f"i/{token}/index.html"
                with self.subTest(data=name, token=token[:4]):
                    for path, content in files.items():
                        if path == page:
                            # the page links to its own calendar files only
                            links = re.findall(rf"/i/{re.escape(token)}/([^\"]*)\"", content)
                            self.assertTrue(links)
                            self.assertTrue(all(link.endswith(".ics") for link in links))
                            self.assertNotIn(token, re.sub(rf"/i/{re.escape(token)}/[\w-]+\.ics", "", content))
                        else:
                            self.assertNotIn(token, content, path)
                            self.assertNotIn(token.lower(), content.lower(), path)
                    self.assertTrue(all(path.startswith(f"i/{token}/") for path in files if token in path))

    def test_media_names_of_the_data_are_not_published(self):
        site, _invitations, files = self.built["data"]
        for item in site["media"].values():
            for key in ("file", "poster", "thumb"):
                if key in item:
                    self.assertFalse(any(item[key] in path for path in files), item[key])
                    self.assertFalse(any(item[key] in content for content in files.values()), item[key])

    def test_the_name_of_a_tile_picture_is_not_published_either(self):
        site, invitations = load("data")
        site["media"]["walk"]["thumb"] = "walk-thumb.png"
        data = support.write_data(self.tmp / "data-thumb", site=site, invitations=invitations)
        names = sorted(
            {item[key] for item in site["media"].values()
             for key in ("file", "poster", "thumb") if key in item}
        )  # fmt: skip
        media = support.write_media(self.tmp / "media-thumb", names)
        (media / "walk-thumb.png").write_bytes(support.png_bytes(16, 9))
        out = self.tmp / "dist-thumb"
        build.build_site(data, media, out, code_dir=ROOT, log=lambda _l: None)
        thumb = build.media_tools.hashed_name(media / "walk-thumb.png")
        contents = [path.read_bytes() for path in out.rglob("*") if path.is_file()]
        for name in names:
            self.assertFalse(any(name in str(path) for path in out.rglob("*")), name)
            self.assertFalse(any(name.encode() in content for content in contents), name)
        page = (out / "i" / invitations[0]["token"] / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'<img src="/assets/{site["mediaDir"]}/{thumb}" alt=""', page)


class MarkupTests(ExamplePagesTestCase):
    def pages(self):
        for name, _site, invitations, files in self.sets():
            for invitation in invitations:
                yield name, invitation["token"][:4], files[f"i/{invitation['token']}/index.html"]

    def test_ids_are_unique_and_every_reference_resolves(self):
        for name, token, document in self.pages():
            page = Page(document)
            with self.subTest(data=name, token=token):
                repeated = [item for item, count in collections.Counter(page.ids).items() if count > 1]
                self.assertEqual(repeated, [])
                self.assertTrue(all(page.ids))
                dangling = [(attribute, target) for attribute, target in page.references
                            if target not in page.ids]  # fmt: skip
                self.assertEqual(dangling, [])
                self.assertTrue(page.references)

    def test_reveal_marks_only_on_the_containers_of_sections(self):
        for name, token, document in self.pages():
            page = Page(document)
            with self.subTest(data=name, token=token):
                self.assertTrue(page.reveals)
                self.assertEqual(page.nested_reveals, 0)
                for tag, attributes in page.reveals:
                    self.assertNotEqual(tag, "li")
                    self.assertEqual(tag, "div")
                    self.assertIn("container", (attributes.get("class") or "").split())
                    for attribute in attributes:
                        self.assertNotIn(attribute, NO_REVEAL_HOOKS)
                        self.assertFalse(attribute.startswith("data-events"), attribute)
                self.assertEqual(
                    len(page.reveals), document.count('<section class="section section--')
                )

    def test_the_validator_types_are_the_fragment_files(self):
        fragments = build.load_fragments(ROOT / build.FRAGMENTS_DIRNAME)
        self.assertEqual(fragments.names("sections"), sorted(build.schema.SECTION_TYPES))
        self.assertEqual(fragments.names("widgets"), sorted(build.schema.WIDGET_TYPES))
        self.assertEqual(fragments.names("media"), sorted(build.schema.MEDIA_TYPES))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
