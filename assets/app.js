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
     Модуль reveal — появление блоков [data-reveal] при прокрутке
     --------------------------------------------------------------------------
     Скрытое состояние действует только под маркером reveal-on на <html>;
     маркер ставится последним шагом и снимается при любой ошибке. Показанный
     блок получает is-revealed, наблюдение за ним снимается; когда переход
     появления завершён — is-settled: переход и задержка каскада больше не
     действуют и не мешают собственным переходам элемента.
     ========================================================================== */

  var REVEAL_INSET = 0.08;      /* блок должен войти в экран на эту долю высоты */
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

    function settleLater(element) {
      var timer = 0;
      function done(event) {
        if (event && event.target !== element) {
          return; /* переход вложенного элемента */
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
      window.removeEventListener('scroll', onViewportChange);
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
    var onFrame = guarded(function () {
      frame = 0;
      var atEnd = viewportHeight() + window.pageYOffset >= root.scrollHeight - 2;
      sweepWithin(atEnd ? 0 : REVEAL_INSET);
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
      window.addEventListener('scroll', onViewportChange, { passive: true });
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

  register('countdown', function (doc) {
    Array.prototype.forEach.call(doc.querySelectorAll('[data-countdown]'), function (container) {
      startCountdown(container);
    });
  });


  /* ==========================================================================
     Модуль gallery — лайтбокс для ссылок a[data-lightbox]
     --------------------------------------------------------------------------
     Ссылка ведёт на файл изображения и без скрипта открывает его как обычно.
     Ссылки внутри одного [data-gallery] листаются как группа; ссылка вне
     [data-gallery] — одиночное изображение без стрелок и счётчика.

     Окно — нативный <dialog>, открытый через showModal(): верхний слой,
     фокус внутри окна и закрытие по Esc (и кнопкой «Назад» на Android) даёт
     браузер; история не меняется. Нет showModal — модуль не включается.
     Окно создаётся при первом открытии; при любой ошибке модуль отключается,
     а переход по ссылке не отменяется.
     ========================================================================== */

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var LIGHTBOX_ICONS = {
    close: 'M6 6 18 18M18 6 6 18',
    prev: 'M15 5 8 12 15 19',
    next: 'M9 5 16 12 9 19'
  };
  var LIGHTBOX_TEXT = {
    gallery: 'Просмотр фотографий',
    single: 'Просмотр изображения',
    close: 'Закрыть',
    prev: 'Предыдущая фотография',
    next: 'Следующая фотография',
    loading: 'Загрузка…',
    error: 'Не удалось загрузить изображение.'
  };
  var SWIPE_MIN = 48;         /* px по горизонтали, чтобы жест стал листанием */
  var SWIPE_RATIO = 1.5;      /* горизонталь должна преобладать над вертикалью */
  var SWIPE_CLICK_MS = 400;   /* клик сразу после жеста окно не закрывает */
  var LOADING_NOTE_MS = 300;  /* «Загрузка…» — только если файл не из кэша */
  var ZOOMED_SCALE = 1.01;    /* страница увеличена щипком */

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

  register('gallery', function (doc) {
    if (!doc.querySelector('a[data-lightbox]')) {
      return;
    }
    doc.addEventListener('focusin', revealInRibbon);

    var Dialog = window.HTMLDialogElement;
    if (typeof Dialog !== 'function' || typeof Dialog.prototype.showModal !== 'function') {
      return; /* ссылки остаются обычными ссылками на файлы */
    }

    var ui = null;      /* элементы окна; создаются при первом открытии */
    var items = [];     /* [{ href, alt }] открытой группы */
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
           окном не сдвигается. */
        var gap = window.innerWidth - root.clientWidth;
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

    function fail(error) {
      doc.removeEventListener('click', onLinkClick);
      try {
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

    function preload(at) {
      if (at < 0 || at >= items.length || preloaded.indexOf(items[at].href) !== -1) {
        return;
      }
      preloaded.push(items[at].href);
      var image = new window.Image();
      image.decoding = 'async';
      image.src = items[at].href;
    }

    function show(at) {
      index = Math.max(0, Math.min(at, items.length - 1));
      var item = items[index];
      clearTimeout(loadingTimer);
      ui.dialog.classList.remove('is-error');
      ui.dialog.classList.add('is-loading');
      setText(ui.status, '');
      ui.image.alt = item.alt;
      ui.image.setAttribute('src', item.href);
      setText(ui.count, (index + 1) + ' / ' + items.length);
      setText(ui.live, 'Фотография ' + (index + 1) + ' из ' + items.length);
      setDisabled(ui.prev, index === 0);
      setDisabled(ui.next, index === items.length - 1);
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

    function step(delta) {
      var at = index + delta;
      if (items.length > 1 && at >= 0 && at < items.length) {
        show(at);
      }
    }

    function onImageLoad() {
      clearTimeout(loadingTimer);
      ui.dialog.classList.remove('is-loading');
      setText(ui.status, '');
      preload(index + 1);
      preload(index - 1);
    }

    function onImageError() {
      clearTimeout(loadingTimer);
      ui.dialog.classList.remove('is-loading');
      ui.dialog.classList.add('is-error');
      setText(ui.status, LIGHTBOX_TEXT.error);
    }

    function isZoomed() {
      return !!window.visualViewport && window.visualViewport.scale > ZOOMED_SCALE;
    }

    /* Пока страница увеличена щипком, жесты принадлежат браузеру: иначе
       увеличенное изображение нельзя было бы сдвинуть. */
    var onZoom = guarded(function () {
      ui.dialog.classList.toggle('is-zoomed', isZoomed());
    });

    function onKeyDown(event) {
      if (event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      var key = event.key;
      if (key === 'Tab') {
        /* Круг по кнопкам окна — и там, где браузер выпускает фокус в свою
           панель. */
        var stops = [ui.close, ui.prev, ui.next].filter(function (button) {
          return !button.hidden;
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
        show(key === 'Home' ? 0 : items.length - 1);
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
      swipe = touches === 1 ?
        { id: event.pointerId, x: event.clientX, y: event.clientY } : null;
    }

    function onPointerEnd(event) {
      if (event.pointerType === 'mouse') {
        return;
      }
      touches = Math.max(0, touches - 1);
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
      clearTimeout(loadingTimer);
      lockScroll(false);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', onZoom);
      }
      ui.dialog.classList.remove('is-zoomed');
      var link = opener;
      opener = null;
      if (link && typeof link.focus === 'function') {
        link.focus({ preventScroll: true });
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
      var status = createElement('p', 'lightbox__status');
      var prev = createIconButton('prev');
      var next = createIconButton('next');

      count.setAttribute('aria-hidden', 'true');
      live.setAttribute('aria-live', 'polite');
      status.setAttribute('role', 'status');
      image.alt = '';
      image.decoding = 'async';
      image.draggable = false;

      counter.appendChild(count);
      counter.appendChild(live);
      bar.appendChild(counter);
      bar.appendChild(close);
      stage.appendChild(image);
      stage.appendChild(status);
      stage.appendChild(prev);
      stage.appendChild(next);
      dialog.appendChild(bar);
      dialog.appendChild(stage);

      image.addEventListener('load', guarded(onImageLoad));
      image.addEventListener('error', guarded(onImageError));
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
      dialog.addEventListener('keydown', guarded(onKeyDown));
      dialog.addEventListener('pointerdown', guarded(onPointerDown));
      dialog.addEventListener('pointerup', guarded(onPointerEnd));
      dialog.addEventListener('pointercancel', guarded(onPointerEnd));
      dialog.addEventListener('close', guarded(onClose));

      doc.body.appendChild(dialog);
      return {
        dialog: dialog, bar: bar, counter: counter, count: count, live: live,
        close: close, stage: stage, image: image, status: status, prev: prev, next: next
      };
    }

    function open(link) {
      var scope = link.closest('[data-gallery]');
      var links = scope ?
        Array.prototype.slice.call(scope.querySelectorAll('a[data-lightbox]')) : [link];
      if (!ui) {
        ui = build();
      }
      items = links.map(function (node) {
        var thumb = node.getElementsByTagName('img')[0];
        return { href: node.getAttribute('href'), alt: thumb ? thumb.alt : '' };
      });
      preloaded = [];
      touches = 0;
      swipe = null;
      opener = link;

      var single = items.length < 2;
      ui.counter.hidden = single;
      ui.prev.hidden = single;
      ui.next.hidden = single;
      ui.dialog.setAttribute('aria-label', LIGHTBOX_TEXT[single ? 'single' : 'gallery']);

      lockScroll(true);
      ui.dialog.showModal();
      /* Счётчик меняется уже в открытом окне — экранный диктор его объявит. */
      show(links.indexOf(link));
      ui.close.focus();
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', onZoom);
      }
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
