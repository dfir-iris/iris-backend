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

"""War-room chat slash commands: scope commands (/asset, /ioc, /stage,
/push, /share-note) and the reworked /decision, /pin and /sitrep.

No database: the access helpers and the business functions are patched,
so these tests pin down which cases the handlers authorize, what they
hand to the business layer and the system row they return."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.war_rooms import chat
from app.blueprints.rest.v2.war_rooms import chat_commands
from app.models.errors import BusinessProcessingError


_CMD = 'app.blueprints.rest.v2.war_rooms.chat_commands'
_CHAT = 'app.blueprints.rest.v2.war_rooms.chat'
_USER = SimpleNamespace(id=7)
_ASSET_TYPES = [(1, 'Account'), (3, 'Windows - Computer')]
_IOC_TYPES = [{'type_id': 20, 'type_name': 'ip-dst'}, {'type_id': 21, 'type_name': 'domain'}]
_STAGES = [{'id': 4, 'name': 'Contained'}, {'id': 5, 'name': 'Under investigation'}]


class _ScopeCommandTest(TestCase):
    """Attached cases 1, 2, 3; readable 1, 2, 3; writable 1, 2."""

    attached = [1, 2, 3]
    readable = [1, 2, 3]
    writable = [1, 2]

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.mocks = {}

        def _patch(name, **kwargs):
            self.mocks[name] = stack.enter_context(patch(f'{_CMD}.{name}', **kwargs))
            return self.mocks[name]

        _patch('iris_current_user', new=_USER)
        _patch('war_room_scope_attached_case_ids', return_value=list(self.attached))
        _patch('_readable_case_ids', return_value=list(self.readable))
        _patch('_writable_case_ids', side_effect=lambda readable: [c for c in readable if c in self.writable])
        _patch('war_room_chat_commands_asset_types', return_value=_ASSET_TYPES)
        _patch('war_room_chat_commands_ioc_types', return_value=_IOC_TYPES)
        _patch('war_room_chat_commands_stages', return_value=_STAGES)
        for name in ('war_room_scope_create_asset', 'war_room_scope_create_ioc', 'war_room_scope_bulk_stage',
                     'war_room_scope_push_assets', 'war_room_scope_push_iocs', 'war_room_scope_staged_push',
                     'war_room_scope_staged_create', 'war_room_scope_list_assets', 'war_room_scope_list_iocs',
                     'war_room_scope_staged_list', 'war_room_chat_commands_decision_id'):
            _patch(name)
        self.mocks['war_room_scope_staged_list'].return_value = []
        self.mocks['war_room_scope_list_assets'].return_value = {'data': []}
        self.mocks['war_room_scope_list_iocs'].return_value = {'data': []}

    @staticmethod
    def _results(*rows):
        return {'results': [{'case_id': case_id, 'status': status} for case_id, status in rows]}


class TestAssetCommand(_ScopeCommandTest):

    def test_without_target_stages_in_the_war_room(self):
        row = chat_commands.chat_commands_resolve(10, 'asset', 'WS-042')
        self.mocks['war_room_scope_staged_create'].assert_called_once_with(
            10, 7, {'object_type': 'asset', 'payload': {'asset_name': 'WS-042', 'asset_type_id': 3}})
        self.mocks['war_room_scope_create_asset'].assert_not_called()
        self.assertEqual(('system', 'Staged asset "WS-042" (Windows - Computer) in the war room', None, None, None),
                         row)

    def test_ip_name_also_sets_asset_ip(self):
        chat_commands.chat_commands_resolve(10, 'asset', '10.1.2.3')
        payload = self.mocks['war_room_scope_staged_create'].call_args.args[2]['payload']
        self.assertEqual('10.1.2.3', payload['asset_ip'])

    def test_all_targets_writable_attached_cases(self):
        self.mocks['war_room_scope_create_asset'].return_value = self._results((1, 'created'), (2, 'exists'))
        row = chat_commands.chat_commands_resolve(10, 'asset', 'CORP\\bob all')
        self.mocks['war_room_scope_create_asset'].assert_called_once_with(
            10, _USER, {'asset_name': 'CORP\\bob', 'asset_type_id': 1}, [1, 2], [1, 2])
        self.assertEqual(('system', 'Asset "CORP\\bob" (Account): added to 1 case; already in 1 case', None, None, None), row)

    def test_single_case_row_points_at_the_case(self):
        self.mocks['war_room_scope_create_asset'].return_value = self._results((2, 'created'))
        row = chat_commands.chat_commands_resolve(10, 'asset', '"WS 1" type:"windows - computer" #case-2')
        self.assertEqual(('case', 2, 2), row[2:])

    def test_unwritable_listed_case_is_denied_and_errors(self):
        self.mocks['war_room_scope_create_asset'].return_value = self._results((3, 'denied'))
        with self.assertRaisesRegex(BusinessProcessingError, 'denied on 1 case'):
            chat_commands.chat_commands_resolve(10, 'asset', 'WS-042 #3')
        self.assertEqual([3], self.mocks['war_room_scope_create_asset'].call_args.args[3])
        self.assertEqual([1, 2], self.mocks['war_room_scope_create_asset'].call_args.args[4])

    def test_errors(self):
        for rest, message in (('', 'Usage: /asset'), ('#1', 'Usage: /asset'),
                              ('two words #1', 'Unexpected text "words"'),
                              ('WS customer', 'cannot target a customer'),
                              ('WS #9', 'Case #9 is not attached'),
                              ('WS type:Router', 'Unknown asset type')):
            with self.assertRaisesRegex(BusinessProcessingError, message, msg=rest):
                chat_commands.chat_commands_resolve(10, 'asset', rest)


class TestIocCommand(_ScopeCommandTest):

    def test_type_is_detected(self):
        self.mocks['war_room_scope_create_ioc'].return_value = self._results((1, 'created'))
        row = chat_commands.chat_commands_resolve(10, 'ioc', '10.0.0.5 #1')
        self.mocks['war_room_scope_create_ioc'].assert_called_once_with(
            10, _USER, {'ioc_value': '10.0.0.5', 'ioc_type_id': 20}, [1], [1, 2])
        self.assertEqual('IOC "10.0.0.5" (ip-dst): added to 1 case', row[1])

    def test_without_target_stages(self):
        chat_commands.chat_commands_resolve(10, 'ioc', 'evil.example')
        self.mocks['war_room_scope_staged_create'].assert_called_once_with(
            10, 7, {'object_type': 'ioc', 'payload': {'ioc_value': 'evil.example', 'ioc_type_id': 21}})

    def test_customer_is_rejected_clearly(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Customer-level IOCs are not supported'):
            chat_commands.chat_commands_resolve(10, 'ioc', 'evil.example customer')
        self.mocks['war_room_scope_create_ioc'].assert_not_called()

    def test_undetectable_type(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Could not detect the IOC type'):
            chat_commands.chat_commands_resolve(10, 'ioc', 'bob@evil.example all')


class TestStageCommand(_ScopeCommandTest):

    def _assets(self, *rows):
        self.mocks['war_room_scope_list_assets'].return_value = {'data': [
            {'asset_id': asset_id, 'asset_name': name, 'case_id': case_id, 'asset_type_id': 3}
            for asset_id, name, case_id in rows
        ]}

    def test_every_case_reason_and_decision(self):
        self._assets((11, 'WS-042', 1), (12, 'ws-042', 3), (13, 'WS-0420', 2))
        self.mocks['war_room_chat_commands_decision_id'].return_value = 99
        self.mocks['war_room_scope_bulk_stage'].return_value = {'results': [
            {'asset_id': 11, 'case_id': 1, 'status': 'updated'},
            {'asset_id': 12, 'case_id': 3, 'status': 'denied'}]}
        row = chat_commands.chat_commands_resolve(10, 'stage', 'WS-042 under investigation beacon seen D-2')
        self.mocks['war_room_scope_list_assets'].assert_called_once_with([1, 2, 3], search='WS-042')
        self.mocks['war_room_chat_commands_decision_id'].assert_called_once_with(10, 2)
        self.mocks['war_room_scope_bulk_stage'].assert_called_once_with(
            10, 7, [11, 12], 5, 'beacon seen', 99, [1, 2, 3], [1, 2])
        self.assertEqual('system', row[0])
        self.assertEqual('Stage of asset "WS-042" set to Under investigation in 1 case; denied on 1 case (D-2)', row[1])

    def test_clear_restricted_by_markup(self):
        self._assets((11, 'WS-042', 2))
        self.mocks['war_room_scope_bulk_stage'].return_value = {'results': [
            {'asset_id': 11, 'case_id': 2, 'status': 'updated'}]}
        row = chat_commands.chat_commands_resolve(10, 'stage', '[Asset "WS-042"](/case/2/assets) none')
        self.mocks['war_room_scope_list_assets'].assert_called_once_with([2], search='WS-042')
        self.assertIsNone(self.mocks['war_room_scope_bulk_stage'].call_args.args[3])
        self.assertEqual(('case', 2, 2), row[2:])
        self.assertIn('cleared in 1 case', row[1])

    def test_errors(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'No asset named "WS-9"'):
            chat_commands.chat_commands_resolve(10, 'stage', 'WS-9 Contained')
        with self.assertRaisesRegex(BusinessProcessingError, 'Unknown stage "Gone"'):
            chat_commands.chat_commands_resolve(10, 'stage', 'WS-9 Gone')
        with self.assertRaisesRegex(BusinessProcessingError, 'already applies to every attached case'):
            chat_commands.chat_commands_resolve(10, 'stage', 'WS-9 Contained all')
        self.mocks['war_room_scope_bulk_stage'].assert_not_called()

    def test_nothing_changed_is_an_error(self):
        self._assets((11, 'WS-042', 3))
        self.mocks['war_room_scope_bulk_stage'].return_value = {'results': [
            {'asset_id': 11, 'case_id': 3, 'status': 'denied'}]}
        with self.assertRaisesRegex(BusinessProcessingError, 'denied on 1 case'):
            chat_commands.chat_commands_resolve(10, 'stage', 'WS-042 Contained')


class TestPushCommand(_ScopeCommandTest):

    def test_staging_is_preferred(self):
        self.mocks['war_room_scope_staged_list'].return_value = [
            {'id': 5, 'object_type': 'asset', 'payload': {'asset_name': 'WS-042'}}]
        self.mocks['war_room_scope_list_assets'].return_value = {'data': [
            {'asset_id': 11, 'asset_name': 'WS-042', 'case_id': 1, 'asset_type_id': 3}]}
        self.mocks['war_room_scope_staged_push'].return_value = self._results((1, 'created'), (2, 'created'))
        row = chat_commands.chat_commands_resolve(10, 'push', 'WS-042 all')
        self.mocks['war_room_scope_staged_push'].assert_called_once_with(10, _USER, 5, [1, 2], [1, 2])
        self.mocks['war_room_scope_push_assets'].assert_not_called()
        self.assertEqual('Staged asset "WS-042": pushed to 2 cases', row[1])

    def test_existing_assets_one_source_per_type(self):
        self.mocks['war_room_scope_list_assets'].return_value = {'data': [
            {'asset_id': 11, 'asset_name': 'WS-042', 'case_id': 1, 'asset_type_id': 3},
            {'asset_id': 12, 'asset_name': 'WS-042', 'case_id': 3, 'asset_type_id': 3}]}
        self.mocks['war_room_scope_push_assets'].return_value = self._results((2, 'created'))
        row = chat_commands.chat_commands_resolve(10, 'push', 'WS-042 #2')
        self.mocks['war_room_scope_push_assets'].assert_called_once_with(10, _USER, [11], [2], [1, 2, 3], [1, 2])
        self.assertEqual(('system', 'Asset "WS-042": pushed to 1 case', 'case', 2, 2), row)

    def test_ioc_fallback(self):
        self.mocks['war_room_scope_list_iocs'].return_value = {'data': [
            {'ioc_id': 30, 'ioc_value': 'evil.example', 'case_id': 1}]}
        self.mocks['war_room_scope_push_iocs'].return_value = self._results((2, 'exists'))
        row = chat_commands.chat_commands_resolve(10, 'push', 'evil.example #2')
        self.mocks['war_room_scope_push_iocs'].assert_called_once_with(10, _USER, [30], [2], [1, 2, 3], [1, 2])
        self.assertEqual('IOC "evil.example": already in 1 case', row[1])

    def test_markup_subject_skips_staging_and_narrows_source(self):
        self.mocks['war_room_scope_staged_list'].return_value = [
            {'id': 5, 'object_type': 'asset', 'payload': {'asset_name': 'WS-042'}}]
        self.mocks['war_room_scope_list_assets'].return_value = {'data': [
            {'asset_id': 12, 'asset_name': 'WS-042', 'case_id': 3, 'asset_type_id': 3}]}
        self.mocks['war_room_scope_push_assets'].return_value = self._results((1, 'created'))
        chat_commands.chat_commands_resolve(10, 'push', '[Asset "WS-042"](/case/3/assets) #1')
        self.mocks['war_room_scope_staged_push'].assert_not_called()
        self.mocks['war_room_scope_list_assets'].assert_called_once_with([3], search='WS-042')

    def test_errors(self):
        for rest, message in (('WS-042', 'Missing target cases'), ('WS-042 customer', 'Customer-level'),
                              ('WS-042 #1', 'Nothing named "WS-042"'), ('', 'Usage: /push')):
            with self.assertRaisesRegex(BusinessProcessingError, message, msg=rest):
                chat_commands.chat_commands_resolve(10, 'push', rest)


class TestShareNoteCommand(TestCase):

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.mocks = {}
        for name in ('war_room_chat_commands_match_note', 'war_room_note_shares_parse_create',
                     'war_room_note_shares_resolve_targets', 'war_room_note_shares_create', '_CaseAccess'):
            self.mocks[name] = stack.enter_context(patch(f'{_CMD}.{name}'))
        stack.enter_context(patch(f'{_CMD}.iris_current_user', new=_USER))
        self.mocks['war_room_chat_commands_match_note'].return_value = SimpleNamespace(note_id=40, title='Plan')
        self.mocks['war_room_note_shares_parse_create'].side_effect = lambda _war_room_id, raw: {
            'note_id': raw['note_id'], 'folder_id': None, 'scope': raw['scope'],
            'case_ids': raw['case_ids'] or [], 'include_future': False, 'delivery': raw['delivery']}
        self.mocks['war_room_note_shares_create'].return_value = (MagicMock(), {})
        self.writable = {1, 2}
        self.mocks['_CaseAccess'].return_value.can_write.side_effect = lambda case_id: case_id in self.writable

    def test_listed_cases_copy(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = [2]
        self.mocks['war_room_note_shares_create'].return_value = (MagicMock(), {2: {'status': 'copied'}})
        row = chat_commands.chat_commands_resolve(10, 'share-note', 'containment plan #2 copy')
        self.mocks['war_room_chat_commands_match_note'].assert_called_once_with(10, 'containment plan', note_id=None)
        self.mocks['war_room_note_shares_parse_create'].assert_called_once_with(
            10, {'note_id': 40, 'scope': 'cases', 'case_ids': [2], 'delivery': 'copy'})
        self.assertEqual(('system', 'Shared note "Plan" with 1 case (copy)', 'war_room_note', 40, 2), row)

    def test_all_mirror_by_id(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = [1, 2]
        row = chat_commands.chat_commands_resolve(10, 'share-note', 'note:40 all')
        self.mocks['war_room_chat_commands_match_note'].assert_called_once_with(10, '', note_id='40')
        self.assertEqual('Shared note "Plan" with all attached cases (mirror)', row[1])
        self.assertIsNone(row[4])

    def test_all_is_refused_when_any_attached_case_is_not_writable(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = [1, 2, 3]
        with self.assertRaisesRegex(BusinessProcessingError, 'You lack full access on 1 attached case'):
            chat_commands.chat_commands_resolve(10, 'share-note', 'Plan all')
        self.mocks['war_room_note_shares_create'].assert_not_called()

    def test_listed_case_without_full_access(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = [3]
        with self.assertRaisesRegex(BusinessProcessingError, 'full access on case #3'):
            chat_commands.chat_commands_resolve(10, 'share-note', 'Plan #3')
        self.mocks['war_room_note_shares_create'].assert_not_called()

    def test_copy_failures_are_reported(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = [1, 2]
        self.mocks['war_room_note_shares_create'].return_value = (
            MagicMock(), {1: {'status': 'copied'}, 2: {'status': 'error'}})
        row = chat_commands.chat_commands_resolve(10, 'share-note', 'Plan #1 #2 copy')
        self.assertEqual('Shared note "Plan" with 2 cases (copy); copy failed on 1 case', row[1])

    def test_errors(self):
        for rest, message in (('Plan', 'Missing target cases'), ('#1', 'Usage: /share-note'),
                              ('Plan customer', 'Notes are shared with cases')):
            with self.assertRaisesRegex(BusinessProcessingError, message, msg=rest):
                chat_commands.chat_commands_resolve(10, 'share-note', rest)

    def test_copy_without_target(self):
        self.mocks['war_room_note_shares_resolve_targets'].return_value = []
        with self.assertRaisesRegex(BusinessProcessingError, 'No attached case to copy into'):
            chat_commands.chat_commands_resolve(10, 'share-note', 'Plan all copy')


class TestDispatch(TestCase):

    def test_unknown_command_is_not_handled(self):
        self.assertIsNone(chat_commands.chat_commands_resolve(10, 'nope', 'x'))


class TestReworkedSlashCommands(TestCase):

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch(f'{_CHAT}.iris_current_user', new=_USER))
        stack.enter_context(patch(f'{_CHAT}.ac_current_user_has_permission', return_value=False))
        self.emit = stack.enter_context(patch(f'{_CHAT}._emit_socket'))
        self.stack = stack

    def _patch(self, name, **kwargs):
        return self.stack.enter_context(patch(f'{_CHAT}.{name}', **kwargs))

    def test_decision_with_approvers_creates_and_notifies(self):
        users = {'alice': (2, 'Alice'), 'bob': (3, 'Bob')}
        self._patch('_resolve_user_handle', side_effect=lambda _war_room_id, handle: users[handle])
        leads = self._patch('war_room_chat_commands_lead_ids')
        create = self._patch('war_room_decisions_create',
                             return_value=SimpleNamespace(number=3, decision_id=30, title='Isolate DC'))
        notify = self._patch('war_room_chat_commands_notify_approvers')
        row = chat._resolve_slash(10, 'decision', '@alice @bob @alice Isolate DC')
        create.assert_called_once_with(
            10, {'title': 'Isolate DC', 'status': 'proposed', 'approver_ids': [2, 3]}, created_by_id=7)
        notify.assert_called_once_with(10, create.return_value, [2, 3], 7)
        leads.assert_not_called()
        self.assertEqual(('decision', 'D-3 Isolate DC', 'war_room_decision', 30, None), row)

    def test_decision_with_unknown_or_non_member_handle_is_rejected(self):
        resolve = self._patch('war_room_decisions_resolve_participant_handle',
                              side_effect=BusinessProcessingError('Unknown or non-member user @mallory'))
        create = self._patch('war_room_decisions_create')
        with self.assertRaisesRegex(BusinessProcessingError, r'^Unknown or non-member user @mallory$'):
            chat._resolve_slash(10, 'decision', '@mallory Isolate DC')
        resolve.assert_called_once_with(10, 'mallory')
        create.assert_not_called()

    def test_task_handle_outside_the_room_falls_back_to_teams_then_fails(self):
        self._patch('war_room_decisions_resolve_participant_handle',
                    side_effect=BusinessProcessingError('Unknown or non-member user @mallory'))
        with patch('app.business.war_room_teams.war_room_team_find_by_handle', return_value=None), \
                patch('app.business.war_room_tasks.war_room_task_create') as create:
            with self.assertRaisesRegex(BusinessProcessingError, 'No user or team of this war room matched @mallory'):
                chat._resolve_slash(10, 'task', '@mallory Reimage WS-042')
        create.assert_not_called()

    def test_decision_defaults_to_leads_other_than_the_author(self):
        self._patch('war_room_chat_commands_lead_ids', return_value=[7, 4])
        create = self._patch('war_room_decisions_create',
                             return_value=SimpleNamespace(number=1, decision_id=11, title='Go'))
        notify = self._patch('war_room_chat_commands_notify_approvers')
        chat._resolve_slash(10, 'decision', 'Go')
        self.assertEqual([4], create.call_args.args[1]['approver_ids'])
        self.assertEqual([4], notify.call_args.args[2])

    def test_decision_usage(self):
        self._patch('_resolve_user_handle', return_value=(2, 'Alice'))
        for rest in ('', '@alice'):
            with self.assertRaisesRegex(BusinessProcessingError, 'Usage: /decision'):
                chat._resolve_slash(10, 'decision', rest)

    def test_bare_pin_pins_the_last_message_of_the_topic(self):
        rows = [SimpleNamespace(message_id=5, deleted_at='x', is_pinned=False, body='gone',
                                author_name='A', author_login='a'),
                SimpleNamespace(message_id=4, deleted_at=None, is_pinned=False, body='Beacon  to\n1.2.3.4',
                                author_name=None, author_login='bob')]
        list_messages = self._patch('list_messages', return_value=rows)
        set_pin = self._patch('set_message_pin')
        row = chat._resolve_slash(10, 'pin', '', topic_id=8)
        self.assertEqual([8], list_messages.call_args.kwargs['topic_ids'])
        set_pin.assert_called_once_with(10, 4, True, 7, False)
        self.emit.assert_called_once_with(10, 'message:pin', {'message_id': 4, 'is_pinned': True})
        self.assertEqual(('system', 'Pinned a message from bob: Beacon to 1.2.3.4', 'war_room_chat', 4, None), row)

    def test_bare_pin_on_main_and_errors(self):
        self._patch('list_topics', return_value=[SimpleNamespace(topic_id=1, is_main=True)])
        list_messages = self._patch('list_messages', return_value=[])
        with self.assertRaisesRegex(BusinessProcessingError, 'No message to pin'):
            chat._resolve_slash(10, 'pin', '')
        self.assertEqual([1], list_messages.call_args.kwargs['topic_ids'])
        list_messages.return_value = [SimpleNamespace(message_id=4, deleted_at=None, is_pinned=True)]
        with self.assertRaisesRegex(BusinessProcessingError, 'already pinned'):
            chat._resolve_slash(10, 'pin', '')

    def test_pin_with_text_is_unchanged(self):
        self.assertEqual(('pin', 'remember', 'war_room_chat', None, None), chat._resolve_slash(10, 'pin', 'remember'))

    def test_sitrep_title_is_optional(self):
        with patch('app.business.war_room_sitreps.sitrep_draft',
                   return_value=SimpleNamespace(sitrep_id=9)) as draft:
            row = chat._resolve_slash(10, 'sitrep', '')
        title = draft.call_args.kwargs['title']
        self.assertRegex(title, r'^SitRep \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$')
        self.assertEqual(('sitrep_published', f'Drafted SitRep: {title}', 'sitrep', 9, None), row)

    def test_scope_commands_are_dispatched(self):
        resolve = self._patch('chat_commands_resolve', return_value=('system', 'ok', None, None, None))
        self.assertEqual(('system', 'ok', None, None, None), chat._resolve_slash(10, 'share-note', 'x all'))
        resolve.assert_called_once_with(10, 'share-note', 'x all')

    def test_help_lists_the_new_commands(self):
        body = chat._resolve_slash(10, 'help', '')[1]
        for cmd in ('/asset', '/ioc', '/stage', '/push', '/share-note'):
            self.assertIn(cmd, body)
