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

"""Read-time derivation of the investigation graph edges (pure).

Node keys are `"<kind>:<id>"`. A derived edge is a dict:

    {source, target, relation (name), directed, origin, event_ids,
     first_seen, last_seen}

built from the timeline events of the case and their asset / IOC links
(each carrying a `role`: `source`, `target` or `involved`), the IOC to
asset links and the parent events. Nothing here touches the database.
"""

import hashlib

from app.models.errors import BusinessProcessingError


CASE_GRAPH_KINDS = ('asset', 'ioc', 'event', 'evidence', 'task', 'note', 'alert', 'node', 'case')

ROLE_SOURCE = 'source'
ROLE_TARGET = 'target'
ROLE_INVOLVED = 'involved'

ORIGIN_EVENT = 'event'
ORIGIN_IOC_ASSET_LINK = 'ioc_asset_link'
ORIGIN_PARENT_EVENT = 'parent_event'
ORIGIN_ALERT = 'alert'
ORIGIN_CROSS_CASE = 'cross_case'

# Category name fragment (lower case) -> relation of the directed edges.
_CATEGORY_RELATIONS = (
    ('lateral movement', 'lateral_movement'),
    ('exfiltration', 'exfiltrated_to'),
    ('command and control', 'beacons_to'),
    ('execution', 'executed_on'),
)
_AUTHENTICATION_CATEGORIES = ('credential access', 'initial access')


def case_graph_key(kind, object_id):
    return f'{kind}:{int(object_id)}'


def case_graph_parse_key(key):
    """`(kind, id)` of a node key; BusinessProcessingError when malformed."""
    if not isinstance(key, str) or ':' not in key:
        raise BusinessProcessingError(f'Invalid node key {key!r}')
    kind, _, raw_id = key.partition(':')
    if kind not in CASE_GRAPH_KINDS or not raw_id.isdigit() or int(raw_id) <= 0:
        raise BusinessProcessingError(f'Invalid node key {key!r}')
    return kind, int(raw_id)


def case_graph_derive_relation(category_name, has_account=False):
    """Relation of the directed edges of an event, from its category."""
    name = (category_name or '').lower()
    for fragment, relation in _CATEGORY_RELATIONS:
        if fragment in name:
            return relation
    if has_account and any(fragment in name for fragment in _AUTHENTICATION_CATEGORIES):
        return 'authenticated_to'
    return 'connected_to'


def case_graph_derived_edge_key(edge):
    """Deterministic `d:<hash>` key of a derived edge."""
    raw = f'{edge["source"]}|{edge["target"]}|{edge["relation"]}|{int(bool(edge["directed"]))}|{edge["origin"]}'
    return f'd:{hashlib.sha1(raw.encode()).hexdigest()[:16]}'


class _EdgeAccumulator:
    """Edges keyed by (source, target, relation, directed, origin); an
    undirected pair is sorted. Each contributing event is appended once."""

    def __init__(self):
        self._edges = {}

    def add(self, source, target, relation, directed, origin, event_id=None, date=None):
        if source == target:
            return
        if not directed and target < source:
            source, target = target, source
        key = (source, target, relation, bool(directed), origin)
        edge = self._edges.get(key)
        if edge is None:
            edge = {'source': source, 'target': target, 'relation': relation, 'directed': bool(directed),
                    'origin': origin, 'event_ids': [], 'first_seen': None, 'last_seen': None}
            self._edges[key] = edge
        if event_id is not None and event_id not in edge['event_ids']:
            edge['event_ids'].append(event_id)
        if date is not None:
            if edge['first_seen'] is None or date < edge['first_seen']:
                edge['first_seen'] = date
            if edge['last_seen'] is None or date > edge['last_seen']:
                edge['last_seen'] = date

    def edges(self):
        return list(self._edges.values())


def case_graph_event_in_graph(event):
    return event.get('event_in_graph') is not False


def _event_entities(links):
    """Entity keys of an event grouped by role, in link order, deduplicated."""
    roles = {ROLE_SOURCE: [], ROLE_TARGET: [], ROLE_INVOLVED: []}
    seen = set()
    for link in links:
        key = case_graph_key(link['kind'], link['object_id'])
        if key in seen:
            continue
        seen.add(key)
        role = link.get('role') if link.get('role') in roles else ROLE_INVOLVED
        roles[role].append(key)
    return roles


def _derive_event(accumulator, event, links, account_keys):
    event_id = event['event_id']
    date = event.get('event_date')
    roles = _event_entities(links)
    sources, targets, involved = roles[ROLE_SOURCE], roles[ROLE_TARGET], roles[ROLE_INVOLVED]

    if sources and targets:
        has_account = any(key in account_keys for key in sources + targets)
        relation = case_graph_derive_relation(event.get('category_name'), has_account)
        for source in sources:
            for target in targets:
                accumulator.add(source, target, relation, True, ORIGIN_EVENT, event_id, date)
        for entity in involved:
            for other in sources + targets:
                accumulator.add(entity, other, 'co_occurs', False, ORIGIN_EVENT, event_id, date)
        return

    entities = sources + targets + involved
    assets = [key for key in entities if key.startswith('asset:')]
    iocs = [key for key in entities if key.startswith('ioc:')]
    for index, asset in enumerate(assets):
        for other in assets[index + 1:]:
            accumulator.add(asset, other, 'co_occurs', False, ORIGIN_EVENT, event_id, date)
    for ioc in iocs:
        for asset in assets:
            accumulator.add(ioc, asset, 'co_occurs', False, ORIGIN_EVENT, event_id, date)
    if not assets:
        for index, ioc in enumerate(iocs):
            for other in iocs[index + 1:]:
                accumulator.add(ioc, other, 'co_occurs', False, ORIGIN_EVENT, event_id, date)


def case_graph_derive_edges(events, event_links, ioc_asset_links=(), include_events=False,
                            account_keys=frozenset()):
    """Derived edges of a case (§3.5).

    `events`: dicts with `event_id`, `event_date`, `event_in_graph`,
    `parent_event_id`, `category_name`. `event_links`: dicts with
    `event_id`, `kind` (`asset` | `ioc`), `object_id`, `role`.
    `ioc_asset_links`: dicts with `ioc_id`, `asset_id`. `account_keys`:
    node keys of the entities that are accounts (for `authenticated_to`).
    """
    accumulator = _EdgeAccumulator()
    in_graph = {event['event_id']: event for event in events if case_graph_event_in_graph(event)}

    links_by_event = {}
    for link in event_links:
        if link['event_id'] in in_graph:
            links_by_event.setdefault(link['event_id'], []).append(link)

    for event_id, event in in_graph.items():
        _derive_event(accumulator, event, links_by_event.get(event_id, []), account_keys)

    for link in ioc_asset_links:
        accumulator.add(case_graph_key('ioc', link['ioc_id']), case_graph_key('asset', link['asset_id']),
                        'related_to', False, ORIGIN_IOC_ASSET_LINK)

    if include_events:
        for event_id, event in in_graph.items():
            parent_id = event.get('parent_event_id')
            if parent_id and parent_id in in_graph:
                accumulator.add(case_graph_key('event', parent_id), case_graph_key('event', event_id),
                                'parent_of', True, ORIGIN_PARENT_EVENT, event_id, event.get('event_date'))
            event_key = case_graph_key('event', event_id)
            for key, role in _links_with_roles(links_by_event.get(event_id, [])):
                if role == ROLE_TARGET:
                    accumulator.add(event_key, key, 'involved_in', True, ORIGIN_EVENT, event_id,
                                    event.get('event_date'))
                else:
                    accumulator.add(key, event_key, 'involved_in', True, ORIGIN_EVENT, event_id,
                                    event.get('event_date'))

    return accumulator.edges()


def _links_with_roles(links):
    seen = set()
    for link in links:
        key = case_graph_key(link['kind'], link['object_id'])
        if key in seen:
            continue
        seen.add(key)
        yield key, link.get('role') or ROLE_INVOLVED


def _same_pair(stored, derived):
    if stored['source'] == derived['source'] and stored['target'] == derived['target']:
        return True
    # An undirected derived edge has a sorted pair: an undirected stored
    # edge between the same two nodes, either way round, is the same link.
    return (not stored['directed'] and not derived['directed']
            and stored['source'] == derived['target'] and stored['target'] == derived['source'])


def case_graph_merge_edges(stored_edges, derived_edges, event_dates=None):
    """Fold every derived edge into the stored edge linking the same
    pair with the same relation (its `event_ids` unioned, the derived
    edge dropped). Stored edges carry `source`, `target`, `relation`
    (name), `directed` and `event_ids`. Returns (stored, remaining
    derived); the stored dicts are updated in place.

    An event cited by a stored edge is that edge's evidence: whatever the
    relation, it no longer derives an edge between the same two nodes.
    The derived edge loses that event and is dropped when none is left;
    `event_dates` (`{event_id: date}`) recomputes its first / last seen."""
    index = {}
    cited = {}
    for stored in stored_edges:
        pair = frozenset((stored['source'], stored['target']))
        index.setdefault((pair, stored['relation']), []).append(stored)
        cited.setdefault(pair, set()).update(stored['event_ids'])

    remaining = []
    for derived in derived_edges:
        pair = frozenset((derived['source'], derived['target']))
        candidates = index.get((pair, derived['relation']), [])
        target = next((stored for stored in candidates if _same_pair(stored, derived)), None)
        if target is None:
            derived = _without_cited_events(derived, cited.get(pair), event_dates)
            if derived is not None:
                remaining.append(derived)
            continue
        for event_id in derived['event_ids']:
            if event_id not in target['event_ids']:
                target['event_ids'].append(event_id)
    return stored_edges, remaining


def _without_cited_events(derived, cited, event_dates):
    if not cited or derived['origin'] != ORIGIN_EVENT or not cited.intersection(derived['event_ids']):
        return derived
    event_ids = [event_id for event_id in derived['event_ids'] if event_id not in cited]
    if not event_ids:
        return None
    dates = [(event_dates or {}).get(event_id) for event_id in event_ids]
    dates = [date for date in dates if date is not None]
    return dict(derived, event_ids=event_ids,
                first_seen=min(dates) if dates else derived['first_seen'],
                last_seen=max(dates) if dates else derived['last_seen'])
