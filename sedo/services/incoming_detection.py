"""Safe public entry points for conservative incoming-document detection.

Text/XML helpers operate only on already trusted bounded values. Every raw
PDF, image or XML document crosses the resource-bounded child process.
"""
from ..document_probe import (AMOUNT, INVOICE, INVOICE_CODES, INVOICE_WORD,
    MAX_DOCUMENT_BYTES, NUMBER, OTHER, OTHER_WORD, TITLE, Detection,
    classify_document, classify_text, classify_xml, normalized)

__all__ = [
    'AMOUNT', 'INVOICE', 'INVOICE_CODES', 'INVOICE_WORD', 'MAX_DOCUMENT_BYTES',
    'NUMBER', 'OTHER', 'OTHER_WORD', 'TITLE', 'Detection', 'classify_document',
    'classify_text', 'classify_xml', 'normalized',
]
