/* Globale Suche / Command Center (UX-02)
 *
 * Kunden, Rechnungen, Angebote und Objekte über /api/suche/.
 * Zusätzlich lokale Schnellaktionen aus data-action-* Attributen.
 * Tastatur: / sowie Cmd/Ctrl+K fokussieren, Pfeile navigieren,
 * Enter öffnet, Escape schließt.
 */
(function () {
  'use strict';

  function init() {
    var input = document.getElementById('global-search');
    var box = document.getElementById('global-search-results');
    if (!input || !box) return;

    var timer = null;
    var activeIndex = -1;
    var controller = null;

    function configuredActions() {
      var actions = [
        ['Neue Rechnung', input.dataset.actionInvoice, 'Rechnung erstellen'],
        ['Neues Angebot', input.dataset.actionOffer, 'Angebot erstellen'],
        ['Neuen Kunden anlegen', input.dataset.actionCustomer, 'Stammdaten'],
        ['Offene Posten öffnen', input.dataset.actionOpenItems, 'Finanzen']
      ];
      return actions.filter(function (a) { return !!a[1]; });
    }

    function close() {
      box.hidden = true;
      box.innerHTML = '';
      activeIndex = -1;
    }

    function items() {
      return Array.prototype.slice.call(box.querySelectorAll('.gs-item,.gs-action'));
    }

    function highlight() {
      items().forEach(function (el, i) { el.classList.toggle('active', i === activeIndex); });
      var el = items()[activeIndex];
      if (el) el.scrollIntoView({ block: 'nearest' });
    }

    function group(label) {
      var g = document.createElement('div');
      g.className = 'gs-group';
      g.textContent = label;
      box.appendChild(g);
    }

    function actionButton(title, url, sub) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'gs-action';
      item.dataset.url = url;
      var titleEl = document.createElement('div');
      titleEl.className = 'gs-title';
      titleEl.textContent = title;
      item.appendChild(titleEl);
      if (sub) {
        var subEl = document.createElement('div');
        subEl.className = 'gs-sub';
        subEl.textContent = sub;
        item.appendChild(subEl);
      }
      item.addEventListener('mousedown', function (e) {
        e.preventDefault();
        window.location.href = url;
      });
      return item;
    }

    function renderActions(filter) {
      var q = (filter || '').toLowerCase();
      var actions = configuredActions().filter(function (a) {
        return !q || (a[0] + ' ' + a[2]).toLowerCase().indexOf(q) !== -1;
      });
      if (!actions.length) return 0;
      group('Schnellaktionen');
      actions.forEach(function (a) { box.appendChild(actionButton(a[0], a[1], a[2])); });
      return actions.length;
    }

    function render(results, query) {
      box.innerHTML = '';
      activeIndex = -1;
      var actionCount = renderActions(query);
      if (results.length) group('Treffer');

      var lastGroup = null;
      results.forEach(function (r) {
        if (r.gruppe !== lastGroup) {
          lastGroup = r.gruppe;
          group(r.gruppe);
        }
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'gs-item';
        item.dataset.url = r.url;
        var title = document.createElement('div');
        title.className = 'gs-title';
        var name = document.createElement('span');
        name.textContent = r.titel;
        title.appendChild(name);
        if (r.badge) {
          var badge = document.createElement('span');
          badge.className = 'gs-badge';
          badge.textContent = r.badge;
          title.appendChild(badge);
        }
        item.appendChild(title);
        if (r.sub) {
          var sub = document.createElement('div');
          sub.className = 'gs-sub';
          sub.textContent = r.sub;
          item.appendChild(sub);
        }
        item.addEventListener('mousedown', function (e) {
          e.preventDefault();
          window.location.href = r.url;
        });
        box.appendChild(item);
      });

      if (!results.length && !actionCount) {
        var empty = document.createElement('div');
        empty.className = 'gs-empty';
        empty.textContent = 'Keine Treffer.';
        box.appendChild(empty);
      }
      box.hidden = false;
    }

    function showHome() {
      box.innerHTML = '';
      activeIndex = -1;
      renderActions('');
      box.hidden = false;
    }

    function search(q) {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch('/api/suche/?q=' + encodeURIComponent(q), { signal: controller.signal })
        .then(function (resp) { return resp.ok ? resp.json() : { results: [] }; })
        .then(function (data) { render(data.results || [], q); })
        .catch(function (err) { if (err.name !== 'AbortError') render([], q); });
    }

    input.addEventListener('focus', function () {
      if (!input.value.trim()) showHome();
    });

    input.addEventListener('input', function () {
      var q = this.value.trim();
      clearTimeout(timer);
      if (!q) { showHome(); return; }
      if (q.length < 2) { render([], q); return; }
      timer = setTimeout(function () { search(q); }, 180);
    });

    input.addEventListener('keydown', function (e) {
      var list = items();
      if (e.key === 'Escape') { close(); input.blur(); return; }
      if (box.hidden || !list.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % list.length;
        highlight();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = (activeIndex - 1 + list.length) % list.length;
        highlight();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        var pick = list[activeIndex] || list[0];
        if (pick) window.location.href = pick.dataset.url;
      }
    });

    input.addEventListener('blur', function () { setTimeout(close, 160); });

    function focusSearch() {
      input.focus();
      input.select();
      if (!input.value.trim()) showHome();
    }

    document.addEventListener('keydown', function (e) {
      var t = document.activeElement;
      var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        focusSearch();
        return;
      }
      if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing) {
        e.preventDefault();
        focusSearch();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
