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

"""Unit tests for the read-time derivation of the case graph edges (§3.5)."""

from datetime import datetime
from unittest import TestCase

from app.business.case_graph_derive import case_graph_derive_edges
from app.business.case_graph_derive import case_graph_derive_relation
from app.business.case_graph_derive import case_graph_derived_edge_key
from app.business.case_graph_derive import case_graph_key
from app.business.case_graph_derive import case_graph_merge_edges
from app.business.case_graph_derive import case_graph_parse_key
from app.models.errors import BusinessProcessingError


def _event(event_id, category=None, in_graph=True, parent=None, date=None):
    return {'event_id': event_id, 'event_date': date or datetime(2026, 10, 1, 10, event_id % 60),
            'event_in_graph': in_graph, 'parent_event_id': parent, 'category_name': category}


def _link(event_id, kind, object_id, role='involved'):
    return {'event_id': event_id, 'kind': kind, 'object_id': object_id, 'role': role}


def _pairs(edges, relation=None, origin=None):
    return {(edge['source'], edge['target'], edge['relation'], edge['directed'])
            for edge in edges
            if (relation is None or edge['relation'] == relation) and (origin is None or edge['origin'] == origin)}


class TestKeys(TestCase):

    def test_key_format(self):
        self.assertEqual('asset:12', case_graph_key('asset', 12))

    def test_parse_key(self):
        self.assertEqual(('ioc', 3), case_graph_parse_key('ioc:3'))

    def test_parse_key_rejects_unknown_kind(self):
        with self.assertRaises(BusinessProcessingError):
            case_graph_parse_key('user:3')

    def test_parse_key_rejects_bad_id(self):
        for raw in ('asset:', 'asset:0', 'asset:-1', 'asset:x', 'asset', None, 12):
            with self.subTest(raw=raw), self.assertRaises(BusinessProcessingError):
                case_graph_parse_key(raw)

    def test_derived_key_is_deterministic(self):
        edge = {'source': 'asset:1', 'target': 'asset:2', 'relation': 'co_occurs', 'directed': False,
                'origin': 'event'}
        key = case_graph_derived_edge_key(edge)
        self.assertTrue(key.startswith('d:'))
        self.assertEqual(18, len(key))
        self.assertEqual(key, case_graph_derived_edge_key(dict(edge)))
        self.assertNotEqual(key, case_graph_derived_edge_key(dict(edge, relation='related_to')))


class TestRelationFromCategory(TestCase):

    def test_lateral_movement(self):
        self.assertEqual('lateral_movement', case_graph_derive_relation('Lateral Movement'))

    def test_exfiltration(self):
        self.assertEqual('exfiltrated_to', case_graph_derive_relation('Exfiltration'))

    def test_command_and_control(self):
        self.assertEqual('beacons_to', case_graph_derive_relation('Command and Control'))

    def test_execution(self):
        self.assertEqual('executed_on', case_graph_derive_relation('Execution'))

    def test_credential_access_with_account(self):
        self.assertEqual('authenticated_to', case_graph_derive_relation('Credential Access', has_account=True))

    def test_credential_access_without_account(self):
        self.assertEqual('connected_to', case_graph_derive_relation('Credential Access'))

    def test_default(self):
        self.assertEqual('connected_to', case_graph_derive_relation(None))
        self.assertEqual('connected_to', case_graph_derive_relation('Discovery'))


class TestDeriveWithRoles(TestCase):

    def test_sources_times_targets_are_directed(self):
        edges = case_graph_derive_edges(
            [_event(1, 'Lateral Movement')],
            [_link(1, 'asset', 1, 'source'), _link(1, 'asset', 2, 'source'),
             _link(1, 'asset', 3, 'target'), _link(1, 'ioc', 4, 'target')])
        self.assertEqual({('asset:1', 'asset:3', 'lateral_movement', True),
                          ('asset:1', 'ioc:4', 'lateral_movement', True),
                          ('asset:2', 'asset:3', 'lateral_movement', True),
                          ('asset:2', 'ioc:4', 'lateral_movement', True)}, _pairs(edges))

    def test_involved_co_occurs_with_sources_and_targets(self):
        edges = case_graph_derive_edges(
            [_event(1)],
            [_link(1, 'asset', 1, 'source'), _link(1, 'asset', 2, 'target'), _link(1, 'ioc', 5, 'involved')])
        self.assertEqual({('asset:1', 'asset:2', 'connected_to', True)}, _pairs(edges, 'connected_to'))
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs', False), ('asset:2', 'ioc:5', 'co_occurs', False)},
                         _pairs(edges, 'co_occurs'))

    def test_involved_are_not_linked_together_when_roles_are_set(self):
        edges = case_graph_derive_edges(
            [_event(1)],
            [_link(1, 'asset', 1, 'source'), _link(1, 'asset', 2, 'target'),
             _link(1, 'asset', 3), _link(1, 'asset', 4)])
        self.assertNotIn(('asset:3', 'asset:4', 'co_occurs', False), _pairs(edges))

    def test_account_entity_gives_authenticated_to(self):
        edges = case_graph_derive_edges(
            [_event(1, 'Initial Access')],
            [_link(1, 'ioc', 7, 'source'), _link(1, 'asset', 2, 'target')],
            account_keys={'ioc:7'})
        self.assertEqual({('ioc:7', 'asset:2', 'authenticated_to', True)}, _pairs(edges))

    def test_only_sources_falls_back_to_co_occurrence(self):
        edges = case_graph_derive_edges(
            [_event(1)], [_link(1, 'asset', 1, 'source'), _link(1, 'asset', 2, 'source')])
        self.assertEqual({('asset:1', 'asset:2', 'co_occurs', False)}, _pairs(edges))


class TestDeriveWithoutRoles(TestCase):

    def test_assets_pairwise(self):
        edges = case_graph_derive_edges(
            [_event(1)], [_link(1, 'asset', 1), _link(1, 'asset', 2), _link(1, 'asset', 3)])
        self.assertEqual({('asset:1', 'asset:2', 'co_occurs', False), ('asset:1', 'asset:3', 'co_occurs', False),
                          ('asset:2', 'asset:3', 'co_occurs', False)}, _pairs(edges))

    def test_iocs_to_assets_and_no_ioc_clique_when_an_asset_is_present(self):
        edges = case_graph_derive_edges(
            [_event(1)], [_link(1, 'asset', 1), _link(1, 'ioc', 2), _link(1, 'ioc', 3)])
        self.assertEqual({('asset:1', 'ioc:2', 'co_occurs', False), ('asset:1', 'ioc:3', 'co_occurs', False)},
                         _pairs(edges))

    def test_ioc_clique_when_the_event_has_no_asset(self):
        edges = case_graph_derive_edges(
            [_event(1)], [_link(1, 'ioc', 2), _link(1, 'ioc', 3), _link(1, 'ioc', 4)])
        self.assertEqual({('ioc:2', 'ioc:3', 'co_occurs', False), ('ioc:2', 'ioc:4', 'co_occurs', False),
                          ('ioc:3', 'ioc:4', 'co_occurs', False)}, _pairs(edges))

    def test_missing_role_is_involved(self):
        edges = case_graph_derive_edges(
            [_event(1)], [{'event_id': 1, 'kind': 'asset', 'object_id': 1},
                          {'event_id': 1, 'kind': 'asset', 'object_id': 2, 'role': None}])
        self.assertEqual({('asset:1', 'asset:2', 'co_occurs', False)}, _pairs(edges))

    def test_single_entity_gives_nothing(self):
        self.assertEqual([], case_graph_derive_edges([_event(1)], [_link(1, 'asset', 1)]))

    def test_duplicate_links_are_counted_once(self):
        edges = case_graph_derive_edges(
            [_event(1)], [_link(1, 'asset', 1), _link(1, 'asset', 1), _link(1, 'asset', 2)])
        self.assertEqual(1, len(edges))
        self.assertEqual([1], edges[0]['event_ids'])

    def test_events_out_of_graph_are_ignored(self):
        edges = case_graph_derive_edges(
            [_event(1, in_graph=False)], [_link(1, 'asset', 1), _link(1, 'asset', 2)])
        self.assertEqual([], edges)

    def test_event_in_graph_none_counts_as_in_graph(self):
        edges = case_graph_derive_edges(
            [_event(1, in_graph=None)], [_link(1, 'asset', 1), _link(1, 'asset', 2)])
        self.assertEqual(1, len(edges))


class TestDeriveAggregation(TestCase):

    def test_same_pair_over_several_events_is_one_edge(self):
        first, last = datetime(2026, 1, 1), datetime(2026, 3, 1)
        edges = case_graph_derive_edges(
            [_event(1, date=last), _event(2, date=first), _event(3, date=datetime(2026, 2, 1))],
            [_link(1, 'asset', 1), _link(1, 'asset', 2),
             _link(2, 'asset', 2), _link(2, 'asset', 1),
             _link(3, 'asset', 1), _link(3, 'asset', 2)])
        self.assertEqual(1, len(edges))
        self.assertEqual([1, 2, 3], edges[0]['event_ids'])
        self.assertEqual(first, edges[0]['first_seen'])
        self.assertEqual(last, edges[0]['last_seen'])

    def test_undirected_pair_is_sorted(self):
        edges = case_graph_derive_edges([_event(1)], [_link(1, 'asset', 9), _link(1, 'asset', 1)])
        self.assertEqual(('asset:1', 'asset:9'), (edges[0]['source'], edges[0]['target']))


class TestDeriveOtherOrigins(TestCase):

    def test_ioc_asset_link_is_related_to(self):
        edges = case_graph_derive_edges([], [], [{'ioc_id': 3, 'asset_id': 1}])
        self.assertEqual(1, len(edges))
        edge = edges[0]
        self.assertEqual(('related_to', False, 'ioc_asset_link', []),
                         (edge['relation'], edge['directed'], edge['origin'], edge['event_ids']))
        self.assertEqual({'asset:1', 'ioc:3'}, {edge['source'], edge['target']})

    def test_parent_and_involved_in_only_with_events(self):
        events = [_event(1), _event(2, parent=1)]
        links = [_link(2, 'asset', 1, 'source'), _link(2, 'asset', 2, 'target'), _link(2, 'ioc', 3)]
        self.assertEqual(set(), _pairs(case_graph_derive_edges(events, links), 'parent_of'))
        edges = case_graph_derive_edges(events, links, include_events=True)
        self.assertEqual({('event:1', 'event:2', 'parent_of', True)}, _pairs(edges, 'parent_of'))
        self.assertEqual({('asset:1', 'event:2', 'involved_in', True), ('event:2', 'asset:2', 'involved_in', True),
                          ('ioc:3', 'event:2', 'involved_in', True)}, _pairs(edges, 'involved_in'))

    def test_parent_out_of_graph_gives_no_parent_edge(self):
        edges = case_graph_derive_edges([_event(1, in_graph=False), _event(2, parent=1)], [], include_events=True)
        self.assertEqual(set(), _pairs(edges, 'parent_of'))


class TestMerge(TestCase):

    def _stored(self, source, target, relation, directed, event_ids=()):
        return {'source': source, 'target': target, 'relation': relation, 'directed': directed,
                'event_ids': list(event_ids)}

    def _derived(self, source, target, relation, directed, event_ids=()):
        return {'source': source, 'target': target, 'relation': relation, 'directed': directed,
                'origin': 'event', 'event_ids': list(event_ids), 'first_seen': None, 'last_seen': None}

    def test_derived_folds_into_stored_with_same_pair_and_relation(self):
        stored = [self._stored('asset:1', 'asset:2', 'lateral_movement', True, [5])]
        derived = [self._derived('asset:1', 'asset:2', 'lateral_movement', True, [5, 7])]
        merged, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual([], remaining)
        self.assertEqual([5, 7], merged[0]['event_ids'])

    def test_other_relation_is_kept(self):
        stored = [self._stored('asset:1', 'asset:2', 'lateral_movement', True)]
        derived = [self._derived('asset:1', 'asset:2', 'co_occurs', False, [1])]
        _, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual(1, len(remaining))

    def test_opposite_direction_is_kept(self):
        stored = [self._stored('asset:2', 'asset:1', 'lateral_movement', True)]
        derived = [self._derived('asset:1', 'asset:2', 'lateral_movement', True, [1])]
        _, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual(1, len(remaining))

    def test_undirected_pairs_match_either_way_round(self):
        stored = [self._stored('ioc:9', 'asset:1', 'co_occurs', False)]
        derived = [self._derived('asset:1', 'ioc:9', 'co_occurs', False, [3])]
        merged, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual([], remaining)
        self.assertEqual([3], merged[0]['event_ids'])

    def test_event_cited_by_a_stored_edge_derives_nothing_on_that_pair(self):
        stored = [self._stored('asset:1', 'asset:2', 'exfiltrated_to', True, [4])]
        derived = [self._derived('asset:1', 'asset:2', 'connected_to', True, [4])]
        merged, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual([], remaining)
        self.assertEqual([4], merged[0]['event_ids'])

    def test_uncited_events_keep_the_derived_edge_and_its_dates(self):
        stored = [self._stored('asset:2', 'asset:1', 'exfiltrated_to', True, [4])]
        derived = [dict(self._derived('asset:1', 'asset:2', 'connected_to', True, [4, 6]),
                        first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 9))]
        _, remaining = case_graph_merge_edges(stored, derived, {4: datetime(2026, 1, 1),
                                                                6: datetime(2026, 1, 5)})
        self.assertEqual([6], remaining[0]['event_ids'])
        self.assertEqual(datetime(2026, 1, 5), remaining[0]['first_seen'])
        self.assertEqual(datetime(2026, 1, 5), remaining[0]['last_seen'])

    def test_cited_event_on_another_pair_changes_nothing(self):
        stored = [self._stored('asset:1', 'asset:3', 'exfiltrated_to', True, [4])]
        derived = [self._derived('asset:1', 'asset:2', 'connected_to', True, [4])]
        _, remaining = case_graph_merge_edges(stored, derived)
        self.assertEqual([4], remaining[0]['event_ids'])
