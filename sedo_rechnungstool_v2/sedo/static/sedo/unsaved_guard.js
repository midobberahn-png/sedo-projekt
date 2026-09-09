/* Warnung bei ungespeicherten Änderungen (v2.1, UX-Review).
 *
 * Wirkt auf den Angebots-/Rechnungsassistenten (#offer-form / #invoice-form):
 * Sobald ein Feld geändert wurde, warnt der Browser beim Verlassen der Seite.
 * Beim regulären Absenden (Speichern/Vorschau) wird die Warnung unterdrückt.
 */
(function () {
  'use strict';

  function init() {
    var form = document.getElementById('offer-form') || document.getElementById('invoice-form');
    if (!form) return;

    var dirty = false;
    var submitting = false;

    form.addEventListener('input', function () { dirty = true; });
    form.addEventListener('change', function () { dirty = true; });
    form.addEventListener('submit', function () { submitting = true; });

    window.addEventListener('beforeunload', function (e) {
      if (!dirty || submitting) return;
      e.preventDefault();
      e.returnValue = '';   // Browser zeigen ihren Standard-Dialog
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
