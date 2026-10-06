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

"""Unit tests for the war-room decision register (no database)."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app.business import war_room_decisions as decisions
from app.datamgmt.war_rooms import war_room_decisions_db as decisions_db
from app.models.errors import BusinessProcessingError


_BIZ = 'app.business.war_room_decisions'
_DB = 'app.datamgmt.war_rooms.war_room_decisions_db'


def _decision(**kwargs):
    fields = {
        'decision_id': 1, 'war_room_id': 7, 'number': 1, 'title': 'Isolate DC01',
        'rationale': None, 'status': 'proposed', 'target_at': None, 'owner_id': None,
        'supersedes_id': None, 'chat_message_id': None, 'decided_at': None,
        'decided_by_id': None, 'implemented_at': None, 'implemented_by_id': None,
        'created_at': None, 'created_by_id': 3, 'updated_at': None,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class TestParseTargetAt(TestCase):

    def test_empty_values_clear_the_target(self):
        self.assertIsNone(decisions.war_room_decisions_parse_target_at(None))
        self.assertIsNone(decisions.war_room_decisions_parse_target_at(''))

    def test_z_suffix_is_utc(self):
        parsed = decisions.war_room_decisions_parse_target_at('2026-10-06T14:30:00Z')
        self.assertEqual(parsed, datetime.datetime(2026, 10, 6, 14, 30))
        self.assertIsNone(parsed.tzinfo)

    def test_offset_is_converted_to_naive_utc(self):
        parsed = decisions.war_room_decisions_parse_target_at('2026-10-06T16:30:00+02:00')
        self.assertEqual(parsed, datetime.datetime(2026, 10, 6, 14, 30))
        self.assertIsNone(parsed.tzinfo)

    def test_naive_value_is_taken_as_utc(self):
        parsed = decisions.war_room_decisions_parse_target_at('2026-10-06T14:30')
        self.assertEqual(parsed, datetime.datetime(2026, 10, 6, 14, 30))

    def test_garbage_is_rejected(self):
        for raw in ('tomorrow', '2026-13-01T00:00', 12, True, ['2026-10-06'], 'x' * 65):
            with self.subTest(raw=raw):
                with self.assertRaises(BusinessProcessingError):
                    decisions.war_room_decisions_parse_target_at(raw)

    def test_pre_epoch_is_rejected(self):
        with self.assertRaises(BusinessProcessingError):
            decisions.war_room_decisions_parse_target_at('1900-01-01T00:00:00Z')


class TestParseIdList(TestCase):

    def test_dedupes_and_keeps_order(self):
        self.assertEqual(decisions.war_room_decisions_parse_id_list([3, 1, 3, 2], 'x', 10), [3, 1, 2])

    def test_none_is_empty(self):
        self.assertEqual(decisions.war_room_decisions_parse_id_list(None, 'x', 10), [])

    def test_rejects_bools_strings_and_non_positive(self):
        for raw in ([True], ['1'], [0], [-4], [1.5], 'not-a-list', {'a': 1}):
            with self.subTest(raw=raw):
                with self.assertRaises(BusinessProcessingError):
                    decisions.war_room_decisions_parse_id_list(raw, 'x', 10)

    def test_caps_length(self):
        with self.assertRaises(BusinessProcessingError):
            decisions.war_room_decisions_parse_id_list([1, 2, 3], 'x', 2)


class TestNumbering(TestCase):

    def test_next_number_starts_at_one(self):
        with patch(f'{_DB}.db') as db:
            db.session.query.return_value.filter.return_value.scalar.return_value = None
            self.assertEqual(decisions_db._next_number(7), 1)

    def test_next_number_follows_the_room_maximum(self):
        with patch(f'{_DB}.db') as db:
            db.session.query.return_value.filter.return_value.scalar.return_value = 4
            self.assertEqual(decisions_db._next_number(7), 5)

    def test_insert_assigns_the_next_number(self):
        built = []

        def _build():
            built.append(SimpleNamespace(war_room_id=7, decision_id=11, number=None))
            return built[-1]

        with patch(f'{_DB}.db'), patch(f'{_DB}._next_number', return_value=3), \
                patch(f'{_DB}._add_links') as add_links:
            decision = decisions_db.war_room_decisions_db_insert(_build, [5], [9], [])
        self.assertEqual(decision.number, 3)
        add_links.assert_called_once_with(11, [5], [9], [])

    def test_insert_retries_with_a_fresh_instance_on_race(self):
        built = []

        def _build():
            built.append(SimpleNamespace(war_room_id=7, decision_id=11, number=None))
            return built[-1]

        with patch(f'{_DB}.db') as db, \
                patch(f'{_DB}._next_number', side_effect=[3, 4]), patch(f'{_DB}._add_links'):
            db.session.flush.side_effect = [IntegrityError('x', {}, Exception()), None]
            decision = decisions_db.war_room_decisions_db_insert(_build, [], [], [])
        self.assertEqual(len(built), 2)
        self.assertIsNot(built[0], built[1])
        self.assertEqual(decision.number, 4)
        db.session.rollback.assert_called_once()

    def test_insert_gives_up_after_two_races(self):
        with patch(f'{_DB}.db') as db, \
                patch(f'{_DB}._next_number', return_value=3), patch(f'{_DB}._add_links'):
            db.session.flush.side_effect = IntegrityError('x', {}, Exception())
            with self.assertRaises(BusinessProcessingError):
                decisions_db.war_room_decisions_db_insert(
                    lambda: SimpleNamespace(war_room_id=7, decision_id=None, number=None), [], [], [])


class TestVoteStateMachine(TestCase):

    def test_all_approved_approves(self):
        self.assertEqual(decisions.war_room_decisions_status_after_vote('proposed', ['approved', 'approved']),
                         'approved')

    def test_any_rejection_rejects(self):
        self.assertEqual(decisions.war_room_decisions_status_after_vote('proposed', ['approved', 'rejected', None]),
                         'rejected')

    def test_pending_votes_keep_proposed(self):
        self.assertIsNone(decisions.war_room_decisions_status_after_vote('proposed', ['approved', None]))

    def test_no_approvers_never_moves(self):
        self.assertIsNone(decisions.war_room_decisions_status_after_vote('proposed', []))

    def test_only_proposed_moves(self):
        for status in ('approved', 'rejected', 'superseded'):
            with self.subTest(status=status):
                self.assertIsNone(decisions.war_room_decisions_status_after_vote(status, ['rejected']))

    def _vote(self, decision, verdicts, verdict='approved', approver=None):
        approver = approver if approver is not None else SimpleNamespace(verdict=None, comment=None,
                                                                           responded_at=None)
        with patch(f'{_BIZ}.war_room_decisions_db_get', return_value=decision), \
                patch(f'{_BIZ}.war_room_decisions_db_get_approver', return_value=approver), \
                patch(f'{_BIZ}.war_room_decisions_db_verdicts', return_value=verdicts), \
                patch(f'{_BIZ}.db'), patch(f'{_BIZ}.track_activity'):
            return decisions.war_room_decisions_vote(7, 1, 5, verdict, ' looks good ')

    def test_last_approval_approves_and_records_the_decider(self):
        decision = _decision()
        result, changed = self._vote(decision, ['approved', 'approved'])
        self.assertTrue(changed)
        self.assertEqual(result.status, 'approved')
        self.assertEqual(result.decided_by_id, 5)
        self.assertIsNotNone(result.decided_at)

    def test_rejection_rejects(self):
        result, changed = self._vote(_decision(), ['rejected', None], verdict='rejected')
        self.assertTrue(changed)
        self.assertEqual(result.status, 'rejected')

    def test_partial_vote_keeps_proposed(self):
        approver = SimpleNamespace(verdict=None, comment=None, responded_at=None)
        result, changed = self._vote(_decision(), ['approved', None], approver=approver)
        self.assertFalse(changed)
        self.assertEqual(result.status, 'proposed')
        self.assertEqual(approver.verdict, 'approved')
        self.assertEqual(approver.comment, 'looks good')

    def test_vote_on_approved_decision_does_not_change_status(self):
        result, changed = self._vote(_decision(status='approved'), ['rejected'], verdict='rejected')
        self.assertFalse(changed)
        self.assertEqual(result.status, 'approved')

    def test_vote_on_superseded_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            self._vote(_decision(status='superseded'), ['approved'])

    def test_non_approver_is_refused(self):
        with patch(f'{_BIZ}.war_room_decisions_db_get', return_value=_decision()), \
                patch(f'{_BIZ}.war_room_decisions_db_get_approver', return_value=None):
            with self.assertRaises(BusinessProcessingError):
                decisions.war_room_decisions_vote(7, 1, 5, 'approved')

    def test_invalid_verdict_is_refused(self):
        for verdict in ('maybe', None, 1, True):
            with self.subTest(verdict=verdict):
                with self.assertRaises(BusinessProcessingError):
                    decisions.war_room_decisions_vote(7, 1, 5, verdict)


class TestOverdue(TestCase):

    _NOW = datetime.datetime(2026, 10, 6, 12, 0)

    def test_past_target_is_overdue(self):
        d = _decision(target_at=self._NOW - datetime.timedelta(minutes=1))
        self.assertTrue(decisions.war_room_decisions_is_overdue(d, self._NOW))

    def test_future_target_is_not_overdue(self):
        d = _decision(target_at=self._NOW + datetime.timedelta(minutes=1))
        self.assertFalse(decisions.war_room_decisions_is_overdue(d, self._NOW))

    def test_no_target_is_not_overdue(self):
        self.assertFalse(decisions.war_room_decisions_is_overdue(_decision(), self._NOW))

    def test_implemented_is_not_overdue(self):
        d = _decision(target_at=self._NOW - datetime.timedelta(days=1), implemented_at=self._NOW)
        self.assertFalse(decisions.war_room_decisions_is_overdue(d, self._NOW))

    def test_closed_statuses_are_not_overdue(self):
        for status in ('rejected', 'superseded'):
            with self.subTest(status=status):
                d = _decision(status=status, target_at=self._NOW - datetime.timedelta(days=1))
                self.assertFalse(decisions.war_room_decisions_is_overdue(d, self._NOW))

    def test_approved_but_not_implemented_is_overdue(self):
        d = _decision(status='approved', target_at=self._NOW - datetime.timedelta(days=1))
        self.assertTrue(decisions.war_room_decisions_is_overdue(d, self._NOW))


class TestCreateValidation(TestCase):

    def _create(self, raw, attached=frozenset({10}), members=frozenset({5}), asset_cases=None):
        captured = {}

        def _insert(build, approver_ids, case_ids, asset_ids, superseded_id=None):
            decision = build()
            decision.decision_id = 1
            decision.number = 1
            captured.update(approver_ids=approver_ids, case_ids=case_ids, asset_ids=asset_ids)
            return decision

        with patch(f'{_BIZ}.war_room_decisions_db_member_ids', return_value=set(members)), \
                patch('app.business.war_rooms_access.ac_fast_check_user_has_war_room_access',
                      return_value=None), \
                patch(f'{_BIZ}.war_room_decisions_db_attached_case_ids', return_value=set(attached)), \
                patch(f'{_BIZ}.war_room_decisions_db_asset_case_ids', return_value=asset_cases or {}), \
                patch(f'{_BIZ}.war_room_decisions_db_get', return_value=None), \
                patch(f'{_BIZ}.war_room_decisions_db_insert', side_effect=_insert), \
                patch(f'{_BIZ}.track_activity'):
            return decisions.war_room_decisions_create(7, raw, created_by_id=3), captured

    def test_happy_path(self):
        decision, captured = self._create({
            'title': '  Isolate DC01 ', 'target_at': '2026-10-06T18:00:00Z',
            'approver_ids': [5], 'case_ids': [10],
        })
        self.assertEqual(decision.title, 'Isolate DC01')
        self.assertEqual(decision.status, 'proposed')
        self.assertEqual(decision.target_at, datetime.datetime(2026, 10, 6, 18, 0))
        self.assertEqual(captured['case_ids'], [10])
        self.assertIsNone(decision.decided_at)

    def test_created_approved_records_decider(self):
        decision, _ = self._create({'title': 'x', 'status': 'approved'})
        self.assertEqual(decision.decided_by_id, 3)
        self.assertIsNotNone(decision.decided_at)

    def test_rejects_bad_inputs(self):
        cases = [
            {'title': ''},
            {'title': 'x' * 257},
            {'title': 'x', 'status': 'rejected'},
            {'title': 'x', 'case_ids': [11]},
            {'title': 'x', 'approver_ids': [99]},
            {'title': 'x', 'owner_id': True},
            {'title': 'x', 'asset_ids': [42]},
            {'title': 'x', 'supersedes_id': 4},
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(BusinessProcessingError):
                    self._create(raw)

    def test_asset_of_attached_case_is_accepted(self):
        _, captured = self._create({'title': 'x', 'asset_ids': [42]}, asset_cases={42: 10})
        self.assertEqual(captured['asset_ids'], [42])


class TestSerializerHidesInaccessibleData(TestCase):

    def _serialize(self, readable):
        d = _decision(decision_id=1, owner_id=5)
        with patch(f'{_BIZ}.war_room_decisions_db_case_links',
                   return_value=[SimpleNamespace(decision_id=1, case_id=10),
                                 SimpleNamespace(decision_id=1, case_id=20)]), \
                patch(f'{_BIZ}.war_room_decisions_db_asset_links',
                      return_value=[SimpleNamespace(decision_id=1, asset_id=100, asset_name='WKS-042', case_id=10),
                                    SimpleNamespace(decision_id=1, asset_id=200, asset_name='SECRET-HOST',
                                                    case_id=20)]), \
                patch(f'{_BIZ}.war_room_decisions_db_approver_rows', return_value=[]), \
                patch(f'{_BIZ}.war_room_decisions_db_case_names',
                      return_value={10: 'Visible case', 20: 'Secret case'}), \
                patch(f'{_BIZ}.war_room_decisions_db_user_names', return_value={5: 'alice'}), \
                patch(f'{_BIZ}.war_room_decisions_db_numbers', return_value={}), \
                patch(f'{_BIZ}.war_room_decisions_db_superseded_by', return_value={}):
            return decisions.war_room_decisions_serialize([d], readable)[0]

    def test_unreadable_case_and_its_assets_are_reduced_to_ids(self):
        out = self._serialize({10})
        self.assertEqual(out['cases'], [
            {'case_id': 10, 'case_name': 'Visible case', 'accessible': True},
            {'case_id': 20, 'accessible': False},
        ])
        self.assertEqual(out['assets'][0]['asset_name'], 'WKS-042')
        self.assertEqual(out['assets'][1], {'asset_id': 200, 'accessible': False})
        self.assertNotIn('Secret case', repr(out))
        self.assertNotIn('SECRET-HOST', repr(out))
        self.assertEqual(out['ref'], 'D-1')
        self.assertEqual(out['owner_name'], 'alice')

    def test_nothing_readable(self):
        out = self._serialize(set())
        self.assertTrue(all(not c['accessible'] for c in out['cases']))
        self.assertTrue(all(not a['accessible'] for a in out['assets']))
        self.assertNotIn('Visible case', repr(out))
        self.assertNotIn('WKS-042', repr(out))

    def test_referenced_case_ids_include_asset_cases(self):
        with patch(f'{_BIZ}.war_room_decisions_db_case_links',
                   return_value=[SimpleNamespace(decision_id=1, case_id=10)]), \
                patch(f'{_BIZ}.war_room_decisions_db_asset_links',
                      return_value=[SimpleNamespace(decision_id=1, asset_id=1, asset_name='a', case_id=30)]):
            self.assertEqual(decisions.war_room_decisions_referenced_case_ids([_decision()]), {10, 30})

    def test_empty_list(self):
        self.assertEqual(decisions.war_room_decisions_serialize([], {1}), [])


class TestListFilters(TestCase):

    def test_unknown_statuses_short_circuit(self):
        with patch(f'{_BIZ}.war_room_decisions_db_list') as db_list:
            self.assertEqual(decisions.war_room_decisions_list(7, status=['bogus']), [])
        db_list.assert_not_called()

    def test_valid_statuses_and_q_are_forwarded(self):
        db_list = MagicMock(return_value=[])
        with patch(f'{_BIZ}.war_room_decisions_db_list', db_list):
            decisions.war_room_decisions_list(7, status=['proposed', 'bogus'], q='  dc  ', case_id=3)
        db_list.assert_called_once_with(7, statuses=['proposed'], q='dc', case_id=3)


class TestParticipants(TestCase):

    def _access(self, granted):
        return patch('app.business.war_rooms_access.ac_fast_check_user_has_war_room_access',
                     side_effect=lambda user_id, *_: 'read' if user_id in granted else None)

    def test_member_or_room_access_is_a_participant(self):
        with patch(f'{_BIZ}.war_room_decisions_db_member_ids', return_value={5}), self._access({6}):
            self.assertTrue(decisions.war_room_decisions_is_participant(7, 5))
            self.assertTrue(decisions.war_room_decisions_is_participant(7, 6))
            self.assertFalse(decisions.war_room_decisions_is_participant(7, 8))

    def test_validate_participants_error_does_not_echo_the_id(self):
        with patch(f'{_BIZ}.war_room_decisions_db_member_ids', return_value={5}), self._access(set()):
            with self.assertRaises(BusinessProcessingError) as ctx:
                decisions._validate_participants(7, [5, 1234])
        self.assertEqual('Unknown or non-member user', ctx.exception.get_message())

    def test_resolve_handle_returns_the_first_participant_match(self):
        rows = [SimpleNamespace(id=2, user='jdoe', name='John Doe'),
                SimpleNamespace(id=9, user='jdoe2', name='John Doe')]
        with patch(f'{_BIZ}.war_room_decisions_db_users_by_handle', return_value=rows) as lookup, \
                patch(f'{_BIZ}.war_room_decisions_db_member_ids', return_value={9}), self._access(set()):
            self.assertEqual((9, 'John Doe'),
                             decisions.war_room_decisions_resolve_participant_handle(7, ' John Doe '))
        lookup.assert_called_once_with('John Doe')

    def test_resolve_handle_same_error_for_unknown_and_non_member(self):
        outsider = [SimpleNamespace(id=2, user='mallory', name=None)]
        for rows in ([], outsider):
            with self.subTest(rows=rows), \
                    patch(f'{_BIZ}.war_room_decisions_db_users_by_handle', return_value=rows), \
                    patch(f'{_BIZ}.war_room_decisions_db_member_ids', return_value=set()), \
                    self._access(set()):
                with self.assertRaises(BusinessProcessingError) as ctx:
                    decisions.war_room_decisions_resolve_participant_handle(7, 'mallory')
                self.assertEqual('Unknown or non-member user @mallory', ctx.exception.get_message())

    def test_resolve_handle_rejects_empty(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'Empty user mention'):
            decisions.war_room_decisions_resolve_participant_handle(7, '  ')

    def test_parse_case_ids(self):
        self.assertEqual([3, 4], decisions.war_room_decisions_parse_case_ids([3, 4, 3]))
        with self.assertRaises(BusinessProcessingError):
            decisions.war_room_decisions_parse_case_ids('all')
