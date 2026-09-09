"""FinTS-Kontoanbindung (v2.3): Umsätze direkt bei der Bank abrufen.

Nutzt python-fints (FinTS 3.0 PIN/TAN). Der Abruf ist REIN LESEND
(Kontoumsätze) – es werden niemals Überweisungen oder andere Aufträge
ausgeführt. Die Umsätze laufen anschließend durch dieselbe Import-Pipeline
wie der Datei-Import (``import_bank_json``), inklusive Doubletten-Schutz über
die Bank-Referenz als ``external_id`` und automatischer Rechnungszuordnung.

Sicherheits-Zuschnitt:
- Die PIN wird bevorzugt NUR für den Moment des Abrufs eingegeben. Optional
  kann sie – wie SMTP-Passwort und Claude-Key – lokal gespeichert werden;
  Backups bereinigen sie dann (``SECRET_SETTING_KEYS``), und die
  Env-Variable ``SEDO_FINTS_PIN`` hat Vorrang vor der Datenbank.
- PSD2: Je nach Bank verlangt der Abruf eine TAN-Freigabe (z. B. pushTAN in
  der Banking-App). Entkoppelte Verfahren werden durch Warten/Nachfragen
  unterstützt; klassische TAN-Eingabe meldet sich mit einer klaren Meldung.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import date, timedelta
from decimal import Decimal

from ..models import AppSetting
from .payments import import_bank_json

logger = logging.getLogger('sedo')

# Produktkennung der Deutschen Kreditwirtschaft. python-fints bringt eine
# Test-Registrierung mit; für den Dauerbetrieb sollte eine eigene (kostenlose)
# DK-Produktregistrierung eingetragen werden: Einstellungen → Bankkonto.
DEFAULT_PRODUCT_ID = None  # None → python-fints-Standard

# Wie lange auf die pushTAN-Freigabe in der Banking-App gewartet wird.
DECOUPLED_WAIT_SECONDS = 90
DECOUPLED_POLL_INTERVAL = 3


class BankingError(Exception):
    """Verständliche Fehlermeldung für die Oberfläche."""


class TanConfirmationPending(BankingError):
    """Die Bank wartet auf eine Freigabe (z. B. pushTAN), die nicht kam."""


def get_banking_config() -> dict:
    return {
        'blz': AppSetting.get('fints_blz', ''),
        'endpoint': AppSetting.get('fints_endpoint', ''),
        'login': AppSetting.get('fints_login', ''),
        # Env-Variable hat Vorrang – dann liegt keine PIN in der Datenbank.
        'pin': os.environ.get('SEDO_FINTS_PIN', '') or AppSetting.get('fints_pin', ''),
        'iban': AppSetting.get('fints_iban', ''),
        'product_id': AppSetting.get('fints_product_id', ''),
    }


def banking_is_configured() -> bool:
    cfg = get_banking_config()
    return bool(cfg['blz'] and cfg['endpoint'] and cfg['login'])


def _stable_external_id(entry: dict) -> str:
    """Stabile Kennung, falls die Bank keine Referenz liefert."""
    raw = '|'.join(str(entry.get(k, '')) for k in ('datum', 'betrag', 'name', 'verwendungszweck'))
    return 'FINTS-' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _normalize_mt940(transactions) -> list[dict]:
    """mt940-Umsätze → Eingabeformat der bestehenden Import-Pipeline."""
    rows = []
    for t in transactions:
        data = getattr(t, 'data', {}) or {}
        amount_obj = data.get('amount')
        amount = getattr(amount_obj, 'amount', None)
        if amount is None:
            continue
        booking = data.get('entry_date') or data.get('date')
        entry = {
            # ISO-String statt date-Objekt: landet als Rohdatum im JSONField
            # der Zahlung und muss serialisierbar sein.
            'datum': booking.isoformat() if hasattr(booking, 'isoformat') else str(booking or ''),
            'betrag': str(Decimal(str(amount))),
            'name': (data.get('applicant_name') or '').strip(),
            'verwendungszweck': (data.get('purpose') or '').strip(),
        }
        ext = (data.get('end_to_end_reference') or data.get('bank_reference') or '').strip()
        if ext.upper() in ('', 'NOTPROVIDED', 'NONREF'):
            ext = _stable_external_id(entry)
        entry['transaktions_id'] = ext
        rows.append(entry)
    return rows


def _await_decoupled(client, tan_request):
    """Wartet auf die Freigabe eines entkoppelten TAN-Verfahrens (pushTAN)."""
    deadline = time.monotonic() + DECOUPLED_WAIT_SECONDS
    last_exc = None
    while time.monotonic() < deadline:
        time.sleep(DECOUPLED_POLL_INTERVAL)
        try:
            result = client.send_tan(tan_request, None)
        except Exception as exc:  # Bank meldet „noch nicht freigegeben“ unterschiedlich
            last_exc = exc
            continue
        if not _is_tan_request(result):
            return result
    raise TanConfirmationPending(
        'Die Freigabe in der Banking-App ist nicht angekommen. Bitte die '
        'Freigabe bestätigen und den Abruf erneut starten.'
    ) from last_exc


def _is_tan_request(obj) -> bool:
    return obj is not None and obj.__class__.__name__ in (
        'NeedTANResponse', 'NeedRetryResponse')


def fetch_bank_transactions(days: int = 30, pin: str | None = None) -> dict:
    """Ruft Umsätze der letzten ``days`` Tage ab und importiert sie.

    Gibt das Ergebnis-Dict von ``import_bank_json`` zurück (imported/matched/
    unclear/ignored/unassigned). Wirft ``BankingError`` mit verständlicher
    Meldung, wenn etwas fehlt oder die Bank ablehnt.
    """
    cfg = get_banking_config()
    if not banking_is_configured():
        raise BankingError('Bankzugang unvollständig – bitte in den Einstellungen '
                           'BLZ, FinTS-Adresse und Anmeldename hinterlegen.')
    use_pin = (pin or '').strip() or cfg['pin']
    if not use_pin:
        raise BankingError('Bitte die Online-Banking-PIN eingeben (sie wird nur '
                           'für diesen Abruf verwendet, sofern nicht gespeichert).')

    try:
        from fints.client import FinTS3PinTanClient
    except ImportError as exc:
        raise BankingError('Das FinTS-Modul ist nicht installiert '
                           '(pip install fints).') from exc

    kwargs = {}
    if cfg['product_id']:
        kwargs['product_id'] = cfg['product_id']

    try:
        client = FinTS3PinTanClient(cfg['blz'], cfg['login'], use_pin,
                                    cfg['endpoint'], **kwargs)
        with client:
            init = getattr(client, 'init_tan_response', None)
            if _is_tan_request(init):
                if getattr(init, 'decoupled', False):
                    logger.info('FinTS: warte auf pushTAN-Freigabe …')
                    _await_decoupled(client, init)
                else:
                    raise BankingError(
                        'Die Bank verlangt eine TAN-Eingabe '
                        f'({getattr(init, "challenge", "TAN erforderlich")}). '
                        'Dieses TAN-Verfahren wird noch nicht unterstützt – bitte in '
                        'der Banking-App auf ein Push-Verfahren umstellen.')

            accounts = client.get_sepa_accounts()
            if _is_tan_request(accounts):
                raise BankingError('Die Bank verlangt eine zusätzliche Freigabe für die '
                                   'Kontenliste – bitte erneut versuchen.')
            if not accounts:
                raise BankingError('Die Bank hat keine Konten zurückgemeldet.')
            account = accounts[0]
            if cfg['iban']:
                wanted = cfg['iban'].replace(' ', '').upper()
                for a in accounts:
                    if (a.iban or '').replace(' ', '').upper() == wanted:
                        account = a
                        break
                else:
                    raise BankingError(f'Konto mit IBAN {cfg["iban"]} wurde bei der '
                                       f'Bank nicht gefunden.')

            start = date.today() - timedelta(days=max(1, min(days, 365)))
            transactions = client.get_transactions(account, start, date.today())
            if _is_tan_request(transactions):
                if getattr(transactions, 'decoupled', False):
                    transactions = _await_decoupled(client, transactions)
                else:
                    raise BankingError('Die Bank verlangt für den Umsatzabruf eine '
                                       'TAN-Eingabe – dieses Verfahren wird noch nicht '
                                       'unterstützt (Push-Verfahren nutzen).')
    except BankingError:
        raise
    except Exception as exc:
        logger.exception('FinTS-Abruf fehlgeschlagen')
        raise BankingError(f'Bankabruf fehlgeschlagen: {exc}') from exc

    rows = _normalize_mt940(transactions)
    result = import_bank_json(rows)
    result['abgerufen'] = len(rows)
    return result
