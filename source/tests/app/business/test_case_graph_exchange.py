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

"""Unit tests for the case graph export (STIX 2.1, GraphML) and import plans (§4.7)."""

import json
from datetime import datetime
from unittest import TestCase
from xml.etree import ElementTree

from app.business.case_graph_exchange import case_graph_exchange_parse_json
from app.business.case_graph_exchange import case_graph_exchange_parse_stix
from app.business.case_graph_exchange import case_graph_exchange_to_graphml
from app.business.case_graph_exchange import case_graph_exchange_to_stix
from app.models.errors import BusinessProcessingError

_NS = '{http://graphml.graphdrawing.org/xmlns}'
_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

_RELATION_TYPES = [{'name': 'beacons_to', 'stix_relationship': 'communicates-with'},
                   {'name': 'co_occurs', 'stix_relationship': None}]


def _node(key, label, node_type=None, **extra):
    kind = key.split(':')[0]
    return dict({'key': key, 'kind': kind, 'label': label, 'type': node_type}, **extra)


def _edge(key, source, target, relation, directed=True, **extra):
    return dict({'key': key, 'source': source, 'target': target, 'directed': directed,
                 'relation': {'name': relation, 'label': relation.title()}, 'origin': 'manual',
                 'review_state': 'accepted', 'count': 1, 'editable': key.startswith('e:')}, **extra)


_GRAPH = {
    'nodes': [
        _node('asset:1', 'WKS-01', 'Windows - Computer', compromised=True, tags=['dc', 'prod']),
        _node('ioc:2', '10.0.0.5', 'ip-dst'),
        _node('ioc:3', 'evil.example', 'domain'),
        _node('ioc:4', _SHA256, 'sha256'),
        _node('ioc:5', 'HKLM\\Run', 'regkey'),
        _node('ioc:6', '10.0.0.5', 'ip-src'),
        _node('node:7', 'APT-X', 'actor'),
        _node('node:8', 'jdoe', 'account'),
        _node('node:9', 'Cobalt', 'malware'),
        _node('node:10', 'Remember this', 'note', description='Long text'),
        _node('node:11', 'Misc', 'other'),
        _node('case:12', 'Other case', accessible=True),
    ],
    'edges': [
        _edge('e:1', 'asset:1', 'ioc:3', 'beacons_to', first_seen='2026-10-01T12:00:00', confidence=80),
        _edge('d:abc', 'asset:1', 'ioc:2', 'co_occurs', directed=False),
        _edge('e:2', 'node:10', 'node:7', 'co_occurs', directed=False),
        _edge('d:def', 'ioc:3', 'case:12', 'same_as', directed=False),
    ],
}


class TestStixExport(TestCase):

    def setUp(self):
        self.bundle = case_graph_exchange_to_stix(_GRAPH, _RELATION_TYPES, 1, datetime(2026, 10, 30, 8))
        self.by_key = {obj.get('x_iris_key'): obj for obj in self.bundle['objects'] if 'x_iris_key' in obj}

    def test_bundle(self):
        self.assertEqual('bundle', self.bundle['type'])
        self.assertTrue(self.bundle['id'].startswith('bundle--'))
        for obj in self.bundle['objects']:
            self.assertEqual('2.1', obj['spec_version'])
        json.dumps(self.bundle)

    def test_iocs_become_scos(self):
        self.assertEqual(('ipv4-addr', '10.0.0.5'), (self.by_key['ioc:2']['type'], self.by_key['ioc:2']['value']))
        self.assertEqual('domain-name', self.by_key['ioc:3']['type'])
        self.assertEqual({'SHA-256': _SHA256}, self.by_key['ioc:4']['hashes'])

    def test_sco_ids_are_deterministic_and_deduplicated(self):
        # ioc:2 and ioc:6 share the same SCO: kept once
        ids = [obj['id'] for obj in self.bundle['objects']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn('ioc:6', self.by_key)
        again = case_graph_exchange_to_stix(_GRAPH, _RELATION_TYPES, 1, datetime(2026, 10, 30, 8))
        self.assertEqual(ids, [obj['id'] for obj in again['objects']])

    def test_unmapped_ioc_becomes_an_indicator(self):
        indicator = self.by_key['ioc:5']
        self.assertEqual(('indicator', 'stix', 'regkey'),
                         (indicator['type'], indicator['pattern_type'], indicator['x_iris_ioc_type']))
        self.assertEqual("[x-iris-ioc:value = 'HKLM\\\\Run']", indicator['pattern'])
        self.assertEqual('2026-10-30T08:00:00.000Z', indicator['valid_from'])

    def test_asset_is_infrastructure(self):
        asset = self.by_key['asset:1']
        self.assertEqual(('infrastructure', 'WKS-01', True), (asset['type'], asset['name'], asset['x_iris_compromised']))

    def test_free_nodes(self):
        self.assertEqual('threat-actor', self.by_key['node:7']['type'])
        self.assertEqual(('user-account', 'jdoe'), (self.by_key['node:8']['type'], self.by_key['node:8']['account_login']))
        self.assertEqual('malware', self.by_key['node:9']['type'])
        self.assertFalse(self.by_key['node:9']['is_family'])
        self.assertEqual(('x-iris-graph-node', 'node', 'other'),
                         (self.by_key['node:11']['type'], self.by_key['node:11']['x_iris_kind'],
                          self.by_key['node:11']['x_iris_node_type']))

    def test_note_references_its_neighbours(self):
        note = self.by_key['node:10']
        self.assertEqual(('note', 'Remember this', 'Long text'), (note['type'], note['abstract'], note['content']))
        self.assertEqual([self.by_key['node:7']['id']], note['object_refs'])

    def test_case_nodes_and_their_edges_are_skipped(self):
        self.assertNotIn('case:12', self.by_key)
        relationships = [obj for obj in self.bundle['objects'] if obj['type'] == 'relationship']
        self.assertEqual(3, len(relationships))

    def test_relationships(self):
        relationships = {obj['x_iris_relation']: obj for obj in self.bundle['objects']
                         if obj['type'] == 'relationship' and obj['x_iris_origin'] == 'manual'
                         and obj['source_ref'] == self.by_key['asset:1']['id']}
        beacon = relationships['beacons_to']
        self.assertEqual(('communicates-with', self.by_key['ioc:3']['id'], True, 80, '2026-10-01T12:00:00.000Z'),
                         (beacon['relationship_type'], beacon['target_ref'], beacon['x_iris_directed'],
                          beacon['confidence'], beacon['start_time']))
        co_occurs = next(obj for obj in self.bundle['objects']
                         if obj['type'] == 'relationship' and obj['x_iris_relation'] == 'co_occurs'
                         and obj['target_ref'] == self.by_key['ioc:2']['id'])
        self.assertEqual('related-to', co_occurs['relationship_type'])
        self.assertFalse(co_occurs['x_iris_directed'])

    def test_no_null_values(self):
        for obj in self.bundle['objects']:
            self.assertNotIn(None, obj.values())


class TestGraphmlExport(TestCase):

    def setUp(self):
        self.document = case_graph_exchange_to_graphml(_GRAPH)
        self.root = ElementTree.fromstring(self.document.split('\n', 1)[1])

    def test_header_and_root(self):
        self.assertTrue(self.document.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertEqual(f'{_NS}graphml', self.root.tag)

    def test_keys(self):
        keys = {key.get('id'): key for key in self.root.findall(f'{_NS}key')}
        self.assertEqual(('node', 'label', 'string'),
                         (keys['n_label'].get('for'), keys['n_label'].get('attr.name'), keys['n_label'].get('attr.type')))
        self.assertEqual('edge', keys['e_relation'].get('for'))
        self.assertEqual('boolean', keys['e_edge_directed'].get('attr.type'))

    def test_nodes(self):
        graph = self.root.find(f'{_NS}graph')
        self.assertEqual('directed', graph.get('edgedefault'))
        nodes = {node.get('id'): node for node in graph.findall(f'{_NS}node')}
        self.assertEqual(len(_GRAPH['nodes']), len(nodes))
        data = {item.get('key'): item.text for item in nodes['asset:1'].findall(f'{_NS}data')}
        self.assertEqual({'n_kind': 'asset', 'n_label': 'WKS-01', 'n_type': 'Windows - Computer',
                          'n_compromised': 'true', 'n_tags': 'dc,prod'}, data)

    def test_edges(self):
        edges = {edge.get('id'): edge for edge in self.root.find(f'{_NS}graph').findall(f'{_NS}edge')}
        self.assertEqual({'e:1', 'd:abc', 'e:2', 'd:def'}, set(edges))
        self.assertEqual(('asset:1', 'ioc:3', 'true'),
                         (edges['e:1'].get('source'), edges['e:1'].get('target'), edges['e:1'].get('directed')))
        self.assertEqual('false', edges['d:abc'].get('directed'))
        data = {item.get('key'): item.text for item in edges['e:1'].findall(f'{_NS}data')}
        self.assertEqual('beacons_to', data['e_relation'])
        self.assertEqual('Beacons_To', data['e_relation_label'])
        self.assertEqual('80', data['e_confidence'])
        self.assertEqual('true', data['e_edge_directed'])


class TestStixImport(TestCase):

    def test_round_trip_plan(self):
        bundle = case_graph_exchange_to_stix(_GRAPH, _RELATION_TYPES, 1, datetime(2026, 10, 30))
        plan = case_graph_exchange_parse_stix(json.dumps(bundle))
        self.assertEqual({('10.0.0.5', 'ip-any'), ('evil.example', 'domain'), (_SHA256, 'sha256'),
                          ('HKLM\\Run', 'regkey')}, {(ioc['value'], ioc['type_hint']) for ioc in plan['iocs']})
        self.assertEqual({('APT-X', 'actor'), ('jdoe', 'account'), ('Cobalt', 'malware'),
                          ('Remember this', 'note'), ('Misc', 'other')},
                         {(node['label'], node['node_type']) for node in plan['nodes']})
        self.assertEqual([('WKS-01', 'Windows - Computer')],
                         [(asset['label'], asset['type_name']) for asset in plan['assets']])
        self.assertEqual(3, len(plan['edges']))
        beacon = next(edge for edge in plan['edges'] if edge['relation'] == 'beacons_to')
        self.assertEqual(('communicates-with', True, 80), (beacon['stix_relationship'], beacon['directed'],
                                                           beacon['confidence']))

    def test_foreign_objects(self):
        bundle = {'type': 'bundle', 'objects': [
            {'type': 'indicator', 'id': 'indicator--1', 'pattern': "[url:value = 'http://x.example/']"},
            {'type': 'file', 'id': 'file--1', 'hashes': {'MD5': 'd41d8cd98f00b204e9800998ecf8427e'}},
            {'type': 'intrusion-set', 'id': 'intrusion-set--1', 'name': 'APT-Y'},
            {'type': 'identity', 'id': 'identity--1', 'name': 'ACME'},
            {'type': 'attack-pattern', 'id': 'attack-pattern--1', 'name': 'T1059'},
            {'type': 'relationship', 'id': 'relationship--1', 'relationship_type': 'uses',
             'source_ref': 'intrusion-set--1', 'target_ref': 'file--1'},
            {'type': 'relationship', 'id': 'relationship--2'},
            'garbage',
        ]}
        plan = case_graph_exchange_parse_stix(bundle)
        self.assertEqual([('http://x.example/', 'url'), ('d41d8cd98f00b204e9800998ecf8427e', 'md5')],
                         [(ioc['value'], ioc['type_hint']) for ioc in plan['iocs']])
        self.assertEqual([('APT-Y', 'actor'), ('ACME', 'actor')],
                         [(node['label'], node['node_type']) for node in plan['nodes']])
        self.assertEqual([('intrusion-set--1', 'file--1', 'uses', None)],
                         [(edge['source_ref'], edge['target_ref'], edge['stix_relationship'], edge['relation'])
                          for edge in plan['edges']])
        self.assertEqual(3, len(plan['skipped']))

    def test_invalid(self):
        for content in ('', 'not json', '{"type": "bundle"}', None):
            with self.subTest(content=content), self.assertRaises(BusinessProcessingError):
                case_graph_exchange_parse_stix(content)


class TestJsonImport(TestCase):

    def test_plan(self):
        plan = case_graph_exchange_parse_json(json.dumps(_GRAPH))
        self.assertEqual(5, len(plan['iocs']))
        self.assertEqual({'node:7', 'node:8', 'node:9', 'node:10', 'node:11'}, {node['ref'] for node in plan['nodes']})
        self.assertEqual(['asset:1'], [asset['ref'] for asset in plan['assets']])
        self.assertEqual([('e:1', 'beacons_to'), ('e:2', 'co_occurs')],
                         [(edge['ref'], edge['relation']) for edge in plan['edges']])
        self.assertEqual({'case:12', 'd:abc', 'd:def'}, set(plan['skipped']))

    def test_invalid(self):
        with self.assertRaises(BusinessProcessingError):
            case_graph_exchange_parse_json('[]')
