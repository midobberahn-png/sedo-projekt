from pathlib import Path

ROOT = Path('sedo_rechnungstool_v2/sedo')


def replace(path, old, new, count=1):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    actual = s.count(old)
    if actual != count:
        raise SystemExit(f'{path}: Anker {actual} statt {count}')
    p.write_text(s.replace(old, new, count), encoding='utf-8')

# Detail-CSS nach State-System laden.
replace(
    'templates/sedo/base.html',
    '  <link rel="stylesheet" href="/static/sedo/state-system-v2.css?v=1">\n',
    '  <link rel="stylesheet" href="/static/sedo/state-system-v2.css?v=1">\n  <link rel="stylesheet" href="/static/sedo/invoice-detail-v2.css?v=1">\n',
)

p = ROOT / 'templates/sedo/invoice_detail.html'
s = p.read_text(encoding='utf-8')
start = s.index('  <div class="toolbar detail-actions">')
end = s.index('  </div>\n</div>\n\n{% if mahn_hindernis', start)
old_actions = s[start:end + len('  </div>')]
s = s[:start] + "  {% include 'sedo/_invoice_actions.html' %}" + s[end + len('  </div>'):]

# Entwurfs-Hinweis: keine zweite Hauptaktion mehr; nur Kontext + Bearbeiten.
old = '''  <div class="toolbar">\n    <a class="btn primary" href="{% url 'sedo:invoice_edit' invoice.pk %}">Bearbeiten</a>\n    <form method="post" action="{% url 'sedo:invoice_finalize' invoice.pk %}">\n      {% csrf_token %}<button class="btn success" type="submit">Rechnung finalisieren</button>\n    </form>\n  </div>'''
new = '''  <div class="draft-guidance-actions">\n    <a class="btn" href="{% url 'sedo:invoice_edit' invoice.pk %}">Entwurfsdaten prüfen</a>\n  </div>'''
if s.count(old) != 1:
    raise SystemExit(f'Draft-Aktionsanker {s.count(old)} statt 1')
s = s.replace(old, new, 1)

# Summenkarte: Restbetrag markieren bleibt kontextuelle Aktion, aber nicht zweite Primäraktion.
s = s.replace('class="btn success" type="submit">Restbetrag als bezahlt markieren',
              'class="btn primary-context-action" type="submit">Restbetrag als bezahlt markieren', 1)

p.write_text(s, encoding='utf-8')
