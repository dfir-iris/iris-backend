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

"""The leakage suite for Manage > Assets.

Every derived number the registry reports — sighting counts, first/last
seen, compromise status, the `has_sightings` and `compromised` filters —
is computed over the sightings the *caller* can open, never over the true
totals. A reader who can open 2 of 4 cases must not be able to tell that
the other 2 exist, in any form: not as a count, not as a timestamp, not
as a filter that includes an asset it should not.

`test_get_managed_asset_should_not_expose_a_hidden_sighting_count_field`
is a regression guard, not a behaviour test. It asserts the *absence* of
any per-asset "there is more you cannot see" field, because such a field
is a one-bit oracle enumerable across the whole registry in one paginated
sweep.
"""

from unittest import TestCase

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_FULL_ACCESS
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER
from iris import IRIS_PERMISSION_ALERTS_READ
from iris import IRIS_PERMISSION_ASSET_MANAGER_READ

_MANAGED_ASSETS_URL = '/api/v2/manage/managed-assets'
_ASSET_NAME = 'SRV-DC01'
_DEFAULT_ASSET_TYPE_IDENTIFIER = 1
_COMPROMISE_STATUS_COMPROMISED = 0x1
_COMPROMISE_STATUS_NOT_COMPROMISED = 0x2

# Every per-asset shape that would tell a reader something exists outside
# their scope. None of these may ever appear in a response.
_FORBIDDEN_DISCLOSURE_FIELDS = (
    'hidden_sighting_count',
    'hidden_case_sighting_count',
    'hidden_alert_sighting_count',
    'has_restricted_sightings',
    'total_case_sighting_count',
    'total_alert_sighting_count',
    'total_sighting_count',
)


class TestsRestManagedAssetsSightings(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 100}).json()
        for asset in response.get('data', []):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')
        self._subject.clear_database()

    # -- fixtures ----------------------------------------------------------

    def _add_case_asset(self, case_identifier, name=_ASSET_NAME, **overrides):
        body = {'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER, 'asset_name': name}
        body.update(overrides)
        return self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()

    def _add_alert_asset(self, customer_identifier, name=_ASSET_NAME, **overrides):
        asset = {'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER, 'asset_name': name}
        asset.update(overrides)
        body = {
            'alert_title': 'alert title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': customer_identifier,
            'alert_assets': [asset],
        }
        return self._subject.create('/api/v2/alerts', body).json()

    def _registry_identifier(self, name=_ASSET_NAME):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': name}).json()
        for asset in response['data']:
            if asset['name'].lower() == name.lower():
                return asset['managed_asset_id']
        self.fail(f'"{name}" was never registered')

    def _reader(self, cases=(), permissions=IRIS_PERMISSION_ASSET_MANAGER_READ):
        """A non-admin who can read the registry for the initial customer."""
        user = self._subject.create_dummy_user(permissions=permissions)
        self._subject.create(
            f'/manage/users/{user.get_identifier()}/customers/update',
            {'customers_membership': [IRIS_INITIAL_CUSTOMER_IDENTIFIER]},
        )
        if cases:
            self._subject.create(
                f'/manage/users/{user.get_identifier()}/cases-access/update',
                {'cases_list': list(cases), 'access_level': IRIS_CASE_ACCESS_LEVEL_FULL_ACCESS},
            )
        return user

    # -- counts are visible-only ------------------------------------------

    def test_get_managed_asset_should_count_a_case_sighting_for_an_administrator(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(1, response['case_sighting_count'])

    def test_get_managed_asset_should_count_an_alert_sighting_for_an_administrator(self):
        self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(1, response['alert_sighting_count'])

    def test_get_managed_asset_should_not_count_a_case_the_reader_cannot_open(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(0, response['case_sighting_count'])

    def test_get_managed_asset_should_count_only_the_cases_the_reader_can_open(self):
        visible_case = self._subject.create_dummy_case()
        hidden_case = self._subject.create_dummy_case()
        self._add_case_asset(visible_case)
        self._add_case_asset(hidden_case)
        identifier = self._registry_identifier()

        reader = self._reader(cases=[visible_case])
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(1, response['case_sighting_count'])

    def test_get_managed_asset_should_not_expose_a_hidden_sighting_count_field(self):
        # Regression guard. A per-asset "n sightings you cannot see" is a
        # one-bit oracle for "an investigation involving this host exists",
        # enumerable across the registry in a single paginated sweep.
        visible_case = self._subject.create_dummy_case()
        hidden_case = self._subject.create_dummy_case()
        self._add_case_asset(visible_case)
        self._add_case_asset(hidden_case)
        identifier = self._registry_identifier()

        reader = self._reader(cases=[visible_case])
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()

        for field in _FORBIDDEN_DISCLOSURE_FIELDS:
            self.assertNotIn(field, response)

    def test_get_managed_assets_should_not_expose_a_hidden_sighting_count_field(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)

        reader = self._reader()
        response = reader.get(_MANAGED_ASSETS_URL).json()

        for asset in response['data']:
            for field in _FORBIDDEN_DISCLOSURE_FIELDS:
                self.assertNotIn(field, asset)

    def test_get_managed_asset_should_not_expose_first_seen_from_an_invisible_case(self):
        # A timestamp is a count with better resolution: `first_seen_at`
        # over the full set would disclose *when* an invisible case
        # touched the host.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIsNone(response['first_seen_at'])

    def test_get_managed_asset_should_not_expose_last_seen_from_an_invisible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIsNone(response['last_seen_at'])

    def test_get_managed_asset_should_not_expose_a_compromise_status_from_an_invisible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIsNone(response['compromise_status_id'])

    def test_get_managed_asset_should_report_compromised_from_a_visible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        identifier = self._registry_identifier()

        reader = self._reader(cases=[case_identifier])
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(_COMPROMISE_STATUS_COMPROMISED, response['compromise_status_id'])

    def test_get_managed_asset_should_prefer_compromised_over_not_compromised(self):
        # One confirmed compromise is the fact that matters; reporting
        # "not compromised" because most sightings were clean would be
        # actively misleading in a DFIR inventory.
        compromised_case = self._subject.create_dummy_case()
        clean_case = self._subject.create_dummy_case()
        self._add_case_asset(
            compromised_case, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        self._add_case_asset(
            clean_case, asset_compromise_status_id=_COMPROMISE_STATUS_NOT_COMPROMISED
        )
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(_COMPROMISE_STATUS_COMPROMISED, response['compromise_status_id'])

    def test_get_managed_asset_should_date_the_compromise_from_the_sighting(self):
        # No column records when a compromise flag was set, so the date
        # reported is the earliest sighting carrying the status — and it
        # has to be the very timestamp the sightings tab shows for that
        # observation, or the two disagree on screen.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        sightings = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertEqual(sightings['data'][0]['seen_at'], response['compromised_at'])

    def test_get_managed_asset_should_not_date_a_compromise_that_did_not_happen(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_NOT_COMPROMISED
        )
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIsNone(response['compromised_at'])

    def test_get_managed_asset_should_date_the_compromise_from_the_earliest_sighting(self):
        # "Compromised since": the oldest observation that says so, not
        # the most recent one.
        older_case = self._subject.create_dummy_case()
        newer_case = self._subject.create_dummy_case()
        self._add_case_asset(
            older_case, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        self._add_case_asset(
            newer_case, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        sightings = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        # The listing is newest first, so the last row is the oldest one.
        self.assertEqual(sightings['data'][-1]['seen_at'], response['compromised_at'])

    def test_get_managed_asset_should_not_date_a_compromise_from_an_invisible_case(self):
        # The date is as disclosing as the status it accompanies: it
        # times an investigation the reader cannot open.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertIsNone(response['compromised_at'])

    # -- sighting listings -------------------------------------------------

    def test_get_sightings_should_return_the_case_observation(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertEqual([case_identifier],
                         [row['reference_id'] for row in response['data'] if row['kind'] == 'case'])

    def test_get_sightings_should_return_an_empty_page_without_case_access(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertEqual(0, response['total'])

    def test_get_sightings_should_return_only_the_accessible_case(self):
        visible_case = self._subject.create_dummy_case()
        hidden_case = self._subject.create_dummy_case()
        self._add_case_asset(visible_case)
        self._add_case_asset(hidden_case)
        identifier = self._registry_identifier()

        reader = self._reader(cases=[visible_case])
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertEqual([visible_case], [row['reference_id'] for row in response['data']])

    def test_get_sightings_should_return_the_alert_observation(self):
        self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings',
                                     {'kind': 'alert'}).json()
        self.assertEqual(1, response['total'])

    def test_get_sightings_should_not_return_alerts_of_a_customer_the_reader_is_not_a_member_of(self):
        other_customer = self._subject.create_dummy_customer()
        self._add_alert_asset(other_customer)

        # Registered under the other customer, so the reader cannot even
        # resolve the registry row — which is the point.
        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': _ASSET_NAME}).json()
        identifier = response['data'][0]['managed_asset_id']

        reader = self._reader(permissions=IRIS_PERMISSION_ASSET_MANAGER_READ
                              | IRIS_PERMISSION_ALERTS_READ)
        self.assertEqual(404, reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').status_code)

    def test_get_sightings_should_filter_on_kind(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        identifier = self._registry_identifier()

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings',
                                     {'kind': 'case'}).json()
        self.assertEqual(['case'], sorted({row['kind'] for row in response['data']}))

    def test_get_sightings_should_reflect_a_case_deletion(self):
        # The test a materialized sighting table would fail: case deletion
        # bulk-updates `case_assets.case_id` with a `Query.update()`, which
        # fires no ORM event, so any cached link would still point at the
        # deleted case.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()

        self._subject.delete(f'/api/v2/cases/{case_identifier}')

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertEqual(0, response['total'])

    def test_get_managed_asset_should_survive_a_case_deletion(self):
        # The registry row and its metadata are durable; only the derived
        # counts follow the cases.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()
        self._subject.update(f'{_MANAGED_ASSETS_URL}/{identifier}', {'owner': 'IT Ops'})

        self._subject.delete(f'/api/v2/cases/{case_identifier}')

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual('IT Ops', response['owner'])

    def test_get_sightings_should_reflect_an_alert_escalation_as_a_case_sighting(self):
        # `create_case_from_alert` mutates the asset's `case_id` in place
        # rather than inserting a new row, so an alert-only observation
        # becomes a case observation without any insert happening.
        alert = self._add_alert_asset(IRIS_INITIAL_CUSTOMER_IDENTIFIER)
        identifier = self._registry_identifier()

        self._subject.create(f'/api/v2/alerts/escalate/{alert["alert_id"]}', {
            'iocs_import_list': [],
            'assets_import_list': [asset['asset_uuid'] for asset in alert['assets']],
            'note': '',
            'case_title': 'escalated case',
            'case_tags': '',
            'case_template_id': '',
        })

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/sightings').json()
        self.assertIn('case', {row['kind'] for row in response['data']})

    # -- filters must run against the visible set only ---------------------

    def test_get_managed_assets_should_not_match_has_sightings_for_an_invisible_case(self):
        # If `has_sightings` ran against the true set, it would become
        # exactly the oracle the counts policy exists to remove.
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}?has_sightings=true').json()
        self.assertEqual(0, response['total'])

    def test_get_managed_assets_should_match_has_sightings_for_a_visible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(case_identifier)

        reader = self._reader(cases=[case_identifier])
        response = reader.get(f'{_MANAGED_ASSETS_URL}?has_sightings=true').json()
        self.assertEqual([_ASSET_NAME], [asset['name'] for asset in response['data']])

    def test_get_managed_assets_should_not_match_compromised_for_an_invisible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}?compromised=true').json()
        self.assertEqual(0, response['total'])

    def test_get_managed_assets_should_match_compromised_for_a_visible_case(self):
        case_identifier = self._subject.create_dummy_case()
        self._add_case_asset(
            case_identifier, asset_compromise_status_id=_COMPROMISE_STATUS_COMPROMISED
        )

        reader = self._reader(cases=[case_identifier])
        response = reader.get(f'{_MANAGED_ASSETS_URL}?compromised=true').json()
        self.assertEqual([_ASSET_NAME], [asset['name'] for asset in response['data']])

    # -- timeline ----------------------------------------------------------

    def test_get_timeline_should_return_the_event_referencing_the_asset(self):
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()
        self._create_event(case_identifier, asset['asset_id'])

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/timeline').json()
        self.assertEqual(1, response['total'])

    def test_get_timeline_should_return_the_event_fields(self):
        # `total` is a separate count query, so a page of empty rows still
        # reports the right number of them. The row payload has to be
        # asserted on its own or a serialisation fault reads as a pass.
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()
        self._create_event(case_identifier, asset['asset_id'])

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/timeline').json()
        self.assertEqual(['lateral movement'],
                         [event['event_title'] for event in response['data']])

    def test_get_timeline_should_count_an_event_seen_through_two_case_assets_once(self):
        # One host recorded twice in a case under different spellings is a
        # single registry entry but two `case_assets` rows, so an event
        # referencing both joins twice.
        case_identifier = self._subject.create_dummy_case()
        first = self._add_case_asset(case_identifier)
        second = self._add_case_asset(case_identifier, name=_ASSET_NAME.lower())
        identifier = self._registry_identifier()
        self._create_event(case_identifier, first['asset_id'], second['asset_id'])

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/timeline').json()
        self.assertEqual(1, response['total'])

    def test_get_timeline_should_return_an_event_seen_through_two_case_assets_once(self):
        # The companion to the count above: `total` and the page are
        # computed by two different statements and can disagree.
        case_identifier = self._subject.create_dummy_case()
        first = self._add_case_asset(case_identifier)
        second = self._add_case_asset(case_identifier, name=_ASSET_NAME.lower())
        identifier = self._registry_identifier()
        self._create_event(case_identifier, first['asset_id'], second['asset_id'])

        response = self._subject.get(f'{_MANAGED_ASSETS_URL}/{identifier}/timeline').json()
        self.assertEqual(1, len(response['data']))

    def test_get_timeline_should_return_an_empty_page_without_case_access(self):
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()
        self._create_event(case_identifier, asset['asset_id'])

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}/timeline').json()
        self.assertEqual(0, response['total'])

    def test_get_managed_asset_should_count_timeline_events_within_scope_only(self):
        case_identifier = self._subject.create_dummy_case()
        asset = self._add_case_asset(case_identifier)
        identifier = self._registry_identifier()
        self._create_event(case_identifier, asset['asset_id'])

        reader = self._reader()
        response = reader.get(f'{_MANAGED_ASSETS_URL}/{identifier}').json()
        self.assertEqual(0, response['timeline_event_count'])

    def _create_event(self, case_identifier, *asset_identifiers):
        body = {
            'event_title': 'lateral movement',
            'event_content': 'observed on the host',
            'event_date': '2026-01-01T10:00:00.000',
            'event_tz': '+00:00',
            'event_assets': list(asset_identifiers),
            'event_iocs': [],
            'event_category_id': 1,
            'event_in_summary': False,
            'event_in_graph': False,
            'event_color': '#1572E899',
        }
        return self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
