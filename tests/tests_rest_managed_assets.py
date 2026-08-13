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

"""CRUD, permissions, tenancy and parameter hardening for Manage > Assets.

The sighting-visibility rules live in `tests_rest_managed_assets_sightings`,
the ingest hooks in `..._observe`, the change log in `..._audit` and
import/export in `..._transfer`. This file covers the registry itself.
"""

from unittest import TestCase
from uuid import uuid4

from iris import Iris
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER
from iris import IRIS_PERMISSION_ASSET_MANAGER_READ
from iris import IRIS_PERMISSION_ASSET_MANAGER_WRITE

_MANAGED_ASSETS_URL = '/api/v2/manage/managed-assets'
_TAGS_URL = '/api/v2/tags'
_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789
_DEFAULT_ASSET_TYPE_IDENTIFIER = 1


def _asset_body(client_identifier, **overrides):
    body = {
        'client_id': client_identifier,
        'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
        'name': 'SRV-DC01',
        'criticality': 'high',
    }
    body.update(overrides)
    return body


class TestsRestManagedAssets(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # Registry rows cascade from the customer, but the initial
        # customer is never deleted — clear its assets explicitly.
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 1000}).json()
        for asset in response.get('data', []):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')
        self._subject.clear_database()

    def _create(self, **overrides):
        body = _asset_body(IRIS_INITIAL_CUSTOMER_IDENTIFIER, **overrides)
        return self._subject.create(_MANAGED_ASSETS_URL, body)

    def _create_identifier(self, **overrides):
        return self._create(**overrides).json()['managed_asset_id']

    # -- creation ----------------------------------------------------------

    def test_create_managed_asset_should_return_201(self):
        response = self._create()
        self.assertEqual(201, response.status_code)

    def test_create_managed_asset_should_return_the_normalized_name(self):
        response = self._create(name='  SRV   DC01 ').json()
        self.assertEqual('srv dc01', response['normalized_name'])

    def test_create_managed_asset_should_set_source_to_manual(self):
        response = self._create().json()
        self.assertEqual('manual', response['source'])

    def test_create_managed_asset_should_return_400_when_name_is_missing(self):
        body = _asset_body(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        del body['name']
        response = self._subject.create(_MANAGED_ASSETS_URL, body)
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_name_is_only_whitespace(self):
        response = self._create(name='   ')
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_asset_type_does_not_exist(self):
        response = self._create(asset_type_id=_IDENTIFIER_FOR_NONEXISTENT_OBJECT)
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_criticality_is_unknown_value(self):
        response = self._create(criticality='apocalyptic')
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_environment_is_unknown_value(self):
        response = self._create(environment='mainframe')
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_ignore_a_supplied_source(self):
        # `source` says how the row came to exist. A caller that could set
        # it could disguise a hand-made row as one observed from a case.
        response = self._create(source='observed').json()
        self.assertEqual('manual', response['source'])

    def test_create_managed_asset_should_ignore_a_supplied_normalized_name(self):
        response = self._create(name='SRV-DC01', normalized_name='something-else').json()
        self.assertEqual('srv-dc01', response['normalized_name'])

    # -- deduplication -----------------------------------------------------

    def test_create_managed_asset_should_return_400_when_identity_already_exists(self):
        self._create()
        response = self._create()
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_only_the_name_case_differs(self):
        self._create(name='SRV-DC01')
        response = self._create(name='srv-dc01')
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_400_when_only_the_whitespace_differs(self):
        self._create(name='SRV DC01')
        response = self._create(name='  SRV   DC01  ')
        self.assertEqual(400, response.status_code)

    def test_create_managed_asset_should_return_201_when_the_asset_type_differs(self):
        types = self._subject.get('/api/v2/manage/asset-types', {'per_page': 100}).json()['data']
        other_type = next(
            asset_type['asset_id'] for asset_type in types
            if asset_type['asset_id'] != _DEFAULT_ASSET_TYPE_IDENTIFIER
        )
        self._create()
        response = self._create(asset_type_id=other_type)
        self.assertEqual(201, response.status_code)

    def test_create_managed_asset_should_return_201_when_the_customer_differs(self):
        self._create()
        other_customer = self._subject.create_dummy_customer()
        response = self._subject.create(_MANAGED_ASSETS_URL, _asset_body(other_customer))
        self.assertEqual(201, response.status_code)

    def test_update_managed_asset_should_return_400_when_the_rename_collides(self):
        self._create(name='SRV-DC01')
        identifier = self._create_identifier(name='SRV-DC02')
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'name': 'srv-dc01'})
        self.assertEqual(400, response.status_code)

    def test_update_managed_asset_should_return_200_when_renamed_to_its_own_name_cased_differently(self):
        identifier = self._create_identifier(name='SRV-DC01')
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'name': 'srv-dc01'})
        self.assertEqual(200, response.status_code)

    # -- reads -------------------------------------------------------------

    def test_get_managed_asset_should_return_200(self):
        identifier = self._create_identifier()
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_managed_asset_should_return_404_when_it_does_not_exist(self):
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_get_managed_asset_should_return_the_nested_customer(self):
        identifier = self._create_identifier()
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(IRIS_INITIAL_CUSTOMER_IDENTIFIER, response['client']['customer_id'])

    def test_get_managed_asset_should_return_a_scope_flag(self):
        identifier = self._create_identifier()
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIn('restricted', response['scope'])

    def test_get_managed_assets_should_return_a_scope_flag(self):
        response = self._subject.get(_MANAGED_ASSETS_URL).json()
        self.assertIn('restricted', response['scope'])

    def test_get_managed_assets_should_return_the_created_asset(self):
        self._create(name='SRV-DC01')
        response = self._subject.get(_MANAGED_ASSETS_URL).json()
        self.assertIn('SRV-DC01', [asset['name'] for asset in response['data']])

    def test_get_managed_assets_should_return_null_timeline_event_count(self):
        # Present-and-null on the list, computed on the detail read: one
        # shape for both endpoints without paying for a query per row.
        self._create()
        response = self._subject.get(_MANAGED_ASSETS_URL).json()
        self.assertIsNone(response['data'][0]['timeline_event_count'])

    def test_get_managed_assets_should_filter_on_criticality(self):
        self._create(name='SRV-DC01', criticality='critical')
        self._create(name='SRV-DC02', criticality='low')
        response = self._subject.get(_MANAGED_ASSETS_URL, {'criticality': 'critical'}).json()
        self.assertEqual(['SRV-DC01'], [asset['name'] for asset in response['data']])

    def test_get_managed_assets_should_filter_on_search(self):
        self._create(name='SRV-DC01')
        self._create(name='WKS-0001')
        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': 'wks'}).json()
        self.assertEqual(['WKS-0001'], [asset['name'] for asset in response['data']])

    # -- updates and deletes ----------------------------------------------

    def test_update_managed_asset_should_return_200(self):
        identifier = self._create_identifier()
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}',
                                        {'criticality': 'critical'})
        self.assertEqual(200, response.status_code)

    def test_update_managed_asset_should_persist_the_change(self):
        identifier = self._create_identifier()
        self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'owner': 'IT Ops'})
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual('IT Ops', response['owner'])

    def test_update_managed_asset_should_return_404_when_it_does_not_exist(self):
        response = self._subject.update(
            f'{_MANAGED_ASSETS_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {'owner': 'IT Ops'}
        )
        self.assertEqual(404, response.status_code)

    def test_delete_managed_asset_should_return_204(self):
        identifier = self._create_identifier()
        response = self._subject.delete(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_delete_managed_asset_should_return_404_when_it_does_not_exist(self):
        response = self._subject.delete(
            f'{_MANAGED_ASSETS_URL}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}'
        )
        self.assertEqual(404, response.status_code)

    def test_get_managed_asset_should_return_404_after_it_was_deleted(self):
        identifier = self._create_identifier()
        self._subject.delete(f'{_MANAGED_ASSETS_URL}/{identifier}')
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(404, response.status_code)

    # -- mass assignment ---------------------------------------------------

    def test_update_managed_asset_should_not_change_the_customer(self):
        # `client_id` is part of the dedup identity. Moving it would
        # re-home the asset under another tenant and orphan its audit
        # trail, so the route strips it before the schema sees it.
        identifier = self._create_identifier()
        other_customer = self._subject.create_dummy_customer()
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}',
                                        {'client_id': other_customer}).json()
        self.assertEqual(IRIS_INITIAL_CUSTOMER_IDENTIFIER, response['client_id'])

    def test_update_managed_asset_should_not_change_the_asset_type(self):
        identifier = self._create_identifier()
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}',
                                        {'asset_type_id': 2}).json()
        self.assertEqual(_DEFAULT_ASSET_TYPE_IDENTIFIER, response['asset_type_id'])

    def test_update_managed_asset_should_not_change_the_normalized_name(self):
        identifier = self._create_identifier(name='SRV-DC01')
        response = self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}',
                                        {'normalized_name': 'anything'}).json()
        self.assertEqual('srv-dc01', response['normalized_name'])

    def test_update_managed_asset_should_not_change_the_uuid(self):
        identifier = self._create_identifier()
        original = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        response = self._subject.update(
            f'{_MANAGED_ASSETS_URL}/{identifier}',
            {'managed_asset_uuid': '00000000-0000-0000-0000-000000000000'}
        ).json()
        self.assertEqual(original['managed_asset_uuid'], response['managed_asset_uuid'])

    # -- tenancy -----------------------------------------------------------

    def test_get_managed_asset_should_return_404_when_customer_is_not_accessible(self):
        # 404, not 403. The caller already proved they hold the read
        # permission, so a 403 would confirm the asset exists under a
        # customer they cannot see.
        other_customer = self._subject.create_dummy_customer()
        identifier = self._subject.create(
            _MANAGED_ASSETS_URL, _asset_body(other_customer)
        ).json()['managed_asset_id']

        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)

        response = user.get(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_managed_assets_should_not_return_assets_of_an_inaccessible_customer(self):
        other_customer = self._subject.create_dummy_customer()
        self._subject.create(_MANAGED_ASSETS_URL, _asset_body(other_customer, name='HIDDEN-01'))

        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)

        response = user.get(_MANAGED_ASSETS_URL).json()
        self.assertNotIn('HIDDEN-01', [asset['name'] for asset in response['data']])

    def test_get_managed_assets_should_return_nothing_when_filtering_on_an_inaccessible_customer(self):
        # The requested customers are intersected with the accessible set,
        # never substituted for it.
        other_customer = self._subject.create_dummy_customer()
        self._subject.create(_MANAGED_ASSETS_URL, _asset_body(other_customer, name='HIDDEN-01'))

        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)

        response = user.get(f'{_MANAGED_ASSETS_URL}?client_id={other_customer}').json()
        self.assertEqual(0, response['total'])

    def test_get_managed_assets_should_return_an_empty_page_when_the_user_has_no_customer(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._create(name='SRV-DC01')
        response = user.get(_MANAGED_ASSETS_URL).json()
        self.assertEqual(0, response['total'])

    def test_get_managed_assets_should_report_a_restricted_scope_for_a_non_administrator(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.get(_MANAGED_ASSETS_URL).json()
        self.assertTrue(response['scope']['restricted'])

    def test_get_managed_assets_should_not_report_a_restricted_scope_for_an_administrator(self):
        response = self._subject.get(_MANAGED_ASSETS_URL).json()
        self.assertFalse(response['scope']['restricted'])

    def test_create_managed_asset_should_return_403_when_customer_is_not_accessible(self):
        # A write naming a customer explicitly answers 403: the id came
        # from the caller, so refusing it discloses nothing new.
        other_customer = self._subject.create_dummy_customer()
        user = self._subject.create_dummy_user(
            permissions=IRIS_PERMISSION_ASSET_MANAGER_READ | IRIS_PERMISSION_ASSET_MANAGER_WRITE
        )
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)

        response = user.create(_MANAGED_ASSETS_URL, _asset_body(other_customer))
        self.assertEqual(403, response.status_code)

    # -- permissions -------------------------------------------------------

    def test_get_managed_assets_should_return_403_without_the_read_permission(self):
        user = self._subject.create_dummy_user()
        response = user.get(_MANAGED_ASSETS_URL)
        self.assertEqual(403, response.status_code)

    def test_create_managed_asset_should_return_403_with_only_the_read_permission(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.create(_MANAGED_ASSETS_URL, _asset_body(IRIS_INITIAL_CUSTOMER_IDENTIFIER))
        self.assertEqual(403, response.status_code)

    def test_update_managed_asset_should_return_403_with_only_the_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'owner': 'someone'})
        self.assertEqual(403, response.status_code)

    def test_delete_managed_asset_should_return_403_with_only_the_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.delete(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_reconcile_managed_assets_should_return_403_with_only_the_read_permission(self):
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.create(f'{_MANAGED_ASSETS_URL}/reconcile',
                               {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})
        self.assertEqual(403, response.status_code)

    def test_get_managed_asset_should_return_200_with_only_the_read_permission(self):
        identifier = self._create_identifier()
        user = self._subject.create_dummy_user(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ)
        self._grant_customer(user, IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        response = user.get(f'{_MANAGED_ASSETS_URL}/{identifier}')
        self.assertEqual(200, response.status_code)

    # -- query parameter hardening ----------------------------------------

    def test_get_managed_assets_should_return_400_when_ordering_on_a_model_internal(self):
        # `paginate` guards ordering with `hasattr(model, order_by)`, which
        # also matches `query`, `metadata` and every relationship. The
        # route allowlists instead, so this is a 400 rather than a 500.
        response = self._subject.get(_MANAGED_ASSETS_URL, {'order_by': 'query'})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_return_400_when_ordering_on_metadata(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'order_by': 'metadata'})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_return_400_when_ordering_on_an_unknown_column(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'order_by': 'not_a_column'})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_return_200_when_ordering_on_an_allowed_column(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'order_by': 'criticality'})
        self.assertEqual(200, response.status_code)

    def test_get_managed_assets_should_return_400_when_page_is_zero(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'page': 0})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_return_400_when_per_page_is_zero(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 0})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_clamp_an_oversized_per_page(self):
        # `parse_pagination_parameters` applies no ceiling at all, so an
        # unclamped route would happily try to serialise the whole table.
        self._create()
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 1000000})
        self.assertEqual(200, response.status_code)

    def test_get_managed_assets_should_return_400_when_client_id_is_not_an_integer(self):
        # Silently dropping it would widen the result set beyond what the
        # caller asked for, which is the wrong way to fail.
        response = self._subject.get(_MANAGED_ASSETS_URL, {'client_id': 'abc'})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_return_400_when_is_active_is_not_a_boolean(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'is_active': 'perhaps'})
        self.assertEqual(400, response.status_code)

    def test_get_managed_assets_should_treat_a_percent_in_search_literally(self):
        # Unescaped, `%` is the ilike wildcard and would match everything.
        self._create(name='SRV-DC01')
        self._create(name='perc%ent')
        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': '%'}).json()
        self.assertEqual(['perc%ent'], [asset['name'] for asset in response['data']])

    def test_get_managed_assets_should_treat_an_underscore_in_search_literally(self):
        self._create(name='SRV-DC01')
        self._create(name='SRV_DC02')
        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': 'srv_'}).json()
        self.assertEqual(['SRV_DC02'], [asset['name'] for asset in response['data']])

    def test_get_managed_asset_sightings_should_return_400_for_an_unknown_kind(self):
        identifier = self._create_identifier()
        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings',
                                     {'kind': 'rumour'})
        self.assertEqual(400, response.status_code)

    def test_reconcile_managed_assets_should_return_400_without_a_customer(self):
        response = self._subject.create(f'{_MANAGED_ASSETS_URL}/reconcile', {})
        self.assertEqual(400, response.status_code)

    # -- tag registration --------------------------------------------------
    #
    # The asset's own `tags` column is the source of truth; the `tags`
    # table is the suggestion index behind autocomplete. Writing an asset
    # feeds that index, so a tag typed once on an asset can be completed
    # the next time — otherwise the field offers suggestions that only
    # ever come from somewhere else in the product.

    def test_create_managed_asset_should_register_its_tags(self):
        tag = f'tag-{uuid4().hex[:12]}'

        self._create(tags=tag)

        self.assertIn(tag, self._registered_tags(tag))

    def test_create_managed_asset_should_register_every_tag_in_the_string(self):
        first = f'tag-{uuid4().hex[:12]}'
        second = f'tag-{uuid4().hex[:12]}'

        self._create(tags=f'{first},{second}')

        self.assertIn(first, self._registered_tags(first))
        self.assertIn(second, self._registered_tags(second))

    def test_create_managed_asset_should_trim_whitespace_around_tags(self):
        tag = f'tag-{uuid4().hex[:12]}'

        self._create(tags=f'  {tag}  ')

        self.assertIn(tag, self._registered_tags(tag))

    def test_create_managed_asset_should_ignore_blank_tags(self):
        tag = f'tag-{uuid4().hex[:12]}'

        response = self._create(tags=f',,{tag},,')

        self.assertEqual(201, response.status_code)
        self.assertEqual([tag], self._registered_tags(tag))

    def test_update_managed_asset_should_register_its_tags(self):
        tag = f'tag-{uuid4().hex[:12]}'
        identifier = self._create_identifier()

        self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'tags': tag})

        self.assertIn(tag, self._registered_tags(tag))

    def test_create_managed_asset_should_accept_a_tag_that_is_already_registered(self):
        # `Tags.save()` is get-or-create, but the title is UNIQUE — a
        # second asset carrying the same tag must not trip the constraint
        # and take the whole write down with it.
        tag = f'tag-{uuid4().hex[:12]}'
        self._create(name='SRV-DC01', tags=tag)

        response = self._create(name='SRV-DC02', tags=tag)

        self.assertEqual(201, response.status_code)
        self.assertEqual([tag], self._registered_tags(tag))

    def test_create_managed_asset_should_register_a_repeated_tag_once(self):
        tag = f'tag-{uuid4().hex[:12]}'

        self._create(tags=f'{tag},{tag}')

        self.assertEqual([tag], self._registered_tags(tag))

    def _registered_tags(self, search):
        response = self._subject.get(_TAGS_URL, {'tag_title': search, 'per_page': 100}).json()
        return [tag['tag_title'] for tag in response['data']]

    def _grant_customer(self, user, customer_identifier):
        body = {'customers_membership': [customer_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/customers/update', body)
