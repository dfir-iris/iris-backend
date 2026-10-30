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

"""Unit tests for the assembly of the GET /graph payload (§4.1)."""

from datetime import datetime
from unittest import TestCase

from app.business.case_graph_assembly import case_graph_assembly_build
from app.business.case_graph_assembly import case_graph_enrichment_summary
from app.business.case_graph_assembly import case_graph_parse_include

_CASE_ID = 1

_RELATION_TYPES = [
    {'id': 1, 'name': 'co_occurs', 'label': 'Co-occurs with', 'directed': False, 'color': 'slate',
     'line_style': 'dotted', 'is_system': True, 'sort_order': 1},
    {'id': 2, 'name': 'lateral_movement', 'label': 'Lateral movement', 'directed': True, 'color': 'red',
     'line_style': 'solid', 'is_system': True, 'sort_order': 2},
    {'id': 3, 'name': 'related_to', 'label': 'Related to', 'directed': False, 'color': 'slate',
     'line_style': 'solid', 'is_system': True, 'sort_order': 3},
    {'id': 4, 'name': 'same_as', 'label': 'Same as', 'directed': False, 'color': 'slate',
     'line_style': 'dashed', 'is_system': True, 'sort_order': 4},
]


def _asset(asset_id, name, **extra):
    return dict({'asset_id': asset_id, 'asset_name': name, 'type_name': 'Windows - Computer'}, **extra)


def _stored(edge_id, source, target, relation_type_id=2, review_state='accepted', event_ids=(), directed=True):
    source_kind, source_id = source.split(':')
    target_kind, target_id = target.split(':')
    return {'edge_id': edge_id, 'source_kind': source_kind, 'source_id': int(source_id),
            'target_kind': target_kind, 'target_id': int(target_id), 'relation_type_id': relation_type_id,
            'directed': directed, 'review_state': review_state, 'origin': 'manual', 'event_ids': list(event_ids),
            'tlp_id': None}


def _data(**overrides):
    data = {
        'case_id': _CASE_ID,
        'relation_types': _RELATION_TYPES,
        'tlps': {1: 'red', 2: 'amber'},
        'assets': [_asset(1, 'WKS-01'), _asset(2, 'SRV-01')],
        'iocs': [{'ioc_id': 5, 'ioc_value': 'evil.example', 'type_name': 'domain', 'tlp_name': 'amber'}],
        'events': [{'event_id': 10, 'event_title': 'Logon', 'event_date': datetime(2026, 10, 1, 12),
                    'event_in_graph': True, 'parent_event_id': None, 'category_name': 'Lateral Movement'},
                   {'event_id': 11, 'event_title': 'Hidden', 'event_date': datetime(2026, 10, 2, 12),
                    'event_in_graph': False, 'parent_event_id': None, 'category_name': None}],
        'event_links': [{'event_id': 10, 'kind': 'asset', 'object_id': 1, 'role': 'source'},
                        {'event_id': 10, 'kind': 'asset', 'object_id': 2, 'role': 'target'},
                        {'event_id': 11, 'kind': 'asset', 'object_id': 1, 'role': 'involved'},
                        {'event_id': 11, 'kind': 'ioc', 'object_id': 5, 'role': 'involved'}],
        'ioc_asset_links': [],
        'stored_edges': [],
        'free_nodes': [],
        'evidences': [{'id': 7, 'filename': 'mem.raw'}],
        'tasks': [{'id': 8, 'task_title': 'Triage'}],
        'notes': [],
        'alerts': [],
        'alert_links': [],
        'ioc_cross_cases': [],
        'asset_cross_cases': [],
        'cases': [],
        'suggestions': [],
    }
    data.update(overrides)
    return data


def _keys(payload, collection='nodes'):
    return {item['key'] for item in payload[collection]}


def _edge(payload, source, target):
    for edge in payload['edges']:
        if (edge['source'], edge['target']) == (source, target):
            return edge
    return None


class TestIncludeParsing(TestCase):

    def test_comma_list(self):
        self.assertEqual({'events', 'notes'}, case_graph_parse_include('events, NOTES,unknown'))

    def test_empty(self):
        self.assertEqual(set(), case_graph_parse_include(None))
        self.assertEqual(set(), case_graph_parse_include(''))

    def test_list(self):
        self.assertEqual({'tasks'}, case_graph_parse_include(['tasks', 'nope']))


class TestEnrichmentSummary(TestCase):

    def test_sources_and_nested_score(self):
        self.assertEqual({'sources': ['misp', 'vt'], 'score': 42},
                         case_graph_enrichment_summary({'vt': {'stats': {'malicious_score': 42}}, 'misp': {}}))

    def test_empty(self):
        self.assertIsNone(case_graph_enrichment_summary({}))
        self.assertIsNone(case_graph_enrichment_summary(None))


class TestAssembly(TestCase):

    def test_default_nodes_are_assets_iocs_and_free_nodes(self):
        payload = case_graph_assembly_build(_data(free_nodes=[{'node_id': 3, 'label': 'Actor', 'node_type': 'actor',
                                                               'tlp_id': 1}]))
        self.assertEqual({'asset:1', 'asset:2', 'ioc:5', 'node:3'}, _keys(payload))
        free = next(node for node in payload['nodes'] if node['key'] == 'node:3')
        self.assertEqual(('node', 3, 'red', 1), (free['kind'], free['id'], free['tlp'], free['tlp_id']))

    def test_node_shape(self):
        payload = case_graph_assembly_build(_data())
        asset = next(node for node in payload['nodes'] if node['key'] == 'asset:1')
        for field in ('key', 'kind', 'id', 'label', 'subtitle', 'type', 'icon', 'compromised', 'analysis_status',
                      'flags', 'tags', 'tlp', 'criticality', 'description', 'first_seen', 'last_seen',
                      'event_count', 'cross_case_count', 'enrichment', 'url'):
            self.assertIn(field, asset)
        self.assertEqual(1, asset['event_count'])
        self.assertEqual('2026-10-01T12:00:00', asset['first_seen'])
        self.assertEqual('/case/1/assets/1', asset['url'])
        self.assertNotIn('_first', asset)

    def test_include_adds_kinds(self):
        payload = case_graph_assembly_build(_data(), include={'events', 'evidences', 'tasks'})
        self.assertEqual({'asset:1', 'asset:2', 'ioc:5', 'event:10', 'evidence:7', 'task:8'}, _keys(payload))

    def test_hidden_event_is_not_a_node(self):
        payload = case_graph_assembly_build(_data(), include={'events'})
        self.assertNotIn('event:11', _keys(payload))
        self.assertEqual([10], [event['event_id'] for event in payload['events']])

    def test_derived_edges(self):
        payload = case_graph_assembly_build(_data())
        edge = _edge(payload, 'asset:1', 'asset:2')
        self.assertEqual('lateral_movement', edge['relation']['name'])
        self.assertEqual(2, edge['relation']['id'])
        self.assertTrue(edge['key'].startswith('d:'))
        self.assertIsNone(edge['id'])
        self.assertFalse(edge['editable'])
        self.assertEqual('event', edge['origin'])
        self.assertEqual([10], edge['event_ids'])
        self.assertEqual(1, edge['count'])
        # event 11 is out of the graph: no asset:1 - ioc:5 edge
        self.assertIsNone(_edge(payload, 'asset:1', 'ioc:5'))

    def test_events_array(self):
        payload = case_graph_assembly_build(_data())
        self.assertEqual([{'event_id': 10, 'title': 'Logon', 'date': '2026-10-01T12:00:00',
                           'category': 'Lateral Movement', 'color': None, 'parent_event_id': None,
                           'keys': ['asset:1', 'asset:2']}], payload['events'])

    def test_stored_edge_endpoints_are_added(self):
        payload = case_graph_assembly_build(_data(stored_edges=[_stored(1, 'asset:1', 'task:8', 3, directed=False)]))
        self.assertIn('task:8', _keys(payload))
        self.assertNotIn('evidence:7', _keys(payload))
        edge = _edge(payload, 'asset:1', 'task:8')
        self.assertEqual(('e:1', 1, True, 'related_to'),
                         (edge['key'], edge['id'], edge['editable'], edge['relation']['name']))

    def test_dangling_stored_edge_is_skipped(self):
        payload = case_graph_assembly_build(_data(stored_edges=[_stored(1, 'asset:1', 'asset:99')]))
        self.assertNotIn('asset:99', _keys(payload))
        self.assertNotIn('e:1', _keys(payload, 'edges'))

    def test_review_state_filter(self):
        data = _data(stored_edges=[_stored(1, 'asset:1', 'ioc:5', 3, 'pending', directed=False),
                                   _stored(2, 'asset:2', 'ioc:5', 3, 'rejected', directed=False)])
        self.assertFalse({'e:1', 'e:2'} & _keys(case_graph_assembly_build(data), 'edges'))
        self.assertTrue({'e:1', 'e:2'} <= _keys(case_graph_assembly_build(data, review_state='all'), 'edges'))

    def test_derived_edge_merges_into_stored(self):
        data = _data(stored_edges=[_stored(4, 'asset:1', 'asset:2', 2)])
        payload = case_graph_assembly_build(data)
        between = [edge for edge in payload['edges'] if {edge['source'], edge['target']} == {'asset:1', 'asset:2'}]
        self.assertEqual(1, len(between))
        self.assertEqual('e:4', between[0]['key'])
        self.assertEqual([10], between[0]['event_ids'])
        self.assertEqual('2026-10-01T12:00:00', between[0]['first_seen'])

    def test_stored_edge_with_other_relation_does_not_absorb(self):
        data = _data(stored_edges=[_stored(4, 'asset:1', 'asset:2', 3, directed=False)])
        payload = case_graph_assembly_build(data)
        between = [edge for edge in payload['edges'] if {edge['source'], edge['target']} == {'asset:1', 'asset:2'}]
        self.assertEqual(2, len(between))

    def test_ioc_asset_link(self):
        payload = case_graph_assembly_build(_data(ioc_asset_links=[{'ioc_id': 5, 'asset_id': 2}]))
        edge = _edge(payload, 'asset:2', 'ioc:5') or _edge(payload, 'ioc:5', 'asset:2')
        self.assertEqual(('related_to', 'ioc_asset_link'), (edge['relation']['name'], edge['origin']))

    def test_alerts(self):
        data = _data(alerts=[{'alert_id': 30, 'alert_title': 'Beacon', 'severity_name': 'High'}],
                     alert_links=[{'alert_id': 30, 'kind': 'ioc', 'object_id': 5}])
        self.assertNotIn('alert:30', _keys(case_graph_assembly_build(data)))
        payload = case_graph_assembly_build(data, include={'alerts'})
        self.assertIn('alert:30', _keys(payload))
        self.assertEqual('alert', _edge(payload, 'alert:30', 'ioc:5')['origin'])

    def test_cross_case_count_is_always_set(self):
        data = _data(ioc_cross_cases=[{'object_id': 5, 'case_id': 2}, {'object_id': 5, 'case_id': 3}])
        payload = case_graph_assembly_build(data)
        ioc = next(node for node in payload['nodes'] if node['key'] == 'ioc:5')
        self.assertEqual(2, ioc['cross_case_count'])
        self.assertFalse({'case:2', 'case:3'} & _keys(payload))

    def test_cross_case_nodes_and_access(self):
        data = _data(ioc_cross_cases=[{'object_id': 5, 'case_id': 2}, {'object_id': 5, 'case_id': 3}],
                     asset_cross_cases=[{'object_id': 1, 'case_id': 2}],
                     cases=[{'case_id': 2, 'name': '#2 - Phishing'}, {'case_id': 3, 'name': '#3 - Secret'}])
        payload = case_graph_assembly_build(data, include={'cross_case'}, readable_case_ids={2})
        nodes = {node['key']: node for node in payload['nodes']}
        self.assertEqual((True, '#2 - Phishing', '/case/2'),
                         (nodes['case:2']['accessible'], nodes['case:2']['label'], nodes['case:2']['url']))
        self.assertEqual((False, 'Restricted case', None),
                         (nodes['case:3']['accessible'], nodes['case:3']['label'], nodes['case:3']['url']))
        same_as = {(edge['source'], edge['target']) for edge in payload['edges'] if edge['origin'] == 'cross_case'}
        self.assertEqual({('ioc:5', 'case:2'), ('ioc:5', 'case:3'), ('asset:1', 'case:2')}, same_as)
        self.assertTrue(all(edge['relation']['name'] == 'same_as'
                            for edge in payload['edges'] if edge['origin'] == 'cross_case'))

    def test_meta_and_warning(self):
        payload = case_graph_assembly_build(_data(), generated_at=datetime(2026, 10, 30), max_nodes=2)
        self.assertEqual(3, payload['meta']['node_count'])
        self.assertEqual(len(payload['edges']), payload['meta']['edge_count'])
        self.assertEqual('2026-10-30T00:00:00', payload['meta']['generated_at'])
        self.assertIn('IRIS_CASE_GRAPH_MAX_NODES', payload['meta']['warning'])
        self.assertNotIn('warning', case_graph_assembly_build(_data())['meta'])

    def test_relation_types_and_suggestions(self):
        payload = case_graph_assembly_build(_data())
        self.assertEqual(['co_occurs', 'lateral_movement', 'related_to', 'same_as'],
                         [row['name'] for row in payload['relation_types']])
        self.assertEqual([], payload['suggestions'])

    def test_include_events_adds_involved_in(self):
        payload = case_graph_assembly_build(_data(), include={'events'})
        self.assertEqual('involved_in', _edge(payload, 'asset:1', 'event:10')['relation']['name'])
        self.assertEqual('involved_in', _edge(payload, 'event:10', 'asset:2')['relation']['name'])
