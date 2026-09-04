#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for direct-OIDC account binding (VI-011).

The `/oidc-authorize` callback used to pick the local account straight out of
`preferred_username` / `email`. Both are frequently editable by the person
holding the token, so an attacker who could rename themselves at the identity
provider was handed the colliding IRIS account. The account is now keyed on the
issuer's `sub`, recorded in `User.external_id` behind an `oidc:` prefix, and the
name claims only get a say the first time a subject is seen.

The DB-touching helpers are stubbed: what is under test is which account the
callback decides on, and when it decides on nobody at all.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.blueprints.pages.login.login_routes import _oidc_email_claim_is_trusted
from app.blueprints.pages.login.login_routes import _oidc_external_id_from_claims
from app.blueprints.pages.login.login_routes import _oidc_resolve_user

_USERNAME_FIELD = 'preferred_username'
_EMAIL_FIELD = 'email'


def _user(user_id, login, external_id=None):
    return SimpleNamespace(id=user_id, user=login, external_id=external_id)


class TestExternalIdFromClaims(TestCase):

    def test_subject_is_namespaced_under_the_oidc_prefix(self):
        """The prefix keeps provider subjects out of the namespace case
        transfers use for their placeholder identities."""
        external_id, refusal = _oidc_external_id_from_claims(
            {'sub': 'stable-subject', 'preferred_username': 'alice'})
        self.assertIsNone(refusal)
        self.assertEqual('oidc:stable-subject', external_id)

    def test_missing_sub_is_refused(self):
        external_id, refusal = _oidc_external_id_from_claims(
            {'preferred_username': 'administrator', 'email': 'attacker@example.test'})
        self.assertIsNone(external_id)
        self.assertIn("no 'sub' claim", refusal)

    def test_empty_sub_is_refused(self):
        external_id, refusal = _oidc_external_id_from_claims(
            {'sub': '', 'preferred_username': 'alice'})
        self.assertIsNone(external_id)
        self.assertIn("no 'sub' claim", refusal)


class TestEmailClaimTrust(TestCase):

    def _trusted(self, claims, require_verified_email=False):
        with patch.dict(app.config,
                        {'OIDC_REQUIRE_VERIFIED_EMAIL': require_verified_email}):
            return _oidc_email_claim_is_trusted(claims)

    def test_boolean_claim_is_honoured(self):
        self.assertTrue(self._trusted({'email_verified': True}))
        self.assertFalse(self._trusted({'email_verified': False}))

    def test_string_claim_is_decoded(self):
        self.assertTrue(self._trusted({'email_verified': 'true'}))
        self.assertTrue(self._trusted({'email_verified': ' True '}))
        self.assertFalse(self._trusted({'email_verified': 'false'}))
        self.assertFalse(self._trusted({'email_verified': ''}))

    def test_explicit_denial_survives_the_flag_being_off(self):
        self.assertFalse(self._trusted({'email_verified': False},
                                       require_verified_email=False))

    def test_silence_follows_the_flag(self):
        self.assertTrue(self._trusted({}, require_verified_email=False))
        self.assertFalse(self._trusted({}, require_verified_email=True))


class _AccountsTestCase(TestCase):
    """Stands a fake `user` table up in front of the three DB helpers.

    `_bind` mirrors the guarded UPDATE in `bind_user_external_id` — it claims a
    row only while that row is still unbound — so the tests can assert on the
    `external_id` an account is left carrying rather than on call counts alone.
    """

    def setUp(self):
        self.rows = {}
        self.by_external_id = {}
        self.by_login = {}
        self.bound = []
        self.concurrent_binder = None

        for name, table in (('get_user_by_external_id', self.by_external_id),
                            ('get_user', self.by_login)):
            patcher = patch(f'app.blueprints.pages.login.login_routes.{name}',
                            side_effect=self._lookup(table))
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

        patcher = patch('app.blueprints.pages.login.login_routes.bind_user_external_id',
                        side_effect=self._bind)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _account(self, user_id, login, external_id=None):
        user = _user(user_id, login, external_id)
        self.rows[user_id] = user
        self.by_login[login] = user
        if external_id:
            self.by_external_id[external_id] = user
        return user

    @staticmethod
    def _lookup(table):
        def _get(key, *args, **kwargs):
            return table.get(key)
        return _get

    def _bind(self, user_id, external_id):
        self.bound.append((user_id, external_id))
        if self.concurrent_binder is not None:
            self.rows[user_id].external_id = self.concurrent_binder
        user = self.rows[user_id]
        if user.external_id is not None:
            return False
        user.external_id = external_id
        return True


class TestResolveUser(_AccountsTestCase):

    def test_subject_binding_wins_over_the_username_claim(self):
        """The whole finding in one assertion: once a subject owns an account,
        renaming yourself at the provider moves nothing."""
        alice = self._account(1, 'alice', 'oidc:alice-subject')
        victim = self._account(2, 'victim')

        user, refusal = _oidc_resolve_user('oidc:alice-subject', 'victim', True)

        self.assertIsNone(refusal)
        self.assertIs(alice, user)
        self.assertIsNone(victim.external_id)
        self.assertEqual([], self.bound)
        self.get_user.assert_not_called()

    def test_a_bound_subject_is_honoured_even_with_unverified_claims(self):
        """The binding is the proof of who this is; no claim is picking the
        account, so nothing about the profile needs vouching for."""
        alice = self._account(1, 'alice', 'oidc:alice-subject')

        user, refusal = _oidc_resolve_user('oidc:alice-subject', 'alice', False)

        self.assertIsNone(refusal)
        self.assertIs(alice, user)

    def test_username_collision_with_another_subject_is_refused(self):
        victim = self._account(2, 'victim', 'oidc:victim-subject')

        user, refusal = _oidc_resolve_user('oidc:attacker-stable-subject', 'victim', True)

        self.assertIsNone(user)
        self.assertIn('already bound', refusal)
        self.assertEqual('oidc:victim-subject', victim.external_id)
        self.assertEqual([], self.bound)

    def test_account_holding_a_case_transfer_identity_is_not_adopted(self):
        # The two namespaces share the column, so an identity from the other
        # one still counts as bound and still blocks adoption.
        imported = self._account(3, 'imported', 'iris-import:some-uuid')

        user, refusal = _oidc_resolve_user('oidc:stable-subject', 'imported', True)

        self.assertIsNone(user)
        self.assertIn('already bound', refusal)
        self.assertEqual('iris-import:some-uuid', imported.external_id)
        self.assertEqual([], self.bound)

    def test_unbound_account_is_adopted_on_first_use(self):
        alice = self._account(1, 'alice')

        user, refusal = _oidc_resolve_user('oidc:alice-subject', 'alice', True)

        self.assertIsNone(refusal)
        self.assertIs(alice, user)
        self.assertEqual('oidc:alice-subject', alice.external_id)
        self.assertEqual([(1, 'oidc:alice-subject')], self.bound)

    def test_adoption_of_an_existing_account_needs_verified_claims(self):
        alice = self._account(1, 'alice')

        user, refusal = _oidc_resolve_user('oidc:alice-subject', 'alice', False)

        self.assertIsNone(user)
        self.assertIn('cannot be taken over', refusal)
        self.assertIsNone(alice.external_id)
        self.assertEqual([], self.bound)

    def test_adoption_lost_to_a_concurrent_login_is_refused(self):
        # The binding UPDATE only touches rows that are still unbound, so a
        # False return means another subject claimed the account first.
        alice = self._account(1, 'alice')
        self.concurrent_binder = 'oidc:someone-else'

        user, refusal = _oidc_resolve_user('oidc:alice-subject', 'alice', True)

        self.assertIsNone(user)
        self.assertIn('bound to another', refusal)
        self.assertEqual('oidc:someone-else', alice.external_id)

    def test_unknown_subject_and_unknown_login_leaves_creation_to_the_caller(self):
        user, refusal = _oidc_resolve_user('oidc:new-subject', 'newcomer', True)

        self.assertIsNone(user)
        self.assertIsNone(refusal)
        self.assertEqual([], self.bound)

    def test_creation_is_not_gated_on_verified_claims(self):
        """Nothing is being taken over when no account matches, so there is no
        victim — and refusing here would break first logins on providers that
        never send `email_verified`."""
        user, refusal = _oidc_resolve_user('oidc:new-subject', 'newcomer', False)

        self.assertIsNone(user)
        self.assertIsNone(refusal)


class TestReportedProofOfConcept(_AccountsTestCase):
    """The claim set from the VI-011 report, run through the callback's own
    sequence: derive the identity, then resolve the account."""

    _POC_CLAIMS = {
        'sub': 'attacker-stable-subject',
        'preferred_username': 'victim',
        'email': 'attacker-controlled@example.test',
        'email_verified': False,
    }

    def _authorise(self, claims, require_verified_email=False):
        user_login = claims.get(_USERNAME_FIELD) or claims.get(_EMAIL_FIELD)

        external_id, refusal = _oidc_external_id_from_claims(claims)
        if refusal:
            return None, refusal

        with patch.dict(app.config,
                        {'OIDC_REQUIRE_VERIFIED_EMAIL': require_verified_email}):
            return _oidc_resolve_user(external_id, user_login,
                                      _oidc_email_claim_is_trusted(claims))

    def test_reported_claims_no_longer_resolve_to_the_victim(self):
        victim = self._account(2, 'victim')

        user, refusal = self._authorise(dict(self._POC_CLAIMS))

        self.assertIsNone(user)
        self.assertIsNotNone(refusal)
        # A refusal that still bound the account would hand the attacker the
        # next login instead of this one.
        self.assertIsNone(victim.external_id)
        self.assertEqual([], self.bound)

    def test_the_same_claims_without_email_verified_still_adopt(self):
        """The upgrade no-regression case: providers that never send the claim
        must keep working while OIDC_IRIS_REQUIRE_VERIFIED_EMAIL is off."""
        victim = self._account(2, 'victim')
        claims = dict(self._POC_CLAIMS)
        del claims['email_verified']

        user, refusal = self._authorise(claims)

        self.assertIsNone(refusal)
        self.assertIs(victim, user)
        self.assertEqual('oidc:attacker-stable-subject', victim.external_id)

    def test_the_same_claims_without_email_verified_are_refused_with_the_flag_on(self):
        victim = self._account(2, 'victim')
        claims = dict(self._POC_CLAIMS)
        del claims['email_verified']

        user, refusal = self._authorise(claims, require_verified_email=True)

        self.assertIsNone(user)
        self.assertIn('cannot be taken over', refusal)
        self.assertIsNone(victim.external_id)

    def test_the_same_claims_with_a_verified_email_adopt(self):
        """A provider that vouches for the profile it sent is taken at its
        word — that is the legitimate migration of a local account to OIDC."""
        victim = self._account(2, 'victim')
        claims = dict(self._POC_CLAIMS)
        claims['email_verified'] = True

        user, refusal = self._authorise(claims)

        self.assertIsNone(refusal)
        self.assertIs(victim, user)
        self.assertEqual('oidc:attacker-stable-subject', victim.external_id)

    def test_reported_claims_without_a_sub_are_refused_before_any_lookup(self):
        victim = self._account(2, 'victim')
        claims = dict(self._POC_CLAIMS)
        del claims['sub']

        user, refusal = self._authorise(claims)

        self.assertIsNone(user)
        self.assertIn("no 'sub' claim", refusal)
        self.assertIsNone(victim.external_id)
        self.get_user.assert_not_called()
        self.get_user_by_external_id.assert_not_called()
