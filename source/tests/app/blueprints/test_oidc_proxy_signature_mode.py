#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for signature-mode oidc_proxy authentication (VI-006).

A valid signature only proves the token came from someone the JWKS endpoint
vouches for. Without pinning the issuer, a token minted by another tenant of
a shared IdP authenticates as whichever local account its `sub` names.

The signing key is a real RSA keypair generated once for the module — PyJWT
needs a genuine key to produce a verifiable RS256 token, and the point of
these tests is that a *validly signed* token is still refused when its
claims don't match the configured issuer and audience. `PyJWKClient` is
stubbed so nothing reaches the network; `_authenticate_with_email` is
stubbed so nothing reaches the database.
"""

from unittest import TestCase
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import request

from app import app
from app.blueprints.access_controls import _oidc_proxy_authentication_process


_ISSUER = 'https://idp.example.test/realms/iris'
_AUDIENCE = 'iris-client'

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


class _StubSigningKey:

    def __init__(self, key):
        self.key = key


class _StubJwksClient:
    """Stands in for PyJWKClient: always returns our public key.

    That models the worst realistic case — a JWKS endpoint that happily
    vouches for the token's signature. Everything after that has to be
    caught by claim validation.
    """

    def __init__(self, url):
        self.url = url

    def get_signing_key_from_jwt(self, token):
        return _StubSigningKey(_PRIVATE_KEY.public_key())


def _token(**claims):
    payload = {'iss': _ISSUER, 'aud': _AUDIENCE, 'sub': 'victim@example.test'}
    payload.update(claims)
    return jwt.encode(payload, _PRIVATE_PEM, algorithm='RS256')


class TestSignatureModeAuthentication(TestCase):

    def setUp(self):
        patcher = patch('app.blueprints.access_controls.PyJWKClient', _StubJwksClient)
        patcher.start()
        self.addCleanup(patcher.stop)

        authenticate = patch('app.blueprints.access_controls._authenticate_with_email',
                             side_effect=lambda email: self.authenticated.append(email) or True)
        self.authenticated = []
        authenticate.start()
        self.addCleanup(authenticate.stop)

    def _authenticate(self, token, **overrides):
        config = {
            'AUTHENTICATION_TOKEN_VERIFY_MODE': 'signature',
            'AUTHENTICATION_JWKS_URL': 'https://idp.example.test/jwks',
            'AUTHENTICATION_ISSUER': _ISSUER,
            'AUTHENTICATION_AUDIENCE': _AUDIENCE,
            'AUTHENTICATION_VERIFY_TOKEN_EXP': False,
        }
        config.update(overrides)
        with patch.dict(app.config, config):
            with app.test_request_context('/', headers={'X-Forwarded-Access-Token': token}):
                return _oidc_proxy_authentication_process(request)

    def test_token_from_the_configured_issuer_is_accepted(self):
        self.assertTrue(self._authenticate(_token()))
        self.assertEqual(['victim@example.test'], self.authenticated)

    def test_token_from_another_issuer_is_refused(self):
        """The finding itself: the signature is valid and the audience
        matches — only the issuer differs. Accepting it is the bug."""
        token = _token(iss='https://unintended-issuer.example.test/')
        self.assertFalse(self._authenticate(token))
        self.assertEqual([], self.authenticated)

    def test_token_without_an_issuer_claim_is_refused(self):
        token = jwt.encode({'aud': _AUDIENCE, 'sub': 'victim@example.test'},
                           _PRIVATE_PEM, algorithm='RS256')
        self.assertFalse(self._authenticate(token))
        self.assertEqual([], self.authenticated)

    def test_token_for_another_audience_is_refused(self):
        self.assertFalse(self._authenticate(_token(aud='some-other-client')))
        self.assertEqual([], self.authenticated)

    def test_token_without_a_subject_is_refused(self):
        token = jwt.encode({'iss': _ISSUER, 'aud': _AUDIENCE},
                           _PRIVATE_PEM, algorithm='RS256')
        self.assertFalse(self._authenticate(token))
        self.assertEqual([], self.authenticated)

    def test_unconfigured_issuer_refuses_rather_than_skipping_the_check(self):
        self.assertFalse(self._authenticate(_token(), AUTHENTICATION_ISSUER=''))
        self.assertEqual([], self.authenticated)

    def test_unconfigured_audience_refuses_rather_than_skipping_the_check(self):
        self.assertFalse(self._authenticate(_token(), AUTHENTICATION_AUDIENCE=''))
        self.assertEqual([], self.authenticated)
