#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the OIDC authentication strength requirements.

OIDC_IRIS_REQUIRE_ACR, OIDC_IRIS_REQUIRE_AUTH_CONTEXT and OIDC_IRIS_REQUIRE_AMR
let a deployment insist on how the user signed in at the provider - typically
phishing-resistant MFA. The authorization request asks for it, and the callback
refuses an ID token that does not state it was met.
"""

from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs
from urllib.parse import urlparse

from oic.oic.message import AuthorizationRequest
from oic.oic.message import IdToken

from app import app
from app.blueprints.pages.login.login_routes import _oidc_authentication_strength_refusal
from app.blueprints.pages.login.login_routes import _oidc_authentication_strength_request
from app.configuration import _parse_oidc_values

_UNSET = {
    'OIDC_REQUIRE_ACR': [],
    'OIDC_REQUIRE_AUTH_CONTEXT': None,
    'OIDC_REQUIRE_AMR': [],
}


def _token(**claims):
    return IdToken(iss='https://idp.example.test', sub='subject', aud='client', exp=0, iat=0, **claims)


class TestParseValues(TestCase):

    def test_values_are_split_and_trimmed(self):
        self.assertEqual(['hwk', 'fido'], _parse_oidc_values(' hwk, fido '))

    def test_empty_entries_are_dropped(self):
        self.assertEqual(['hwk'], _parse_oidc_values('hwk,,'))

    def test_unset_is_nothing_required(self):
        self.assertEqual([], _parse_oidc_values(None))
        self.assertEqual([], _parse_oidc_values(''))


class _StrengthTestCase(TestCase):

    def _configure(self, **settings):
        patcher = patch.dict(app.config, {**_UNSET, **settings})
        patcher.start()
        self.addCleanup(patcher.stop)


class TestRequest(_StrengthTestCase):

    def _query(self):
        args = {
            'client_id': 'client',
            'response_type': 'code',
            'scope': 'openid',
            'redirect_uri': 'https://iris.example.test/oidc-authorize',
            'state': 'state',
            'nonce': 'nonce',
        }
        args.update(_oidc_authentication_strength_request())
        url = AuthorizationRequest(**args).request('https://idp.example.test/authorize')
        return parse_qs(urlparse(url).query)

    def test_nothing_is_added_when_unset(self):
        self._configure()
        self.assertEqual({}, _oidc_authentication_strength_request())

    def test_acr_values_are_requested_in_order(self):
        self._configure(OIDC_REQUIRE_ACR=['phr', 'phrh'])
        self.assertEqual(['phr phrh'], self._query()['acr_values'])

    def test_auth_context_is_requested_as_an_essential_claim(self):
        """Entra ID only emits `acrs` when the context is asked for this way."""
        self._configure(OIDC_REQUIRE_AUTH_CONTEXT='c1')
        self.assertEqual(['{"id_token": {"acrs": {"essential": true, "value": "c1"}}}'],
                         self._query()['claims'])

    def test_amr_alone_requests_nothing(self):
        """There is no standard way to ask for an amr, it can only be checked."""
        self._configure(OIDC_REQUIRE_AMR=['hwk'])
        self.assertEqual({}, _oidc_authentication_strength_request())


class TestRefusal(_StrengthTestCase):

    def test_unset_accepts_any_sign_in(self):
        self._configure()
        self.assertIsNone(_oidc_authentication_strength_refusal(_token(), True))
        self.assertIsNone(_oidc_authentication_strength_refusal({'sub': 'subject'}, False))

    def test_userinfo_is_refused_once_a_strength_is_required(self):
        self._configure(OIDC_REQUIRE_AUTH_CONTEXT='c1')
        refusal = _oidc_authentication_strength_refusal({'sub': 'subject', 'acrs': ['c1']}, False)
        self.assertIn('no ID token', refusal)

    def test_acr_must_be_one_of_the_required(self):
        self._configure(OIDC_REQUIRE_ACR=['phr', 'phrh'])
        self.assertIsNone(_oidc_authentication_strength_refusal(_token(acr='phrh'), True))
        self.assertIn("acr 'mfa'", _oidc_authentication_strength_refusal(_token(acr='mfa'), True))
        self.assertIn("acr 'None'", _oidc_authentication_strength_refusal(_token(), True))

    def test_auth_context_must_be_in_acrs(self):
        self._configure(OIDC_REQUIRE_AUTH_CONTEXT='c1')
        self.assertIsNone(_oidc_authentication_strength_refusal(_token(acrs=['c2', 'c1']), True))
        self.assertIn("'c1' was not satisfied",
                      _oidc_authentication_strength_refusal(_token(acrs=['c2']), True))

    def test_missing_acrs_is_refused(self):
        """What a disabled or report-only Conditional Access policy looks like."""
        self._configure(OIDC_REQUIRE_AUTH_CONTEXT='c1')
        self.assertIn("'c1' was not satisfied", _oidc_authentication_strength_refusal(_token(), True))

    def test_acrs_sent_as_a_single_string(self):
        self._configure(OIDC_REQUIRE_AUTH_CONTEXT='c1')
        self.assertIsNone(_oidc_authentication_strength_refusal({'acrs': 'c1'}, True))

    def test_any_accepted_amr_is_enough(self):
        self._configure(OIDC_REQUIRE_AMR=['hwk', 'fido'])
        self.assertIsNone(_oidc_authentication_strength_refusal(_token(amr=['pwd', 'fido']), True))
        self.assertIn('contains none of',
                      _oidc_authentication_strength_refusal(_token(amr=['pwd', 'otp']), True))
        self.assertIn('contains none of', _oidc_authentication_strength_refusal(_token(), True))

    def test_every_configured_requirement_applies(self):
        self._configure(OIDC_REQUIRE_ACR=['phr'], OIDC_REQUIRE_AMR=['hwk'])
        self.assertIsNone(_oidc_authentication_strength_refusal(_token(acr='phr', amr=['hwk']), True))
        self.assertIsNotNone(_oidc_authentication_strength_refusal(_token(acr='phr', amr=['pwd']), True))
        self.assertIsNotNone(_oidc_authentication_strength_refusal(_token(acr='mfa', amr=['hwk']), True))
