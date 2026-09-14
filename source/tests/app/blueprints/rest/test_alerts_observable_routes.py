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

"""Unit tests for the pure helpers behind the alert IOC/asset update routes.

The allow-list and the activity diff are plain dict/attribute work — no
DB, no Flask context. The route bodies around them (access control,
schema load) are covered by the REST suite in `iris-backend/tests`.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.blueprints.rest.v2.alerts_routes.assets import _asset_update_activity
from app.blueprints.rest.v2.alerts_routes.assets import _editable_fields_only as _asset_fields_only
from app.blueprints.rest.v2.alerts_routes.iocs import _editable_fields_only as _ioc_fields_only
from app.blueprints.rest.v2.alerts_routes.iocs import _ioc_update_activity


class TestIocEditableFieldsOnly(TestCase):

    def test_keeps_the_analyst_supplied_details(self):
        payload = {
            'ioc_value': 'evil.example.com',
            'ioc_type_id': 3,
            'ioc_tlp_id': 2,
            'ioc_description': 'seen in the proxy logs',
            'ioc_tags': 'c2,confirmed',
            'ioc_enrichment': {'vt': {'malicious': 3}}
        }
        self.assertEqual(payload, _ioc_fields_only(payload))

    def test_drops_the_case_attachment(self):
        # The whole point of the allow-list: alerts_write must not become
        # a way to move an IOC into, or out of, a case.
        result = _ioc_fields_only({'ioc_description': 'note', 'case_id': 42})
        self.assertEqual({'ioc_description': 'note'}, result)

    def test_drops_identity_and_ownership(self):
        result = _ioc_fields_only({
            'ioc_id': 1,
            'ioc_uuid': '0e3ff0e5-0000-0000-0000-000000000000',
            'user_id': 7,
            'ioc_misp': 'x',
            'ioc_tags': 'c2'
        })
        self.assertEqual({'ioc_tags': 'c2'}, result)

    def test_a_body_less_request_yields_nothing(self):
        # request.get_json() returns None on an empty body; the route
        # turns an empty result into a 400 rather than a crash.
        self.assertEqual({}, _ioc_fields_only(None))
        self.assertEqual({}, _ioc_fields_only([{'ioc_tags': 'c2'}]))
        self.assertEqual({}, _ioc_fields_only('ioc_tags'))

    def test_an_empty_payload_yields_nothing(self):
        self.assertEqual({}, _ioc_fields_only({}))


class TestAssetEditableFieldsOnly(TestCase):

    def test_keeps_the_analyst_supplied_details(self):
        payload = {
            'asset_name': 'WKS-01',
            'asset_type_id': 9,
            'asset_description': 'jump host',
            'asset_domain': 'corp.example.com',
            'asset_ip': '10.0.0.5',
            'asset_tags': 'workstation',
            'asset_enrichment': {'edr': 'isolated'}
        }
        self.assertEqual(payload, _asset_fields_only(payload))

    def test_drops_the_case_attachment_and_identity(self):
        result = _asset_fields_only({
            'asset_ip': '10.0.0.5',
            'case_id': 42,
            'asset_id': 4,
            'asset_uuid': '0e3ff0e5-0000-0000-0000-000000000000',
            'user_id': 7
        })
        self.assertEqual({'asset_ip': '10.0.0.5'}, result)

    def test_a_body_less_request_yields_nothing(self):
        self.assertEqual({}, _asset_fields_only(None))


class TestIocUpdateActivity(TestCase):

    @staticmethod
    def _ioc(**overrides):
        values = {
            'ioc_value': 'evil.example.com',
            'ioc_type_id': 3,
            'ioc_tlp_id': 2,
            'ioc_description': None,
            'ioc_tags': None,
            'ioc_enrichment': None
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_an_untouched_field_is_not_reported(self):
        ioc = self._ioc()
        self.assertEqual([], _ioc_update_activity(ioc, {'ioc_value': 'evil.example.com'}))

    def test_the_whole_form_coming_back_unchanged_reports_nothing(self):
        # The dialog PUTs every field it holds, so this is the common case
        # for a save that only touched one of them.
        ioc = self._ioc(ioc_description='known bad', ioc_tags='c2')
        request_data = {
            'ioc_value': 'evil.example.com',
            'ioc_type_id': 3,
            'ioc_tlp_id': 2,
            'ioc_description': 'known bad',
            'ioc_tags': 'c2'
        }
        self.assertEqual([], _ioc_update_activity(ioc, request_data))

    def test_a_changed_field_is_reported_from_old_to_new(self):
        ioc = self._ioc(ioc_tags='c2')
        result = _ioc_update_activity(ioc, {'ioc_tags': 'c2,confirmed'})
        self.assertEqual(['"ioc_tags" from "c2" to "c2,confirmed"'], result)

    def test_only_the_changed_fields_are_reported(self):
        ioc = self._ioc()
        result = _ioc_update_activity(ioc, {'ioc_value': 'evil.example.com', 'ioc_tlp_id': 4})
        self.assertEqual(['"ioc_tlp_id" from "2" to "4"'], result)

    def test_a_select_sent_back_as_a_json_string_is_not_a_change(self):
        # The model holds an int, the payload a JSON scalar — without the
        # coercion an untouched select would be reported on every save.
        ioc = self._ioc()
        self.assertEqual([], _ioc_update_activity(ioc, {'ioc_type_id': '3'}))

    def test_a_select_really_changing_is_still_reported(self):
        ioc = self._ioc()
        result = _ioc_update_activity(ioc, {'ioc_type_id': '5'})
        self.assertEqual(['"ioc_type_id" from "3" to "5"'], result)

    def test_free_text_and_json_are_named_but_not_quoted_into_the_history(self):
        ioc = self._ioc()
        result = _ioc_update_activity(ioc, {
            'ioc_description': 'exfil to 10.0.0.5, credentials in the body',
            'ioc_enrichment': {'vt': {'malicious': 3}}
        })
        self.assertEqual(['"ioc_description"', '"ioc_enrichment"'], result)

    def test_clearing_a_field_is_reported(self):
        ioc = self._ioc(ioc_tags='c2')
        result = _ioc_update_activity(ioc, {'ioc_tags': ''})
        self.assertEqual(['"ioc_tags" from "c2" to ""'], result)

    def test_filling_a_null_column_is_reported(self):
        ioc = self._ioc()
        result = _ioc_update_activity(ioc, {'ioc_tags': 'c2'})
        self.assertEqual(['"ioc_tags" from "None" to "c2"'], result)

    def test_an_attribute_the_model_does_not_carry_is_treated_as_unset(self):
        result = _ioc_update_activity(SimpleNamespace(), {'ioc_tags': 'c2'})
        self.assertEqual(['"ioc_tags" from "None" to "c2"'], result)


class TestAssetUpdateActivity(TestCase):

    @staticmethod
    def _asset(**overrides):
        values = {
            'asset_name': 'WKS-01',
            'asset_type_id': 9,
            'asset_description': None,
            'asset_domain': None,
            'asset_ip': None,
            'asset_tags': None,
            'asset_enrichment': None
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_an_untouched_field_is_not_reported(self):
        self.assertEqual([], _asset_update_activity(self._asset(), {'asset_name': 'WKS-01'}))

    def test_a_changed_field_is_reported_from_old_to_new(self):
        result = _asset_update_activity(self._asset(), {'asset_ip': '10.0.0.5'})
        self.assertEqual(['"asset_ip" from "None" to "10.0.0.5"'], result)

    def test_a_select_sent_back_as_a_json_string_is_not_a_change(self):
        self.assertEqual([], _asset_update_activity(self._asset(), {'asset_type_id': '9'}))

    def test_free_text_and_json_are_named_but_not_quoted_into_the_history(self):
        result = _asset_update_activity(self._asset(), {
            'asset_description': 'jump host, admin creds cached',
            'asset_enrichment': {'edr': 'isolated'}
        })
        self.assertEqual(['"asset_description"', '"asset_enrichment"'], result)
