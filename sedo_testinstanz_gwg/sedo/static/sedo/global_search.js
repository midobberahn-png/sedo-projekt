/* Globale Suche (v2.1): Kunden, Rechnungen, Angebote, Objekte.
 *
 * Sitzt in der Sidebar, fragt /api/suche/ mit Debounce ab und zeigt
 * gruppierte Treffer. Tastatur: „/" fokussiert das Feld (außerhalb von
 * Eingabefeldern), Pfeile navigieren, Enter öffnet, Escape schließt.
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

    function close() {
      box.hidden = true;
      box.innerHTML = '';
      activeIndex = -1;
    }

    function items() {
      return Array.prototype.slice.call(box.querySelectorAll('.gs-item'));
    }

    function highlight() {
      items().forEach(function (el, i) { el.classList.toggle('active', i === activeIndex); });
      var el = items()[activeIndex];
      if (el) el.scrollIntoView({ block: 'nearest' });
    }

    function render(results) {
      box.innerHTML = '';
      activeIndex = -1;
      if (!results.length) {
        var empty = document.createElement('div');
        empty.className = 'gs-empty';
        empty.textContent = 'Keine Treffer.';
        box.appendChild(empty);
        box.hidden = false;
        return;
      }
      var lastGroup = null;
      results.forEach(function (r) {
        if (r.gruppe !== lastGroup) {
          lastGroup = r.gruppe;
          var g = document.createElement('div');
          g.className = 'gs-group';
          g.textContent = r.gruppe;
          box.appendChild(g);
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
      box.hidden = false;
    }

    function search(q) {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch('/api/suche/?q=' + encodeURIComponent(q), { signal: controller.signal })
        .then(function (resp) { return resp.ok ? resp.json() : { results: [] }; })
        .then(function (data) { render(data.results || []); })
        .catch(function (err) { if (err.name !== 'AbortError') close(); });
    }

    input.addEventListener('input', function () {
      var q = this.value.trim();
      clearTimeout(timer);
      if (q.length < 2) { close(); return; }
      timer = setTimeout(function () { search(q); }, 220);
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

    input.addEventListener('blur', function () { setTimeout(close, 150); });

    /* „/" fokussiert die Suche – aber nie, wenn gerade getippt wird. */
    document.addEventListener('keydown', function (e) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      var t = document.activeElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      e.preventDefault();
      input.focus();
      input.select();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
