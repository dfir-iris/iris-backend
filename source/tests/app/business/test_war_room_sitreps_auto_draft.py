#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Unit tests for the SitRep cadence and auto-draft helpers (no DB)."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.war_room_sitreps import sitrep_auto_draft
from app.business.war_room_sitreps import sitrep_auto_draft_render
from app.business.war_room_sitreps import sitrep_cadence_compute
from app.business.war_room_sitreps import sitrep_cadence_set
from app.business.war_room_sitreps import sitrep_cadence_validate
from app.business.war_room_sitreps import sitrep_md_escape
from app.models.errors import BusinessProcessingError

_MODULE = 'app.business.war_room_sitreps'
_NOW = datetime.datetime(2026, 10, 6, 12, 0)


class TestCadenceValidate(TestCase):

    def test_null_cadence_clears_reminder(self):
        self.assertEqual((None, None), sitrep_cadence_validate(None, 30))

    def test_bounds(self):
        self.assertEqual((15, None), sitrep_cadence_validate(15, None))
        self.assertEqual((10080, 0), sitrep_cadence_validate(10080, 0))
        for bad in (14, 10081, 0, -5, True, '60', 60.0):
            with self.assertRaises(BusinessProcessingError, msg=repr(bad)):
                sitrep_cadence_validate(bad, None)

    def test_reminder_bounds(self):
        self.assertEqual((60, 60), sitrep_cadence_validate(60, 60))
        for bad in (61, -1, False, '10'):
            with self.assertRaises(BusinessProcessingError, msg=repr(bad)):
                sitrep_cadence_validate(60, bad)


class TestCadenceCompute(TestCase):

    def test_no_cadence(self):
        out = sitrep_cadence_compute(None, None, None, _NOW, _NOW)
        self.assertIsNone(out['next_due_at'])
        self.assertFalse(out['is_overdue'])

    def test_from_last_published(self):
        last = _NOW - datetime.timedelta(minutes=30)
        out = sitrep_cadence_compute(60, 10, last, _NOW - datetime.timedelta(days=3), _NOW)
        self.assertEqual((last + datetime.timedelta(minutes=60)).isoformat(), out['next_due_at'])
        self.assertEqual(last.isoformat(), out['last_published_at'])
        self.assertFalse(out['is_overdue'])
        self.assertEqual(10, out['reminder_minutes'])

    def test_overdue_from_war_room_creation(self):
        created = _NOW - datetime.timedelta(hours=5)
        out = sitrep_cadence_compute(60, None, None, created, _NOW)
        self.assertEqual((created + datetime.timedelta(hours=1)).isoformat(), out['next_due_at'])
        self.assertTrue(out['is_overdue'])
        self.assertIsNone(out['last_published_at'])


class TestCadenceSet(TestCase):

    def _run(self, war_room, *args, **kwargs):
        with patch(f'{_MODULE}.war_room_get', return_value=war_room), \
                patch(f'{_MODULE}.db'), patch(f'{_MODULE}.track_activity'), \
                patch(f'{_MODULE}.sitrep_cadence_get', return_value={'ok': True}):
            return sitrep_cadence_set(*args, **kwargs)

    def test_keeps_reminder_when_omitted_and_fits(self):
        room = SimpleNamespace(sitrep_cadence_minutes=60, sitrep_reminder_minutes=10)
        self._run(room, 1, 120)
        self.assertEqual((120, 10), (room.sitrep_cadence_minutes, room.sitrep_reminder_minutes))

    def test_drops_reminder_when_omitted_and_too_large(self):
        room = SimpleNamespace(sitrep_cadence_minutes=120, sitrep_reminder_minutes=90)
        self._run(room, 1, 60)
        self.assertEqual((60, None), (room.sitrep_cadence_minutes, room.sitrep_reminder_minutes))

    def test_invalid_does_not_mutate(self):
        room = SimpleNamespace(sitrep_cadence_minutes=60, sitrep_reminder_minutes=10)
        with self.assertRaises(BusinessProcessingError):
            self._run(room, 1, 5, reminder_minutes=None)
        self.assertEqual(60, room.sitrep_cadence_minutes)

    def test_explicit_clear(self):
        room = SimpleNamespace(sitrep_cadence_minutes=60, sitrep_reminder_minutes=10)
        self._run(room, 1, None)
        self.assertEqual((None, None), (room.sitrep_cadence_minutes, room.sitrep_reminder_minutes))


class TestMarkdownEscape(TestCase):

    def test_pipes_and_newlines(self):
        self.assertEqual('a \\| b c', sitrep_md_escape('a | b\nc'))

    def test_markdown_specials(self):
        self.assertEqual('\\*x\\* \\[l\\](u) \\<b\\> \\#h \\_ \\` \\\\',
                         sitrep_md_escape('*x* [l](u) <b> #h _ ` \\'))

    def test_none(self):
        self.assertEqual('', sitrep_md_escape(None))


def _sections(case_name='Case | A', reason='line1\nline2'):
    return {
        'changes': [{'type': 'stage', 'at': _NOW.isoformat(), 'case_id': 1, 'case_name': case_name,
                     'asset_id': 5, 'asset_name': 'srv|01', 'from_stage_name': None,
                     'to_stage_name': 'Isolated', 'reason': reason, 'changed_by_name': 'Ann'}],
        'cases': [{'case_id': 1, 'case_name': case_name, 'customer_name': 'ACME',
                   'state_name': 'Open', 'assets_total': 3, 'assets_done': 1,
                   'assets_exception': 1, 'tasks_open': 2}],
        'decisions': [{'decision_id': 9, 'ref': 'D-2', 'title': 'Reset *all*', 'status': 'approved',
                       'target_at': _NOW.isoformat(), 'is_overdue': True, 'owner_name': 'Bob'}],
        'exceptions': [{'asset_id': 6, 'asset_name': 'legacy', 'case_id': 1, 'case_name': case_name,
                        'stage_name': 'Unpatched', 'reason': 'vendor EOL', 'decision_ref': 'D-2'}],
        'next_actions': [{'type': 'task', 'task_id': 3, 'title': 'Call <ISP>',
                          'due_at': None, 'assignee_name': None}],
    }


class TestAutoDraftRender(TestCase):

    def test_sections_present_and_escaped(self):
        md = sitrep_auto_draft_render('Wave | 1', _NOW, _NOW, _sections())
        for heading in ('## Summary', '## Changes since last SitRep', '## Per-case status',
                        '## Decisions', '## Exceptions', '## Next actions'):
            self.assertIn(heading, md)
        self.assertIn('# SitRep — Wave \\| 1', md)
        self.assertIn('| #1 Case \\| A | ACME | Open | 3 | 1 | 1 | 2 |', md)
        self.assertIn('**srv\\|01**', md)
        self.assertIn('no stage → Isolated — line1 line2', md)
        self.assertIn('**D-2** Reset \\*all\\* — approved', md)
        self.assertIn('**overdue**', md)
        self.assertIn('vendor EOL (D-2)', md)
        self.assertIn('- [ ] Call \\<ISP\\>', md)

    def test_table_rows_have_constant_column_count(self):
        md = sitrep_auto_draft_render('WR', _NOW, _NOW, _sections(case_name='a|b|c\n|d'))
        rows = [line for line in md.splitlines() if line.startswith('| #')]
        self.assertEqual(1, len(rows))
        unescaped_pipes = rows[0].replace('\\|', '').count('|')
        self.assertEqual(8, unescaped_pipes)

    def test_empty_sections(self):
        empty = {'changes': [], 'cases': [], 'decisions': [], 'exceptions': [], 'next_actions': []}
        md = sitrep_auto_draft_render('WR', None, _NOW, empty)
        self.assertIn('_No changes._', md)
        self.assertIn('_No readable attached case._', md)
        self.assertIn('_No open decision._', md)
        self.assertIn('_No exception._', md)
        self.assertIn('_No pending action._', md)


class TestAutoDraftScope(TestCase):

    def test_only_readable_cases_are_queried(self):
        room = SimpleNamespace(name='WR', created_at=_NOW - datetime.timedelta(days=1))
        mocks = {
            'war_room_get': MagicMock(return_value=room),
            'sitreps_db_last_published_at': MagicMock(return_value=None),
            'sitreps_db_attached_case_ids': MagicMock(return_value=[1, 2, 3]),
            'sitreps_db_decisions': MagicMock(return_value=[]),
            'sitreps_db_case_rows': MagicMock(return_value=[]),
            'sitreps_db_exception_assets': MagicMock(return_value=[]),
            'sitreps_db_open_tasks': MagicMock(return_value=[]),
            'sitreps_db_stage_transitions': MagicMock(return_value=[]),
            'sitreps_db_cases_attached_since': MagicMock(return_value=[]),
        }
        patchers = [patch(f'{_MODULE}.{name}', mock) for name, mock in mocks.items()]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

        # Case 99 is readable but not attached; case 2 is attached but unreadable.
        draft = sitrep_auto_draft(1, [1, 3, 99])

        self.assertEqual([1, 3], mocks['sitreps_db_case_rows'].call_args[0][1])
        self.assertEqual([1, 3], mocks['sitreps_db_exception_assets'].call_args[0][0])
        self.assertEqual([1, 3], mocks['sitreps_db_stage_transitions'].call_args[0][0])
        self.assertEqual([1, 3], mocks['sitreps_db_cases_attached_since'].call_args[0][1])
        self.assertEqual(room.created_at.isoformat(), draft['since'])
        self.assertTrue(draft['title'].startswith('SitRep — WR — '))
        self.assertIn('## Per-case status', draft['body_md'])

    def test_decision_changes_since_last_publish(self):
        # sitrep_auto_draft reads the real clock; anchor the fixture on it.
        now = datetime.datetime.utcnow()
        since = now - datetime.timedelta(hours=2)
        decisions = [
            SimpleNamespace(decision_id=1, number=1, title='Old', status='approved', target_at=None,
                            created_at=since - datetime.timedelta(hours=1),
                            decided_at=since + datetime.timedelta(minutes=5),
                            implemented_at=None, owner_name=None),
            SimpleNamespace(decision_id=2, number=2, title='Soon', status='proposed',
                            target_at=now + datetime.timedelta(hours=2),
                            created_at=since + datetime.timedelta(minutes=10),
                            decided_at=None, implemented_at=None, owner_name='Ann'),
        ]
        room = SimpleNamespace(name='WR', created_at=since - datetime.timedelta(days=1))
        with patch(f'{_MODULE}.war_room_get', return_value=room), \
                patch(f'{_MODULE}.sitreps_db_last_published_at', return_value=since), \
                patch(f'{_MODULE}.sitreps_db_attached_case_ids', return_value=[]), \
                patch(f'{_MODULE}.sitreps_db_decisions', return_value=decisions), \
                patch(f'{_MODULE}.sitreps_db_case_rows', return_value=[]), \
                patch(f'{_MODULE}.sitreps_db_exception_assets', return_value=[]), \
                patch(f'{_MODULE}.sitreps_db_open_tasks', return_value=[]), \
                patch(f'{_MODULE}.sitreps_db_stage_transitions', return_value=[]), \
                patch(f'{_MODULE}.sitreps_db_cases_attached_since', return_value=[]):
            draft = sitrep_auto_draft(1, [])
        types = sorted((c['type'], c['ref']) for c in draft['sections']['changes'])
        self.assertEqual([('decision_approved', 'D-1'), ('decision_created', 'D-2')], types)
        self.assertEqual(['D-1', 'D-2'], [d['ref'] for d in draft['sections']['decisions']])
        due_soon = [a for a in draft['sections']['next_actions'] if a['type'] == 'decision']
        self.assertEqual(['D-2'], [a['ref'] for a in due_soon])
