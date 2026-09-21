"""Static checks of the page script source (no JavaScript engine required)."""

import gzip
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "assets" / "app.js"
APP_CSS = ROOT / "assets" / "app.css"
TEMPLATE = ROOT / "template.html"

# A namespace URI is an identifier, not a reference: nothing is requested from it.
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


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
        self.assertEqual(self.raw.count(SVG_NAMESPACE), 1)
        # "\b" never matches before "//" (a quote is not a word character),
        # so a protocol-relative address is looked for after a quote or a
        # bracket, and an absolute one by its scheme
        source = self.raw.replace(SVG_NAMESPACE, "")
        for pattern in (r"(?i)https?://", r"""(?i)['"(]//[a-z0-9.-]+\.[a-z]{2,}"""):
            self.assertNotRegex(source, pattern)
        for pattern in (r"\bfetch\s*\(", r"\bXMLHttpRequest\b", r"\bsendBeacon\b",
                        r"\bWebSocket\b", r"\bEventSource\b", r"\blocalStorage\b",
                        r"\bsessionStorage\b", r"\bdocument\.cookie\b"):
            self.assertNotRegex(self.code, pattern)

    def test_modules_go_through_the_registry(self):
        names = re.findall(r"\bregister\('([a-z-]+)',", self.code)
        for expected in ("cover", "reveal", "countdown", "events", "gallery"):
            self.assertIn(expected, names)
        self.assertEqual(len(names), len(set(names)), "duplicate module names")
        # every init runs inside try/catch and failures are reported as warnings
        runner = self.code[self.code.index("function start()"):]
        runner = runner[:runner.index("document.addEventListener")]
        self.assertRegex(runner, r"try\s*\{\s*module\.init\(document\);\s*\}"
                                 r"\s*catch\s*\(error\)\s*\{\s*report\(module\.name, error\);")
        reporter = self.code[self.code.index("function report("):self.code.index("function start()")]
        self.assertIn("console.warn", reporter)
        self.assertEqual(self.code.count("console.warn"), 1, "failures are reported in one place")
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

    def test_size_stays_small(self):
        # Our own code, not a library, so the budget is ours: without comments
        # the viewer window (module gallery) takes about 16.6 KB, the event
        # tabs (module events) about 7.1 KB and the cover bar (module coverBar)
        # about 2.3 KB; with them the file is about 37 KB (16.7 KB gzipped,
        # comments included).  Raise the limits deliberately,
        # when a module needs it, not to make room in advance.
        self.assertLess(len(self.code.encode("utf-8")), 40 * 1024)
        self.assertLess(len(gzip.compress(self.raw.encode("utf-8"), 9)), 20 * 1024)

    def test_timer_and_event_entry_points_are_guarded(self):
        # countdown: the tick that timers and events call is wrapped as a whole
        tick = self.code[self.code.index("function tick()"):]
        tick = tick[:tick.index("function onVisibilityChange")]
        self.assertRegex(tick, r"try\s*\{\s*update\(\);\s*\}\s*catch")
        self.assertIn("finish();", tick)
        # reveal: a scroll/resize sweep that does not depend on observer callbacks
        for event in ("scroll", "resize", "orientationchange"):
            self.assertIn("window.addEventListener('%s', onViewportChange" % event, self.code)
            self.assertIn("window.removeEventListener('%s', onViewportChange)" % event, self.code)
        self.assertIn("requestAnimationFrame(onFrame)", self.code)

    def test_every_countdown_starts_in_its_own_try(self):
        # several date widgets give several timers: one failing leaves the others
        init = self.code[self.code.index("register('countdown'"):]
        init = init[:init.index("\n  });")]
        self.assertRegex(init, r"forEach\.call\([^;]*function \(container\) \{\s*try \{\s*"
                               r"startCountdown\(container\);\s*\}\s*catch \(error\) \{")
        self.assertIn("container.hidden = true;", init)
        self.assertIn("report('countdown', error);", init)


class EventsSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = strip_comments(APP_JS.read_text(encoding="utf-8"))
        start = code.index("var EVENTS_RECHECK_MS")
        end = code.index("\n  });", code.index("register('events'")) + len("\n  });")
        cls.module = code[start:end]
        css = strip_comments(APP_CSS.read_text(encoding="utf-8"))
        cls.css = css
        start = css.index(".events__tabs {")
        cls.tabs_css = css[start:css.index(".stub {", start)]

    def function(self, name):
        """Source of a function of the module, up to the next declaration at its level."""
        if name == "startEvents":
            return self.module[self.module.index("function startEvents("):
                               self.module.index("register('events'")].rstrip()
        at = self.module.index("function %s(" % name)
        indent = at - self.module.rindex("\n", 0, at) - 1
        body = self.module[at:]
        following = re.search(r"\n {%d}(?:function \w+\(|var \w+ = |register\()" % indent, body)
        return body[:following.start() + 1] if following else body

    def test_one_instance_per_widget_each_in_its_own_try(self):
        init = self.module[self.module.index("register('events'"):]
        self.assertRegex(init, r"querySelectorAll\('\[data-events\]'\), function \(widget\) \{\s*"
                               r"try \{\s*startEvents\(widget\);\s*\}\s*catch \(error\) \{\s*"
                               r"report\('events', error\);")
        # every query of an instance starts from its own root
        start = self.function("startEvents")
        for query in re.findall(r"(\w+)\.querySelector(?:All)?\(", start):
            self.assertIn(query, ("widget", "tablist"))
        self.assertNotRegex(self.module, r"\bdocument\.querySelector")

    def test_runs_before_reveal_measures_the_page(self):
        # hiding the cards shortens the page; reveal must measure the final layout
        code = strip_comments(APP_JS.read_text(encoding="utf-8"))
        self.assertLess(code.index("register('events'"), code.index("register('reveal'"))

    def test_the_module_has_no_text_of_its_own(self):
        # the labels «past» / «now» live in the markup; the module only toggles hidden
        self.assertNotRegex(self.module, r"[А-Яа-яЁё]")
        self.assertNotRegex(self.module, r"\.textContent\s*=|createElement|createTextNode|appendChild")
        self.assertIn("label.hidden = label.getAttribute('data-events-when') !== state;", self.module)

    def test_handlers_are_guarded(self):
        listeners = re.findall(r"\.addEventListener\('[a-z]+', (\w+)\);", self.module)
        self.assertTrue(listeners)
        for listener in listeners:
            with self.subTest(listener=listener):
                self.assertRegex(self.module, r"var %s = guarded\(function" % listener)
        guarded = self.function("guarded")
        self.assertRegex(guarded, r"catch \(error\) \{\s*restore\(\);\s*report\('events', error\);")
        # a failure while starting also goes back to the markup as it was
        start = self.function("startEvents")
        self.assertRegex(start, r"\} catch \(error\) \{\s*restore\(\);\s*throw error;\s*\}\s*\}$")

    def test_restore_returns_the_view_without_the_script(self):
        restore = self.function("restore")
        for step in ("tablist.hidden = true;", "panel.hidden = false;",
                     "panel.removeAttribute('role');", "panel.removeAttribute('tabindex');",
                     "panel.setAttribute('aria-labelledby', labelledBy[i]);",
                     "markTime(tabs[i], '');", "markTime(panel, '');", "clearTimeout(timer);"):
            self.assertIn(step, restore)
        for listener in re.findall(r"\.addEventListener\(('[a-z]+', \w+)\);", self.module):
            with self.subTest(listener=listener):
                self.assertIn(".removeEventListener(%s);" % listener, restore)

    def test_the_tab_list_is_shown_last_and_counted_through_cssom(self):
        start = self.function("startEvents")
        body = start[start.rindex("try {"):]
        self.assertTrue(body[:body.index("} catch")].rstrip().endswith("tablist.hidden = false;"))
        self.assertIn("tablist.style.setProperty('--events-count', String(tabs.length));", start)
        self.assertNotRegex(self.module, r"setAttribute\('style'|\.style\.cssText|\.style\s*=")

    def test_tabs_follow_the_aria_pattern(self):
        start = self.function("startEvents")
        for step in ("panel.setAttribute('role', 'tabpanel');", "panel.setAttribute('tabindex', '0');",
                     "panel.setAttribute('aria-labelledby', tabs[i].id);"):
            self.assertIn(step, start)
        select = self.function("select")
        for step in ("tab.setAttribute('aria-selected', on ? 'true' : 'false');",
                     "tab.tabIndex = on ? 0 : -1;", "panels[i].hidden = !on;"):
            self.assertIn(step, select)
        # both axes, because the tabs stand in a row or in a column
        for key in ("ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"):
            self.assertIn(key + ":", self.module)
        keys = self.module[self.module.index("var onKeyDown"):self.module.index("var onFocusOut")]
        self.assertIn("event.preventDefault();", keys)
        self.assertIn("chosen = true;", keys)
        self.assertIn("select(to, true);", keys)
        self.assertIn("'Home'", keys)
        self.assertIn("'End'", keys)
        self.assertIn("% tabs.length", keys)
        # ids are read from the markup, never put together
        self.assertIn("tab.getAttribute('aria-controls')", start)
        self.assertNotRegex(self.module, r"'--(?:tab|panel|title)'|\+\s*'-")

    def test_the_open_window_holds_the_automatic_switch(self):
        modal = self.function("modalOpen")
        self.assertIn("root.classList.contains('is-lightbox-open')", modal)
        self.assertIn("getElementsByTagName('dialog')", modal)
        self.assertIn(".open", modal)
        refresh = self.function("refresh")
        self.assertIn("var pending = !chosen && target !== current;", refresh)
        self.assertIn("modalOpen()", refresh)
        self.assertRegex(refresh, r"if \(pending && !modal && !widget\.contains\(document\.activeElement\)\) \{\s*"
                                  r"select\(target, false\);")
        # while the window is open the check is repeated until it is closed
        self.assertIn("schedule(now, modal);", refresh)
        self.assertIn("soon ? EVENTS_MODAL_RETRY_MS : EVENTS_RECHECK_MS", self.function("schedule"))

    def test_rechecks_come_from_a_timeout_chain_and_page_events(self):
        self.assertNotIn("setInterval", self.module)
        schedule = self.function("schedule")
        self.assertRegex(schedule, r"if \(document\.hidden\) \{\s*return;")
        self.assertIn("timer = setTimeout(onTime, wait + EVENTS_EDGE_MS);", schedule)
        self.assertRegex(self.module, r"\bvar EVENTS_RECHECK_MS = 3600000;")
        for listener in ("document.addEventListener('visibilitychange', onTime);",
                         "window.addEventListener('pageshow', onTime);"):
            self.assertIn(listener, self.module)

    def test_the_default_tab_is_chosen_in_one_place(self):
        # a later answer about which tab to show changes this one function
        self.assertEqual(len(re.findall(r"(?<!function )\bdefaultTab\(now\)", self.module)), 2)  # start, recheck
        default = self.function("defaultTab")
        self.assertIn("if (primary !== -1 && now < ends[primary]) {", default)
        self.assertIn("return next === -1 ? panels.length - 1 : next;", default)
        self.assertIn("return Math.max(primary, 0);", default)
        mark = self.function("mark")
        self.assertIn("var state = now >= ends[i] ? 'past' : now >= starts[i] ? 'now' : '';", mark)
        self.assertIn("parseTarget(panel.getAttribute('data-events-start'))", self.module)
        self.assertIn("parseTarget(panel.getAttribute('data-events-end'))", self.module)

    def test_tab_styles(self):
        tab = re.search(r"\.events__tab \{([^}]*)\}", self.tabs_css).group(1)
        self.assertIn("min-block-size: var(--tap-min);", tab)
        self.assertIn("var(--duration-fast)", tab)
        self.assertIn("var(--events-count", self.tabs_css)
        self.assertIn("var(--events-tab-min)", self.tabs_css)
        self.assertNotRegex(self.tabs_css, r"@container|@media[^{]*width|:has\(|animation")
        for state in ('[aria-selected="true"]', ".is-past", ".is-now", ":focus-visible"):
            self.assertIn(".events__tab" + state, self.tabs_css)
        outside = re.sub(r"@media \(hover: hover\)\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", self.tabs_css)
        self.assertNotIn(":hover", outside)
        self.assertIn(".events__tab:hover", self.tabs_css)

    def test_panels_have_no_reveal_of_their_own(self):
        fragment = (ROOT / "fragments" / "widgets" / "events.html").read_text(encoding="utf-8")
        self.assertNotIn("data-reveal", fragment)
        self.assertNotRegex(self.tabs_css, r"data-reveal|@keyframes")

    def test_tab_width_token(self):
        self.assertRegex(self.css, r"\n\s*--events-tab-min: 11rem;")


class ScheduleStylesTest(unittest.TestCase):
    def test_time_column_leaves_room_for_the_description(self):
        # a long label takes the free width up to the minimum of the description,
        # and never less than the old share of the list
        css = strip_comments(APP_CSS.read_text(encoding="utf-8"))
        formula = ("grid-template-columns: fit-content(max(var(--schedule-time-max), "
                   "100% - var(--schedule-body-min))) minmax(0, 1fr);")
        self.assertEqual(css.count(formula), 2)  # the item, and the list with subgrid
        self.assertNotIn("fit-content(var(--schedule-time-max))", css)
        self.assertRegex(css, r"\n\s*--schedule-body-min: 16rem;")


class LightboxSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = strip_comments(APP_JS.read_text(encoding="utf-8"))
        cls.code = code
        cls.module = code[code.index("register('gallery'"):]
        # the markup of a page: the frame and the fragments of its sections
        fragments = sorted((ROOT / "fragments").rglob("*.html"))
        cls.fragments = {path.relative_to(ROOT / "fragments").as_posix(): path.read_text(encoding="utf-8")
                         for path in fragments}
        cls.template = TEMPLATE.read_text(encoding="utf-8") + "".join(cls.fragments.values())

    def test_requires_native_modal_dialog(self):
        guard = self.module[:self.module.index("var ui = null")]
        self.assertIn("window.HTMLDialogElement", guard)
        self.assertRegex(guard, r"typeof Dialog\.prototype\.showModal !== 'function'\)\s*\{\s*return;")
        self.assertIn(".showModal();", self.module)

    def test_link_default_is_cancelled_only_after_successful_open(self):
        handler = self.module[self.module.index("function onLinkClick"):]
        self.assertRegex(handler, r"try\s*\{\s*open\(link\);\s*event\.preventDefault\(\);\s*\}\s*catch")
        for key in ("altKey", "ctrlKey", "metaKey", "shiftKey"):
            self.assertIn("event." + key, handler)

    def test_history_is_left_alone(self):
        self.assertNotRegex(self.module, r"pushState|replaceState|location\.hash|\bhistory\b")

    def function(self, name):
        """Source of a function of the module, up to the next function."""
        body = self.module[self.module.index("function %s(" % name):]
        following = re.search(r"\n    (?:function \w+\(|var \w+ = guarded)", body[1:])
        return body[:following.start() + 1] if following else body

    def test_controls_are_labelled_and_focus_returns(self):
        for fragment in ("'aria-label'", "'aria-live', 'polite'", "'aria-disabled'",
                         "link.focus({ preventScroll: true })",
                         "(items[index].video ? ui.video : ui.close).focus();"):
            self.assertIn(fragment, self.module)

    def test_the_script_announces_the_viewer_on_the_tiles(self):
        # no dialog without the script, so no aria-haspopup in the markup
        self.assertIn("link.setAttribute('aria-haspopup', 'dialog');", self.module)
        guard = self.module[:self.module.index("aria-haspopup")]
        self.assertIn("typeof Dialog.prototype.showModal !== 'function'", guard)
        self.assertNotIn("aria-haspopup", self.template)

    def test_media_hooks(self):
        item = self.code[self.code.index("function readItem("):self.code.index("register('gallery'")]
        for hook in ("'data-media'", "'data-media-poster'", "'data-media-ratio'"):
            self.assertIn("link.getAttribute(%s)" % hook, item)
        self.assertIn("!== 'video'", item)
        # the ratio reaches CSS only after a check
        self.assertIn("RATIO_RE.test(ratio)", item)
        self.assertIn("video.style.setProperty('--lightbox-ratio', item.ratio);", self.module)

    def test_the_video_is_released_when_it_is_left(self):
        release = self.function("releaseVideo")
        for step in ("video.pause();", "video.removeAttribute('src');",
                     "video.removeAttribute('poster');", "video.load();"):
            self.assertIn(step, release)
        self.assertLess(release.index("video.pause();"), release.index("video.load();"))
        # leaving the item (show), closing and failing release it
        for name in ("show", "onClose", "fail"):
            with self.subTest(function=name):
                self.assertIn("releaseVideo();", self.function(name))

    def test_neighbours_load_only_posters(self):
        preload = self.function("preload")
        self.assertIn("item.video ? item.poster : item.href", preload)

    def test_play_is_called_in_the_click_handler(self):
        # the tap opens the window, show(…, true) starts the video, and nothing
        # asynchronous stands between the click and play()
        handler = self.function("onLinkClick")
        self.assertRegex(handler, r"try\s*\{\s*open\(link\);")
        opening = self.function("open")
        self.assertIn("show(group.indexOf(link), true);", opening)
        self.assertLess(opening.index(".showModal();"), opening.index("show(group.indexOf(link), true);"))
        chain = handler + opening + self.function("show") + self.function("showVideo") + self.function("startVideo")
        self.assertNotRegex(chain, r"\bawait\b|\bsetTimeout\(|requestAnimationFrame|\.then\(\s*function\s*\(\)\s*\{\s*[^}]*play")
        self.assertIn("if (autoplay) {\n        startVideo();", self.function("showVideo"))
        self.assertTrue(self.function("startVideo").split("{", 1)[1].strip().startswith("var promise = ui.video.play();"))
        # the refusal of the browser is not an error
        self.assertRegex(self.function("startVideo"), r"promise\.then\(null, function \(\) \{")
        # moving inside the window never starts a video by itself
        self.assertIn("show(at, false);", self.function("step"))
        self.assertIn("show(key === 'Home' ? 0 : items.length - 1, false);", self.function("onKeyDown"))
        self.assertEqual(len(re.findall(r"\bshow\(.*, true\);", self.module)), 1)
        self.assertEqual(len(re.findall(r"\bstartVideo\(\);", self.module)), 2)  # tile tap, frame click

    def test_a_not_started_video_starts_from_a_click_on_the_frame(self):
        click = self.function("onVideoClick")
        self.assertIn("video.paused && video.readyState === HAVE_NOTHING", click)
        self.assertIn("startVideo();", click)
        self.assertRegex(self.code, r"\bvar HAVE_NOTHING = 0;")
        self.assertIn("video.addEventListener('click', guarded(onVideoClick));", self.module)

    def test_keys_are_heard_on_the_document_while_the_window_is_open(self):
        self.assertIn("doc.addEventListener('keydown', ui.onKey);", self.function("open"))
        for name in ("onClose", "fail"):
            with self.subTest(function=name):
                self.assertIn("doc.removeEventListener('keydown', ui.onKey);", self.function(name))
        self.assertIn("onKey: guarded(onKeyDown)", self.module)
        self.assertNotRegex(self.module, r"dialog\.addEventListener\('keydown'")
        # keys on the video belong to the player; so does a gesture on its frame
        self.assertIn("event.target === ui.video", self.function("onKeyDown"))
        self.assertIn("event.target !== ui.video", self.function("onPointerDown"))

    def test_focus_does_not_stay_in_the_closed_window(self):
        close = self.function("onClose")
        self.assertIn("link.getClientRects().length", close)
        self.assertRegex(close, r"else if \(ui\.dialog\.contains\(doc\.activeElement\)\) \{\s*"
                                r"doc\.activeElement\.blur\(\);")

    def test_event_handlers_are_guarded(self):
        listeners = re.findall(r"\.addEventListener\('[a-z]+', ([^;]+)\);", self.module)
        self.assertTrue(listeners)
        # onLinkClick catches on its own; revealInRibbon only scrolls the ribbon
        allowed = {"onLinkClick", "revealInRibbon", "ui.onKey", "onZoom"}
        for listener in listeners:
            with self.subTest(listener=listener):
                self.assertTrue(listener.startswith("guarded(") or listener in allowed, listener)
        self.assertIn("var onZoom = guarded(", self.module)

    def test_template_hooks(self):
        self.assertRegex(self.fragments["partials/location-card.html"],
                         r'<ul class="[^"]*\bgallery--ribbon\b[^"]*"[^>]* data-gallery\b')
        self.assertRegex(self.fragments["widgets/media.html"],
                         r'<ul class="gallery gallery--\{\{\.layout\}\}"[^>]* data-gallery\b')
        self.assertRegex(self.fragments["media/image.html"],
                         r'<a class="gallery__link media-tile" href="\{\{\.src\}\}" data-lightbox>')
        self.assertRegex(self.fragments["partials/location-card.html"],
                         r'<a class="media media--scheme" href="\{\{\.src\}\}" data-lightbox>')
        self.assertRegex(self.fragments["media/video.html"],
                         r'<a class="gallery__link media-tile media-tile--video" href="\{\{\.src\}\}" '
                         r'type="video/mp4" data-lightbox data-media="video" '
                         r'data-media-poster="\{\{\.posterSrc\}\}" '
                         r'data-media-ratio="\{\{\.ratio\}\}"')
        self.assertNotIn("<dialog", self.template)
        self.assertNotIn("<video", self.template)
        maps = self.fragments["partials/map-links.html"]
        maps = maps[maps.index("<!-- if:.hasMapLinks -->"):]
        self.assertIn('<ul class="venue__maps"', maps)
        # the whole list is inside the condition; indentation is not the point
        self.assertTrue(" ".join(maps.split()).endswith("</ul> <!-- endif -->"))


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
            if selector.strip() in ("from", "to"):  # a keyframe runs only under a marker rule
                continue
            for part in selector.split(","):
                self.assertRegex(part.strip(), r"^html\.js\.[a-z-]+ ")

    def test_print_and_reduced_motion_fallbacks(self):
        self.assertRegex(self.section, r"@media print\s*\{")
        self.assertRegex(self.section, r"@media \(prefers-reduced-motion: reduce\)\s*\{")

    def test_reveal_transition_runs_only_while_appearing(self):
        # hiding (marker set) is instant; the transition starts with is-revealed
        # and is dropped with is-settled
        self.assertIn("html.js.reveal-on [data-reveal].is-revealed:not(.is-settled) {", self.section)
        self.assertNotRegex(self.section, r"html\.js\.reveal-on \[data-reveal\]\s*\{\s*transition")
        self.assertNotRegex(self.section, r"\[data-reveal\]:not\(\.is-settled\)\s*\{")
        hidden = re.search(r"html\.js\.reveal-on \[data-reveal\]:not\(\.is-revealed\) \{([^}]*)\}",
                           self.section)
        self.assertIsNotNone(hidden)
        self.assertNotIn("transition", hidden.group(1))

    def test_reveal_uses_the_entrance_duration(self):
        rule = re.search(r"\.is-revealed:not\(\.is-settled\) \{([^}]*)\}", self.section).group(1)
        # opacity starts gently (a steep start reads as a flash), the offset decelerates
        self.assertIn("opacity var(--duration-slow) var(--ease-in-out),", rule)
        self.assertIn("transform var(--duration-slow) var(--ease-out);", rule)
        self.assertNotRegex(rule, r"--duration-(?:fast|base)")

    def test_cover_animation_does_not_hold_final_values(self):
        keyframes = self.section[self.section.index("@keyframes cover-in"):]
        keyframes = keyframes[:keyframes.index("html.js")]
        self.assertNotRegex(keyframes, r"\bto\s*\{|100%")
        self.assertRegex(self.section, r"animation: cover-in [^;]*\bbackwards;")
        self.assertNotRegex(self.section, r"animation: cover-in [^;]*\bboth\b")
        # the cascade counts with an index variable, not with literal multipliers
        cover = self.section[self.section.index("html.js.cover-animate"):]
        self.assertIn("calc(var(--cover-index, 0) * var(--reveal-stagger))", cover)
        self.assertNotRegex(cover, r"var\(--reveal-stagger\)\s*\*\s*\d")

    def test_only_opacity_and_vertical_transform_are_animated(self):
        # keyframes change the opacity or a vertical offset
        keyframes = re.findall(r"@keyframes [\w-]+\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", self.section)
        self.assertEqual(len(keyframes), 4)  # cover-in; the veil, the cover content, the bar
        for body in keyframes:
            for prop, value in re.findall(r"([\w-]+):\s*([^;]+);", body):
                with self.subTest(prop=prop):
                    self.assertIn(prop, ("opacity", "transform"))
                    if prop == "transform":
                        self.assertRegex(value, r"^translateY\(")
        # transitions run on the same two properties
        transitions = re.findall(r"\btransition:\s*([^;]+);", self.section)
        self.assertTrue(transitions)
        for value in transitions:
            if value.strip() != "none":
                for part in value.split(","):
                    self.assertIn(part.split()[0], ("opacity", "transform"))
        # what the progress of the cover drives is only the opacity
        driven = re.findall(r"\{([^{}]*var\(--cover-progress\)[^{}]*)\}", self.section)
        self.assertEqual(len(driven), 3)  # the veil, the cover content, the bar
        for body in driven:
            self.assertEqual(re.findall(r"([\w-]+):", body), ["opacity"])
        # no layout property is set here at all (a timeline inset is not one)
        self.assertNotRegex(self.section, r"translateX|translate\(|translate3d|scale\(")
        self.assertNotRegex(self.section, r"(?<![\w-])(?:margin|inset|width|height|top|bottom)[\w-]*\s*:")


class CoverBarTest(unittest.TestCase):
    """The cover folds into the top bar: CSS and the module coverBar."""

    @classmethod
    def setUpClass(cls):
        code = strip_comments(APP_JS.read_text(encoding="utf-8"))
        cls.code = code
        cls.module = code[code.index("var COVER_BAR_TIMELINES"):code.index("var EVENTS_RECHECK_MS")]
        raw = APP_CSS.read_text(encoding="utf-8")
        motion = raw[raw.index("@property --cover-progress"):raw.index("\n   8. Gallery & lightbox")]
        css = strip_comments(raw)
        cls.css = css
        cls.layout = css[css.index(".cover-bar {"):css.index(".invite__greeting")]
        cls.motion = strip_comments(motion + "*/")

    def function(self, name):
        body = self.module[self.module.index("function %s(" % name):]
        following = re.search(r"\n    function \w+\(", body[1:])
        return body[:following.start() + 1] if following else body

    def test_the_driver_is_chosen_by_one_condition_in_css_and_in_the_module(self):
        conditions = re.findall(r"@supports ([^{]*timeline[^{]*?)\s*\{", self.css)
        self.assertEqual(conditions, ["(animation-timeline: --a) and (timeline-scope: --a)"])
        self.assertIn("var COVER_BAR_TIMELINES = '%s';" % conditions[0], self.module)
        self.assertIn("css.supports(COVER_BAR_TIMELINES)", self.module)
        # with the timelines the module only sets the marker
        self.assertRegex(self.module, r"css\.supports\(COVER_BAR_TIMELINES\)\) \{\s*"
                                      r"root\.classList\.add\('cover-bar-on'\);\s*return;")

    def test_the_bar_is_hidden_without_the_marker(self):
        self.assertRegex(self.layout, r"\n\.cover-bar\[data-cover-bar\] \{\s*display: none;\s*\}")
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", self.css)
        for selector, body in rules:
            if re.search(r"position:\s*sticky|display:\s*block", body) and "cover-bar" in selector:
                with self.subTest(selector=selector.strip()):
                    self.assertRegex(selector.strip(), r"^html\.js\.cover-bar-on \.cover-bar\[data-cover-bar\]$")
        printed = self.layout[self.layout.index("@media print"):]
        self.assertRegex(printed, r"\.cover-bar,\s*html\.js\.cover-bar-on \.cover-bar\[data-cover-bar\]\s*\{"
                                  r"\s*display: none;")

    def test_the_bar_takes_no_room_and_anchors_clear_it(self):
        # one token: the height, the negative offset and the scroll padding
        bar = re.search(r"\n\.cover-bar \{([^}]*)\}", self.css).group(1)
        self.assertIn("block-size: var(--cover-bar-block-size);", bar)
        sticky = re.search(r"html\.js\.cover-bar-on \.cover-bar\[data-cover-bar\] \{([^}]*)\}",
                           self.layout).group(1)
        for line in ("position: sticky;", "inset-block-start: 0;", "z-index: var(--z-sticky);",
                     "margin-block-start: calc(var(--cover-bar-block-size) * -1);"):
            self.assertIn(line, sticky)
        self.assertRegex(self.layout, r"html\.js\.cover-bar-on \{\s*"
                                      r"scroll-padding-block-start: var\(--cover-bar-block-size\);\s*\}")
        # the names give way first, the bar stays one line
        self.assertIn("white-space: nowrap;", self.layout)
        self.assertIn("text-overflow: ellipsis;", self.layout)

    def test_the_strip_above_the_bar_is_filled_by_the_bar_itself(self):
        # a pseudo-element of the bar: it shows and fades with the bar (its
        # opacity, the reduced-motion step), is gone with it without the marker
        # and in print, takes no room and catches no taps
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", self.css)
        fills = [(selector.strip(), body) for selector, body in rules if "::before" in selector
                 and "cover-bar" in selector]
        self.assertEqual([selector for selector, _body in fills], [".cover-bar::before"])
        fill = fills[0][1]
        for line in ('content: "";', "position: absolute;", "inset-inline: 0;", "inset-block-end: 100%;",
                     "block-size: var(--viewport-height);", "background-color: inherit;",
                     "pointer-events: none;"):
            with self.subTest(line=line):
                self.assertIn(line, fill)
        # nothing of its own that could make it visible apart from the bar
        self.assertNotRegex(fill, r"\b(?:opacity|display|visibility|animation|z-index)\s*:")
        # the bar is its containing block in the styleguide too (sticky on the page)
        bar = re.search(r"\n\.cover-bar \{([^}]*)\}", self.css).group(1)
        self.assertIn("position: relative;", bar)
        printed = self.layout[self.layout.index("@media print"):]
        self.assertRegex(printed, r"\.cover-bar,[^{}]*\{\s*display: none;")

    def test_the_canvas_of_an_invitation_has_the_colour_of_the_bar(self):
        # the canvas (beyond the edges of the page, the under-page colour of
        # WebKit) gets the token of the bar, on the invitation pages only: the
        # stub and the styleguide keep the canvas of their body
        rules = [(" ".join(selector.split()), body)
                 for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.css)]
        backgrounds = [selector for selector, body in rules
                       if re.match(r"html\b", selector) and re.search(r"\bbackground(?:-color)?\s*:", body)]
        self.assertEqual(backgrounds, ["html.invitation"])
        canvas = dict(rules)["html.invitation"]
        self.assertEqual(canvas.strip(), "background-color: var(--color-cover-veil);")
        # with a background of <html> the one of body no longer reaches the
        # canvas: body is at least as tall as the window, so a short page shows
        # its own background below the content, as before
        self.assertEqual(dict(rules)["html.invitation body"].strip(),
                         "min-block-size: var(--viewport-height-dynamic);")
        self.assertIn("color-scheme: only light;", self.css)
        template = (ROOT / "template.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="ru" class="invitation">', template)
        for other in ("stub.html", "styleguide.html"):
            with self.subTest(page=other):
                self.assertNotIn("invitation", re.search(r"<html[^>]*>", (ROOT / other).read_text(
                    encoding="utf-8")).group(0))

    def test_the_timeline_covers_the_cover_itself(self):
        self.assertIn("view-timeline-inset: 0;", self.motion)
        self.assertIn("view-timeline-name: --cover;", self.motion)
        self.assertIn("timeline-scope: --cover;", self.motion)
        self.assertIn("--cover-span: calc(100% - var(--cover-bar-block-size));", self.motion)

    def test_the_css_driver_animates_the_opacity_of_three_elements(self):
        # no inherited property animated on the whole subtree of the cover:
        # each element fades by the timeline on its own part of the course
        driver = self.motion[self.motion.index("@supports"):self.motion.index("@media")]
        self.assertNotIn("--cover-progress", driver)
        self.assertNotRegex(self.motion, r"@keyframes [\w-]+\s*\{[^@]*--cover-progress:")
        span = "exit-crossing 0% exit-crossing var(--cover-span)"
        expected = {
            r"\.cover__backdrop::after": ("cover-veil", span),
            r"\.cover__inner": ("cover-fade",
                                "exit-crossing 0% exit-crossing calc(var(--cover-span) * var(--cover-fade-end))"),
            r"\.cover-bar\[data-cover-bar\]": ("cover-bar-in",
                                            "exit-crossing calc(var(--cover-span) * var(--cover-bar-fade-start)) "
                                            "exit-crossing var(--cover-span)"),
        }
        for selector, (name, animation_range) in expected.items():
            with self.subTest(animation=name):
                rule = re.search(r"html\.js\.cover-bar-on %s \{([^}]*)\}" % selector, driver).group(1)
                self.assertIn("animation: %s linear both;" % name, rule)
                self.assertIn("animation-timeline: --cover;", rule)
                found = re.search(r"animation-range:\s*([^;]+);", rule).group(1)
                self.assertEqual(" ".join(found.split()), animation_range)
        keyframes = dict(re.findall(r"@keyframes ([\w-]+) \{\s*(from \{[^}]*\}\s*to \{[^}]*\})", self.motion))
        self.assertEqual(" ".join(keyframes["cover-veil"].split()),
                         "from { opacity: var(--cover-veil-opacity); } to { opacity: 1; }")
        self.assertEqual(" ".join(keyframes["cover-fade"].split()), "from { opacity: 1; } to { opacity: 0; }")
        self.assertEqual(" ".join(keyframes["cover-bar-in"].split()), "from { opacity: 0; } to { opacity: 1; }")
        self.assertRegex(self.motion, r'@property --cover-progress \{\s*syntax: "<number>";\s*'
                                      r"inherits: true;\s*initial-value: 0;\s*\}")

    def test_reduced_motion_steps_and_print_unfolds(self):
        reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}",
                             self.motion)
        three = (r"html\.js\.cover-bar-on \.cover__backdrop::after,\s*html\.js\.cover-bar-on \.cover__inner,\s*"
                 r"html\.js\.cover-bar-on \.cover-bar\[data-cover-bar\] \{")
        # with reduced motion all three run over the whole course and step at its end
        self.assertTrue(any(re.search(three + r"\s*animation-timing-function: steps\(1, jump-end\);\s*"
                                      r"animation-range: exit-crossing 0% exit-crossing var\(--cover-span\);",
                                      block) for block in reduced))
        printed = re.findall(r"@media print\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", self.motion)
        self.assertEqual(len(printed), 1)
        self.assertRegex(printed[0], three + r"\s*animation: none;")
        self.assertRegex(printed[0], r"html\.js\.cover-bar-on \.cover__inner \{\s*opacity: 1;")
        # the module counts in steps too
        self.assertIn("value = value >= 1 ? 1 : 0;", self.function("progress"))

    def test_the_module_writes_only_the_progress_on_the_cover_and_the_bar(self):
        styles = re.findall(r"(\w+)\.style\.(\w+)\(([^)]*)\)", self.module)
        self.assertEqual(sorted({method for _node, method, _args in styles}),
                         ["removeProperty", "setProperty"])
        for node, _method, args in styles:
            self.assertEqual(node, "node")
            self.assertTrue(args.startswith("'--cover-progress'"), args)
        self.assertIn("var nodes = [cover, bar];", self.module)
        self.assertNotRegex(self.module, r"setAttribute\('style'|\.style\.cssText|\.style\s*=|"
                                         r"\.style\.[a-zA-Z]+\s*=|root\.style|documentElement\.style")
        self.assertNotRegex(self.module, r"[А-Яа-яЁё]")  # no text of its own
        # frames, not every scroll event
        self.assertIn("frame = window.requestAnimationFrame(update);", self.function("onChange"))
        self.assertIn("{ passive: true }", self.module)

    def test_stop_returns_the_view_without_the_module(self):
        stop = self.function("stop")
        self.assertIn("root.classList.remove('cover-bar-on');", stop)
        self.assertIn("node.style.removeProperty('--cover-progress');", stop)
        for listener in re.findall(r"window\.addEventListener\('(\w+)', onChange", self.module):
            with self.subTest(listener=listener):
                self.assertIn("window.removeEventListener('%s', onChange);" % listener, stop)
        self.assertRegex(self.function("update"), r"catch \(error\) \{\s*stop\(\);\s*report\('coverBar', error\);")
        # the listeners are in place before the first count, so a failure removes them
        init = self.module[self.module.index("root.classList.add('cover-bar-on');\n    window"):]
        self.assertLess(init.index("addEventListener('scroll'"), init.index("update();"))

    def test_registered_after_the_cover(self):
        self.assertLess(self.code.index("register('cover',"), self.code.index("register('coverBar',"))
        self.assertIn("root.classList.add('cover-animate');",
                      self.code[self.code.index("register('cover',"):self.code.index("register('coverBar',")])

    def test_the_tokens(self):
        for token, value in (("--cover-fade-end", "0.6"), ("--cover-bar-fade-start", "0.55"),
                             ("--z-sticky", "50"), ("--cover-bar-padding-block", "var(--space-2xs)")):
            with self.subTest(token=token):
                self.assertRegex(self.css, r"\n\s*%s: %s;" % (re.escape(token), re.escape(value)))
        self.assertRegex(self.css, r"--cover-bar-size: max\(var\(--tap-min\),\s*calc\(var\(--text-base\) \* "
                                   r"var\(--line-height-tight\) \+ var\(--cover-bar-padding-block\) \* 2\)\);")
        self.assertIn("--cover-bar-block-size: calc(var(--cover-bar-size) + var(--safe-inset-top));", self.css)


def css_ms(css, token):
    value = re.search(r"\n\s*" + re.escape(token) + r":\s*([\d.]+)(ms|s)\b", css)
    number = float(value.group(1))
    return number * 1000 if value.group(2) == "s" else number


class RevealInvariantsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = strip_comments(APP_JS.read_text(encoding="utf-8"))
        cls.module = code[code.index("register('reveal'"):code.index("register('countdown'")]
        cls.code = code
        cls.css = APP_CSS.read_text(encoding="utf-8")

    def test_settles_only_at_the_real_end_of_the_opacity_transition(self):
        # an event from a nested element, the transform transition or an extra
        # transitionend with elapsedTime 0 (sent while scrolling) is not the end
        end = self.module[self.module.index("function isRevealEnd("):self.module.index("function settleLater(")]
        self.assertRegex(end, r"if \(event\.target !== element\) \{\s*return false;\s*\}")
        self.assertRegex(end, r"if \(event\.type === 'transitioncancel'\) \{\s*return true;\s*\}")
        self.assertIn("return event.propertyName === 'opacity' && event.elapsedTime > 0;", end)
        later = self.module[self.module.index("function settleLater("):self.module.index("function show(")]
        self.assertIn("if (event && !isRevealEnd(event, element))", later)
        for event in ("transitionend", "transitioncancel"):
            self.assertIn("element.addEventListener('%s', done);" % event, later)
            self.assertIn("element.removeEventListener('%s', done);" % event, later)
        self.assertIn("setTimeout(done, REVEAL_SETTLE_MS)", later)

    def test_reveals_from_the_first_visible_pixel(self):
        # a non-zero inset leaves a band at the bottom edge where a block is on
        # screen but still empty
        self.assertRegex(self.code, r"\bvar REVEAL_INSET = 0;")
        self.assertIn("rootMargin: REVEAL_MARGIN", self.module)

    def test_revealed_blocks_are_never_hidden_again(self):
        self.assertNotRegex(self.module, r"classList\.remove\('is-(?:revealed|settled)'\)")
        self.assertNotRegex(self.module, r"classList\.toggle\(")
        # the marker is removed only on failure (everything becomes visible)
        self.assertEqual(self.module.count("root.classList.remove('reveal-on')"), 1)
        fail = self.module[self.module.index("function fail()"):]
        self.assertTrue(fail[:fail.index("}")].strip().endswith("finish();"))

    def test_safety_timer_outlasts_the_longest_reveal(self):
        settle = int(re.search(r"REVEAL_SETTLE_MS = (\d+)", self.code).group(1))
        stagger_max = int(re.search(r"REVEAL_STAGGER_MAX = (\d+)", self.code).group(1))
        longest = css_ms(self.css, "--duration-slow") + stagger_max * css_ms(self.css, "--reveal-stagger")
        self.assertGreater(settle, longest + 200)


class GalleryStylesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        css = APP_CSS.read_text(encoding="utf-8")
        start = css.index("\n   8. Gallery & lightbox")
        end = css.find("\n   9. ", start)  # the last section, unless one is added after it
        cls.section = strip_comments("/*" + (css[start:] if end == -1 else css[start:end] + "*/"))
        cls.css = strip_comments(css)

    def test_ribbon_needs_js_class_and_stays_below_the_layout_breakpoint(self):
        selectors = re.findall(r"([^{}]+)\{[^{}]*scroll-snap-type[^{}]*\}", self.section)
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertRegex(selector.strip(), r"^html\.js \.gallery--ribbon$")
        self.assertIn("@media not all and (min-width: 48em)", self.section)
        self.assertIn("overscroll-behavior-inline: contain;", self.section)

    def test_no_raw_values_outside_tokens(self):
        body = re.sub(r"@media[^{]*\{", "{", self.section)
        self.assertNotRegex(body, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\d(?:px|rem|em|ms|s|vh|vw)\b")

    def test_hover_only_inside_hover_media_query(self):
        outside = re.sub(r"@media \(hover: hover\)\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", self.section)
        self.assertNotIn(":hover", outside)

    def test_dialog_is_hidden_until_open_and_motion_can_be_reduced(self):
        self.assertRegex(self.section, r"\.lightbox\[open\]\s*\{\s*display: grid;")
        self.assertNotRegex(self.section, r"\.lightbox\s*\{[^{}]*\bdisplay:")
        self.assertRegex(self.section, r"@media \(prefers-reduced-motion: reduce\)\s*\{")

    def test_printing_leaves_the_invitation_on_the_page(self):
        printed = self.section[self.section.index("@media print"):]
        self.assertRegex(printed, r"\.lightbox\[open\]\s*\{\s*display: none;")
        # the page gets its scrolling and the scrollbar gap back
        self.assertRegex(printed, r"html\.is-lightbox-open\s*\{\s*overflow: visible;")
        self.assertIn("padding-inline-end: 0;", printed)

    def test_the_layout_breakpoint_is_the_only_one_and_matches_its_token(self):
        token = re.search(r"--bp-layout:\s*([^;]+);", self.css)
        self.assertIsNotNone(token)
        widths = set(re.findall(r"@media[^{]*\(\s*(?:min|max)-width:\s*([^)\s]+)\s*\)", self.css))
        self.assertEqual(widths, {token.group(1).strip()})

    def test_gallery_link_focus_ring_is_drawn_inside_the_frame(self):
        self.assertRegex(self.css, r"\.gallery__link:focus-visible::after,\s*\.gallery__link\.is-focus::after\s*\{"
                                   r"[^{}]*outline-offset: var\(--focus-ring-offset-inset\);")


if __name__ == "__main__":
    unittest.main()
