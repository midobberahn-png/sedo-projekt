// Review-Schritt-Workflow für SEDO v2 – limit-tauglich in kleine Aufrufe zerlegt.
// Aufruf mit args = { modus: 'finden'|'verifizieren'|'synthese', ... }
//   finden:       { modus:'finden', dimension:'zugferd-xml-pdf', bekannt:[…Titel] }
//   verifizieren: { modus:'verifizieren', dimension:'…', funde:[…] }   (2 Gutachter je Dimension, Batch-Urteil)
//   synthese:     { modus:'synthese', bestaetigt:[…], unklar:[…], verworfen:[…], coverage:[…] }
export const meta = {
  name: 'sedo-v2-review-schritt',
  description: 'Ein einzelner Review-Schritt (Prüfer, Verifikation einer Dimension oder Synthese) – Ergebnis wird vom Hauptprozess als JSON gesichert',
  phases: [
    { title: 'Finden', detail: 'ein Prüfer für eine Dimension' },
    { title: 'Verifizieren', detail: 'zwei unabhängige Gutachter beurteilen alle Funde einer Dimension' },
    { title: 'Synthese', detail: 'Bericht + Vollständigkeitskritik' },
  ],
}

const ROOT = '/Users/mirekdobberahn/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/sedo_rechnungstool_v2'

const REGELN = `
PROJEKT: SEDO Rechnungstool v2 (Django 6.0.8, SQLite), Pfad: "${ROOT}"
Lokales Rechnungstool einer Gebäudereinigungsfirma (2 Nutzer). Läuft auf Mac/Windows im Heimnetz (0.0.0.0:8000), zeitweise per Tunnel erreichbar; zusätzlich als PyInstaller-Desktop-App (desktop.py). Erzeugt ZUGFeRD-E-Rechnungen (CII/EN16931) für Aareon/GWG. GoBD ist wichtig (Nummernkreise via NumberRange, Soft-Delete, Storno statt Löschen). Harte Datenschutzregel: In KI-Aufrufe (Anthropic, services/ai.py) dürfen NUR Leistungsdaten – niemals Kunden-, Objekt-, Adress- oder Personendaten; kein Audio (Whisper läuft lokal). FinTS-Kontoanbindung ist rein lesend.

VERBINDLICHE REGELN FÜR DICH:
1. NUR LESEN. Ändere keine Datei, lege keine Dateien im Projekt an, führe kein migrate/makemigrations ohne --check/--dry-run aus, führe nichts aus, das data/sedo_web.sqlite3 (LIVE-Kundendaten!) oder media/ verändert. Die Test-Suite (.venv/bin/python manage.py test sedo, ggf. mit DJANGO_ALLOWED_HOSTS=testserver) darf laufen – sie nutzt eine eigene Test-DB.
2. Keine Kunden-/Personendaten aus Datenbank oder Dateien in deine Ausgabe übernehmen.
3. Jeder Fund braucht: Datei + Zeilennummer, wörtlichen Code-Beleg, ein KONKRETES Fehlerszenario (Eingabe/Zustand → falsches Ergebnis), Auswirkung, Fix-Vorschlag, Schwere, Aufwand.
4. Nur belegte Funde, die du im Code tatsächlich nachgelesen hast. Keine Stilkritik ohne Auswirkung, keine Spekulation, keine Wiederholung desselben Musters an 10 Stellen (dann EIN Fund mit Stellenliste).
5. Schwere: kritisch = Datenverlust/GoBD-Verstoß/Sicherheitslücke mit realem Angriffspfad; hoch = falsche Rechnungen/Beträge/XML oder ausnutzbare Schwäche; mittel = Fehlverhalten in realistischen Randfällen; niedrig = Robustheit/Klarheit mit geringer Auswirkung.
6. Ausgabe auf Deutsch. Dateipfade relativ zum Projektordner (z. B. sedo/views.py).
`

const FINDINGS = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      title: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, category: { type: 'string' },
      severity: { type: 'string', enum: ['kritisch', 'hoch', 'mittel', 'niedrig'] },
      description: { type: 'string' }, evidence: { type: 'string' }, scenario: { type: 'string' },
      impact: { type: 'string' }, fix: { type: 'string' }, effort: { type: 'string', enum: ['S', 'M', 'L'] },
    }, required: ['title', 'file', 'line', 'category', 'severity', 'description', 'evidence', 'scenario', 'impact', 'fix', 'effort'] } },
    coverage_note: { type: 'string' },
  },
  required: ['findings', 'coverage_note'],
}

const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: { type: 'array', items: { type: 'object', properties: {
      index: { type: 'integer' },
      refuted: { type: 'boolean' },
      confidence: { type: 'string', enum: ['hoch', 'mittel', 'niedrig'] },
      reasoning: { type: 'string' },
      corrected_severity: { type: ['string', 'null'], enum: ['kritisch', 'hoch', 'mittel', 'niedrig', null] },
    }, required: ['index', 'refuted', 'confidence', 'reasoning', 'corrected_severity'] } },
  },
  required: ['verdicts'],
}

const DIMENSIONEN = {
  'sicherheit-web': 'Dimension: WEB-SICHERHEIT. Prüfe sedo/views.py, sedo/urls.py, sedo/middleware.py, config/settings.py, sedo/templates/**: Sind ALLE Views login-geschützt? Mutierende Aktionen nur per POST + CSRF? AJAX-/API-Endpunkte – Auth, Größenlimits? Datei-Uploads – Typ-/Größenprüfung, Path-Traversal, Download-Endpunkte? Open Redirect? XSS durch |safe, mark_safe, Werte in <script>? Security-Header/Cookies, DEBUG-Default, ALLOWED_HOSTS, Admin-Site.',
  'sicherheit-secrets-banking': 'Dimension: SECRETS & BANKING. Prüfe services/banking.py (FinTS), services/mail.py, services/ai.py, backup_utils.py, settings.py, AppSetting: Speicherung/Logging/Ausgabe von Bank-Zugangsdaten, TAN, SMTP-Passwort, Claude-Key; Backup-Bereinigung vollständig? FinTS wirklich nur lesend? .env-Loader, SECRET_KEY-Datei, Backup-Rechte/Scheduler.',
  'datenschutz-ki': 'Dimension: DATENSCHUTZREGEL KI. Verfolge jeden Payload an api.anthropic.com (services/ai.py, views ai_*, offer_ai.js, magic_tone.js, plausibility.js): Können Kunden-/Objekt-/Personendaten in die Cloud gelangen? Audio-Löschung in transcription.py? Prompt-Injection über Katalogtexte? KI-Ausgaben ungeprüft in Beträge?',
  'gobd-geschaeftslogik': 'Dimension: GoBD & GESCHÄFTSLOGIK. Prüfe services/numbering.py, calc.py, invoices.py, dunning.py, payments.py, models.py und die Rechnungs-/Zahlungs-Views: Nummernkreise lückenlos/kollisionsfrei? Soft-Delete vollständig (Rückwärtsrelationen)? Statusübergänge, Unveränderbarkeit versendeter Rechnungen, Zahlungen (Überzahlung/negativ/storniert), Storno/Korrektur, Mahnstufen, Rundung/Decimal, Fälligkeit, Monatsläufe idempotent?',
  'zugferd-xml-pdf': 'Dimension: ZUGFeRD/EN16931 & PDF. Prüfe sedo/core/rechnungslauf_core.py (build_xml, create_pdf, embed_xml_into_pdf, unece_unit_code, D()) und sedo/services/invoices.py (invoice_to_core_row, generate_invoice_files): Profil-/GuidelineID korrekt? Pflichtfelder EN16931 (BT-1, BT-2 Datum Format 102, BT-3 TypeCode, BT-5 Währung, BT-27/31 Verkäufer inkl. USt-ID, BT-44 Käufer, BT-9 Fälligkeit, BT-20 Zahlungsbedingungen, BT-84 IBAN, Steuerkategorie S + Rate, Summen BT-106..115 konsistent: LineTotal = Summe Positionen, TaxBasis, TaxTotal, GrandTotal, TotalPrepaid, DuePayable)? Rundung Position vs. Gesamt? Storno 457: negative Mengen, BT-25 Referenz? Korrektur 384? Mehrere Steuersätze, 0 %/§19? Leistungszeitraum BT-73/74? BT-130 unitCode-Mapping valide? BT-154 Description. ShipTo-Party (Aareon: ID/Name/Adresse), Notes-Format. XML-Escaping/Encoding (&, <, Umlaute), Längenlimits. PDF/A-3-Einbettung: AFRelationship, XMP (fx:ConformanceLevel, DocumentType, Version), Dateiname factur-x.xml, MIME. GiroCode: Betrag/IBAN? Entwurfskennzeichnung darf nicht in echte XML. Prüfe erzeugte Beispiel-XML unter media/generated/, falls vorhanden – nur lesen.',
  'funktionalitaet-views': 'Dimension: FUNKTIONALITÄT & ROBUSTHEIT DER VIEWS. Prüfe sedo/views.py komplett und sedo/forms.py: Fehlerpfade (404 statt 500?), Transaktionsgrenzen, Doppel-Submit, Formularvalidierung, Session-Nutzung, Redirects, Limits/Pagination, Datei-Existenz, Import-Robustheit, Monatsgrenzen/Zeitzonen, verschluckte Exceptions, N+1, tote Pfade, Inkonsistenzen Angebot vs. Rechnung.',
  'datenmodell-migrationen': 'Dimension: DATENMODELL & MIGRATIONEN. Prüfe sedo/models.py, migrations/, signals.py, admin.py, audit.py: Feldtypen/Precision, Constraints, Indexe, on_delete, Manager-Umgehungen, Migrationskonsistenz (makemigrations --check --dry-run), Datenmigrationen, Audit-Lücken.',
  'frontend-js-templates': 'Dimension: FRONTEND (JS & TEMPLATES). Prüfe sedo/static/sedo/*.js und sedo/templates/sedo/*.html: fetch-Fehlerbehandlung, CSRF, Race, Observer-Lecks, Zahlen-Parsing, |unlocalize-Lücken, type=number mit Komma, |safe, Doppel-Submit, URL-Namen, Barrierefreiheit, Cache-Busting; Start-/Desktop-Skripte grob.',
  'tests-konfig-deployment': 'Dimension: TESTS, KONFIGURATION & DEPLOYMENT. Test-Suite ausführen (DJANGO_ALLOWED_HOSTS=testserver,127.0.0.1), check --deploy, makemigrations --check; requirements (ungepinnt), Desktop-Bundle (DB-Speicherort, Updates), settings (DEBUG, Hosts, WhiteNoise, LOGGING, Cookies, CSRF_TRUSTED_ORIGINS), Backup-Scheduler/Restore, Testlücken, Doku-Drift.',
}

const GUTACHTER = [
  { key: 'reproduzieren', prompt: 'Rolle REPRODUZIEREN: Lies für JEDEN Fund die genannte Stelle und ihren Aufrufkontext selbst. Tritt das beschriebene Fehlerszenario mit diesem Code tatsächlich ein? Prüfe, ob eine vorgelagerte Prüfung, ein Decorator, eine Middleware, ein Manager oder Django selbst es bereits verhindert. Wenn du es nicht konkret nachvollziehen kannst: refuted=true. Korrigiere die Schwere, wenn sie im Projektkontext (2 Nutzer, Heimnetz + Tunnel, GoBD, Aareon) zu hoch oder zu niedrig ist.' },
  { key: 'skeptiker', prompt: 'Rolle SKEPTIKER: Deine Aufgabe ist, JEDEN Fund zu WIDERLEGEN. Suche aktiv nach Gründen, warum er falsch ist: existiert ein Test dafür (sedo/tests/)? Fängt Django/das ORM es ab? Ist die Zeilenangabe falsch und der Code sagt etwas anderes? Ist es eine bewusst dokumentierte Entscheidung (CHANGELOG.md, DEPLOYMENT_SECURITY_NOTES.md)? Bei Unsicherheit: refuted=true. Nur wenn du nach ehrlicher Suche keinen Widerlegungsgrund findest: refuted=false.' },
]

const modus = args && args.modus

if (modus === 'finden') {
  phase('Finden')
  const dim = args.dimension
  const bekannt = (args.bekannt || []).map(t => '- ' + t)
  const zusatz = bekannt.length ? '\n\nBEREITS BEKANNTE FUNDE (nicht erneut melden, sondern gezielt Übersehenes suchen):\n' + bekannt.join('\n') : ''
  const r = await agent(REGELN + '\n\n' + DIMENSIONEN[dim] + zusatz + '\n\nGib alle Funde strukturiert zurück; in coverage_note: was du geprüft hast und was du NICHT prüfen konntest.',
    { label: 'pruefen:' + dim, phase: 'Finden', schema: FINDINGS, effort: 'medium' })
  return { modus, dimension: dim, findings: r ? r.findings : [], coverage_note: r ? r.coverage_note : 'PRÜFER FEHLGESCHLAGEN' }
}

if (modus === 'verifizieren') {
  phase('Verifizieren')
  const dim = args.dimension
  const funde = args.funde || []
  const liste = funde.map((f, i) => '### Fund ' + i + '\n' + JSON.stringify({
    title: f.title, file: f.file, line: f.line, severity: f.severity, description: f.description,
    evidence: f.evidence, scenario: f.scenario, impact: f.impact, fix: f.fix }, null, 1)).join('\n\n')
  const urteile = await parallel(GUTACHTER.map(g => () =>
    agent(REGELN + '\n\nDimension: ' + dim + '. Du beurteilst ' + funde.length + ' Funde.\n\n' + g.prompt +
      '\n\nWICHTIG: Liefere für JEDEN Index 0..' + (funde.length - 1) + ' genau ein Urteil (index, refuted, confidence, reasoning mit Zeilenbezug, corrected_severity oder null). Lies die betroffenen Dateien vollständig, statt Zeile für Zeile zu raten.\n\n' + liste,
      { label: 'gutachten:' + g.key + ':' + dim, phase: 'Verifizieren', schema: VERDICTS, effort: 'medium' })))
  const byIdx = urteile.map(u => {
    const m = {}
    if (u && Array.isArray(u.verdicts)) u.verdicts.forEach(v => { m[v.index] = v })
    return m
  })
  const rang = { kritisch: 4, hoch: 3, mittel: 2, niedrig: 1 }
  const ergebnis = funde.map((f, i) => {
    const votes = GUTACHTER.map((g, gi) => byIdx[gi][i] ? ({ gutachter: g.key, ...byIdx[gi][i] }) : null).filter(Boolean)
    const nicht_widerlegt = votes.filter(v => !v.refuted).length
    const status = votes.length === 0 ? 'unklar' : (nicht_widerlegt === votes.length ? 'bestaetigt' : (nicht_widerlegt === 0 ? 'verworfen' : 'unklar'))
    const sevs = votes.map(v => v.corrected_severity).filter(Boolean)
    let severity = f.severity
    if (sevs.length === 2 && sevs[0] === sevs[1]) severity = sevs[0]
    else if (sevs.length) { const s = sevs.slice().sort((a, b) => rang[a] - rang[b])[0]; severity = rang[s] < rang[f.severity] ? s : f.severity }
    return { ...f, dimension: dim, status, severity_final: severity, votes }
  })
  log(dim + ': ' + ergebnis.filter(e => e.status === 'bestaetigt').length + ' bestätigt, ' +
      ergebnis.filter(e => e.status === 'unklar').length + ' unklar, ' + ergebnis.filter(e => e.status === 'verworfen').length + ' verworfen')
  return { modus, dimension: dim, ergebnis }
}

if (modus === 'synthese') {
  phase('Synthese')
  const b = args.bestaetigt || [], u = args.unklar || [], v = args.verworfen || [], cov = args.coverage || []
  const BERICHT = REGELN + '\n\nDu bist der Senior-Reviewer, der den Abschlussbericht schreibt. Unten: BESTÄTIGTE Funde (von zwei unabhängigen Gutachtern bestätigt), UNKLARE (Gutachter uneins – vom Hauptreviewer bereits nachentschieden, Feld "entscheidung") und VERWORFENE. Schreibe einen deutschen Markdown-Bericht mit genau dieser Struktur:\n\n' +
    '# Senior-Code-Review SEDO v2 – Fundliste\n**Stand / Umfang / Methode** (3 Zeilen)\n' +
    '## 1. Auf einen Blick\nTabelle: Schwere | Anzahl. Dann 5–8 Sätze Gesamteinschätzung und die 3 wichtigsten Baustellen.\n' +
    '## 2. Funde nach Schwere\nJe Fund ein Block (F-01…):\n### F-xx · [SCHWERE] · Kurztitel\n- **Wo:** datei:zeile\n- **Was:** 1–3 Sätze\n- **Beleg:** kurzes Codezitat\n- **Szenario:** konkret\n- **Auswirkung:**\n- **Fix:** konkreter Vorschlag\n- **Aufwand:** S/M/L · **Gutachter-Notiz:** 1 Satz\n' +
    'Fasse echte Duplikate/gleiche Muster zu EINEM Fund mit Stellenliste zusammen (Dimensionen überschneiden sich!). Erfinde nichts; lass nichts Bestätigtes weg.\n' +
    '## 3. Empfohlene Reihenfolge\nNummeriert: Quick-Wins mit hoher Wirkung zuerst, dann kritisch/hoch, je ein Halbsatz Begründung.\n' +
    '## 4. Geprüft und verworfen (Transparenz)\nKompakte Tabelle: Titel | Datei | Warum verworfen.\n\n' +
    'BESTÄTIGT:\n' + JSON.stringify(b, null, 1) + '\n\nUNKLAR (nachentschieden):\n' + JSON.stringify(u, null, 1) + '\n\nVERWORFEN:\n' + JSON.stringify(v, null, 1)
  const KRITIK = REGELN + '\n\nDu bist der Vollständigkeits-Kritiker. Unten: Abdeckungsnotizen aller Prüfer und die bestätigten Fund-Titel. Was fehlt? Welche Module/Risikobereiche wurden nicht oder nur oberflächlich geprüft? Welche typischen Fehlerklassen (Django-Rechnungstool, ZUGFeRD, SQLite, FinTS, PyInstaller) tauchen nirgends auf? Prüfe 2–3 Lücken stichprobenartig selbst im Code (nur lesen). Gib deutsches Markdown zurück: "## 5. Abdeckung und offene Prüfbereiche" (geprüft / nicht geprüft / empfohlene Nachprüfung) und ggf. "### Zusatzfunde des Kritikers (UNVERIFIZIERT)" im Fund-Format.\n\n' +
    'ABDECKUNGSNOTIZEN:\n' + JSON.stringify(cov, null, 1) + '\n\nBESTÄTIGTE TITEL:\n' + b.map(f => '- ' + f.severity_final + ' · ' + f.file + ':' + f.line + ' · ' + f.title).join('\n')
  const out = await parallel([
    () => agent(BERICHT, { label: 'synthese:bericht', phase: 'Synthese', effort: 'high' }),
    () => agent(KRITIK, { label: 'synthese:kritik', phase: 'Synthese', effort: 'high' }),
  ])
  return { modus, bericht_md: out[0] || '', kritik_md: out[1] || '' }
}

if (modus === 'synthese_datei') {
  // Große Eingaben: Agenten lesen die JSON-Datei selbst und schreiben den Bericht direkt.
  phase('Synthese')
  const datei = args.datei
  const berichtPfad = args.bericht_pfad
  const kritikPfad = args.kritik_pfad
  const ausnahme = function (pfad) { return '\n\nAUSNAHME VON REGEL 1: Du DARFST genau eine Datei schreiben: "' + pfad + '". Nichts anderes anlegen oder ändern.' }
  const BERICHT = REGELN + ausnahme(berichtPfad) +
    '\n\nDu bist der Senior-Reviewer, der den Abschlussbericht schreibt. Lies zuerst vollständig die Datei "' + datei + '" (JSON mit Schlüsseln bestaetigt, unklar, verworfen, coverage). Die bestätigten Funde wurden von je zwei unabhängigen Gutachtern bestätigt; strittige Funde tragen im Feld "entscheidung" das Urteil des Hauptreviewers (Feld "dimension" nennt die Prüfdimension; severity_final ist die verbindliche Schwere).\n\n' +
    'Schreibe einen deutschen Markdown-Bericht in die Datei "' + berichtPfad + '" mit genau dieser Struktur:\n\n' +
    '# Senior-Code-Review SEDO v2 – Fundliste\n**Stand / Umfang / Methode** (3 Zeilen: September 2026, ~7.500 Zeilen Python + Templates/JS, 9 Prüfdimensionen, je Fund 2 unabhängige Gutachter, strittige Fälle vom Hauptreviewer entschieden)\n' +
    '## 1. Auf einen Blick\nTabelle: Schwere | Anzahl (nach Zusammenführung von Duplikaten!). Dann 6–10 Sätze Gesamteinschätzung: Wo steht der Code, was sind die 3–4 wichtigsten Baustellen (Themencluster, nicht Einzelfunde).\n' +
    '## 2. Funde nach Schwere\nZuerst ALLE hoch, dann mittel, dann niedrig. Je Fund ein Block (F-01, F-02, …):\n### F-xx · [SCHWERE] · Kurztitel\n- **Wo:** datei:zeile (bei zusammengeführten Funden: alle Stellen)\n- **Was:** 1–3 Sätze\n- **Beleg:** kurzes Codezitat\n- **Szenario:** konkret\n- **Auswirkung:** 1 Satz\n- **Fix:** konkreter Vorschlag\n- **Aufwand:** S/M/L · **Gutachter-Notiz:** 1 Satz (z. B. Schwere korrigiert, weil …)\n' +
    'WICHTIG: Die Dimensionen überschneiden sich – dieselbe Ursache taucht mehrfach auf (z. B. Status "korrigiert" wird von Zahlung/Versand/OP-GET überschrieben; Storno-Sperren; makemigrations in Startskripten; Backup-Themen; Open Redirect via next; Freitext an die KI). Führe echte Duplikate/gleiche Ursache zu EINEM Fund mit Stellenliste zusammen und nenne die Dimensionen, aus denen er stammt. Erfinde nichts dazu; lass keinen bestätigten Fund weg (zusammengeführt zählt als enthalten).\n' +
    '## 3. Empfohlene Reihenfolge\nNummerierte Liste in 4 Wellen: (1) Quick-Wins mit hoher Wirkung (S-Aufwand, hoch/mittel), (2) die hoch-Funde, (3) Konzeptthemen (Status-Trennung, Entwurf-Flow, Backup/Restore, Redaktion vor KI), (4) Rest. Je Punkt ein Halbsatz Begründung und Verweis auf F-Nummern.\n' +
    '## 4. Geprüft und verworfen (Transparenz)\nKompakte Tabelle: Titel | Datei | Warum verworfen (1 Halbsatz).\n\n' +
    'Schreibe die Datei vollständig (kein Abschneiden, alle Funde). Gib am Ende NUR eine kurze Zusammenfassung zurück: Anzahl Funde je Schwere nach Zusammenführung und die Pfadangabe.'
  const KRITIK = REGELN + ausnahme(kritikPfad) +
    '\n\nDu bist der Vollständigkeits-Kritiker. Lies die Datei "' + datei + '" – vor allem "coverage" (Abdeckungsnotizen aller Prüfer) und die Titel der bestätigten Funde. Fragen: Was fehlt? Welche Module/Risikobereiche wurden laut Notizen NICHT oder nur oberflächlich geprüft? Welche typischen Fehlerklassen (Django-Rechnungstool, ZUGFeRD, SQLite, FinTS, PyInstaller-Desktop) tauchen weder in Funden noch in Notizen auf? Prüfe 2–3 solcher Lücken stichprobenartig selbst im Code (nur lesen) und melde konkrete Zusatzfunde, falls du welche findest.\n\n' +
    'Schreibe deutsches Markdown in die Datei "' + kritikPfad + '" mit: "## 5. Abdeckung und offene Prüfbereiche" (Liste: geprüft / nicht geprüft / empfohlene Nachprüfung) und ggf. "### Zusatzfunde des Kritikers (UNVERIFIZIERT)" im Fund-Format (Wo/Was/Beleg/Szenario/Fix). Gib am Ende NUR eine kurze Zusammenfassung zurück.'
  const out = await parallel([
    () => agent(BERICHT, { label: 'synthese:bericht', phase: 'Synthese', effort: 'high' }),
    () => agent(KRITIK, { label: 'synthese:kritik', phase: 'Synthese', effort: 'high' }),
  ])
  return { modus, bericht_summary: out[0] || '', kritik_summary: out[1] || '' }
}

return { fehler: 'Unbekannter Modus: ' + modus }
