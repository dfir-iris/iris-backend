#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the outbound destination guard (VI-013).

`allow_private_egress` reads app.config, so it is patched at module level
rather than pushing an app context. DNS is stubbed everywhere a hostname
is involved so the suite stays offline and deterministic.
"""

from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.utils.egress import egress_destination_error


def _resolving_to(*addresses):
    """Stand in for socket.getaddrinfo, returning the given addresses."""
    return lambda *args, **kwargs: [(None, None, None, None, (address, 0)) for address in addresses]


class TestEgressDestinationError(TestCase):

    def setUp(self):
        patcher = patch('app.iris_engine.utils.egress.allow_private_egress', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- scheme ----------

    def test_file_scheme_is_refused(self):
        self.assertIsNotNone(egress_destination_error('file:///etc/passwd'))

    def test_gopher_scheme_is_refused(self):
        self.assertIsNotNone(egress_destination_error('gopher://example.com/'))

    def test_empty_url_is_refused(self):
        self.assertIsNotNone(egress_destination_error(''))
        self.assertIsNotNone(egress_destination_error(None))

    def test_url_without_host_is_refused(self):
        self.assertIsNotNone(egress_destination_error('http:///no-host'))

    # ---------- literal addresses ----------

    def test_cloud_metadata_address_is_refused(self):
        self.assertIsNotNone(
            egress_destination_error('http://169.254.169.254/latest/meta-data/')
        )

    def test_loopback_address_is_refused(self):
        self.assertIsNotNone(egress_destination_error('http://127.0.0.1:8080/probe'))

    def test_private_address_is_refused(self):
        self.assertIsNotNone(egress_destination_error('https://10.1.2.3/internal'))

    def test_ipv6_loopback_is_refused(self):
        self.assertIsNotNone(egress_destination_error('http://[::1]/probe'))

    def test_public_literal_address_is_allowed(self):
        self.assertIsNone(egress_destination_error('https://93.184.216.34/logo.png'))

    # ---------- resolved hostnames ----------

    def test_hostname_resolving_public_is_allowed(self):
        with patch('socket.getaddrinfo', _resolving_to('93.184.216.34')):
            self.assertIsNone(egress_destination_error('https://example.com/logo.png'))

    def test_hostname_resolving_private_is_refused(self):
        with patch('socket.getaddrinfo', _resolving_to('192.168.1.10')):
            self.assertIsNotNone(egress_destination_error('https://rebind.example.com/x.png'))

    def test_hostname_with_any_private_answer_is_refused(self):
        # A public answer first must not mask a private one behind it.
        with patch('socket.getaddrinfo', _resolving_to('93.184.216.34', '127.0.0.1')):
            self.assertIsNotNone(egress_destination_error('https://mixed.example.com/x.png'))

    def test_unresolvable_hostname_is_refused(self):
        import socket as socket_module

        def _fail(*args, **kwargs):
            raise socket_module.gaierror('no such host')

        with patch('socket.getaddrinfo', _fail):
            self.assertIsNotNone(egress_destination_error('https://nowhere.invalid/x.png'))

    # ---------- opt-out ----------

    def test_private_address_allowed_when_operator_opts_in(self):
        with patch('app.iris_engine.utils.egress.allow_private_egress', return_value=True):
            self.assertIsNone(egress_destination_error('http://10.1.2.3/internal.png'))

    def test_opt_in_does_not_relax_the_scheme_allowlist(self):
        with patch('app.iris_engine.utils.egress.allow_private_egress', return_value=True):
            self.assertIsNotNone(egress_destination_error('file:///etc/passwd'))
