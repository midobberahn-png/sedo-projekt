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
    const boxes = [...document.querySelectorAll('input[form="spam-selection"]')];
    const update = () => {
      const count = boxes.filter(box => box.checked).length;
      selection.hidden = count === 0;
      selection.querySelector('[data-mail-selection-count]').textContent = `${count} ausgewählt`;
    };
    boxes.forEach(box => box.addEventListener('change', update));
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
    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(notice.dataset.mailStatus, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) return;
        const state = await response.json();
        const key = JSON.stringify(state);
        if (previous !== null && previous !== key) notice.hidden = false;
        previous = key;
      } catch (_) { /* Existing messages remain readable offline. */ }
    };
    poll();
    window.setInterval(poll, 15000);
  }
})();
