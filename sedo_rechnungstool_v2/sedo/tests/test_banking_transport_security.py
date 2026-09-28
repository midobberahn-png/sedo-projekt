from io import BytesIO
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase
import requests

from sedo.services.banking_transport import BankSession, BankTransportError, MAX_RESPONSE_BYTES, protect_client


class BankTransportTests(SimpleTestCase):
    endpoint = 'https://bank.example.invalid/fints'

    def response(self, body=b'test', status=200, headers=None):
        result = requests.Response()
        result.status_code = status
        result.headers.update(headers or {})
        result.raw = BytesIO(body)
        return result

    def test_success_is_bounded_tls_verified_without_proxies(self):
        response = self.response(b'bounded-body')
        session = BankSession(self.endpoint)
        with patch('requests.Session.request', return_value=response) as send, patch.object(response, 'close', wraps=response.close) as close:
            result = session.post(self.endpoint, data=b'synthetic-message')
        self.assertEqual(result.content, b'bounded-body')
        self.assertFalse(session.trust_env)
        self.assertFalse(send.call_args.kwargs['allow_redirects'])
        self.assertEqual(send.call_args.kwargs['proxies'], {})
        self.assertTrue(send.call_args.kwargs['verify'])
        self.assertEqual(send.call_args.kwargs['timeout'], (5,20))
        self.assertTrue(send.call_args.kwargs['stream'])
        close.assert_called_once()

    def test_identity_change_cannot_issue_request(self):
        with patch('requests.Session.request') as send, self.assertRaises(BankTransportError):
            BankSession(self.endpoint).post('https://different.invalid/', data=b'synthetic-pin')
        send.assert_not_called()

    def test_redirects_are_rejected_and_not_prepared(self):
        session = BankSession(self.endpoint)
        self.assertEqual(list(session.resolve_redirects(None, None)), [])
        for status in (301,302,303,307,308):
            response = self.response(b'synthetic-private', status, {'Location':'https://different.invalid/'})
            with patch('requests.Session.request', return_value=response), self.assertRaises(BankTransportError) as failure:
                session.post(self.endpoint, data=b'synthetic-pin')
            self.assertNotIn('synthetic-private', str(failure.exception))
            self.assertTrue(response.raw.closed)

    def test_oversize_body_aborts(self):
        response = self.response(b'X'*(MAX_RESPONSE_BYTES+1))
        with patch('requests.Session.request', return_value=response), self.assertRaises(BankTransportError):
            BankSession(self.endpoint).post(self.endpoint)
        self.assertTrue(response.raw.closed)

    def test_compressed_body_is_not_decompressed(self):
        response = self.response(b'invalid', headers={'Content-Encoding':'gzip'})
        with patch('requests.Session.request', return_value=response), self.assertRaises(BankTransportError):
            BankSession(self.endpoint).post(self.endpoint)
        self.assertTrue(response.raw.closed)

    def test_read_budget_is_checked_between_chunks(self):
        response = self.response()
        with patch('requests.Session.request', return_value=response), patch('sedo.services.banking_transport.time.monotonic', side_effect=[0,41]), self.assertRaises(BankTransportError):
            BankSession(self.endpoint).post(self.endpoint)

    def test_real_fints_client_is_protected_before_network(self):
        from fints.client import FinTS3PinTanClient
        with patch('requests.Session.request') as send:
            client = FinTS3PinTanClient('00000000', 'synthetic', 'synthetic-pin', self.endpoint, product_id='synthetic-test')
            protect_client(client, self.endpoint)
            self.assertIsInstance(client.connection.session, BankSession)
            self.assertEqual(client.connection.session.endpoint, self.endpoint)
            send.assert_not_called()

    def test_both_banking_consumers_install_transport(self):
        # The actual transport contract above is tested without a bank account.
        import ast
        from pathlib import Path
        from sedo.services import banking
        tree = ast.parse(Path(banking.__file__).read_text())
        for name in ('fetch_bank_transactions', 'fetch_bank_balance'):
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name==name)
            self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id=='protect_client' for n in ast.walk(node)))
