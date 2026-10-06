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

"""Unit tests of the cve.org synchronisation (no database, no network).

The records under `cve_records/` are real CVE JSON 5 records as the CVE
Services API served them."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

import requests

from app.business import vulnerabilities_cve_sync as sync
from app.iris_engine.vulnerabilities.cve_org import CveOrgError
from app.iris_engine.vulnerabilities.cve_org import cve_org_fetch
from app.models.errors import BusinessProcessingError

_SYNC = 'app.business.vulnerabilities_cve_sync'
_RECORDS = Path(__file__).parent / 'cve_records'


def _record(cve_id):
    return json.loads((_RECORDS / f'{cve_id}.json').read_text())


def _parse(cve_id):
    return sync.vulnerabilities_cve_parse(_record(cve_id))


def _entry(**fields):
    values = {
        'vulnerability_id': 1, 'identifier': 'CVE-2024-3400', 'is_private': False, 'title': 'CVE-2024-3400',
        'description': None, 'kind': 'cve', 'cvss_vector': None, 'cvss_version': None, 'cvss_score': None,
        'severity': 'unknown', 'cwes': None, 'affected_products': None, 'reference_urls': None,
        'published_at': None, 'modified_at': None, 'kev': False, 'kev_date_added': None,
        'exploit_maturity': 'unknown', 'source': 'manual', 'enrichment': None,
    }
    values.update(fields)
    return SimpleNamespace(**values)


class TestParse(TestCase):

    def test_cna_fields_should_be_mapped(self):
        fields = _parse('CVE-2024-3400')['fields']
        self.assertEqual(fields['title'],
                         'PAN-OS: Arbitrary File Creation Leads to OS Command Injection Vulnerability in GlobalProtect')
        self.assertTrue(fields['description'].startswith('A command injection'))
        self.assertEqual(fields['cvss_vector'], 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H')
        self.assertEqual(fields['cvss_version'], '3.1')
        self.assertEqual(fields['cvss_score'], 10.0)
        self.assertEqual(fields['severity'], 'critical')
        self.assertEqual(fields['published_at'], '2024-04-12')
        self.assertEqual(fields['modified_at'], '2025-10-21')
        self.assertIn('CWE-77', fields['cwes'])

    def test_only_affected_products_should_be_kept_with_their_ranges(self):
        products = _parse('CVE-2024-3400')['fields']['affected_products']
        self.assertEqual(products, [{
            'vendor': 'Palo Alto Networks', 'product': 'PAN-OS',
            'versions': '10.2.0 to < 10.2.9-h1, 11.0.0 to < 11.0.4-h1, 11.1.0 to < 11.1.2-h3',
        }])

    def test_upper_bound_equal_to_the_version_should_read_as_below(self):
        products = _parse('CVE-2023-4863')['fields']['affected_products']
        self.assertIn({'vendor': 'Google', 'product': 'Chrome', 'versions': '< 116.0.5845.187'}, products)

    def test_package_name_should_stand_in_for_a_missing_product(self):
        products = _parse('CVE-2024-6387')['fields']['affected_products']
        self.assertEqual(products[0], {'product': 'OpenSSH', 'versions': '8.5p1 to <= 9.7p1'})

    def test_adp_cvss_should_fill_a_cna_without_cvss(self):
        fields = _parse('CVE-2021-44228')['fields']
        self.assertEqual(fields['cvss_vector'], 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H')
        self.assertEqual(fields['severity'], 'critical')

    def test_kev_listing_should_mean_exploited_in_the_wild(self):
        fields = _parse('CVE-2021-44228')['fields']
        self.assertTrue(fields['kev'])
        self.assertEqual(fields['kev_date_added'], '2021-12-10')
        self.assertEqual(fields['exploit_maturity'], 'in-the-wild')

    def test_ssvc_poc_should_map_to_poc(self):
        fields = _parse('CVE-2024-6387')['fields']
        self.assertFalse(fields['kev'])
        self.assertEqual(fields['exploit_maturity'], 'poc')

    def test_record_without_title_or_metrics_should_use_the_description(self):
        fields = _parse('CVE-1999-0001')['fields']
        self.assertTrue(fields['title'].startswith('ip_input.c in BSD-derived'))
        self.assertIsNone(fields['cvss_vector'])
        self.assertIsNone(fields['cvss_score'])
        self.assertEqual(fields['affected_products'], [])

    def test_references_should_be_deduplicated_and_capped(self):
        urls = _parse('CVE-2021-44228')['fields']['reference_urls']
        self.assertEqual(len(urls), len(set(urls)))
        self.assertLessEqual(len(urls), 100)
        self.assertTrue(all(url.startswith('http') for url in urls))

    def test_rejected_record_should_raise(self):
        record = _record('CVE-1999-0001')
        record['cveMetadata']['state'] = 'REJECTED'
        record['containers']['cna']['rejectedReasons'] = [{'lang': 'en', 'value': 'Duplicate of CVE-1999-0002.'}]
        with self.assertRaisesRegex(BusinessProcessingError, 'rejected.*Duplicate'):
            sync.vulnerabilities_cve_parse(record)

    def test_unparseable_vector_should_fall_back_to_the_score(self):
        record = _record('CVE-2024-3400')
        record['containers']['cna']['metrics'][0]['cvssV3_1']['vectorString'] = 'garbage'
        fields = sync.vulnerabilities_cve_parse(record)['fields']
        self.assertIsNone(fields['cvss_vector'])
        self.assertEqual(fields['cvss_version'], '3.1')
        self.assertEqual(fields['cvss_score'], 10.0)

    def test_parsed_fields_should_pass_the_catalogue_validation(self):
        from app.business.vulnerabilities import _apply_fields
        for path in _RECORDS.glob('*.json'):
            fields = sync.vulnerabilities_cve_parse(json.loads(path.read_text()))['fields']
            with patch('app.business.vulnerabilities.vulnerabilities_db_tlp_exists', return_value=True):
                _apply_fields(SimpleNamespace(cvss_vector=None, cvss_version=None, cvss_score=None,
                                              severity='unknown'), fields, creating=True)


class TestSync(TestCase):

    def setUp(self):
        patcher = patch(f'{_SYNC}._proxies', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.update = patch(f'{_SYNC}.vulnerabilities_update').start()
        self.addCleanup(patch.stopall)

    def _sync(self, entry, cve_id='CVE-2024-3400', force=False, record=None):
        with patch(f'{_SYNC}.cve_org_fetch', return_value=record or _record(cve_id)):
            report = sync.vulnerabilities_cve_sync(entry, 7, force=force)
        body = self.update.call_args.args[1]
        return report, body

    def test_bare_entry_should_be_filled(self):
        report, body = self._sync(_entry())
        self.assertEqual(body['title'],
                         'PAN-OS: Arbitrary File Creation Leads to OS Command Injection Vulnerability in GlobalProtect')
        self.assertEqual(body['cvss_vector'], 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H')
        self.assertTrue(body['kev'])
        self.assertEqual(body['exploit_maturity'], 'in-the-wild')
        self.assertEqual(body['source'], 'import')
        self.assertIn('title', report['updated_fields'])
        self.assertIn('cvss', report['updated_fields'])
        self.assertEqual(report['kept_fields'], [])
        marker = body['enrichment']['cve_org']
        self.assertEqual(marker['assigner'], 'palo_alto')
        self.assertEqual(marker['values']['cvss_score'], 10.0)

    def test_local_edit_should_be_kept(self):
        values = _parse('CVE-2024-3400')['fields']
        entry = _entry(title='Our own title', enrichment={'cve_org': {'values': values}, 'other': 1})
        report, body = self._sync(entry)
        self.assertNotIn('title', body)
        self.assertIn('title', report['kept_fields'])
        self.assertEqual(body['enrichment']['other'], 1)

    def test_force_should_overwrite_a_local_edit(self):
        values = _parse('CVE-2024-3400')['fields']
        entry = _entry(title='Our own title', enrichment={'cve_org': {'values': values}})
        report, body = self._sync(entry, force=True)
        self.assertEqual(body['title'], values['title'])
        self.assertIn('title', report['updated_fields'])

    def test_value_written_by_the_last_sync_should_follow_the_record(self):
        values = dict(_parse('CVE-2024-3400')['fields'])
        values['title'] = 'Old upstream title'
        entry = _entry(title='Old upstream title', enrichment={'cve_org': {'values': values}})
        report, body = self._sync(entry)
        self.assertIn('title', report['updated_fields'])
        self.assertTrue(body['title'].startswith('PAN-OS'))

    def test_hand_scored_entry_should_keep_its_cvss(self):
        entry = _entry(cvss_vector='CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H', cvss_version='3.1',
                       cvss_score=9.8, severity='critical')
        report, body = self._sync(entry)
        self.assertNotIn('cvss_vector', body)
        self.assertIn('cvss', report['kept_fields'])

    def test_exploit_maturity_should_never_go_down(self):
        entry = _entry(identifier='CVE-2024-6387', title='CVE-2024-6387', exploit_maturity='weaponized')
        _report, body = self._sync(entry, cve_id='CVE-2024-6387')
        self.assertNotIn('exploit_maturity', body)

    def test_unchanged_entry_should_report_nothing(self):
        _report, first = self._sync(_entry())
        entry = _entry(**{key: value for key, value in first.items() if key not in ('source', 'kind')})
        entry.cvss_version = '3.1'
        report, _body = self._sync(entry)
        self.assertEqual(report['updated_fields'], [])
        self.assertEqual(report['kept_fields'], [])

    def test_private_entry_should_be_refused(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Private'):
            self._sync(_entry(identifier='IRIS-VULN-2026-0001', is_private=True))
        self.update.assert_not_called()

    def test_non_cve_identifier_should_be_refused(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Only CVE'):
            self._sync(_entry(identifier='GHSA-jfh8-c2jp-5v3q'))

    def test_disabled_sync_should_be_refused(self):
        with patch(f'{_SYNC}.vulnerabilities_cve_sync_enabled', return_value=False):
            with self.assertRaisesRegex(BusinessProcessingError, 'disabled'):
                self._sync(_entry())

    def test_unknown_cve_should_say_not_found(self):
        with patch(f'{_SYNC}.cve_org_fetch', side_effect=CveOrgError('nope', not_found=True)):
            with self.assertRaises(BusinessProcessingError) as raised:
                sync.vulnerabilities_cve_sync(_entry(identifier='CVE-2024-99999'), 7)
        self.assertEqual(raised.exception.get_data(), {'not_found': True})


class TestQuickAddFill(TestCase):

    def test_bare_cve_should_be_synced_and_failures_swallowed(self):
        entry = _entry()
        with patch(f'{_SYNC}.vulnerabilities_cve_sync', side_effect=BusinessProcessingError('down')) as synced:
            sync.vulnerabilities_cve_fill_quick_add(entry, 7)
        synced.assert_called_once()
        self.assertLess(synced.call_args.kwargs['timeout'], 10)

    def test_titled_synced_or_private_entries_should_be_left_alone(self):
        cases = (_entry(title='Named'), _entry(enrichment={'cve_org': {}}),
                 _entry(identifier='IRIS-VULN-2026-0001', title='IRIS-VULN-2026-0001', is_private=True),
                 _entry(identifier='GHSA-jfh8-c2jp-5v3q', title='GHSA-jfh8-c2jp-5v3q'))
        with patch(f'{_SYNC}.vulnerabilities_cve_sync') as synced:
            for entry in cases:
                sync.vulnerabilities_cve_fill_quick_add(entry, 7)
        synced.assert_not_called()


class TestLookup(TestCase):

    def test_lookup_should_return_fields_and_the_existing_entry(self):
        with patch(f'{_SYNC}._proxies', return_value=None), \
                patch(f'{_SYNC}.cve_org_fetch', return_value=_record('CVE-2024-3400')), \
                patch(f'{_SYNC}.vulnerabilities_db_find', return_value=SimpleNamespace(vulnerability_id=42)):
            result = sync.vulnerabilities_cve_lookup('cve-2024-3400')
        self.assertEqual(result['identifier'], 'CVE-2024-3400')
        self.assertEqual(result['existing_id'], 42)
        self.assertEqual(result['fields']['kind'], 'cve')
        self.assertIn('values', result['enrichment']['cve_org'])


class TestCveOrgClient(TestCase):

    def _response(self, status, payload):
        response = MagicMock()
        response.status_code = status
        response.iter_content.return_value = [json.dumps(payload).encode()]
        response.__enter__.return_value = response
        return response

    def test_record_should_be_fetched_from_the_base_url(self):
        with patch('requests.Session.get', return_value=self._response(200, _record('CVE-2024-3400'))) as get:
            record = cve_org_fetch('https://cve.example/api/cve/', 'CVE-2024-3400', timeout=3)
        self.assertEqual(record['cveMetadata']['cveId'], 'CVE-2024-3400')
        self.assertEqual(get.call_args.args[0], 'https://cve.example/api/cve/CVE-2024-3400')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_unknown_record_should_raise_not_found(self):
        payload = {'error': 'CVE_RECORD_DNE', 'message': 'The cve record for the cve id does not exist.'}
        with patch('requests.Session.get', return_value=self._response(404, payload)):
            with self.assertRaises(CveOrgError) as raised:
                cve_org_fetch('https://cve.example/api/cve', 'CVE-2024-99999')
        self.assertTrue(raised.exception.not_found)

    def test_network_failure_should_raise(self):
        with patch('requests.Session.get', side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(CveOrgError) as raised:
                cve_org_fetch('https://cve.example/api/cve', 'CVE-2024-3400')
        self.assertFalse(raised.exception.not_found)

    def test_malformed_identifier_should_never_reach_the_network(self):
        with patch('requests.Session.get') as get:
            with self.assertRaises(CveOrgError):
                cve_org_fetch('https://cve.example/api/cve', '../admin')
        get.assert_not_called()
