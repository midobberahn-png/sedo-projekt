/* Compact settings and selection tools. Documentation lives outside the UI. */
(() => {
  'use strict';
  document.querySelectorAll('[data-settings-section]').forEach((card, index) => {
    const heading = card.querySelector('h2');
    if (!heading) return;
    const head = heading.closest('.section-head') || heading;
    const panel = document.createElement('details'); panel.className = 'settings-panel';
    const summary = document.createElement('summary');
    const title = heading.cloneNode(true);
    summary.setAttribute('aria-label', title.textContent.trim() + ' einstellen');
    summary.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-cog"/></svg>';
    const body = document.createElement('div'); body.className = 'settings-body';
    body.id = 'settings-form-' + index; summary.setAttribute('aria-controls', body.id);
    [...card.childNodes].filter(node => node !== head).forEach(node => body.append(node));
    card.append(head, panel); panel.append(summary, body);
    const openFromHash = () => { if (location.hash && card.id === location.hash.slice(1)) panel.open = true; };
    openFromHash(); window.addEventListener('hashchange', openFromHash);
    if (body.querySelector('.errorlist, [aria-invalid="true"]')) panel.open = true;
    body.addEventListener('invalid', () => { panel.open = true; }, true);
  });
  document.querySelectorAll('[data-selection-tools]').forEach(bar => {
    const form = bar.closest('form');
    const refresh = () => { bar.hidden = !form.querySelector('input[name="invoice"]:checked'); };
    form.addEventListener('change', refresh); refresh();
  });
})();
