"""F-18: echte Originalbytes, fehlende Archive, HTTP-Wege und sichere Wiederherstellung."""
import hashlib
import json
import os
import tempfile
from email import policy
from email.parser import BytesParser
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.audit import AuditWriteError
from sedo.models import AuditLog, Customer, CustomerObject, Invoice, InvoiceItem, Offer, OfferItem
from sedo.services import (WorkflowError, generate_invoice_files, finalisiere_entwurf,
                           create_storno, create_invoice_from_offer)
from sedo.services.invoice_archive import (ARCHIV_FELDER, ARCHIV_VORGANG, archivnachweis,
    lese_archivdatei, stelle_archiv_wieder_her, zielpfade, BelegArchivFehler)
from sedo.services.mail import create_email_draft, send_email_smtp
from sedo.views import _create_invoice_email_file, _monthly_zip_email


class InvoiceArchiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('f18', password='test-only')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sedo_f18_')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.media = self.home / 'media'
        cfg = override_settings(MEDIA_ROOT=self.media, SEDO_BACKUP_DIR=self.home / 'backups')
        cfg.enable(); self.addCleanup(cfg.disable)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Originalkunde', street='Originalweg 1',
            postcode='12345', city='Teststadt', email='f18@example.invalid')
        self.obj = CustomerObject.objects.create(customer=self.customer, name='TEST Originalobjekt')
        self.counter = 0

    def invoice(self, **values):
        self.counter += 1
        data = dict(number=f'RE-F18-{self.counter}', customer=self.customer, obj=self.obj,
                    issue_date=timezone.localdate(), due_date=timezone.localdate())
        data.update(values)
        inv = Invoice.objects.create(**data)
        InvoiceItem.objects.create(invoice=inv, description='TEST Leistung', quantity=1, unit_price=100)
        return inv

    def issued(self, **values):
        inv = self.invoice(**values)
        generate_invoice_files(inv, erstmalig=True)
        return inv

    def docs(self, inv):
        return {f: Path(getattr(inv, f).path).read_bytes() for f in ARCHIV_FELDER}

    def state(self, inv):
        inv.refresh_from_db()
        return Invoice.objects.values().get(pk=inv.pk)

    def files(self):
        return {str(p.relative_to(self.media)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.media.rglob('*') if p.is_file()}

    def backup(self, inv):
        root = self.home / 'quelle'
        for field in ARCHIV_FELDER:
            f = getattr(inv, field)
            p = root / f.name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(Path(f.path).read_bytes())
        return root

    def mutate_customer_items(self, inv):
        self.customer.name = 'TEST Neuer Name'; self.customer.street = 'Geändert 99'
        self.customer.save()
        self.obj.name = 'TEST Neues Objekt'; self.obj.save()
        inv.items.update(unit_price=999, description='TEST geändert')

    def test_erstausstellung_braucht_expliziten_internen_aufruf(self):
        inv = self.invoice()
        before = self.state(inv)
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv)
        self.assertEqual(self.state(inv), before)
        self.assertFalse(self.files())

    def test_erstausstellung_speichert_flache_vollstaendige_pruefsummen(self):
        inv = self.issued()
        proof = archivnachweis(inv)
        for field, data in self.docs(inv).items():
            self.assertEqual(proof[field], getattr(inv, field).name)
            self.assertEqual(proof[field + '_sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(proof[field + '_bytes'], len(data))
        self.assertEqual(AuditLog.objects.filter(metadata__vorgang=ARCHIV_VORGANG).count(), 1)

    def test_wiederverwenden_berechnet_nichts_neu_und_schreibt_nichts(self):
        inv = self.issued()
        self.mutate_customer_items(inv)
        before = self.state(inv), self.files(), AuditLog.objects.count()
        with patch('sedo.services.invoices._generate_invoice_files_inner') as generator:
            generate_invoice_files(inv)
        generator.assert_not_called()
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_schutz_gilt_fuer_alle_ausgestellten_zustaende(self):
        inv = self.issued()
        for status, _ in Invoice.STATUS:
            if status == 'entwurf':
                continue
            with self.subTest(status=status):
                lifecycle = {'storniert':'cancelled', 'korrigiert':'corrected'}.get(status, 'issued')
                Invoice.objects.filter(pk=inv.pk).update(status=status, lifecycle=lifecycle)
                inv.refresh_from_db()
                before = self.state(inv), self.files(), AuditLog.objects.count()
                generate_invoice_files(inv)
                self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_auch_erstmalig_flag_ersetzt_keine_originale(self):
        inv = self.issued()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv, erstmalig=True)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_fehlende_datei_ist_keine_neuerzeugungserlaubnis(self):
        for field in ARCHIV_FELDER:
            with self.subTest(field=field):
                inv = self.issued()
                Path(getattr(inv, field).path).unlink()  # ausschließlich eigene Testdatei
                before = self.state(inv), self.files(), AuditLog.objects.count()
                with self.assertRaises(BelegArchivFehler):
                    generate_invoice_files(inv)
                self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_fehlende_verweise_und_dateien_loeschen_nicht_die_ausstellung(self):
        inv = self.issued()
        for f in ARCHIV_FELDER:
            Path(getattr(inv, f).path).unlink()
        Invoice.objects.filter(pk=inv.pk).update(**{f: '' for f in ARCHIV_FELDER})
        for first in (False, True):
            with self.assertRaises(BelegArchivFehler):
                generate_invoice_files(inv, erstmalig=first)
        self.assertFalse(self.files())
        self.assertIsNotNone(archivnachweis(inv))

    def test_zurueckgesetzter_status_oeffnet_keinen_schreibweg(self):
        inv = self.issued()
        # Ein geschützter Legacy-Belegstatus kann lifecycle nicht mehr allein überschreiben.
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=inv.pk).update(status='entwurf')
        Invoice.objects.filter(pk=inv.pk).update(status='ueberfaellig')
        before = self.files()
        with patch('sedo.services.invoices._generate_invoice_files_inner') as generator:
            generate_invoice_files(inv)
        generator.assert_not_called()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual(self.files(), before)

    def test_altbeleg_wird_nur_gelesen_nicht_nachtraeglich_versiegelt(self):
        inv = self.invoice()
        for field, path in zielpfade(inv).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'Historische Originalbytes ' + field.encode())
            getattr(inv, field).name = path.relative_to(self.media.resolve()).as_posix()
        inv.save()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        generate_invoice_files(inv)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)
        self.assertIsNone(archivnachweis(inv))

    def test_altbeleg_ohne_dateien_bleibt_gesperrt(self):
        inv = self.invoice(status='versendet')
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv)
        self.assertFalse(self.files())

    def test_manipulierte_datei_wird_erkannt_nicht_repariert(self):
        inv = self.issued()
        path = Path(inv.zugferd_file.path)
        original = path.read_bytes()
        path.write_bytes(b'X' + original[1:])
        before = self.files()
        with self.assertRaises(BelegArchivFehler):
            lese_archivdatei(inv)
        self.assertEqual(self.files(), before)

    def test_unvollstaendiger_oder_mehrdeutiger_nachweis_sperrt(self):
        inv = self.issued()
        seal = AuditLog.objects.get(metadata__vorgang=ARCHIV_VORGANG)
        proof = dict(seal.metadata); proof.pop('xml_file_sha256')
        seal.metadata = proof; seal.save()
        with self.assertRaises(BelegArchivFehler):
            lese_archivdatei(inv)
        AuditLog.objects.create(object_type='Invoice', object_id=str(inv.pk), metadata=proof, action='generate')
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv)

    def test_dateiverweis_darf_nicht_auf_fremdes_original_umgebogen_werden(self):
        inv, other = self.issued(), self.issued()
        Invoice.objects.filter(pk=inv.pk).update(zugferd_file=other.zugferd_file.name)
        with self.assertRaises(BelegArchivFehler):
            lese_archivdatei(inv)

    def test_entwurf_darf_keine_fremden_dateien_quarantaenieren(self):
        original = self.issued()
        draft = self.invoice(status='entwurf', xml_file=original.xml_file.name,
                             zugferd_file=original.zugferd_file.name)
        before = self.files()
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(draft)
        self.assertEqual(self.files(), before)

    def test_erstausstellung_ueberschreibt_auch_keine_verwaisten_ziele(self):
        inv = self.invoice()
        p = zielpfade(inv)['pdf_file']; p.parent.mkdir(parents=True); p.write_bytes(b'Fremdes Original')
        from sedo.services.invoice_archive import pruefe_schreibziele
        with self.assertRaises(BelegArchivFehler):
            pruefe_schreibziele(inv, erstmalig=True)
        with self.assertRaises(BelegArchivFehler):
            generate_invoice_files(inv, erstmalig=True)
        self.assertEqual(p.read_bytes(), b'Fremdes Original')

    def test_race_beim_veroeffentlichen_bewegt_keine_fremde_datei(self):
        inv = self.invoice()
        real = os.link
        def collide(source, target):
            if Path(target).name == 'factur-x.xml':
                Path(target).write_bytes(b'Parallel vorhandenes Original')
            return real(source, target)
        with patch('sedo.services.invoice_files.os.link', side_effect=collide):
            with self.assertRaises(FileExistsError):
                generate_invoice_files(inv, erstmalig=True)
        self.assertTrue(zielpfade(inv)['xml_file'].is_file(), 'Eine fremde Datei wurde beim Rollback verschoben')
        self.assertEqual(zielpfade(inv)['xml_file'].read_bytes(), b'Parallel vorhandenes Original')
        self.assertFalse(zielpfade(inv)['pdf_file'].exists())
        self.assertFalse(AuditLog.objects.filter(metadata__vorgang=ARCHIV_VORGANG).exists())

    def test_archivauditfehler_rollt_dateien_und_verweise_zurueck(self):
        inv = self.invoice()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('Testfehler')):
            with self.assertRaises(AuditWriteError):
                generate_invoice_files(inv, erstmalig=True)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_symlink_und_traversal_werden_nicht_verfolgt(self):
        inv = self.invoice()
        outside = self.home / 'extern.pdf'; outside.write_bytes(b'Nicht lesen/ersetzen')
        self.media.mkdir()
        (self.media / 'link.pdf').symlink_to(outside)
        for name in ('../extern.pdf', 'link.pdf', '/tmp/f18.pdf', 'foo\\bar.pdf'):
            Invoice.objects.filter(pk=inv.pk).update(zugferd_file=name)
            with self.assertRaises(BelegArchivFehler):
                lese_archivdatei(inv)
        self.assertEqual(outside.read_bytes(), b'Nicht lesen/ersetzen')

    def test_lesebytes_bleiben_nach_pruefung_unveraendert(self):
        inv = self.issued()
        attachment = lese_archivdatei(inv)
        original = attachment.data
        Path(inv.zugferd_file.path).write_bytes(b'Datei nach der Pruefung veraendert')
        path = create_email_draft('test@example.invalid', 'TEST', 'TEST', attachment,
                                 self.media / 'emails', 'f18')
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        self.assertEqual(next(msg.iter_attachments()).get_payload(decode=True), original)

    def test_http_versteckt_generator_fuer_ausgestellte_rechnung(self):
        inv = self.issued()
        url = reverse('sedo:invoice_generate', args=[inv.pk])
        response = self.client.get(reverse('sedo:invoice_detail', args=[inv.pk]))
        self.assertNotContains(response, f'action="{url}"')
        self.assertContains(response, 'Originaldateien geschützt')
        before = self.state(inv), self.files()
        response = self.client.post(url, {'erstmalig': '1', 'force': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual((self.state(inv), self.files()), before)

    def test_http_fehlendes_archiv_zeigt_dauerhaften_hinweis(self):
        inv = self.invoice()
        response = self.client.post(reverse('sedo:invoice_generate', args=[inv.pk]), follow=True)
        self.assertContains(response, 'Belegarchiv prüfen')
        self.assertContains(response, 'Keine Neuerzeugung')
        self.assertFalse(self.files())

    def test_http_entwurf_bleibt_neu_erzeugbar(self):
        inv = self.invoice(status='entwurf')
        self.client.post(reverse('sedo:invoice_generate', args=[inv.pk]))
        inv.refresh_from_db()
        first = Path(inv.pdf_file.path).read_bytes()
        inv.items.update(unit_price=222)
        self.client.post(reverse('sedo:invoice_generate', args=[inv.pk]))
        inv.refresh_from_db()
        self.assertNotEqual(Path(inv.pdf_file.path).read_bytes(), first)
        self.assertFalse(inv.xml_file or inv.zugferd_file)
        self.assertIsNone(archivnachweis(inv))

    def test_http_generator_braucht_post_login_und_csrf(self):
        inv = self.issued(); url = reverse('sedo:invoice_generate', args=[inv.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url).status_code, 302)
        client = Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(client.post(url).status_code, 403)

    def test_erneuter_smtp_versand_nutzt_originalbytes_auch_korrigiert(self):
        inv = self.issued(); original = self.docs(inv)['zugferd_file']
        self.mutate_customer_items(inv)
        Invoice.objects.filter(pk=inv.pk).update(status='korrigiert', lifecycle='corrected')
        before = self.state(inv), self.files()
        with patch('sedo.views.smtp_is_configured', return_value=True), patch('sedo.views.send_email_smtp') as send:
            response = self.client.post(reverse('sedo:invoice_email', args=[inv.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(send.call_args.args[3].data, original)
        self.assertEqual((self.state(inv), self.files()), before)

    def test_fehlender_anhang_blockiert_smtp_und_eml(self):
        inv = self.issued(); Path(inv.zugferd_file.path).unlink()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        for configured in (True, False):
            with patch('sedo.views.smtp_is_configured', return_value=configured), patch('sedo.views.send_email_smtp') as send, patch('sedo.views.create_email_draft') as eml, patch('sedo.views.generate_invoice_files') as gen:
                self.client.post(reverse('sedo:invoice_email', args=[inv.pk]))
            send.assert_not_called(); eml.assert_not_called(); gen.assert_not_called()
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_email_helper_erzeugt_keine_rechnungsdateien(self):
        inv = self.issued(); original = self.docs(inv)['zugferd_file']
        self.mutate_customer_items(inv)
        with patch('sedo.views.generate_invoice_files') as gen:
            path = _create_invoice_email_file(inv)
        gen.assert_not_called()
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        self.assertEqual(next(msg.iter_attachments()).get_payload(decode=True), original)

    def test_mailservice_verschweigt_keinen_fehlenden_pflichtanhang(self):
        path = self.home / 'fehlt.pdf'
        with self.assertRaises(FileNotFoundError):
            create_email_draft('test@example.invalid', 'TEST', 'TEST', path, self.home, 'f18')
        cfg = dict(host='mail.example.invalid', user='test', password='test', from_name='TEST', port=587, tls=True)
        with patch('sedo.services.mail.get_smtp_config', return_value=cfg), patch('sedo.services.mail.smtplib.SMTP') as smtp:
            with self.assertRaises(FileNotFoundError):
                send_email_smtp('test@example.invalid', 'TEST', 'TEST', path)
        smtp.assert_not_called()

    def test_sammelmail_uebernimmt_jedes_original_unveraendert(self):
        a, b = self.issued(), self.issued()
        originals = {inv.zugferd_file.name.split('/')[-1]: self.docs(inv)['zugferd_file'] for inv in (a,b)}
        self.mutate_customer_items(a)
        path = _monthly_zip_email([a,b], self.customer, '2026-09')
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        with ZipFile(BytesIO(next(msg.iter_attachments()).get_payload(decode=True))) as z:
            self.assertEqual({n:z.read(n) for n in z.namelist()}, originals)

    def test_sammelmail_darf_fehlenden_beleg_nicht_still_auslassen(self):
        a, b = self.issued(), self.invoice()
        with self.assertRaises(BelegArchivFehler):
            _monthly_zip_email([a,b], self.customer, '2026-09')
        self.assertFalse((self.media / 'monthly_emails').exists())

    def test_steuerexport_bleibt_byteidentisch(self):
        inv = self.issued(); original = self.docs(inv)['pdf_file']; self.mutate_customer_items(inv)
        response = self.client.post(reverse('sedo:month_close'), {'month': timezone.localdate().strftime('%Y-%m'), 'action': 'export_tax_email'})
        self.assertEqual(response.status_code, 200)
        msg = BytesParser(policy=policy.default).parsebytes(b''.join(response.streaming_content))
        with ZipFile(BytesIO(next(msg.iter_attachments()).get_payload(decode=True))) as z:
            pdfs = [n for n in z.namelist() if n.endswith('.pdf')]
            self.assertEqual(len(pdfs), 1); self.assertEqual(z.read(pdfs[0]), original)

    def test_steuerexport_bei_fehlendem_pdf_kein_teilarchiv(self):
        a, b = self.issued(), self.invoice()
        with patch('sedo.views.generate_invoice_files') as gen:
            response = self.client.post(reverse('sedo:month_close'), {'month': timezone.localdate().strftime('%Y-%m'), 'action': 'export_tax_email'})
        self.assertEqual(response.status_code, 302)
        gen.assert_not_called()
        self.assertFalse((self.media / 'month_close').exists())

    def test_storno_und_angebot_erstellen_eigene_archive(self):
        inv = self.issued(); before = self.docs(inv)
        storno = create_storno(inv)
        self.assertIsNotNone(archivnachweis(storno))
        self.assertEqual(self.docs(inv), before)
        offer = Offer.objects.create(number='AG-F18', customer=self.customer)
        OfferItem.objects.create(offer=offer, description='TEST Angebot', quantity=1, unit_price=10)
        generated = create_invoice_from_offer(offer)
        self.assertIsNotNone(archivnachweis(generated))

    def test_wiederherstellung_trockenlauf_anwenden_wiederholung(self):
        inv = self.issued(); original = self.docs(inv); source = self.backup(inv)
        Path(inv.xml_file.path).unlink(); Path(inv.pdf_file.path).unlink()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        report = stelle_archiv_wieder_her(inv, source)
        self.assertFalse(report['uebernommen'])
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)
        report = stelle_archiv_wieder_her(inv, source, anwenden=True)
        self.assertTrue(report['uebernommen']); self.assertEqual(self.docs(inv), original)
        after = self.state(inv), self.files(), AuditLog.objects.count()
        self.assertEqual(after[2], before[2] + 1)
        self.assertFalse(stelle_archiv_wieder_her(inv, source, anwenden=True)['uebernommen'])
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), after)

    def test_wiederherstellung_prueft_alle_bytes_vor_dem_ersten_schreiben(self):
        inv = self.issued(); source = self.backup(inv)
        Path(inv.pdf_file.path).unlink(); Path(inv.xml_file.path).unlink()
        (source / inv.xml_file.name).write_bytes(b'Falsches Backup')
        before = self.state(inv), self.files(), AuditLog.objects.count()
        with self.assertRaises(BelegArchivFehler):
            stelle_archiv_wieder_her(inv, source, anwenden=True)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_wiederherstellung_ueberschreibt_keine_beschaedigten_originale(self):
        inv = self.issued(); source = self.backup(inv)
        Path(inv.pdf_file.path).write_bytes(b'Abweichender Bestand')
        before = self.files()
        with self.assertRaises(BelegArchivFehler):
            stelle_archiv_wieder_her(inv, source, anwenden=True)
        self.assertEqual(self.files(), before)

    def test_wiederherstellung_repariert_fehlende_verweise_nur_auf_original(self):
        inv = self.issued(); source = self.backup(inv); names = {f:getattr(inv,f).name for f in ARCHIV_FELDER}
        Invoice.objects.filter(pk=inv.pk).update(xml_file='')
        stelle_archiv_wieder_her(inv, source, anwenden=True)
        inv.refresh_from_db(); self.assertEqual(inv.xml_file.name, names['xml_file'])

    def test_wiederherstellung_auditfehler_nimmt_nur_eigene_dateien_zurueck(self):
        inv = self.issued(); source = self.backup(inv)
        Path(inv.pdf_file.path).unlink(); Path(inv.xml_file.path).unlink()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('Pflichtaudit defekt')):
            with self.assertRaises(AuditWriteError):
                stelle_archiv_wieder_her(inv, source, anwenden=True)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_wiederherstellung_altbeleg_ohne_beweis_wird_nicht_erfunden(self):
        inv = self.invoice()
        with self.assertRaises(BelegArchivFehler):
            stelle_archiv_wieder_her(inv, self.home, anwenden=True)
        self.assertFalse(self.files())

    def test_cli_ist_standardmaessig_nur_pruefung(self):
        inv = self.issued(); source = self.backup(inv); Path(inv.xml_file.path).unlink()
        output = StringIO()
        call_command('belegarchiv_wiederherstellen', inv.number, quelle=source, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['modus'], 'trockenlauf')
        self.assertFalse(Path(inv.xml_file.path).exists())
        call_command('belegarchiv_wiederherstellen', inv.number, quelle=source, anwenden=True, stdout=StringIO())
        self.assertTrue(Path(inv.xml_file.path).is_file())
        with self.assertRaises(CommandError):
            call_command('belegarchiv_wiederherstellen', 'UNBEKANNT', quelle=source)

    def payload(self, **changes):
        data = {'customer': str(self.customer.pk), 'obj': str(self.obj.pk),
                'issue_date': timezone.localdate().isoformat(), 'payment_days': '0',
                'vat_rate': '19.00', 'action': 'save', 'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000',
                'items-0-description': 'TEST neue Leistung', 'items-0-quantity': '1',
                'items-0-unit': 'pauschal', 'items-0-unit_price': '100.00'}
        data.update(changes)
        return data

    def test_http_erstellen_und_eml_generiert_originale_nur_einmal(self):
        from sedo.services.invoices import _generate_invoice_files_inner
        with patch('sedo.services.invoices._generate_invoice_files_inner', wraps=_generate_invoice_files_inner) as gen:
            response = self.client.post(reverse('sedo:invoice_create'), self.payload(action='send'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(gen.call_count, 1)
        inv = Invoice.objects.get()
        self.assertIsNotNone(archivnachweis(inv))
        msg = BytesParser(policy=policy.default).parsebytes(b''.join(response.streaming_content))
        self.assertEqual(next(msg.iter_attachments()).get_payload(decode=True), self.docs(inv)['zugferd_file'])

    def test_http_korrektur_nach_dateienfehlversuch_rollt_alles_zurueck(self):
        inv = self.issued()
        before = self.state(inv), self.files(), AuditLog.objects.count()
        from sedo.services import WorkflowError
        with patch('sedo.views.mark_invoice_corrected', side_effect=WorkflowError('TEST nach Veröffentlichung')):
            response = self.client.post(reverse('sedo:invoice_create'), self.payload(correction_of=str(inv.pk)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual((self.state(inv), self.files(), AuditLog.objects.count()), before)

    def test_abo_erstellung_erhaelt_archivnachweis_ohne_fachumbau(self):
        from sedo.models import RecurringInvoiceTemplate, RecurringInvoiceItem
        from sedo.views import _generate_recurring_invoice_from_template
        tmpl = RecurringInvoiceTemplate.objects.create(customer=self.customer, title='TEST Abo')
        RecurringInvoiceItem.objects.create(template=tmpl, description='TEST', quantity=1, unit_price=100)
        day = timezone.localdate()
        inv = _generate_recurring_invoice_from_template(tmpl, day, day, day, day)
        self.assertIsNotNone(archivnachweis(inv))
        self.assertEqual(inv.due_date, day)

    def test_re_altentwurf_mit_vorschau_darf_einmal_finalisiert_werden(self):
        inv = self.invoice(status='entwurf')
        generate_invoice_files(inv)
        old = Path(inv.pdf_file.path).read_bytes()
        finalisiere_entwurf(inv)
        self.assertIsNotNone(archivnachweis(inv))
        self.assertNotEqual(Path(inv.pdf_file.path).read_bytes(), old)
        self.assertTrue(any(p.read_bytes() == old for p in (self.home / '.media_quarantaene').rglob('*.pdf')))

    def test_stale_entwurfsinstanz_darf_keine_originale_ueberschreiben(self):
        inv = self.invoice(status='entwurf')
        stale = Invoice.objects.get(pk=inv.pk)
        finalisiere_entwurf(inv)
        before = self.state(inv), self.files()
        generate_invoice_files(stale)
        self.assertEqual((self.state(inv), self.files()), before)


from django.db import OperationalError, close_old_connections
from django.test import TransactionTestCase
import threading
from sedo.services.workflow_errors import WorkflowError


class InvoiceArchiveConcurrencyTests(TransactionTestCase):
    def test_parallel_erstveroeffentlichung_schreibt_nur_ein_original(self):
        """Zwei echte Verbindungen; die zweite darf kein Original ersetzen."""
        from sedo.services.invoices import _generate_invoice_files_inner
        customer = Customer.objects.create(name='TEST F18 Parallel')
        inv = Invoice.objects.create(number='RE-F18-PARALLEL', customer=customer)
        InvoiceItem.objects.create(invoice=inv, description='TEST', unit_price=100)
        reached, release = threading.Event(), threading.Event()
        results = []

        def held(invoice):
            reached.set()
            if not release.wait(10):
                raise RuntimeError('Parallelprobe nicht freigegeben')
            return _generate_invoice_files_inner(invoice)

        def worker():
            close_old_connections()
            try:
                generate_invoice_files(Invoice.objects.get(pk=inv.pk), erstmalig=True)
                results.append('ok')
            except Exception as exc:
                results.append(exc)
            finally:
                close_old_connections()

        with tempfile.TemporaryDirectory(prefix='sedo_f18_parallel_') as tmp, override_settings(MEDIA_ROOT=Path(tmp)/'media'):
            with patch('sedo.services.invoices._generate_invoice_files_inner', side_effect=held):
                thread = threading.Thread(target=worker)
                thread.start()
                try:
                    self.assertTrue(reached.wait(10), 'Erstausstellung hat Schreibsperre nicht erreicht')
                    with self.assertRaises((WorkflowError, OperationalError)):
                        generate_invoice_files(inv, erstmalig=True)
                finally:
                    release.set()
                    thread.join(10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(results, ['ok'])
            inv.refresh_from_db()
            proof = archivnachweis(inv)
            self.assertIsNotNone(proof)
            before = {field: Path(getattr(inv, field).path).read_bytes() for field in ARCHIV_FELDER}
            with self.assertRaises(WorkflowError):
                generate_invoice_files(inv, erstmalig=True)
            generate_invoice_files(inv)
            self.assertEqual(before, {field: Path(getattr(inv, field).path).read_bytes() for field in ARCHIV_FELDER})
            self.assertEqual(AuditLog.objects.filter(metadata__vorgang=ARCHIV_VORGANG).count(), 1)
