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

"""Unit tests for the vulnerabilities a war room tracks."""

import datetime
from collections import namedtuple
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_vulnerabilities as biz
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BIZ = 'app.business.war_room_vulnerabilities'
_FINDINGS = 'app.business.vulnerability_findings'

_MatrixRow = namedtuple('_MatrixRow', ['Vulnerability', 'case_id', 'findings', 'open', 'fixed', 'dismissed',
                                       'exploited'])


def _vulnerability(vulnerability_id, identifier, severity='high'):
    return SimpleNamespace(vulnerability_id=vulnerability_id, identifier=identifier, is_private=False, kind='cve',
                           title=identifier, cvss_score=None, cvss_version=None, severity=severity,
                           epss_score=None, kev=False, exploit_maturity='unknown', patch_availability='unknown')


def _tracking(vulnerability_id, note=None):
    return SimpleNamespace(war_room_id=4, vulnerability_id=vulnerability_id, note=note,
                           added_at=datetime.datetime(2026, 10, 6, 12, 0), added_by_id=9)


class TestMatrixPage(TestCase):

    def _run(self, args=None, case_ids=(1, 2), page_ids=(2, 1), hide=False):
        observed = _vulnerability(1, 'CVE-2024-0001', 'medium')
        fresh = _vulnerability(2, 'CVE-2026-0002', 'critical')
        rows = [_MatrixRow(observed, 1, 2, 2, 0, 0, 0)]
        self.mocks = {}
        patches = {
            'page': patch(f'{_BIZ}.findings_db_matrix_page', return_value=(list(page_ids), 7)),
            'many': patch(f'{_BIZ}.vulnerabilities_db_get_many', return_value={1: observed, 2: fresh}),
            'tracked': patch(f'{_BIZ}.tracked_db_list', return_value=[(_tracking(2, 'n'), fresh, 'Ana')]),
            'cells': patch(f'{_FINDINGS}.findings_db_matrix', return_value=rows),
            'summary': patch(f'{_BIZ}.vulnerability_findings_summary', return_value={'findings': 2}),
            'counts': patch(f'{_BIZ}.tracked_db_counts', return_value=(1, 1)),
            'totals': patch(f'{_BIZ}.findings_db_matrix_case_totals', return_value=[
                SimpleNamespace(case_id=1, findings=2, open=2, fixed=0, dismissed=0, exploited=0)]),
        }
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        return biz.war_room_vulnerabilities_matrix(4, list(case_ids), args or {}, hide_private_content=hide)

    def test_page_should_list_tracked_entries_in_the_db_order(self):
        out = self._run()
        self.assertEqual([e['vulnerability']['identifier'] for e in out['vulnerabilities']],
                         ['CVE-2026-0002', 'CVE-2024-0001'])
        fresh = out['vulnerabilities'][0]
        self.assertEqual(fresh['tracked']['note'], 'n')
        self.assertEqual(fresh['tracked']['added_by_name'], 'Ana')
        self.assertEqual(fresh['cases'], {})
        self.assertIsNone(out['vulnerabilities'][1]['tracked'])
        self.assertEqual(out['summary'], {'findings': 2, 'tracked': 1, 'tracked_unobserved': 1})
        self.assertEqual(out['case_totals']['1']['open'], 2)
        self.assertEqual((out['total'], out['page'], out['per_page']), (7, 1, 50))
        self.mocks['tracked'].assert_called_once_with(4, [2, 1])

    def test_filters_should_reach_the_db(self):
        self._run({'page': '2', 'per_page': '10', 'search': '  log4 ', 'tracked': 'true'}, hide=True)
        args = self.mocks['page'].call_args.args
        self.assertEqual(args[0], [1, 2])
        self.assertEqual(args[2], {'search': 'log4', 'tracked_only': True, 'observed_only': False,
                                   'private_content_hidden': True})
        self.assertEqual(args[3:], (2, 10))

    def test_case_filter_should_narrow_the_cells_but_not_the_summary(self):
        self._run({'case_id': '2'})
        self.assertEqual(self.mocks['page'].call_args.args[0], [2])
        self.assertTrue(self.mocks['page'].call_args.args[2]['observed_only'])
        self.mocks['summary'].assert_called_once_with([1, 2])

    def test_invalid_arguments_should_be_refused(self):
        for args in ({'page': '0'}, {'page': 'x'}, {'per_page': '201'}, {'per_page': True},
                     {'case_id': '3'}, {'tracked': 'maybe'}, {'search': 'x' * 300}, {'search': 5}):
            with self.assertRaises(BusinessProcessingError, msg=args):
                self._run(args)


class TestTrack(TestCase):

    def _patches(self, existing=None, commit=True):
        vulnerability = _vulnerability(3, 'CVE-2026-0003')
        listed = [(existing or _tracking(3), vulnerability, 'Ana')]
        return vulnerability, {
            'get': patch(f'{_BIZ}.tracked_db_get', return_value=existing),
            'add': patch(f'{_BIZ}.tracked_db_add'),
            'commit': patch(f'{_BIZ}.vulnerabilities_db_commit', return_value=commit),
            'list': patch(f'{_BIZ}.tracked_db_list', return_value=listed),
            'activity': patch(f'{_BIZ}.track_activity'),
            'db_get': patch(f'{_BIZ}.vulnerabilities_db_get', return_value=vulnerability),
        }

    def _run(self, body, existing=None, commit=True):
        vulnerability, patches = self._patches(existing, commit)
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            result = biz.war_room_vulnerabilities_track(4, body, 9)
        finally:
            for p in patches.values():
                p.stop()
        return result, mocks

    def test_creates_by_id(self):
        (entry, created), mocks = self._run({'vulnerability_id': 3, 'note': 'fresh 0-day'})
        self.assertTrue(created)
        added = mocks['add'].call_args[0][0]
        self.assertEqual((added.war_room_id, added.vulnerability_id, added.note, added.added_by_id),
                         (4, 3, 'fresh 0-day', 9))
        mocks['activity'].assert_called_once()
        self.assertEqual(entry['vulnerability']['identifier'], 'CVE-2026-0003')

    def test_idempotent_updates_note_only_when_given(self):
        existing = _tracking(3, 'old')
        (_entry, created), mocks = self._run({'vulnerability_id': 3}, existing=existing)
        self.assertFalse(created)
        self.assertEqual(existing.note, 'old')
        mocks['add'].assert_not_called()
        mocks['activity'].assert_not_called()
        (_entry, _created), _mocks = self._run({'vulnerability_id': 3, 'note': 'new'}, existing=existing)
        self.assertEqual(existing.note, 'new')

    def test_concurrent_insert_is_not_created(self):
        (_entry, created), mocks = self._run({'vulnerability_id': 3}, commit=False)
        self.assertFalse(created)
        mocks['activity'].assert_not_called()

    def test_quick_add_by_identifier(self):
        vulnerability = _vulnerability(3, 'CVE-2026-0003')
        with patch(f'{_BIZ}.vulnerabilities_get_or_create', return_value=vulnerability) as get_or_create, \
                patch(f'{_BIZ}.vulnerabilities_cve_fill_quick_add') as fill:
            self._run({'identifier': 'cve-2026-0003'})
        get_or_create.assert_called_once_with('cve-2026-0003', None, 9)
        fill.assert_called_once_with(vulnerability, 9)

    def test_rejects_missing_target_and_bad_body(self):
        for body in ({}, {'vulnerability_id': '3'}, {'vulnerability_id': True}, None):
            with self.subTest(body=body), self.assertRaises(BusinessProcessingError):
                biz.war_room_vulnerabilities_track(4, body, 9)

    def test_rejects_unknown_id(self):
        with patch(f'{_BIZ}.vulnerabilities_db_get', return_value=None), \
                self.assertRaises(BusinessProcessingError):
            biz.war_room_vulnerabilities_track(4, {'vulnerability_id': 99}, 9)

    def test_rejects_too_long_note(self):
        with self.assertRaises(BusinessProcessingError):
            biz.war_room_vulnerabilities_track(4, {'vulnerability_id': 3, 'note': 'x' * 2001}, 9)


class TestUntrackAndUpdate(TestCase):

    def test_untrack_unknown_is_not_found(self):
        with patch(f'{_BIZ}.tracked_db_get', return_value=None), self.assertRaises(ObjectNotFoundError):
            biz.war_room_vulnerabilities_untrack(4, 3)

    def test_untrack_deletes_only_the_tracking(self):
        tracking = _tracking(3)
        delete = MagicMock()
        with patch(f'{_BIZ}.tracked_db_get', return_value=tracking), \
                patch(f'{_BIZ}.vulnerabilities_db_get', return_value=_vulnerability(3, 'CVE-2026-0003')), \
                patch(f'{_BIZ}.tracked_db_delete', delete), \
                patch(f'{_BIZ}.vulnerabilities_db_commit', return_value=True), \
                patch(f'{_BIZ}.track_activity') as activity:
            biz.war_room_vulnerabilities_untrack(4, 3)
        delete.assert_called_once_with(tracking)
        # War-room activity is read without the vulnerability permission.
        self.assertIn('#3', activity.call_args[0][0])
        self.assertNotIn('CVE-2026-0003', activity.call_args[0][0])

    def test_untrack_should_fire_the_untrack_hook(self):
        with patch(f'{_BIZ}.tracked_db_get', return_value=_tracking(3)), \
                patch(f'{_BIZ}.vulnerabilities_db_get', return_value=_vulnerability(3, 'CVE-2026-0003')), \
                patch(f'{_BIZ}.tracked_db_delete'), \
                patch(f'{_BIZ}.vulnerabilities_db_commit', return_value=True), \
                patch(f'{_BIZ}.track_activity'), \
                patch(f'{_BIZ}.call_modules_hook') as hook:
            biz.war_room_vulnerabilities_untrack(4, 3)
        hook.assert_called_once_with('on_postload_war_room_vulnerability_untrack',
                                     {'war_room_id': 4, 'vulnerability_id': 3, 'identifier': 'CVE-2026-0003'})

    def test_untrack_unknown_should_not_fire_the_hook(self):
        with patch(f'{_BIZ}.tracked_db_get', return_value=None), \
                patch(f'{_BIZ}.call_modules_hook') as hook, self.assertRaises(ObjectNotFoundError):
            biz.war_room_vulnerabilities_untrack(4, 3)
        hook.assert_not_called()

    def test_update_clears_note(self):
        tracking = _tracking(3, 'old')
        with patch(f'{_BIZ}.tracked_db_get', return_value=tracking), \
                patch(f'{_BIZ}.vulnerabilities_db_commit', return_value=True), \
                patch(f'{_BIZ}.tracked_db_list',
                      return_value=[(tracking, _vulnerability(3, 'CVE-2026-0003'), None)]):
            entry = biz.war_room_vulnerabilities_update(4, 3, {'note': None})
        self.assertIsNone(tracking.note)
        self.assertIsNone(entry['note'])
