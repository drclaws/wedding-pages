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
        /* Только имя модуля и тип ошибки — без данных страницы. */
        console.warn('app: module "' + module.name + '" failed (' +
          (error && error.name ? error.name : 'Error') + ')');
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
     блок получает is-revealed, наблюдение за ним снимается.
     ========================================================================== */

  var REVEAL_MARGIN = '0px 0px -8% 0px'; /* блок должен немного войти в экран */
  var REVEAL_STAGGER_MAX = 4;            /* предел каскада задержек в одной группе */

  register('reveal', function (doc) {
    var pending = Array.prototype.slice.call(doc.querySelectorAll('[data-reveal]'));
    if (!pending.length || typeof window.IntersectionObserver !== 'function' ||
        prefersReducedMotion()) {
      return; /* маркер не ставится — всё видно */
    }

    var observer = null;

    function viewportHeight() {
      return window.innerHeight || root.clientHeight;
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
        element.classList.add('is-instant');
      } else if (order > 0) {
        element.style.setProperty('--reveal-index', String(Math.min(order, REVEAL_STAGGER_MAX)));
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
      doc.removeEventListener('focusin', onFocusIn);
      window.removeEventListener('scroll', onScroll);
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
          console.warn('app: module "reveal" failed (' +
            (error && error.name ? error.name : 'Error') + ')');
        }
      };
    }

    /* Показывает всё, что уже на экране или выше него (быстрая прокрутка,
       переход к концу страницы, возврат из фоновой вкладки). */
    var sweep = guarded(function () {
      var limit = viewportHeight();
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

    /* У самого конца страницы блок может не дойти до границы срабатывания. */
    var onScroll = guarded(function () {
      if (window.innerHeight + window.pageYOffset >= root.scrollHeight - 2) {
        sweep();
      }
    });

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
      window.addEventListener('scroll', onScroll, { passive: true });
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

    function tick() {
      stop();
      var left = target - Date.now();
      if (left <= 0) {
        /* Событие наступило: остаётся текстовая дата, нули не показываются. */
        over = true;
        container.hidden = true;
        document.removeEventListener('visibilitychange', onVisibilityChange);
        window.removeEventListener('pageshow', tick);
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
