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

"""Unit tests for pure-logic functions in resolvers.py.

DB-touching helpers (login_exists, email_exists, find_lookup_ids_by_name, …)
are patched via unittest.mock so no database connection is needed.
"""

from unittest import TestCase
from unittest.mock import patch

from app.business.case_transfer.manifest import BundleFormatError
from app.business.case_transfer.resolvers import (
    count_principal_references,
    _principal_identity,
    inspect_principals,
    _allocate_login,
    _allocate_email,
    inspect_lookups,
    _lookup_entries,
)


# ---------------------------------------------------------------------------
# count_principal_references
# ---------------------------------------------------------------------------

class TestCountPrincipalReferences(TestCase):

    def test_empty_inputs_return_empty_counts(self):
        result = count_principal_references(None, None)
        self.assertEqual(result, {})

    def test_case_row_user_refs_counted(self):
        # CASE_SPEC.user_refs = ('user_id', 'owner_id', 'reviewer_id')
        case_row = {'user_id': 'user:1', 'owner_id': 'user:2', 'reviewer_id': 'user:1'}
        result = count_principal_references(case_row, None)
        self.assertEqual(result['user:1'], 2)
        self.assertEqual(result['user:2'], 1)

    def test_entity_rows_tallied(self):
        # 'asset' has user_refs = ('user_id',)
        entities = {
            'asset': [
                {'user_id': 'user:10'},
                {'user_id': 'user:10'},
                {'user_id': 'user:20'},
            ]
        }
        result = count_principal_references(None, entities)
        self.assertEqual(result['user:10'], 2)
        self.assertEqual(result['user:20'], 1)

    def test_entity_key_with_no_spec_is_skipped(self):
        entities = {'nonexistent_entity_key': [{'user_id': 'user:99'}]}
        result = count_principal_references(None, entities)
        self.assertNotIn('user:99', result)

    def test_entity_with_no_user_refs_is_skipped(self):
        # 'kanban' has no user_refs
        entities = {'kanban': [{'kanban_data': '{}', 'user_id': 'user:42'}]}
        result = count_principal_references(None, entities)
        self.assertNotIn('user:42', result)

    def test_none_values_in_row_not_counted(self):
        case_row = {'user_id': None, 'owner_id': 'user:5', 'reviewer_id': None}
        result = count_principal_references(case_row, None)
        self.assertNotIn(None, result)
        self.assertEqual(result.get('user:5'), 1)

    def test_case_row_and_entities_combined(self):
        case_row = {'user_id': 'user:1', 'owner_id': 'user:2', 'reviewer_id': None}
        entities = {
            'comment': [{'comment_user_id': 'user:1'}, {'comment_user_id': 'user:3'}],
        }
        result = count_principal_references(case_row, entities)
        self.assertEqual(result['user:1'], 2)  # once in case_row, once in comment
        self.assertEqual(result['user:2'], 1)
        self.assertEqual(result['user:3'], 1)

    def test_empty_entities_dict(self):
        result = count_principal_references(None, {})
        self.assertEqual(result, {})

    def test_empty_case_row(self):
        result = count_principal_references({}, None)
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# _principal_identity
# ---------------------------------------------------------------------------

class TestPrincipalIdentity(TestCase):

    def test_valid_principal_returned_normalised(self):
        principal = {
            'ref': 'user:1',
            'uuid': 'abc-123',
            'email': 'alice@example.com',
            'login': 'alice',
            'name': 'Alice',
            'external_id': 'ext-001',
        }
        result = _principal_identity(principal)
        self.assertEqual(result['ref'], 'user:1')
        self.assertEqual(result['uuid'], 'abc-123')
        self.assertEqual(result['email'], 'alice@example.com')
        self.assertEqual(result['login'], 'alice')
        self.assertEqual(result['name'], 'Alice')
        self.assertEqual(result['external_id'], 'ext-001')

    def test_missing_optional_fields_default_to_none(self):
        result = _principal_identity({'ref': 'user:2'})
        self.assertIsNone(result['uuid'])
        self.assertIsNone(result['email'])
        self.assertIsNone(result['login'])
        self.assertIsNone(result['name'])
        self.assertIsNone(result['external_id'])

    def test_not_a_dict_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity('not-a-dict')

    def test_none_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity(None)

    def test_list_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity(['ref', 'user:1'])

    def test_missing_ref_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity({'uuid': 'abc', 'email': 'x@y.com'})

    def test_empty_ref_string_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity({'ref': ''})

    def test_non_string_ref_raises_bundle_format_error(self):
        with self.assertRaises(BundleFormatError):
            _principal_identity({'ref': 42})

    def test_result_contains_exactly_expected_keys(self):
        result = _principal_identity({'ref': 'user:7'})
        self.assertEqual(set(result.keys()), {'ref', 'uuid', 'external_id', 'email', 'login', 'name'})


# ---------------------------------------------------------------------------
# inspect_principals — DB mocked via match_principals
# ---------------------------------------------------------------------------

class TestInspectPrincipals(TestCase):

    def _make_matched(self, ref, uuid=None, login=None, name=None, email=None,
                      external_id=None, matched_user_id=None, matched_by=None):
        return {
            ref: {
                'source': {
                    'ref': ref,
                    'uuid': uuid,
                    'login': login,
                    'name': name,
                    'email': email,
                    'external_id': external_id,
                },
                'matched_user_id': matched_user_id,
                'matched_by': matched_by,
            }
        }

    @patch('app.business.case_transfer.resolvers.match_principals')
    def test_report_includes_reference_count(self, mock_match):
        mock_match.return_value = self._make_matched('user:1', matched_user_id=5, matched_by='uuid')
        case_row = {'user_id': 'user:1', 'owner_id': 'user:1', 'reviewer_id': None}
        report = inspect_principals([{'ref': 'user:1'}], case_row, None)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]['ref'], 'user:1')
        self.assertEqual(report[0]['reference_count'], 2)

    @patch('app.business.case_transfer.resolvers.match_principals')
    def test_report_sorted_by_reference_count_descending(self, mock_match):
        mock_match.return_value = {
            **self._make_matched('user:1', matched_user_id=5),
            **self._make_matched('user:2', matched_user_id=6),
        }
        case_row = {
            'user_id': 'user:2', 'owner_id': 'user:2', 'reviewer_id': 'user:1',
        }
        report = inspect_principals([{'ref': 'user:1'}, {'ref': 'user:2'}], case_row, None)
        # user:2 has 2 references, user:1 has 1
        self.assertEqual(report[0]['ref'], 'user:2')
        self.assertEqual(report[1]['ref'], 'user:1')

    @patch('app.business.case_transfer.resolvers.match_principals')
    def test_unmatched_principal_has_none_matched_user_id(self, mock_match):
        mock_match.return_value = self._make_matched('user:99', matched_user_id=None, matched_by=None)
        report = inspect_principals([{'ref': 'user:99'}], None, None)
        self.assertIsNone(report[0]['matched_user_id'])
        self.assertIsNone(report[0]['matched_by'])

    @patch('app.business.case_transfer.resolvers.match_principals')
    def test_principal_not_in_case_row_has_zero_count(self, mock_match):
        mock_match.return_value = self._make_matched('user:5', matched_user_id=10)
        report = inspect_principals([{'ref': 'user:5'}], None, None)
        self.assertEqual(report[0]['reference_count'], 0)

    @patch('app.business.case_transfer.resolvers.match_principals')
    def test_ties_in_reference_count_sorted_by_ref_alphabetically(self, mock_match):
        mock_match.return_value = {
            **self._make_matched('user:b'),
            **self._make_matched('user:a'),
        }
        # Neither appears in case_row or entities, so both have count 0
        report = inspect_principals([{'ref': 'user:a'}, {'ref': 'user:b'}], None, None)
        self.assertEqual(report[0]['ref'], 'user:a')
        self.assertEqual(report[1]['ref'], 'user:b')


# ---------------------------------------------------------------------------
# _allocate_login
# ---------------------------------------------------------------------------

class TestAllocateLogin(TestCase):

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=False)
    def test_preferred_login_returned_when_free(self, _mock):
        result = _allocate_login('alice', 'Alice')
        self.assertEqual(result, 'alice')

    @patch('app.business.case_transfer.resolvers.login_exists', side_effect=[True, False])
    def test_suffix_added_when_preferred_taken(self, _mock):
        # First call (base) → taken, second call (base-2) → free
        result = _allocate_login('alice', 'Alice')
        self.assertEqual(result, 'alice-2')

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=False)
    def test_fallback_used_when_preferred_is_none(self, _mock):
        result = _allocate_login(None, 'Bob Smith')
        self.assertEqual(result, 'Bob Smith')

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=False)
    def test_default_used_when_both_none(self, _mock):
        result = _allocate_login(None, None)
        self.assertEqual(result, 'imported-user')

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=False)
    def test_whitespace_only_preferred_falls_back_to_default(self, _mock):
        result = _allocate_login('   ', None)
        self.assertEqual(result, 'imported-user')

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=False)
    def test_login_truncated_to_64_chars(self, _mock):
        long_login = 'a' * 100
        result = _allocate_login(long_login, None)
        self.assertLessEqual(len(result), 64)

    @patch('app.business.case_transfer.resolvers.login_exists', return_value=True)
    def test_raises_when_no_free_login_found(self, _mock):
        from app.models.errors import BusinessProcessingError
        with self.assertRaises(BusinessProcessingError):
            _allocate_login('taken', None)


# ---------------------------------------------------------------------------
# _allocate_email
# ---------------------------------------------------------------------------

class TestAllocateEmail(TestCase):

    @patch('app.business.case_transfer.resolvers.email_exists', return_value=False)
    def test_preferred_email_returned_when_free(self, _mock):
        result = _allocate_email('alice@example.com', 'alice')
        self.assertEqual(result, 'alice@example.com')

    @patch('app.business.case_transfer.resolvers.email_exists', side_effect=[True, False])
    def test_synthesised_email_used_when_preferred_taken(self, _mock):
        # First call: preferred email exists; second call: synthesised candidate is free
        result = _allocate_email('alice@example.com', 'alice')
        self.assertEqual(result, 'alice@iris-import.invalid')

    @patch('app.business.case_transfer.resolvers.email_exists', return_value=False)
    def test_synthesised_email_when_no_preferred(self, _mock):
        result = _allocate_email(None, 'bob')
        self.assertEqual(result, 'bob@iris-import.invalid')

    @patch('app.business.case_transfer.resolvers.email_exists', side_effect=[True, True])
    def test_randomised_fallback_when_both_taken(self, _mock):
        result = _allocate_email('alice@example.com', 'alice')
        self.assertIn('@iris-import.invalid', result)
        # The randomised form includes a hex token: alice-<hex>@iris-import.invalid
        self.assertIn('alice-', result)

    @patch('app.business.case_transfer.resolvers.email_exists', return_value=False)
    def test_synthesised_email_uses_dot_invalid_domain(self, _mock):
        result = _allocate_email(None, 'carol')
        self.assertTrue(result.endswith('@iris-import.invalid'))


# ---------------------------------------------------------------------------
# _lookup_entries
# ---------------------------------------------------------------------------

class TestLookupEntries(TestCase):

    def test_returns_list_for_existing_key(self):
        lookups = {'tag': [{'ref': 'tag:1', 'name': 'malware'}]}
        result = _lookup_entries(lookups, 'tag')
        self.assertEqual(result, [{'ref': 'tag:1', 'name': 'malware'}])

    def test_returns_empty_list_for_missing_key(self):
        result = _lookup_entries({}, 'tag')
        self.assertEqual(result, [])

    def test_returns_empty_list_for_none_lookups(self):
        result = _lookup_entries(None, 'tag')
        self.assertEqual(result, [])

    def test_raises_if_entry_is_not_a_list(self):
        lookups = {'tag': {'ref': 'tag:1'}}
        with self.assertRaises(BundleFormatError):
            _lookup_entries(lookups, 'tag')


# ---------------------------------------------------------------------------
# inspect_lookups — DB mocked
# ---------------------------------------------------------------------------

class TestInspectLookups(TestCase):

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={'malware': 7})
    def test_matched_entry_has_matched_id_and_no_will_create(self, _mock):
        lookups = {'tag': [{'ref': 'tag:1', 'name': 'malware'}]}
        report = inspect_lookups(lookups)
        tag_report = report['tag']
        self.assertEqual(len(tag_report), 1)
        entry = tag_report[0]
        self.assertEqual(entry['matched_id'], 7)
        self.assertFalse(entry['will_create'])
        self.assertFalse(entry['requires_decision'])

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={})
    def test_creatable_missing_entry_will_create(self, _mock):
        # 'tag' is creatable
        lookups = {'tag': [{'ref': 'tag:1', 'name': 'new-tag'}]}
        report = inspect_lookups(lookups)
        entry = report['tag'][0]
        self.assertIsNone(entry['matched_id'])
        self.assertTrue(entry['will_create'])
        self.assertFalse(entry['requires_decision'])

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={})
    def test_non_creatable_missing_entry_requires_decision(self, _mock):
        # 'case_state' is not creatable
        lookups = {'case_state': [{'ref': 'cs:1', 'name': 'Open'}]}
        report = inspect_lookups(lookups)
        entry = report['case_state'][0]
        self.assertIsNone(entry['matched_id'])
        self.assertFalse(entry['will_create'])
        self.assertTrue(entry['requires_decision'])

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={'Open': 3})
    def test_empty_lookups_skipped_in_report(self, _mock):
        # Pass only 'tag' entries; 'case_state' is absent → not in report
        lookups = {'tag': [{'ref': 'tag:1', 'name': 'malware'}]}
        report = inspect_lookups(lookups)
        self.assertNotIn('case_state', report)

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={})
    def test_empty_lookups_dict_returns_empty_report(self, _mock):
        report = inspect_lookups({})
        self.assertEqual(report, {})

    @patch('app.business.case_transfer.resolvers.find_lookup_ids_by_name', return_value={})
    def test_report_entry_includes_ref_and_name(self, _mock):
        lookups = {'tag': [{'ref': 'tag:99', 'name': 'ransomware'}]}
        report = inspect_lookups(lookups)
        entry = report['tag'][0]
        self.assertEqual(entry['ref'], 'tag:99')
        self.assertEqual(entry['name'], 'ransomware')
