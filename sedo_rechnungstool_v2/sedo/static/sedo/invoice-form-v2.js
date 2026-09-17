(function () {
  'use strict';

  /* ── Hilfsfunktionen ─────────────────────────────── */

  /** Feld mit Wert befüllen + blauen Flash-Effekt */
  function fill(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = (val !== null && val !== undefined) ? val : '';
    el.classList.add('autofill-flash', 'filled');
    setTimeout(() => el.classList.remove('filled'), 1400);
  }

  /** Deutschen Geldbetrag formatieren: 1234.56 → "1.234,56 €" */
  function fmtMoney(v) {
    const n = isNaN(v) ? 0 : parseFloat(v);
    return n.toFixed(2)
      .replace('.', ',')
      .replace(/\B(?=(\d{3})+(?!\d))/g, '.') + ' €';
  }

  /** Textinhalt eines Elements setzen */
  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  const formRoot = document.getElementById('invoice-form');
  if (!formRoot) return;
  const customerSel = document.getElementById('id_customer');
  const objSel = document.getElementById('id_obj');
  const customerFields = document.getElementById('customer-fields');
  const objectFields = document.getElementById('object-fields');
  formRoot.classList.add('wizard-enhanced');
  const initial = JSON.parse(document.getElementById('invoice-wizard-initial').textContent);
  const customerNames = ['salutation', 'name', 'name2', 'customer_no', 'customer_type',
    'contact_person', 'phone', 'email', 'street', 'postcode', 'city', 'country'];
  const objectNames = ['name', 'street', 'postcode', 'city', 'country'];
  let selectedCustomer = initial.customer;
  let customerObjectCache = selectedCustomer ? selectedCustomer.objects : [];
  let selectedObject = customerObjectCache.find(o => String(o.id) === initial.object_id) || null;
  let customerRequest = 0;
  let customerLoading = false;
  let customerLoadFailed = false;

  function address(data) {
    return [data.street, [data.postcode, data.city].filter(Boolean).join(' '), data.country].filter(Boolean).join(' · ');
  }

  function setDetails(kind, open) {
    const fields = kind === 'customer' ? customerFields : objectFields;
    fields.hidden = !open;
    const toggle = document.getElementById(kind + '-details-toggle');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'Details ausblenden' : 'Details anzeigen';
  }

  function showSelection(kind, data) {
    const fields = kind === 'customer' ? customerFields : objectFields;
    document.getElementById('section-' + kind).classList.toggle('section-autofilled', !!data);
    // Fehler bleiben sichtbar und bearbeitbar; gebundene Werte nicht überschreiben.
    const hasErrors = !!fields.querySelector('.errorlist');
    fields.querySelectorAll('input, select').forEach(el => {
      if (el.tagName === 'SELECT') el.disabled = !!data && !hasErrors;
      else el.readOnly = !!data && !hasErrors;
    });
    setDetails(kind, !data || hasErrors);
    if (kind === 'customer') {
      document.getElementById('customer-pill').classList.toggle('visible', !!data);
      setText('pill-name', data ? [data.name, data.name2].filter(Boolean).join(' · ') : '');
      setText('pill-info', data ? address(data) : '');
      setText('pill-email', data ? data.email || 'Keine E-Mail-Adresse hinterlegt' : '');
    } else {
      document.getElementById('object-summary').hidden = !data;
      setText('object-summary-name', data ? data.name : '');
      setText('object-summary-address', data ? address(data) : '');
    }
  }

  function updateObjectDropdown(objects, keep = '') {
    // Nur Objekte des gewählten Kunden anbieten. Ohne JS bleibt der Server-Fallback.
    const previous = Array.from(objSel.options).find(o => o.value === keep);
    while (objSel.options.length > 1) objSel.remove(1);
    objects.forEach(obj => objSel.add(new Option(obj.name + (obj.street ? ' – ' + obj.street : ''), String(obj.id))));
    // Ungültige gebundene Auswahl sichtbar lassen, damit sie korrigiert werden kann.
    if (keep && !objects.some(o => String(o.id) === keep) && previous) objSel.add(previous);
    objSel.value = keep;
    setText('obj-hint', objects.length ? objects.length + ' Objekte für diesen Kunden verfügbar.' : 'Kein Objekt ausgewählt. Leistungsort bei Bedarf direkt eingeben.');
  }

  function clearObject() {
    selectedObject = null;
    objSel.value = '';
    objectNames.forEach(name => fill('id_object_' + name, name === 'country' ? 'DE' : ''));
    showSelection('object', null);
  }

  async function loadCustomer() {
    const pk = customerSel.value;
    const request = ++customerRequest;
    selectedCustomer = null;
    customerObjectCache = [];
    customerNames.forEach(name => fill('id_' + name, name === 'country' ? 'DE' : name === 'customer_type' ? 'einmalig' : ''));
    clearObject();
    updateObjectDropdown([]);
    showSelection('customer', null);
    customerLoading = !!pk;
    customerLoadFailed = false;
    document.getElementById('customer-retry').hidden = true;
    setText('customer-load-status', pk ? 'Kundendaten werden geladen …' : 'Neuen Rechnungsempfänger eingeben.');
    if (!pk) return;
    customerFields.querySelectorAll('input, select').forEach(el => {
      if (el.tagName === 'SELECT') el.disabled = true;
      else el.readOnly = true;
    });
    try {
      const resp = await fetch(`/api/kunden/${encodeURIComponent(pk)}/daten/`);
      if (!resp.ok) throw new Error('Kundendaten nicht verfügbar');
      const data = await resp.json();
      if (request !== customerRequest || customerSel.value !== pk) return;
      selectedCustomer = data;
      customerNames.forEach(name => fill('id_' + name, data[name]));
      fill('id_payment_days', data.payment_days);
      customerObjectCache = data.objects || [];
      updateObjectDropdown(customerObjectCache);
      showSelection('customer', data);
      setText('customer-load-status', 'Gespeicherte Kundendaten übernommen.');
      updateDueDate();
    } catch (error) {
      if (request !== customerRequest) return;
      customerLoadFailed = true;
      showSelection('customer', null);
      document.getElementById('customer-retry').hidden = false;
      setText('customer-load-status', 'Kundendaten konnten nicht geladen werden. Bitte erneut laden oder einen neuen Empfänger erfassen.');
    } finally {
      if (request === customerRequest) customerLoading = false;
    }
  }

  customerSel.addEventListener('change', loadCustomer);
  document.getElementById('customer-retry').addEventListener('click', loadCustomer);
  document.getElementById('clear-customer').addEventListener('click', () => {
    customerSel.value = '';
    customerSel.dispatchEvent(new Event('change', {bubbles: true}));
    document.getElementById('id_name').focus();
  });
  objSel.addEventListener('change', () => {
    selectedObject = customerObjectCache.find(o => String(o.id) === objSel.value) || null;
    if (!selectedObject) { clearObject(); return; }
    objectNames.forEach(name => fill('id_object_' + name, selectedObject[name]));
    showSelection('object', selectedObject);
  });
  document.getElementById('clear-object').addEventListener('click', () => {
    clearObject();
    objSel.dispatchEvent(new Event('change', {bubbles: true}));
    document.getElementById('id_object_name').focus();
  });
  ['customer', 'object'].forEach(kind => {
    document.getElementById(kind + '-data-actions').hidden = false;
    document.getElementById(kind + '-details-toggle').addEventListener('click', () => {
      const fields = kind === 'customer' ? customerFields : objectFields;
      setDetails(kind, fields.hidden);
    });
  });
  function blockPending(event) {
    if (!customerLoading && !customerLoadFailed) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.sedoWizard && window.sedoWizard.goTo('step-kunde');
    document.getElementById('customer-load-status').scrollIntoView({block: 'center'});
  }
  formRoot.addEventListener('submit', blockPending, true);
  formRoot.addEventListener('click', event => {
    if (event.target.closest('[data-wizard-next], .wizard-pill')) blockPending(event);
  }, true);
  formRoot.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.tagName !== 'BUTTON') blockPending(event);
  }, true);
  // Native Validierung muss auch ein eingeklapptes Feld erreichen können.
  formRoot.addEventListener('invalid', event => {
    const step = event.target.closest('[data-wizard-step]');
    if (step && window.sedoWizard) window.sedoWizard.goTo(step.id, {scroll: true});
    if (customerFields.contains(event.target)) setDetails('customer', true);
    if (objectFields.contains(event.target)) setDetails('object', true);
  }, true);
  document.querySelectorAll('[data-review-goto]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => window.sedoWizard && window.sedoWizard.goTo(button.dataset.reviewGoto));
  });
  updateObjectDropdown(customerObjectCache, initial.object_id);
  showSelection('customer', selectedCustomer);
  showSelection('object', selectedObject);

  /* ── Fälligkeitsdatum-Vorschau ───────────────────── */
  const issueDateEl = document.getElementById('id_issue_date');
  const payDaysEl   = document.getElementById('id_payment_days');
  const duePreview  = document.getElementById('due-preview');
  const dueDateVal  = document.getElementById('due-date-val');

  function dueDate() {
    if (!issueDateEl || !payDaysEl) return null;
    const d = new Date(issueDateEl.value + 'T12:00:00');
    const days = parseInt(payDaysEl.value, 10);
    if (isNaN(d.getTime()) || isNaN(days)) return null;
    d.setDate(d.getDate() + days);
    return d;
  }

  function updateDueDate() {
    if (!duePreview || !dueDateVal) return;
    const d = dueDate();
    if (!d) { duePreview.classList.add('hidden'); return; }
    dueDateVal.textContent = d.toLocaleDateString('de-DE', {day:'2-digit', month:'2-digit', year:'numeric'});
    duePreview.classList.remove('hidden');
  }

  issueDateEl && issueDateEl.addEventListener('change', updateDueDate);
  payDaysEl   && payDaysEl.addEventListener('input',  updateDueDate);

  /* ── Live-Positionskalkulation ───────────────────── */
  const vatEl = document.getElementById('id_vat_rate');
  let lastNet = 0, lastGross = 0, lastCount = 0;

  function recalcTotals() {
    let net = 0, count = 0;
    document.querySelectorAll('#position-list .position-form').forEach(form => {
      const delInput = form.querySelector('input[name$="-DELETE"]');
      if (delInput && delInput.checked) return;

      const qtyEl   = form.querySelector('input[name$="-quantity"]');
      const priceEl = form.querySelector('input[name$="-unit_price"]');
      const qty     = parseFloat((qtyEl   ? qtyEl.value   : '0').replace(',', '.')) || 0;
      const price   = parseFloat((priceEl ? priceEl.value : '0').replace(',', '.')) || 0;
      const line    = qty * price;

      const descEl = form.querySelector('input[name$="-description"]');
      if (descEl && descEl.value.trim()) count++;

      const lineDisplay = form.querySelector('.pos-line-total-val');
      if (lineDisplay) lineDisplay.textContent = fmtMoney(line);
      net += line;
    });

    const vatRate = parseFloat((vatEl ? vatEl.value : '19').replace(',', '.')) || 0;
    const vat   = net * vatRate / 100;
    lastNet = net; lastGross = net + vat; lastCount = count;

    setText('calc-net',      fmtMoney(net));
    setText('calc-vat',      fmtMoney(vat));
    setText('calc-gross',    fmtMoney(lastGross));
    setText('calc-vat-rate', vatRate % 1 === 0 ? vatRate.toFixed(0) : vatRate.toFixed(2));
  }

  const posList = document.getElementById('position-list');
  if (posList) {
    posList.addEventListener('input',  recalcTotals);
    posList.addEventListener('change', recalcTotals);
  }
  vatEl && vatEl.addEventListener('input', recalcTotals);

  /* ── Position hinzufügen ─────────────────────────── */
  const addBtn = document.getElementById('add-position');
  const tmpl   = document.getElementById('empty-form-template');
  const total  = document.querySelector('#id_items-TOTAL_FORMS');

  if (addBtn && posList && tmpl && total) {
    addBtn.addEventListener('click', function () {
      const idx = parseInt(total.value, 10);
      const html = tmpl.innerHTML
        .replaceAll('__prefix__', String(idx))
        .replaceAll('__num__',    String(idx + 1));
      posList.insertAdjacentHTML('beforeend', html);
      total.value = idx + 1;

      const newPos = posList.lastElementChild;
      if (newPos) {
        const descInput = newPos.querySelector('input[name$="-description"]');
        descInput && descInput.focus();
        updatePositionNumbers();
      }
      recalcTotals();
    });
  }

  /** Positions-Nummern nach Löschen/Hinzufügen neu setzen */
  function updatePositionNumbers() {
    document.querySelectorAll('#position-list .position-form').forEach((form, i) => {
      const badge = form.querySelector('.pos-no');
      if (badge) badge.textContent = i + 1;
      const labels = {description: 'Bezeichnung', quantity: 'Menge', unit: 'Einheit',
        unit_price: 'Einzelpreis netto', DELETE: 'Entfernen', long_text: 'Leistungsbeschreibung',
        article_no: 'Artikelnummer', leistungsart_id: 'Leistungsart-ID', leistungsart_text: 'Leistungsart-Text'};
      Object.entries(labels).forEach(([field, label]) => {
        const input = form.querySelector('[name$="-' + field + '"]');
        if (input) input.setAttribute('aria-label', 'Position ' + (i + 1) + ': ' + label);
      });
    });
  }

  if (posList) {
    posList.addEventListener('change', function (e) {
      if (e.target && e.target.name && e.target.name.endsWith('-DELETE')) {
        updatePositionNumbers();
        recalcTotals();
      }
    });
  }

  /* ── Zusammenfassung im letzten Schritt ──────────── */
  function textOf(id) {
    const el = document.getElementById(id);
    return el ? (el.value || '').trim() : '';
  }
  function selectedText(id) {
    const el = document.getElementById(id);
    if (!el || el.selectedIndex < 0) return '';
    const opt = el.options[el.selectedIndex];
    return (opt && opt.value) ? opt.text.trim() : '';
  }

  function updateRecap() {
    recalcTotals();
    const customer = selectedCustomer || Object.fromEntries(customerNames.map(name => [name, textOf('id_' + name)]));
    const obj = selectedObject || Object.fromEntries(objectNames.map(name => [name, textOf('id_object_' + name)]));
    setText('rc-customer', [customer.name, customer.name2].filter(Boolean).join(' · ') || '—');
    setText('rc-customer-address', address(customer));
    setText('rc-email', customer.email || 'Keine E-Mail-Adresse hinterlegt');
    setText('rc-object', obj.name || 'Kein Leistungsort ausgewählt');
    setText('rc-object-address', address(obj));
    setText('rc-period', [textOf('id_period_start'), textOf('id_period_end')].filter(Boolean).map(value => new Date(value + 'T12:00:00').toLocaleDateString('de-DE')).join(' – ') || 'Nicht angegeben');
    const items = document.getElementById('rc-items');
    items.replaceChildren();
    document.querySelectorAll('#position-list .position-form').forEach(row => {
      if (row.querySelector('[name$="-DELETE"]')?.checked) return;
      const value = suffix => row.querySelector('[name$="-' + suffix + '"]')?.value || '';
      if (!value('description').trim()) return;
      const item = document.createElement('li');
      const description = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = value('description');
      const detail = document.createElement('span');
      detail.textContent = value('long_text');
      description.append(name, detail);
      const amount = document.createElement('span');
      amount.textContent = new Intl.NumberFormat('de-DE', {maximumFractionDigits: 2}).format(parseFloat(value('quantity').replace(',', '.'))) + ' ' + value('unit') + ' × ' + fmtMoney(parseFloat(value('unit_price').replace(',', '.'))) + ' = ' + row.querySelector('.pos-line-total-val').textContent;
      item.append(description, amount);
      items.appendChild(item);
    });
    setText('rc-net', fmtMoney(lastNet));
    setText('rc-vat', fmtMoney(lastGross - lastNet));
    const d = (issueDateEl && issueDateEl.value) ? new Date(issueDateEl.value + 'T12:00:00') : null;
    setText('rc-date', (d && !isNaN(d.getTime())) ? d.toLocaleDateString('de-DE') : '—');
    const due = dueDate();
    setText('rc-due', due ? due.toLocaleDateString('de-DE') : '—');
    setText('rc-count', String(lastCount));
    setText('rc-gross', fmtMoney(lastGross));

    const warn = [];
    if (!textOf('id_name') && !selectedText('id_customer')) warn.push('Kein Kunde eingetragen – bitte Schritt 1 prüfen.');
    if (!obj.name) warn.push('Kein Leistungsort eingetragen – bitte prüfen, ob eine Leistungsadresse benötigt wird.');
    if (lastCount === 0) warn.push('Noch keine Position mit Bezeichnung – bitte Schritt 4 prüfen.');
    else if (lastNet <= 0) warn.push('Der Nettobetrag ist 0,00 € – Preise in Schritt 4 prüfen.');

    const box = document.getElementById('rc-warnings');
    if (!box) return;
    box.innerHTML = '';
    if (!warn.length) return;
    const div = document.createElement('div');
    div.className = 'card warning-card';
    div.style.marginTop = '14px';
    const t = document.createElement('strong');
    t.textContent = 'Bitte noch prüfen:';
    div.appendChild(t);
    const ul = document.createElement('ul');
    ul.style.cssText = 'margin:8px 0 0 18px;padding:0';
    warn.forEach(w => { const li = document.createElement('li'); li.textContent = w; ul.appendChild(li); });
    div.appendChild(ul);
    box.appendChild(div);
  }

  document.addEventListener('wizard:step', function (e) {
    if (e.detail && e.detail.id === 'step-pruefen') updateRecap();
  });

  /* ── Initialzustand ──────────────────────────────── */
  updatePositionNumbers();
  recalcTotals();
  updateDueDate();

})();
