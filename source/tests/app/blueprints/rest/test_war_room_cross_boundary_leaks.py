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

"""Case / war-room boundary: what a case reader sees of war rooms (asset
flag history) and what a war-room member sees of cases (chat stream).

No database: access helpers and business functions are patched."""

import inspect
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.case_routes import assets as assets_routes
from app.blueprints.rest.v2.war_rooms import access
from app.blueprints.rest.v2.war_rooms import chat as chat_routes
from app.business import war_room_chat
from app.datamgmt.war_rooms.war_room_chat_db import escape_like


_ACCESS = 'app.blueprints.rest.v2.war_rooms.access'
_ASSETS = 'app.blueprints.rest.v2.case_routes.assets'
_CHAT = 'app.blueprints.rest.v2.war_rooms.chat'
_BIZ_CHAT = 'app.business.war_room_chat'


def _history_entry(war_room_id, decision_id=None):
    return {'id': 1, 'asset_id': 11, 'case_id': 7, 'war_room_id': war_room_id,
            'war_room_name': f'Room {war_room_id}' if war_room_id else None,
            'decision_id': decision_id, 'decision_number': 3 if decision_id else None}


class TestFlagHistoryRedaction(TestCase):

    def test_unreadable_rooms_are_blanked_and_checked_once(self):
        entries = [_history_entry(5, 9), _history_entry(6, 10), _history_entry(5, 11), _history_entry(None)]
        with patch(f'{_ACCESS}.require_war_room_read',
                   side_effect=lambda war_room_id: None if war_room_id == 6 else 'denied') as check:
            out = access.war_room_redact_flag_history(entries)
        self.assertEqual([5, 6], [call.args[0] for call in check.call_args_list])
        for entry in (out[0], out[2]):
            self.assertEqual((None, None, None, None), (entry['war_room_id'], entry['war_room_name'],
                                                        entry['decision_id'], entry['decision_number']))
        self.assertEqual((6, 'Room 6', 10, 3), (out[1]['war_room_id'], out[1]['war_room_name'],
                                                 out[1]['decision_id'], out[1]['decision_number']))
        self.assertEqual(7, out[0]['case_id'])

    def test_flag_history_route_redacts(self):
        asset = SimpleNamespace(asset_id=11, case_id=7)
        entries = [_history_entry(5, 9)]
        with patch(f'{_ASSETS}.ac_fast_check_current_user_has_case_access', return_value='read'), \
                patch.object(assets_routes.assets_operations, '_get_asset_in_case', return_value=asset), \
                patch(f'{_ASSETS}.asset_flags_history', return_value=entries), \
                patch(f'{_ACCESS}.require_war_room_read', return_value='denied'), \
                patch(f'{_ASSETS}.response_api_success', side_effect=lambda data: data):
            out = assets_routes.assets_operations.flag_history(7, 11)
        self.assertIsNone(out[0]['war_room_id'])
        self.assertIsNone(out[0]['war_room_name'])
        self.assertIsNone(out[0]['decision_id'])
        self.assertIsNone(out[0]['decision_number'])


class TestReadableAttachedCases(TestCase):

    def test_batched_check_keeps_attachment_order(self):
        with patch(f'{_ACCESS}.war_room_scope_attached_case_ids', return_value=[3, 1, 2]), \
                patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access',
                      return_value={2: 'full', 3: 'read'}) as check:
            self.assertEqual([3, 2], access.war_room_readable_attached_case_ids(10))
        check.assert_called_once()
        self.assertEqual([3, 1, 2], check.call_args.args[0])

    def test_no_attached_case_skips_the_check(self):
        with patch(f'{_ACCESS}.war_room_scope_attached_case_ids', return_value=[]), \
                patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access') as check:
            self.assertEqual([], access.war_room_readable_attached_case_ids(10))
        check.assert_not_called()


class TestLiveCaseActivities(TestCase):

    def _fetch(self, attached, readable, case_ids=None):
        war_room_case = MagicMock()
        war_room_case.query.with_entities.return_value.filter.return_value.all.return_value = [
            SimpleNamespace(case_id=case_id) for case_id in attached]
        with patch('app.models.war_rooms.WarRoomCase', war_room_case), \
                patch(f'{_BIZ_CHAT}._build_case_activity_query', return_value=[]) as query:
            war_room_chat._fetch_live_case_activities(10, None, 50, case_ids=case_ids,
                                                      readable_case_ids=readable)
        return query

    def test_only_readable_attached_cases_are_queried(self):
        query = self._fetch([1, 2, 3], [3, 1, 99])
        self.assertEqual([1, 3], query.call_args.args[0])

    def test_case_filter_is_intersected(self):
        query = self._fetch([1, 2, 3], [1, 2], case_ids=[2, 3])
        self.assertEqual([2], query.call_args.args[0])

    def test_nothing_readable_returns_nothing(self):
        for readable in (None, []):
            with self.subTest(readable=readable):
                self._fetch([1, 2], readable).assert_not_called()


class TestListChatRoute(TestCase):

    def test_list_chat_passes_the_readable_attached_cases(self):
        handler = inspect.unwrap(chat_routes.list_chat)
        with app.test_request_context('/?search=x'), \
                patch(f'{_CHAT}.require_war_room_read', return_value=None), \
                patch(f'{_CHAT}.war_room_readable_attached_case_ids', return_value=[4]) as readable, \
                patch(f'{_CHAT}.list_messages', return_value=[]) as list_messages, \
                patch(f'{_CHAT}.list_reactions', return_value={}), \
                patch(f'{_CHAT}.iris_current_user', SimpleNamespace(id=7)), \
                patch(f'{_CHAT}.response_api_success', side_effect=lambda data: data):
            handler(10)
        readable.assert_called_once_with(10)
        self.assertEqual([4], list_messages.call_args.kwargs['readable_case_ids'])


class TestEscapeLike(TestCase):

    def test_wildcards_and_backslash_are_escaped(self):
        self.assertEqual('50\\%\\_off\\\\x', escape_like('50%_off\\x'))
        self.assertEqual('plain', escape_like('plain'))
