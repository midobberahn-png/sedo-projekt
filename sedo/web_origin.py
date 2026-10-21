"""Validate the one HTTPS origin used by the company web installation."""
import ipaddress
import re
from urllib.parse import urlsplit


def validate_origin(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) <= 32 or ord(char) > 126 for char in value):
        raise ValueError('Die Online-Adresse muss eine genaue HTTPS-Domain sein.')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        labels = host.split('.')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            address_is_ip = False
        else:
            address_is_ip = True
        if (parsed.scheme != 'https' or parsed.netloc != host
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or address_is_ip or len(host) > 253 or len(labels) < 2
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                       for label in labels)):
            raise ValueError()
    except ValueError:
        raise ValueError('Die Online-Adresse muss eine genaue HTTPS-Domain ohne Pfad sein.') from None
    return f'https://{host}'
