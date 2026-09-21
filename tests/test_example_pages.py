"""The finished pages of the example data sets: privacy, ids and reveal marks.

Both example sets are built with the real template, fragments and assets
into a temporary directory (the media files are placeholders made from the
media registry).  The checks look at the published files only: what a guest
does not see must be absent from the HTML of the page, which links the
calendar files of the events it shows and no other.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
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
        """The files of the directory of one invitation (its page only)."""
        prefix = f"i/{invitation['token']}/"
        return {path: text for path, text in files.items() if path.startswith(prefix)}

    @staticmethod
    def calendars(site: dict, files: dict[str, str]) -> dict[str, str]:
        """Event id -> the published path of its calendar file (as the build names it)."""
        prefix = f"assets/{site['mediaDir']}/"
        by_uid = {
            re.search(r"^UID:(\S+)", content, re.M).group(1): path
            for path, content in files.items()
            if path.startswith(prefix) and path.endswith(".ics")
        }
        found = {}
        for event_id, event in site["events"].items():
            uid = hashlib.sha256(
                f"{site['mediaDir']}\n{event_id}\n{event['start']}".encode()
            ).hexdigest()[:32]
            if f"{uid}@invitation" in by_uid:
                found[event_id] = by_uid[f"{uid}@invitation"]
        return found


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
                self.assertEqual(list(own), [f"i/{invitation['token']}/index.html"])
                calendars = self.calendars(site, files)
                page = own[f"i/{invitation['token']}/index.html"]
                linked = {path.rsplit("/", 1)[1] for path in re.findall(r'href="/([^"]+\.ics)"', page)}
                self.assertEqual(linked, {calendars[event_id].rsplit("/", 1)[1] for event_id in seen})
                for event_id, event in site["events"].items():
                    if event_id in seen:
                        continue
                    for path, content in own.items():
                        with self.subTest(data=name, token=invitation["token"][:4], event=event_id):
                            self.assertNotIn(event["title"], content, path)
                            self.assertNotIn(event["start"], content, path)
                            self.assertNotIn(f"e-{event_id}", content, path)
                            self.assertNotIn(f"/{event_id}.ics", content, path)
                            # neither the link to its calendar file nor its name
                            self.assertNotIn(calendars[event_id].rsplit("/", 1)[1], content, path)
                            checked += 1
        self.assertGreater(checked, 0)

    def test_one_calendar_file_per_event_somebody_sees(self):
        for name, site, invitations, files in self.sets():
            with self.subTest(data=name):
                seen = set().union(*(visible_events(site, invitation) for invitation in invitations))
                published = sorted(path for path in files if path.endswith(".ics"))
                self.assertEqual(sorted(self.calendars(site, files).values()), published)
                self.assertEqual(set(self.calendars(site, files)), seen)
                for path in published:
                    self.assertRegex(path, rf"\Aassets/{re.escape(site['mediaDir'])}/[0-9a-f]{{16}}\.ics\Z")
                    name_hash = path.rsplit("/", 1)[1][:16]
                    key = hashlib.sha256(
                        "\n".join(sorted(item["token"] for item in invitations)).encode()
                    ).digest()
                    expected = hmac.new(key, files[path].encode(), hashlib.sha256).hexdigest()[:16]
                    self.assertEqual(name_hash, expected)
                    # a plain hash of the contents is not the name
                    self.assertNotEqual(name_hash, hashlib.sha256(files[path].encode()).hexdigest()[:16])

    def test_hidden_events_are_where_they_are_shown(self):
        site, invitations, files = self.built["data"]
        title = site["events"]["brunch"]["title"]
        guests = [invitation for invitation in invitations if "brunch" in visible_events(site, invitation)]
        self.assertEqual(len(guests), 2)
        brunch = self.calendars(site, files)["brunch"]
        pages = sorted(f"i/{invitation['token']}/index.html" for invitation in guests)
        self.assertEqual(
            sorted(path for path, content in files.items() if title in content),
            sorted([*pages, brunch]),
        )
        # its calendar file is linked from these pages only: not from the stub,
        # nor from the pages of the other guests
        file_name = brunch.rsplit("/", 1)[1]
        self.assertEqual(sorted(path for path, content in files.items() if file_name in content), pages)

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
        travel = next(section for section in site["sections"] if section["id"] == "travel")
        text = travel["widgets"][2]["text"]
        pages = [
            path for path, content in files.items()
            if text["ty"] in content or text["vy"] in content
        ]  # fmt: skip
        self.assertEqual(pages, ["i/IBAhKaLEOj1DHuOUdUMzQA/index.html"])

    def test_calendar_files_carry_nothing_of_the_invitation(self):
        for name, _site, invitations, files in self.sets():
            for invitation in invitations:
                for path, content in files.items():
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
                            # nothing of the page is under its token: the
                            # calendar files are in the media directory
                            self.assertNotIn(token, content)
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


def has_companion(invitation: dict) -> bool:
    widgets = invitation.get("sections", {}).get("invite", {}).get("widgets", {})
    return widgets.get("plus-one", {}).get("visible", False)


class CompanionTests(ExamplePagesTestCase):
    """The companion text is a paragraph of the invitation, only where switched on."""

    TEXT_WIDGET_RE = re.compile(r'<div class="widget text-widget[^"]*">\s*<p>(.*?)</p>\s*</div>', re.S)

    @staticmethod
    def companion_text(site: dict) -> dict:
        invite = next(section for section in site["sections"] if section["id"] == "invite")
        return next(widget for widget in invite["widgets"] if widget.get("id") == "plus-one")["text"]

    def invite_texts(self, page: str) -> list[str]:
        start = page.index('id="s-invite"')
        return self.TEXT_WIDGET_RE.findall(page[start:page.index("</section>", start)])

    def test_the_companion_text_is_between_the_invitation_and_the_date(self):
        counts = collections.Counter()
        for name, site, invitations, files in self.sets():
            text = self.companion_text(site)
            for invitation in invitations:
                page = files[f"i/{invitation['token']}/index.html"]
                texts = self.invite_texts(page)
                with self.subTest(data=name, token=invitation["token"][:4]):
                    self.assertTrue(texts[0].startswith("Мы, "), texts[0])
                    self.assertTrue(texts[-1].startswith("Ждём "), texts[-1])
                    if has_companion(invitation):
                        self.assertEqual(texts[1:-1], [build.text_to_html(text[invitation["form"]])])
                    else:
                        self.assertEqual(len(texts), 2)
                    counts[has_companion(invitation)] += 1
        # four of eight guests in the main set, one of three in the other
        self.assertEqual(counts, {True: 5, False: 6})

    def test_without_a_companion_the_text_is_nowhere_on_the_page(self):
        for name, site, invitations, files in self.sets():
            text = self.companion_text(site)
            lines = {line for form in ("ty", "vy") for line in text[form].split("\n")}
            for invitation in invitations:
                if has_companion(invitation):
                    continue
                own = self.own(invitation, files)
                with self.subTest(data=name, token=invitation["token"][:4]):
                    for path, content in own.items():
                        for line in lines:
                            self.assertNotIn(build.text_to_html(line), content, path)
                        self.assertNotIn("спутни", content, path)

    def test_there_is_no_companion_section(self):
        for name, _site, _invitations, files in self.sets():
            with self.subTest(data=name):
                self.assertEqual([path for path, content in files.items() if "s-plus-one" in content], [])


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
