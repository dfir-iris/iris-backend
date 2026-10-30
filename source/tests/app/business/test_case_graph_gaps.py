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

"""Unit tests for the investigation gaps of a graph (§4.8)."""

from unittest import TestCase

from app.business.case_graph_gaps import case_graph_gaps_compute


def _asset(asset_id, compromised=False, status='Done', event_count=1):
    return {'key': f'asset:{asset_id}', 'kind': 'asset', 'label': f'WKS-{asset_id}', 'compromised': compromised,
            'analysis_status': status, 'event_count': event_count}


def _ioc(ioc_id):
    return {'key': f'ioc:{ioc_id}', 'kind': 'ioc', 'label': f'ioc-{ioc_id}', 'event_count': 0}


def _edge(key, source, target, directed=True, editable=True, event_ids=(), description=None):
    return {'key': key, 'id': int(key[2:]) if editable else None, 'source': source, 'target': target,
            'relation': {'name': 'lateral_movement'}, 'directed': directed, 'editable': editable,
            'event_ids': list(event_ids), 'description': description}


def _codes(gaps):
    return [(gap['code'], tuple(gap['node_keys']), tuple(gap['edge_keys'])) for gap in gaps]


class TestGaps(TestCase):

    def test_compromised_asset_without_incoming_edge(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1, compromised=True), _asset(2)], 'edges': [
            _edge('e:1', 'asset:1', 'asset:2', event_ids=[3])]})
        self.assertEqual([('initial_access_unknown', ('asset:1',), ())], _codes(gaps))
        self.assertEqual('high', gaps[0]['severity'])
        self.assertIn('WKS-1', gaps[0]['message'])

    def test_undirected_edge_is_not_an_initial_access(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1, compromised=True), _asset(2)], 'edges': [
            _edge('e:1', 'asset:2', 'asset:1', directed=False, event_ids=[3])]})
        self.assertIn('initial_access_unknown', [gap['code'] for gap in gaps])

    def test_incoming_directed_edge_answers_the_initial_access(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1, compromised=True), _asset(2)], 'edges': [
            _edge('e:1', 'asset:2', 'asset:1', event_ids=[3])]})
        self.assertEqual([], gaps)

    def test_compromised_without_event(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1, compromised=True, event_count=0), _asset(2)],
                                        'edges': [_edge('e:1', 'asset:2', 'asset:1', event_ids=[3])]})
        self.assertEqual([('compromised_without_event', ('asset:1',), ())], _codes(gaps))
        self.assertEqual('medium', gaps[0]['severity'])

    def test_unanalysed_compromised(self):
        for status in ('To be done', 'Started', None, 'Pending'):
            gaps = case_graph_gaps_compute({'nodes': [_asset(1, compromised=True, status=status), _asset(2)],
                                            'edges': [_edge('e:1', 'asset:2', 'asset:1', event_ids=[3])]})
            self.assertEqual([('unanalysed_compromised', ('asset:1',), ())], _codes(gaps), status)

    def test_not_compromised_asset_has_no_asset_gap(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1, status='To be done', event_count=0)], 'edges': []})
        self.assertEqual([], gaps)

    def test_unsupported_stored_edge(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1), _asset(2)], 'edges': [
            _edge('e:1', 'asset:1', 'asset:2'),
            _edge('e:2', 'asset:2', 'asset:1', description='Seen in the EDR'),
            _edge('d:abc', 'asset:1', 'asset:2', editable=False)]})
        self.assertEqual([('unsupported_edge', ('asset:1', 'asset:2'), ('e:1',))], _codes(gaps))
        self.assertEqual('low', gaps[0]['severity'])

    def test_isolated_ioc(self):
        gaps = case_graph_gaps_compute({'nodes': [_asset(1), _ioc(5), _ioc(6)], 'edges': [
            _edge('d:abc', 'asset:1', 'ioc:6', editable=False)]})
        self.assertEqual([('isolated_ioc', ('ioc:5',), ())], _codes(gaps))

    def test_gaps_are_sorted_by_severity(self):
        gaps = case_graph_gaps_compute({'nodes': [_ioc(5), _asset(1, compromised=True, status='Started',
                                                                  event_count=0), _asset(2)],
                                        'edges': [_edge('e:1', 'asset:1', 'asset:2')]})
        self.assertEqual(['initial_access_unknown', 'compromised_without_event', 'unanalysed_compromised',
                          'unsupported_edge', 'isolated_ioc'], [gap['code'] for gap in gaps])
        for gap in gaps:
            self.assertEqual({'code', 'severity', 'node_keys', 'edge_keys', 'message'}, set(gap))
