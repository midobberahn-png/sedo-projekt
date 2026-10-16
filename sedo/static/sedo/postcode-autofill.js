/* Local German postcode lookup for company, customer, object and document forms. */
(function () {
  'use strict';
  const endpoint = document.currentScript && document.currentScript.dataset.endpoint;
  if (!endpoint) return;

  function bind(prefix) {
    const postcode = document.getElementById('id_' + prefix + 'postcode');
    const city = document.getElementById('id_' + prefix + 'city');
    const country = document.getElementById('id_' + prefix + 'country');
    if (!postcode || !city || !country) return;

    const status = document.createElement('small');
    status.className = 'postcode-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    postcode.insertAdjacentElement('afterend', status);

    const choices = document.createElement('select');
    choices.className = 'form-select form-select-sm postcode-choices';
    choices.setAttribute('aria-label', 'Ort zur Postleitzahl auswählen');
    choices.hidden = true;
    city.insertAdjacentElement('afterend', choices);
    let serial = 0;
    let suggested = '';

    function clearSuggestions() {
      choices.replaceChildren();
      choices.hidden = true;
      status.textContent = '';
      status.classList.remove('postcode-warning');
    }

    async function lookup() {
      const current = ++serial;
      const code = postcode.value.trim();
      const region = country.value.trim().toUpperCase();
      clearSuggestions();
      if (region !== 'DE' && region !== 'DEUTSCHLAND') return;
      if (!/^[0-9]{5}$/.test(code)) {
        if (code.length >= 5) {
          status.textContent = 'Deutsche PLZ: bitte genau fünf Ziffern eingeben.';
          status.classList.add('postcode-warning');
        }
        return;
      }
      try {
        const response = await fetch(endpoint + '?postcode=' + encodeURIComponent(code), {
          credentials: 'same-origin', headers: {'Accept': 'application/json'}
        });
        if (!response.ok) throw new Error('lookup failed');
        const cities = (await response.json()).cities;
        if (current !== serial || postcode.value.trim() !== code) return;
        if (!cities.length) {
          status.textContent = 'PLZ im Ortsverzeichnis nicht gefunden. Bitte PLZ und Ort selbst prüfen (Sonder-PLZ möglich).';
          status.classList.add('postcode-warning');
        } else if (cities.length === 1) {
          if (!city.value.trim() || city.value === suggested) {
            city.value = cities[0];
            suggested = cities[0];
            city.dispatchEvent(new Event('change', {bubbles: true}));
          }
          status.textContent = 'Ort zur PLZ: ' + cities[0];
        } else {
          const placeholder = new Option('Ort zur PLZ auswählen (' + cities.length + ' Möglichkeiten)', '');
          choices.add(placeholder);
          for (const name of cities) choices.add(new Option(name, name));
          if (cities.includes(city.value.trim())) choices.value = city.value.trim();
          choices.hidden = false;
          status.textContent = 'Mehrere Orte möglich. Bitte den passenden Ort auswählen.';
        }
      } catch (_) {
        if (current === serial) status.textContent = 'PLZ-Prüfung derzeit nicht verfügbar. Bitte Ort selbst eintragen.';
      }
    }

    postcode.addEventListener('input', function () {
      if (suggested && city.value === suggested) city.value = '';
      suggested = '';
      lookup();
    });
    postcode.addEventListener('blur', function () {
      if (postcode.value && !/^[0-9]{5}$/.test(postcode.value.trim()) &&
          ['DE', 'DEUTSCHLAND'].includes(country.value.trim().toUpperCase())) {
        status.textContent = 'Deutsche PLZ: bitte genau fünf Ziffern eingeben.';
        status.classList.add('postcode-warning');
      }
    });
    country.addEventListener('change', lookup);
    choices.addEventListener('change', function () {
      if (choices.value) {
        city.value = choices.value;
        suggested = choices.value;
        city.dispatchEvent(new Event('change', {bubbles: true}));
      }
    });
    for (const selector of ['id_customer', 'id_obj']) {
      const saved = document.getElementById(selector);
      if (saved) saved.addEventListener('change', function () { setTimeout(lookup, 0); });
    }
    if (postcode.value) lookup();
  }

  bind('');
  bind('object_');
})();
