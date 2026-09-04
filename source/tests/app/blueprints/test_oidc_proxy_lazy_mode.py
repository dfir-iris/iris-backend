#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for lazy-mode oidc_proxy authentication (VI-005).

Lazy mode takes the caller's identity from the `X-Email` header without
validating any token, so it is only sound behind a proxy that authenticates
the request and overwrites that header. These tests pin the trust decision:
which peer is allowed to assert an identity, and what happens to everyone
else.

`_authenticate_with_email` is stubbed so nothing reaches the database — the
assertion is about whether it is reached at all.
"""

from unittest import TestCase
from unittest.mock import patch

from flask import request

from app import app
from app.blueprints.access_controls import _oidc_proxy_authentication_process
from app.blueprints.access_controls import _request_is_from_trusted_proxy
from app.blueprints.access_controls import _trusted_proxy_networks_cache


class TestTrustedProxyMatching(TestCase):

    def setUp(self):
        _trusted_proxy_networks_cache.clear()
        self.addCleanup(_trusted_proxy_networks_cache.clear)

    def _decide(self, trusted, peer):
        with patch.dict(app.config, {'AUTHENTICATION_PROXY_TRUSTED_IPS': trusted}):
            with app.test_request_context('/', environ_base={'REMOTE_ADDR': peer}):
                return _request_is_from_trusted_proxy(request)

    def test_unconfigured_list_trusts_nobody(self):
        self.assertFalse(self._decide('', '127.0.0.1'))
        self.assertFalse(self._decide(None, '10.0.0.1'))

    def test_exact_address_is_trusted(self):
        self.assertTrue(self._decide('10.0.0.5', '10.0.0.5'))

    def test_other_address_is_not_trusted(self):
        self.assertFalse(self._decide('10.0.0.5', '10.0.0.6'))

    def test_cidr_block_is_honoured(self):
        self.assertTrue(self._decide('10.0.0.0/24', '10.0.0.77'))
        self.assertFalse(self._decide('10.0.0.0/24', '10.0.1.77'))

    def test_list_entries_are_trimmed(self):
        self.assertTrue(self._decide(' 192.0.2.1 , 10.0.0.0/24 ', '10.0.0.9'))

    def test_unparseable_entry_does_not_void_the_valid_ones(self):
        self.assertTrue(self._decide('not-an-ip,10.0.0.0/24', '10.0.0.9'))

    def test_unparseable_entry_alone_trusts_nobody(self):
        self.assertFalse(self._decide('not-an-ip', '10.0.0.9'))

    def test_ipv6_proxy_is_matched(self):
        self.assertTrue(self._decide('fd00::/8', 'fd00::1'))


class TestLazyModeAuthentication(TestCase):

    def setUp(self):
        _trusted_proxy_networks_cache.clear()
        self.addCleanup(_trusted_proxy_networks_cache.clear)

        patcher = patch('app.blueprints.access_controls._authenticate_with_email',
                        side_effect=lambda email: self.authenticated.append(email) or True)
        self.authenticated = []
        patcher.start()
        self.addCleanup(patcher.stop)

    def _authenticate(self, trusted, peer, email='victim@example.test'):
        config = {
            'AUTHENTICATION_TOKEN_VERIFY_MODE': 'lazy',
            'AUTHENTICATION_PROXY_TRUSTED_IPS': trusted,
        }
        with patch.dict(app.config, config):
            with app.test_request_context('/', headers={'X-Email': email},
                                          environ_base={'REMOTE_ADDR': peer}):
                return _oidc_proxy_authentication_process(request)

    def test_spoofed_header_from_an_untrusted_peer_is_refused(self):
        """The whole finding in one assertion: anyone who can reach the
        backend directly must not be able to name a user and become them."""
        self.assertFalse(self._authenticate('10.0.0.5', '203.0.113.9'))
        self.assertEqual([], self.authenticated)

    def test_header_is_refused_when_no_proxy_is_declared(self):
        self.assertFalse(self._authenticate('', '10.0.0.5'))
        self.assertEqual([], self.authenticated)

    def test_header_from_the_declared_proxy_is_accepted(self):
        self.assertTrue(self._authenticate('10.0.0.5', '10.0.0.5'))
        self.assertEqual(['victim@example.test'], self.authenticated)

    def test_first_value_of_a_multi_valued_header_is_used(self):
        # Preserved from the original parsing; the surrounding trust check
        # is what makes it safe to keep.
        self.assertTrue(self._authenticate('10.0.0.5', '10.0.0.5',
                                           email='first@example.test, second@example.test'))
        self.assertEqual(['first@example.test'], self.authenticated)
