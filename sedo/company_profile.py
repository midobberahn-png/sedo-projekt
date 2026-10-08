"""Validated local sender data; no business identity ships with the program.

An immutable context-local snapshot keeps PDF, XML and metadata consistent even
when an administrator edits the profile while a document is being generated.
Imports of Django/database models are lazy so core code remains importable.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import re
from types import MappingProxyType
import unicodedata

from .workflow_errors import WorkflowError

PROFILE_KEY = 'seller_profile'
PROFILE_FIELDS = {
    'name': ('Firma / vollständiger Name', 120, True),
    'street': ('Straße und Hausnummer', 120, True),
    'postcode': ('Postleitzahl', 20, True),
    'city': ('Ort', 80, True),
    'country': ('Land (ISO-Code, z. B. DE)', 2, True),
    'phone': ('Telefon', 40, True),
    'email': ('E-Mail-Adresse', 254, True),
    'tax_no': ('Steuernummer', 40, False),
    'vat_id': ('Umsatzsteuer-ID', 20, False),
    'seller_id': ('Unternehmens-/Lieferantenkennung (ohne USt-ID erforderlich)', 120, False),
    'iban': ('IBAN', 42, True),
    'bic': ('BIC', 11, True),
    'bank_name': ('Bankname', 80, False),
    'register': ('Registerangaben (falls erforderlich)', 120, False),
    'ceo': ('Vertretungsberechtigte Person', 120, True),
}
_snapshot = ContextVar('sedo_seller_profile', default=None)
_COUNTRIES = frozenset(('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW').split())


class SellerProfileError(WorkflowError):
    """Only fixed messages/field labels, never supplied business values."""
    def __init__(self, message, *, field=None):
        super().__init__(message)
        self.field = field


def validate_profile(value: Mapping) -> dict[str, str]:
    """Normalize and validate every entry, including profiles loaded from DB."""
    if not isinstance(value, Mapping) or set(value) - set(PROFILE_FIELDS):
        raise SellerProfileError('Die Firmendaten haben ein ungültiges Format.')
    result = {}
    for name, (label, max_length, required) in PROFILE_FIELDS.items():
        item = value.get(name, '')
        if not isinstance(item, str):
            raise SellerProfileError(f'{label}: Bitte Text eingeben.', field=name)
        # Validate before strip: CR/LF, bidi/control and invisible formatting
        # characters must not be smuggled into PDF/XML or mail headers.
        if any(unicodedata.category(char).startswith('C') for char in item):
            raise SellerProfileError(f'{label}: Steuerzeichen sind nicht erlaubt.', field=name)
        item = item.strip()
        if len(item) > max_length or (required and not item):
            raise SellerProfileError(f'{label}: Bitte vollständig und mit höchstens {max_length} Zeichen ausfüllen.', field=name)
        result[name] = item
    result['country'] = result['country'].upper()
    if result['country'] not in _COUNTRIES:
        raise SellerProfileError('Land: Bitte einen zweistelligen ISO-Ländercode eingeben.', field='country')
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    try:
        validate_email(result['email'])
    except ValidationError:
        raise SellerProfileError('E-Mail-Adresse: Bitte eine gültige Adresse eingeben.', field='email') from None
    result['iban'] = result['iban'].replace(' ', '').upper()
    iban = result['iban']
    if not re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}', iban) or iban[:2] not in _COUNTRIES:
        raise SellerProfileError('IBAN: Format oder Prüfsumme ist ungültig.', field='iban')
    digits = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in iban[4:] + iban[:4])
    if int(digits) % 97 != 1:
        raise SellerProfileError('IBAN: Format oder Prüfsumme ist ungültig.', field='iban')
    result['bic'] = result['bic'].upper()
    if (not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?', result['bic'])
            or result['bic'][4:6] not in _COUNTRIES):
        raise SellerProfileError('BIC: Bitte 8 oder 11 gültige Zeichen eingeben.', field='bic')
    if not result['tax_no'] and not result['vat_id']:
        raise SellerProfileError('Bitte Steuernummer oder Umsatzsteuer-ID hinterlegen.')
    if not result['vat_id'] and not result['seller_id']:
        raise SellerProfileError('Ohne Umsatzsteuer-ID ist für die E-Rechnung eine eigene Unternehmens-/Lieferantenkennung erforderlich.', field='seller_id')
    if result['tax_no'] and not re.fullmatch(r'[A-Za-z0-9 ./-]{3,40}', result['tax_no']):
        raise SellerProfileError('Steuernummer: Das Format ist ungültig.', field='tax_no')
    if result['vat_id'] and not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{2,18}', result['vat_id']):
        raise SellerProfileError('Umsatzsteuer-ID: Das Format ist ungültig.', field='vat_id')
    return result


def load_profile() -> dict[str, str]:
    from .models import AppSetting
    raw = AppSetting.get(PROFILE_KEY, '')
    if not raw:
        raise SellerProfileError('Bitte zuerst unter Einstellungen → Firmendaten ein vollständiges Absenderprofil hinterlegen. Es wurde kein neuer Beleg erzeugt.')
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise SellerProfileError('Die gespeicherten Firmendaten sind ungültig. Bitte die Administration um Korrektur bitten.') from None
    return validate_profile(value)


def profile_form_initial() -> dict[str, str]:
    """Return only validated values to settings; malformed data fails closed."""
    try:
        return load_profile()
    except SellerProfileError:
        return {'country': 'DE'}


def save_profile(value: Mapping) -> dict[str, str]:
    from .models import AppSetting
    clean = validate_profile(value)
    AppSetting.set(PROFILE_KEY, json.dumps(clean, ensure_ascii=False, sort_keys=True))
    return clean


def initialize_profile(value: Mapping) -> bool:
    """Complete setup once; never replace a valid profile from another request.

    Call inside the audit transaction. The conditional update also protects
    SQLite, where select_for_update does not provide row-level locks.
    """
    from .models import AppSetting
    clean = validate_profile(value)
    entry, _ = AppSetting.objects.get_or_create(key=PROFILE_KEY, defaults={'value': ''})
    try:
        validate_profile(json.loads(entry.value))
    except (SellerProfileError, ValueError, TypeError):
        pass
    else:
        return False
    changed = AppSetting.objects.filter(pk=entry.pk, value=entry.value).update(
        value=json.dumps(clean, ensure_ascii=False, sort_keys=True))
    if not changed:
        raise SellerProfileError('Die Firmendaten wurden zwischenzeitlich geändert. Bitte die Seite neu laden.')
    return True


@contextmanager
def seller_snapshot(profile=None):
    """Pin once per operation. Explicit profiles are useful for isolated tools/tests."""
    current = _snapshot.get()
    if current is not None and profile is None:
        yield current
        return
    snapshot = MappingProxyType(validate_profile(profile) if profile is not None else load_profile())
    token = _snapshot.set(snapshot)
    try:
        yield snapshot
    finally:
        _snapshot.reset(token)


def seller_document(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with seller_snapshot():
            return function(*args, **kwargs)
    return wrapped


def seller_post(function):
    """Validate and pin sender data before a document-writing HTTP request.

    Read-only form requests remain available before onboarding. A POST must
    not create customer records or reserve numbers before profile validation.
    """
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if request.method != 'POST':
            return function(request, *args, **kwargs)
        with seller_snapshot():
            return function(request, *args, **kwargs)
    return wrapped


class _Seller(Mapping):
    """Compatibility mapping, with no process-global cached business data."""
    def __getitem__(self, key):
        current = _snapshot.get()
        return (current if current is not None else load_profile())[key]

    def __iter__(self):
        return iter(PROFILE_FIELDS)

    def __len__(self):
        return len(PROFILE_FIELDS)


SELLER = _Seller()


class SellerProfileMiddleware:
    """Friendly fallback for direct document routes not catching WorkflowError."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, SellerProfileError):
            from django.shortcuts import render
            from .security import is_administrator
            response = render(request, 'sedo/company_profile_required.html', {
                'profile_error': str(exception),
                'can_edit_company': is_administrator(request.user),
            }, status=400)
            response['Cache-Control'] = 'no-store'
            return response
