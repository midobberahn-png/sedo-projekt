"""F-18: Originale lesen, versiegeln und aus identischen Sicherungsbytes ergänzen.

Keine Neuberechnung und kein Generator in diesem Modul. Die vorhandene Audit-
Tabelle hält flache Prüfsummen-Metadaten; Altbelege werden nicht rückwirkend als
prüfsummengesichert ausgegeben. Kein Schutz vor direktem DB-/Dateisystemzugriff.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q

from ..audit import write_audit
from ..models import AuditLog, Invoice
from .invoice_files import datei_rueckweg, veroeffentliche_neue_datei
from .mail import EmailAttachment
from .workflow_errors import BelegArchivFehler

ARCHIV_FELDER = ('pdf_file', 'xml_file', 'zugferd_file')
ARCHIV_VORGANG = 'belegarchiv_erstellt'


def _fehler(invoice, text):
    return BelegArchivFehler(
        f'Belegarchiv {invoice.number}: {text} '
        'Keine Neuerzeugung aus aktuellen Stammdaten. Originale aus einer '
        'Sicherung wiederherstellen; fachliche Änderungen über eine Korrekturrechnung.')


def sicherer_pfad(root: Path, name: str) -> Path:
    """Relative, reguläre Pfade ohne Traversal oder symbolische Verknüpfungen."""
    root = root.resolve()
    rel = Path(name)
    if not name or rel.is_absolute() or '..' in rel.parts or '\\' in name or '\x00' in name:
        raise BelegArchivFehler('Ungültiger Archivpfad.')
    path = root / rel
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise BelegArchivFehler('Symbolische Verknüpfung im Archivpfad ist nicht zulässig.')
    if not path.resolve().is_relative_to(root):
        raise BelegArchivFehler('Archivpfad liegt außerhalb des Medienordners.')
    return path


def zielpfade(invoice) -> dict[str, Path]:
    number = invoice.number
    if not number or number in ('.', '..') or '/' in number or '\\' in number:
        raise BelegArchivFehler('Ungültige Rechnungsnummer für den Archivpfad.')
    names = (f'{number}.pdf', 'factur-x.xml', f'{number}_zugferd.pdf')
    return {field: sicherer_pfad(Path(settings.MEDIA_ROOT), f'generated/{number}/{name}')
            for field, name in zip(ARCHIV_FELDER, names)}


def archivnachweis(invoice) -> dict | None:
    entries = list(AuditLog.objects.filter(
        object_type='Invoice', object_id=str(invoice.pk),
        metadata__vorgang=ARCHIV_VORGANG).values_list('metadata', flat=True)[:2])
    if not entries:
        return None
    if len(entries) != 1:
        raise _fehler(invoice, 'Mehrdeutiger Archivnachweis.')
    proof = entries[0]
    if proof.get('schema') != 1 or proof.get('nummer') != invoice.number:
        raise _fehler(invoice, 'Archivnachweis passt nicht zum Beleg.')
    for field in ARCHIV_FELDER:
        if (not isinstance(proof.get(field), str) or not proof[field]
                or not isinstance(proof.get(field + '_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', proof[field + '_sha256'])
                or type(proof.get(field + '_bytes')) is not int or proof[field + '_bytes'] <= 0):
            raise _fehler(invoice, 'Unvollständiger Archivnachweis.')
    return proof


def _frisch(invoice):
    invoice.refresh_from_db()
    if invoice.deleted_at is not None:
        raise _fehler(invoice, 'Der Beleg ist ausgeblendet.')


def _datei_lesen(invoice, field, proof):
    if field not in ARCHIV_FELDER:
        raise ValueError('Unbekannte Archivdatei.')
    name = getattr(invoice, field).name
    if not name or (proof and name != proof[field]):
        raise _fehler(invoice, f'Dateiverweis für {field} fehlt oder wurde verändert.')
    path = sicherer_pfad(Path(settings.MEDIA_ROOT), name)
    try:
        if not path.is_file():
            raise OSError('Keine reguläre Datei')
        data = path.read_bytes()
    except OSError as exc:
        raise _fehler(invoice, f'Originaldatei {field} fehlt oder ist nicht lesbar.') from exc
    if not data:
        raise _fehler(invoice, f'Originaldatei {field} ist leer.')
    if proof and (len(data) != proof[field + '_bytes'] or
                  hashlib.sha256(data).hexdigest() != proof[field + '_sha256']):
        raise _fehler(invoice, f'Prüfsumme der Originaldatei {field} stimmt nicht überein.')
    # Versand und ZIP verwenden genau diese einmal gelesenen/geprüften Bytes,
    # nicht einen Pfad, dessen Inhalt zwischen Prüfung und Versand wechseln kann.
    return EmailAttachment(path.name, data)


def lese_archivdatei(invoice, field='zugferd_file') -> EmailAttachment:
    """Benötigtes Original lesen, niemals erzeugen. Auch korrigierte bleiben lesbar."""
    _frisch(invoice)
    if invoice.lifecycle == 'draft' or invoice.number.startswith('EN'):
        raise _fehler(invoice, 'Ein Entwurf ist kein ausgestelltes Original.')
    return _datei_lesen(invoice, field, archivnachweis(invoice))


def pruefe_archiv(invoice) -> dict:
    """Lesender UI-/Servicebericht. Kein neues Siegel für historische Dateien."""
    _frisch(invoice)
    errors = []
    proof = None
    try:
        proof = archivnachweis(invoice)
        for field in ARCHIV_FELDER:
            try:
                _datei_lesen(invoice, field, proof)
            except BelegArchivFehler as exc:
                errors.append(str(exc))
    except BelegArchivFehler as exc:
        errors.append(str(exc))
    return {'fehler': errors, 'vollstaendig': not errors, 'mit_pruefsummen': bool(proof)}


def pruefe_schreibziele(invoice, *, erstmalig: bool) -> None:
    """Vor jeder schreibenden Erzeugung, auch vor Backup-/Quarantäneoperationen."""
    if archivnachweis(invoice) is not None:
        raise _fehler(invoice, 'Bereits ausgestellte Dateien sind schreibgeschützt.')
    targets = zielpfade(invoice)
    refs = [getattr(invoice, f).name for f in ARCHIV_FELDER if getattr(invoice, f).name]
    paths = [*targets.values(), *(sicherer_pfad(Path(settings.MEDIA_ROOT), n) for n in refs)]
    names = {p.relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix() for p in paths}
    # Auch Entwürfe dürfen nicht über fehlerhafte Dateiverweise fremde Originale
    # verschieben. Ausgeblendete Belege zählen ausdrücklich mit.
    query = Q()
    seals = Q()
    for field in ARCHIV_FELDER:
        query |= Q(**{field + '__in': names})
        seals |= Q(**{'metadata__' + field + '__in': names})
    if (Invoice.all_objects.exclude(pk=invoice.pk).filter(query).exists()
            or AuditLog.objects.filter(object_type='Invoice', metadata__vorgang=ARCHIV_VORGANG)
            .exclude(object_id=str(invoice.pk)).filter(seals).exists()):
        raise _fehler(invoice, 'Ein Schreibziel gehört zu einem anderen Beleg.')
    if erstmalig:
        if invoice.status != 'erstellt' or invoice.number.startswith('EN'):
            raise _fehler(invoice, 'Erstausstellung ist nur im Erstellungs-/Finalisierungsvorgang zulässig.')
        if refs or any(p.exists() for p in targets.values()):
            raise _fehler(invoice, 'Vorhandene Dateien oder Dateiverweise verhindern eine Erstausstellung.')


def versiegele_archiv(invoice, request=None) -> None:
    metadata = {'vorgang': ARCHIV_VORGANG, 'schema': 1, 'nummer': invoice.number}
    for field in ARCHIV_FELDER:
        document = _datei_lesen(invoice, field, None)
        metadata[field] = getattr(invoice, field).name
        metadata[field + '_sha256'] = hashlib.sha256(document.data).hexdigest()
        metadata[field + '_bytes'] = len(document.data)
    # Flache Werte bleiben in audit._json_sicher unverändert (kein gekürztes
    # verschachteltes Manifest). Ein Fehlschlag verhindert die Ausstellung.
    write_audit(request, 'generate', invoice, 'Originaldateien mit SHA-256 archiviert',
                metadata=metadata, pflicht=True)


def stelle_archiv_wieder_her(invoice, quelle: Path, *, anwenden=False, request=None) -> dict:
    """Nur fehlende Originalbytes/Verweise ergänzen; keine vorhandene Datei ersetzen.

    Die Quelle enthält die relative Medienstruktur. Ohne vorbestehende Prüfsummen
    ist keine automatische Echtheitsentscheidung möglich; Altbelege bleiben gesperrt.
    """
    quelle = Path(quelle).resolve()

    def vorbereiten():
        _frisch(invoice)
        proof = archivnachweis(invoice)
        if not proof or invoice.lifecycle == 'draft':
            raise _fehler(invoice, 'Für diese Wiederherstellung fehlt ein ausgestellter Prüfsummennachweis.')
        missing, links = {}, []
        for field in ARCHIV_FELDER:
            name = getattr(invoice, field).name
            if name and name != proof[field]:
                raise _fehler(invoice, f'Abweichender Dateiverweis {field}; keine automatische Änderung.')
            target = sicherer_pfad(Path(settings.MEDIA_ROOT), proof[field])
            source = target if target.exists() else sicherer_pfad(quelle, proof[field])
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise _fehler(invoice, f'Original {field} fehlt auch in der gewählten Sicherung.') from exc
            if (len(data) != proof[field + '_bytes'] or
                    hashlib.sha256(data).hexdigest() != proof[field + '_sha256']):
                raise _fehler(invoice, f'Original-/Sicherungsbytes für {field} weichen vom Archivnachweis ab.')
            if not target.exists():
                missing[field] = (target, data)
            if not name:
                links.append(field)
        return proof, missing, links

    proof, missing, links = vorbereiten()
    if not anwenden or not (missing or links):
        return {'modus': 'anwenden' if anwenden else 'trockenlauf', 'nummer': invoice.number,
                'fehlende_dateien': list(missing), 'fehlende_verweise': links, 'uebernommen': False}
    with datei_rueckweg() as merken, transaction.atomic():
        if not Invoice.objects.filter(pk=invoice.pk).update(updated_at=F('updated_at')):
            raise _fehler(invoice, 'Der Beleg ist nicht mehr verfügbar.')
        proof, missing, links = vorbereiten()
        # Fehlende Verweise zur Erfassung der Rückweg-Ziele nur am Pythonobjekt
        # ergänzen. Die DB-Änderung folgt erst nach erfolgreicher Byteübernahme.
        for field in links:
            getattr(invoice, field).name = proof[field]
        merken(invoice)
        root = Path(settings.MEDIA_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, prefix='.restoring_') as tmp:
            for field, (target, data) in missing.items():
                staged = Path(tmp) / field
                staged.write_bytes(data)
                target.parent.mkdir(parents=True, exist_ok=True)
                veroeffentliche_neue_datei(staged, target)
        if links:
            invoice.save(update_fields=links + ['updated_at'])
        if missing or links:
            write_audit(request, 'update', invoice, 'Fehlende Originaldateien aus Sicherung wiederhergestellt',
                        metadata={'vorgang': 'belegarchiv_wiederhergestellt', 'nummer': invoice.number,
                                  'dateien': ','.join(missing), 'verweise': ','.join(links)}, pflicht=True)
    return {'modus': 'anwenden', 'nummer': invoice.number, 'fehlende_dateien': list(missing),
            'fehlende_verweise': links, 'uebernommen': bool(missing or links)}
