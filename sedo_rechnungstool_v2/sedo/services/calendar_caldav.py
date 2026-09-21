"""Small, bounded iCloud CalDAV transport with conditional writes.

Only Apple's calendar endpoints may receive credentials. No proxy inheritance,
unvalidated redirects, remote attachments or XML entities are used.
"""
import base64
import http.client
import re
import ssl
from urllib.parse import urljoin, urlsplit

import certifi
from lxml import etree

from .calendar_data import CalendarError, MAX_ICAL

DAV = 'DAV:'
CAL = 'urn:ietf:params:xml:ns:caldav'
ROOT = 'https://caldav.icloud.com/'
MAX_XML = 8 * 1024 * 1024


def checked_url(value, base=ROOT, within=None):
    url = urljoin(base, value)
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username or parts.password or parts.query or parts.fragment
                or parts.port not in (None, 443)
                or not re.fullmatch(r'(?:caldav|p[0-9]+-caldav)\.icloud\.com', parts.hostname or '')
                or any(char in url for char in '\r\n\x00\\')
                or any(token in parts.path.lower() for token in ('%2f', '%5c', '%2e', '/./', '/../'))
                or len(url) > 1500):
            raise ValueError()
        if within and (parts.netloc != urlsplit(within).netloc or not parts.path.startswith(urlsplit(within).path.rstrip('/') + '/')):
            raise ValueError()
    except ValueError as exc:
        raise CalendarError('iCloud hat eine unerwartete Kalenderadresse geliefert. Abgleich gestoppt.') from exc
    return url


def strong_etag(value):
    return bool(value and re.fullmatch(r'"[^"\r\n\x00]{1,490}"', value))


def xml_body(tag, properties):
    root = etree.Element(f'{{{DAV}}}{tag}', nsmap={'d': DAV, 'c': CAL})
    prop = etree.SubElement(root, f'{{{DAV}}}prop')
    for namespace, name in properties:
        etree.SubElement(prop, f'{{{namespace}}}{name}')
    return etree.tostring(root, xml_declaration=True, encoding='utf-8')


def multistatus(body):
    try:
        if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
            raise ValueError()
        root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        if root.tag != f'{{{DAV}}}multistatus':
            raise ValueError()
        responses = root.findall(f'{{{DAV}}}response')
        if len(responses) > 10001:
            raise ValueError()
        return responses
    except Exception as exc:
        raise CalendarError('Die Kalenderliste konnte nicht vollständig gelesen werden. Es wurden keine Termine entfernt.') from exc


def props(response):
    found = {}
    for item in response.findall(f'{{{DAV}}}propstat'):
        if ' 200 ' in (item.findtext(f'{{{DAV}}}status') or ''):
            prop = item.find(f'{{{DAV}}}prop')
            if prop is not None:
                found.update({child.tag: child for child in prop})
    return found


class ICloudDAV:
    def __init__(self, username, password):
        token = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
        self.authorization = 'Basic ' + token

    def request(self, method, url, body=None, headers=None, *, redirects=0):
        url = checked_url(url)
        parts = urlsplit(url)
        conn = http.client.HTTPSConnection(parts.hostname, timeout=20,
            context=ssl.create_default_context(cafile=certifi.where()))
        try:
            conn.request(method, parts.path or '/', body=body, headers={
                'Authorization': self.authorization, 'User-Agent': 'SEDO-Kalender/1',
                'Accept-Encoding': 'identity', **(headers or {})})
            response = conn.getresponse()
            status, result_headers = response.status, dict(response.getheaders())
            maximum = MAX_ICAL if method == 'GET' else MAX_XML
            data = response.read(maximum + 1)
            if len(data) > maximum:
                raise CalendarError('Die Antwort von iCloud ist zu groß. Abgleich abgebrochen.')
            if status in (301, 302, 307, 308) and method in ('GET', 'PROPFIND') and redirects < 3:
                location = response.getheader('Location')
                if not location:
                    raise CalendarError('iCloud hat keine gültige Weiterleitung geliefert.')
                return self.request(method, checked_url(location, url), body, headers, redirects=redirects + 1)
            if status in (401, 403):
                raise CalendarError('iCloud-Zugang oder Kalenderberechtigung prüfen. Es wird ein App-spezifisches Passwort benötigt.')
            return status, {key.lower(): value for key, value in result_headers.items()}, data
        except CalendarError:
            raise
        except Exception as exc:
            raise CalendarError('iCloud ist derzeit nicht erreichbar. Lokale Änderungen bleiben gespeichert.') from exc
        finally:
            conn.close()

    def propfind(self, url, properties, depth='0'):
        status, _, body = self.request('PROPFIND', url, xml_body('propfind', properties),
            {'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'})
        if status != 207:
            raise CalendarError('Die Kalenderliste konnte nicht vollständig von iCloud geladen werden.')
        return multistatus(body)

    def discover(self):
        rows = self.propfind(ROOT, [(DAV, 'current-user-principal')])
        principal = next((element.findtext(f'{{{DAV}}}href') for row in rows
            for key, element in props(row).items() if key == f'{{{DAV}}}current-user-principal'), None)
        if not principal:
            raise CalendarError('Das iCloud-Kalenderkonto konnte nicht ermittelt werden.')
        principal = checked_url(principal)
        rows = self.propfind(principal, [(CAL, 'calendar-home-set')])
        home = next((element.findtext(f'{{{DAV}}}href') for row in rows
            for key, element in props(row).items() if key == f'{{{CAL}}}calendar-home-set'), None)
        if not home:
            raise CalendarError('iCloud hat keine Kalendersammlung geliefert.')
        home = checked_url(home, principal)
        rows = self.propfind(home, [(DAV, 'resourcetype'), (DAV, 'displayname'),
            (DAV, 'current-user-privilege-set'), (CAL, 'supported-calendar-component-set')], '1')
        calendars = []
        for row in rows:
            values = props(row)
            kind = values.get(f'{{{DAV}}}resourcetype')
            if kind is None or kind.find(f'{{{CAL}}}calendar') is None:
                continue
            supported = values.get(f'{{{CAL}}}supported-calendar-component-set')
            if supported is not None and not any(item.get('name') == 'VEVENT' for item in supported):
                continue
            privileges = values.get(f'{{{DAV}}}current-user-privilege-set')
            names = {etree.QName(item).localname for item in privileges.iter()} if privileges is not None else set()
            writable = bool({'all', 'write'} & names or {'write-content', 'bind', 'unbind'} <= names)
            href = row.findtext(f'{{{DAV}}}href')
            if not href:
                raise CalendarError('Ein iCloud-Kalender hat keine gültige Adresse.')
            url = checked_url(href, home, within=home).rstrip('/') + '/'
            title = values.get(f'{{{DAV}}}displayname')
            calendars.append(dict(remote_url=url, name=((title.text if title is not None else '') or 'iCloud-Kalender')[:180], writable=writable))
        if len(calendars) > 100 or len({row['remote_url'] for row in calendars}) != len(calendars):
            raise CalendarError('Die iCloud-Kalenderliste ist nicht eindeutig oder zu umfangreich.')
        return calendars

    def inventory(self, calendar_url):
        rows = self.propfind(calendar_url, [(DAV, 'getetag'), (DAV, 'resourcetype')], '1')
        result = {}
        collection_seen = False
        for row in rows:
            href = row.findtext(f'{{{DAV}}}href')
            if not href:
                raise CalendarError('Die Terminliste ist unvollständig. Abgleich gestoppt.')
            url = checked_url(href, calendar_url)
            if url.rstrip('/') == calendar_url.rstrip('/'):
                collection_kind = props(row).get(f'{{{DAV}}}resourcetype')
                if collection_kind is None or collection_kind.find(f'{{{DAV}}}collection') is None:
                    raise CalendarError('Die Kalendersammlung ist nicht vollständig lesbar.')
                collection_seen = True
                continue
            url = checked_url(url, within=calendar_url)
            values = props(row)
            kind = values.get(f'{{{DAV}}}resourcetype')
            if kind is not None and kind.find(f'{{{DAV}}}collection') is not None:
                continue
            etag = values.get(f'{{{DAV}}}getetag')
            if etag is None or not etag.text or len(etag.text) > 500 or url in result:
                raise CalendarError('Die Terminliste ist unvollständig. Es wurden keine Termine entfernt.')
            result[url] = etag.text
        if not collection_seen:
            raise CalendarError('Die Kalendersammlung fehlt in der Antwort. Es wurden keine Termine entfernt.')
        return result

    def get(self, url):
        status, headers, raw = self.request('GET', url)
        if status == 404:
            return None
        if status != 200 or not headers.get('etag'):
            raise CalendarError('Ein iCloud-Termin konnte nicht sicher gelesen werden.')
        try:
            return raw.decode('utf-8-sig'), headers['etag']
        except UnicodeError as exc:
            raise CalendarError('Ein iCloud-Termin enthält ungültige Zeichen.') from exc

    def put(self, url, raw, etag):
        if etag and not strong_etag(etag):
            raise CalendarError('iCloud liefert keinen sicheren Änderungsstand. Termin nicht überschrieben.')
        headers = {'Content-Type': 'text/calendar; charset=utf-8'}
        headers['If-Match' if etag else 'If-None-Match'] = etag or '*'
        return self.request('PUT', url, raw.encode('utf-8'), headers)[0]

    def delete(self, url, etag):
        if not strong_etag(etag):
            raise CalendarError('Ohne sicheren Änderungsstand wird kein iCloud-Termin gelöscht.')
        return self.request('DELETE', url, headers={'If-Match': etag})[0]
