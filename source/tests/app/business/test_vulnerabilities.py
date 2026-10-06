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

"""Unit tests for the vulnerability catalogue business layer (no database)."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business import vulnerabilities as biz
from app.models.errors import BusinessProcessingError

_BIZ = 'app.business.vulnerabilities'
_V31_CRITICAL = 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H'


class TestNormalizeIdentifier(TestCase):

    def test_cve_should_be_upper_cased(self):
        self.assertEqual(biz.vulnerabilities_normalize_identifier('cve-2024-3400'), 'CVE-2024-3400')
        self.assertEqual(biz.vulnerabilities_normalize_identifier('  Cve-2021-44228 '), 'CVE-2021-44228')
        self.assertEqual(biz.vulnerabilities_normalize_identifier('CVE-2024-1234567'), 'CVE-2024-1234567')

    def test_malformed_cve_should_be_rejected(self):
        for value in ('CVE-2024-123', 'CVE-24-1234', 'cve-2024-', 'CVE-2024-12a4', 'CVE-2024-1234-5'):
            with self.assertRaises(BusinessProcessingError, msg=value):
                biz.vulnerabilities_normalize_identifier(value)

    def test_ghsa_should_keep_an_upper_prefix_and_a_lower_body(self):
        self.assertEqual(biz.vulnerabilities_normalize_identifier('ghsa-ABCD-ef12-3456'), 'GHSA-abcd-ef12-3456')
        self.assertEqual(biz.vulnerabilities_normalize_identifier('GHSA-jfh8-c2jp-5v3q'), 'GHSA-jfh8-c2jp-5v3q')

    def test_malformed_ghsa_should_be_rejected(self):
        for value in ('GHSA-abc-defg-hijk', 'GHSA-abcd-efgh', 'ghsa-abcd-efgh-ijkl-mnop', 'GHSA-ab_d-efgh-ijkl'):
            with self.assertRaises(BusinessProcessingError, msg=value):
                biz.vulnerabilities_normalize_identifier(value)

    def test_private_prefix_should_be_upper_cased_and_detected(self):
        identifier = biz.vulnerabilities_normalize_identifier('iris-vuln-2026-0001')
        self.assertEqual(identifier, 'IRIS-VULN-2026-0001')
        self.assertTrue(biz.vulnerabilities_is_private_identifier(identifier))
        self.assertFalse(biz.vulnerabilities_is_private_identifier('CVE-2024-3400'))

    def test_generic_identifiers_should_be_upper_cased(self):
        self.assertEqual(biz.vulnerabilities_normalize_identifier('ms17-010'), 'MS17-010')
        self.assertEqual(biz.vulnerabilities_normalize_identifier('rhsa-2024:1234'), 'RHSA-2024:1234')
        self.assertEqual(biz.vulnerabilities_normalize_identifier('zdi-24.001_x'), 'ZDI-24.001_X')

    def test_invalid_generic_identifiers_should_be_rejected(self):
        for value in ('', ' ', 'a', 'with space', '-leading', 'bad/char', 'x' * 65, 'é-123'):
            with self.assertRaises(BusinessProcessingError, msg=value):
                biz.vulnerabilities_normalize_identifier(value)

    def test_non_string_should_be_rejected_with_the_field_name(self):
        for value in (None, 2024, ['CVE-2024-3400']):
            with self.assertRaises(BusinessProcessingError) as context:
                biz.vulnerabilities_normalize_identifier(value, 'aliases')
            self.assertIn('aliases', context.exception.get_message())


class TestQueryParsers(TestCase):

    def test_query_bool_absent_should_be_none(self):
        self.assertIsNone(biz.vulnerabilities_parse_query_bool(None, 'kev'))
        self.assertIsNone(biz.vulnerabilities_parse_query_bool('', 'kev'))

    def test_query_bool_should_accept_strings_and_booleans(self):
        for value in ('1', 'true', 'TRUE', ' Yes ', True):
            self.assertIs(biz.vulnerabilities_parse_query_bool(value, 'kev'), True, value)
        for value in ('0', 'false', 'False', 'no', False):
            self.assertIs(biz.vulnerabilities_parse_query_bool(value, 'kev'), False, value)

    def test_query_bool_should_reject_anything_else(self):
        for value in ('maybe', 'on', 1, 0, ['true']):
            with self.assertRaises(BusinessProcessingError, msg=value):
                biz.vulnerabilities_parse_query_bool(value, 'kev')

    def test_query_list_absent_should_be_empty(self):
        self.assertEqual(biz.vulnerabilities_parse_query_list(None, 'severity', ('high',)), [])
        self.assertEqual(biz.vulnerabilities_parse_query_list('', 'severity', ('high',)), [])

    def test_query_list_should_split_strip_and_drop_blanks(self):
        choices = ('critical', 'high', 'low')
        self.assertEqual(biz.vulnerabilities_parse_query_list('critical, high,,', 'severity', choices),
                         ['critical', 'high'])
        self.assertEqual(biz.vulnerabilities_parse_query_list([' low ', 'high'], 'severity', choices),
                         ['low', 'high'])

    def test_query_list_should_reject_unknown_values_and_bad_types(self):
        for value in ('critical,bogus', 'CRITICAL', [1], {'a': 'b'}, 3):
            with self.assertRaises(BusinessProcessingError, msg=value):
                biz.vulnerabilities_parse_query_list(value, 'severity', ('critical',))

    def test_choice(self):
        self.assertEqual(biz.vulnerabilities_parse_choice('cve', 'kind', ('cve', 'other')), 'cve')
        for value in ('bogus', None, 'CVE'):
            with self.assertRaises(BusinessProcessingError):
                biz.vulnerabilities_parse_choice(value, 'kind', ('cve', 'other'))

    def test_id(self):
        self.assertIsNone(biz.vulnerabilities_parse_id(None, 'x'))
        self.assertEqual(biz.vulnerabilities_parse_id(4, 'x'), 4)
        for value in (True, '4', 4.0):
            with self.assertRaises(BusinessProcessingError):
                biz.vulnerabilities_parse_id(value, 'x')

    def test_dates(self):
        self.assertEqual(biz.vulnerabilities_parse_date('2024-04-12', 'd'), datetime.date(2024, 4, 12))
        self.assertEqual(biz.vulnerabilities_parse_date('2024-04-12T10:00:00Z', 'd'), datetime.date(2024, 4, 12))
        self.assertIsNone(biz.vulnerabilities_parse_date('', 'd'))
        self.assertEqual(biz.vulnerabilities_parse_datetime('2024-04-12T10:00:00+02:00', 'd'),
                         datetime.datetime(2024, 4, 12, 8, 0, 0))
        self.assertEqual(biz.vulnerabilities_parse_datetime('2024-04-12T10:00:00Z', 'd'),
                         datetime.datetime(2024, 4, 12, 10, 0, 0))
        for parser in (biz.vulnerabilities_parse_date, biz.vulnerabilities_parse_datetime):
            for value in ('not a date', 20240412):
                with self.assertRaises(BusinessProcessingError):
                    parser(value, 'd')


class TestTransferIdentifier(TestCase):

    def test_public_identifiers_should_be_normalised(self):
        self.assertEqual(biz._transfer_identifier('cve-2024-3400'), 'CVE-2024-3400')
        self.assertEqual(biz._transfer_identifier('GHSA-ABCD-efgh-1234'), 'GHSA-abcd-efgh-1234')

    def test_private_or_invalid_should_be_none(self):
        for name in ('IRIS-VULN-2026-0001', 'iris-vuln-2026-0042', 'CVE-1-1', 'bad name', '', None, 12):
            self.assertIsNone(biz._transfer_identifier(name), name)


def _entry(identifier, vulnerability_id, is_private=False):
    return SimpleNamespace(identifier=identifier, vulnerability_id=vulnerability_id, is_private=is_private)


class TestTransferMatch(TestCase):

    def test_should_match_normalised_names_and_never_private_entries(self):
        catalogue = {
            'CVE-2024-3400': _entry('CVE-2024-3400', 1),
            # A public alias held by a private entry of this instance.
            'GHSA-abcd-efgh-1234': _entry('IRIS-VULN-2026-0003', 2, is_private=True),
        }
        with patch(f'{_BIZ}.vulnerabilities_db_find_many', return_value=catalogue) as find_many:
            result = biz.vulnerabilities_transfer_match(
                {'cve-2024-3400', 'GHSA-abcd-efgh-1234', 'IRIS-VULN-2026-0001', 'CVE-2023-9999', 'bad name', None})
        self.assertEqual(result, {'cve-2024-3400': 1})
        queried = find_many.call_args.args[0]
        self.assertEqual(set(queried), {'CVE-2024-3400', 'GHSA-abcd-efgh-1234', 'CVE-2023-9999'})

    def test_no_usable_name_should_query_nothing_matchable(self):
        with patch(f'{_BIZ}.vulnerabilities_db_find_many', return_value={}) as find_many:
            self.assertEqual(biz.vulnerabilities_transfer_match(['IRIS-VULN-2026-0001', 'bad name']), {})
        self.assertEqual(set(find_many.call_args.args[0]), set())


class TestTransferCreate(TestCase):

    def setUp(self):
        self.added = []

        def _add(row):
            row.vulnerability_id = 77
            self.added.append(row)

        self._patchers = {
            'add': patch(f'{_BIZ}.vulnerabilities_db_add', side_effect=_add),
            'flush': patch(f'{_BIZ}.vulnerabilities_db_flush', return_value=True),
            'commit': patch(f'{_BIZ}.vulnerabilities_db_commit', return_value=True),
            'tlp': patch(f'{_BIZ}.vulnerabilities_db_tlp_exists', return_value=True),
            'aliases': patch(f'{_BIZ}.vulnerabilities_db_replace_aliases'),
            'track': patch(f'{_BIZ}.track_activity'),
        }
        self.mocks = {name: patcher.start() for name, patcher in self._patchers.items()}

    def tearDown(self):
        for patcher in self._patchers.values():
            patcher.stop()

    def test_should_create_an_imported_public_entry_without_committing(self):
        entry = {
            'ref': 'vulnerability:5', 'name': 'cve-2024-3400', 'title': 'PAN-OS GlobalProtect',
            'description': 'Command injection', 'cvss_vector': _V31_CRITICAL, 'cvss_version': '3.1',
            'cvss_score': 10.0, 'severity': 'critical', 'kev': True, 'published_at': '2024-04-12',
            'cwes': ['cwe-77'], 'exploit_maturity': 'in-the-wild', 'patch_availability': 'patch',
            # Not part of the transfer fields: must be ignored.
            'source': 'manual', 'tlp_id': 3, 'created_by_id': 5, 'enrichment': {'x': 1}, 'is_private': True,
        }
        self.assertEqual(biz.vulnerabilities_transfer_create(entry), 77)

        self.assertEqual(len(self.added), 1)
        row = self.added[0]
        self.assertEqual(row.identifier, 'CVE-2024-3400')
        self.assertFalse(row.is_private)
        self.assertEqual(row.source, 'import')
        self.assertEqual(row.kind, 'cve')
        self.assertEqual(row.title, 'PAN-OS GlobalProtect')
        self.assertEqual(row.cvss_version, '3.1')
        self.assertEqual(row.cvss_score, 10.0)
        self.assertEqual(row.severity, 'critical')
        self.assertTrue(row.kev)
        self.assertEqual(row.cwes, ['CWE-77'])
        self.assertEqual(row.published_at, datetime.date(2024, 4, 12))
        self.assertIsNone(row.tlp_id)
        self.assertIsNone(row.created_by_id)
        self.assertIsNone(row.enrichment)
        self.mocks['flush'].assert_called_once()
        self.mocks['commit'].assert_not_called()
        self.mocks['tlp'].assert_not_called()
        self.mocks['track'].assert_not_called()

    def test_should_default_the_title_kind_and_derive_the_score(self):
        biz.vulnerabilities_transfer_create({'name': 'GHSA-abcd-efgh-1234', 'title': None,
                                             'cvss_vector': _V31_CRITICAL})
        row = self.added[0]
        self.assertEqual(row.title, 'GHSA-abcd-efgh-1234')
        self.assertEqual(row.kind, 'advisory')
        self.assertEqual(row.cvss_score, 9.8)
        self.assertEqual(row.severity, 'critical')
        self.assertEqual(row.source, 'import')

    def test_without_scoring_severity_should_be_unknown(self):
        biz.vulnerabilities_transfer_create({'name': 'CVE-2024-0001'})
        self.assertEqual(self.added[0].severity, 'unknown')
        self.assertIsNone(self.added[0].cvss_score)

    def test_private_or_invalid_names_should_be_refused(self):
        for name in ('IRIS-VULN-2026-0001', 'not valid!', None):
            with self.assertRaises(BusinessProcessingError, msg=name):
                biz.vulnerabilities_transfer_create({'name': name, 'title': 'x'})
        self.assertEqual(self.added, [])

    def test_invalid_fields_should_be_refused_before_adding(self):
        for entry in ({'name': 'CVE-2024-0001', 'kind': 'bogus'},
                      {'name': 'CVE-2024-0001', 'cvss_vector': 'garbage'},
                      {'name': 'CVE-2024-0001', 'cvss_score': 11},
                      {'name': 'CVE-2024-0001', 'reference_urls': ['ftp://x']}):
            with self.assertRaises(BusinessProcessingError, msg=entry):
                biz.vulnerabilities_transfer_create(entry)
        self.assertEqual(self.added, [])

    def test_flush_failure_should_raise(self):
        self.mocks['flush'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            biz.vulnerabilities_transfer_create({'name': 'CVE-2024-0001'})
        self.mocks['commit'].assert_not_called()


class TestHidePrivateContent(TestCase):

    def _private(self):
        return SimpleNamespace(vulnerability_id=3, identifier='IRIS-VULN-2026-0001', is_private=True)

    def test_lookup_of_a_private_entry_by_alias_should_find_nothing(self):
        with patch(f'{_BIZ}.vulnerabilities_db_find', return_value=self._private()), \
                patch(f'{_BIZ}.vulnerabilities_get_public', return_value={'vulnerability_id': 3}):
            self.assertIsNone(biz.vulnerabilities_lookup('ACME-SEC-12', None, None, hide_private_content=True))
            self.assertEqual(biz.vulnerabilities_lookup('ACME-SEC-12', None, None), {'vulnerability_id': 3})
            self.assertEqual(
                biz.vulnerabilities_lookup('iris-vuln-2026-0001', None, None, hide_private_content=True),
                {'vulnerability_id': 3})

    def _pagination(self, order_by=None):
        return SimpleNamespace(get_page=lambda: 1, get_per_page=lambda: 25,
                               get_order_by=lambda: order_by, get_direction=lambda: None)

    def test_search_should_forward_the_flag_to_the_filters(self):
        with patch(f'{_BIZ}.vulnerabilities_db_search', return_value=([], 0)) as search:
            biz.vulnerabilities_search({'search': 'portal'}, self._pagination(), None, None,
                                       hide_private_content=True)
        self.assertTrue(search.call_args.args[0]['private_content_hidden'])

    def test_search_should_refuse_ordering_on_the_title(self):
        with patch(f'{_BIZ}.vulnerabilities_db_search', return_value=([], 0)):
            with self.assertRaises(BusinessProcessingError):
                biz.vulnerabilities_search({}, self._pagination('title'), None, None, hide_private_content=True)
            biz.vulnerabilities_search({}, self._pagination('title'), None, None)
