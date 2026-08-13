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

"""The change log of the asset registry.

Every human write is recorded with the field-level diff and the acting
account. Automatic registrations are deliberately not: they would be the
highest-volume writes in the system and would bury the trail the log
exists for, so they go to the activity log instead (asserted in
`tests_rest_managed_assets_observe.py`).

The log is append-only and outlives what it describes. A delete entry
keeps the asset name after the asset is gone, and every entry keeps the
acting login after the account is gone — which is why the per-asset
endpoint cannot be the only way to read it: the asset id is nulled on
delete, so the deletion of an asset is readable only from the
registry-wide log.

The log accumulates across tests (there is no way to delete an entry,
which is the point), so assertions here pick their own entries out by a
per-test asset name rather than counting the whole table.
"""

from unittest import TestCase
from uuid import uuid4

from iris import Iris
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER
from iris import IRIS_PERMISSION_ASSET_MANAGER_READ
from iris import IRIS_PERMISSION_ASSET_MANAGER_WRITE

_MANAGED_ASSETS_URL = '/api/v2/manage/managed-assets'
_AUDIT_LOG_URL = f'{_MANAGED_ASSETS_URL}/audit'
_DEFAULT_ASSET_TYPE_IDENTIFIER = 1
# Well above the number of entries any single test writes, so the ones it
# cares about are never pushed off the (most-recent-first) page.
_AUDIT_PAGE = {'per_page': 200}


class TestsRestManagedAssetsAudit(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        self._asset_name = f'SRV-{uuid4().hex[:12]}'

    def tearDown(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 100}).json()
        for asset in response.get('data', []):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')
        self._subject.clear_database()

    # -- fixtures ----------------------------------------------------------

    def _create(self, client_identifier=IRIS_INITIAL_CUSTOMER_IDENTIFIER, actor=None, **overrides):
        body = {
            'client_id': client_identifier,
            'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
            'name': self._asset_name,
        }
        body.update(overrides)
        return (actor or self._subject).create(_MANAGED_ASSETS_URL, body).json()

    def _audit(self, identifier):
        return self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/audit',
                                 _AUDIT_PAGE).json()['data']

    def _log_entries_for_this_asset(self, actor=None):
        """Registry-wide log entries naming this test's asset."""
        if actor is None:
            response = self._subject.get(_AUDIT_LOG_URL, _AUDIT_PAGE).json()
        else:
            response = actor.get(f'{_AUDIT_LOG_URL}?per_page=200').json()
        return [entry for entry in response['data']
                if entry['asset_name_snapshot'] == self._asset_name]

    def _writer(self, customers=(IRIS_INITIAL_CUSTOMER_IDENTIFIER,),
                permissions=IRIS_PERMISSION_ASSET_MANAGER_READ
                | IRIS_PERMISSION_ASSET_MANAGER_WRITE):
        user = self._subject.create_dummy_user(permissions=permissions)
        self._subject.create(
            f'/manage/users/{user.get_identifier()}/customers/update',
            {'customers_membership': list(customers)},
        )
        return user

    # -- writes are recorded -----------------------------------------------

    def test_create_managed_asset_should_write_a_create_entry(self):
        asset = self._create()

        entries = self._audit(asset['managed_asset_id'])

        self.assertEqual(['create'], [entry['action'] for entry in entries])

    def test_create_managed_asset_should_record_the_asset_name(self):
        asset = self._create()

        entries = self._audit(asset['managed_asset_id'])

        self.assertEqual(self._asset_name, entries[0]['asset_name_snapshot'])

    def test_create_managed_asset_should_record_the_acting_user(self):
        writer = self._writer()

        asset = self._create(actor=writer)

        entries = self._audit(asset['managed_asset_id'])
        self.assertEqual(writer.get_login(), entries[0]['user_login'])

    def test_create_managed_asset_should_record_the_api_source(self):
        asset = self._create()

        entries = self._audit(asset['managed_asset_id'])

        self.assertEqual('api', entries[0]['source'])

    def test_update_managed_asset_should_write_the_field_level_diff(self):
        asset = self._create()

        self._subject.update(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}',
                             {'criticality': 'critical'})

        entries = self._audit(asset['managed_asset_id'])
        self.assertEqual({'criticality': {'from': 'unknown', 'to': 'critical'}},
                         entries[0]['changes'])

    def test_update_managed_asset_should_write_an_update_entry(self):
        asset = self._create()

        self._subject.update(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}',
                             {'owner': 'IT Ops'})

        entries = self._audit(asset['managed_asset_id'])
        self.assertEqual(['update', 'create'], [entry['action'] for entry in entries])

    def test_update_managed_asset_should_not_write_an_entry_when_nothing_changed(self):
        # A log that records "somebody pressed save" is noise, and noise in
        # an audit trail is what makes the real entries hard to find.
        asset = self._create(criticality='high')

        self._subject.update(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}',
                             {'criticality': 'high'})

        entries = self._audit(asset['managed_asset_id'])
        self.assertEqual(['create'], [entry['action'] for entry in entries])

    def test_update_managed_asset_should_record_the_acting_user(self):
        asset = self._create()
        writer = self._writer()

        writer.update(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}',
                      {'owner': 'IT Ops'})

        entries = self._audit(asset['managed_asset_id'])
        self.assertEqual(writer.get_login(), entries[0]['user_login'])

    def test_get_audit_should_return_the_most_recent_entry_first(self):
        asset = self._create()
        self._subject.update(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}',
                             {'owner': 'IT Ops'})

        entries = self._audit(asset['managed_asset_id'])

        self.assertEqual('update', entries[0]['action'])

    # -- deletions are only readable from the registry-wide log -------------

    def test_delete_managed_asset_should_write_a_delete_entry(self):
        asset = self._create()

        self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

        actions = [entry['action'] for entry in self._log_entries_for_this_asset()]
        self.assertIn('delete', actions)

    def test_delete_managed_asset_should_keep_the_asset_name_in_the_log(self):
        # The FK is ON DELETE SET NULL, so the snapshot is the only thing
        # left that says *which* asset was deleted.
        asset = self._create()

        self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

        entries = self._log_entries_for_this_asset()
        self.assertEqual([self._asset_name] * len(entries),
                         [entry['asset_name_snapshot'] for entry in entries])

    def test_delete_managed_asset_should_null_the_asset_id_in_the_log(self):
        asset = self._create()

        self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

        entries = self._log_entries_for_this_asset()
        self.assertEqual([None] * len(entries),
                         [entry['managed_asset_id'] for entry in entries])

    def test_delete_managed_asset_should_record_the_acting_user(self):
        asset = self._create()
        writer = self._writer()

        writer.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

        entries = [entry for entry in self._log_entries_for_this_asset()
                   if entry['action'] == 'delete']
        self.assertEqual([writer.get_login()], [entry['user_login'] for entry in entries])

    def test_get_audit_should_return_404_after_the_asset_is_deleted(self):
        asset = self._create()
        self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}/audit')

        self.assertEqual(404, response.status_code)

    # -- tenancy -----------------------------------------------------------

    def test_get_audit_should_return_404_when_the_customer_is_inaccessible(self):
        # 404 rather than 403: the caller holds the permission, so a 403
        # would confirm the asset exists under a customer they cannot see.
        other_customer = self._subject.create_dummy_customer()
        asset = self._create(client_identifier=other_customer)

        reader = self._writer(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}/audit')

        self.assertEqual(404, response.status_code)

    def test_get_audit_log_should_not_return_entries_of_an_inaccessible_customer(self):
        other_customer = self._subject.create_dummy_customer()
        self._create(client_identifier=other_customer)

        reader = self._writer(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)

        self.assertEqual([], self._log_entries_for_this_asset(actor=reader))

    def test_get_audit_log_should_return_entries_of_an_accessible_customer(self):
        self._create()

        reader = self._writer(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)

        self.assertEqual(['create'],
                         [entry['action']
                          for entry in self._log_entries_for_this_asset(actor=reader)])

    def test_get_audit_log_should_return_an_empty_page_for_a_user_without_customers(self):
        self._create()

        reader = self._writer(customers=(), permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        response = reader.get(f'{_AUDIT_LOG_URL}?per_page=200').json()

        self.assertEqual(0, response['total'])

    def test_get_audit_log_should_intersect_the_client_id_filter_with_the_scope(self):
        # Asking for a customer you cannot read yields an empty page, never
        # that customer's history.
        other_customer = self._subject.create_dummy_customer()
        self._create(client_identifier=other_customer)

        reader = self._writer(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        response = reader.get(f'{_AUDIT_LOG_URL}?client_id={other_customer}').json()

        self.assertEqual(0, response['total'])

    def test_get_audit_log_should_return_400_for_a_non_integer_client_id(self):
        response = self._subject.get(_AUDIT_LOG_URL, {'client_id': 'abc'})

        self.assertEqual(400, response.status_code)

    # -- permissions -------------------------------------------------------

    def test_get_audit_should_return_403_without_the_read_permission(self):
        asset = self._create()

        user = self._subject.create_dummy_user()
        response = user.get(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}/audit')

        self.assertEqual(403, response.status_code)

    def test_get_audit_log_should_return_403_without_the_read_permission(self):
        user = self._subject.create_dummy_user()

        response = user.get(_AUDIT_LOG_URL)

        self.assertEqual(403, response.status_code)

    def test_get_audit_should_be_allowed_with_the_read_permission_only(self):
        asset = self._create()

        reader = self._writer(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}/audit')

        self.assertEqual(200, response.status_code)
