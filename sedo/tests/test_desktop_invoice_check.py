"""The frozen build gate must exercise real invoice code with synthetic data."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from sedo.company_profile import PROFILE_KEY
from sedo.models import AppSetting, Invoice


class DesktopInvoiceCheckTests(TestCase):
    def test_invoice_gate_generates_and_validates_synthetic_document(self):
        entry_path = Path(__file__).resolve().parents[2] / 'tools/desktop_entry.py'
        spec = importlib.util.spec_from_file_location('sedo_invoice_build_check', entry_path)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        # Only Django's isolated test database is modified here.
        AppSetting.objects.filter(key=PROFILE_KEY).delete()
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            self.assertTrue(entry._check_invoice_workflow())
            self.assertEqual(Invoice.objects.count(), 1)
            with self.assertRaises(RuntimeError):
                entry._check_invoice_workflow()
            self.assertEqual(Invoice.objects.count(), 1)
