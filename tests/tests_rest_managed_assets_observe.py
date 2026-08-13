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

"""Automatic registration of case and alert assets into the registry.

The registry is fed by ingest hooks rather than by an analyst typing
hostnames in twice. Those hooks are deliberately one-directional: they
create a row that is missing and never touch a row that exists, so an
alert arriving at 03:00 cannot overwrite the criticality somebody set
during the investigation, and `updated_at` never moves on machine
activity (which would make it a timing side channel for invisible
cases).

Because they only ever add, the registry can drift in exactly one
direction — a missing row — and `POST /reconcile` is the repair. Several
tests here simulate drift by deleting the registry row and asserting the
hook or the reconcile puts it back.
"""

from unittest import TestCase

from iris import Iris
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER

_MANAGED_ASSETS_URL = '/api/v2/manage/managed-assets'
_ASSET_NAME = 'SRV-DC01'
_DEFAULT_ASSET_TYPE_IDENTIFIER = 1
_OTHER_ASSET_TYPE_IDENTIFIER = 2


class TestsRestManagedAssetsObserve(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # Registry rows of the initial customer outlive `clear_database`
        # (that customer is never deleted, so nothing cascades).
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 100}).json()
        for asset in response.get('data', []):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')
        self._subject.clear_database()

    # -- fixtures ----------------------------------------------------------

    def _add_case_asset(self, case_identifier, name=_ASSET_NAME, **overrides):
        body = {'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER, 'asset_name': name}
        body.update(overrides)
        return self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()

    def _add_alert_asset(self, customer_identifier, name=_ASSET_NAME):
        body = {
            'alert_title': 'alert title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': customer_identifier,
            'alert_assets': [{'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
                              'asset_name': name}],
        }
        return self._subject.create('/api/v2/alerts', body).json()

    def _registry(self, name=_ASSET_NAME, **filters):
        query = {'search': name, 'per_page': 100}
        query.update(filters)
        response = self._subject.get(_MANAGED_ASSETS_URL, query).json()
        return [asset for asset in response['data'] if asset['name'].lower() == name.lower()]

    def _registry_entry(self, name=_ASSET_NAME):
        matches = self._registry(name)
        if not matches:
            self.fail(f'"{name}" was never registered')
        return matches[0]

    def _delete_registry_entries(self, name=_ASSET_NAME):
        """Simulate the one drift the registry can suffer: a missing row."""
        for asset in self._registry(name):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

    # -- case assets -------------------------------------------------------

    def test_create_case_asset_should_register_the_asset(self):
        case_identifier = self._subject.create_dummy_case()

        self._add_case_asset(case_identifier)

        self.assertEqual(1, len(self._registry()))

    def test_create_case_asset_should_register_the_asset_with_the_observed_source(self):
        case_identifier = self._subject.create_dummy_case()

        self._add_case_asset(case_identifier)

        self.assertEqual('observed', self._registry_entry()['source'])

    def test_create_case_asset_should_register_the_asset_for_the_case_customer(self):
        case_identifier = self._subject.create_dummy_case()

        self._add_case_asset(case_identifier)

        self.assertEqual(IRIS_INITIAL_CUSTOMER_IDENTIFIER, self._registry_entry()['client_id'])

    def test_create_case_asset_should_not_duplicate_an_existing_entry(self):
        first_case = self._subject.create_dummy_case()
        second_case = self._subject.create_dummy_case()
        self._add_case_asset(first_case)

        self._add_case_asset(second_case)

        self.assertEqual(1, len(self._registry()))

    def test_create_case_asset_should_not_duplicate_when_only_the_case_differs(self):
        # The whole point of the registry: two `case_assets` rows, one
        # inventory entry, even though the names differ only in case.
        first_case = self._subject.create_dummy_case()
        second_case = self._subject.create_dummy_case()
        self._add_case_asset(first_case, name=_ASSET_NAME)

        self._add_case_asset(second_case, name=_ASSET_NAME.lower())

        self.assertEqual(1, len(self._registry()))

    def test_create_case_asset_should_register_a_separate_entry_for_another_asset_type(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)

        self._add_case_asset(case_identifier, asset_type_id=_OTHER_ASSET_TYPE_IDENTIFIER)

        self.assertEqual(2, len(self._registry()))

    def test_create_case_asset_should_register_a_separate_entry_for_another_customer(self):
        other_customer = self._subject.create_dummy_customer()
        self._add_case_asset(self._subject.create_dummy_case())

        self._add_case_asset(self._subject.create_dummy_case(other_customer))

        self.assertEqual(2, len(self._registry()))

    def test_create_case_asset_should_not_overwrite_curated_metadata(self):
        # An ingest at 03:00 must not undo what an analyst recorded.
        created = self._subject.create(_MANAGED_ASSETS_URL, {
            'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER,
            'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
            'name': _ASSET_NAME,
            'criticality': 'critical',
            'owner': 'IT Ops',
        }).json()

        self._add_case_asset(self._subject.create_dummy_case())

        response = self._subject.get(
            f'{_MANAGED_ASSETS_URL}/{created["managed_asset_id"]}').json()
        self.assertEqual(('critical', 'IT Ops', 'manual'),
                         (response['criticality'], response['owner'], response['source']))

    def test_update_case_asset_should_register_the_new_name(self):
        # A rename is a new identity. The old row keeps its metadata and
        # its sighting count simply drops as observations stop matching.
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)

        self._subject.update(
            f'/api/v2/cases/{case_identifier}/assets/{asset["asset_id"]}',
            {'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER, 'asset_name': 'SRV-DC02'},
        )

        self.assertEqual((1, 1), (len(self._registry()), len(self._registry('SRV-DC02'))))

    def test_delete_case_asset_should_keep_the_registry_entry(self):
        # Deleting the metadata because the last case closed would be data
        # loss; only the derived counts follow the observations.
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)
        identifier = self._registry_entry()['managed_asset_id']
        self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'owner': 'IT Ops'})

        self._subject.delete(f'/api/v2/cases/{case_identifier}/assets/{asset["asset_id"]}')

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual('IT Ops', response['owner'])

    def test_upload_case_assets_should_register_the_uploaded_assets(self):
        # The legacy CSV upload calls `create_asset` directly rather than
        # going through `business.assets`, so it carries its own hook.
        case_identifier = self._subject.create_dummy_case()
        body = {'CSVData': 'asset_name,asset_type_name,asset_description,asset_ip,'
                           'asset_domain,asset_tags\n'
                           f'{_ASSET_NAME},Windows - Server,,,,'}

        self._subject.create(f'/case/assets/upload?cid={case_identifier}', body)

        self.assertEqual(1, len(self._registry()))

    # -- alerts ------------------------------------------------------------

    def test_create_alert_should_register_the_alert_assets(self):
        self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)

        self.assertEqual(1, len(self._registry()))

    def test_create_alert_should_register_the_asset_for_the_alert_customer(self):
        other_customer = self._subject.create_dummy_customer()

        self._add_alert_asset(other_customer)

        self.assertEqual(other_customer, self._registry_entry()['client_id'])

    def test_escalate_alert_should_register_the_case_assets(self):
        alert = self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        self._delete_registry_entries()

        self._subject.create(f'/api/v2/alerts/escalate/{alert["alert_id"]}', {
            'iocs_import_list': [],
            'assets_import_list': [asset['asset_uuid'] for asset in alert['assets']],
            'note': '',
            'case_title': 'escalated case',
            'case_tags': '',
            'case_template_id': '',
        })

        self.assertEqual(1, len(self._registry()))

    def test_merge_alert_should_register_the_case_assets(self):
        case_identifier = self._subject.create_dummy_case()
        alert = self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        self._delete_registry_entries()

        self._subject.create(f'/api/v2/alerts/merge/{alert["alert_id"]}', {
            'target_case_id': case_identifier,
            'iocs_import_list': [],
            'assets_import_list': [asset['asset_uuid'] for asset in alert['assets']],
        })

        self.assertEqual(1, len(self._registry()))

    # -- reconcile ---------------------------------------------------------

    def test_reconcile_should_register_an_asset_missing_from_the_registry(self):
        self._add_case_asset(self._subject.create_dummy_case())
        self._delete_registry_entries()

        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        self.assertEqual(1, len(self._registry()))

    def test_reconcile_should_report_the_number_of_registered_assets(self):
        self._add_case_asset(self._subject.create_dummy_case())
        self._delete_registry_entries()

        response = self._subject.create(
            f'{_MANAGED_ASSETS_URL}/reconcile',
            {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER}).json()

        self.assertEqual(1, response['created'])

    def test_reconcile_should_register_an_alert_asset_missing_from_the_registry(self):
        self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        self._delete_registry_entries()

        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        self.assertEqual(1, len(self._registry()))

    def test_reconcile_should_be_idempotent(self):
        self._add_case_asset(self._subject.create_dummy_case())
        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        response = self._subject.create(
            f'{_MANAGED_ASSETS_URL}/reconcile',
            {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER}).json()

        self.assertEqual(0, response['created'])

    def test_reconcile_should_only_touch_the_requested_customer(self):
        other_customer = self._subject.create_dummy_customer()
        self._add_case_asset(self._subject.create_dummy_case(other_customer))
        self._delete_registry_entries()

        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        self.assertEqual([], self._registry())

    def test_reconcile_should_not_overwrite_curated_metadata(self):
        created = self._subject.create(_MANAGED_ASSETS_URL, {
            'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER,
            'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
            'name': _ASSET_NAME,
            'criticality': 'critical',
        }).json()
        self._add_case_asset(self._subject.create_dummy_case())

        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        response = self._subject.get(
            f'{_MANAGED_ASSETS_URL}/{created["managed_asset_id"]}').json()
        self.assertEqual('critical', response['criticality'])

    # -- observations are not audit events ---------------------------------

    def test_observe_should_not_write_an_audit_entry(self):
        # Machine-created rows would be the highest-volume writes in the
        # system and would bury the human trail the audit log exists for.
        self._add_case_asset(self._subject.create_dummy_case())
        identifier = self._registry_entry()['managed_asset_id']

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/audit').json()

        self.assertEqual(0, response['total'])

    def test_reconcile_should_not_write_an_audit_entry(self):
        self._add_case_asset(self._subject.create_dummy_case())
        self._delete_registry_entries()
        self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                             {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})
        identifier = self._registry_entry()['managed_asset_id']

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/audit').json()

        self.assertEqual(0, response['total'])
