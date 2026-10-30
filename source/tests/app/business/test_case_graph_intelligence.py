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

"""Unit tests for the suggestion review, the module hooks and the report
context of the investigation graph. The DB is mocked."""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import case_graph
from app.models.errors import BusinessProcessingError


_MODULE = 'app.business.case_graph'
_KEY_A = 's:0123456789abcdef'
_KEY_B = 's:fedcba9876543210'


def _suggestion(key, source='asset:1', target='ioc:5'):
    return {'key': key, 'kind': 'ip_match', 'source': source, 'target': target, 'relation': 'same_as',
            'directed': False, 'reason': 'The IP matches', 'event_ids': [], 'alert_ids': [], 'score': 90}


class TestHook(TestCase):

    def test_hook_is_called_with_the_case(self):
        with patch(f'{_MODULE}.call_modules_hook') as hook:
            case_graph._hook('on_postload_graph_edge_create', ['edge'], 4)
        hook.assert_called_once_with('on_postload_graph_edge_create', data=['edge'], caseid=4)

    def test_failing_hook_never_breaks_the_request(self):
        with patch(f'{_MODULE}.call_modules_hook', MagicMock(side_effect=RuntimeError('module down'))), \
                patch(f'{_MODULE}.case_graph_db_rollback') as rollback:
            case_graph._hook('on_postload_graph_edge_delete', [3], 4)
        rollback.assert_called_once()

    def test_empty_data_calls_nothing(self):
        with patch(f'{_MODULE}.call_modules_hook') as hook:
            case_graph._hook('on_postload_graph_edge_create', [], 4)
        hook.assert_not_called()


class TestSuggestionReview(TestCase):

    def test_keys_are_validated(self):
        for keys in (None, [], 's:0123456789abcdef', ['e:1'], ['s:XYZ'], [_KEY_A] * 1001):
            with self.assertRaises(BusinessProcessingError):
                case_graph._validate_suggestion_keys(keys)
        self.assertEqual([_KEY_A], case_graph._validate_suggestion_keys([_KEY_A, _KEY_A]))

    def test_accept_creates_suggestion_edges_and_lists_unknown_keys(self):
        batch = MagicMock(return_value=([{'key': 'e:1'}], []))
        with patch(f'{_MODULE}.case_graph_suggestions', MagicMock(return_value=[_suggestion(_KEY_A)])), \
                patch(f'{_MODULE}.case_graph_edges_create_batch', batch):
            result = case_graph.case_graph_suggestions_accept(4, 3, [_KEY_A, _KEY_B])
        self.assertEqual({'edges': [{'key': 'e:1'}], 'unknown_keys': [_KEY_B]}, result)
        _, _, specs, origin, review_state, _ = batch.call_args.args
        self.assertEqual(('suggestion', 'accepted'), (origin, review_state))
        self.assertEqual({'source': 'asset:1', 'target': 'ioc:5', 'relation': 'same_as', 'directed': False,
                          'event_ids': [], 'description': 'The IP matches', 'confidence': 90}, specs[0])

    def test_skipped_edge_is_listed_as_unknown(self):
        batch = MagicMock(return_value=([], [{'index': 0, 'reason': 'duplicate'}]))
        with patch(f'{_MODULE}.case_graph_suggestions', MagicMock(return_value=[_suggestion(_KEY_A)])), \
                patch(f'{_MODULE}.case_graph_edges_create_batch', batch):
            result = case_graph.case_graph_suggestions_accept(4, 3, [_KEY_A])
        self.assertEqual([_KEY_A], result['unknown_keys'])

    def test_dismiss_counts_new_keys(self):
        dismiss = MagicMock(return_value=2)
        with patch(f'{_MODULE}.case_graph_db_dismiss_suggestions', dismiss), \
                patch(f'{_MODULE}._commit'), patch(f'{_MODULE}.track_activity') as activity:
            self.assertEqual({'dismissed': 2}, case_graph.case_graph_suggestions_dismiss(4, 3, [_KEY_A, _KEY_B]))
        self.assertEqual((4, 3, [_KEY_A, _KEY_B]), dismiss.call_args.args[:3])
        activity.assert_called_once()


class TestReportContext(TestCase):

    def _run(self, snapshot):
        graph = {'nodes': [{'key': 'asset:1'}], 'edges': [], 'events': [], 'meta': {}}
        with patch(f'{_MODULE}.case_graph_get', MagicMock(return_value=graph)), \
                patch(f'{_MODULE}.case_graph_db_report_default_snapshot', MagicMock(return_value=snapshot)):
            return case_graph.case_graph_report_context(4)

    def test_graph_and_raw_base64_snapshot(self):
        result = self._run('data:image/png;base64,iVBOR\nw0KGgo=')
        self.assertEqual({'nodes': [{'key': 'asset:1'}], 'edges': []}, result['graph'])
        self.assertEqual('iVBORw0KGgo=', result['graph_snapshot'])

    def test_no_report_default_view(self):
        self.assertIsNone(self._run(None)['graph_snapshot'])
