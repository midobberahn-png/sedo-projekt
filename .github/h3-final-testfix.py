from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    s = p.read_text()
    if s.count(old) != 1:
        raise SystemExit(f'{path}: Anker {s.count(old)} statt 1')
    p.write_text(s.replace(old, new, 1))

# F-18: stale Anzeige-Cache darf keinen Schreibweg öffnen. Wiederverwenden des
# bereits versiegelten Originals ist ausdrücklich erlaubt; Neuberechnung nicht.
path = 'sedo_rechnungstool_v2/sedo/tests/test_invoice_archive.py'
replace(
    path,
    "from sedo.services import generate_invoice_files, finalisiere_entwurf, create_storno, create_invoice_from_offer",
    "from sedo.services import (WorkflowError, generate_invoice_files, finalisiere_entwurf,\n                           create_storno, create_invoice_from_offer)",
)
replace(
    path,
    """        before = self.files()
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv)
        with self.assertRaises(BelegArchivFehler):
            finalisiere_entwurf(inv)
        self.assertEqual(self.files(), before)
""",
    """        before = self.files()
        with patch('sedo.services.invoices._generate_invoice_files_inner') as generator:
            generate_invoice_files(inv)
        generator.assert_not_called()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual(self.files(), before)
""",
)

# F-07 Alt-Test: 'bezahlt' ist ab H3 keine Zahlungswahrheit mehr. Die
# vollbezahlte Rechnung braucht eine echte aktive Payment-Buchung.
path = 'sedo_rechnungstool_v2/sedo/tests/test_review_v2.py'
replace(
    path,
    """        self._rechnung('RE2026080024', 'bezahlt')
        self._rechnung('RE2026080025', 'erstellt', typ='457', brutto=Decimal('-119.00'))
""",
    """        bezahlt = self._rechnung('RE2026080024', 'bezahlt')
        Payment.objects.create(invoice=bezahlt, amount=bezahlt.gross_total,
                               booking_date=timezone.localdate(),
                               external_id='TEST-H3-REVIEW-PAID')
        self._rechnung('RE2026080025', 'erstellt', typ='457', brutto=Decimal('-119.00'))
""",
)
