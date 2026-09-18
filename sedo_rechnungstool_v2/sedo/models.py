from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActiveManager(models.Manager):
    """Standard-Manager, der soft-gelöschte Datensätze ausblendet.

    Direkte Abfragen (z. B. ``Invoice.objects``) liefern damit automatisch nur
    aktive Datensätze. Für vollständigen Zugriff (Admin, Audit) dient
    ``all_objects``; Rückwärtsrelationen laufen bewusst über ``all_objects``
    (``base_manager_name``), damit dort nichts unbemerkt verschwindet – die
    wenigen Stellen filtern explizit mit ``deleted_at__isnull=True``.
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class DeleteProtectedError(Exception):
    """Ein aufbewahrungspflichtiger Beleg sollte hart gelöscht werden.

    Wird von den ``pre_delete``-Wächtern in ``sedo/signals.py`` geworfen und
    schließt damit auch die Wege, die an den Views vorbeiführen: Django-Admin,
    ``manage.py shell``, ``QuerySet.delete()``. Der vorgesehene Weg ist
    Storno bzw. Korrektur, bei einem Entwurf das Ausblenden (Soft-Delete).

    Achtung beim Abfangen: Django sendet ``pre_delete`` innerhalb eines
    ``transaction.atomic(savepoint=False)``. Wer diese Ausnahme fängt, hat
    trotzdem die gesamte umgebende Transaktion verloren – sie lässt sich nicht
    „wegfangen und weitermachen".
    """


class SoftDeleteModel(TimeStampedModel):
    """Basisklasse für GoBD-relevante Datensätze: kein hartes Löschen.

    Statt aus der Datenbank entfernt zu werden, erhalten Datensätze einen
    ``deleted_at``-Zeitstempel und verschwinden aus den Standard-Abfragen.
    """
    deleted_at = models.DateTimeField('Gelöscht am', null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = 'all_objects'

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Markiert den Datensatz als gelöscht, ohne ihn zu entfernen."""
        if self.deleted_at is None:
            self.deleted_at = timezone.now()
            self.save(update_fields=['deleted_at', 'updated_at'])


class Customer(TimeStampedModel):
    CUSTOMER_TYPES = [
        ('einmalig', 'Einmalig'),
        ('monatskunde', 'Monatskunde'),
    ]
    SALUTATIONS = [
        ('', '—'),
        ('Herr', 'Herr'),
        ('Frau', 'Frau'),
        ('Familie', 'Familie'),
        ('Firma', 'Firma'),
        ('Eheleute', 'Eheleute'),
    ]
    customer_no = models.CharField('Kunden-Nr.', max_length=80, blank=True)
    customer_type = models.CharField('Kundentyp', max_length=30, choices=CUSTOMER_TYPES, default='einmalig')
    salutation = models.CharField('Anrede', max_length=30, choices=SALUTATIONS, blank=True)
    name = models.CharField('Name / Firma', max_length=255)
    name2 = models.CharField('Name Zusatz', max_length=255, blank=True)
    contact_person = models.CharField('Ansprechpartner', max_length=255, blank=True)
    phone = models.CharField('Telefon', max_length=80, blank=True)
    email = models.EmailField('E-Mail', blank=True)
    street = models.CharField('Straße', max_length=255, blank=True)
    postcode = models.CharField('PLZ', max_length=20, blank=True)
    city = models.CharField('Stadt', max_length=100, blank=True)
    country = models.CharField('Land', max_length=2, default='DE')
    payment_days = models.PositiveIntegerField('Zahlungsziel Tage', default=14)
    aareon_receiver_id = models.CharField('Aareon/ARP Rechnungsempfänger-ID', max_length=120, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CustomerObject(TimeStampedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='customer_objects')
    name = models.CharField('Objekt / Leistungsort', max_length=255)
    street = models.CharField('Objekt Straße', max_length=255, blank=True)
    postcode = models.CharField('Objekt PLZ', max_length=20, blank=True)
    city = models.CharField('Objekt Stadt', max_length=100, blank=True)
    country = models.CharField('Land', max_length=2, default='DE')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CustomerNote(TimeStampedModel):
    """Freie, unveränderliche Gesprächs- oder Arbeitsnotiz in der Kundenakte."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sedo_customer_notes',
    )
    text = models.TextField('Notiz', max_length=4000)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['customer', '-created_at'], name='custnote_customer_date_idx')]

    def __str__(self):
        return f'Notiz zu {self.customer}'


class WorkTask(TimeStampedModel):
    """Planbare Wiedervorlage mit optional genau einem fachlichen Bezug."""
    PRIORITIES = [('normal', 'Normal'), ('high', 'Wichtig')]
    STATUS = [('open', 'Offen'), ('done', 'Erledigt')]

    title = models.CharField('Aufgabe', max_length=255)
    note = models.TextField('Notiz', blank=True, max_length=4000)
    due_date = models.DateField('Fällig am', db_index=True)
    priority = models.CharField('Priorität', max_length=12, choices=PRIORITIES, default='normal')
    status = models.CharField('Status', max_length=12, choices=STATUS, default='open', db_index=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='work_tasks')
    offer = models.ForeignKey(
        'Offer', on_delete=models.SET_NULL, null=True, blank=True, related_name='work_tasks')
    invoice = models.ForeignKey(
        'Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='work_tasks')
    created_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_sedo_tasks')
    completed_at = models.DateTimeField('Erledigt am', null=True, blank=True)

    class Meta:
        ordering = ['status', 'due_date', 'priority', '-created_at']
        indexes = [models.Index(fields=['status', 'due_date'], name='worktask_status_due_idx')]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(customer__isnull=True, offer__isnull=True, invoice__isnull=True)
                    | models.Q(customer__isnull=False, offer__isnull=True, invoice__isnull=True)
                    | models.Q(customer__isnull=True, offer__isnull=False, invoice__isnull=True)
                    | models.Q(customer__isnull=True, offer__isnull=True, invoice__isnull=False)
                ),
                name='worktask_max_one_reference',
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        return self.status == 'open' and self.due_date < timezone.localdate()

    @property
    def reference_label(self):
        if self.customer_id:
            return f'Kunde {self.customer}'
        if self.offer_id:
            return f'Angebot {self.offer}'
        if self.invoice_id:
            return f'Rechnung {self.invoice}'
        return 'Allgemein'


class Invoice(SoftDeleteModel):
    STATUS = [
        ('entwurf', 'Entwurf'),
        ('erstellt', 'Erstellt'),
        ('versendet', 'Versendet'),
        ('ueberfaellig', 'Überfällig'),
        ('teilbezahlt', 'Teilbezahlt'),
        ('gemahnt', 'Gemahnt'),
        ('bezahlt', 'Bezahlt'),
        ('storniert', 'Storniert'),
        ('korrigiert', 'Korrigiert'),
    ]
    LIFECYCLE = [
        ('draft', 'Entwurf'),
        ('issued', 'Ausgestellt'),
        ('corrected', 'Ersetzt durch Korrektur'),
        ('cancelled', 'Storniert'),
    ]
    PAYMENT_STATES = [
        ('unpaid', 'Offen'), ('partial', 'Teilbezahlt'), ('paid', 'Bezahlt'),
    ]

    # Das Feld `status` trägt zwei Bedeutungen: den Zahlstatus (erstellt,
    # versendet, ueberfaellig, teilbezahlt, gemahnt, bezahlt) und den
    # Belegzustand (entwurf, korrigiert, storniert). Der Zahlstatus ist eine
    # ABGELEITETE Größe – calc.abgeleiteter_zahlstatus() berechnet ihn aus
    # Zahlungen, Mahnungen und Fälligkeit, er ist jederzeit reproduzierbar.
    # Der Belegzustand ist eine GESETZTE Größe: 'korrigiert', 'storniert' und
    # 'entwurf' entstehen an genau je einer Stelle und lassen sich aus nichts
    # rekonstruieren.
    #
    # Review-Fund F-07 war exakt das: eine abgeleitete Größe überschreibt eine
    # gesetzte. Diese drei Listen sind die eine Quelle dafür, wer wen nicht
    # anfassen darf. Reine Klassenkonstanten – für makemigrations unsichtbar,
    # deshalb ohne Migration.
    GESCHUETZTE_STATUS = ('entwurf', 'korrigiert', 'storniert')
    NICHT_OFFENE_STATUS = GESCHUETZTE_STATUS + ('bezahlt',)
    GESPERRT_FUER_MAHNUNG = NICHT_OFFENE_STATUS

    TYPE_CODES = [
        ('380', 'Rechnung'),
        ('384', 'Korrekturrechnung'),
        ('457', 'Stornorechnung'),
    ]
    number = models.CharField('Rechnungsnummer', max_length=80, unique=True)
    invoice_type = models.CharField('Rechnungsart', max_length=10, choices=TYPE_CODES, default='380')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='invoices')
    obj = models.ForeignKey(CustomerObject, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    issue_date = models.DateField('Rechnungsdatum', default=timezone.localdate)
    due_date = models.DateField('Fälligkeitsdatum', null=True, blank=True)
    period_start = models.DateField('Leistungszeitraum von', null=True, blank=True)
    period_end = models.DateField('Leistungszeitraum bis', null=True, blank=True)
    net_total = models.DecimalField('Netto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField('USt %', max_digits=5, decimal_places=2, default=Decimal('19.00'))
    vat_amount = models.DecimalField('USt Betrag', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    gross_total = models.DecimalField('Brutto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    # Legacy-Anzeige-/Kompatibilitätscache. Die kanonische Belegwahrheit ist lifecycle.
    status = models.CharField('Status', max_length=30, choices=STATUS, default='erstellt')
    lifecycle = models.CharField('Belegzustand', max_length=16, choices=LIFECYCLE, default='issued')
    pdf_file = models.FileField('PDF', upload_to='invoices/pdf/', blank=True)
    xml_file = models.FileField('XML', upload_to='invoices/xml/', blank=True)
    zugferd_file = models.FileField('ZUGFeRD-PDF', upload_to='invoices/zugferd/', blank=True)
    note = models.TextField('Hinweis', blank=True)
    source = models.CharField('Quelle', max_length=80, blank=True)
    reference_invoice = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='followups')

    class Meta:
        ordering = ['-issue_date', '-number']
        indexes = [
            # Statusfilter; der linke Präfix deckt auch status allein ab.
            models.Index(fields=['status', 'due_date'], name='invoice_status_due_idx'),
            # Mahnauswahl: NOT IN(status) liefert keinen festen Status-Präfix.
            # Fälligkeit zuerst; Statusprüfung im Index, nur aktive Belege.
            models.Index(fields=['due_date', 'status'],
                         condition=models.Q(deleted_at__isnull=True),
                         name='invoice_active_due_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=('entwurf', 'korrigiert', 'storniert'))
                    | models.Q(status='entwurf', lifecycle='draft')
                    | models.Q(status='korrigiert', lifecycle='corrected')
                    | models.Q(status='storniert', lifecycle='cancelled')
                ),
                name='invoice_lifecycle_legacy_consistent',
            ),
            # Idempotenz-Schutz für den GWG-Monatslauf: pro Kunde, Objekt und
            # Leistungszeitraum nur EINE aktive Monatsrechnung – auch wenn der
            # Lauf doppelt/parallel gestartet wird (Check-then-act reicht nicht).
            models.UniqueConstraint(
                fields=['customer', 'obj', 'period_start', 'period_end'],
                condition=(models.Q(source='GWG Monatsrechnung')
                           & models.Q(deleted_at__isnull=True)
                           & ~models.Q(lifecycle='cancelled')),
                name='invoice_unique_gwg_monthly_run',
            ),
            # Harte Parallelitäts-Schranken (v3.23.1): Die ensure_*-Guards
            # prüfen VOR der Transaktion – zwei gleichzeitige Requests könnten
            # beide passieren. Diese Constraints erzwingen auf DB-Ebene
            # maximal EIN aktives Storno bzw. EINE aktive Korrektur je
            # Ursprungsrechnung; der zweite Request bekommt IntegrityError,
            # den die Services in einen WorkflowError übersetzen.
            models.UniqueConstraint(
                fields=['reference_invoice'],
                condition=(models.Q(invoice_type='457')
                           & models.Q(deleted_at__isnull=True)),
                name='invoice_unique_active_storno',
            ),
            models.UniqueConstraint(
                fields=['reference_invoice'],
                condition=(models.Q(invoice_type='384')
                           & models.Q(deleted_at__isnull=True)),
                name='invoice_unique_active_correction',
            ),
        ]

    def save(self, *args, **kwargs):
        # Übergangskompatibilität für vorhandene Aufrufer/Tests: bei einer
        # NEUEN Rechnung mit altem Belegstatus wird lifecycle einmalig
        # abgeleitet. Spätere Status-Cache-Updates ändern lifecycle nie.
        if self._state.adding and self.lifecycle == 'issued':
            self.lifecycle = {
                'entwurf': 'draft', 'korrigiert': 'corrected', 'storniert': 'cancelled',
            }.get(self.status, self.lifecycle)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.number

    @property
    def paid_amount(self):
        # Rückwärtsrelation läuft über all_objects; soft-gelöschte Zahlungen
        # explizit ausschließen.
        active_payments = getattr(self, '_timeline_active_payments', None)
        if active_payments is None:
            active_payments = self.payments.filter(deleted_at__isnull=True)
        return sum((p.amount for p in active_payments), Decimal('0.00'))

    @property
    def open_amount(self):
        return (Decimal(str(self.gross_total)) - self.paid_amount).quantize(Decimal('0.01'))

    @property
    def is_draft(self):
        return self.lifecycle == 'draft'

    @property
    def is_issued(self):
        return self.lifecycle == 'issued'

    @property
    def is_corrected(self):
        return self.lifecycle == 'corrected'

    @property
    def is_cancelled(self):
        return self.lifecycle == 'cancelled'

    @property
    def payment_state(self):
        """Zahlungszustand ausschließlich aus aktiven Zahlungen und Forderung ableiten."""
        gross = Decimal(str(self.gross_total))
        if gross <= Decimal('0.00'):
            return 'unpaid'
        paid = self.paid_amount
        if paid >= gross:
            return 'paid'
        if paid > Decimal('0.00'):
            return 'partial'
        return 'unpaid'

    @property
    def display_state(self):
        """Ein UI-Status ohne Vermischung der kanonischen Zustände."""
        if self.lifecycle == 'draft':
            return 'entwurf'
        if self.lifecycle == 'corrected':
            return 'korrigiert'
        if self.lifecycle == 'cancelled':
            return 'storniert'
        if self.invoice_type == '457':
            return 'erstellt'
        if self.payment_state == 'paid':
            return 'bezahlt'
        if self.payment_state == 'partial':
            return 'teilbezahlt'
        if self.dunning_documents.exists():
            return 'gemahnt'
        if self.due_date and self.due_date < timezone.localdate():
            return 'ueberfaellig'
        if self.status == 'versendet':
            return 'versendet'
        return 'erstellt'

    @property
    def display_state_label(self):
        return dict(self.STATUS).get(self.display_state, self.display_state)

    @property
    def active_storno(self):
        # Rückwärtsrelation läuft über all_objects; soft-gelöschte Folgebelege
        # explizit ausschließen. Entwürfe zählen NICHT als aktiver Folgebeleg:
        # sie wurden nie ausgestellt und dürfen das Original nicht blockieren –
        # sonst entsteht eine Sackgasse (siehe active_correction).
        return self.followups.filter(invoice_type='457', deleted_at__isnull=True).exclude(
            lifecycle='draft').first()

    @property
    def active_correction(self):
        """Die ausgestellte Korrektur zu dieser Rechnung – oder None.

        Entwürfe sind ausgenommen. Vorher galt ein Korrektur-ENTWURF als aktive
        Korrektur, und weil das Original dabei nicht als 'korrigiert' markiert
        wird, entstand eine Sackgasse: Das Original ließ sich weder zahlen noch
        stornieren noch erneut korrigieren noch ausblenden, der Entwurf ebenso
        wenig – und eine Finalisieren-Route gibt es (noch) nicht. Gleichzeitig
        buchte der Bankimport weiter auf genau dieses Original. Zwei Wege
        desselben Programms widersprachen sich bei derselben Zahlung.
        """
        return self.followups.filter(invoice_type='384', deleted_at__isnull=True).exclude(
            lifecycle='draft').first()

    @property
    def active_followups(self):
        return self.followups.filter(deleted_at__isnull=True).order_by('number')

    @property
    def is_overdue(self):
        # Ein Entwurf und ein durch eine Korrektur ersetztes Original galten
        # bisher als überfällig – sie erschienen rot in „Offene Posten" und
        # zählten in die Summe der überfälligen Forderungen (F-07). Ein
        # Stornobeleg (457) ist ebenfalls nie überfällig: er fordert nichts.
        if not self.due_date or self.lifecycle != 'issued' or self.invoice_type == '457':
            return False
        return self.open_amount > Decimal('0.00') and self.due_date < timezone.localdate()

    @property
    def days_overdue(self):
        if not self.due_date or not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days


class InvoiceItem(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    position = models.PositiveIntegerField(default=1)
    description = models.CharField('Bezeichnung', max_length=255)
    # Leistungsbeschreibung: erscheint im PDF unter dem Titel und als BT-154
    # (SpecifiedTradeProduct/Description) in der ZUGFeRD-XML.
    long_text = models.TextField('Leistungsbeschreibung', blank=True, default='')
    quantity = models.DecimalField('Menge', max_digits=12, decimal_places=2, default=Decimal('1.00'))
    unit = models.CharField('Einheit', max_length=40, default='Stk.')
    unit_price = models.DecimalField('Einzelpreis netto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    article_no = models.CharField('Artikelnr.', max_length=80, blank=True)
    leistungsart_id = models.CharField('Leistungsart-ID', max_length=120, blank=True)
    leistungsart_text = models.CharField('Leistungsart-Text', max_length=255, blank=True)

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    class Meta:
        ordering = ['position']


class RecurringInvoiceTemplate(TimeStampedModel):
    STATUS = [('active', 'Aktiv'), ('paused', 'Pausiert')]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='recurring_templates')
    obj = models.ForeignKey(CustomerObject, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_templates')
    title = models.CharField('Vorlagenname', max_length=255)
    status = models.CharField('Status', max_length=20, choices=STATUS, default='active')
    vat_rate = models.DecimalField('USt %', max_digits=5, decimal_places=2, default=Decimal('19.00'))
    payment_days = models.PositiveIntegerField('Zahlungsziel Tage', default=14)
    aareon_receiver_id = models.CharField('Aareon/ARP Rechnungsempfänger-ID', max_length=120, blank=True)
    note = models.TextField('Hinweis', blank=True)

    class Meta:
        ordering = ['customer__name', 'title']

    def __str__(self):
        return f'{self.customer.name} · {self.title}'


class RecurringInvoiceItem(TimeStampedModel):
    template = models.ForeignKey(RecurringInvoiceTemplate, on_delete=models.CASCADE, related_name='items')
    position = models.PositiveIntegerField(default=1)
    description = models.CharField('Bezeichnung', max_length=255)
    long_text = models.TextField('Leistungsbeschreibung', blank=True, default='')
    quantity = models.DecimalField('Menge', max_digits=12, decimal_places=2, default=Decimal('1.00'))
    unit = models.CharField('Einheit', max_length=40, default='Stk.')
    unit_price = models.DecimalField('Einzelpreis netto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    article_no = models.CharField('Artikelnr.', max_length=80, blank=True)

    class Meta:
        ordering = ['position']

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class Offer(SoftDeleteModel):
    # Seit v3.22 Soft-Delete wie Rechnungen/Zahlungen (Angebote sind
    # Geschäftsbriefe – GoBD-konsistent nur ausblenden, nie hart löschen).
    # ⚠️ Rückwärtsrelationen (customer.offers …) laufen über all_objects –
    # soft-gelöschte Angebote explizit mit deleted_at__isnull=True filtern!
    STATUS = [
        ('offen', 'Offen'),
        ('angenommen', 'Angenommen'),
        ('abgelehnt', 'Abgelehnt'),
        ('in_rechnung', 'In Rechnung übernommen'),
    ]
    number = models.CharField('Angebotsnummer', max_length=80, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='offers')
    obj = models.ForeignKey(CustomerObject, on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    issue_date = models.DateField('Angebotsdatum', default=timezone.localdate)
    valid_until = models.DateField('Gültig bis', null=True, blank=True)
    net_total = models.DecimalField('Netto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField('USt %', max_digits=5, decimal_places=2, default=Decimal('19.00'))
    vat_amount = models.DecimalField('USt Betrag', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    gross_total = models.DecimalField('Brutto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField('Status', max_length=30, choices=STATUS, default='offen')
    pdf_file = models.FileField('PDF', upload_to='offers/pdf/', blank=True)
    linked_invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='source_offer')
    follow_up_date = models.DateField('Wiedervorlage am', null=True, blank=True, db_index=True)
    follow_up_note = models.TextField('Wiedervorlage – Notiz', blank=True, default='')
    last_follow_up_at = models.DateTimeField('Zuletzt nachgefasst', null=True, blank=True)

    class Meta:
        ordering = ['-issue_date', '-number']

    def __str__(self):
        return self.number


class OfferItem(TimeStampedModel):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name='items')
    position = models.PositiveIntegerField(default=1)
    description = models.CharField('Bezeichnung', max_length=255)
    long_text = models.TextField('Leistungsbeschreibung', blank=True, default='')
    quantity = models.DecimalField('Menge', max_digits=12, decimal_places=2, default=Decimal('1.00'))
    unit = models.CharField('Einheit', max_length=40, default='Stk.')
    unit_price = models.DecimalField('Einzelpreis netto', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    article_no = models.CharField('Artikelnr.', max_length=80, blank=True)

    class Meta:
        ordering = ['position']



class ServiceCatalogItem(TimeStampedModel):
    """Selbstlernender Leistungskatalog für den KI-Angebotsassistenten.

    Neue Leistungen werden beim ersten Auftauchen in einem Angebot durch die KI
    angelegt (created_by_ai=True) und danach für konsistente Leistungstexte
    wiederverwendet. Manuell pflegbar über die Katalogseite.
    """
    name = models.CharField('Leistung', max_length=120, unique=True)
    long_text = models.TextField('Leistungsbeschreibung', blank=True)
    default_unit = models.CharField('Übliche Einheit', max_length=40, blank=True)
    created_by_ai = models.BooleanField('Durch KI angelegt', default=False)

    class Meta:
        ordering = ['name']
        verbose_name = 'Katalog-Leistung'
        verbose_name_plural = 'Katalog-Leistungen'

    def __str__(self):
        return self.name


class Payment(SoftDeleteModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    booking_date = models.DateField('Buchungsdatum')
    amount = models.DecimalField('Betrag', max_digits=12, decimal_places=2)
    payer_name = models.CharField('Name', max_length=255, blank=True)
    purpose = models.TextField('Verwendungszweck', blank=True)
    # Stabile Banktransaktions-ID aus dem Export (falls vorhanden) – primärer
    # Doubletten-Schlüssel; die Heuristik Datum+Betrag+Text ist nur Fallback.
    external_id = models.CharField('Bank-Transaktions-ID', max_length=120, blank=True, db_index=True)
    raw = models.JSONField('Rohdaten', default=dict, blank=True)
    matched_automatically = models.BooleanField(default=False)

    class Meta:
        ordering = ['-booking_date']
        constraints = [
            models.UniqueConstraint(
                fields=['external_id'],
                condition=models.Q(external_id__gt=''),
                name='payment_external_id_unique',
            ),
        ]


class NumberRange(TimeStampedModel):
    """Zentraler Nummernkreis je Präfix und Monat (GoBD: lückenlos, eindeutig).

    ``next_number`` wird ausschließlich über ein atomares F()-Update in
    ``services.numbering`` hochgezählt – innerhalb derselben Transaktion wie
    die Beleganlage. Schlägt die Anlage fehl, rollt auch der Zähler zurück;
    es entstehen keine „verbrannten“ Nummern.
    (``select_for_update`` wäre auf SQLite wirkungslos, deshalb F()-Update.)
    """
    prefix = models.CharField('Präfix', max_length=10)
    year = models.PositiveIntegerField('Jahr')
    month = models.PositiveIntegerField('Monat')
    next_number = models.PositiveIntegerField('Nächste laufende Nummer', default=1)

    class Meta:
        verbose_name = 'Nummernkreis'
        verbose_name_plural = 'Nummernkreise'
        constraints = [
            models.UniqueConstraint(fields=['prefix', 'year', 'month'],
                                    name='numberrange_unique_period'),
        ]

    def __str__(self):
        return f'{self.prefix}{self.year}{self.month:02d} → {self.next_number:04d}'


class CashbookEntry(TimeStampedModel):
    KIND = [('income', 'Einnahme'), ('expense', 'Ausgabe'), ('correction', 'Korrektur')]
    month = models.CharField('Monat', max_length=7)  # YYYY-MM
    booking_no = models.PositiveIntegerField('Buchungsnummer')
    receipt_no = models.CharField('Belegnummer', max_length=80, blank=True)
    booking_date = models.DateField('Buchungstag')
    kind = models.CharField('Art', max_length=20, choices=KIND)
    name = models.CharField('Zahlender / Empfänger', max_length=255)
    text = models.TextField('Buchungstext', blank=True)
    amount = models.DecimalField('Betrag', max_digits=12, decimal_places=2)
    is_locked = models.BooleanField(default=False)
    correction_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['month', 'booking_no']
        unique_together = [('month', 'booking_no')]


class AareonMapping(TimeStampedModel):
    billing_unit_sender = models.CharField('Rechnungssteller Abrechnungseinheit', max_length=255)
    service_id = models.CharField('Leistungsart-ID', max_length=120)
    company_code = models.CharField('Buchungskreis', max_length=80, blank=True)
    erp_contract_no = models.CharField('ERP-Vertragsnummer', max_length=120, blank=True)
    we_internal = models.CharField('WE intern', max_length=120, blank=True)
    ae_internal = models.CharField('AE intern', max_length=120, blank=True)
    nksl_internal = models.CharField('NkSl intern', max_length=120, blank=True)
    account = models.CharField('Sachkonto', max_length=120, blank=True)
    valid_from = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['billing_unit_sender', 'service_id']
        unique_together = [('billing_unit_sender', 'service_id')]


class AppSetting(TimeStampedModel):
    key = models.CharField('Schlüssel', max_length=120, unique=True)
    value = models.TextField('Wert', blank=True)

    class Meta:
        ordering = ['key']

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        cls.objects.update_or_create(key=key, defaults={'value': value or ''})


class DunningDocument(TimeStampedModel):
    KIND = [('reminder', 'Zahlungserinnerung'), ('dunning', 'Mahnung')]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='dunning_documents')
    kind = models.CharField('Art', max_length=20, choices=KIND)
    document_date = models.DateField('Datum', default=timezone.localdate)
    due_date_new = models.DateField('Neue Zahlungsfrist', null=True, blank=True)
    amount_open = models.DecimalField('Offener Betrag', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    pdf_file = models.FileField('PDF', upload_to='dunning/', blank=True)

    class Meta:
        ordering = ['-document_date', '-created_at']
        constraints = [
            # Stufenmodell: je Rechnung maximal EINE Erinnerung und EINE
            # Mahnung – auch bei parallelen Requests (DB-seitige Schranke,
            # die ensure_dunning_allowed alleine nicht bieten kann).
            models.UniqueConstraint(fields=['invoice', 'kind'],
                                    name='dunning_invoice_kind_unique'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} zu {self.invoice.number}'


class AuditLog(TimeStampedModel):
    ACTIONS = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('create', 'Erstellt'),
        ('update', 'Geändert'),
        ('delete', 'Gelöscht'),
        ('generate', 'Erzeugt'),
        ('payment', 'Zahlung'),
        ('backup', 'Backup'),
        ('import', 'Import'),
        ('security', 'Sicherheit'),
    ]
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='sedo_audit_logs')
    action = models.CharField('Aktion', max_length=30, choices=ACTIONS)
    object_type = models.CharField('Objekttyp', max_length=80, blank=True)
    object_id = models.CharField('Objekt-ID', max_length=80, blank=True)
    object_repr = models.CharField('Objekt', max_length=255, blank=True)
    message = models.TextField('Hinweis', blank=True)
    ip_address = models.GenericIPAddressField('IP-Adresse', null=True, blank=True)
    user_agent = models.TextField('User Agent', blank=True)
    metadata = models.JSONField('Metadaten', default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Audit-Log'
        verbose_name_plural = 'Audit-Logs'

    def __str__(self):
        who = self.user.get_username() if self.user_id else 'System'
        return f'{self.created_at:%Y-%m-%d %H:%M} · {who} · {self.get_action_display()} · {self.object_repr or self.object_type}'
