/* Скрипт страницы приглашения: прогрессивное улучшение поверх готового HTML.

   Устройство файла:
     1. Класс js на <html> — первое действие; только под ним действуют скрытые
        начальные состояния анимаций. Не загрузился скрипт — всё видно сразу.
     2. Реестр модулей: register(name, init). Каждый init выполняется после
        разбора документа в собственном try/catch — ошибка одного модуля не
        отключает остальные.
     3. Модули. Цепляются только за data-атрибуты; классы is-* и классы-маркеры
        на <html> — это состояния для стилей (секция 7 в app.css).

   Без сборки и зависимостей; совместимо с CSP script-src 'self'
   (нет inline-кода, eval, new Function). Содержимое страницы доступно
   и без скрипта. */
(function () {
  'use strict';

  document.documentElement.classList.add('js');

  var root = document.documentElement;


  /* ==========================================================================
     Реестр модулей
     ========================================================================== */

  var modules = [];
  var started = false;

  /* Только имя модуля и тип ошибки — без данных страницы. */
  function report(name, error) {
    console.warn('app: module "' + name + '" failed (' +
      (error && error.name ? error.name : 'Error') + ')');
  }

  /* init(document) вызывается один раз, когда DOM разобран. */
  function register(name, init) {
    modules.push({ name: name, init: init });
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    modules.forEach(function (module) {
      try {
        module.init(document);
      } catch (error) {
        report(module.name, error);
      }
    });
  }

  /* Скрипт подключён с defer: DOMContentLoaded ещё впереди. Остальные ветки —
     страховка на случай другого способа подключения. Подписка стоит до кода
     модулей: сбой при объявлении позднего модуля не отменит запуск ранних. */
  document.addEventListener('DOMContentLoaded', start);
  window.addEventListener('load', start);
  if (document.readyState === 'complete') {
    setTimeout(start, 0);
  }

  function prefersReducedMotion() {
    return typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }


  /* ==========================================================================
     Модуль cover — анимация обложки при загрузке
     --------------------------------------------------------------------------
     Сама анимация — в CSS под маркером cover-animate на <html>. Маркер ставится,
     только если страница ещё не была отрисована: иначе уже видимая обложка
     исчезла бы и появилась заново.
     ========================================================================== */

  var COVER_LATE_MS = 1000;

  function alreadyPainted() {
    var perf = window.performance;
    if (!perf || typeof perf.now !== 'function') {
      return true;
    }
    var types = window.PerformanceObserver && window.PerformanceObserver.supportedEntryTypes;
    if (types && types.indexOf('paint') !== -1 && typeof perf.getEntriesByType === 'function') {
      return perf.getEntriesByType('paint').length > 0;
    }
    /* Нет Paint Timing — судим по времени от начала навигации. */
    return perf.now() > COVER_LATE_MS;
  }

  register('cover', function () {
    if (prefersReducedMotion() || alreadyPainted()) {
      return;
    }
    root.classList.add('cover-animate');
  });


  /* ==========================================================================
     Модуль coverBar — сворачивание обложки [data-cover] в панель [data-cover-bar]
     --------------------------------------------------------------------------
     Панель и все правила сворачивания — в CSS под маркером cover-bar-on на
     <html>; без маркера панели нет, обложка неподвижна. Прогресс
     --cover-progress (0…1) там, где есть временные шкалы прокрутки, считает
     сам CSS, и модуль только ставит маркер. Условие выбора — то же, что у
     @supports в app.css. Иначе модуль считает прогресс по положению обложки
     не чаще раза в кадр и пишет его через CSSOM на обложку и панель. При
     сокращённом движении прогресс ступенчатый: 0 или 1. Любая ошибка снимает
     маркер и записанные значения — страница возвращается к виду без модуля.
     Своих строк у модуля нет; модуль cover он не трогает.
     ========================================================================== */

  var COVER_BAR_TIMELINES = '(animation-timeline: --a) and (timeline-scope: --a)';

  register('coverBar', function (doc) {
    var cover = doc.querySelector('[data-cover]');
    var bar = doc.querySelector('[data-cover-bar]');
    if (!cover || !bar) {
      return;
    }
    var css = window.CSS;
    if (css && typeof css.supports === 'function' && css.supports(COVER_BAR_TIMELINES)) {
      root.classList.add('cover-bar-on');
      return;
    }
    if (typeof window.requestAnimationFrame !== 'function') {
      return;
    }

    var reduced = typeof window.matchMedia === 'function' ?
      window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    var nodes = [cover, bar];
    var frame = 0;
    var last = '';

    /* 0 — верх обложки у верха окна, 1 — от обложки осталась полоса панели. */
    function progress() {
      var box = cover.getBoundingClientRect();
      var span = box.height - bar.offsetHeight;
      var value = span > 0 ? Math.min(1, Math.max(0, -box.top / span)) : 1;
      if (reduced && reduced.matches) {
        value = value >= 1 ? 1 : 0;
      }
      return String(Math.round(value * 1000) / 1000);
    }

    function stop() {
      window.removeEventListener('scroll', onChange, true);
      window.removeEventListener('resize', onChange);
      window.removeEventListener('load', onChange);
      if (frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
      root.classList.remove('cover-bar-on');
      nodes.forEach(function (node) {
        node.style.removeProperty('--cover-progress');
        if (!node.getAttribute('style')) {
          node.removeAttribute('style');
        }
      });
    }

    function update() {
      frame = 0;
      try {
        var value = progress();
        if (value !== last) {
          last = value;
          nodes.forEach(function (node) {
            node.style.setProperty('--cover-progress', value);
          });
        }
      } catch (error) {
        stop();
        report('coverBar', error);
      }
    }

    function onChange() {
      if (!frame) {
        frame = window.requestAnimationFrame(update);
      }
    }

    /* Сначала маркер: без него панель скрыта и её высоту не измерить.
       Подписка до первого расчёта: при ошибке stop() её же и снимет.
       На сенсорном экране страницу приглашения прокручивает body (app.css,
       секция 3), а scroll элемента до window не всплывает — слушатель ловит
       его на фазе перехвата, прокрутку документа (десктоп) тоже. */
    root.classList.add('cover-bar-on');
    window.addEventListener('scroll', onChange, { passive: true, capture: true });
    window.addEventListener('resize', onChange);
    window.addEventListener('load', onChange);
    update();
  });


  /* ==========================================================================
     Модуль events — переключатель событий [data-events]
     --------------------------------------------------------------------------
     Без скрипта карточки событий идут подряд по времени начала, список
     вкладок скрыт атрибутом hidden в разметке. Со скриптом — вкладки по
     шаблону ARIA Tabs: видна одна карточка; стрелки (по кругу), Home и End,
     выбор следует за фокусом, tabindex="0" только у выбранной вкладки. Роли
     и связи панелей ставит скрипт, id берёт из разметки (aria-controls).
     Число вкладок уходит в стили переменной --events-count через CSSOM.

     Время: у карточки data-events-start и data-events-end — ISO со
     смещением; окончание сборка подставляет всегда. Моменты сравниваются
     абсолютно, часовой пояс гостя не влияет. Событие прошло, когда
     наступило окончание, и идёт между началом и окончанием. Пометки —
     элементы [data-events-when="past|now"] в разметке: скрипт только
     снимает и ставит hidden и классы is-past / is-now на вкладке и
     карточке. Своих текстов у модуля нет.

     Вкладка по умолчанию — функция defaultTab(): первостепенное событие
     (id карточки в data-events-primary), пока оно не прошло; потом
     ближайшее непрошедшее; прошли все — последнее. Без корректного
     времени — первостепенное, иначе первое.

     Пересчёт — на ближайшей границе событий, но не реже раза в час, и при
     возврате на вкладку браузера. Выбранную вкладку пересчёт не меняет,
     если гость выбирал её сам, пока фокус внутри виджета (переключение
     догонит, когда фокус уйдёт) и пока открыто модальное окно (проверка
     повторяется, пока окно не закроют). Любая ошибка возвращает разметку к
     виду «без скрипта».

     Модуль объявлен раньше reveal и запускается раньше него: скрытие
     карточек укорачивает страницу, и reveal должен мерить блоки уже в
     окончательной раскладке — иначе при перезагрузке посреди страницы
     блок, который на самом деле на экране, появился бы с затуханием.
     Время разбирает parseTarget из модуля countdown (к запуску модулей весь
     файл уже выполнен).
     ========================================================================== */

  var EVENTS_RECHECK_MS = 3600000;    /* пересчёт не реже раза в час */
  var EVENTS_MODAL_RETRY_MS = 1000;   /* пока открыто окно — ждать его закрытия */
  var EVENTS_EDGE_MS = 50;            /* пересчёт чуть позже границы, а не до неё */
  var EVENTS_STEPS = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };

  /* Открыто окно просмотра или другой <dialog>: вкладка под ним не
     переключается. */
  function modalOpen() {
    if (root.classList.contains('is-lightbox-open')) {
      return true;
    }
    var dialogs = document.getElementsByTagName('dialog');
    for (var i = 0; i < dialogs.length; i += 1) {
      if (dialogs[i].open) {
        return true;
      }
    }
    return false;
  }

  /* Пометка времени на вкладке или карточке: 'past', 'now' или ''. */
  function markTime(node, state) {
    node.classList.toggle('is-past', state === 'past');
    node.classList.toggle('is-now', state === 'now');
    Array.prototype.forEach.call(node.querySelectorAll('[data-events-when]'), function (label) {
      label.hidden = label.getAttribute('data-events-when') !== state;
    });
  }

  function startEvents(widget) {
    var tablist = widget.querySelector('[data-events-tablist]');
    var tabs = tablist ? Array.prototype.slice.call(tablist.querySelectorAll('[data-events-tab]')) : [];
    var all = Array.prototype.slice.call(widget.querySelectorAll('[data-events-panel]'));
    var panels = tabs.map(function (tab) {
      var id = tab.getAttribute('aria-controls');
      return all.filter(function (panel) {
        return panel.id === id;
      })[0];
    });
    /* Одна карточка — переключать нечего; карточка без вкладки или вкладка
       без карточки — разметка не та, всё остаётся видимым. */
    if (tabs.length < 2 || panels.length !== all.length || panels.indexOf(undefined) !== -1) {
      return;
    }

    var starts = panels.map(function (panel) {
      return parseTarget(panel.getAttribute('data-events-start'));
    });
    var ends = panels.map(function (panel) {
      return parseTarget(panel.getAttribute('data-events-end'));
    });
    var timed = !starts.concat(ends).some(isNaN);
    var labelledBy = panels.map(function (panel) {
      return panel.getAttribute('aria-labelledby');
    });
    var primary = panels.map(function (panel) {
      return panel.id;
    }).indexOf(widget.getAttribute('data-events-primary'));

    var current = -1;
    var chosen = false;   /* гость выбрал вкладку сам — пересчёт её не меняет */
    var timer = 0;

    function select(index, focus) {
      current = index;
      tabs.forEach(function (tab, i) {
        var on = i === index;
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      if (focus) {
        tabs[index].focus();
      }
    }

    /* Ставит пометки на момент now и возвращает индекс непрошедшего
       события, ближайшего по времени (-1 — прошли все). */
    function mark(now) {
      var next = -1;
      panels.forEach(function (panel, i) {
        var state = now >= ends[i] ? 'past' : now >= starts[i] ? 'now' : '';
        markTime(tabs[i], state);
        markTime(panel, state);
        if (state !== 'past' && next === -1) {
          next = i;
        }
      });
      return next;
    }

    /* Выбор вкладки по умолчанию — здесь и только здесь; заодно обновляет
       пометки. */
    function defaultTab(now) {
      if (!timed) {
        return Math.max(primary, 0);
      }
      var next = mark(now);
      if (primary !== -1 && now < ends[primary]) {
        return primary;
      }
      return next === -1 ? panels.length - 1 : next;
    }

    /* Следующий пересчёт — на ближайшей границе; в фоновой вкладке цепочка
       не крутится (её перезапускает visibilitychange). */
    function schedule(now, soon) {
      clearTimeout(timer);
      timer = 0;
      if (document.hidden) {
        return;
      }
      var wait = soon ? EVENTS_MODAL_RETRY_MS : EVENTS_RECHECK_MS;
      starts.concat(ends).forEach(function (moment) {
        if (moment > now && moment - now < wait) {
          wait = moment - now;
        }
      });
      timer = setTimeout(onTime, wait + EVENTS_EDGE_MS);
    }

    function refresh() {
      var now = Date.now();
      var target = defaultTab(now);
      var pending = !chosen && target !== current;
      var modal = pending && modalOpen();
      if (pending && !modal && !widget.contains(document.activeElement)) {
        select(target, false);
      }
      schedule(now, modal);
    }

    /* Вид «без скрипта»: список вкладок скрыт, все карточки видны, роли и
       пометки сняты, исходные связи возвращены. */
    function restore() {
      clearTimeout(timer);
      timer = 0;
      tablist.hidden = true;
      tablist.style.removeProperty('--events-count');
      if (!tablist.getAttribute('style')) {
        tablist.removeAttribute('style');
      }
      panels.forEach(function (panel, i) {
        panel.hidden = false;
        panel.removeAttribute('role');
        panel.removeAttribute('tabindex');
        if (labelledBy[i]) {
          panel.setAttribute('aria-labelledby', labelledBy[i]);
        } else {
          panel.removeAttribute('aria-labelledby');
        }
        markTime(tabs[i], '');
        markTime(panel, '');
      });
      tablist.removeEventListener('keydown', onKeyDown);
      tablist.removeEventListener('click', onClick);
      widget.removeEventListener('focusout', onFocusOut);
      document.removeEventListener('visibilitychange', onTime);
      window.removeEventListener('pageshow', onTime);
    }

    function guarded(handler) {
      return function (event) {
        try {
          handler(event);
        } catch (error) {
          restore();
          report('events', error);
        }
      };
    }

    var onTime = guarded(function () {
      if (timed) {
        refresh();
      }
    });

    var onClick = guarded(function (event) {
      var tab = event.target && typeof event.target.closest === 'function' ?
        event.target.closest('[data-events-tab]') : null;
      var at = tabs.indexOf(tab);
      if (at !== -1) {
        chosen = true;
        select(at, false);
      }
    });

    var onKeyDown = guarded(function (event) {
      var at = tabs.indexOf(event.target);
      if (at === -1 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
        return;
      }
      var last = tabs.length - 1;
      var step = EVENTS_STEPS[event.key];
      var to = event.key === 'Home' ? 0 : event.key === 'End' ? last :
        typeof step === 'number' ? (at + step + tabs.length) % tabs.length : -1;
      if (to === -1) {
        return;
      }
      event.preventDefault();
      chosen = true;
      select(to, true);
    });

    /* Фокус ушёл из виджета — отложенное переключение можно сделать. */
    var onFocusOut = guarded(function (event) {
      if (timed && !chosen && !widget.contains(event.relatedTarget)) {
        clearTimeout(timer);
        timer = setTimeout(onTime, 0);
      }
    });

    try {
      panels.forEach(function (panel, i) {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('tabindex', '0');
        panel.setAttribute('aria-labelledby', tabs[i].id);
      });
      tablist.style.setProperty('--events-count', String(tabs.length));
      var now = Date.now();
      select(defaultTab(now), false);
      tablist.addEventListener('keydown', onKeyDown);
      tablist.addEventListener('click', onClick);
      if (timed) {
        widget.addEventListener('focusout', onFocusOut);
        document.addEventListener('visibilitychange', onTime);
        window.addEventListener('pageshow', onTime);
        schedule(now, false);
      }
      tablist.hidden = false;
    } catch (error) {
      restore();
      throw error;
    }
  }

  register('events', function (doc) {
    Array.prototype.forEach.call(doc.querySelectorAll('[data-events]'), function (widget) {
      try {
        startEvents(widget);
      } catch (error) {
        report('events', error);
      }
    });
  });


  /* ==========================================================================
     Модуль reveal — появление блоков [data-reveal] при прокрутке
     --------------------------------------------------------------------------
     Скрытое состояние действует только под маркером reveal-on на <html>;
     маркер ставится последним шагом и снимается при любой ошибке. Показанный
     блок получает is-revealed, наблюдение за ним снимается; когда переход
     появления завершён — is-settled: переход и задержка каскада больше не
     действуют и не мешают собственным переходам элемента.
     ========================================================================== */

  /* Доля высоты экрана, на которую блок должен войти, прежде чем появиться.
     0 — появление начинается с первого показанного пикселя: иначе у нижнего
     края экрана остаётся полоса, где блок уже на экране, но пуст. */
  var REVEAL_INSET = 0;
  var REVEAL_MARGIN = '0px 0px -' + (REVEAL_INSET * 100) + '% 0px';
  var REVEAL_STAGGER_MAX = 4;   /* предел каскада задержек в одной группе */
  var REVEAL_SETTLE_MS = 2000;  /* страховка, если transitionend не пришёл */

  register('reveal', function (doc) {
    var pending = Array.prototype.slice.call(doc.querySelectorAll('[data-reveal]'));
    if (!pending.length || typeof window.IntersectionObserver !== 'function' ||
        prefersReducedMotion()) {
      return; /* маркер не ставится — всё видно */
    }

    var observer = null;
    var frame = 0;

    function viewportHeight() {
      return window.innerHeight || root.clientHeight;
    }

    /* Появление завершено: снять переход, задержку каскада и свои слушатели. */
    function settle(element) {
      if (element.classList.contains('is-settled')) {
        return;
      }
      element.classList.add('is-settled');
      element.style.removeProperty('--reveal-index');
      if (!element.getAttribute('style')) {
        element.removeAttribute('style');
      }
    }

    /* Конец появления — только настоящий конец перехода opacity самого блока.
       Chromium во время прокрутки присылает лишний transitionend с
       elapsedTime 0 в первом кадре перехода (перенос времени его старта);
       если принять его, is-settled снимет переход посреди появления, а там,
       где такое снятие обрывает переход, блок «выскочит» скачком. */
    function isRevealEnd(event, element) {
      if (event.target !== element) {
        return false; /* переход вложенного элемента */
      }
      if (event.type === 'transitioncancel') {
        return true;
      }
      return event.propertyName === 'opacity' && event.elapsedTime > 0;
    }

    function settleLater(element) {
      var timer = 0;
      function done(event) {
        if (event && !isRevealEnd(event, element)) {
          return;
        }
        clearTimeout(timer);
        element.removeEventListener('transitionend', done);
        element.removeEventListener('transitioncancel', done);
        settle(element);
      }
      element.addEventListener('transitionend', done);
      element.addEventListener('transitioncancel', done);
      timer = setTimeout(done, REVEAL_SETTLE_MS);
    }

    /* order — место в группе появившихся одновременно; instant — без перехода. */
    function show(element, order, instant) {
      var at = pending.indexOf(element);
      if (at === -1) {
        return;
      }
      pending.splice(at, 1);
      if (observer) {
        observer.unobserve(element);
      }
      if (instant) {
        settle(element);
      } else {
        if (order > 0) {
          element.style.setProperty('--reveal-index', String(Math.min(order, REVEAL_STAGGER_MAX)));
        }
        settleLater(element);
      }
      element.classList.add('is-revealed');
      if (!pending.length) {
        finish();
      }
    }

    function finish() {
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      if (frame && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(frame);
      }
      frame = 0;
      doc.removeEventListener('focusin', onFocusIn);
      window.removeEventListener('scroll', onViewportChange, true);
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('orientationchange', onViewportChange);
      doc.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pageshow', sweep);
    }

    function fail() {
      root.classList.remove('reveal-on');
      pending = [];
      finish();
    }

    /* Оборачивает обработчики событий: после сбоя всё остаётся видимым. */
    function guarded(handler) {
      return function (argument) {
        try {
          handler(argument);
        } catch (error) {
          fail();
          report('reveal', error);
        }
      };
    }

    /* Показывает всё, что уже на экране или выше него (быстрая прокрутка,
       переход к концу страницы, возврат из фоновой вкладки). inset — доля
       высоты экрана снизу, в которую блок ещё должен войти; без неё — 0. */
    function sweepWithin(inset) {
      var limit = viewportHeight() * (1 - inset);
      var order = 0;
      pending.slice().forEach(function (element) {
        var rect = element.getBoundingClientRect();
        if (rect.bottom <= 0) {
          show(element, 0, true);
        } else if (rect.top < limit) {
          show(element, order, false);
          order += 1;
        }
      });
    }

    var sweep = guarded(function () {
      sweepWithin(0);
    });

    /* Фокус с клавиатуры внутри ещё скрытого блока — показать немедленно. */
    var onFocusIn = guarded(function (event) {
      var node = event.target;
      while (node && node.nodeType === 1) {
        if (node.hasAttribute('data-reveal')) {
          show(node, 0, true);
        }
        node = node.parentElement;
      }
    });

    /* Прокрутка, смена размера или ориентации: проверка не зависит от того,
       пришёл ли колбэк наблюдателя, и идёт не чаще кадра. Граница та же, что у
       наблюдателя; у самого конца страницы блок может до неё не дойти — там
       она снимается. */
    /* Конец страницы — у body, если прокручивает он (страница приглашения
       на сенсорном экране, app.css секция 3), иначе у документа. Документ
       тогда размером с окно и «в конце» всегда — его не спрашивать. */
    function atEnd() {
      var body = doc.body;
      if (window.getComputedStyle(body).overflowY !== 'visible') {
        return body.scrollTop + body.clientHeight >= body.scrollHeight - 2;
      }
      return viewportHeight() + window.pageYOffset >= root.scrollHeight - 2;
    }

    var onFrame = guarded(function () {
      frame = 0;
      sweepWithin(atEnd() ? 0 : REVEAL_INSET);
    });

    function onViewportChange() {
      if (frame) {
        return;
      }
      if (typeof window.requestAnimationFrame === 'function') {
        frame = window.requestAnimationFrame(onFrame);
      } else {
        onFrame();
      }
    }

    var onVisibilityChange = guarded(function () {
      if (!doc.hidden) {
        sweep();
      }
    });

    var onIntersect = guarded(function (entries) {
      var order = 0;
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          show(entry.target, order, false);
          order += 1;
        }
      });
      /* Блоки, которые прокрутка «перепрыгнула», пересечения не дают. */
      var passed = pending.filter(function (element) {
        return element.getBoundingClientRect().bottom <= 0;
      });
      passed.forEach(function (element) {
        show(element, 0, true);
      });
    });

    try {
      /* Блоки, видимые при загрузке, помечаются до включения скрытого
         состояния: они не скрываются вовсе, поэтому не «мигают». */
      var limit = viewportHeight();
      pending.slice().forEach(function (element) {
        if (element.getBoundingClientRect().top < limit) {
          show(element, 0, true);
        }
      });
      if (!pending.length) {
        return;
      }

      observer = new window.IntersectionObserver(onIntersect, {
        rootMargin: REVEAL_MARGIN,
        threshold: 0
      });
      pending.forEach(function (element) {
        observer.observe(element);
      });
      doc.addEventListener('focusin', onFocusIn);
      /* Перехват: прокрутка body (страница приглашения на сенсорном экране)
         до window не всплывает. */
      window.addEventListener('scroll', onViewportChange, { passive: true, capture: true });
      window.addEventListener('resize', onViewportChange);
      window.addEventListener('orientationchange', onViewportChange);
      doc.addEventListener('visibilitychange', onVisibilityChange);
      window.addEventListener('pageshow', sweep);

      root.classList.add('reveal-on');
    } catch (error) {
      fail();
      throw error;
    }
  });


  /* ==========================================================================
     Модуль countdown — таймер обратного отсчёта [data-countdown="<dateISO>"]
     --------------------------------------------------------------------------
     Контейнер в разметке скрыт (hidden); рядом всегда остаётся текстовая дата.
     Некорректная дата или наступившее событие — контейнер остаётся скрытым.
     ========================================================================== */

  var ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?(Z|[+-]\d{2}:\d{2})$/;

  var COUNTDOWN_UNITS = [
    { name: 'days', seconds: 86400, pad: false, forms: ['день', 'дня', 'дней'] },
    { name: 'hours', seconds: 3600, pad: true, forms: ['час', 'часа', 'часов'] },
    { name: 'minutes', seconds: 60, pad: true, forms: ['минута', 'минуты', 'минут'] },
    { name: 'seconds', seconds: 1, pad: true, forms: ['секунда', 'секунды', 'секунд'] }
  ];

  /* Русская словоформа по числу: forms = [один, два–четыре, много]. */
  function pluralRu(count, forms) {
    var n = Math.abs(count) % 100;
    var last = n % 10;
    if (n >= 11 && n <= 14) {
      return forms[2];
    }
    if (last === 1) {
      return forms[0];
    }
    if (last >= 2 && last <= 4) {
      return forms[1];
    }
    return forms[2];
  }

  function parseTarget(value) {
    var text = (value || '').trim();
    if (!ISO_WITH_OFFSET.test(text)) {
      return NaN;
    }
    return Date.parse(text);
  }

  function setText(node, text) {
    if (node && node.textContent !== text) {
      node.textContent = text;
    }
  }

  function startCountdown(container) {
    var target = parseTarget(container.getAttribute('data-countdown'));
    if (isNaN(target)) {
      return;
    }

    var cells = COUNTDOWN_UNITS.map(function (unit) {
      var cell = container.querySelector('[data-countdown-unit="' + unit.name + '"]');
      return {
        unit: unit,
        value: cell && cell.querySelector('[data-countdown-value]'),
        label: cell && cell.querySelector('[data-countdown-label]')
      };
    });
    if (cells.some(function (cell) { return !cell.value; })) {
      return;
    }

    var timer = 0;
    var over = false;

    function stop() {
      if (timer) {
        clearTimeout(timer);
        timer = 0;
      }
    }

    /* Таймер больше не нужен: остаётся текстовая дата. */
    function finish() {
      stop();
      over = true;
      container.hidden = true;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pageshow', tick);
    }

    function update() {
      stop();
      var left = target - Date.now();
      if (left <= 0) {
        /* Событие наступило: нули не показываются. */
        finish();
        return;
      }

      /* Округление вверх: последняя секунда — «1», затем таймер скрывается. */
      var rest = Math.ceil(left / 1000);
      cells.forEach(function (cell) {
        var amount = Math.floor(rest / cell.unit.seconds);
        rest -= amount * cell.unit.seconds;
        var text = String(amount);
        setText(cell.value, cell.unit.pad && amount < 10 ? '0' + text : text);
        setText(cell.label, pluralRu(amount, cell.unit.forms));
      });
      container.hidden = false;

      /* Следующее обновление — на границе секунды, отсчёт всегда от Date.now():
         ошибка не накапливается. В фоновой вкладке цепочка не крутится. */
      if (!document.hidden) {
        timer = setTimeout(tick, (left % 1000) || 1000);
      }
    }

    /* Вызывается и из таймера, и из событий — вне try/catch реестра: при
       любой ошибке отсчёт останавливается, контейнер скрывается. */
    function tick() {
      try {
        update();
      } catch (error) {
        finish();
        report('countdown', error);
      }
    }

    function onVisibilityChange() {
      if (over) {
        return;
      }
      if (document.hidden) {
        stop();
      } else {
        tick();
      }
    }

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pageshow', tick);
    tick();
  }

  /* Таймеров на странице может быть несколько (виджет даты в разных
     секциях): сбой одного не мешает остальным. */
  register('countdown', function (doc) {
    Array.prototype.forEach.call(doc.querySelectorAll('[data-countdown]'), function (container) {
      try {
        startCountdown(container);
      } catch (error) {
        container.hidden = true;
        report('countdown', error);
      }
    });
  });


  /* ==========================================================================
     Модуль gallery — окно просмотра фото и видео для ссылок a[data-lightbox]
     --------------------------------------------------------------------------
     Ссылка-плитка ведёт на файл и без скрипта открывает его как обычно:
     картинку браузер показывает, ролик играет своим плеером. Ссылки внутри
     одного [data-gallery] листаются как группа; ссылка вне [data-gallery] —
     одиночный элемент без стрелок и счётчика.

     Ролик помечен на плитке: data-media="video" (href — файл ролика),
     data-media-poster — постер, data-media-ratio — пропорция кадра «w / h»
     до загрузки метаданных.

     Окно — нативный <dialog>, открытый через showModal(): верхний слой,
     фокус внутри окна и закрытие по Esc (и кнопкой «Назад» на Android) даёт
     браузер; история не меняется. Нет showModal — модуль не включается.
     Окно создаётся при первом открытии; при любой ошибке модуль отключается,
     а переход по ссылке не отменяется.

     Ролик играет в одном на всё окно <video controls> с нативной панелью
     браузера: одновременно звучит не больше одного ролика. Запуск — play()
     синхронно в обработчике нажатия на плитку (жест пользователя: без него
     iOS и встроенные браузеры не дают звука). К ролику, до которого
     долистали, файл не запрашивается и сам он не стартует; при уходе с него
     и при закрытии окна — пауза и обрыв загрузки.
     ========================================================================== */

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var LIGHTBOX_ICONS = {
    close: 'M6 6 18 18M18 6 6 18',
    prev: 'M15 5 8 12 15 19',
    next: 'M9 5 16 12 9 19'
  };
  var LIGHTBOX_TEXT = {
    photos: 'Просмотр фотографий',
    mixed: 'Просмотр фото и видео',
    image: 'Просмотр изображения',
    video: 'Просмотр видео',
    close: 'Закрыть',
    prev: 'Предыдущая фотография',
    next: 'Следующая фотография',
    prevItem: 'Назад',
    nextItem: 'Вперёд',
    itemImage: 'Фотография',
    itemVideo: 'Видео',
    loading: 'Загрузка…',
    error: 'Не удалось загрузить изображение.',
    videoError: 'Не удалось загрузить видео.'
  };
  var SWIPE_MIN = 48;         /* px по горизонтали, чтобы жест стал листанием */
  var SWIPE_RATIO = 1.5;      /* горизонталь должна преобладать над вертикалью */
  var SWIPE_CLICK_MS = 400;   /* клик сразу после жеста окно не закрывает */
  var LOADING_NOTE_MS = 300;  /* «Загрузка…» — только если файл не из кэша */
  var ZOOMED_SCALE = 1.01;    /* страница увеличена щипком */
  var HAVE_NOTHING = 0;       /* readyState ролика, о котором ещё ничего не загружено */
  /* Пропорция кадра из разметки: «w / h», целые больше нуля. */
  var RATIO_RE = /^\s*[1-9]\d*\s*\/\s*[1-9]\d*\s*$/;

  function createElement(tag, className) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    return node;
  }

  function createIconButton(kind) {
    var button = createElement('button', 'lightbox__button lightbox__button--' + kind);
    button.type = 'button';
    button.setAttribute('aria-label', LIGHTBOX_TEXT[kind]);
    var icon = document.createElementNS(SVG_NS, 'svg');
    icon.setAttribute('class', 'lightbox__icon');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    icon.setAttribute('focusable', 'false');
    var path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', LIGHTBOX_ICONS[kind]);
    icon.appendChild(path);
    button.appendChild(icon);
    return button;
  }

  /* Лента галереи: ссылка, получившая фокус, показывается целиком. Частично
     видимый элемент браузер сам не доводит; двигается только лента. */
  function revealInRibbon(event) {
    var link = event.target;
    var ribbon = link && typeof link.closest === 'function' &&
      link.hasAttribute('data-lightbox') ? link.closest('[data-gallery]') : null;
    if (!ribbon) {
      return;
    }
    var frame = ribbon.getBoundingClientRect();
    var box = (link.parentElement || link).getBoundingClientRect();
    if (box.left < frame.left) {
      ribbon.scrollLeft -= frame.left - box.left;
    } else if (box.right > frame.right) {
      ribbon.scrollLeft += box.right - frame.right;
    }
  }

  /* Элемент ленты по ссылке-плитке. label — подпись элемента в окне: alt
     картинки плитки, у ролика — доступное имя самой плитки. */
  function readItem(link) {
    var thumb = link.getElementsByTagName('img')[0];
    var alt = thumb ? thumb.alt : '';
    if (link.getAttribute('data-media') !== 'video') {
      return { href: link.getAttribute('href'), video: false, label: alt };
    }
    var ratio = link.getAttribute('data-media-ratio') || '';
    return {
      href: link.getAttribute('href'),
      video: true,
      poster: link.getAttribute('data-media-poster') || '',
      ratio: RATIO_RE.test(ratio) ? ratio.trim() : '',
      label: link.getAttribute('aria-label') || alt || LIGHTBOX_TEXT.itemVideo
    };
  }

  register('gallery', function (doc) {
    var links = doc.querySelectorAll('a[data-lightbox]');
    if (!links.length) {
      return;
    }
    doc.addEventListener('focusin', revealInRibbon);

    var Dialog = window.HTMLDialogElement;
    if (typeof Dialog !== 'function' || typeof Dialog.prototype.showModal !== 'function') {
      return; /* ссылки остаются обычными ссылками на файлы */
    }
    /* Отсюда ссылка открывает окно — это объявляется экранному диктору. Без
       скрипта окна нет, нет и атрибута. */
    Array.prototype.forEach.call(links, function (link) {
      link.setAttribute('aria-haspopup', 'dialog');
    });

    var ui = null;      /* элементы окна; создаются при первом открытии */
    var items = [];     /* readItem() каждой ссылки открытой группы */
    var index = 0;
    var opener = null;  /* ссылка, которой вернётся фокус */
    var loadingTimer = 0;
    var preloaded = [];
    var touches = 0;
    var swipe = null;   /* { id, x, y } — начало жеста одним пальцем */
    var swipedAt = 0;

    function lockScroll(on) {
      if (on) {
        /* Место исчезнувшей полосы прокрутки занимает отступ: страница под
           окном не сдвигается. Полоса — у окна или у body, если прокручивает
           он (страница приглашения на сенсорном экране, app.css секция 3);
           рамки у body нет. */
        var body = doc.body;
        var gap = window.innerWidth - root.clientWidth +
          (body ? body.offsetWidth - body.clientWidth : 0);
        if (gap > 0) {
          root.style.setProperty('--lightbox-scroll-gap', gap + 'px');
        }
        root.classList.add('is-lightbox-open');
      } else {
        root.classList.remove('is-lightbox-open');
        root.style.removeProperty('--lightbox-scroll-gap');
        if (!root.getAttribute('style')) {
          root.removeAttribute('style');
        }
      }
    }

    /* Ролик в окне больше не нужен: остановить и оборвать загрузку. Без
       источника load() сбрасывает элемент, и браузер закрывает соединение. */
    function releaseVideo() {
      var video = ui && ui.video;
      if (!video || !video.getAttribute('src')) {
        return;
      }
      try {
        video.pause();
      } catch (ignored) { /* уже остановлен */ }
      video.removeAttribute('src');
      video.removeAttribute('poster');
      try {
        video.load();
      } catch (ignored) { /* источника нет — сбрасывать нечего */ }
    }

    function fail(error) {
      doc.removeEventListener('click', onLinkClick);
      if (ui) {
        doc.removeEventListener('keydown', ui.onKey);
      }
      try {
        releaseVideo();
        if (ui && ui.dialog.open) {
          ui.dialog.close();
        }
      } catch (ignored) { /* окно уже недоступно */ }
      lockScroll(false);
      report('gallery', error);
    }

    function guarded(handler) {
      return function (event) {
        try {
          handler(event);
        } catch (error) {
          fail(error);
        }
      };
    }

    function setDisabled(button, disabled) {
      if (disabled) {
        button.setAttribute('aria-disabled', 'true');
      } else {
        button.removeAttribute('aria-disabled');
      }
    }

    /* Соседа подгружает картинка: фото целиком, у ролика — только постер.
       Файл ролика заранее не запрашивается никогда. */
    function preload(at) {
      var item = items[at];
      var src = item && (item.video ? item.poster : item.href);
      if (!src || preloaded.indexOf(src) !== -1) {
        return;
      }
      preloaded.push(src);
      var image = new window.Image();
      image.decoding = 'async';
      image.src = src;
    }

    /* Отказ браузера (нет жеста, политика встроенного браузера) — не ошибка:
       ролик остаётся на постере, запустить его можно с панели. */
    function startVideo() {
      var promise = ui.video.play();
      if (promise && typeof promise.then === 'function') {
        promise.then(null, function () { /* не запустился — ждёт нажатия */ });
      }
    }

    function showVideo(item, autoplay) {
      var video = ui.video;
      ui.image.removeAttribute('src');
      ui.image.alt = '';
      if (item.ratio) {
        video.style.setProperty('--lightbox-ratio', item.ratio);
      } else {
        video.style.removeProperty('--lightbox-ratio');
      }
      video.setAttribute('aria-label', item.label);
      if (item.poster) {
        video.setAttribute('poster', item.poster);
      }
      video.setAttribute('src', item.href);
      if (autoplay) {
        startVideo();
      }
      preload(index + 1);
      preload(index - 1);
    }

    function showImage(item) {
      ui.dialog.classList.add('is-loading');
      ui.image.alt = item.label;
      ui.image.setAttribute('src', item.href);
      if (ui.image.complete && ui.image.naturalWidth > 0) {
        onImageLoad();
      } else {
        loadingTimer = setTimeout(function () {
          if (ui.dialog.classList.contains('is-loading')) {
            setText(ui.status, LIGHTBOX_TEXT.loading);
          }
        }, LOADING_NOTE_MS);
      }
    }

    /* autoplay — только когда show() вызван из обработчика нажатия на плитку. */
    function show(at, autoplay) {
      index = Math.max(0, Math.min(at, items.length - 1));
      var item = items[index];
      clearTimeout(loadingTimer);
      releaseVideo();
      ui.dialog.classList.remove('is-error', 'is-loading');
      ui.dialog.classList.toggle('is-video', item.video);
      setText(ui.status, '');
      setText(ui.count, (index + 1) + ' / ' + items.length);
      setText(ui.live, (item.video ? LIGHTBOX_TEXT.itemVideo : LIGHTBOX_TEXT.itemImage) +
        ' ' + (index + 1) + ' из ' + items.length);
      setDisabled(ui.prev, index === 0);
      setDisabled(ui.next, index === items.length - 1);
      if (item.video) {
        showVideo(item, autoplay);
      } else {
        showImage(item);
      }
    }

    function step(delta) {
      var at = index + delta;
      if (items.length > 1 && at >= 0 && at < items.length) {
        show(at, false);
      }
    }

    function onImageLoad() {
      if (!ui.image.getAttribute('src')) {
        return; /* показан ролик */
      }
      clearTimeout(loadingTimer);
      ui.dialog.classList.remove('is-loading');
      setText(ui.status, '');
      preload(index + 1);
      preload(index - 1);
    }

    function onImageError() {
      if (!ui.image.getAttribute('src')) {
        return;
      }
      clearTimeout(loadingTimer);
      ui.dialog.classList.remove('is-loading');
      ui.dialog.classList.add('is-error');
      setText(ui.status, LIGHTBOX_TEXT.error);
    }

    function onVideoError() {
      if (!ui.video.getAttribute('src')) {
        return; /* источник сняли сами (releaseVideo) */
      }
      ui.dialog.classList.add('is-error');
      setText(ui.status, LIGHTBOX_TEXT.videoError);
    }

    /* Большая кнопка ▶ по центру кадра и щелчок по кадру в Chrome ничего не
       делают, пока о ролике ничего не загружено (preload="none"); кнопка ▶
       панели и пробел работают. Запуск по нажатию на кадр — только в этом
       состоянии: дальше щелчок по кадру обрабатывает сам браузер (пауза и
       продолжение), и двойного переключения не бывает. Это тоже жест
       пользователя, звук разрешён. */
    function onVideoClick() {
      var video = ui.video;
      if (video.paused && video.readyState === HAVE_NOTHING && video.getAttribute('src')) {
        startVideo();
      }
    }

    function isZoomed() {
      return !!window.visualViewport && window.visualViewport.scale > ZOOMED_SCALE;
    }

    /* Пока страница увеличена щипком, жесты принадлежат браузеру: иначе
       увеличенное изображение нельзя было бы сдвинуть. */
    var onZoom = guarded(function () {
      ui.dialog.classList.toggle('is-zoomed', isZoomed());
    });

    /* Клавиши слушает документ, пока окно открыто, а не само окно: выйдя из
       полноэкранного режима, Chrome через секунду-другую переводит фокус на
       <body>, и окно перестало бы получать клавиши. Нажатия на самом ролике
       (фокус на нём или в его панели) принадлежат плееру: пробел — пауза,
       стрелки и Home/End — перемотка, Tab — ход по кнопкам панели. */
    function onKeyDown(event) {
      if (!ui.dialog.open || event.target === ui.video ||
          event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      var key = event.key;
      if (key === 'Tab') {
        /* Круг по элементам окна — и там, где браузер выпускает фокус в свою
           панель. Ролик — одна остановка: дальше Tab ведёт браузер. */
        var stops = [ui.close, ui.video, ui.prev, ui.next].filter(function (node) {
          return node === ui.video ? ui.dialog.classList.contains('is-video') &&
            !ui.dialog.classList.contains('is-error') : !node.hidden;
        });
        var at = stops.indexOf(doc.activeElement);
        var last = stops.length - 1;
        if (at === -1 || (event.shiftKey ? at === 0 : at === last)) {
          event.preventDefault();
          stops[event.shiftKey ? last : 0].focus();
        }
        return;
      }
      if (event.shiftKey || items.length < 2) {
        return;
      }
      if (key === 'ArrowLeft' || key === 'ArrowRight') {
        step(key === 'ArrowLeft' ? -1 : 1);
      } else if (key === 'Home' || key === 'End') {
        show(key === 'Home' ? 0 : items.length - 1, false);
      } else {
        return;
      }
      event.preventDefault();
    }

    function onPointerDown(event) {
      if (event.pointerType === 'mouse') {
        return;
      }
      touches += 1;
      /* Жест на кадре ролика — это перемотка по дорожке нативной панели
         (события из неё приходят от самого <video>), а не листание. */
      swipe = touches === 1 && event.target !== ui.video ?
        { id: event.pointerId, x: event.clientX, y: event.clientY } : null;
    }

    function onPointerEnd(event) {
      if (event.pointerType === 'mouse') {
        return;
      }
      touches = Math.max(0, touches - 1);
      /* Палец подняли уже после закрытия (Esc во время жеста): листать нечего. */
      if (!ui.dialog.open) {
        swipe = null;
        return;
      }
      var from = swipe;
      if (!from || from.id !== event.pointerId) {
        return;
      }
      swipe = null;
      var dx = event.clientX - from.x;
      var dy = event.clientY - from.y;
      /* Вертикальный жест и жест на увеличенной странице — не листание. */
      if (event.type !== 'pointerup' || isZoomed() ||
          Math.abs(dx) < SWIPE_MIN || Math.abs(dx) < Math.abs(dy) * SWIPE_RATIO) {
        return;
      }
      swipedAt = Date.now();
      step(dx < 0 ? 1 : -1);
    }

    /* Клик мимо изображения и кнопок — по подложке. */
    function onDialogClick(event) {
      var target = event.target;
      if (Date.now() - swipedAt > SWIPE_CLICK_MS &&
          (target === ui.dialog || target === ui.bar || target === ui.stage)) {
        ui.dialog.close();
      }
    }

    function onClose() {
      doc.removeEventListener('keydown', ui.onKey);
      clearTimeout(loadingTimer);
      releaseVideo();
      lockScroll(false);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', onZoom);
      }
      ui.dialog.classList.remove('is-zoomed');
      var link = opener;
      opener = null;
      /* Плитку могли скрыть, пока окно было открыто (например, другой модуль
         переключил её панель). Тогда фокус не возвращается в скрытый элемент,
         но и в закрытом окне не остаётся. */
      if (link && typeof link.focus === 'function' && link.getClientRects().length) {
        link.focus({ preventScroll: true });
      } else if (ui.dialog.contains(doc.activeElement)) {
        doc.activeElement.blur();
      }
    }

    function build() {
      var dialog = createElement('dialog', 'lightbox on-overlay');
      var bar = createElement('div', 'lightbox__bar');
      var counter = createElement('p', 'lightbox__counter');
      var count = createElement('span');
      var live = createElement('span', 'visually-hidden');
      var close = createIconButton('close');
      var stage = createElement('div', 'lightbox__stage');
      var image = createElement('img', 'lightbox__image');
      var video = createElement('video', 'lightbox__video');
      var status = createElement('p', 'lightbox__status');
      var prev = createIconButton('prev');
      var next = createIconButton('next');

      count.setAttribute('aria-hidden', 'true');
      live.setAttribute('aria-live', 'polite');
      status.setAttribute('role', 'status');
      image.alt = '';
      image.decoding = 'async';
      image.draggable = false;
      /* Нативная панель; на iPhone без playsinline ролик сразу ушёл бы в
         системный полноэкранный плеер. preload="none": к ролику, до которого
         долистали, до нажатия не уходит ни байта. */
      video.setAttribute('controls', '');
      video.setAttribute('playsinline', '');
      video.setAttribute('preload', 'none');
      video.setAttribute('disablepictureinpicture', '');

      counter.appendChild(count);
      counter.appendChild(live);
      bar.appendChild(counter);
      bar.appendChild(close);
      stage.appendChild(image);
      stage.appendChild(video);
      stage.appendChild(status);
      stage.appendChild(prev);
      stage.appendChild(next);
      dialog.appendChild(bar);
      dialog.appendChild(stage);

      image.addEventListener('load', guarded(onImageLoad));
      image.addEventListener('error', guarded(onImageError));
      video.addEventListener('error', guarded(onVideoError));
      video.addEventListener('click', guarded(onVideoClick));
      close.addEventListener('click', guarded(function () {
        dialog.close();
      }));
      prev.addEventListener('click', guarded(function () {
        step(-1);
      }));
      next.addEventListener('click', guarded(function () {
        step(1);
      }));
      dialog.addEventListener('click', guarded(onDialogClick));
      dialog.addEventListener('pointerdown', guarded(onPointerDown));
      dialog.addEventListener('pointerup', guarded(onPointerEnd));
      dialog.addEventListener('pointercancel', guarded(onPointerEnd));
      dialog.addEventListener('close', guarded(onClose));

      doc.body.appendChild(dialog);
      return {
        dialog: dialog, bar: bar, counter: counter, count: count, live: live,
        close: close, stage: stage, image: image, video: video, status: status,
        prev: prev, next: next, onKey: guarded(onKeyDown)
      };
    }

    /* Подпись окна по составу группы. */
    function dialogLabel() {
      var videos = items.filter(function (item) {
        return item.video;
      }).length;
      if (videos === items.length) {
        return LIGHTBOX_TEXT.video;
      }
      if (items.length < 2) {
        return LIGHTBOX_TEXT.image;
      }
      return LIGHTBOX_TEXT[videos ? 'mixed' : 'photos'];
    }

    function open(link) {
      var scope = link.closest('[data-gallery]');
      var group = scope ?
        Array.prototype.slice.call(scope.querySelectorAll('a[data-lightbox]')) : [link];
      if (!ui) {
        ui = build();
      }
      items = group.map(readItem);
      preloaded = [];
      touches = 0;
      swipe = null;
      opener = link;

      var single = items.length < 2;
      var photos = !items.some(function (item) {
        return item.video;
      });
      ui.counter.hidden = single;
      ui.prev.hidden = single;
      ui.next.hidden = single;
      ui.prev.setAttribute('aria-label', LIGHTBOX_TEXT[photos ? 'prev' : 'prevItem']);
      ui.next.setAttribute('aria-label', LIGHTBOX_TEXT[photos ? 'next' : 'nextItem']);
      ui.dialog.setAttribute('aria-label', dialogLabel());

      lockScroll(true);
      ui.dialog.showModal();
      doc.addEventListener('keydown', ui.onKey);
      /* Всё ещё внутри обработчика нажатия на плитку: play() в show() получает
         жест пользователя. Счётчик меняется уже в открытом окне — экранный
         диктор его объявит. */
      show(group.indexOf(link), true);
      /* У ролика фокус — на нём самом: пробел ставит его на паузу, а не
         нажимает «Закрыть». */
      (items[index].video ? ui.video : ui.close).focus();
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', onZoom);
      }
      /* Страница могла быть увеличена щипком ещё до открытия: события resize
         не будет, состояние снимается сразу. */
      onZoom();
    }

    function onLinkClick(event) {
      if (event.defaultPrevented || event.button !== 0 || event.altKey ||
          event.ctrlKey || event.metaKey || event.shiftKey ||
          !event.target || typeof event.target.closest !== 'function') {
        return; /* открытие в новой вкладке и т.п. — как у обычной ссылки */
      }
      var link = event.target.closest('a[data-lightbox]');
      if (!link || !link.getAttribute('href') || (ui && ui.dialog.open)) {
        return;
      }
      try {
        open(link);
        event.preventDefault();
      } catch (error) {
        fail(error); /* переход по ссылке не отменён: файл откроется как обычно */
      }
    }

    doc.addEventListener('click', onLinkClick);
  });


  /* ==========================================================================
     Место для следующих модулей
     --------------------------------------------------------------------------
     Каждый модуль — отдельный блок ниже этой черты, внутри этой же функции:

       register('имя', function (doc) {
         // найти свои элементы по data-атрибутам; нет элементов — выйти
       });

     Модуль не полагается на другие модули и на порядок их запуска; состояние
     для стилей отдаёт классами is-* и CSS-переменными через style.setProperty.
     ========================================================================== */

}());
