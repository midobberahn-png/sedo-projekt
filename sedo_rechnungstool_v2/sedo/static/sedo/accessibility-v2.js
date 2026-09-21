/* UX-09: Navigation, Tabellen und Meldungen; keine Fachaktionen ausführen. */
(function () {
  'use strict';
  const body = document.body;
  const sidebar = document.getElementById('appSidebar');
  const main = document.getElementById('app-main');
  const toggle = document.getElementById('sidebar-toggle');
  const closeButton = document.getElementById('sidebar-close');
  const mobile = window.matchMedia('(max-width: 1050px)');
  let collapsed = true;
  let pointerInside = false;
  let keyboardNavigation = false;
  let opened = false;
  if (sidebar && toggle && main) {
    body.classList.add('shell-enhanced');
    toggle.hidden = false;
    const focusable = () => [...sidebar.querySelectorAll('a,button,summary')].filter(el => !el.disabled && el.getClientRects().length && !el.closest('.nav-sub')?.closest('details:not([open])'));
    function sync() {
      body.classList.toggle('sidebar-locked', mobile.matches ? opened : collapsed);
      toggle.setAttribute('aria-expanded', String(mobile.matches ? opened : !collapsed));
      sidebar.inert = mobile.matches && !opened;
      main.inert = mobile.matches && opened;
      closeButton.hidden = !mobile.matches;
      if (mobile.matches && opened) {
        sidebar.setAttribute('role', 'dialog');
        sidebar.setAttribute('aria-modal', 'true');
        sidebar.setAttribute('aria-label', 'Hauptmenü');
      } else {
        sidebar.removeAttribute('role');
        sidebar.removeAttribute('aria-modal');
        sidebar.removeAttribute('aria-label');
      }
    }
    function close() {
      opened = false;
      sync();
      toggle.focus();
    }
    toggle.addEventListener('click', () => {
      if (mobile.matches) {
        opened = !opened;
        sync();
        if (opened) closeButton.focus();
      } else { collapsed = !collapsed; sync(); }
    });
    closeButton.addEventListener('click', close);
    document.addEventListener('keydown', e => {
      if (!mobile.matches || !opened) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      if (e.key === 'Tab') {
        const nodes = focusable();
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    document.addEventListener('click', e => {
      if (mobile.matches && opened && !sidebar.contains(e.target) && !toggle.contains(e.target)) close();
    });
    mobile.addEventListener('change', () => {
      const wasInside = sidebar.contains(document.activeElement);
      opened = false;
      pointerInside = !mobile.matches && sidebar.matches(':hover');
      collapsed = !pointerInside;
      sync();
      if (wasInside) toggle.focus();
    });
    sidebar.addEventListener('pointerenter', () => {
      if (!mobile.matches) { pointerInside = true; collapsed = false; sync(); }
    });
    sidebar.addEventListener('pointerleave', () => {
      pointerInside = false;
      if (!mobile.matches && !(keyboardNavigation && sidebar.contains(document.activeElement))) { collapsed = true; sync(); }
    });
    document.addEventListener('pointerdown', event => {
      keyboardNavigation = false;
      if (!mobile.matches && !sidebar.contains(event.target) && !toggle.contains(event.target)) { collapsed = true; sync(); }
    });
    document.addEventListener('keydown', event => { if (event.key === 'Tab') keyboardNavigation = true; });
    sidebar.addEventListener('focusin', () => { if (!mobile.matches && keyboardNavigation) { collapsed = false; sync(); } });
    sidebar.addEventListener('focusout', event => {
      if (!mobile.matches && !pointerInside && !sidebar.contains(event.relatedTarget)) { collapsed = true; sync(); }
    });
    pointerInside = !mobile.matches && sidebar.matches(':hover');
    collapsed = !pointerInside;
    sync();
  }

  // Spaltenbezeichnungen bleiben in Kartenansichten sichtbar; Tabellen bleiben semantisch erhalten.
  document.querySelectorAll('table.responsive-records').forEach(table => {
    const header = table.rows[0];
    if (!header || !header.querySelector('th')) return;
    const labels = [...header.cells].map(cell => cell.textContent.trim());
    table.setAttribute('role', 'table');
    table.querySelectorAll('thead,tbody,tfoot').forEach(group => group.setAttribute('role', 'rowgroup'));
    [...table.rows].forEach(row => {
      row.setAttribute('role', 'row');
      [...row.cells].forEach(cell => cell.setAttribute('role', cell.tagName === 'TH' ? 'columnheader' : 'cell'));
    });
    header.classList.add('record-head');
    [...header.cells].forEach(cell => cell.setAttribute('scope', 'col'));
    [...table.rows].slice(1).forEach(row => {
      if (row.cells.length !== labels.length) return;
      [...row.cells].forEach((cell, i) => {
        cell.dataset.label = labels[i];
      });
    });
    table.classList.add('records-ready');
  });

  // Serverseitige Fehler auch bei individuell gerenderten Feldern verknüpfen.
  document.querySelectorAll('.errorlist[id]').forEach(error => {
    const id = error.id.replace(/_error$/, '');
    const field = document.getElementById(id);
    if (!field) return;
    const refs = new Set((field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
    refs.add(error.id);
    field.setAttribute('aria-describedby', [...refs].join(' '));
    field.setAttribute('aria-invalid', 'true');
  });

  // Dynamische Meldungen verwenden ausschließlich Text; keine Zeitbegrenzung.
  window.showToast = function (text, tags) {
    if (!/error|warning/.test(tags || '')) return;
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    const level = /error/.test(tags || '') ? 'error' : /warning/.test(tags || '') ? 'warning' : 'info';
    toast.className = 'sedo-toast toast-visible ' + level;
    toast.setAttribute('role', level === 'error' ? 'alert' : 'status');
    const message = document.createElement('span');
    message.className = 'toast-text';
    message.textContent = String(text);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'toast-close';
    button.setAttribute('aria-label', 'Meldung schließen');
    button.textContent = '×';
    button.addEventListener('click', () => {
      const restore = toast.contains(document.activeElement);
      toast.remove();
      if (restore) (container.querySelector('button') || document.getElementById('main-content')).focus();
    });
    toast.append(message, button);
    container.appendChild(toast);
  };
})();
