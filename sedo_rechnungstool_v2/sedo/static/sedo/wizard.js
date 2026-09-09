/* SEDO Schritt-Assistent (v3.19.0)
 * Führt lange Formulare (Angebot, Rechnung) Schritt für Schritt durch.
 *
 * Wichtig: Alle Felder bleiben im selben <form> – es wird nur die Sichtbarkeit
 * gesteuert. Ohne JavaScript sind alle Schritte sichtbar und das Formular
 * verhält sich exakt wie vorher (eine lange Seite mit Speichern-Leiste).
 *
 * Markup-Vertrag:
 *   <div data-wizard [data-wizard-unlocked]>            Container
 *     <ol data-wizard-nav hidden></ol>                  Fortschritts-Pills (JS füllt)
 *     <section data-wizard-step data-step-label="…" data-step-icon="…">
 *     …
 *     <div class="sticky-actions">
 *       <button data-wizard-prev hidden>                Zurück
 *       <span  data-wizard-progress hidden>             „Schritt 2 von 6“
 *       <button data-wizard-next hidden>                Weiter
 *       <span  data-wizard-final>                       Abschluss-Buttons
 */
(function () {
  'use strict';

  var FOCUS_SELECTOR = 'input:not([type=hidden]):not([disabled]):not([readonly]),' +
                       'select:not([disabled]),textarea:not([disabled])';
  /* Bei diesen Feldern soll Enter „Weiter“ auslösen statt das Formular zu senden. */
  var ENTER_TYPES = ['text', 'number', 'date', 'email', 'tel', 'url', 'search', 'password'];

  function initWizard(root) {
    var steps = Array.prototype.slice.call(root.querySelectorAll('[data-wizard-step]'));
    if (steps.length < 2) return null;

    var form = root.closest('form') || document.querySelector('form');
    var nav = root.querySelector('[data-wizard-nav]');
    var btnPrev = root.querySelector('[data-wizard-prev]');
    var btnNext = root.querySelector('[data-wizard-next]');
    var progress = root.querySelector('[data-wizard-progress]');
    var progressCur = root.querySelector('[data-wizard-current]');
    var progressTotal = root.querySelector('[data-wizard-total]');
    var finalBox = root.querySelector('[data-wizard-final]');

    var index = 0;
    var maxReached = 0;
    var pills = [];

    /* ── Fortschritts-Pills aufbauen ──────────────────────────────── */
    if (nav) {
      steps.forEach(function (step, i) {
        var li = document.createElement('li');
        var btn = document.createElement('button');
        btn.type = 'button';               // niemals absenden
        btn.className = 'wizard-pill';
        btn.setAttribute('data-goto', String(i));

        var no = document.createElement('span');
        no.className = 'wizard-pill-no';
        no.textContent = String(i + 1);

        var label = document.createElement('span');
        label.className = 'wizard-pill-label';
        label.textContent = step.getAttribute('data-step-label') || ('Schritt ' + (i + 1));

        btn.appendChild(no);
        btn.appendChild(label);
        btn.addEventListener('click', function () {
          if (btn.disabled) return;
          setStep(i, { focus: true, scroll: true });
        });

        li.appendChild(btn);
        nav.appendChild(li);
        pills.push(btn);
      });
      nav.hidden = false;
    }

    /* ── Anzeige aktualisieren ────────────────────────────────────── */
    function render() {
      steps.forEach(function (step, i) {
        var active = (i === index);
        step.classList.toggle('is-active', active);
        step.setAttribute('aria-hidden', active ? 'false' : 'true');
      });

      pills.forEach(function (pill, i) {
        pill.classList.toggle('is-current', i === index);
        pill.classList.toggle('is-done', i < index || (i < maxReached && i !== index));
        var locked = i > maxReached;
        pill.disabled = locked;
        pill.title = locked ? 'Bitte zuerst die vorherigen Schritte durchgehen' : '';
        if (i === index) pill.setAttribute('aria-current', 'step');
        else pill.removeAttribute('aria-current');
      });

      if (btnPrev) {
        btnPrev.hidden = false;
        btnPrev.disabled = (index === 0);
      }
      var isLast = (index === steps.length - 1);
      if (btnNext) btnNext.hidden = isLast;
      if (finalBox) finalBox.hidden = !isLast;
      if (progress) {
        progress.hidden = false;
        if (progressCur) progressCur.textContent = String(index + 1);
        if (progressTotal) progressTotal.textContent = String(steps.length);
      }

      root.setAttribute('data-wizard-index', String(index));
      // Seiten-Skripte können darauf reagieren (z. B. Zusammenfassung neu rechnen)
      root.dispatchEvent(new CustomEvent('wizard:step', {
        bubbles: true,
        detail: { index: index, id: steps[index].id || '', total: steps.length }
      }));
    }

    /* Defensiv: ältere Browser kennen die Optionen nicht, manche Umgebungen
       die Funktion überhaupt nicht – Scrollen darf den Assistenten nie stoppen. */
    function scrollToTop(el) {
      if (!el || typeof el.scrollIntoView !== 'function') return;
      try { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      catch (e) { try { el.scrollIntoView(); } catch (e2) { /* ignorieren */ } }
    }

    function focusFirstField(step) {
      var field = step.querySelector(FOCUS_SELECTOR);
      if (!field || typeof field.focus !== 'function') return;
      try { field.focus({ preventScroll: true }); }
      catch (e) { try { field.focus(); } catch (e2) { /* ignorieren */ } }
    }

    /* Aktuellen Schritt je Seite merken – so landet man nach „Zurück zur
     * Eingabe“ aus der Vorschau wieder im letzten Schritt statt bei Schritt 1. */
    var stepStoreKey = 'sedoWizardStep:' + location.pathname;

    function rememberStep(i) {
      try { sessionStorage.setItem(stepStoreKey, String(i)); } catch (e) { /* z. B. Privatmodus */ }
    }

    function recallStep() {
      try {
        var raw = sessionStorage.getItem(stepStoreKey);
        if (raw === null) return -1;
        var i = parseInt(raw, 10);
        return (i >= 0 && i < steps.length) ? i : -1;
      } catch (e) { return -1; }
    }

    function setStep(i, opts) {
      if (i < 0 || i >= steps.length) return;
      opts = opts || {};
      index = i;
      if (i > maxReached) maxReached = i;
      rememberStep(i);
      render();
      if (opts.scroll !== false) scrollToTop(root);
      if (opts.focus) focusFirstField(steps[i]);
    }

    function next() { if (index < steps.length - 1) setStep(index + 1, { focus: true, scroll: true }); }
    function prev() { if (index > 0) setStep(index - 1, { focus: true, scroll: true }); }

    btnNext && btnNext.addEventListener('click', next);
    btnPrev && btnPrev.addEventListener('click', prev);

    /* ── Enter im Eingabefeld = „Weiter“, nicht „Absenden“ ────────── */
    if (form) {
      form.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
        var t = e.target;
        if (!t || !t.tagName) return;
        var tag = t.tagName.toUpperCase();
        if (tag === 'TEXTAREA' || tag === 'BUTTON') return;   // Zeilenumbruch / Klick zulassen
        if (tag === 'A') return;
        if (tag === 'INPUT') {
          var type = (t.getAttribute('type') || 'text').toLowerCase();
          if (ENTER_TYPES.indexOf(type) === -1) return;       // z. B. checkbox/submit unverändert
        } else if (tag !== 'SELECT') {
          return;
        }
        e.preventDefault();
        if (index < steps.length - 1) next();
      });
    }

    /* ── Startzustand: bei Serverfehlern zum ersten Fehler springen ─ */
    var errorStep = -1;
    for (var i = 0; i < steps.length; i++) {
      if (steps[i].querySelector('.errorlist, .field-error, .wizard-step-error')) { errorStep = i; break; }
    }

    if (root.hasAttribute('data-wizard-unlocked') || errorStep > -1) {
      maxReached = steps.length - 1;   // alles freigeben, damit alles korrigierbar ist
    }

    /* Kommt der Nutzer per Browser-Zurück (z. B. aus der Vorschau), den
     * gemerkten Schritt wiederherstellen. Bei normalem Aufruf frisch starten. */
    var navEntry = (performance.getEntriesByType && performance.getEntriesByType('navigation')[0]) || null;
    var cameBack = !!navEntry && navEntry.type === 'back_forward';
    var recalled = cameBack ? recallStep() : -1;
    if (recalled > -1) {
      maxReached = steps.length - 1;   // alle Schritte waren bereits erreicht
    }

    root.classList.add('wizard-ready');
    if (errorStep > -1) {
      setStep(errorStep, { focus: false, scroll: true });
    } else if (recalled > -1) {
      setStep(recalled, { focus: false, scroll: false });
    } else {
      setStep(0, { focus: false, scroll: false });
    }

    return {
      next: next,
      prev: prev,
      /** Direkt zu einem Schritt springen – per Index oder Element-ID. */
      goTo: function (target, opts) {
        var i = -1;
        if (typeof target === 'number') {
          i = target;
        } else {
          for (var k = 0; k < steps.length; k++) {
            if (steps[k].id === target) { i = k; break; }
          }
        }
        if (i > -1) setStep(i, opts || { focus: false, scroll: true });
      },
      /** Schritte ab sofort frei anspringbar machen. */
      unlockAll: function () { maxReached = steps.length - 1; render(); },
      get index() { return index; },
      get total() { return steps.length; }
    };
  }

  document.addEventListener('DOMContentLoaded', function () {
    var roots = document.querySelectorAll('[data-wizard]');
    var instances = [];
    Array.prototype.forEach.call(roots, function (root) {
      var inst = initWizard(root);
      if (inst) { root.wizard = inst; instances.push(inst); }
    });
    window.sedoWizard = instances[0] || null;
    window.sedoWizards = instances;
  });
})();
