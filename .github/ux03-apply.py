from pathlib import Path

ROOT = Path('sedo_rechnungstool_v2/sedo')


def replace(path, old, new, count=1):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    actual = s.count(old)
    if actual != count:
        raise SystemExit(f'{path}: Anker {actual} statt {count}')
    p.write_text(s.replace(old, new, count), encoding='utf-8')

# State-System global nach App-Shell laden.
replace(
    'templates/sedo/base.html',
    '  <link rel="stylesheet" href="/static/sedo/app-shell-v2.css?v=1">\n',
    '  <link rel="stylesheet" href="/static/sedo/app-shell-v2.css?v=1">\n  <link rel="stylesheet" href="/static/sedo/state-system-v2.css?v=1">\n',
)

# Rechnungsarchiv: Composite-Badge durch getrennten State Stack ersetzen.
replace(
    'templates/sedo/invoice_list.html',
    '''        <td>\n          <span class="badge\n            {% if r.display_state == 'bezahlt' %}green\n            {% elif r.display_state == 'storniert' %}red\n            {% elif r.display_state == 'ueberfaellig' %}red\n            {% elif r.display_state == 'gemahnt' %}amber\n            {% elif r.display_state == 'entwurf' %}yellow\n            {% elif r.display_state == 'versendet' %}blue\n            {% endif %}">{{ r.display_state_label }}</span>\n        </td>''',
    '''        <td>{% include 'sedo/_invoice_state_stack.html' with invoice=r compact=1 %}</td>''',
)
replace('templates/sedo/invoice_list.html', '<th>Status</th>', '<th>Zustand</th>')

# Offene Posten: Legacy-Statusbadge vollständig entfernen.
old = '''        {# 'korrigiert' kann auf dem Reiter „Alle ohne Storno" erscheinen und war\n           dort bisher farblos. 'entwurf' steht in keiner dieser Listen mehr –\n           der Zweig bleibt trotzdem stehen, falls sich die Mengen ändern.\n           Nur vorhandene Badge-Klassen verwenden: green/red/amber/yellow. #}\n        <td><span class="badge status-{{ i.status }} {% if i.status == 'bezahlt' %}green{% elif i.status == 'ueberfaellig' or i.status == 'storniert' %}red{% elif i.status == 'gemahnt' %}amber{% elif i.status == 'entwurf' or i.status == 'korrigiert' %}yellow{% endif %}">{{ i.get_status_display }}</span></td>'''
new = '''        <td>{% include 'sedo/_invoice_state_stack.html' with invoice=i compact=1 %}</td>'''
replace('templates/sedo/open_items.html', old, new)
replace('templates/sedo/open_items.html', '<th>Offen</th><th>Status</th><th>Aktionen</th>', '<th>Offen</th><th>Zustand</th><th>Aktionen</th>')

# Dashboard: letzte Rechnungen verwenden dieselbe Komponente.
replace(
    'templates/sedo/dashboard.html',
    '''<td><span class="badge {% if r.display_state == 'bezahlt' %}green{% elif r.display_state == 'storniert' or r.display_state == 'ueberfaellig' %}red{% elif r.display_state == 'gemahnt' %}amber{% endif %}">{{ r.display_state_label }}</span></td>''',
    '''<td>{% include 'sedo/_invoice_state_stack.html' with invoice=r compact=1 %}</td>''',
)
replace('templates/sedo/dashboard.html', '<th>Brutto</th><th>Status</th>', '<th>Brutto</th><th>Zustand</th>')

# Rechnungsdetail: prominent State Stack statt Composite-Badge.
replace(
    'templates/sedo/invoice_detail.html',
    '''    <span class="badge status-{{ invoice.display_state }}">{{ invoice.display_state_label }}</span>''',
    '''    {% include 'sedo/_invoice_state_stack.html' with invoice=invoice %}''',
)

# Detail-Fakten: alten gemischten Status explizit in Beleg- und Zahlungszustand teilen.
replace(
    'templates/sedo/invoice_detail.html',
    '''      <div><span>Status</span><strong>{{ invoice.get_status_display }}</strong></div>''',
    '''      <div><span>Belegzustand</span><strong>{{ invoice.get_lifecycle_display }}</strong></div>\n      <div><span>Zahlung</span><strong>{% if invoice.lifecycle != 'issued' or invoice.invoice_type == '457' %}—{% elif invoice.payment_state == 'paid' %}Bezahlt{% elif invoice.payment_state == 'partial' %}Teilbezahlt{% else %}Offen{% endif %}</strong></div>''',
)
