"""Per-client FinTS HTTP policy. No inherited proxy, redirects or unbounded body.

FinTS puts credentials into its message body, not an Authorization header.
A redirect must therefore be rejected even when Requests strips HTTP auth.
"""
import time

import certifi
import requests

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class BankTransportError(ValueError):
    """Contains no response body or credentials."""


class BankSession(requests.Session):
    def __init__(self, endpoint):
        super().__init__()
        self.endpoint = endpoint
        self.trust_env = False

    def resolve_redirects(self, *args, **kwargs):
        # Even allow_redirects=False normally prepares Response.next and may
        # consume the redirect body. This client never needs either operation.
        return iter(())

    def request(self, method, url, **kwargs):
        if method.upper() != 'POST' or url != self.endpoint:
            raise BankTransportError('Unerwartetes Bankziel; Abruf gestoppt.')
        kwargs.update(allow_redirects=False, timeout=(5, 20), verify=certifi.where(),
                      proxies={}, stream=True)
        kwargs['headers'] = {**(kwargs.get('headers') or {}), 'Accept-Encoding': 'identity'}
        started = time.monotonic()
        response = super().request(method, url, **kwargs)
        try:
            if not 200 <= response.status_code < 300:
                raise BankTransportError('Die Bank hat keine erfolgreiche Antwort geliefert; Weiterleitungen sind gesperrt.')
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise BankTransportError('Unerwartete Kodierung der Bankantwort.')
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES or time.monotonic() - started > 40:
                    raise BankTransportError('Die Bankantwort überschreitet die Sicherheitsgrenzen.')
                chunks.append(chunk)
            response._content = b''.join(chunks)
            response._content_consumed = True
            return response
        finally:
            response.close()


def protect_client(client, endpoint):
    """Install before entering the FinTS dialog, without a global monkeypatch."""
    previous = client.connection.session
    client.connection.session = BankSession(endpoint)
    previous.close()
