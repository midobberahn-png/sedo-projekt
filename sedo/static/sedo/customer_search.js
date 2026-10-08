/* Kundenschnellsuche mit echter Trefferliste (Angebot + Rechnung).
 *
 * Vorher wurden nur <option>-Einträge im Dropdown versteckt – beim Tippen
 * erschien nichts, und der Fokus auf dem Dropdown setzte den Filter zurück.
 * Jetzt: Autocomplete-Liste unter dem Suchfeld; ein Klick (oder Enter)
 * wählt den Kunden im echten <select> aus und löst dessen change-Event aus,
 * sodass die bestehende Autobefüllung unverändert funktioniert.
 */
(function () {
  'use strict';

  function init() {
    var input = document.getElementById('customer-search');
    var select = document.getElementById('id_customer');
    if (!input || !select) return;

    var wrap = input.closest('.customer-search-wrap') || input.parentElement;
    var list = document.createElement('div');
    list.className = 'customer-search-results';
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    wrap.appendChild(list);

    var activeIndex = -1;

    function options() {
      return Array.prototype.filter.call(select.options, function (o) { return o.value; });
    }

    function close() {
      list.hidden = true;
      list.innerHTML = '';
      activeIndex = -1;
    }

    function choose(value, label) {
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      input.value = label;
      close();
    }

    function render(matches) {
      list.innerHTML = '';
      activeIndex = -1;
      if (!matches.length) {
        var empty = document.createElement('div');
        empty.className = 'customer-search-empty';
        empty.textContent = 'Kein Kunde gefunden – Kopfdaten einfach direkt ausfüllen.';
        list.appendChild(empty);
      } else {
        matches.slice(0, 8).forEach(function (opt) {
          var item = document.createElement('button');
          item.type = 'button';
          item.className = 'customer-search-item';
          item.setAttribute('role', 'option');
          item.textContent = opt.text;
          item.addEventListener('mousedown', function (e) {
            e.preventDefault(); // Blur des Suchfelds nicht vor dem Klick auslösen
            choose(opt.value, opt.text);
          });
          list.appendChild(item);
        });
      }
      list.hidden = false;
    }

    function currentItems() {
      return Array.prototype.slice.call(list.querySelectorAll('.customer-search-item'));
    }

    function highlight(items) {
      items.forEach(function (el, i) { el.classList.toggle('active', i === activeIndex); });
      if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: 'nearest' });
    }

    input.addEventListener('input', function () {
      var q = this.value.toLowerCase().trim();
      if (!q) { close(); return; }
      var matches = options().filter(function (o) {
        return o.text.toLowerCase().indexOf(q) !== -1;
      });
      render(matches);
    });

    input.addEventListener('keydown', function (e) {
      var items = currentItems();
      if (list.hidden || !items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % items.length;
        highlight(items);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = (activeIndex - 1 + items.length) % items.length;
        highlight(items);
      } else if (e.key === 'Enter') {
        // Enter übernimmt den markierten (oder einzigen) Treffer,
        // statt dass der Assistent zum nächsten Schritt springt.
        e.preventDefault();
        e.stopPropagation();
        var pick = items[activeIndex] || (items.length === 1 ? items[0] : null);
        if (pick) pick.dispatchEvent(new MouseEvent('mousedown'));
      } else if (e.key === 'Escape') {
        close();
      }
    }, true);

    input.addEventListener('blur', function () {
      setTimeout(close, 150); // Klick auf einen Treffer noch zulassen
    });

    // Auswahl aufgehoben (×-Knopf) → Suchfeld leeren
    var clearBtn = document.getElementById('clear-customer');
    if (clearBtn) clearBtn.addEventListener('click', function () { input.value = ''; close(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
