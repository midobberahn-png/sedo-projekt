/* Explizite Abo-Auswahl; ohne JavaScript bleibt die Serverprüfung verbindlich. */
(function () {
  'use strict';
  document.querySelectorAll('[data-recurring-selection]').forEach(function (form) {
    var boxes = Array.from(form.querySelectorAll('input[name="template_ids"]:not(:disabled)'));
    var all = form.querySelector('[data-recurring-select-all]');
    var none = form.querySelector('[data-recurring-select-none]');
    var count = form.querySelector('[data-recurring-selection-count]');
    var submit = form.querySelector('[data-recurring-generate]');
    function refresh() {
      var selected = boxes.filter(function (box) { return box.checked; }).length;
      count.textContent = selected + ' von ' + boxes.length + ' aktiven Vorlagen ausgewählt';
      submit.disabled = selected === 0;
      all.disabled = boxes.length === 0 || selected === boxes.length;
      none.disabled = selected === 0;
    }
    all.hidden = false;
    none.hidden = false;
    all.addEventListener('click', function () {
      boxes.forEach(function (box) { box.checked = true; });
      refresh();
    });
    none.addEventListener('click', function () {
      boxes.forEach(function (box) { box.checked = false; });
      refresh();
    });
    form.addEventListener('change', refresh);
    window.addEventListener('pageshow', refresh);
    form.addEventListener('submit', function (event) {
      refresh();
      if (submit.disabled) event.preventDefault();
    });
    refresh();
  });
}());
