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

"""Unit tests for Yuki's war-room decision tools (business layer mocked)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.tools import war_rooms as war_rooms_tools
from app.models.errors import BusinessProcessingError


_WR = 'app.blueprints.rest.v2.mcp.tools.war_rooms'
_BIZ = f'{_WR}.war_room_decisions_biz'


def _user():
    user = MagicMock()
    user._get_current_object.return_value = SimpleNamespace(id=5)
    return user


class TestDecisionToolsClassification(TestCase):

    def test_list_is_read_only_and_create_is_write(self):
        self.assertIn('iris_war_room_decisions_list', classification.READ_ONLY_TOOLS)
        self.assertIn('iris_war_room_decisions_create', classification.WRITE_TOOLS)


class TestDecisionsList(TestCase):

    def test_serializes_with_only_readable_cases(self):
        decision = SimpleNamespace(decision_id=1)
        with patch(f'{_BIZ}.war_room_decisions_list', return_value=[decision]) as biz_list, \
                patch(f'{_BIZ}.war_room_decisions_referenced_case_ids', return_value={10, 20}), \
                patch(f'{_WR}.ac_fast_check_current_user_has_cases_access',
                      side_effect=lambda cids, *_: {cid: 'ok' for cid in cids if cid == 10}), \
                patch(f'{_BIZ}.war_room_decisions_serialize', return_value=[{'decision_id': 1}]) as ser:
            out = war_rooms_tools.iris_war_room_decisions_list(
                {'war_room_id': 7, 'status': 'proposed', 'q': 'dc'})
        self.assertEqual(out, {'decisions': [{'decision_id': 1}]})
        biz_list.assert_called_once_with(7, status=['proposed'], q='dc')
        ser.assert_called_once_with([decision], {10})

    def test_ignores_malformed_filters(self):
        with patch(f'{_BIZ}.war_room_decisions_list', return_value=[]) as biz_list, \
                patch(f'{_BIZ}.war_room_decisions_referenced_case_ids', return_value=set()), \
                patch(f'{_BIZ}.war_room_decisions_serialize', return_value=[]):
            war_rooms_tools.iris_war_room_decisions_list({'war_room_id': 7, 'status': 3, 'q': ['x']})
        biz_list.assert_called_once_with(7, status=None, q=None)


class TestDecisionsCreate(TestCase):

    def test_allow_lists_fields_and_emits_event(self):
        decision = SimpleNamespace(decision_id=11, number=4, title='Isolate DC01')
        emit = MagicMock()
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_BIZ}.war_room_decisions_create', return_value=decision) as create, \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event', emit), \
                patch(f'{_BIZ}.war_room_decisions_referenced_case_ids', return_value=set()), \
                patch(f'{_BIZ}.war_room_decisions_serialize', return_value=[{'decision_id': 11}]):
            out = war_rooms_tools.iris_war_room_decisions_create({
                'war_room_id': 7, 'title': 'Isolate DC01', 'target_at': '2026-10-06T18:00:00Z',
                'asset_ids': [1], 'supersedes_id': 3, 'chat_message_id': 9,
            })
        self.assertEqual(out, {'decision_id': 11})
        create.assert_called_once_with(
            7, {'title': 'Isolate DC01', 'target_at': '2026-10-06T18:00:00Z'}, created_by_id=5)
        emit.assert_called_once()
        self.assertEqual(emit.call_args.args, (7, 'decision', 'D-4 Isolate DC01'))
        self.assertEqual(emit.call_args.kwargs['ref_id'], 11)

    def test_business_error_maps_to_invalid_params(self):
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_BIZ}.war_room_decisions_create',
                      side_effect=BusinessProcessingError('Case #3 is not attached to this war room')), \
                patch(f'{_WR}.ac_fast_check_current_user_has_cases_access', return_value={3: 'ok'}), \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event') as emit:
            with self.assertRaises(MCPError):
                war_rooms_tools.iris_war_room_decisions_create({'war_room_id': 7, 'title': 'x', 'case_ids': [3]})
        emit.assert_not_called()

    def test_unreadable_case_is_rejected_like_an_unattached_one(self):
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_BIZ}.war_room_decisions_create') as create, \
                patch(f'{_WR}.ac_fast_check_current_user_has_cases_access',
                      return_value={3: 'ok'}) as access, \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event') as emit:
            with self.assertRaises(MCPError) as ctx:
                war_rooms_tools.iris_war_room_decisions_create(
                    {'war_room_id': 7, 'title': 'x', 'case_ids': [3, 4]})
        self.assertIn('Case #4 is not attached to this war room', str(ctx.exception))
        self.assertEqual([3, 4], access.call_args.args[0])
        create.assert_not_called()
        emit.assert_not_called()

    def test_readable_cases_reach_the_business_layer(self):
        decision = SimpleNamespace(decision_id=11, number=4, title='x')
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_BIZ}.war_room_decisions_create', return_value=decision) as create, \
                patch(f'{_WR}.ac_fast_check_current_user_has_cases_access',
                      side_effect=lambda cids, *_: {cid: 'ok' for cid in cids}), \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event'), \
                patch(f'{_BIZ}.war_room_decisions_referenced_case_ids', return_value=set()), \
                patch(f'{_BIZ}.war_room_decisions_serialize', return_value=[{'decision_id': 11}]):
            war_rooms_tools.iris_war_room_decisions_create({'war_room_id': 7, 'title': 'x', 'case_ids': [3]})
        create.assert_called_once_with(7, {'title': 'x', 'case_ids': [3]}, created_by_id=5)

    def test_malformed_case_ids_are_rejected(self):
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_BIZ}.war_room_decisions_create') as create:
            with self.assertRaises(MCPError):
                war_rooms_tools.iris_war_room_decisions_create(
                    {'war_room_id': 7, 'title': 'x', 'case_ids': 'all'})
        create.assert_not_called()
