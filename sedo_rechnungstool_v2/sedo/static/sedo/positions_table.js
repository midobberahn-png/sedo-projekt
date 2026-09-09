/* Positions-Tabelle (Variante A, v3.23): Beschreibungszeile auf-/zuklappen.
 *
 * Jede Position ist ein <tbody class="position-form"> mit Hauptzeile und
 * Beschreibungszeile. Ohne Inhalt zeigt die Beschreibungszeile nur den
 * „+ Beschreibung“-Knopf; sobald ein Text vorhanden ist (manuell, per KI –
 * die füllt via input-Events – oder vom Server gerendert), bleibt sie offen.
 */
(function () {
  'use strict';

  function hasContent(tbody) {
    var lt = tbody.querySelector('textarea[name$="-long_text"]');
    if (lt && lt.value.trim()) return true;
    var extras = tbody.querySelectorAll('.pos-desc-body input:not([type=hidden])');
    return Array.prototype.some.call(extras, function (i) { return i.value.trim(); });
  }

  function refresh() {
    document.querySelectorAll('#position-list .position-form').forEach(function (tbody) {
      if (hasContent(tbody) || tbody.querySelector('.pos-desc-body .errorlist')) {
        tbody.classList.add('has-desc');
      }
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.pos-desc-toggle');
    if (!btn) return;
    var tbody = btn.closest('.position-form');
    if (!tbody) return;
    tbody.classList.add('has-desc');
    var lt = tbody.querySelector('textarea[name$="-long_text"]');
    if (lt) lt.focus();
  });

  /* KI-Befüllung und manuelles Tippen: sobald Text ankommt, Zeile öffnen. */
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!t || !t.name) return;
    if ((t.name.endsWith('-long_text') || t.name.endsWith('-article_no') ||
         t.name.endsWith('-leistungsart_id') || t.name.endsWith('-leistungsart_text'))
        && t.value.trim()) {
      var tbody = t.closest('.position-form');
      if (tbody) tbody.classList.add('has-desc');
    }
  }, true);

  window.sedoPositionsRefresh = refresh;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refresh);
  } else {
    refresh();
  }
})();
