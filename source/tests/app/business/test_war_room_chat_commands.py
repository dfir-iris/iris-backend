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

"""Parsing and matching helpers of the war-room scope chat commands."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.war_room_chat import parse_slash
from app.business.war_room_chat_commands import war_room_chat_commands_decision_id
from app.business.war_room_chat_commands import war_room_chat_commands_case_count
from app.business.war_room_chat_commands import war_room_chat_commands_describe_results
from app.business.war_room_chat_commands import war_room_chat_commands_lead_ids
from app.business.war_room_chat_commands import war_room_chat_commands_match_assets
from app.business.war_room_chat_commands import war_room_chat_commands_match_iocs
from app.business.war_room_chat_commands import war_room_chat_commands_match_note
from app.business.war_room_chat_commands import war_room_chat_commands_match_stage
from app.business.war_room_chat_commands import war_room_chat_commands_match_staged
from app.business.war_room_chat_commands import war_room_chat_commands_notify_approvers
from app.business.war_room_chat_commands import war_room_chat_commands_one_source_per_type
from app.business.war_room_chat_commands import war_room_chat_commands_parse_args
from app.business.war_room_chat_commands import war_room_chat_commands_parse_target
from app.business.war_room_chat_commands import war_room_chat_commands_pick_asset_type
from app.business.war_room_chat_commands import war_room_chat_commands_pick_ioc_type
from app.business.war_room_chat_commands import war_room_chat_commands_reject_words
from app.business.war_room_chat_commands import war_room_chat_commands_resolve_targets
from app.business.war_room_chat_commands import war_room_chat_commands_take_decision_number
from app.business.war_room_chat_commands import war_room_chat_commands_tokenize
from app.models.errors import BusinessProcessingError


_MODULE = 'app.business.war_room_chat_commands'


class TestParseSlashHyphen(TestCase):

    def test_hyphenated_command(self):
        self.assertEqual(('share-note', 'Plan #3'), parse_slash('/share-note Plan #3'))

    def test_malformed_hyphens_are_not_commands(self):
        for body in ('/share-', '/-share', '/share--note x'):
            self.assertIsNone(parse_slash(body), body)


class TestTokenize(TestCase):

    def test_keeps_markup_quotes_and_options_whole(self):
        text = '[Asset "WS 042"](/case/12/assets) "two words" type:"Windows - Computer" #3,#4'
        self.assertEqual(
            ['[Asset "WS 042"](/case/12/assets)', '"two words"', 'type:"Windows - Computer"', '#3,#4'],
            war_room_chat_commands_tokenize(text),
        )

    def test_non_string(self):
        self.assertEqual([], war_room_chat_commands_tokenize(None))


class TestParseTarget(TestCase):

    def test_case_forms(self):
        for token in ('#12', '#case-12', '#case12', '#CASE-12', '[Case "Ransom"](/case/12)',
                      '[Asset "x"](/case/12/assets)', '[x](/case/12?tab=iocs)'):
            self.assertEqual(('case', 12), war_room_chat_commands_parse_target(token), token)

    def test_keywords(self):
        self.assertEqual(('all', None), war_room_chat_commands_parse_target('ALL'))
        self.assertEqual(('customer', None), war_room_chat_commands_parse_target('customer'))

    def test_not_targets(self):
        for token in ('#abc', '12', '#', '[x](/alerts/12)', 'everything', None):
            self.assertIsNone(war_room_chat_commands_parse_target(token), repr(token))


class TestParseArgs(TestCase):

    def test_subject_targets_options_words(self):
        parsed = war_room_chat_commands_parse_args('WS-042 #3,#4 #3 all type:Account extra')
        self.assertEqual('WS-042', parsed['subject'])
        self.assertEqual([3, 4], parsed['case_ids'])
        self.assertTrue(parsed['all'])
        self.assertEqual({'type': 'Account'}, parsed['options'])
        self.assertEqual(['extra'], parsed['words'])

    def test_markup_subject_restricts_case(self):
        parsed = war_room_chat_commands_parse_args('[Asset "WS 042"](/case/12/assets) #5')
        self.assertEqual('WS 042', parsed['subject'])
        self.assertEqual(12, parsed['subject_case_id'])
        self.assertEqual('asset', parsed['subject_kind'])
        self.assertEqual([5], parsed['case_ids'])

    def test_quoted_subject_and_customer(self):
        parsed = war_room_chat_commands_parse_args('"evil host" customer')
        self.assertEqual('evil host', parsed['subject'])
        self.assertTrue(parsed['customer'])

    def test_leading_target_leaves_subject_empty(self):
        parsed = war_room_chat_commands_parse_args('#3')
        self.assertIsNone(parsed['subject'])
        self.assertEqual([3], parsed['case_ids'])

    def test_url_value_is_not_an_option(self):
        parsed = war_room_chat_commands_parse_args('http://evil.example/a all')
        self.assertEqual('http://evil.example/a', parsed['subject'])
        self.assertEqual({}, parsed['options'])

    def test_comma_word_stays_a_word(self):
        parsed = war_room_chat_commands_parse_args('x a,#3')
        self.assertEqual(['a,#3'], parsed['words'])

    def test_reject_words(self):
        war_room_chat_commands_reject_words([], 'usage')
        with self.assertRaisesRegex(BusinessProcessingError, 'Quote names'):
            war_room_chat_commands_reject_words(['foo'], 'usage')

    def test_take_decision_number(self):
        self.assertEqual((7, ['Contained', 'D-8']),
                         war_room_chat_commands_take_decision_number(['Contained', 'd7', 'D-8']))
        self.assertEqual((None, ['x']), war_room_chat_commands_take_decision_number(['x']))


class TestResolveTargets(TestCase):

    def _parsed(self, case_ids=(), all_=False):
        return {'case_ids': list(case_ids), 'all': all_, 'customer': False}

    def test_all_is_attached_and_writable(self):
        self.assertEqual([1, 3], war_room_chat_commands_resolve_targets(self._parsed(all_=True), [1, 2, 3], [3, 1]))

    def test_all_without_writable_case(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'full access on none'):
            war_room_chat_commands_resolve_targets(self._parsed(all_=True), [1, 2], [])

    def test_listed_unwritable_case_is_kept_for_a_denied_row(self):
        self.assertEqual([2], war_room_chat_commands_resolve_targets(self._parsed([2]), [1, 2], [1]))

    def test_listed_case_must_be_attached(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Case #9 is not attached'):
            war_room_chat_commands_resolve_targets(self._parsed([9]), [1, 2], [1, 2])

    def test_all_plus_listed_is_deduplicated(self):
        self.assertEqual([1, 2], war_room_chat_commands_resolve_targets(self._parsed([1, 2], True), [1, 2], [1]))


class TestPickTypes(TestCase):

    _ASSET_TYPES = [(1, 'Account'), (2, 'Firewall'), (3, 'Windows - Computer')]

    def test_explicit_asset_type(self):
        self.assertEqual((2, 'Firewall'),
                         war_room_chat_commands_pick_asset_type('fw', self._ASSET_TYPES, 'firewall'))
        with self.assertRaisesRegex(BusinessProcessingError, 'Unknown asset type'):
            war_room_chat_commands_pick_asset_type('fw', self._ASSET_TYPES, 'router')

    def test_account_heuristic(self):
        self.assertEqual((1, 'Account'), war_room_chat_commands_pick_asset_type('CORP\\bob', self._ASSET_TYPES))
        self.assertEqual((1, 'Account'), war_room_chat_commands_pick_asset_type('bob@corp', self._ASSET_TYPES))

    def test_default_asset_type(self):
        self.assertEqual((3, 'Windows - Computer'),
                         war_room_chat_commands_pick_asset_type('WS-042', self._ASSET_TYPES))
        self.assertEqual((7, 'Other'),
                         war_room_chat_commands_pick_asset_type('x', [(6, 'Router'), (7, 'Other')]))
        self.assertEqual((6, 'Router'), war_room_chat_commands_pick_asset_type('x', [(6, 'Router')]))
        with self.assertRaisesRegex(BusinessProcessingError, 'No asset type'):
            war_room_chat_commands_pick_asset_type('x', [])

    def test_ioc_type(self):
        types = [{'type_id': 1, 'type_name': 'ip-dst'}, {'type_id': 2, 'type_name': 'other'}]
        self.assertEqual(1, war_room_chat_commands_pick_ioc_type('10.0.0.1', types)['type_id'])
        self.assertEqual(2, war_room_chat_commands_pick_ioc_type('10.0.0.1', types, 'OTHER')['type_id'])
        with self.assertRaisesRegex(BusinessProcessingError, 'Unknown IOC type'):
            war_room_chat_commands_pick_ioc_type('10.0.0.1', types, 'nope')
        with self.assertRaisesRegex(BusinessProcessingError, 'Could not detect'):
            war_room_chat_commands_pick_ioc_type('free text', types)


class TestMatchStage(TestCase):

    _STAGES = [{'id': 1, 'name': 'Under'}, {'id': 2, 'name': 'Under investigation'},
               {'id': 3, 'name': 'Contained'}]

    def test_longest_prefix_wins(self):
        stage, cleared, rest = war_room_chat_commands_match_stage(
            ['under', 'Investigation', 'beacon', 'seen'], self._STAGES)
        self.assertEqual(2, stage['id'])
        self.assertFalse(cleared)
        self.assertEqual(['beacon', 'seen'], rest)

    def test_clear(self):
        self.assertEqual((None, True, ['why']), war_room_chat_commands_match_stage(['none', 'why'], self._STAGES))

    def test_unknown_and_missing(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Unknown stage "Eradicated". Stages: Under'):
            war_room_chat_commands_match_stage(['Eradicated'], self._STAGES)
        with self.assertRaisesRegex(BusinessProcessingError, 'Missing stage'):
            war_room_chat_commands_match_stage([], self._STAGES)


class TestMatching(TestCase):

    def test_assets_exact_case_insensitive_and_case_restriction(self):
        assets = [{'asset_id': 1, 'asset_name': 'WS-042', 'case_id': 1},
                  {'asset_id': 2, 'asset_name': 'ws-042 ', 'case_id': 2},
                  {'asset_id': 3, 'asset_name': 'WS-0420', 'case_id': 1}]
        self.assertEqual([1, 2], [a['asset_id'] for a in war_room_chat_commands_match_assets(assets, 'Ws-042')])
        self.assertEqual([2], [a['asset_id'] for a in war_room_chat_commands_match_assets(assets, 'WS-042', 2)])

    def test_iocs(self):
        iocs = [{'ioc_id': 1, 'ioc_value': 'Evil.com', 'case_id': 1}, {'ioc_id': 2, 'ioc_value': 'x', 'case_id': 1}]
        self.assertEqual([1], [i['ioc_id'] for i in war_room_chat_commands_match_iocs(iocs, 'evil.com')])

    def test_staged(self):
        staged = [{'id': 1, 'object_type': 'asset', 'payload': {'asset_name': 'WS-1'}},
                  {'id': 2, 'object_type': 'ioc', 'payload': {'ioc_value': 'ws-1'}},
                  {'id': 3, 'object_type': 'asset', 'payload': None}]
        self.assertEqual([1, 2], [s['id'] for s in war_room_chat_commands_match_staged(staged, 'ws-1')])
        self.assertEqual([2], [s['id'] for s in war_room_chat_commands_match_staged(staged, 'ws-1', 'ioc')])

    def test_one_source_per_type(self):
        assets = [{'asset_id': 1, 'asset_type_id': 9}, {'asset_id': 2, 'asset_type_id': 9},
                  {'asset_id': 3, 'asset_type_id': 4}]
        self.assertEqual([1, 3], [a['asset_id'] for a in war_room_chat_commands_one_source_per_type(assets)])


class TestMatchNote(TestCase):

    _NOTES = [SimpleNamespace(note_id=1, title='Containment plan'),
              SimpleNamespace(note_id=2, title='Containment plan v2'),
              SimpleNamespace(note_id=3, title='Comms'),
              SimpleNamespace(note_id=4, title='Comms')]

    def _match(self, query, note_id=None):
        with patch(f'{_MODULE}.war_room_note_list', return_value=self._NOTES):
            return war_room_chat_commands_match_note(10, query, note_id=note_id)

    def test_exact_title_wins_over_substring(self):
        self.assertEqual(1, self._match('containment PLAN').note_id)

    def test_unique_substring(self):
        self.assertEqual(2, self._match('v2').note_id)

    def test_by_id(self):
        self.assertEqual(3, self._match('', note_id='3').note_id)
        with self.assertRaisesRegex(BusinessProcessingError, 'Note #9 not found'):
            self._match('', note_id='9')

    def test_ambiguous(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Several notes are titled "comms"; use one of note:3, note:4'):
            self._match('comms')
        with self.assertRaisesRegex(BusinessProcessingError, 'matches 2 notes'):
            self._match('containment p')

    def test_not_found(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'No note matches'):
            self._match('zzz')
        with self.assertRaisesRegex(BusinessProcessingError, 'Missing note title'):
            self._match('')


class TestDescribeResults(TestCase):

    def test_all_buckets(self):
        results = [
            {'case_id': 1, 'status': 'created'},
            {'case_id': 2, 'status': 'exists'},
            {'case_id': 3, 'status': 'denied'},
            {'case_id': 4, 'status': 'error', 'message': 'boom'},
            {'case_id': None, 'status': 'denied'},
        ]
        text, succeeded = war_room_chat_commands_describe_results(results, 'added to')
        self.assertEqual('added to 1 case; already in 1 case; denied on 1 case, 1 not found; failed on 1 case (boom)', text)
        self.assertEqual(2, succeeded)

    def test_updated_and_unchanged(self):
        results = [{'case_id': 1, 'status': 'updated'}, {'case_id': 1, 'status': 'updated'},
                   {'case_id': 2, 'status': 'unchanged'}]
        text, succeeded = war_room_chat_commands_describe_results(results, 'set in')
        self.assertEqual('set in 1 case; unchanged in 1 case', text)
        self.assertEqual(2, succeeded)

    def test_never_names_a_case(self):
        results = [{'case_id': 101, 'status': 'created'}, {'case_id': 202, 'status': 'created'},
                   {'case_id': 303, 'status': 'denied'}, {'case_id': 404, 'status': 'error', 'message': 'boom'},
                   {'case_id': 505, 'status': 'error', 'message': 'boom'}]
        text, succeeded = war_room_chat_commands_describe_results(results, 'pushed to')
        self.assertEqual('pushed to 2 cases; denied on 1 case; failed on 2 cases (boom)', text)
        for case_id in ('101', '202', '303', '404', '505'):
            self.assertNotIn(case_id, text)
        self.assertEqual(2, succeeded)

    def test_case_count(self):
        self.assertEqual('1 case', war_room_chat_commands_case_count([7]))
        self.assertEqual('3 cases', war_room_chat_commands_case_count([7, 8, 9]))

    def test_nothing_succeeded(self):
        text, succeeded = war_room_chat_commands_describe_results([{'case_id': 5, 'status': 'denied'}], 'added to')
        self.assertEqual('denied on 1 case', text)
        self.assertEqual(0, succeeded)


class TestDecisionHelpers(TestCase):

    def test_decision_id_by_number(self):
        decisions = [SimpleNamespace(number=1, decision_id=11), SimpleNamespace(number=2, decision_id=12)]
        with patch(f'{_MODULE}.war_room_decisions_list', return_value=decisions):
            self.assertEqual(12, war_room_chat_commands_decision_id(5, 2))
            with self.assertRaisesRegex(BusinessProcessingError, 'Decision D-3 not found'):
                war_room_chat_commands_decision_id(5, 3)

    def test_lead_ids(self):
        rows = [SimpleNamespace(user_id=1, role='lead'), SimpleNamespace(user_id=2, role='responder'),
                SimpleNamespace(user_id=3, role='lead')]
        with patch(f'{_MODULE}.war_room_members_list', return_value=rows):
            self.assertEqual([1, 3], war_room_chat_commands_lead_ids(5))

    def test_notify_approvers(self):
        decision = SimpleNamespace(number=4, decision_id=40, title='Isolate the DC')
        with patch('app.iris_engine.notifications.service.notify_many') as notify:
            war_room_chat_commands_notify_approvers(5, decision, [2, 3], 1)
        kwargs = notify.call_args.kwargs
        self.assertEqual([2, 3], kwargs['user_ids'])
        self.assertEqual('Approval requested: D-4', kwargs['title'])
        self.assertEqual('/war-rooms/5/decisions?d=40', kwargs['link'])
        self.assertEqual([1], kwargs['exclude_user_ids'])

    def test_notify_without_approvers_or_on_failure(self):
        decision = SimpleNamespace(number=4, decision_id=40, title='t')
        with patch('app.iris_engine.notifications.service.notify_many') as notify:
            war_room_chat_commands_notify_approvers(5, decision, [], 1)
        notify.assert_not_called()
        with patch('app.iris_engine.notifications.service.notify_many', side_effect=RuntimeError('db')):
            war_room_chat_commands_notify_approvers(5, decision, [2], 1)
