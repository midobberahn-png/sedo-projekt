/* SEDO Magic-Tone (v3.21.0)
 * (a) "Formulieren"-Knopf an jedem Leistungstext-Feld: lose/emotionale Ansage
 *     wird per Claude in eine professionelle Leistungsbeschreibung umformuliert.
 * (b) KI-Begleittext für den Rechnungsversand (Karte auf der Rechnungsseite).
 * Datenschutz: Es geht ausschließlich der jeweilige TEXT an Claude; der
 * System-Prompt entfernt Namen/Adressen. Kunden-/Objektdaten bleiben lokal. */
(function () {
  'use strict';

  var urls = window.__sedoMagicUrls || {};
  if (!urls.polish && !urls.coverLetter) return;

  function getCsrf() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  function notify(text, level) {
    if (typeof window.showToast === 'function') window.showToast(text, level || 'info');
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrf(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () {
        throw new Error('Serverantwort war nicht lesbar. Bitte erneut versuchen.');
      }).then(function (data) {
        if (!res.ok || !data.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen (HTTP ' + res.status + ').');
        return data;
      });
    });
  }

  var SPARKLE = '<svg class="ico" aria-hidden="true"><use href="#i-sparkles"/></svg>';

  /* ── (a) Formulieren-Knopf an long_text-Feldern ───────────────────── */
  function attachPolishButtons(root) {
    if (!urls.polish) return;
    (root || document).querySelectorAll('textarea[name$="-long_text"]').forEach(function (ta) {
      if (ta.dataset.magicTone) return;
      ta.dataset.magicTone = '1';
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn small ai-polish-btn';
      btn.innerHTML = SPARKLE + ' Formulieren';
      btn.title = 'Text per KI professionell formulieren (Namen werden entfernt)';
      btn.addEventListener('click', function () {
        var text = ta.value.trim();
        if (text.length < 3) {
          notify('Bitte zuerst einen (auch groben) Text in das Feld schreiben.', 'warning');
          return;
        }
        btn.disabled = true;
        ta.classList.add('ai-pulse');
        postJson(urls.polish, { text: text }).then(function (data) {
          ta.value = data.text;
          ta.dispatchEvent(new Event('input', { bubbles: true }));
          notify('Leistungstext professionell formuliert – bitte kurz prüfen.', 'success');
        }).catch(function (err) {
          notify(err.message || 'KI-Formulierung fehlgeschlagen.', 'error');
        }).finally(function () {
          btn.disabled = false;
          ta.classList.remove('ai-pulse');
        });
      });
      ta.insertAdjacentElement('afterend', btn);
    });
  }

  attachPolishButtons(document);
  // Auch dynamisch hinzugefügte Positionszeilen (Formset "+ Position") abdecken
  new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      m.addedNodes.forEach(function (node) {
        if (node.nodeType === 1) attachPolishButtons(node);
      });
    });
  }).observe(document.body, { childList: true, subtree: true });

  /* ── (b) KI-Begleittext auf der Rechnungsseite ────────────────────── */
  var coverBtn = document.getElementById('ai-cover-btn');
  var coverBody = document.getElementById('ai-cover-body');
  var coverHint = document.getElementById('ai-cover-hint');
  if (coverBtn && coverBody && urls.coverLetter && urls.invoiceId) {
    coverBtn.addEventListener('click', function () {
      coverBtn.disabled = true;
      coverBody.classList.add('ai-pulse');
      postJson(urls.coverLetter, {
        invoice: urls.invoiceId,
        hint: coverHint ? coverHint.value.trim() : ''
      }).then(function (data) {
        coverBody.value = data.text;
        notify('Begleittext erzeugt – bitte prüfen und dann senden.', 'success');
      }).catch(function (err) {
        notify(err.message || 'Begleittext konnte nicht erzeugt werden.', 'error');
      }).finally(function () {
        coverBtn.disabled = false;
        coverBody.classList.remove('ai-pulse');
      });
    });
  }
})();
