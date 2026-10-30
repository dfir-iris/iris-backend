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

"""Export and import formats of the investigation graph (§4.7), pure.

* `case_graph_exchange_to_stix`: STIX 2.1 bundle of an assembled graph.
* `case_graph_exchange_to_graphml`: GraphML document (Gephi, yEd).
* `case_graph_exchange_parse_stix` / `case_graph_exchange_parse_json`:
  normalise an imported document into an import plan
  `{iocs, nodes, assets, edges, skipped}` whose entries reference each
  other by `ref`; `app.business.case_graph` resolves and creates them.
"""

import json
import re
import uuid
from xml.etree import ElementTree

from app.models.errors import BusinessProcessingError


CASE_GRAPH_IMPORT_MAX_CONTENT = 20 * 1024 * 1024

# Namespace of the deterministic SCO identifiers (STIX 2.1, 2.9).
_STIX_SCO_NAMESPACE = uuid.UUID('00abedb4-aa42-466c-9c01-fed23315a9b7')
# Namespace of the other identifiers of an IRIS export (stable per case).
_IRIS_NAMESPACE = uuid.UUID('5c6a1f0e-7f3b-4d64-9a59-2f1f9e4bb0d1')

_FREE_NODE_STIX_TYPES = {
    'actor': 'threat-actor',
    'account': 'user-account',
    'malware': 'malware',
    'infrastructure': 'infrastructure',
    'note': 'note',
}

_STIX_FREE_NODE_TYPES = {
    'threat-actor': 'actor',
    'intrusion-set': 'actor',
    'campaign': 'actor',
    'identity': 'actor',
    'user-account': 'account',
    'malware': 'malware',
    'tool': 'malware',
    'infrastructure': 'infrastructure',
    'note': 'note',
}

_STIX_PATTERN_VALUE = re.compile(r"\[\s*([a-z0-9-]+):([a-z0-9_.'\[\]-]+)\s*=\s*'((?:[^'\\]|\\.)*)'\s*\]",
                                 re.IGNORECASE)

_GRAPHML_NS = 'http://graphml.graphdrawing.org/xmlns'

_GRAPHML_NODE_KEYS = (
    ('kind', 'string'), ('label', 'string'), ('subtitle', 'string'), ('type', 'string'),
    ('compromised', 'boolean'), ('tlp', 'string'), ('criticality', 'string'), ('tags', 'string'),
    ('first_seen', 'string'), ('last_seen', 'string'), ('event_count', 'int'), ('cross_case_count', 'int'),
)
_GRAPHML_EDGE_KEYS = (
    ('relation', 'string'), ('relation_label', 'string'), ('label', 'string'), ('origin', 'string'),
    ('review_state', 'string'), ('confidence', 'int'), ('count', 'int'), ('first_seen', 'string'),
    ('last_seen', 'string'), ('edge_directed', 'boolean'),
)


# ---------------------------------------------------------------- STIX --

def _stix_timestamp(value):
    """`YYYY-MM-DDTHH:MM:SS.sssZ` of an ISO string or datetime (UTC)."""
    if value is None:
        return None
    text = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    match = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?', text)
    if not match:
        return None
    fraction = (match.group(3) or '.000')[1:4].ljust(3, '0')
    return f'{match.group(1)}T{match.group(2)}.{fraction}Z'


def _sco_id(stix_type, contributing):
    canonical = json.dumps(contributing, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return f'{stix_type}--{uuid.uuid5(_STIX_SCO_NAMESPACE, canonical)}'


def _iris_id(stix_type, case_id, key):
    return f'{stix_type}--{uuid.uuid5(_IRIS_NAMESPACE, f"case:{case_id}/{key}")}'


def _escape_pattern(value):
    return value.replace('\\', '\\\\').replace("'", "\\'")


def _ioc_sco(value, type_name):
    """(SCO dict, pattern) of an IOC value, None when it maps to no SCO."""
    type_name = (type_name or '').lower()
    if type_name in ('ip-src', 'ip-dst', 'ip-any', 'ip') or type_name.startswith('ip-'):
        stix_type = 'ipv6-addr' if ':' in value else 'ipv4-addr'
        return {'type': stix_type, 'value': value}, f"[{stix_type}:value = '{_escape_pattern(value)}']"
    if type_name in ('domain', 'hostname'):
        return {'type': 'domain-name', 'value': value}, f"[domain-name:value = '{_escape_pattern(value)}']"
    if type_name in ('url', 'uri', 'link'):
        return {'type': 'url', 'value': value}, f"[url:value = '{_escape_pattern(value)}']"
    if type_name.startswith('email'):
        return {'type': 'email-addr', 'value': value}, f"[email-addr:value = '{_escape_pattern(value)}']"
    hash_names = {'md5': 'MD5', 'sha1': 'SHA-1', 'sha256': 'SHA-256', 'sha512': 'SHA-512'}
    if type_name in hash_names:
        algorithm = hash_names[type_name]
        return ({'type': 'file', 'hashes': {algorithm: value}},
                f"[file:hashes.'{algorithm}' = '{_escape_pattern(value)}']")
    return None


def _node_stix_object(node, case_id, timestamp, neighbour_refs):
    """STIX object of a graph node (None for `case` nodes)."""
    kind = node['kind']
    label = node.get('label') or node['key']
    common = {'spec_version': '2.1', 'x_iris_key': node['key']}
    if kind == 'case':
        return None
    if kind == 'ioc':
        sco = _ioc_sco(label, node.get('type'))
        if sco is not None:
            body, _ = sco
            contributing = {key: value for key, value in body.items() if key != 'type'}
            return dict(body, id=_sco_id(body['type'], contributing), **common)
        return dict(type='indicator', id=_iris_id('indicator', case_id, node['key']), created=timestamp,
                    modified=timestamp, name=label, description=node.get('description'),
                    pattern=f"[x-iris-ioc:value = '{_escape_pattern(label)}']", pattern_type='stix',
                    valid_from=timestamp, x_iris_ioc_type=node.get('type'), **common)
    if kind == 'asset':
        return dict(type='infrastructure', id=_iris_id('infrastructure', case_id, node['key']), created=timestamp,
                    modified=timestamp, name=label, description=node.get('description'),
                    x_iris_asset_type=node.get('type'), x_iris_compromised=node.get('compromised'), **common)
    if kind == 'node':
        stix_type = _FREE_NODE_STIX_TYPES.get(node.get('type'))
        if stix_type == 'user-account':
            return dict(type='user-account', id=_sco_id('user-account', {'account_login': label}),
                        account_login=label, display_name=label, **common)
        if stix_type == 'malware':
            return dict(type='malware', id=_iris_id('malware', case_id, node['key']), created=timestamp,
                        modified=timestamp, name=label, description=node.get('description'), is_family=False,
                        **common)
        if stix_type == 'note' and neighbour_refs:
            return dict(type='note', id=_iris_id('note', case_id, node['key']), created=timestamp,
                        modified=timestamp, abstract=label, content=node.get('description') or label,
                        object_refs=neighbour_refs, **common)
        if stix_type in ('threat-actor', 'infrastructure'):
            return dict(type=stix_type, id=_iris_id(stix_type, case_id, node['key']), created=timestamp,
                        modified=timestamp, name=label, description=node.get('description'), **common)
    return dict(type='x-iris-graph-node', id=_iris_id('x-iris-graph-node', case_id, node['key']),
                created=timestamp, modified=timestamp, name=label, description=node.get('description'),
                x_iris_kind=kind, x_iris_node_type=node.get('type'), **common)


def _clean(obj):
    return {key: value for key, value in obj.items() if value is not None}


def case_graph_exchange_to_stix(graph, relation_types, case_id, generated_at):
    """STIX 2.1 bundle of an assembled graph. Edges become
    `relationship` objects typed by the relation `stix_relationship`."""
    timestamp = _stix_timestamp(generated_at) or '1970-01-01T00:00:00.000Z'
    stix_by_relation = {row['name']: row.get('stix_relationship') or 'related-to' for row in relation_types}
    nodes = graph.get('nodes') or []
    edges = graph.get('edges') or []

    neighbours = {}
    for edge in edges:
        neighbours.setdefault(edge['source'], []).append(edge['target'])
        neighbours.setdefault(edge['target'], []).append(edge['source'])

    # Two passes: notes reference the ids of their neighbours.
    ids = {}
    objects = []
    for node in nodes:
        if node['kind'] == 'node' and node.get('type') == 'note':
            continue
        obj = _node_stix_object(node, case_id, timestamp, [])
        if obj is not None:
            ids[node['key']] = obj['id']
            objects.append(_clean(obj))
    for node in nodes:
        if node['kind'] != 'node' or node.get('type') != 'note':
            continue
        refs = [ids[key] for key in neighbours.get(node['key'], []) if key in ids]
        obj = _node_stix_object(node, case_id, timestamp, sorted(set(refs)))
        ids[node['key']] = obj['id']
        objects.append(_clean(obj))

    for edge in edges:
        source, target = ids.get(edge['source']), ids.get(edge['target'])
        if source is None or target is None:
            continue
        relation = (edge.get('relation') or {}).get('name')
        objects.append(_clean({
            'type': 'relationship', 'spec_version': '2.1',
            'id': _iris_id('relationship', case_id, edge['key']),
            'created': timestamp, 'modified': timestamp,
            'relationship_type': stix_by_relation.get(relation, 'related-to'),
            'source_ref': source, 'target_ref': target,
            'description': edge.get('description') or edge.get('label'),
            'confidence': edge.get('confidence'),
            'start_time': _stix_timestamp(edge.get('first_seen')),
            'stop_time': _stix_timestamp(edge.get('last_seen')),
            'x_iris_relation': relation,
            'x_iris_directed': bool(edge.get('directed')),
            'x_iris_origin': edge.get('origin'),
        }))

    # The same SCO may come from two IOCs (same value): keep the first.
    unique = []
    seen = set()
    for obj in objects:
        if obj['id'] in seen:
            continue
        seen.add(obj['id'])
        unique.append(obj)
    return {'type': 'bundle', 'id': f'bundle--{uuid.uuid4()}', 'objects': unique}


# ------------------------------------------------------------- GraphML --

def _graphml_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ','.join(str(item) for item in value)
    return str(value)


def case_graph_exchange_to_graphml(graph):
    """GraphML document (str) of an assembled graph."""
    ElementTree.register_namespace('', _GRAPHML_NS)
    root = ElementTree.Element(f'{{{_GRAPHML_NS}}}graphml')
    for name, attr_type in _GRAPHML_NODE_KEYS:
        ElementTree.SubElement(root, f'{{{_GRAPHML_NS}}}key', id=f'n_{name}', attrib={
            'for': 'node', 'attr.name': name, 'attr.type': attr_type})
    for name, attr_type in _GRAPHML_EDGE_KEYS:
        ElementTree.SubElement(root, f'{{{_GRAPHML_NS}}}key', id=f'e_{name}', attrib={
            'for': 'edge', 'attr.name': name, 'attr.type': attr_type})
    graph_element = ElementTree.SubElement(root, f'{{{_GRAPHML_NS}}}graph', id='case_graph', edgedefault='directed')

    for node in graph.get('nodes') or []:
        element = ElementTree.SubElement(graph_element, f'{{{_GRAPHML_NS}}}node', id=node['key'])
        for name, _ in _GRAPHML_NODE_KEYS:
            value = node.get(name)
            if value is None or value == []:
                continue
            data = ElementTree.SubElement(element, f'{{{_GRAPHML_NS}}}data', key=f'n_{name}')
            data.text = _graphml_value(value)

    for edge in graph.get('edges') or []:
        element = ElementTree.SubElement(graph_element, f'{{{_GRAPHML_NS}}}edge', id=edge['key'],
                                         source=edge['source'], target=edge['target'],
                                         directed='true' if edge.get('directed') else 'false')
        relation = edge.get('relation') or {}
        values = dict(edge, relation=relation.get('name'), relation_label=relation.get('label'),
                      edge_directed=bool(edge.get('directed')))
        for name, _ in _GRAPHML_EDGE_KEYS:
            value = values.get(name)
            if value is None:
                continue
            data = ElementTree.SubElement(element, f'{{{_GRAPHML_NS}}}data', key=f'e_{name}')
            data.text = _graphml_value(value)

    body = ElementTree.tostring(root, encoding='unicode')
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}'


# -------------------------------------------------------------- Import --

def _load(content):
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str) or not content.strip():
        raise BusinessProcessingError('content is required')
    if len(content) > CASE_GRAPH_IMPORT_MAX_CONTENT:
        raise BusinessProcessingError('content is too large')
    try:
        return json.loads(content)
    except ValueError as e:
        raise BusinessProcessingError(f'content is not valid JSON: {e}') from e


def _empty_plan():
    return {'iocs': [], 'nodes': [], 'assets': [], 'edges': [], 'skipped': []}


def _pattern_value(pattern):
    """(object type, value) of a simple `[type:prop = 'value']` pattern."""
    match = _STIX_PATTERN_VALUE.search(pattern or '')
    if not match:
        return None
    value = re.sub(r'\\(.)', r'\1', match.group(3))
    return match.group(1).lower(), value


_SCO_IOC_TYPES = {
    'ipv4-addr': 'ip-any',
    'ipv6-addr': 'ip-any',
    'domain-name': 'domain',
    'url': 'url',
    'email-addr': 'email',
}
_HASH_PREFERENCE = (('SHA-256', 'sha256'), ('SHA-1', 'sha1'), ('MD5', 'md5'), ('SHA-512', 'sha512'))


def _stix_file_hash(hashes):
    if not isinstance(hashes, dict):
        return None
    normalised = {str(key).upper().replace('_', '-'): value for key, value in hashes.items()}
    for algorithm, type_name in _HASH_PREFERENCE:
        value = normalised.get(algorithm) or normalised.get(algorithm.replace('-', ''))
        if isinstance(value, str) and value:
            return value, type_name
    return None


def _stix_object_plan(obj, plan):
    stix_type = obj.get('type')
    ref = obj.get('id')
    description = obj.get('description') if isinstance(obj.get('description'), str) else None
    if stix_type in _SCO_IOC_TYPES and isinstance(obj.get('value'), str):
        plan['iocs'].append({'ref': ref, 'value': obj['value'], 'type_hint': _SCO_IOC_TYPES[stix_type],
                             'description': description})
        return
    if stix_type == 'file':
        found = _stix_file_hash(obj.get('hashes'))
        if found:
            plan['iocs'].append({'ref': ref, 'value': found[0], 'type_hint': found[1], 'description': description})
            return
        if isinstance(obj.get('name'), str):
            plan['nodes'].append({'ref': ref, 'label': obj['name'], 'node_type': 'other', 'description': description})
            return
    if stix_type == 'indicator':
        found = _pattern_value(obj.get('pattern'))
        if found:
            object_type, value = found
            type_hint = obj.get('x_iris_ioc_type') or _SCO_IOC_TYPES.get(object_type)
            plan['iocs'].append({'ref': ref, 'value': value, 'type_hint': type_hint,
                                 'description': description or obj.get('name')})
            return
    if stix_type == 'infrastructure' and obj.get('x_iris_key', '').startswith('asset:'):
        plan['assets'].append({'ref': ref, 'label': obj.get('name'), 'type_name': obj.get('x_iris_asset_type'),
                               'description': description})
        return
    if stix_type == 'x-iris-graph-node' and isinstance(obj.get('name'), str):
        plan['nodes'].append({'ref': ref, 'label': obj['name'], 'node_type': obj.get('x_iris_node_type') or 'other',
                              'description': description})
        return
    if stix_type in _STIX_FREE_NODE_TYPES:
        label = (obj.get('name') or obj.get('account_login') or obj.get('display_name') or obj.get('user_id')
                 or obj.get('abstract'))
        if stix_type == 'note':
            description = obj.get('content') if isinstance(obj.get('content'), str) else description
            label = label or (description or '')[:80]
        if isinstance(label, str) and label.strip():
            plan['nodes'].append({'ref': ref, 'label': label.strip(), 'node_type': _STIX_FREE_NODE_TYPES[stix_type],
                                  'description': description})
            return
    plan['skipped'].append(f'{stix_type}:{ref}')


def case_graph_exchange_parse_stix(content):
    """Import plan of a STIX 2.x bundle (or list of objects)."""
    document = _load(content)
    objects = document.get('objects') if isinstance(document, dict) else document
    if not isinstance(objects, list):
        raise BusinessProcessingError('content is not a STIX bundle')
    plan = _empty_plan()
    relationships = []
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str):
            plan['skipped'].append(str(obj)[:80])
            continue
        if obj.get('type') == 'relationship':
            relationships.append(obj)
            continue
        _stix_object_plan(obj, plan)
    for obj in relationships:
        if not isinstance(obj.get('source_ref'), str) or not isinstance(obj.get('target_ref'), str):
            plan['skipped'].append(f'relationship:{obj.get("id")}')
            continue
        plan['edges'].append({
            'source_ref': obj['source_ref'], 'target_ref': obj['target_ref'],
            'relation': obj.get('x_iris_relation'),
            'stix_relationship': obj.get('relationship_type'),
            'directed': obj.get('x_iris_directed'),
            'description': obj.get('description'),
            'confidence': obj.get('confidence'),
            'first_seen': obj.get('start_time'),
            'last_seen': obj.get('stop_time'),
            'ref': obj['id'],
        })
    return plan


def case_graph_exchange_parse_json(content):
    """Import plan of an IRIS graph export (`format=json`). Derived edges
    (`editable: false`) are skipped: they are recomputed from case data."""
    document = _load(content)
    if not isinstance(document, dict) or not isinstance(document.get('nodes'), list):
        raise BusinessProcessingError('content is not an IRIS graph export')
    plan = _empty_plan()
    for node in document['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('key'), str):
            plan['skipped'].append(str(node)[:80])
            continue
        kind = node.get('kind')
        label = node.get('label')
        if not isinstance(label, str) or not label.strip():
            plan['skipped'].append(node['key'])
            continue
        if kind == 'ioc':
            plan['iocs'].append({'ref': node['key'], 'value': label.strip(), 'type_hint': node.get('type'),
                                 'description': node.get('description')})
        elif kind == 'node':
            plan['nodes'].append({'ref': node['key'], 'label': label.strip(), 'node_type': node.get('type') or 'other',
                                  'description': node.get('description'), 'icon': node.get('icon'),
                                  'color': node.get('color')})
        elif kind == 'asset':
            plan['assets'].append({'ref': node['key'], 'label': label.strip(), 'type_name': node.get('type'),
                                   'description': node.get('description')})
        else:
            plan['skipped'].append(node['key'])
    for edge in document.get('edges') or []:
        if not isinstance(edge, dict) or edge.get('editable') is False:
            plan['skipped'].append(edge.get('key') if isinstance(edge, dict) else str(edge)[:80])
            continue
        relation = edge.get('relation')
        plan['edges'].append({
            'source_ref': edge.get('source'), 'target_ref': edge.get('target'),
            'relation': relation.get('name') if isinstance(relation, dict) else relation,
            'stix_relationship': None,
            'directed': edge.get('directed'),
            'description': edge.get('description'),
            'label': edge.get('label'),
            'confidence': edge.get('confidence'),
            'first_seen': edge.get('first_seen'),
            'last_seen': edge.get('last_seen'),
            'ref': edge.get('key'),
        })
    return plan
