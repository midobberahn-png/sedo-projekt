/* Globale Suche: native Links, Tastaturfokus und gegen veraltete Antworten geschützt. */
(function () {
  'use strict';
  function init() {
    const input = document.getElementById('global-search');
    const box = document.getElementById('global-search-results');
    const status = document.getElementById('search-status');
    if (!input || !box) return;
    const root = input.closest('.global-search');
    input.setAttribute('aria-controls', box.id);
    let timer, controller, generation = 0;
    const actions = [
      ['Neue Rechnung', input.dataset.actionInvoice, 'Rechnung erstellen'],
      ['Neues Angebot', input.dataset.actionOffer, 'Angebot erstellen'],
      ['Neuen Kunden anlegen', input.dataset.actionCustomer, 'Stammdaten'],
      ['Offene Posten öffnen', input.dataset.actionOpenItems, 'Finanzen']
    ].filter(a => a[1]);
    const items = () => [...box.querySelectorAll('a')];
    function cancel() {
      clearTimeout(timer);
      generation++;
      if (controller) controller.abort();
    }
    function close() {
      cancel();
      box.hidden = true;
      box.replaceChildren();
      if (status) status.textContent = '';
    }
    function group(label) {
      const node = document.createElement('div');
      node.className = 'gs-group';
      node.textContent = label;
      box.appendChild(node);
    }
    function link(title, url, sub, badge, action) {
      const node = document.createElement('a');
      node.href = url;
      node.className = action ? 'gs-action' : 'gs-item';
      const heading = document.createElement('div');
      heading.className = 'gs-title';
      const name = document.createElement('span');
      name.textContent = title;
      heading.appendChild(name);
      if (badge) {
        const state = document.createElement('span');
        state.className = 'gs-badge';
        state.textContent = badge;
        heading.appendChild(state);
      }
      node.appendChild(heading);
      if (sub) {
        const detail = document.createElement('div');
        detail.className = 'gs-sub';
        detail.textContent = sub;
        node.appendChild(detail);
      }
      box.appendChild(node);
    }
    function render(results, query, error) {
      box.replaceChildren();
      const matches = actions.filter(a => !query || (a[0] + ' ' + a[2]).toLowerCase().includes(query.toLowerCase()));
      if (matches.length) group('Schnellaktionen');
      matches.forEach(a => link(a[0], a[1], a[2], '', true));
      let lastGroup;
      results.forEach(r => {
        if (lastGroup !== r.gruppe) { group(r.gruppe); lastGroup = r.gruppe; }
        link(r.titel, r.url, r.sub, r.badge, false);
      });
      if (error || (!results.length && !matches.length)) {
        const message = document.createElement('div');
        message.className = 'gs-empty';
        message.textContent = error ? 'Suche nicht verfügbar. Bitte erneut eingeben.' : 'Keine Treffer.';
        box.appendChild(message);
      }
      box.hidden = false;
      if (status) status.textContent = error ? 'Suche nicht verfügbar.' : items().length + ' Treffer und Schnellaktionen.';
    }
    function request() {
      cancel();
      const q = input.value.trim();
      if (q.length < 2) { render([], q); return; }
      box.hidden = true;
      if (status) status.textContent = 'Suche läuft …';
      const current = generation;
      timer = setTimeout(() => {
        controller = new AbortController();
        fetch('/api/suche/?q=' + encodeURIComponent(q), {signal: controller.signal})
          .then(response => { if (!response.ok) throw new Error('Suche fehlgeschlagen'); return response.json(); })
          .then(data => { if (current === generation) render(data.results || [], q); })
          .catch(error => { if (current === generation && error.name !== 'AbortError') render([], q, true); });
      }, 180);
    }
    input.addEventListener('focus', request);
    input.addEventListener('input', request);
    root.addEventListener('focusout', () => {
      setTimeout(() => { if (!root.contains(document.activeElement)) close(); }, 0);
    });
    root.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); input.focus(); close(); return; }
      if (box.hidden) return;
      const list = items();
      if (!list.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const index = list.indexOf(document.activeElement);
        const next = e.key === 'ArrowDown' ? (index + 1) % list.length : (index < 0 ? list.length - 1 : (index - 1 + list.length) % list.length);
        list[next].focus();
      } else if (e.key === 'Enter' && e.target === input) {
        e.preventDefault();
        list[0].click();
      }
    });
    document.addEventListener('click', e => { if (!root.contains(e.target)) close(); });
    document.addEventListener('keydown', e => {
      const t = document.activeElement;
      const typing = t && (['INPUT','TEXTAREA','SELECT'].includes(t.tagName) || t.isContentEditable);
      if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') || (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing)) {
        e.preventDefault();
        input.focus();
        input.select();
        request();
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
