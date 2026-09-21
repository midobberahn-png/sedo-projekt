/* Explicitly marked explanations only. Errors, status and confirmations stay visible. */
(() => {
  'use strict';
  document.querySelectorAll('[data-help], .helptext').forEach(text => {
    if (text.closest('.info-tip, noscript')) return;
    const tip = document.createElement('span');
    tip.className = 'info-tip';
    text.before(tip);
    const heading = [...tip.parentElement.children].find(node => /^H[1-4]$/.test(node.tagName));
    const label = [...tip.parentElement.children].find(node => node.tagName === 'LABEL' && node.htmlFor);
    if (heading) heading.append(tip);
    else if (label) { label.after(tip); label.classList.add('has-help'); }
    text.classList.add('info-tip-text');
    tip.append(text);
  });
  let active = null;
  document.querySelectorAll('.info-tip').forEach((tip, index) => {
    const text = tip.querySelector('.info-tip-text');
    if (!text) return;
    const label = tip.getAttribute('aria-label') || 'Hinweis anzeigen';
    [...tip.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).forEach(node => node.remove());
    tip.removeAttribute('tabindex');
    tip.removeAttribute('aria-label');
    tip.removeAttribute('aria-describedby');
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'help-trigger'; button.textContent = 'i';
    button.setAttribute('aria-label', label);
    text.id = text.id || 'sedo-help-' + index;
    button.setAttribute('aria-controls', text.id);
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-describedby', text.id);
    text.setAttribute('role', text.querySelector('a') ? 'note' : 'tooltip');
    text.setAttribute('popover', 'manual');
    text.hidden = true;
    tip.prepend(button);
    let timer;
    const place = () => {
      const rect = button.getBoundingClientRect();
      const width = Math.min(420, window.innerWidth - 24);
      text.style.width = width + 'px';
      text.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
      const height = text.getBoundingClientRect().height;
      text.style.top = Math.max(12, rect.bottom + height + 10 < window.innerHeight ? rect.bottom + 6 : rect.top - height - 6) + 'px';
    };
    const close = () => {
      clearTimeout(timer);
      if (text.matches(':popover-open')) text.hidePopover();
      text.hidden = true; tip.classList.remove('help-open');
      button.setAttribute('aria-expanded', 'false');
      if (active === close) active = null;
    };
    const open = () => {
      clearTimeout(timer);
      if (active && active !== close) active();
      tip.classList.remove('tip-dismissed');
      text.hidden = false; tip.classList.add('help-open');
      if (typeof text.showPopover === 'function' && !text.matches(':popover-open')) text.showPopover();
      button.setAttribute('aria-expanded', 'true');
      active = close; place();
    };
    tip.addEventListener('pointerenter', open);
    text.addEventListener('pointerenter', () => clearTimeout(timer));
    const leave = () => { timer = setTimeout(() => { if (!tip.contains(document.activeElement)) close(); }, 160); };
    tip.addEventListener('pointerleave', leave);
    text.addEventListener('pointerleave', leave);
    button.addEventListener('focus', open);
    // Focus opens the help on first tap; a second tap closes it.
    button.addEventListener('click', event => { if (event.detail === 0) open(); else if (button.dataset.pinned) { delete button.dataset.pinned; close(); } else { button.dataset.pinned = '1'; open(); } });
    tip.addEventListener('focusout', event => { if (!tip.contains(event.relatedTarget)) { delete button.dataset.pinned; close(); } });
    tip.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); delete button.dataset.pinned; close(); } });
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
  });
  document.addEventListener('pointerdown', event => { if (active && !event.target.closest('.info-tip')) active(); });

  document.querySelectorAll('[data-settings-section]').forEach((card, index) => {
    const heading = card.querySelector('h2');
    if (!heading) return;
    const head = heading.closest('.section-head') || heading;
    const panel = document.createElement('details'); panel.className = 'settings-panel';
    const summary = document.createElement('summary');
    const title = heading.cloneNode(true);
    title.querySelectorAll('.info-tip').forEach(node => node.remove());
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
