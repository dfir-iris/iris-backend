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

"""Integration tests for the vulnerability catalogue (`/api/v2/manage/vulnerabilities`).

The catalogue is instance-wide and is not emptied by `clear_database`, so
every test works on freshly drawn identifiers and deletes what it created.
"""

import re
from unittest import TestCase
from uuid import uuid4

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY
from iris import IRIS_PERMISSION_VULNERABILITIES_CREATE
from iris import IRIS_PERMISSION_VULNERABILITIES_READ
from iris import IRIS_PERMISSION_VULNERABILITIES_WRITE

_VULNERABILITIES_URL = '/api/v2/manage/vulnerabilities'
_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789
_VULNERABILITIES_READ_CREATE = IRIS_PERMISSION_VULNERABILITIES_READ | IRIS_PERMISSION_VULNERABILITIES_CREATE
_PRIVATE_IDENTIFIER_RE = re.compile(r'^IRIS-VULN-\d{4}-\d{4,}$')
_V31_CRITICAL = 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H'


def _random_cve():
    return f'CVE-2099-{uuid4().int % 90000000 + 10000000}'


def _random_ghsa():
    digits = uuid4().hex
    return f'GHSA-{digits[0:4]}-{digits[4:8]}-{digits[8:12]}'


class TestsRestVulnerabilities(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        self._vulnerability_identifiers = []

    def tearDown(self):
        # Deleting the cases removes their findings, which unblocks the
        # deletion of the catalogue entries.
        self._subject.clear_database()
        for identifier in self._vulnerability_identifiers:
            self._subject.delete(f'{_VULNERABILITIES_URL}/{identifier}')

    def _track(self, response):
        if response.status_code == 201:
            self._vulnerability_identifiers.append(response.json()['vulnerability_id'])
        return response

    def _create(self, actor=None, **body):
        actor = actor or self._subject
        if 'identifier' not in body and not body.get('is_private'):
            body['identifier'] = _random_cve()
        body.setdefault('title', 'Test vulnerability')
        return self._track(actor.create(_VULNERABILITIES_URL, body))

    def _create_identifier(self, **body):
        return self._create(**body).json()['vulnerability_id']

    def _contributor(self):
        return self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)

    def _case_with_finding(self, vulnerability_identifier, asset_name='DC01'):
        case_identifier = self._subject.create_dummy_case()
        asset_identifier = self._subject.create(
            f'/api/v2/cases/{case_identifier}/assets', {'asset_type_id': 1, 'asset_name': asset_name}
        ).json()['asset_id']
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/vulnerabilities', {
            'asset_ids': [asset_identifier], 'vulnerability_id': vulnerability_identifier,
            'remediation_status': 'affected',
        })
        self.assertEqual(201, response.status_code, response.text)
        return case_identifier

    # -- creation ----------------------------------------------------------

    def test_create_vulnerability_should_return_201(self):
        response = self._create()
        self.assertEqual(201, response.status_code)

    def test_create_vulnerability_should_normalise_the_identifier(self):
        identifier = _random_cve()
        response = self._create(identifier=identifier.lower()).json()
        self.assertEqual(identifier, response['identifier'])
        self.assertFalse(response['is_private'])
        self.assertEqual('cve', response['kind'])
        self.assertEqual('manual', response['source'])

    def test_create_vulnerability_should_normalise_a_ghsa_identifier(self):
        identifier = _random_ghsa()
        response = self._create(identifier=identifier.upper()).json()
        self.assertEqual(identifier, response['identifier'])
        self.assertEqual('advisory', response['kind'])

    def test_create_vulnerability_should_compute_the_cvss_score_and_severity(self):
        response = self._create(cvss_vector=_V31_CRITICAL).json()
        self.assertEqual('3.1', response['cvss_version'])
        self.assertEqual(9.8, response['cvss_score'])
        self.assertEqual('critical', response['severity'])

    def test_create_vulnerability_should_return_400_when_the_cvss_vector_is_invalid(self):
        response = self._create(cvss_vector='CVSS:3.1/AV:X')
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_the_identifier_is_invalid(self):
        response = self._create(identifier='CVE-12-1')
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_the_title_is_missing(self):
        response = self._subject.create(_VULNERABILITIES_URL, {'identifier': _random_cve()})
        self._track(response)
        self.assertEqual(400, response.status_code)

    def test_create_private_vulnerability_should_allocate_an_iris_vuln_identifier(self):
        response = self._create(is_private=True)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertTrue(body['is_private'])
        self.assertRegex(body['identifier'], _PRIVATE_IDENTIFIER_RE)
        self.assertEqual('other', body['kind'])

    def test_create_private_vulnerabilities_should_allocate_distinct_identifiers(self):
        first = self._create(is_private=True).json()['identifier']
        second = self._create(is_private=True).json()['identifier']
        self.assertNotEqual(first, second)

    def test_create_private_vulnerability_should_return_400_when_an_identifier_is_supplied(self):
        response = self._create(is_private=True, identifier=_random_cve())
        self.assertEqual(400, response.status_code)

    def test_create_public_vulnerability_should_return_400_with_the_private_prefix(self):
        response = self._create(identifier='IRIS-VULN-2026-9999')
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_the_identifier_already_exists(self):
        identifier = _random_cve()
        self._create(identifier=identifier)
        response = self._create(identifier=identifier.lower())
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_the_identifier_is_an_alias(self):
        alias = _random_ghsa()
        self._create(aliases=[alias])
        response = self._create(identifier=alias)
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_an_alias_belongs_to_another_entry(self):
        identifier = _random_cve()
        self._create(identifier=identifier)
        response = self._create(aliases=[identifier])
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_return_400_when_an_alias_is_private(self):
        response = self._create(aliases=['IRIS-VULN-2026-0001'])
        self.assertEqual(400, response.status_code)

    def test_create_vulnerability_should_be_allowed_with_the_vulnerabilities_read_and_create_permissions(self):
        user = self._contributor()
        response = self._create(actor=user)
        self.assertEqual(201, response.status_code)
        self.assertEqual(user.get_identifier(), response.json()['created_by_id'])

    def test_create_vulnerability_should_return_403_without_the_vulnerabilities_create_permission(self):
        user = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_VULNERABILITIES_READ)
        response = self._create(actor=user)
        self.assertEqual(403, response.status_code)

    def test_create_vulnerability_should_return_403_without_the_vulnerabilities_read_permission(self):
        user = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_VULNERABILITIES_CREATE)
        response = self._create(actor=user)
        self.assertEqual(403, response.status_code)

    # -- read permission ---------------------------------------------------

    def test_search_vulnerabilities_should_return_403_without_the_vulnerabilities_read_permission(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_CREATE)
        response = user.get(_VULNERABILITIES_URL)
        self.assertEqual(403, response.status_code)

    def test_search_vulnerabilities_should_return_200_with_the_vulnerabilities_read_permission(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_READ)
        response = user.get(_VULNERABILITIES_URL)
        self.assertEqual(200, response.status_code)

    def test_lookup_vulnerability_should_return_403_without_the_vulnerabilities_read_permission(self):
        identifier = _random_cve()
        self._create(identifier=identifier)
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_CREATE)
        response = user.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': identifier})
        self.assertEqual(403, response.status_code)

    def test_get_vulnerability_should_return_403_without_the_vulnerabilities_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_CREATE)
        response = user.get(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_vulnerability_should_return_200_with_the_vulnerabilities_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_READ)
        response = user.get(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_vulnerability_exposure_should_return_403_without_the_vulnerabilities_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_CREATE)
        response = user.get(f'{_VULNERABILITIES_URL}/{identifier}/exposure')
        self.assertEqual(403, response.status_code)

    # -- read --------------------------------------------------------------

    def test_get_vulnerability_should_return_200(self):
        identifier = self._create_identifier()
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_vulnerability_should_return_404_when_it_does_not_exist(self):
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_lookup_vulnerability_should_find_the_entry_by_identifier(self):
        identifier = _random_cve()
        vulnerability_identifier = self._create_identifier(identifier=identifier)
        response = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': identifier.lower()})
        self.assertEqual(200, response.status_code)
        self.assertEqual(vulnerability_identifier, response.json()['vulnerability_id'])

    def test_lookup_vulnerability_should_find_the_entry_by_alias(self):
        alias = _random_ghsa()
        vulnerability_identifier = self._create_identifier(aliases=[alias])
        response = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': alias.upper()}).json()
        self.assertEqual(vulnerability_identifier, response['vulnerability_id'])
        self.assertEqual([alias], response['aliases'])

    def test_lookup_vulnerability_should_return_null_when_unknown(self):
        response = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': _random_cve()})
        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.json())

    def test_lookup_vulnerability_should_return_400_without_identifier(self):
        response = self._subject.get(f'{_VULNERABILITIES_URL}/lookup')
        self.assertEqual(400, response.status_code)

    def test_lookup_vulnerability_should_return_400_when_the_identifier_is_invalid(self):
        response = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': 'not valid!'})
        self.assertEqual(400, response.status_code)

    # -- search ------------------------------------------------------------

    def test_search_vulnerabilities_should_filter_on_search(self):
        marker = uuid4().hex
        identifier = self._create_identifier(title=f'Needle {marker}')
        self._create_identifier(title='Haystack')
        response = self._subject.get(_VULNERABILITIES_URL, {'search': marker}).json()
        self.assertEqual(1, response['total'])
        self.assertEqual(identifier, response['data'][0]['vulnerability_id'])

    def test_search_vulnerabilities_should_filter_on_severity(self):
        marker = uuid4().hex
        critical = self._create_identifier(title=f'{marker} a', cvss_vector=_V31_CRITICAL)
        self._create_identifier(title=f'{marker} b', severity='low')
        response = self._subject.get(_VULNERABILITIES_URL, {'search': marker, 'severity': 'critical'}).json()
        self.assertEqual([critical], [entry['vulnerability_id'] for entry in response['data']])

    def test_search_vulnerabilities_should_filter_on_kev(self):
        marker = uuid4().hex
        kev = self._create_identifier(title=f'{marker} a', kev=True)
        self._create_identifier(title=f'{marker} b')
        response = self._subject.get(_VULNERABILITIES_URL, {'search': marker, 'kev': 'true'}).json()
        self.assertEqual([kev], [entry['vulnerability_id'] for entry in response['data']])

    def test_search_vulnerabilities_should_filter_on_private(self):
        marker = uuid4().hex
        private = self._create_identifier(title=f'{marker} a', is_private=True)
        self._create_identifier(title=f'{marker} b')
        response = self._subject.get(_VULNERABILITIES_URL, {'search': marker, 'private': 'true'}).json()
        self.assertEqual([private], [entry['vulnerability_id'] for entry in response['data']])

    def test_search_vulnerabilities_should_filter_on_affected(self):
        marker = uuid4().hex
        affected = self._create_identifier(title=f'{marker} a')
        self._create_identifier(title=f'{marker} b')
        self._case_with_finding(affected)
        response = self._subject.get(_VULNERABILITIES_URL, {'search': marker, 'affected': 'true'}).json()
        self.assertEqual([affected], [entry['vulnerability_id'] for entry in response['data']])

    def test_search_vulnerabilities_should_match_an_alias(self):
        alias = _random_ghsa()
        identifier = self._create_identifier(aliases=[alias])
        response = self._subject.get(_VULNERABILITIES_URL, {'search': alias}).json()
        self.assertEqual([identifier], [entry['vulnerability_id'] for entry in response['data']])

    def test_search_vulnerabilities_should_return_400_when_a_filter_is_invalid(self):
        for parameters in ({'severity': 'apocalyptic'}, {'kind': 'bogus'}, {'kev': 'maybe'},
                           {'order_by': 'password'}, {'sort_dir': 'sideways'}):
            response = self._subject.get(_VULNERABILITIES_URL, parameters)
            self.assertEqual(400, response.status_code, parameters)

    # -- update ------------------------------------------------------------

    def test_update_vulnerability_should_return_200_for_its_creator(self):
        user = self._contributor()
        identifier = self._create(actor=user).json()['vulnerability_id']
        response = user.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Renamed'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('Renamed', response.json()['title'])

    def test_update_vulnerability_should_return_403_for_its_creator_without_the_vulnerabilities_create_permission(self):
        user = self._contributor()
        identifier = self._create(actor=user).json()['vulnerability_id']
        read_only_group = self._subject.create_dummy_group(IRIS_PERMISSION_VULNERABILITIES_READ)
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update',
                             {'groups_membership': [read_only_group]})
        response = user.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Renamed'})
        self.assertEqual(403, response.status_code)

    def test_update_vulnerability_should_return_403_for_another_user(self):
        creator = self._contributor()
        identifier = self._create(actor=creator).json()['vulnerability_id']
        other = self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)
        response = other.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Hijacked'})
        self.assertEqual(403, response.status_code)

    def test_update_vulnerability_should_not_persist_a_refused_change(self):
        creator = self._contributor()
        identifier = self._create(actor=creator, title='Original').json()['vulnerability_id']
        other = self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)
        other.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Hijacked'})
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{identifier}').json()
        self.assertEqual('Original', response['title'])

    def test_update_vulnerability_should_return_200_with_the_vulnerabilities_write_permission(self):
        creator = self._contributor()
        identifier = self._create(actor=creator).json()['vulnerability_id']
        curator = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_VULNERABILITIES_WRITE | IRIS_PERMISSION_VULNERABILITIES_READ)
        response = curator.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Curated'})
        self.assertEqual(200, response.status_code)

    def test_update_vulnerability_should_return_403_with_the_vulnerabilities_write_permission_but_without_read(self):
        identifier = self._create_identifier()
        curator = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_VULNERABILITIES_WRITE)
        response = curator.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Curated'})
        self.assertEqual(403, response.status_code)

    def test_update_vulnerability_should_return_200_for_an_administrator(self):
        creator = self._contributor()
        identifier = self._create(actor=creator).json()['vulnerability_id']
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {'title': 'Administered'})
        self.assertEqual(200, response.status_code)

    def test_update_vulnerability_should_return_404_when_it_does_not_exist(self):
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}',
                                        {'title': 'x'})
        self.assertEqual(404, response.status_code)

    def test_update_vulnerability_should_recompute_the_score_when_the_vector_changes(self):
        identifier = self._create_identifier(cvss_vector=_V31_CRITICAL)
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {
            'cvss_vector': 'CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N'}).json()
        self.assertEqual(6.1, response['cvss_score'])
        self.assertEqual('medium', response['severity'])

    def test_update_vulnerability_should_return_400_when_making_it_private(self):
        identifier = self._create_identifier()
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {'is_private': True})
        self.assertEqual(400, response.status_code)

    def test_update_vulnerability_should_return_400_when_renaming_a_private_entry(self):
        identifier = self._create_identifier(is_private=True)
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {'identifier': _random_cve()})
        self.assertEqual(400, response.status_code)

    def test_update_vulnerability_should_return_400_when_renamed_onto_another_entry(self):
        taken = _random_cve()
        self._create(identifier=taken)
        identifier = self._create_identifier()
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {'identifier': taken})
        self.assertEqual(400, response.status_code)

    def test_update_vulnerability_should_replace_the_aliases(self):
        first_alias = _random_ghsa()
        second_alias = _random_ghsa()
        identifier = self._create_identifier(aliases=[first_alias])
        response = self._subject.update(f'{_VULNERABILITIES_URL}/{identifier}', {'aliases': [second_alias]}).json()
        self.assertEqual([second_alias], response['aliases'])
        lookup = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': first_alias}).json()
        self.assertIsNone(lookup)

    # -- delete ------------------------------------------------------------

    def test_delete_vulnerability_should_return_204(self):
        identifier = self._create_identifier()
        response = self._subject.delete(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_get_vulnerability_should_return_404_after_it_was_deleted(self):
        identifier = self._create_identifier()
        self._subject.delete(f'{_VULNERABILITIES_URL}/{identifier}')
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_vulnerability_should_return_404_when_it_does_not_exist(self):
        response = self._subject.delete(f'{_VULNERABILITIES_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_vulnerability_should_return_403_for_its_creator(self):
        user = self._contributor()
        identifier = self._create(actor=user).json()['vulnerability_id']
        response = user.delete(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_delete_vulnerability_should_return_403_with_the_vulnerabilities_write_permission(self):
        identifier = self._create_identifier()
        curator = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_VULNERABILITIES_WRITE | _VULNERABILITIES_READ_CREATE)
        response = curator.delete(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_delete_vulnerability_should_return_400_when_findings_use_it(self):
        identifier = self._create_identifier()
        self._case_with_finding(identifier)
        response = self._subject.delete(f'{_VULNERABILITIES_URL}/{identifier}')
        self.assertEqual(400, response.status_code)
        self.assertEqual(1, response.json()['data']['findings'])

    # -- merge -------------------------------------------------------------

    def test_merge_vulnerability_should_move_the_findings_to_the_target(self):
        source_identifier = _random_cve()
        source = self._create_identifier(identifier=source_identifier)
        target = self._create_identifier()
        case_identifier = self._case_with_finding(source)

        response = self._subject.create(f'{_VULNERABILITIES_URL}/{source}/merge', {'target_id': target})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(target, body['vulnerability_id'])
        self.assertEqual(1, body['merge']['moved'])
        self.assertEqual(0, body['merge']['dropped'])
        self.assertIn(source_identifier, body['aliases'])

        findings = self._subject.get(f'/api/v2/cases/{case_identifier}/vulnerabilities').json()['findings']
        self.assertEqual([target], [finding['vulnerability']['vulnerability_id'] for finding in findings])

    def test_merge_vulnerability_should_delete_the_source(self):
        source = self._create_identifier()
        target = self._create_identifier()
        self._subject.create(f'{_VULNERABILITIES_URL}/{source}/merge', {'target_id': target})
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{source}')
        self.assertEqual(404, response.status_code)

    def test_merge_vulnerability_should_make_the_source_identifier_resolve_to_the_target(self):
        source_identifier = _random_cve()
        source = self._create_identifier(identifier=source_identifier)
        target = self._create_identifier()
        self._subject.create(f'{_VULNERABILITIES_URL}/{source}/merge', {'target_id': target})
        lookup = self._subject.get(f'{_VULNERABILITIES_URL}/lookup', {'identifier': source_identifier}).json()
        self.assertEqual(target, lookup['vulnerability_id'])

    def test_merge_vulnerability_should_drop_a_finding_duplicated_on_the_same_asset(self):
        source = self._create_identifier()
        target = self._create_identifier()
        case_identifier = self._subject.create_dummy_case()
        asset_identifier = self._subject.create(
            f'/api/v2/cases/{case_identifier}/assets', {'asset_type_id': 1, 'asset_name': 'DC01'}
        ).json()['asset_id']
        for vulnerability_identifier in (source, target):
            self._subject.create(f'/api/v2/cases/{case_identifier}/vulnerabilities',
                                 {'asset_ids': [asset_identifier], 'vulnerability_id': vulnerability_identifier})
        response = self._subject.create(f'{_VULNERABILITIES_URL}/{source}/merge', {'target_id': target}).json()
        self.assertEqual(0, response['merge']['moved'])
        self.assertEqual(1, response['merge']['dropped'])

    def test_merge_vulnerability_should_return_400_when_merged_into_itself(self):
        identifier = self._create_identifier()
        response = self._subject.create(f'{_VULNERABILITIES_URL}/{identifier}/merge', {'target_id': identifier})
        self.assertEqual(400, response.status_code)

    def test_merge_vulnerability_should_return_400_when_the_target_does_not_exist(self):
        identifier = self._create_identifier()
        response = self._subject.create(f'{_VULNERABILITIES_URL}/{identifier}/merge',
                                        {'target_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT})
        self.assertEqual(400, response.status_code)

    def test_merge_vulnerability_should_return_404_when_the_source_does_not_exist(self):
        target = self._create_identifier()
        response = self._subject.create(f'{_VULNERABILITIES_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/merge',
                                        {'target_id': target})
        self.assertEqual(404, response.status_code)

    def test_merge_vulnerability_should_return_403_for_a_non_administrator(self):
        source = self._create_identifier()
        target = self._create_identifier()
        curator = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_VULNERABILITIES_WRITE | _VULNERABILITIES_READ_CREATE)
        response = curator.create(f'{_VULNERABILITIES_URL}/{source}/merge', {'target_id': target})
        self.assertEqual(403, response.status_code)

    # -- exposure ----------------------------------------------------------

    def test_get_vulnerability_exposure_should_list_every_case_for_an_administrator(self):
        identifier = self._create_identifier()
        first_case = self._case_with_finding(identifier)
        second_case = self._case_with_finding(identifier)
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{identifier}/exposure')
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual({first_case, second_case}, {case['case_id'] for case in body['cases']})
        self.assertEqual(2, body['totals']['cases'])
        self.assertEqual(2, body['totals']['open'])

    def test_get_vulnerability_exposure_should_only_list_readable_cases(self):
        identifier = self._create_identifier()
        readable_case = self._case_with_finding(identifier)
        self._case_with_finding(identifier, asset_name='HIDDEN-HOST')
        user = self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)
        self._subject.grant_case_access(user, readable_case, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)

        response = user.get(f'{_VULNERABILITIES_URL}/{identifier}/exposure')
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([readable_case], [case['case_id'] for case in body['cases']])
        self.assertEqual(1, body['totals']['cases'])
        self.assertEqual([], body['registry'])

    def test_get_vulnerability_should_count_only_readable_cases(self):
        identifier = self._create_identifier()
        readable_case = self._case_with_finding(identifier)
        self._case_with_finding(identifier)
        user = self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)
        self._subject.grant_case_access(user, readable_case, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)

        counts = user.get(f'{_VULNERABILITIES_URL}/{identifier}').json()['counts']
        self.assertEqual(1, counts['cases'])
        self.assertEqual(1, counts['findings'])
        admin_counts = self._subject.get(f'{_VULNERABILITIES_URL}/{identifier}').json()['counts']
        self.assertEqual(2, admin_counts['cases'])

    def test_get_vulnerability_exposure_should_be_empty_without_case_access(self):
        identifier = self._create_identifier()
        self._case_with_finding(identifier)
        user = self._subject.create_dummy_user(permissions=_VULNERABILITIES_READ_CREATE)
        body = user.get(f'{_VULNERABILITIES_URL}/{identifier}/exposure').json()
        self.assertEqual([], body['cases'])
        self.assertEqual(0, body['totals']['case_findings'])

    def test_get_vulnerability_exposure_should_return_404_when_it_does_not_exist(self):
        response = self._subject.get(f'{_VULNERABILITIES_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/exposure')
        self.assertEqual(404, response.status_code)
