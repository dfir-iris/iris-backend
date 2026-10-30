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

"""Heuristic edge suggestions of the investigation graph (§6), pure.

Every heuristic takes the plain rows of `case_graph_load_data` and returns
suggestions:

    {key, kind, source, target, relation, directed, reason, event_ids,
     alert_ids, score}

`key` is `s:<hash>` of the kind, the endpoints (sorted when undirected)
and the relation, so a dismissal sticks across reads. The same pair found
several times by one heuristic is one suggestion (its events / alerts
merged, its score raised). `case_graph_suggest_compute` runs them all and
drops what the graph already holds (a stored edge in any review state, a
derived edge) and what was dismissed.
"""

import hashlib
import ipaddress
import re

from app.business.case_graph_derive import case_graph_event_in_graph
from app.business.case_graph_derive import case_graph_key


KIND_TEXT_MENTION = 'text_mention'
KIND_IP_MATCH = 'ip_match'
KIND_ALERT_LINK = 'alert_link'
KIND_ENRICHMENT_RESOLUTION = 'enrichment_resolution'

CASE_GRAPH_SUGGESTION_KINDS = (KIND_TEXT_MENTION, KIND_IP_MATCH, KIND_ALERT_LINK, KIND_ENRICHMENT_RESOLUTION)

CASE_GRAPH_SUGGESTION_KEY = re.compile(r'^s:[0-9a-f]{16}$')

# Values shorter than this are not looked for in event texts (noise).
_MENTION_MIN_LENGTH = 3
# Characters of an event (title + content + raw) searched for mentions.
_MENTION_TEXT_MAX = 200000
# An alert linking more entities than this is not paired up (noise).
_ALERT_MAX_ENTITIES = 30
# Bounds of the walk through an enrichment document.
_ENRICHMENT_MAX_DEPTH = 8
_ENRICHMENT_MAX_ITEMS = 5000

_SCORES = {KIND_TEXT_MENTION: 50, KIND_IP_MATCH: 90, KIND_ALERT_LINK: 60, KIND_ENRICHMENT_RESOLUTION: 80}
_SCORE_STEP = 10
_SCORE_MAX = 95

# Key tokens (split on non-alphanumerics) under which an enrichment
# value is taken for a resolution (`resolutions`, `last_dns_records`,
# `a_records`, `resolved_ips`, `hostnames`...).
_RESOLUTION_TOKENS = frozenset({
    'resolution', 'resolutions', 'resolve', 'resolves', 'resolved', 'resolver', 'dns', 'a', 'aaaa', 'cname',
    'record', 'records', 'ip', 'ips', 'ipv4', 'ipv6', 'address', 'addresses', 'host', 'hosts', 'hostname',
    'hostnames', 'domain', 'domains', 'fqdn', 'passive',
})


def case_graph_suggestion_key(kind, source, target, relation, directed):
    """Deterministic `s:<hash>` key; an undirected pair is sorted."""
    if not directed and target < source:
        source, target = target, source
    raw = f'{kind}|{source}|{target}|{relation}'
    return f's:{hashlib.sha1(raw.encode()).hexdigest()[:16]}'


class _Collector:
    """Suggestions of one heuristic, merged by key."""

    def __init__(self, kind, relation, directed):
        self._kind = kind
        self._relation = relation
        self._directed = directed
        self._items = {}

    def add(self, source, target, reason, event_id=None, alert_id=None):
        if source == target:
            return
        if not self._directed and target < source:
            source, target = target, source
        key = case_graph_suggestion_key(self._kind, source, target, self._relation, self._directed)
        item = self._items.get(key)
        if item is None:
            item = {'key': key, 'kind': self._kind, 'source': source, 'target': target,
                    'relation': self._relation, 'directed': self._directed, 'reason': reason,
                    'event_ids': [], 'alert_ids': [], 'score': _SCORES[self._kind], '_hits': 0}
            self._items[key] = item
        item['_hits'] += 1
        if event_id is not None and event_id not in item['event_ids']:
            item['event_ids'].append(event_id)
        if alert_id is not None and alert_id not in item['alert_ids']:
            item['alert_ids'].append(alert_id)

    def items(self):
        result = []
        for item in self._items.values():
            hits = item.pop('_hits')
            item['score'] = min(_SCORE_MAX, item['score'] + _SCORE_STEP * (hits - 1))
            item['event_ids'].sort()
            item['alert_ids'].sort()
            result.append(item)
        return result


# ---------------------------------------------------------- text_mention --

def _mention_pattern(value):
    """`value` as a whole token: not glued to a letter, a digit, `_` or
    `-`, nor to a `.` that continues the token (`10.0.0.1` does not match
    in `10.0.0.12` nor in `110.0.0.1`, but does in `to 10.0.0.1.`)."""
    return re.compile(rf'(?<![\w\-])(?<!\w\.){re.escape(value)}(?![\w\-])(?!\.\w)', re.IGNORECASE)


def _entity_terms(assets, iocs):
    """`[(key, label, [terms])]`: the asset names and IPs, the IOC values."""
    entities = []
    for row in assets:
        terms = []
        for value in [row.get('asset_name')] + _split_ips(row.get('asset_ip')):
            value = (value or '').strip()
            if len(value) >= _MENTION_MIN_LENGTH and value.lower() not in (term.lower() for term in terms):
                terms.append(value)
        if terms:
            entities.append((case_graph_key('asset', row['asset_id']), row.get('asset_name') or terms[0], terms))
    for row in iocs:
        value = (row.get('ioc_value') or '').strip()
        if len(value) >= _MENTION_MIN_LENGTH:
            entities.append((case_graph_key('ioc', row['ioc_id']), value, [value]))
    return entities


def _event_text(event):
    parts = [event.get(field) for field in ('event_title', 'event_content', 'event_raw')]
    return '\n'.join(str(part) for part in parts if part)[:_MENTION_TEXT_MAX]


def case_graph_suggest_text_mentions(events, event_links, assets, iocs):
    """An asset name / IP or an IOC value written in an event (title,
    content or raw) the entity is not linked to: `co_occurs` with each
    entity of the event (linked or mentioned)."""
    collector = _Collector(KIND_TEXT_MENTION, 'co_occurs', False)
    entities = _entity_terms(assets, iocs)
    linked_by_event = {}
    for link in event_links:
        if link.get('kind') in ('asset', 'ioc'):
            keys = linked_by_event.setdefault(link['event_id'], [])
            key = case_graph_key(link['kind'], link['object_id'])
            if key not in keys:
                keys.append(key)
    patterns = {}

    for event in events:
        if not case_graph_event_in_graph(event):
            continue
        text = _event_text(event)
        if not text:
            continue
        lowered = text.lower()
        linked = linked_by_event.get(event['event_id'], [])
        mentioned = []
        for key, label, terms in entities:
            if key in linked:
                continue
            for term in terms:
                if term.lower() not in lowered:
                    continue
                pattern = patterns.get(term)
                if pattern is None:
                    pattern = patterns[term] = _mention_pattern(term)
                if pattern.search(text):
                    mentioned.append((key, label))
                    break
        if not mentioned:
            continue
        title = event.get('event_title') or f'#{event["event_id"]}'
        mentioned_keys = [key for key, _ in mentioned]
        for index, (key, label) in enumerate(mentioned):
            reason = f'"{label}" is mentioned in the event "{title}" but not linked to it'
            for other in linked + mentioned_keys[index + 1:]:
                collector.add(key, other, reason, event_id=event['event_id'])
    return collector.items()


# -------------------------------------------------------------- ip_match --

def _split_ips(raw):
    """Valid IP addresses of an asset IP field (`10.0.0.1, 10.0.0.2`)."""
    result = []
    for part in re.split(r'[\s,;|]+', str(raw or '')):
        address = _parse_ip(part)
        if address is not None and part not in result:
            result.append(part)
    return result


def _parse_ip(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def case_graph_suggest_ip_matches(assets, iocs):
    """An asset IP equal to an IOC value: `same_as`."""
    collector = _Collector(KIND_IP_MATCH, 'same_as', False)
    iocs_by_ip = {}
    for row in iocs:
        address = _parse_ip(row.get('ioc_value'))
        if address is not None:
            iocs_by_ip.setdefault(address, []).append(row)
    for row in assets:
        for raw in _split_ips(row.get('asset_ip')):
            for ioc in iocs_by_ip.get(_parse_ip(raw), []):
                name = row.get('asset_name') or f'#{row["asset_id"]}'
                collector.add(case_graph_key('asset', row['asset_id']), case_graph_key('ioc', ioc['ioc_id']),
                              f'The IP {raw} of the asset "{name}" is the IOC {ioc["ioc_value"]}')
    return collector.items()


# ------------------------------------------------------------ alert_link --

def case_graph_suggest_alert_links(alerts, alert_links):
    """Assets and IOCs linked by the same alert merged into the case:
    `co_occurs` between each pair."""
    collector = _Collector(KIND_ALERT_LINK, 'co_occurs', False)
    titles = {row['alert_id']: row.get('alert_title') for row in alerts}
    entities_by_alert = {}
    for link in alert_links:
        if link.get('kind') not in ('asset', 'ioc'):
            continue
        keys = entities_by_alert.setdefault(link['alert_id'], [])
        key = case_graph_key(link['kind'], link['object_id'])
        if key not in keys:
            keys.append(key)
    for alert_id in sorted(entities_by_alert):
        keys = entities_by_alert[alert_id]
        if len(keys) > _ALERT_MAX_ENTITIES:
            continue
        title = titles.get(alert_id) or f'#{alert_id}'
        reason = f'Linked together by the alert "{title}"'
        for index, key in enumerate(keys):
            for other in keys[index + 1:]:
                collector.add(key, other, reason, alert_id=alert_id)
    return collector.items()


# ------------------------------------------------- enrichment_resolution --

def _key_tokens(name):
    return {token for token in re.split(r'[^a-z0-9]+', str(name).lower()) if token}


def _is_resolution_key(name):
    tokens = _key_tokens(name)
    return bool(tokens & _RESOLUTION_TOKENS) or any(token.startswith('resol') for token in tokens)


def case_graph_suggest_resolution_values(enrichment):
    """Strings found under a resolution-like key of an enrichment
    document (`{source: {...}}` as modules and workflows write it), at
    any depth below that key."""
    values = []
    budget = [_ENRICHMENT_MAX_ITEMS]

    def _walk(node, in_resolution, depth):
        if budget[0] <= 0 or depth > _ENRICHMENT_MAX_DEPTH:
            return
        budget[0] -= 1
        if isinstance(node, dict):
            for name, child in node.items():
                _walk(child, in_resolution or _is_resolution_key(name), depth + 1)
        elif isinstance(node, (list, tuple)):
            for child in node:
                _walk(child, in_resolution, depth + 1)
        elif isinstance(node, str) and in_resolution:
            value = node.strip().rstrip('.')
            if value and value not in values:
                values.append(value)

    _walk(enrichment, False, 0)
    return values


def case_graph_suggest_enrichment_resolutions(iocs):
    """The enrichment of an IOC names another IOC value of the case as a
    resolution: `resolves_to`, from the name to the address (an IP
    enriched with the domains resolving to it gives domain -> IP)."""
    collector = _Collector(KIND_ENRICHMENT_RESOLUTION, 'resolves_to', True)
    by_value = {}
    for row in iocs:
        value = (row.get('ioc_value') or '').strip().rstrip('.').lower()
        if value:
            by_value.setdefault(value, []).append(row)
    for row in iocs:
        enrichment = row.get('ioc_enrichment')
        if not isinstance(enrichment, (dict, list)):
            continue
        own_value = (row.get('ioc_value') or '').strip()
        own_is_ip = _parse_ip(own_value) is not None
        for value in case_graph_suggest_resolution_values(enrichment):
            for other in by_value.get(value.lower(), []):
                if other['ioc_id'] == row['ioc_id']:
                    continue
                own_key = case_graph_key('ioc', row['ioc_id'])
                other_key = case_graph_key('ioc', other['ioc_id'])
                other_is_ip = _parse_ip(other.get('ioc_value')) is not None
                if own_is_ip and not other_is_ip:
                    source, target = other_key, own_key
                    source_value, target_value = other['ioc_value'], own_value
                else:
                    source, target = own_key, other_key
                    source_value, target_value = own_value, other['ioc_value']
                collector.add(source, target,
                              f'The enrichment of {own_value} resolves {source_value} to {target_value}')
    return collector.items()


# --------------------------------------------------------------- Compute --

def _pairs(edges):
    """`{(frozenset(pair), relation)}` of edges with `source`, `target`
    and `relation` (a name, or a §4.1 relation object)."""
    result = set()
    for edge in edges:
        relation = edge.get('relation')
        if isinstance(relation, dict):
            relation = relation.get('name')
        result.add((frozenset((edge['source'], edge['target'])), relation))
    return result


def case_graph_suggest_compute(data, existing_edges=(), dismissed_keys=frozenset()):
    """Every heuristic suggestion of the case, best first.

    `data`: the rows of `case_graph_load_data` (`assets`, `iocs`,
    `events`, `event_links`, `alerts`, `alert_links`, `stored_edges`).
    `existing_edges`: edges already shown (`{source, target, relation}`);
    the stored edges of `data` in every review state are added. A
    suggestion linking the same pair with the same relation, either way
    round, is dropped, as is a dismissed one."""
    relation_names = {row['id']: row['name']
                      for row in (_relation_row(item) for item in data.get('relation_types') or [])}
    stored = [{'source': case_graph_key(row['source_kind'], row['source_id']),
               'target': case_graph_key(row['target_kind'], row['target_id']),
               'relation': relation_names.get(row['relation_type_id'])}
              for row in data.get('stored_edges') or []]
    taken = _pairs(stored) | _pairs(existing_edges)
    dismissed = set(dismissed_keys or ())

    assets = data.get('assets') or []
    iocs = data.get('iocs') or []
    suggestions = []
    suggestions.extend(case_graph_suggest_ip_matches(assets, iocs))
    suggestions.extend(case_graph_suggest_enrichment_resolutions(iocs))
    suggestions.extend(case_graph_suggest_alert_links(data.get('alerts') or [], data.get('alert_links') or []))
    suggestions.extend(case_graph_suggest_text_mentions(data.get('events') or [], data.get('event_links') or [],
                                                        assets, iocs))

    suggestions.sort(key=lambda item: (-item['score'], CASE_GRAPH_SUGGESTION_KINDS.index(item['kind']),
                                       item['key']))
    result = []
    seen = set()
    for item in suggestions:
        pair = (frozenset((item['source'], item['target'])), item['relation'])
        if item['key'] in dismissed or pair in taken or pair in seen:
            continue
        seen.add(pair)
        result.append(item)
    return result


def _relation_row(item):
    """`{id, name}` of a relation type row (model or dict)."""
    if isinstance(item, dict):
        return {'id': item.get('id'), 'name': item.get('name')}
    return {'id': getattr(item, 'id', None), 'name': getattr(item, 'name', None)}
