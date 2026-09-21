(() => {
  'use strict';
  document.querySelectorAll('[data-spam-mode], [data-spam-rule]').forEach(form => {
    const select = form.querySelector('select');
    const confirm = form.querySelector('[data-spam-confirm]');
    const refresh = () => {
      confirm.hidden = select.value !== (form.hasAttribute('data-spam-mode') ? 'auto' : 'domain');
    };
    select.addEventListener('change', refresh);
    refresh();
  });
  const selection = document.querySelector('[data-mail-selection]');
  if (selection) {
    const update = () => {
      const count = document.querySelectorAll('input[form="spam-selection"]:checked').length;
      selection.hidden = count === 0;
      selection.querySelector('[data-mail-selection-count]').textContent = `${count} ausgewählt`;
    };
    document.addEventListener('change', update);
    update();
  }
  document.querySelectorAll('[data-mail-confirm]').forEach(button => {
    button.addEventListener('click', event => {
      if (!window.confirm(button.dataset.mailConfirm)) event.preventDefault();
    });
  });
  const compose = document.querySelector('[data-mail-compose]');
  if (compose) {
    let dirty = false;
    compose.addEventListener('input', () => { dirty = true; });
    compose.addEventListener('change', () => { dirty = true; });
    compose.addEventListener('submit', () => { dirty = false; });
    window.addEventListener('beforeunload', event => {
      if (dirty) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  const notice = document.querySelector('[data-mail-status]');
  if (notice) {
    let previous = null;
    let busy = false;
    const poll = async () => {
      if (document.hidden || busy) return;
      busy = true;
      try {
        const response = await fetch(notice.dataset.mailStatus, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('status');
        const state = await response.json();
        const active = state.accounts.filter(account => account.running);
        notice.hidden = !active.length;
        notice.textContent = active.length ? `E-Mails werden abgerufen · ${active.reduce((n, a) => n + a.loaded, 0)}/${active.reduce((n, a) => n + a.total, 0)} Nachrichten geladen` : '';
        state.accounts.forEach(account => {
          const label = document.querySelector(`[data-account="${Number(account.id)}"]`);
          if (label) label.textContent = account.running ? `${account.loaded}/${account.total} geladen` : account.last_sync ? `Abgerufen ${new Date(account.last_sync).toLocaleString('de-DE', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})}` : '';
          const error = document.querySelector(`[data-mail-error="${Number(account.id)}"]`);
          if (error) { error.textContent = account.last_error; error.hidden = !account.last_error; }
        });
        const key = JSON.stringify([state.count, state.accounts.map(account => account.last_sync)]);
        const list = document.querySelector('.mail-message-list');
        const interacting = () => document.querySelector('input[form="spam-selection"]:checked') || list?.contains(document.activeElement);
        if (previous !== null && key !== previous && list && !interacting()) {
          // Replace only the list: never discard a composed message, selection or reader state.
          const page = await fetch(location.href, {cache:'no-store'});
          if (!page.ok) throw new Error('list');
          const parsed = new DOMParser().parseFromString(await page.text(), 'text/html');
          const fresh = parsed.querySelector('.mail-message-list');
          if (!fresh) throw new Error('list');
          // Selection or keyboard focus may have changed while the request was in flight.
          if (document.hidden || !list.isConnected || interacting()) return;
          const scroll = list.scrollTop;
          list.replaceChildren(...fresh.childNodes);
          list.scrollTop = scroll;
          const folders = document.querySelector('.mail-folders');
          const freshFolders = parsed.querySelector('.mail-folders');
          if (folders && freshFolders && !folders.contains(document.activeElement)) {
            const offset = folders.scrollTop;
            folders.replaceChildren(...freshFolders.childNodes);
            folders.scrollTop = offset;
          }
          previous = key;
        } else if (previous === null || !list) previous = key;
      } catch (_) {
        notice.hidden = false;
        notice.textContent = 'Der Abrufstatus ist gerade nicht erreichbar.';
      } finally { busy = false; }
    };
    poll();
    window.setInterval(poll, 2500);
    document.addEventListener('visibilitychange', poll);
  }
})();
