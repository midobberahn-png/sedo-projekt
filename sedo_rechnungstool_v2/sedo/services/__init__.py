"""Service-Schicht des SEDO Rechnungstools.

Früher eine einzelne ``services.py``; jetzt ein Package mit klar getrennten
Verantwortlichkeiten. Dieses ``__init__`` re-exportiert die öffentliche API,
damit bestehende Aufrufe (``from sedo.services import ...``) unverändert laufen.

Module:
- ``numbering``  – Nummernkreise (kollisionssicher, GoBD)
- ``calc``       – Betrags-/Statusberechnung
- ``invoices``   – Rechnungslogik (Kern-Mapping, Dateien, Storno, Angebot→Rechnung)
- ``pdf``        – Angebots-/Kassenbuch-PDF
- ``dunning``    – Mahnwesen
- ``payments``   – Bank-JSON-Import
- ``mail``       – E-Mail-Entwürfe und SMTP-Versand
- ``ai``         – KI-Angebotsassistent (Anthropic Claude)
- ``formatting`` – Formatierungshelfer
"""
from __future__ import annotations

from .ai import (
    AI_DEFAULT_TEXT_MODEL,
    AI_MAX_POSITIONS,
    AI_MAX_TEXT_CHARS,
    AIError,
    _AI_SYSTEM_PROMPT_BASE,
    _ai_generate_via_tool_use,
    _ai_normalize_unit,
    _ai_to_decimal,
    _service_catalog_prompt_block,
    ai_is_configured,
    apply_and_learn_service_catalog,
    extract_offer_positions_from_text,
    generate_cover_letter_text,
    generate_offer_from_text,
    get_ai_config,
    ai_review_positions,
    normalize_ai_positions,
    polish_service_text,
)
from .calc import (
    abgeleiteter_zahlstatus,
    offene_rechnungen,
    recalc_invoice,
    recalc_offer,
    refresh_invoice_payment_status,
    zuordenbare_rechnungen,
)
from .dunning import (
    mahn_hindernis,
    ensure_dunning_allowed,
    generate_dunning_document,
    get_dunning_candidates,
    next_dunning_kind,
    send_bulk_dunning,
)
from .formatting import _euro, euro
from .invoices import (
    WorkflowError,
    create_invoice_from_offer,
    create_storno,
    ausblenden_hindernis,
    ensure_correction_allowed,
    ensure_versand_erlaubt,
    ensure_zahlung_erlaubt,
    versand_nachweis,
    ensure_offer_invoicing_allowed,
    ensure_storno_allowed,
    generate_invoice_files,
    invoice_to_core_row,
    mark_invoice_corrected,
    finalisiere_entwurf,
    entwurf_sperren,
)
from .mail import (
    create_email_draft,
    get_smtp_config,
    send_email_smtp,
    smtp_is_configured,
)
from .numbering import (
    create_numbered_invoice,
    create_numbered_offer,
    next_cashbook_no,
    next_number,
    save_with_unique_number,
)
from .banking import (
    BankingError,
    banking_is_configured,
    fetch_bank_transactions,
    get_banking_config,
)
from .payments import import_bank_json, parse_bank_date
from .plausibility import check_positions_local
from .pdf import generate_cashbook_pdf, generate_offer_pdf
from .transcription import (
    TranscriptionUnavailable,
    stt_status,
    transcribe_audio,
)

__all__ = [
    # numbering
    'next_number', 'next_cashbook_no', 'create_numbered_invoice', 'create_numbered_offer',
    'save_with_unique_number',
    # calc
    'recalc_invoice', 'recalc_offer', 'refresh_invoice_payment_status',
    'abgeleiteter_zahlstatus', 'offene_rechnungen', 'zuordenbare_rechnungen',
    # invoices
    'invoice_to_core_row', 'generate_invoice_files', 'create_storno', 'create_invoice_from_offer',
    'WorkflowError', 'ensure_storno_allowed', 'ensure_correction_allowed',
    'ensure_zahlung_erlaubt', 'ensure_versand_erlaubt', 'versand_nachweis',
    'ausblenden_hindernis',
    'ensure_offer_invoicing_allowed', 'mark_invoice_corrected',
    'finalisiere_entwurf', 'entwurf_sperren',
    # pdf
    'generate_offer_pdf', 'generate_cashbook_pdf',
    # dunning
    'generate_dunning_document', 'get_dunning_candidates', 'send_bulk_dunning',
    'mahn_hindernis', 'ensure_dunning_allowed', 'next_dunning_kind',
    # payments
    'import_bank_json', 'parse_bank_date',
    # banking (FinTS)
    'fetch_bank_transactions', 'get_banking_config', 'banking_is_configured', 'BankingError',
    # transcription (lokales Whisper)
    'transcribe_audio', 'stt_status', 'TranscriptionUnavailable',
    # mail
    'create_email_draft', 'get_smtp_config', 'smtp_is_configured', 'send_email_smtp',
    # formatting
    'euro', '_euro',
    # ai
    'AIError', 'AI_DEFAULT_TEXT_MODEL', 'AI_MAX_POSITIONS', 'AI_MAX_TEXT_CHARS',
    'get_ai_config', 'ai_is_configured', 'generate_offer_from_text',
    'extract_offer_positions_from_text', 'normalize_ai_positions',
    'polish_service_text', 'generate_cover_letter_text',
    'ai_review_positions', 'check_positions_local',
    'apply_and_learn_service_catalog',
    '_ai_to_decimal', '_ai_normalize_unit', '_service_catalog_prompt_block',
    '_ai_generate_via_tool_use', '_AI_SYSTEM_PROMPT_BASE',
]
