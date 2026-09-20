"""Official wetter.com / METEONOMIQS PWA v4.0 adapter; no credentials in the browser.

Provider contract: https://doc.meteonomiqs.com/doc/forecast_v4_0.html
Dashboard rendering reads the cache only. The visible weather widget refreshes
through an authenticated, CSRF-protected endpoint. All users share a 10-minute
cache per provider credential and postcode to limit provider requests.
"""
import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import timedelta

import requests
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import AppSetting

API_BASE = 'https://forecast.meteonomiqs.com/v4_0/nowcast/weather/byLocation'
API_DOCS = 'https://doc.meteonomiqs.com/doc/forecast_v4_0.html'


def weather_key():
    return os.environ.get('SEDO_WEATHER_API_KEY', '') or AppSetting.get('weather_api_key', '')


def weather_for(layout, refresh=False):
    key = weather_key()
    country, postcode = layout['weather_country'], layout['weather_postcode']
    if not key:
        return {'state': 'setup', 'message': 'Wetter.com-Zugang noch nicht eingerichtet.'}
    if country not in ('DE', 'AT', 'CH', 'LI') or not re.fullmatch(r'[0-9]{5}' if country == 'DE' else r'[0-9]{4}', postcode):
        return {'state': 'error', 'message': 'Bitte den Wetterort in der Kachelauswahl prüfen.'}
    digest = hashlib.sha256((key + '\0' + country + '\0' + postcode).encode()).hexdigest()
    cache_key = 'dashboard-weather-' + digest
    saved = cache.get(cache_key)
    if saved is not None:
        return saved
    if not refresh:
        return {'state': 'loading', 'message': 'Wetter wird geladen …'}
    token = uuid.uuid4().hex
    if not cache.add(cache_key + '-lock', token, timeout=30):
        return {'state': 'loading', 'message': 'Wetter wird gerade aktualisiert …'}
    try:
        deadline = time.monotonic() + 15
        with requests.get(f'{API_BASE}/{country}/{postcode}',
                headers={'x-api-key': key, 'Accept-Language': 'de-DE', 'Accept': 'application/json'},
                timeout=(3, 5), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError('provider status')
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 1_000_000 or time.monotonic() > deadline:
                    raise ValueError('response too large')
        payload = json.loads(data)
        readings = payload['realtimeWeather']['items']
        now = timezone.now()
        valid = []
        for item in readings:
            stamp = parse_datetime(item['from'])
            if stamp and timezone.is_aware(stamp) and now - timedelta(hours=2) <= stamp <= now + timedelta(minutes=10):
                valid.append((stamp, item))
        if not valid:
            raise ValueError('no current data')
        stamp, item = max(valid, key=lambda pair: pair[0])
        temp = item['temperature']['value']
        description = item['weather']['text']
        if isinstance(temp, bool) or not isinstance(temp, (int, float)) or not math.isfinite(temp) or not -100 <= temp <= 70:
            raise ValueError('invalid temperature')
        if item['temperature']['unit'] != '°C' or not isinstance(description, str) or not description.strip():
            raise ValueError('invalid weather description')
        result = {'state': 'ready', 'temperature': f'{temp:g}'.replace('.', ','),
                  'description': description[:160],
                  'observed_at': timezone.localtime(stamp).strftime('%d.%m.%Y %H:%M'),
                  'message': ''}
        cache.set(cache_key, result, timeout=600)
        return result
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        result = {'state': 'error', 'message': 'Wetter.com ist derzeit nicht erreichbar oder liefert keine aktuellen Daten. Bitte Zugang und gebuchtes API-Paket prüfen.'}
        cache.set(cache_key, result, timeout=60)
        return result
    finally:
        if cache.get(cache_key + '-lock') == token:
            cache.delete(cache_key + '-lock')
