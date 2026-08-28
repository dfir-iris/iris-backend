#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the HMAC sign/verify helpers in app.util.

These functions wrap cryptography.hazmat HMAC-SHA256. The sign→verify
round-trip is the most important property; additionally: tampered data
must fail verification, a different key must fail, and sign is
deterministic for the same input + key combination.
"""

from unittest import TestCase

from flask import Flask

from app.util import hmac_sign, hmac_verify

_SECRET = 'test-secret-key-for-unit-tests'
_app = Flask(__name__)
_app.config['SECRET_KEY'] = _SECRET


class TestHmacSignVerify(TestCase):

    def test_round_trip_succeeds(self):
        data = b'iris test payload'
        with _app.app_context():
            sig = hmac_sign(data)
            self.assertTrue(hmac_verify(sig, data))

    def test_tampered_data_fails_verification(self):
        data = b'original payload'
        with _app.app_context():
            sig = hmac_sign(data)
            self.assertFalse(hmac_verify(sig, b'tampered payload'))

    def test_empty_bytes_round_trip(self):
        with _app.app_context():
            sig = hmac_sign(b'')
            self.assertTrue(hmac_verify(sig, b''))

    def test_empty_bytes_signature_does_not_match_nonempty(self):
        with _app.app_context():
            sig = hmac_sign(b'')
            self.assertFalse(hmac_verify(sig, b'not empty'))

    def test_sign_is_deterministic(self):
        data = b'deterministic input'
        with _app.app_context():
            sig1 = hmac_sign(data)
            sig2 = hmac_sign(data)
        self.assertEqual(sig1, sig2)

    def test_sign_returns_bytes(self):
        with _app.app_context():
            sig = hmac_sign(b'data')
        self.assertIsInstance(sig, bytes)

    def test_different_payloads_produce_different_signatures(self):
        with _app.app_context():
            sig_a = hmac_sign(b'payload A')
            sig_b = hmac_sign(b'payload B')
        self.assertNotEqual(sig_a, sig_b)

    def test_wrong_key_fails_verification(self):
        data = b'sensitive data'
        # Sign with the normal key
        with _app.app_context():
            sig = hmac_sign(data)

        # Verify with a different key configured
        other_app = Flask(__name__)
        other_app.config['SECRET_KEY'] = 'completely-different-key'
        with other_app.app_context():
            self.assertFalse(hmac_verify(sig, data))

    def test_truncated_signature_fails(self):
        data = b'complete data'
        with _app.app_context():
            sig = hmac_sign(data)
            truncated = sig[:-4]
            self.assertFalse(hmac_verify(truncated, data))

    def test_binary_payload_round_trip(self):
        data = bytes(range(256))
        with _app.app_context():
            sig = hmac_sign(data)
            self.assertTrue(hmac_verify(sig, data))

    def test_large_payload_round_trip(self):
        data = b'x' * 100_000
        with _app.app_context():
            sig = hmac_sign(data)
            self.assertTrue(hmac_verify(sig, data))
