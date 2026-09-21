/* SEDO Plausibilitäts-Check (v3.21.0)
 * Läuft im Prüfen-Schritt von Angebot und Rechnung, zweistufig:
 *  1. LOKAL (automatisch, kostenlos): Preis-Ausreißer gegen die eigene
 *     Kundenhistorie, Dubletten, Nullwerte – rein auf dem eigenen Server.
 *  2. KI-CHECK (per Knopf): Bonsai prüft NUR die Leistungsdaten
 *     (Bezeichnung/Menge/Einheit/Preis) auf kaufmännische Plausibilität.
 * Kunden-/Objektdaten gehen nie an Bonsai. */
(function () {
  'use strict';

  var urls = window.__sedoPlausiUrls || {};
  var step = document.getElementById('step-pruefen');
  var list = document.getElementById('position-list');
  if (!urls.check || !step || !list) return;

  function getCsrf() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  function notify(text, level) {
    if (typeof window.showToast === 'function') window.showToast(text, level || 'info');
  }

  /* ── Panel in den Prüfen-Schritt einhängen ────────────────────────── */
  var panel = document.createElement('div');
  panel.className = 'form-card plausi-panel';
  panel.innerHTML =
    '<div class="form-card-title">Plausibilitäts-Check' +
    ' <span class="info-tip" tabindex="0" aria-label="Hinweis zum KI-Check">i<span class="info-tip-text">' +
    'Die lokale Prüfung (Historie, Dubletten, Nullwerte) läuft automatisch auf diesem Rechner. ' +
    'Der KI-Check prüft nur die Leistungsdaten – Kunden- und Objektdaten werden nie übertragen.' +
    '</span></span></div>' +
    '<div id="plausi-local" class="plausi-results" aria-live="polite"></div>' +
    '<div class="toolbar" style="margin-top:10px">' +
    '  <button type="button" class="btn" id="plausi-ki-btn">' +
    '    <svg class="ico" aria-hidden="true"><use href="#i-shield"/></svg> KI-Check starten' +
    '  </button>' +
    '</div>' +
    '<div id="plausi-ki" class="plausi-results" aria-live="polite"></div>';
  step.insertBefore(panel, step.firstElementChild ? step.firstElementChild.nextSibling : null);

  var localBox = panel.querySelector('#plausi-local');
  var kiBox = panel.querySelector('#plausi-ki');
  var kiBtn = panel.querySelector('#plausi-ki-btn');

  /* ── Positionen aus dem Formular einsammeln ───────────────────────── */
  function collectPositions() {
    var out = [];
    list.querySelectorAll('.position-form').forEach(function (row) {
      var val = function (sel) {
        var el = row.querySelector(sel);
        return el ? el.value.trim() : '';
      };
      out.push({
        bezeichnung: val('input[name$="-description"]'),
        menge: val('input[name$="-quantity"]'),
        einheit: val('input[name$="-unit"]'),
        einzelpreis: val('input[name$="-unit_price"]')
      });
    });
    return out;
  }

  function customerId() {
    var sel = document.getElementById('id_customer');
    return sel && sel.value ? sel.value : null;
  }

  /* ── Befunde rendern ──────────────────────────────────────────────── */
  function renderFindings(box, findings, emptyText) {
    box.innerHTML = '';
    if (!findings.length) {
      if (emptyText) {
        var okLine = document.createElement('div');
        okLine.className = 'plausi-ok';
        okLine.textContent = emptyText;
        box.appendChild(okLine);
      }
      return;
    }
    var ul = document.createElement('ul');
    ul.className = 'plausi-list';
    findings.forEach(function (f) {
      var li = document.createElement('li');
      li.className = 'plausi-item plausi-item--' + (f.schwere === 'warnung' ? 'warnung' : 'hinweis');
      li.textContent = f.text; // textContent = sicher gegen HTML-Injection
      ul.appendChild(li);
      if (f.position) markRow(f.position);
    });
    box.appendChild(ul);
  }

  function markRow(positionNo) {
    var row = list.querySelectorAll('.position-form')[positionNo - 1];
    if (!row) return;
    row.classList.add('ai-row-review');
    var handler = function (ev) {
      if (!ev.isTrusted) return;
      row.classList.remove('ai-row-review');
      row.removeEventListener('input', handler, true);
    };
    row.addEventListener('input', handler, true);
  }

  function postCheck(mode) {
    return fetch(urls.check, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrf(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ positionen: collectPositions(), customer: customerId(), mode: mode })
    }).then(function (res) {
      return res.json().catch(function () {
        throw new Error('Serverantwort war nicht lesbar.');
      }).then(function (data) {
        if (!res.ok || !data.ok) throw new Error(data.error || 'Prüfung fehlgeschlagen (HTTP ' + res.status + ').');
        return data;
      });
    });
  }

  /* ── Lokaler Check: automatisch beim Betreten des Prüfen-Schritts ──── */
  var localRunning = false;
  function runLocalCheck() {
    if (localRunning) return;
    var hasContent = collectPositions().some(function (p) { return p.bezeichnung; });
    if (!hasContent) { localBox.innerHTML = ''; return; }
    localRunning = true;
    postCheck('lokal').then(function (data) {
      renderFindings(localBox, data.lokal || [], '✓ Lokale Prüfung: keine Auffälligkeiten (Historie, Dubletten, Nullwerte).');
    }).catch(function () {
      localBox.innerHTML = ''; // lokale Prüfung ist Komfort – Fehler nicht aufdrängen
    }).finally(function () {
      localRunning = false;
    });
  }

  document.addEventListener('wizard:step', function (e) {
    if (e.detail && e.detail.id === 'step-pruefen') runLocalCheck();
  });
  // Fallback ohne Assistent (alle Schritte sichtbar): einmal beim Laden prüfen
  if (!document.querySelector('[data-wizard]') || step.offsetParent !== null) runLocalCheck();

  /* ── KI-Check per Knopf ───────────────────────────────────────────── */
  kiBtn.addEventListener('click', function () {
    kiBtn.disabled = true;
    kiBox.innerHTML = '<div class="ai-skeleton"><div class="ai-skeleton-label">Bonsai prüft die Positionen …</div><span style="width:86%"></span><span style="width:70%"></span></div>';
    postCheck('ki').then(function (data) {
      if (data.ki_error) {
        kiBox.innerHTML = '';
        notify(data.ki_error, 'error');
        return;
      }
      var findings = data.ki || [];
      renderFindings(kiBox, findings, '');
      if (!findings.length) {
        kiBox.innerHTML = '<div class="plausi-ok">✓ KI-Check: keine Auffälligkeiten gefunden.</div>';
        notify('KI-Check abgeschlossen: keine Auffälligkeiten.', 'success');
      } else {
        notify('KI-Check: ' + findings.length + ' Punkt' + (findings.length === 1 ? '' : 'e') + ' zum Prüfen gefunden.', 'warning');
      }
    }).catch(function (err) {
      kiBox.innerHTML = '';
      notify(err.message || 'KI-Check fehlgeschlagen.', 'error');
    }).finally(function () {
      kiBtn.disabled = false;
    });
  });
})();
