/* SEDO: Rohtext oder lokales Whisper-Diktat → lokaler Bonsai-Entwurf.
 * Kein automatischer Wechsel zu Cloud- oder Browser-Spracherkennung. */
(function () {
  'use strict';

  var recordBtn = document.getElementById('ai-record-btn');
  var extractBtn = document.getElementById('ai-extract-btn');
  var transcript = document.getElementById('ai-transcript');
  var statusEl = document.getElementById('ai-status');
  var hintsEl = document.getElementById('ai-hints');
  var assumptionsEl = document.getElementById('ai-assumptions');
  var recIndicator = document.getElementById('ai-rec-indicator');
  var recTimer = document.getElementById('ai-rec-timer');
  var list = document.getElementById('position-list');
  var tmpl = document.getElementById('empty-form-template');
  var total = document.querySelector('#id_items-TOTAL_FORMS');
  var urls = window.__sedoAiUrls || {};

  if (!recordBtn || !extractBtn || !transcript || !list || !tmpl || !total || !urls.extract) return;

  var csrfInput = document.querySelector('#offer-form [name=csrfmiddlewaretoken]');
  var csrf = csrfInput ? csrfInput.value : '';

  var busy = false;
  var recording = false;
  var timerInterval = null;
  var recStartedAt = 0;
  var recordBtnStartHTML = recordBtn.innerHTML;   // Mikrofon-Icon + Beschriftung
  var STOP_HTML = '<svg class="ico" aria-hidden="true"><use href="#i-stop"/></svg> Diktat beenden';

  function setStatus(text) { if (statusEl) statusEl.textContent = text || ''; }

  function notify(text, level) {
    if (typeof window.showToast === 'function') window.showToast(text, level || 'info');
    else setStatus(text);
  }

  function setBusy(state, label) {
    busy = state;
    extractBtn.disabled = state;
    recordBtn.disabled = !whisperAvailable || (state && !recording);
    setStatus(state ? (label || 'Bitte warten …') : '');
  }

  /* ── Lade-Feedback: Shimmer-Skeleton unter dem KI-Bereich ─────────── */
  var skeletonEl = null;
  function showSkeleton(label) {
    hideSkeleton();
    skeletonEl = document.createElement('div');
    skeletonEl.className = 'ai-skeleton';
    skeletonEl.setAttribute('aria-hidden', 'true');
    var caption = document.createElement('div');
    caption.className = 'ai-skeleton-label';
    caption.textContent = label;
    skeletonEl.appendChild(caption);
    for (var i = 0; i < 3; i++) {
      var bar = document.createElement('span');
      bar.style.width = (88 - i * 14) + '%';
      skeletonEl.appendChild(bar);
    }
    transcript.parentNode.appendChild(skeletonEl);
  }
  function hideSkeleton() {
    if (skeletonEl) { skeletonEl.remove(); skeletonEl = null; }
  }

  /* ── Aufnahme-Anzeige (pulsierender Punkt + Laufzeit) ─────────────── */
  function showRecIndicator() {
    if (!recIndicator) return;
    recStartedAt = Date.now();
    recIndicator.hidden = false;
    if (recTimer) recTimer.textContent = '0:00';
    timerInterval = setInterval(function () {
      var s = Math.floor((Date.now() - recStartedAt) / 1000);
      var m = Math.floor(s / 60);
      var r = s % 60;
      if (recTimer) recTimer.textContent = m + ':' + (r < 10 ? '0' : '') + r;
    }, 500);
  }

  function hideRecIndicator() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    if (recIndicator) recIndicator.hidden = true;
  }

  function resetRecordBtn() {
    recording = false;
    recordBtn.innerHTML = recordBtnStartHTML;
    recordBtn.classList.remove('primary');
    hideRecIndicator();
  }

  function markRecording() {
    recording = true;
    recordBtn.innerHTML = STOP_HTML;
    recordBtn.classList.add('primary');
    showRecIndicator();
  }

  /* ══ Zahlen-/Einheiten-Normalisierung des Diktats ═══════════════════
   * "dreihundertfünfzig Euro" → "350 €", "neunzehn Prozent" → "19 %",
   * "drei Komma fünf" → "3,5", "120 Quadratmeter" → "120 m²".
   * Bewusst konservativ: Artikel wie "ein/eine" bleiben unangetastet. */
  var NUM_ONES = { 'null':0,'eins':1,'zwei':2,'zwo':2,'drei':3,'vier':4,'fünf':5,'fuenf':5,'sechs':6,'sieben':7,'acht':8,'neun':9 };
  var NUM_TEENS = { 'zehn':10,'elf':11,'zwölf':12,'zwoelf':12,'dreizehn':13,'vierzehn':14,'fünfzehn':15,'fuenfzehn':15,'sechzehn':16,'siebzehn':17,'achtzehn':18,'neunzehn':19 };
  var NUM_TENS = { 'zwanzig':20,'dreißig':30,'dreissig':30,'vierzig':40,'fünfzig':50,'fuenfzig':50,'sechzig':60,'siebzig':70,'achtzig':80,'neunzig':90 };

  // Parst ein zusammengesetztes deutsches Zahlwort (bis 999 999); null = kein Zahlwort.
  function parseGermanNumber(word) {
    var w = word.toLowerCase();
    if (w === 'ein' || w === 'eine' || w === 'einen' || w === 'einem' || w === 'einer' || w === 'eines') return null; // Artikel
    function upTo999(s) {
      if (!s) return 0;
      var sum = 0;
      var hIdx = s.indexOf('hundert');
      if (hIdx >= 0) {
        var pre = s.slice(0, hIdx);
        sum += (pre === '' || pre === 'ein' ? 1 : (NUM_ONES[pre] !== undefined ? NUM_ONES[pre] : NaN)) * 100;
        s = s.slice(hIdx + 'hundert'.length);
        if (!s) return sum;
      }
      if (NUM_TEENS[s] !== undefined) return sum + NUM_TEENS[s];
      var undIdx = s.indexOf('und');
      if (undIdx > 0) {
        var ones = s.slice(0, undIdx);
        var tens = s.slice(undIdx + 3);
        var o = (ones === 'ein') ? 1 : NUM_ONES[ones];
        if (o === undefined || NUM_TENS[tens] === undefined) return NaN;
        return sum + NUM_TENS[tens] + o;
      }
      if (NUM_TENS[s] !== undefined) return sum + NUM_TENS[s];
      if (NUM_ONES[s] !== undefined) return sum + NUM_ONES[s];
      return NaN;
    }
    var total = 0;
    var tIdx = w.indexOf('tausend');
    if (tIdx >= 0) {
      var pre = w.slice(0, tIdx);
      var mult = (pre === '' || pre === 'ein') ? 1 : upTo999(pre);
      if (isNaN(mult)) return null;
      total += mult * 1000;
      w = w.slice(tIdx + 'tausend'.length);
      if (!w) return total;
    }
    var rest = upTo999(w);
    if (isNaN(rest)) return null;
    total += rest;
    return (total === 0 && word.toLowerCase() !== 'null') ? total : total;
  }

  function normalizeDictation(text) {
    // 1. Zahlwörter → Ziffern (nur ganze Wörter, die sauber parsen)
    var out = text.replace(/[a-zäöüß]+/gi, function (word) {
      var lw = word.toLowerCase();
      // Nur Kandidaten anfassen, die wie ein Zahlwort beginnen
      var starts = /^(ein|zwei|zwo|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwoelf|hundert|tausend|null)/.test(lw);
      if (!starts) return word;
      var n = parseGermanNumber(word);
      return (n === null || isNaN(n)) ? word : String(n);
    });
    // 2. "3 Komma 5" → "3,5"
    out = out.replace(/(\d+)\s+komma\s+(\d+)/gi, '$1,$2');
    // 3. Einheiten nach Zahlen: Prozent → %, Euro → €, Quadratmeter → m²
    out = out.replace(/(\d+(?:,\d+)?)\s*prozent\b/gi, '$1 %');
    out = out.replace(/(\d+(?:,\d+)?)\s*euro\b/gi, '$1 €');
    out = out.replace(/(\d+(?:,\d+)?)\s*quadratmetern?\b/gi, '$1 m²');
    return out;
  }

  /* ══ Aufnahme-Weg 1: MediaRecorder → lokales Whisper ════════════════ */
  var mediaRecorder = null;
  var mediaChunks = [];
  var mediaStream = null;
  var whisperAvailable = !!(urls.transcribe && urls.sttAvailable &&
    navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

  function pickMimeType() {
    var candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    for (var i = 0; i < candidates.length; i++) {
      if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    }
    return '';
  }

  function startWhisperRecording() {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      mediaStream = stream;
      mediaChunks = [];
      var mime = pickMimeType();
      mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRecorder.ondataavailable = function (e) { if (e.data && e.data.size) mediaChunks.push(e.data); };
      mediaRecorder.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        mediaStream = null;
        var type = mediaRecorder.mimeType || 'audio/webm';
        var blob = new Blob(mediaChunks, { type: type });
        mediaChunks = [];
        if (blob.size < 1000) { setStatus(''); return; } // praktisch leer
        uploadForTranscription(blob, type);
      };
      mediaRecorder.start();
      markRecording();
      setStatus('Grob ansagen, was gemacht werden soll – die KI baut daraus das Angebot.');
    }).catch(function () {
      notify('Mikrofonzugriff wurde verweigert. Bitte in den Browser-Einstellungen erlauben.', 'error');
      resetRecordBtn();
    });
  }

  function uploadForTranscription(blob, mimeType) {
    var ext = mimeType.indexOf('mp4') >= 0 ? '.m4a' : (mimeType.indexOf('ogg') >= 0 ? '.ogg' : '.webm');
    var fd = new FormData();
    fd.append('audio', blob, 'diktat' + ext);
    setBusy(true, 'Aufnahme wird lokal transkribiert …');
    transcript.classList.add('ai-pulse');
    showSkeleton('Whisper transkribiert die Aufnahme …');
    fetch(urls.transcribe, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: fd })
      .then(parseJsonResponse)
      .then(function (data) {
        transcript.classList.remove('ai-pulse');
        hideSkeleton();
        setBusy(false);
        var clean = normalizeDictation((data.text || '').trim());
        if (!clean) { notify('In der Aufnahme wurde keine Sprache erkannt.', 'warning'); return; }
        var base = transcript.value.trim();
        transcript.value = base ? base + '\n' + clean : clean;
        runGeneration();
      })
      .catch(function (err) {
        transcript.classList.remove('ai-pulse');
        hideSkeleton();
        setBusy(false);
        if (err && err.code === 'stt_unavailable') {
          whisperAvailable = false;
          recordBtn.disabled = true;
          notify('Lokales Diktat ist nicht verfügbar – bitte Text eingeben. Die Aufnahme wurde nicht an einen Cloud-Dienst gesendet.', 'warning');
        } else {
          notify((err && err.message) || 'Transkription fehlgeschlagen. Bitte erneut versuchen.', 'error');
        }
      });
  }

  if (!whisperAvailable) {
    recordBtn.disabled = true;
    recordBtn.title = 'Lokales Diktat nicht verfügbar – bitte Text eingeben.';
    setStatus('Lokales Diktat nicht verfügbar – bitte Text unten eintippen.');
  }

  /* ── Aufnahme-Button: startet/stoppt den jeweils aktiven Weg ──────── */
  recordBtn.addEventListener('click', function () {
    if (busy) return;
    if (!recording) {
      if (whisperAvailable) startWhisperRecording();
      else notify('Lokales Diktat nicht verfügbar – bitte Text eingeben.', 'warning');
      return;
    }
    // Stopp
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      resetRecordBtn();
      setStatus('Aufnahme wird verarbeitet …');
      mediaRecorder.stop();
      return;
    }
  });

  /* ── Angebot erzeugen ─────────────────────────────────────────────── */
  extractBtn.addEventListener('click', function () {
    if (busy) return;
    transcript.value = normalizeDictation(transcript.value);
    runGeneration();
  });

  function runGeneration() {
    var text = transcript.value.trim();
    if (text.length < 5) {
      notify('Bitte zuerst diktieren oder einen Text eingeben.', 'warning');
      return;
    }
    setBusy(true, 'Bonsai erstellt den Angebotsentwurf …');
    showSkeleton('Bonsai erstellt Positionen, Leistungstexte und Preise …');
    fetch(urls.extract, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    }).then(parseJsonResponse).then(function (data) {
      hideSkeleton();
      setBusy(false);
      renderAssumptions(data.annahmen || [], data.katalog_info || '');
      renderHints(data.hinweise || []);
      var n = fillPositions(data.positionen || []);
      if (n > 0) {
        notify('Angebotsentwurf mit ' + n + ' Position' + (n === 1 ? '' : 'en') + ' erstellt – bitte prüfen und dann speichern.', 'success');
        // Im Schritt-Assistenten direkt zu den erzeugten Positionen springen
        if (window.sedoWizard && typeof window.sedoWizard.goTo === 'function') {
          window.sedoWizard.goTo('step-positionen', { focus: false, scroll: true });
        }
      } else {
        notify('Es konnten keine Positionen erzeugt werden. Bitte die Leistung kurz beschreiben.', 'warning');
      }
    }).catch(function (err) {
      hideSkeleton();
      setBusy(false);
      notify((err && err.message) || 'KI-Auswertung fehlgeschlagen. Bitte erneut versuchen.', 'error');
    });
  }

  function parseJsonResponse(res) {
    return res.json().catch(function () {
      throw new Error('Serverantwort war nicht lesbar. Bitte erneut versuchen.');
    }).then(function (data) {
      if (!res.ok || !data.ok) {
        var e = new Error(data.error || 'Anfrage fehlgeschlagen (HTTP ' + res.status + ').');
        if (data.code) e.code = data.code;
        throw e;
      }
      return data;
    });
  }

  /* ── Formset-Befüllung ────────────────────────────────────────────── */
  function rowInputs(row) {
    return {
      d: row.querySelector('input[name$="-description"]'),
      lt: row.querySelector('textarea[name$="-long_text"]'),
      q: row.querySelector('input[name$="-quantity"]'),
      u: row.querySelector('input[name$="-unit"]'),
      p: row.querySelector('input[name$="-unit_price"]'),
      a: row.querySelector('input[name$="-article_no"]')
    };
  }

  function allRows() { return list.querySelectorAll('.position-form'); }

  function hasFilledRow() {
    var rows = allRows();
    for (var i = 0; i < rows.length; i++) {
      var inp = rowInputs(rows[i]);
      if (inp.d && inp.d.value.trim()) return true;
    }
    return false;
  }

  function clearAllRows() {
    allRows().forEach(function (row) {
      var inp = rowInputs(row);
      if (inp.d) inp.d.value = '';
      if (inp.lt) inp.lt.value = '';
      if (inp.q) inp.q.value = '1.00';
      if (inp.u) inp.u.value = 'Stk.';
      if (inp.p) inp.p.value = '0.00';
      if (inp.a) inp.a.value = '';
      clearAiMarks(row);
    });
  }

  function nextEmptyRow() {
    var rows = allRows();
    for (var i = 0; i < rows.length; i++) {
      var inp = rowInputs(rows[i]);
      if (inp.d && !inp.d.value.trim()) return rows[i];
    }
    return null;
  }

  function addRow() {
    var idx = parseInt(total.value, 10);
    var html = tmpl.innerHTML.replaceAll('__prefix__', idx).replaceAll('__num__', idx + 1);
    list.insertAdjacentHTML('beforeend', html);
    total.value = idx + 1;
    return list.lastElementChild;
  }

  function setValue(input, value) {
    if (!input) return;
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  /* ── Dauerhafte KI-Markierung (bis der Nutzer das Feld anfasst) ───── */
  function clearAiMarks(row) {
    row.classList.remove('ai-row-dictated', 'ai-row-review');
    row.querySelectorAll('.ai-review-field').forEach(function (el) {
      el.classList.remove('ai-review-field');
    });
  }

  function markRow(row, needsReview, priceInput) {
    row.classList.add(needsReview ? 'ai-row-review' : 'ai-row-dictated');
    if (needsReview && priceInput) priceInput.classList.add('ai-review-field');
    // Markierung verschwindet, sobald der Nutzer in der Zeile etwas ändert.
    var handler = function (ev) {
      if (!ev.isTrusted) return;             // programmatische Events ignorieren
      clearAiMarks(row);
      row.removeEventListener('input', handler, true);
    };
    row.addEventListener('input', handler, true);
  }

  function ensureLegend() {
    var legend = document.getElementById('ai-legend');
    if (legend) return legend;
    legend = document.createElement('div');
    legend.id = 'ai-legend';
    legend.className = 'ai-legend';
    legend.innerHTML =
      '<span class="ai-legend-swatch ai-legend-swatch--review"></span> Preis geschätzt/aufgeteilt – bitte prüfen' +
      '<span class="ai-legend-sep">·</span>' +
      '<span class="ai-legend-swatch ai-legend-swatch--dictated"></span> aus Diktat übernommen' +
      '<span class="ai-legend-sep">·</span>Markierung verschwindet nach Bearbeitung';
    list.parentNode.insertBefore(legend, list);
    return legend;
  }

  function fillPositions(positions) {
    if (!positions.length) return 0;
    if (hasFilledRow()) {
      var replace = window.confirm('Es sind bereits Positionen vorhanden.\n\nOK = durch den neuen KI-Entwurf ersetzen\nAbbrechen = neue Positionen zusätzlich anhängen');
      if (replace) clearAllRows();
    }
    var filled = 0;
    positions.forEach(function (pos) {
      if (!pos || !pos.bezeichnung) return;
      var row = nextEmptyRow() || addRow();
      if (!row) return;
      var inp = rowInputs(row);
      setValue(inp.d, pos.bezeichnung);
      setValue(inp.lt, pos.leistungstext || '');
      setValue(inp.q, pos.menge || '1.00');
      setValue(inp.u, pos.einheit || 'Stk.');
      setValue(inp.p, pos.einzelpreis || '');
      if (pos.artikel_nr) setValue(inp.a, pos.artikel_nr);
      // Amber = geschätzt/aufgeteilt/offen (prüfen!), Blau = diktierter Preis
      var pruefen = pos.preis_art && pos.preis_art !== 'genannt';
      markRow(row, pruefen, inp.p);
      filled++;
    });
    if (filled > 0) ensureLegend();
    return filled;
  }

  /* ── Annahmen & Hinweise ──────────────────────────────────────────── */
  function buildBox(title, items, borderColor, bgColor, textColor) {
    var box = document.createElement('div');
    box.style.cssText = 'margin-top:10px;padding:10px 12px;border:1px solid ' + borderColor +
      ';border-radius:8px;background:' + bgColor + ';color:' + textColor + ';font-size:13px;';
    var t = document.createElement('div');
    t.style.fontWeight = '600';
    t.textContent = title;
    box.appendChild(t);
    var ul = document.createElement('ul');
    ul.style.cssText = 'margin:6px 0 0 18px;padding:0;';
    items.forEach(function (h) {
      var li = document.createElement('li');
      li.textContent = h; // textContent = sicher gegen HTML-Injection
      ul.appendChild(li);
    });
    box.appendChild(ul);
    return box;
  }

  function renderAssumptions(assumptions, katalogInfo) {
    assumptionsEl.innerHTML = '';
    var items = assumptions.slice();
    if (katalogInfo) items.push('Katalog: ' + katalogInfo);
    if (!items.length) return;
    assumptionsEl.appendChild(buildBox('ℹ Getroffene Annahmen (bitte bestätigen):', items, '#93c5fd', '#eff6ff', '#1e40af'));
  }

  function renderHints(hints) {
    hintsEl.innerHTML = '';
    if (!hints.length) return;
    hintsEl.appendChild(buildBox('⚠ Hinweise der KI-Prüfung:', hints, '#f59e0b', '#fffbeb', '#92400e'));
  }

  /* Seite verlassen während Aufnahme → alles sauber stoppen */
  window.addEventListener('beforeunload', function () {
    if (mediaRecorder && mediaRecorder.state === 'recording') { try { mediaRecorder.stop(); } catch (e) { /* ignorieren */ } }
    if (mediaStream) { mediaStream.getTracks().forEach(function (t) { t.stop(); }); }
    hideRecIndicator();
  });
})();
