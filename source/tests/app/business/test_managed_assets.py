#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
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

"""Unit tests for the asset registry's dedup identity.

`managed_assets_normalize_name` decides whether two names are the same
asset. It is the single source of truth for that question, and two
copies of it exist in SQL — the expression index created by the
migration, and `_normalized_case_asset_name` in datamgmt, which is what
resolves a registry row to its case and alert sightings. All three must
agree exactly: if they drift, an asset stops finding its own sightings
and the UNIQUE constraint stops matching what the application thinks is
a duplicate.

That is why these cases are spelled out one by one rather than left to
the REST suite. They are the contract the SQL is written against.
"""

from unittest import TestCase

from app.business.managed_assets import managed_assets_normalize_name


class TestManagedAssetsNormalizeName(TestCase):

    def test_normalize_name_should_lowercase_the_value(self):
        self.assertEqual('srv-dc01', managed_assets_normalize_name('SRV-DC01'))

    def test_normalize_name_should_leave_an_already_normalized_value_alone(self):
        # Idempotence is a DB-level requirement: the CHECK constraint is
        # `normalized_name = lower(btrim(normalized_name))`.
        self.assertEqual('srv-dc01', managed_assets_normalize_name('srv-dc01'))

    def test_normalize_name_should_strip_surrounding_whitespace(self):
        self.assertEqual('srv-dc01', managed_assets_normalize_name('   SRV-DC01   '))

    def test_normalize_name_should_collapse_an_internal_whitespace_run(self):
        self.assertEqual('dc 01', managed_assets_normalize_name('DC     01'))

    def test_normalize_name_should_fold_a_tab_to_a_space(self):
        self.assertEqual('dc 01', managed_assets_normalize_name('DC\t01'))

    def test_normalize_name_should_fold_a_newline_to_a_space(self):
        self.assertEqual('dc 01', managed_assets_normalize_name('DC\n01'))

    def test_normalize_name_should_fold_a_carriage_return_to_a_space(self):
        self.assertEqual('dc 01', managed_assets_normalize_name('DC\r\n01'))

    def test_normalize_name_should_return_an_empty_string_for_an_empty_value(self):
        self.assertEqual('', managed_assets_normalize_name(''))

    def test_normalize_name_should_return_an_empty_string_for_none(self):
        # Business callers reject the empty result rather than storing
        # it; the CHECK constraint refuses a zero-length name outright.
        self.assertEqual('', managed_assets_normalize_name(None))

    def test_normalize_name_should_return_an_empty_string_for_whitespace_only(self):
        self.assertEqual('', managed_assets_normalize_name(' \t\n '))

    def test_normalize_name_should_preserve_the_length_of_a_long_name(self):
        name = 'A' * 512

        self.assertEqual(512, len(managed_assets_normalize_name(name)))

    def test_normalize_name_should_not_unicode_fold(self):
        # Deliberate: NFKC would merge visually distinct names an analyst
        # then cannot tell apart. `Ⅾ` (U+216E) stays itself.
        self.assertNotEqual('dc01', managed_assets_normalize_name('ⅮC01'))

    def test_normalize_name_should_treat_a_case_difference_as_the_same_asset(self):
        self.assertEqual(managed_assets_normalize_name('SRV-DC01'),
                         managed_assets_normalize_name('srv-dc01'))

    def test_normalize_name_should_treat_a_spacing_difference_as_the_same_asset(self):
        self.assertEqual(managed_assets_normalize_name('web  server'),
                         managed_assets_normalize_name(' Web Server '))

    def test_normalize_name_should_keep_two_different_hosts_distinct(self):
        self.assertNotEqual(managed_assets_normalize_name('SRV-DC01'),
                            managed_assets_normalize_name('SRV-DC02'))
