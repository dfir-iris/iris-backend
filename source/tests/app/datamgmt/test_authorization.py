#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from types import SimpleNamespace
from unittest import TestCase

from app.models.authorization import CaseAccessLevel
from app.datamgmt.authorization import RESTRICTED_USER_FIELDS
from app.datamgmt.authorization import has_deny_all_access_level


class TestRestrictedUserFields(TestCase):

    def test_password_in_restricted_fields(self):
        self.assertIn('password', RESTRICTED_USER_FIELDS)

    def test_mfa_secrets_in_restricted_fields(self):
        self.assertIn('mfa_secrets', RESTRICTED_USER_FIELDS)

    def test_webauthn_credentials_in_restricted_fields(self):
        self.assertIn('webauthn_credentials', RESTRICTED_USER_FIELDS)

    def test_api_key_in_restricted_fields(self):
        self.assertIn('api_key', RESTRICTED_USER_FIELDS)

    def test_external_id_in_restricted_fields(self):
        self.assertIn('external_id', RESTRICTED_USER_FIELDS)

    def test_restricted_fields_is_a_set(self):
        self.assertIsInstance(RESTRICTED_USER_FIELDS, set)


class TestHasDenyAllAccessLevel(TestCase):

    def test_deny_all_access_level_returns_true(self):
        row = SimpleNamespace(access_level=CaseAccessLevel.deny_all.value)
        self.assertTrue(has_deny_all_access_level(row))

    def test_non_deny_all_access_level_returns_false(self):
        # Use a value that does not have bit 0x1 set
        row = SimpleNamespace(access_level=0x0)
        self.assertFalse(has_deny_all_access_level(row))

    def test_deny_all_bit_combined_with_other_bits_returns_true(self):
        # deny_all is 0x1; if that bit is set alongside others, still true
        row = SimpleNamespace(access_level=CaseAccessLevel.deny_all.value | 0x4)
        self.assertTrue(has_deny_all_access_level(row))

    def test_other_bit_only_returns_false(self):
        row = SimpleNamespace(access_level=0x2)
        self.assertFalse(has_deny_all_access_level(row))

    def test_deny_all_value_is_bitmask_0x1(self):
        self.assertEqual(0x1, CaseAccessLevel.deny_all.value)
