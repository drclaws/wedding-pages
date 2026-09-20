/* Скрипт витрины (styleguide.html).
 *
 * Читает список токенов прямо из таблицы стилей дизайн-системы
 * (document.styleSheets -> правила :root -> свойства --*) и показывает их
 * вычисленные значения (getComputedStyle), пересчитывая при изменении ширины
 * окна. Ничего не настраивает и в сборку сайта не попадает.
 *
 * Без этого скрипта витрина остаётся работоспособной: компоненты и
 * типографическая шкала — статическая разметка, скриптом строится только
 * таблица токенов.
 */
(function () {
  'use strict';

  // Тот же первый шаг, что и у скрипта страницы: прогрессивное улучшение.
  document.documentElement.classList.add('js');

  var SOURCE = 'app.css'; // токены берём только из дизайн-системы, не из витрины

  var GROUPS = [
    ['Опорные ширины и интерполяция', /^--(vw-|fluid$|bp-)/],
    ['Цвета', /^--color-/],
    ['Шрифты', /^--(font-|line-height-|tracking-)/],
    ['Типографическая шкала', /^--text-/],
    ['Отступы', /^--(space-|flow-)/],
    ['Контейнер', /^--container-/],
    ['Сетка', /^--(grid-|gallery-columns|countdown-columns|countdown-gap|countdown-inline-)/],
    ['Радиусы, тени, линии', /^--(radius-|shadow-|line-width|focus-ring|opacity-)/],
    ['Пропорции изображений и кадр', /^--(ratio-|image-focus)/],
    ['Высота экрана, обложка, предел медиа', /^--(viewport-|cover-height-|cover-min-|media-max-)/],
    ['Движение', /^--(duration-|ease-|reveal-|cover-distance|hover-scale)/],
    ['Доступность и слои', /^--(tap-|z-|safe-inset-)/],
    ['Токены компонентов', /^--(player-|icon-|countdown-cell|schedule-|divider-|card-|btn-)/]
  ];
  var OTHER = 'Прочее';

  var COLOR_RE = /^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i;

  /* getComputedStyle для кастомного свойства возвращает записанное значение
     (clamp/calc целиком), а не длину. Чтобы показать именно вычисленное
     значение, длина измеряется скрытым пробником: свойству width назначается
     var(--токен) и читается уже разрешённая ширина. Единицы, зависящие от шрифта
     конкретного элемента (ch, em), не разрешаются: у пробника свой шрифт, и
     число получилось бы не тем, что в компоненте, — показывается исходная запись.
     Исключение — пороги медиазапросов (--bp-*) в em: там em — размер шрифта
     браузера по умолчанию, и пробник измеряет их при font-size: medium. */
  var LENGTH_RE = /^(calc|clamp|min|max|env)\(|^-?[0-9.]+(px|rem|vw|vh|svh|dvh|lvh|vmin|vmax)$/;
  var MEDIA_EM_RE = /^--bp-/;
  var EM_RE = /^-?[0-9.]+em$/;
  /* Короткая запись в rem/em показывается вместе с пикселями: «48em = 768px». */
  var RELATIVE_RE = /^-?[0-9.]+(rem|em)$/;
  var probe = null;

  function probeElement() {
    if (probe) return probe;
    probe = document.createElement('div');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.position = 'absolute';
    probe.style.insetInlineStart = '0';
    probe.style.insetBlockStart = '0';
    probe.style.blockSize = '0';
    probe.style.visibility = 'hidden';
    probe.style.pointerEvents = 'none';
    document.body.appendChild(probe);
    return probe;
  }

  function resolveLength(name) {
    var node = probeElement();
    node.style.removeProperty('width');
    node.style.setProperty('font-size', MEDIA_EM_RE.test(name) ? 'medium' : 'inherit');
    node.style.setProperty('width', 'var(' + name + ')');
    var resolved = getComputedStyle(node).width;
    node.style.removeProperty('width');
    return /^-?[0-9.]+px$/.test(resolved) ? resolved : null;
  }

  /* Возвращает то, что показать в таблице, и полную запись для подсказки. */
  function readValue(css, name) {
    var raw = css.getPropertyValue(name).trim().replace(/\s+/g, ' ');
    var shown = raw;
    var title = '';
    if (LENGTH_RE.test(raw) || (MEDIA_EM_RE.test(name) && EM_RE.test(raw))) {
      var resolved = resolveLength(name);
      if (resolved && resolved !== raw) {
        shown = RELATIVE_RE.test(raw) ? raw + ' = ' + resolved : resolved;
        title = raw;
      }
    }
    return { raw: raw, shown: shown, title: title };
  }

  function groupOf(name) {
    for (var i = 0; i < GROUPS.length; i++) {
      if (GROUPS[i][1].test(name)) return GROUPS[i][0];
    }
    return OTHER;
  }

  /* Собирает имена кастомных свойств из всех правил :root нужной таблицы. */
  function collectNames() {
    var names = [];
    var seen = Object.create(null);

    function walk(rules) {
      for (var i = 0; i < rules.length; i++) {
        var rule = rules[i];
        if (rule.style && rule.selectorText &&
            /(^|,)\s*:root\s*$/.test(rule.selectorText)) {
          for (var j = 0; j < rule.style.length; j++) {
            var prop = rule.style[j];
            if (prop.indexOf('--') === 0 && !seen[prop]) {
              seen[prop] = true;
              names.push(prop);
            }
          }
        }
        // @media, @supports и вложенные правила. Проверять надо после
        // rule.style: у обычного правила cssRules тоже есть (вложенный CSS),
        // просто пустой.
        if (rule.cssRules && rule.cssRules.length) walk(rule.cssRules);
      }
    }

    var sheets = document.styleSheets;
    for (var s = 0; s < sheets.length; s++) {
      var sheet = sheets[s];
      if (!sheet.href || sheet.href.indexOf(SOURCE) === -1) continue;
      var rules;
      try {
        rules = sheet.cssRules;
      } catch (err) {
        continue; // на всякий случай: чужой источник
      }
      if (rules) walk(rules);
    }
    return names;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function build(container, names) {
    var css = getComputedStyle(document.documentElement);
    var known = Object.create(null);
    names.forEach(function (n) { known[n] = true; });

    var order = [];
    var buckets = Object.create(null);
    names.forEach(function (name) {
      var group = groupOf(name);
      if (!buckets[group]) {
        buckets[group] = [];
        order.push(group);
      }
      buckets[group].push(name);
    });

    var rows = [];
    order.forEach(function (group) {
      container.appendChild(el('h3', 'sg-tokens__group', group));
      var list = el('div', 'sg-tokens');
      buckets[group].forEach(function (name) {
        var row = el('div', 'sg-token');
        var value = readValue(css, name);

        var swatch = null;
        if (COLOR_RE.test(value.raw)) {
          swatch = el('span', 'sg-token__swatch');
          swatch.setAttribute('aria-hidden', 'true');
          swatch.style.backgroundColor = value.raw;
          row.appendChild(swatch);
        }

        row.appendChild(el('code', 'sg-token__name', name));
        var valueNode = el('code', 'sg-token__value', value.shown);
        if (value.title) valueNode.title = value.title;
        row.appendChild(valueNode);

        var badge = null;
        if (/-(mobile|desktop)$/.test(name)) {
          badge = 'опорное значение';
        } else if (known[name + '-mobile'] && known[name + '-desktop']) {
          badge = 'mobile/desktop';
        }
        if (badge) row.appendChild(el('span', 'sg-token__badge', badge));

        list.appendChild(row);
        rows.push({ name: name, valueNode: valueNode, swatch: swatch });
      });
      container.appendChild(list);
    });
    return rows;
  }

  function refresh(rows, meta) {
    var css = getComputedStyle(document.documentElement);
    rows.forEach(function (row) {
      var value = readValue(css, row.name);
      if (row.valueNode.textContent !== value.shown) {
        row.valueNode.textContent = value.shown;
      }
      if (value.title) row.valueNode.title = value.title;
      if (row.swatch) row.swatch.style.backgroundColor = value.raw;
    });
    if (meta) {
      var width = document.documentElement.clientWidth;
      meta.textContent = '';
      meta.appendChild(el('span', null, 'ширина области просмотра: ' + width + ' px'));
      // Граница раскладки читается из токена --bp-layout, а не зашита в скрипт.
      var bp = css.getPropertyValue('--bp-layout').trim();
      var desktop = bp ? window.matchMedia('(min-width: ' + bp + ')').matches : false;
      meta.appendChild(el('span', null, 'раскладка: ' + (desktop ? 'десктоп' : 'мобильная') +
        (bp ? ' (граница ' + bp + ')' : '')));
      meta.appendChild(el('span', null, 'токенов: ' + rows.length));
    }
  }

  /* Переключатель «Проверка масштаба шрифта»: ставит на <html> атрибут, по
     которому таблица стилей витрины меняет размер шрифта корневого элемента. */
  function initFontScale(onChange) {
    var group = document.querySelector('[data-font-scale]');
    if (!group) return;
    var buttons = group.querySelectorAll('[data-font-scale-value]');
    group.addEventListener('click', function (event) {
      var button = event.target.closest('[data-font-scale-value]');
      if (!button) return;
      var scale = button.getAttribute('data-font-scale-value');
      if (scale === '100') {
        document.documentElement.removeAttribute('data-sg-font-scale');
      } else {
        document.documentElement.setAttribute('data-sg-font-scale', scale);
      }
      for (var i = 0; i < buttons.length; i++) {
        var pressed = buttons[i] === button;
        buttons[i].setAttribute('aria-pressed', pressed ? 'true' : 'false');
        buttons[i].classList.toggle('btn--primary', pressed);
        buttons[i].classList.toggle('btn--secondary', !pressed);
      }
      onChange();
    });
  }

  function init() {
    var container = document.querySelector('[data-token-table]');
    if (!container) return;
    var meta = document.querySelector('[data-token-meta]');
    var names = collectNames();
    if (!names.length) {
      container.appendChild(el('p', 'sg-note',
        'Не удалось прочитать токены из таблицы стилей. Откройте страницу ' +
        'через локальный HTTP-сервер, а не как file://.'));
      return;
    }
    var rows = build(container, names);
    refresh(rows, meta);

    initFontScale(function () { refresh(rows, meta); });

    var pending = false;
    window.addEventListener('resize', function () {
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(function () {
        pending = false;
        refresh(rows, meta);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
