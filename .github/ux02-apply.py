from pathlib import Path

p = Path('sedo_rechnungstool_v2/sedo/templates/sedo/base.html')
s = p.read_text(encoding='utf-8')

# App-Shell-CSS nach dem Design-System laden.
old = '  <link rel="stylesheet" href="/static/sedo/design-system-v2.css?v=1">\n'
new = old + '  <link rel="stylesheet" href="/static/sedo/app-shell-v2.css?v=1">\n'
if s.count(old) != 1:
    raise SystemExit(f'CSS-Anker {s.count(old)} statt 1')
s = s.replace(old, new, 1)

# Alte Suche aus der Sidebar entfernen; sie wandert in die Command-Bar.
old_search = '''    <div class="global-search">
      <input type="search" id="global-search" placeholder="Suchen …" autocomplete="off"
             aria-label="Globale Suche: Kunden, Rechnungen, Angebote, Objekte">
      <div class="global-search-results" id="global-search-results" hidden></div>
    </div>
'''
if s.count(old_search) != 1:
    raise SystemExit(f'Sidebar-Suchanker {s.count(old_search)} statt 1')
s = s.replace(old_search, '', 1)

# Navigation komplett nach Aufgabenhäufigkeit gruppieren.
nav_start = s.index('    <nav class="nav">')
nav_end = s.index('    </nav>', nav_start) + len('    </nav>')
nav = '''    <nav class="nav" aria-label="Hauptnavigation">
      <div class="drawer-title"><span>Tägliche Arbeit</span></div>
      <a {% if request.resolver_match.url_name == 'dashboard' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:dashboard' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-dashboard"/></svg></span><span class="nav-text">Dashboard</span></a>
      <a {% if 'customer' in request.resolver_match.url_name or 'object' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:customer_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-users"/></svg></span><span class="nav-text">Kunden</span></a>
      <a {% if 'invoice' in request.resolver_match.url_name or 'reminder' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:invoice_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-invoice"/></svg></span><span class="nav-text">Rechnungen</span></a>
      <a {% if 'offer' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:offer_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-offer"/></svg></span><span class="nav-text">Angebote</span></a>
      <a {% if request.resolver_match.url_name == 'open_items' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:open_items' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-open"/></svg></span><span class="nav-text">Offene Posten</span></a>

      <div class="drawer-title"><span>Abrechnung</span></div>
      <a {% if request.resolver_match.url_name == 'aareon_monthly' or request.resolver_match.url_name == 'aareon_import' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:aareon_monthly' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-building"/></svg></span><span class="nav-text">GWG</span></a>
      <a {% if 'recurring' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:recurring_monthly' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-repeat"/></svg></span><span class="nav-text">Abo-Rechnungen</span></a>
      <a {% if 'payment' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:payment_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-payment"/></svg></span><span class="nav-text">Zahlungen</span></a>
      <a {% if 'cashbook' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:cashbook_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-cashbook"/></svg></span><span class="nav-text">Kassenbuch</span></a>
      <a {% if request.resolver_match.url_name == 'month_close' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:month_close' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-monthclose"/></svg></span><span class="nav-text">Monatsabschluss</span></a>

      <div class="drawer-title"><span>System</span></div>
      <a {% if 'service_catalog' in request.resolver_match.url_name %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:service_catalog_list' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-catalog"/></svg></span><span class="nav-text">Leistungskatalog</span></a>
      <details class="nav-group"{% if request.resolver_match.url_name == 'settings' or request.resolver_match.url_name == 'backup' or request.resolver_match.url_name == 'security_status' or request.resolver_match.url_name == 'audit_log' %} open{% endif %}>
        <summary><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-settings"/></svg></span><span class="nav-text">Administration</span><span class="nav-caret">▾</span></summary>
        <div class="nav-sub">
          <a {% if request.resolver_match.url_name == 'settings' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:settings' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-settings"/></svg></span><span class="nav-text">Einstellungen</span></a>
          <a {% if request.resolver_match.url_name == 'backup' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:backup' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-backup"/></svg></span><span class="nav-text">Backup</span></a>
          <a {% if request.resolver_match.url_name == 'security_status' or request.resolver_match.url_name == 'audit_log' %}class="active" aria-current="page"{% endif %} href="{% url 'sedo:security_status' %}"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-shield"/></svg></span><span class="nav-text">Sicherheit</span></a>
          <a href="/admin/"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-admin"/></svg></span><span class="nav-text">Django-Admin</span></a>
          <form method="post" action="{% url 'logout' %}" class="nav-logout-form">{% csrf_token %}<button type="submit" class="nav-logout-btn"><span class="nav-ico"><svg class="ico" aria-hidden="true"><use href="#i-logout"/></svg></span><span class="nav-text">Abmelden</span></button></form>
        </div>
      </details>
    </nav>'''
s = s[:nav_start] + nav + s[nav_end:]

# Command-Bar vor den Seiteninhalt setzen.
old_main = '''  <main class="main">
    <div class="page-shell">
'''
new_main = '''  <main class="main">
    <header class="app-commandbar" aria-label="Schnellzugriff">
      <div class="command-search">
        <div class="global-search">
          <input type="search" id="global-search" placeholder="Suchen oder Aktion starten …" autocomplete="off"
                 aria-label="Globale Suche: Kunden, Rechnungen, Angebote, Objekte und Schnellaktionen"
                 data-action-invoice="{% url 'sedo:invoice_create' %}"
                 data-action-offer="{% url 'sedo:offer_create' %}"
                 data-action-customer="{% url 'sedo:customer_create' %}"
                 data-action-open-items="{% url 'sedo:open_items' %}">
          <span class="command-shortcut" aria-hidden="true">⌘/Ctrl K</span>
          <div class="global-search-results" id="global-search-results" hidden></div>
        </div>
      </div>
      <div class="command-actions" aria-label="Schnellaktionen">
        <a class="command-action" href="{% url 'sedo:invoice_create' %}"><svg class="ico" aria-hidden="true"><use href="#i-invoice"/></svg><span>Neue Rechnung</span></a>
        <a class="command-action" href="{% url 'sedo:offer_create' %}"><svg class="ico" aria-hidden="true"><use href="#i-offer"/></svg><span>Angebot</span></a>
        <span class="command-user" aria-hidden="true">SE</span>
      </div>
    </header>
    <div class="page-shell">
'''
if s.count(old_main) != 1:
    raise SystemExit(f'Main-Anker {s.count(old_main)} statt 1')
s = s.replace(old_main, new_main, 1)

# Neue Command-Center-Version laden.
s = s.replace('/static/sedo/global_search.js?v=2_1', '/static/sedo/global_search.js?v=3_0')

p.write_text(s, encoding='utf-8')
