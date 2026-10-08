"""Explicit synthetic identities for tests, never imported by production code."""
import unittest

from sedo.company_profile import seller_snapshot


SYNTHETIC_PROFILE = {
    'name': 'Synthetische Testfirma GmbH',
    'street': 'Teststraße 1',
    'postcode': '10115',
    'city': 'Berlin',
    'country': 'DE',
    'phone': '030 0000000',
    'email': 'rechnung@seller.example.invalid',
    'tax_no': '00/000/00000',
    'vat_id': '',
    'seller_id': 'SYNTHETIC-TEST-SUPPLIER',
    # Public standard IBAN example, not an account copied from an operator.
    'iban': 'DE89370400440532013000',
    'bic': 'COBADEFFXXX',
    'bank_name': 'Synthetische Testbank',
    'register': '',
    'ceo': 'Synthetische Testperson',
}


class SyntheticSellerTestCase(unittest.TestCase):
    """Core-only tests opt into an isolated immutable sender snapshot."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        snapshot = seller_snapshot(SYNTHETIC_PROFILE)
        snapshot.__enter__()
        cls.addClassCleanup(snapshot.__exit__, None, None, None)
