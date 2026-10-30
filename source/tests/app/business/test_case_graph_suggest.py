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

"""Unit tests for the heuristic edge suggestions of the investigation graph (§6)."""

from unittest import TestCase

from app.business.case_graph_suggest import CASE_GRAPH_SUGGESTION_KEY
from app.business.case_graph_suggest import case_graph_suggest_alert_links
from app.business.case_graph_suggest import case_graph_suggest_compute
from app.business.case_graph_suggest import case_graph_suggest_enrichment_resolutions
from app.business.case_graph_suggest import case_graph_suggest_ip_matches
from app.business.case_graph_suggest import case_graph_suggest_resolution_values
from app.business.case_graph_suggest import case_graph_suggest_text_mentions
from app.business.case_graph_suggest import case_graph_suggestion_key


def _asset(asset_id, name, ip=None):
    return {'asset_id': asset_id, 'asset_name': name, 'asset_ip': ip}


def _ioc(ioc_id, value, enrichment=None):
    return {'ioc_id': ioc_id, 'ioc_value': value, 'ioc_enrichment': enrichment}


def _event(event_id, title='', content=None, raw=None, in_graph=True):
    return {'event_id': event_id, 'event_title': title, 'event_content': content, 'event_raw': raw,
            'event_in_graph': in_graph}


def _link(event_id, kind, object_id):
    return {'event_id': event_id, 'kind': kind, 'object_id': object_id, 'role': 'involved'}


def _pairs(suggestions):
    return {(item['source'], item['target'], item['relation']) for item in suggestions}


class TestSuggestionKey(TestCase):

    def test_key_is_deterministic_and_well_formed(self):
        key = case_graph_suggestion_key('ip_match', 'asset:1', 'ioc:2', 'same_as', False)
        self.assertEqual(key, case_graph_suggestion_key('ip_match', 'asset:1', 'ioc:2', 'same_as', False))
        self.assertRegex(key, CASE_GRAPH_SUGGESTION_KEY)

    def test_undirected_pair_is_sorted(self):
        self.assertEqual(case_graph_suggestion_key('alert_link', 'asset:1', 'ioc:2', 'co_occurs', False),
                         case_graph_suggestion_key('alert_link', 'ioc:2', 'asset:1', 'co_occurs', False))

    def test_directed_pair_keeps_its_direction(self):
        self.assertNotEqual(case_graph_suggestion_key('enrichment_resolution', 'ioc:1', 'ioc:2', 'resolves_to', True),
                            case_graph_suggestion_key('enrichment_resolution', 'ioc:2', 'ioc:1', 'resolves_to', True))

    def test_kind_and_relation_change_the_key(self):
        base = case_graph_suggestion_key('ip_match', 'asset:1', 'ioc:2', 'same_as', False)
        self.assertNotEqual(base, case_graph_suggestion_key('text_mention', 'asset:1', 'ioc:2', 'same_as', False))
        self.assertNotEqual(base, case_graph_suggestion_key('ip_match', 'asset:1', 'ioc:2', 'co_occurs', False))


class TestTextMentions(TestCase):

    def _run(self, events, links, assets=(), iocs=()):
        return case_graph_suggest_text_mentions(events, links, list(assets), list(iocs))

    def test_mentioned_ioc_co_occurs_with_the_linked_asset(self):
        result = self._run([_event(10, 'Beacon to evil.example')], [_link(10, 'asset', 1)],
                           [_asset(1, 'WKS-01')], [_ioc(5, 'evil.example')])
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs')}, _pairs(result))
        item = result[0]
        self.assertEqual('text_mention', item['kind'])
        self.assertFalse(item['directed'])
        self.assertEqual([10], item['event_ids'])
        self.assertIn('evil.example', item['reason'])

    def test_ip_does_not_match_a_longer_ip(self):
        result = self._run([_event(10, 'Connection from 10.0.0.12 and 110.0.0.1')], [_link(10, 'asset', 1)],
                           [_asset(1, 'WKS-01')], [_ioc(5, '10.0.0.1')])
        self.assertEqual([], result)

    def test_ip_matches_as_a_whole_token(self):
        for text in ('Connection from 10.0.0.1.', 'src=10.0.0.1:445', '(10.0.0.1)', '10.0.0.1'):
            result = self._run([_event(10, text)], [_link(10, 'asset', 1)], [_asset(1, 'WKS-01')],
                               [_ioc(5, '10.0.0.1')])
            self.assertEqual({('asset:1', 'ioc:5', 'co_occurs')}, _pairs(result), text)

    def test_name_glued_to_a_word_does_not_match(self):
        result = self._run([_event(10, 'host WKS-011 and xWKS-01 and WKS-01_bak')], [_link(10, 'ioc', 5)],
                           [_asset(1, 'WKS-01')], [_ioc(5, 'evil.example')])
        self.assertEqual([], result)

    def test_match_is_case_insensitive_and_searches_content_and_raw(self):
        result = self._run([_event(10, 'x', content='seen on wks-01'), _event(11, 'y', raw='WKS-01 EVIL.EXAMPLE')],
                           [_link(10, 'ioc', 5)], [_asset(1, 'WKS-01')], [_ioc(5, 'evil.example')])
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs')}, _pairs(result))
        self.assertEqual([10, 11], result[0]['event_ids'])

    def test_asset_ip_is_a_term(self):
        result = self._run([_event(10, 'RDP from 192.168.1.20')], [_link(10, 'ioc', 5)],
                           [_asset(1, 'WKS-01', ip='192.168.1.20')], [_ioc(5, 'evil.example')])
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs')}, _pairs(result))

    def test_linked_entity_is_not_suggested(self):
        result = self._run([_event(10, 'WKS-01 beacons to evil.example')],
                           [_link(10, 'asset', 1), _link(10, 'ioc', 5)],
                           [_asset(1, 'WKS-01')], [_ioc(5, 'evil.example')])
        self.assertEqual([], result)

    def test_mentioned_entities_pair_up(self):
        result = self._run([_event(10, 'WKS-01 beacons to evil.example')], [],
                           [_asset(1, 'WKS-01')], [_ioc(5, 'evil.example')])
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs')}, _pairs(result))

    def test_short_values_and_hidden_events_are_ignored(self):
        result = self._run([_event(10, 'ab cd'), _event(11, 'evil.example', in_graph=False)], [_link(10, 'asset', 1),
                                                                                             _link(11, 'asset', 1)],
                           [_asset(1, 'WKS-01')], [_ioc(5, 'ab'), _ioc(6, 'evil.example')])
        self.assertEqual([], result)

    def test_more_events_raise_the_score(self):
        one = self._run([_event(10, 'evil.example')], [_link(10, 'asset', 1)], [_asset(1, 'WKS-01')],
                        [_ioc(5, 'evil.example')])
        two = self._run([_event(10, 'evil.example'), _event(11, 'evil.example')],
                        [_link(10, 'asset', 1), _link(11, 'asset', 1)], [_asset(1, 'WKS-01')],
                        [_ioc(5, 'evil.example')])
        self.assertGreater(two[0]['score'], one[0]['score'])
        self.assertEqual(one[0]['key'], two[0]['key'])


class TestIpMatches(TestCase):

    def test_asset_ip_equal_to_ioc_value(self):
        result = case_graph_suggest_ip_matches([_asset(1, 'SRV', ip='10.0.0.1, 10.0.0.2')],
                                               [_ioc(5, '10.0.0.2'), _ioc(6, '10.0.0.12')])
        self.assertEqual({('asset:1', 'ioc:5', 'same_as')}, _pairs(result))
        self.assertEqual('ip_match', result[0]['kind'])
        self.assertEqual([], result[0]['event_ids'])

    def test_addresses_are_compared_as_ips(self):
        result = case_graph_suggest_ip_matches([_asset(1, 'SRV', ip='2001:db8::1')], [_ioc(5, '2001:DB8:0::1')])
        self.assertEqual({('asset:1', 'ioc:5', 'same_as')}, _pairs(result))

    def test_non_ip_values_are_ignored(self):
        self.assertEqual([], case_graph_suggest_ip_matches([_asset(1, 'SRV', ip='n/a')], [_ioc(5, 'n/a')]))


class TestAlertLinks(TestCase):

    def test_entities_of_one_alert_co_occur(self):
        links = [{'alert_id': 3, 'kind': 'asset', 'object_id': 1}, {'alert_id': 3, 'kind': 'ioc', 'object_id': 5},
                 {'alert_id': 3, 'kind': 'ioc', 'object_id': 6}]
        result = case_graph_suggest_alert_links([{'alert_id': 3, 'alert_title': 'Beacon'}], links)
        self.assertEqual({('asset:1', 'ioc:5', 'co_occurs'), ('asset:1', 'ioc:6', 'co_occurs'),
                          ('ioc:5', 'ioc:6', 'co_occurs')}, _pairs(result))
        self.assertTrue(all(item['alert_ids'] == [3] for item in result))
        self.assertIn('Beacon', result[0]['reason'])

    def test_alert_with_too_many_entities_is_skipped(self):
        links = [{'alert_id': 3, 'kind': 'ioc', 'object_id': index} for index in range(1, 40)]
        self.assertEqual([], case_graph_suggest_alert_links([{'alert_id': 3}], links))

    def test_same_pair_in_two_alerts_is_one_suggestion(self):
        links = [{'alert_id': alert_id, 'kind': kind, 'object_id': object_id}
                 for alert_id in (3, 4) for kind, object_id in (('asset', 1), ('ioc', 5))]
        result = case_graph_suggest_alert_links([{'alert_id': 3}, {'alert_id': 4}], links)
        self.assertEqual(1, len(result))
        self.assertEqual([3, 4], result[0]['alert_ids'])


class TestEnrichmentResolutions(TestCase):

    def test_resolution_values_are_found_under_resolution_keys(self):
        enrichment = {'virustotal': {'last_dns_records': [{'type': 'A', 'value': '1.2.3.4'}],
                                     'whois': 'registrar 5.6.7.8'},
                      'passivedns': {'resolutions': ['9.9.9.9']}}
        values = case_graph_suggest_resolution_values(enrichment)
        self.assertIn('1.2.3.4', values)
        self.assertIn('9.9.9.9', values)
        self.assertNotIn('registrar 5.6.7.8', values)

    def test_domain_resolves_to_ip(self):
        iocs = [_ioc(5, 'evil.example', {'virustotal': {'resolutions': ['1.2.3.4']}}), _ioc(6, '1.2.3.4')]
        result = case_graph_suggest_enrichment_resolutions(iocs)
        self.assertEqual({('ioc:5', 'ioc:6', 'resolves_to')}, _pairs(result))
        self.assertTrue(result[0]['directed'])

    def test_ip_enriched_with_domains_gives_domain_to_ip(self):
        iocs = [_ioc(5, 'evil.example.'), _ioc(6, '1.2.3.4', {'vt': {'last_dns_records': [{'value': 'Evil.Example.'}]}})]
        result = case_graph_suggest_enrichment_resolutions(iocs)
        self.assertEqual({('ioc:5', 'ioc:6', 'resolves_to')}, _pairs(result))

    def test_unrelated_enrichment_gives_nothing(self):
        iocs = [_ioc(5, 'evil.example', {'vt': {'comment': '1.2.3.4'}}), _ioc(6, '1.2.3.4')]
        self.assertEqual([], case_graph_suggest_enrichment_resolutions(iocs))


class TestCompute(TestCase):

    def _data(self, **overrides):
        data = {
            'relation_types': [{'id': 1, 'name': 'same_as'}, {'id': 2, 'name': 'co_occurs'}],
            'assets': [_asset(1, 'WKS-01', ip='10.0.0.1')],
            'iocs': [_ioc(5, '10.0.0.1')],
            'events': [_event(10, 'WKS-01 talks to 10.0.0.1')],
            'event_links': [],
            'alerts': [],
            'alert_links': [],
            'stored_edges': [],
        }
        data.update(overrides)
        return data

    def test_best_suggestion_of_a_pair_wins(self):
        result = case_graph_suggest_compute(self._data())
        self.assertEqual(['ip_match', 'text_mention'], [item['kind'] for item in result])

    def test_stored_edge_in_any_review_state_excludes_the_suggestion(self):
        for state in ('accepted', 'pending', 'rejected'):
            stored = [{'source_kind': 'ioc', 'source_id': 5, 'target_kind': 'asset', 'target_id': 1,
                       'relation_type_id': 1, 'review_state': state}]
            result = case_graph_suggest_compute(self._data(stored_edges=stored))
            self.assertEqual(['text_mention'], [item['kind'] for item in result], state)

    def test_existing_derived_edge_excludes_the_suggestion(self):
        existing = [{'source': 'asset:1', 'target': 'ioc:5', 'relation': {'name': 'co_occurs'}}]
        result = case_graph_suggest_compute(self._data(), existing)
        self.assertEqual(['ip_match'], [item['kind'] for item in result])

    def test_dismissed_key_is_excluded(self):
        first = case_graph_suggest_compute(self._data())
        result = case_graph_suggest_compute(self._data(), dismissed_keys={first[0]['key']})
        self.assertEqual(['text_mention'], [item['kind'] for item in result])

    def test_items_carry_the_contract_fields(self):
        item = case_graph_suggest_compute(self._data())[0]
        for field in ('key', 'kind', 'source', 'target', 'relation', 'directed', 'reason', 'event_ids', 'score'):
            self.assertIn(field, item)
