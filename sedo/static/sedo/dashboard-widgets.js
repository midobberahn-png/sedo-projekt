(() => {
  'use strict';
  const menu = document.getElementById('dashboard-customizer');
  if (menu) {
    const closeButton = menu.querySelector('.widget-menu-close');
    closeButton.hidden = false;
    closeButton.addEventListener('click', () => {
      menu.open = false;
      menu.querySelector('summary').focus();
    });
    document.addEventListener('click', event => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) {
        menu.open = false;
        menu.querySelector('summary').focus();
        event.preventDefault();
      }
    });
  }
  document.querySelectorAll('.widget-bank-form').forEach(form => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button');
      button.disabled = true;
      button.textContent = 'Bank wird abgefragt …';
    });
  });
  const card = document.querySelector('[data-widget="weather"]');
  const form = card?.querySelector('.widget-weather-form');
  if (!form) return;
  form.hidden = false;
  const button = form.querySelector('button');
  let retryCount = 0;
  const update = async () => {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = 'Wetter wird geladen …';
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(form.action, {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value},
      });
      if (!response.ok || response.redirected) throw new Error('weather unavailable');
      const data = await response.json();
      card.querySelector('[data-weather-temperature]').textContent = data.state === 'ready' ? `${data.temperature} °C` : '—';
      card.querySelector('[data-weather-description]').textContent = data.description || '';
      card.querySelector('[data-weather-time]').textContent = data.observed_at ? `Stand: ${data.observed_at}` : '';
      card.querySelector('[data-weather-message]').textContent = data.message || '';
      if (data.state === 'loading' && retryCount++ < 2) window.setTimeout(update, 3000);
    } catch (_) {
      card.querySelector('[data-weather-message]').textContent = 'Wetter konnte nicht aktualisiert werden. Bitte später erneut versuchen.';
    } finally {
      window.clearTimeout(timeout);
      button.disabled = false;
      button.textContent = 'Wetter aktualisieren';
    }
  };
  form.addEventListener('submit', event => { event.preventDefault(); retryCount = 0; update(); });
  if (card.dataset.weatherState === 'loading') update();
})();
