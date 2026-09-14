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

"""Unit tests for the alert IOC/asset lookup and history helpers.

`alerts_get_ioc` / `alerts_get_asset` are the authorization boundary for
the update routes — an observable that is not on the alert has to be
indistinguishable from one that does not exist. `_updated_object_detail`
formats what the route diffed into the history line.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.business.alerts import _updated_object_detail
from app.business.alerts import alerts_get_asset
from app.business.alerts import alerts_get_ioc
from app.models.errors import ObjectNotFoundError


class TestUpdatedObjectDetail(TestCase):

    def test_no_effective_change_leaves_the_history_line_bare(self):
        # 'updated ioc', not 'updated ioc: '.
        self.assertEqual('', _updated_object_detail([]))
        self.assertEqual('', _updated_object_detail(None))

    def test_a_single_change_is_appended_after_a_colon(self):
        result = _updated_object_detail(['"ioc_tags" from "c2" to "c2,confirmed"'])
        self.assertEqual(': "ioc_tags" from "c2" to "c2,confirmed"', result)

    def test_several_changes_are_comma_separated(self):
        result = _updated_object_detail(['"ioc_description"', '"ioc_tlp_id" from "2" to "4"'])
        self.assertEqual(': "ioc_description", "ioc_tlp_id" from "2" to "4"', result)


class TestAlertsGetIoc(TestCase):

    @staticmethod
    def _alert(*ioc_identifiers):
        return SimpleNamespace(iocs=[SimpleNamespace(ioc_id=i) for i in ioc_identifiers])

    def test_returns_the_ioc_attached_to_the_alert(self):
        alert = self._alert(7, 8)
        self.assertEqual(8, alerts_get_ioc(alert, 8).ioc_id)

    def test_an_ioc_on_another_alert_is_not_found(self):
        # Looking the IOC up through the alert is what stops the route
        # from reaching an observable the caller cannot see.
        with self.assertRaises(ObjectNotFoundError):
            alerts_get_ioc(self._alert(7, 8), 9)

    def test_an_alert_without_iocs_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            alerts_get_ioc(self._alert(), 7)


class TestAlertsGetAsset(TestCase):

    @staticmethod
    def _alert(*asset_identifiers):
        return SimpleNamespace(assets=[SimpleNamespace(asset_id=i) for i in asset_identifiers])

    def test_returns_the_asset_attached_to_the_alert(self):
        self.assertEqual(4, alerts_get_asset(self._alert(3, 4), 4).asset_id)

    def test_an_asset_on_another_alert_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            alerts_get_asset(self._alert(3, 4), 5)

    def test_an_alert_without_assets_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            alerts_get_asset(self._alert(), 4)
