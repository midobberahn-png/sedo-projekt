"""FinTS-Kontoanbindung (v2.3): Umsätze direkt bei der Bank abrufen.

Nutzt python-fints (FinTS 3.0 PIN/TAN). Der Abruf ist REIN LESEND
(Kontoumsätze) – es werden niemals Überweisungen oder andere Aufträge
ausgeführt. Die Umsätze laufen anschließend durch dieselbe Import-Pipeline
wie der Datei-Import (``import_bank_json``), inklusive Doubletten-Schutz über
die Bank-Referenz als ``external_id`` und automatischer Rechnungszuordnung.

Sicherheits-Zuschnitt:
- Die PIN wird bevorzugt NUR für den Moment des Abrufs eingegeben. Optional
  wird sie lokal verschlüsselt und an Bankadresse/Anmeldename gebunden gespeichert;
  Backups bereinigen sie dann (``SECRET_SETTING_KEYS``), und die
  Env-Variable ``SEDO_FINTS_PIN`` hat Vorrang vor der Datenbank.
- PSD2: Je nach Bank verlangt der Abruf eine TAN-Freigabe (z. B. pushTAN in
  der Banking-App). Entkoppelte Verfahren werden durch Warten/Nachfragen
  unterstützt; klassische TAN-Eingabe meldet sich mit einer klaren Meldung.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from contextlib import contextmanager
import requests
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import AppSetting
from .payments import import_bank_json
from .credentials import CredentialError, checked_fints_endpoint, configuration, secret_for, validate_configuration

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
    """Metadata only; a PIN is loaded only for an explicit bank operation."""
    return {**configuration('fints'), 'pin': ''}


class _FinTSSession(requests.Session):
    """Bound, verified HTTPS transport; never follow a PIN-bearing redirect."""
    MAX_RESPONSE = 8 * 1024 * 1024

    def __init__(self, endpoint):
        super().__init__()
        self.endpoint = checked_fints_endpoint(endpoint)
        self.trust_env = False

    def post(self, url, **kwargs):
        if checked_fints_endpoint(url) != self.endpoint:
            raise BankingError('Die Bankverbindung hat unerwartet ihr Ziel geändert.')
        kwargs.update(timeout=(5, 20), allow_redirects=False, verify=True, stream=True)
        started = time.monotonic()
        response = super().post(self.endpoint, **kwargs)
        try:
            if 300 <= response.status_code < 400:
                raise BankingError('Die Bankadresse leitet auf ein anderes Ziel um. Bitte die FinTS-Adresse prüfen.')
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                content.extend(chunk)
                if len(content) > self.MAX_RESPONSE or time.monotonic() - started > 30:
                    raise BankingError('Die Bankantwort ist zu groß oder hat zu lange gedauert.')
            response._content = bytes(content)
            response._content_consumed = True
            return response
        finally:
            response.close()


@contextmanager
def _bank_client(cfg, pin):
    from fints.client import FinTS3PinTanClient
    kwargs = {'product_id': cfg['product_id']} if cfg['product_id'] else {}
    client = FinTS3PinTanClient(cfg['blz'], cfg['login'], pin, cfg['endpoint'], **kwargs)
    client.connection.session.close()
    session = _FinTSSession(cfg['endpoint'])
    client.connection.session = session
    try:
        with client:
            yield client
    finally:
        session.close()


def _bank_credentials(cfg, pin):
    try:
        cfg = validate_configuration('fints', cfg)
        use_pin = (pin or '').strip() or secret_for('fints', cfg)
    except CredentialError as exc:
        raise BankingError(str(exc)) from None
    if not use_pin:
        raise BankingError('Bitte die Online-Banking-PIN für diesen Abruf eingeben oder verschlüsselt speichern.')
    return cfg, use_pin


def banking_is_configured() -> bool:
    cfg = get_banking_config()
    return bool(cfg['blz'] and cfg['endpoint'] and cfg['login'])


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
        # Gleiche Buchungsdaten beweisen keine identische Bankbuchung. Eine
        # daraus berechnete ID würde eine zweite echte Zahlung still verwerfen.
        # Ohne Bankkennung entscheidet der Import anhand der Anzahl und meldet
        # nicht unterscheidbare Wiederholungen ausdrücklich zur Klärung.
        references = (data.get('bank_reference'), data.get('end_to_end_reference'))
        entry['transaktions_id'] = next(
            (str(ref).strip() for ref in references
             if ref and str(ref).strip().upper() not in ('NOTPROVIDED', 'NONREF')),
            '',
        )
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
    cfg, use_pin = _bank_credentials(cfg, pin)

    try:
        with _bank_client(cfg, use_pin) as client:
            init = getattr(client, 'init_tan_response', None)
            if _is_tan_request(init):
                if getattr(init, 'decoupled', False):
                    logger.info('FinTS: warte auf pushTAN-Freigabe …')
                    _await_decoupled(client, init)
                else:
                    raise BankingError(
                        'Die Bank verlangt eine TAN-Eingabe. '
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
    except Exception:
        # Bank exceptions can contain PINs and raw protocol messages.
        logger.warning('FinTS-Abruf fehlgeschlagen; Zugang und Freigabe prüfen.')
        raise BankingError('Bankabruf fehlgeschlagen. Bitte Bankzugang und Freigabe prüfen.') from None

    rows = _normalize_mt940(transactions)
    result = import_bank_json(rows)
    result['abgerufen'] = len(rows)
    return result


def _balance_identity(cfg):
    # A cached balance must never appear under a different connection/account.
    values = [cfg.get(key, '') for key in ('blz', 'endpoint', 'login', 'iban', 'product_id')]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def cached_bank_balance(cfg=None):
    cfg = cfg or get_banking_config()
    try:
        saved = json.loads(AppSetting.get('bank_balance_snapshot', '{}'))
        if saved['identity'] != _balance_identity(cfg):
            return None
        amount = Decimal(saved['amount'])
        fetched = parse_datetime(saved['fetched_at'])
        if not amount.is_finite() or fetched is None or not re.fullmatch('[A-Z]{3}', saved['currency']):
            return None
        return dict(amount=amount, currency=saved['currency'], fetched_at=fetched,
                    balance_date=date.fromisoformat(saved['balance_date']),
                    account_label='Konto …' + saved['iban'][-4:])
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None


def fetch_bank_balance(pin=None):
    """Read the bank's booked balance on explicit request, without importing payments."""
    cfg = get_banking_config()
    if not all(cfg[key] for key in ('blz', 'endpoint', 'login')):
        raise BankingError('Bitte zuerst den Bankzugang unter Einstellungen → Bankkonto einrichten.')
    cfg, use_pin = _bank_credentials(cfg, pin)
    identity = _balance_identity(cfg)
    lock_key, token = 'dashboard-balance-' + identity, uuid.uuid4().hex
    if not cache.add(lock_key, token, timeout=240):
        raise BankingError('Ein Kontostand-Abruf läuft bereits. Bitte kurz warten.')
    try:
        def resolved(value):
            if _is_tan_request(value):
                if not getattr(value, 'decoupled', False):
                    raise BankingError('Die Bank verlangt eine TAN-Eingabe. Bitte ein Push-Verfahren verwenden.')
                return _await_decoupled(client, value)
            return value

        with _bank_client(cfg, use_pin) as client:
            resolved(getattr(client, 'init_tan_response', None))
            accounts = resolved(client.get_sepa_accounts())
            if not accounts:
                raise BankingError('Die Bank hat keine Konten zurückgemeldet.')
            wanted = cfg['iban'].replace(' ', '').upper()
            matches = [a for a in accounts if (a.iban or '').replace(' ', '').upper() == wanted] if wanted else accounts
            if len(matches) != 1:
                raise BankingError('Bitte in den Bank-Einstellungen die IBAN des gewünschten Kontos festlegen bzw. prüfen.')
            account = matches[0]
            balance = resolved(client.get_balance(account))
        amount = Decimal(str(balance.amount.amount))
        currency = str(balance.amount.currency).upper()
        if not amount.is_finite() or abs(amount) > Decimal('1e18') or not re.fullmatch('[A-Z]{3}', currency):
            raise BankingError('Die Bank hat keinen gültigen Kontostand geliefert.')
        balance_date = balance.date.isoformat()
        date.fromisoformat(balance_date)
        if _balance_identity(get_banking_config()) != identity:
            raise BankingError('Der Bankzugang wurde während des Abrufs geändert. Bitte erneut abrufen.')
        snapshot = {'identity': identity, 'amount': str(amount), 'currency': currency,
                    'balance_date': balance_date, 'fetched_at': timezone.now().isoformat(), 'iban': account.iban}
        AppSetting.set('bank_balance_snapshot', json.dumps(snapshot))
        return cached_bank_balance(cfg)
    except BankingError:
        raise
    except Exception as exc:
        # Bank responses can contain credentials; do not expose exception text.
        raise BankingError('Kontostand konnte nicht abgerufen werden. Bitte Bankzugang und Freigabe prüfen.') from exc
    finally:
        if cache.get(lock_key) == token:
            cache.delete(lock_key)
