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

"""Catalogue of the investigation graph relation types (§4.9).

Listing is open to every user; the blueprint restricts the changes to
server administrators. System types (seeded) keep their name and cannot
be deleted; a type used by a stored edge cannot be deleted either.
"""

import re

from app.business.case_graph_assembly import case_graph_relation_type_serialize
from app.datamgmt.case.case_graph_db import case_graph_db_add
from app.datamgmt.case.case_graph_db import case_graph_db_commit
from app.datamgmt.case.case_graph_db import case_graph_db_delete
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_by_name
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_get
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_max_sort
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_usage
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_usages
from app.datamgmt.case.case_graph_db import case_graph_db_relation_types
from app.iris_engine.utils.tracker import track_activity
from app.models.assets import ASSET_FLAG_COLORS
from app.models.case_graph import CASE_GRAPH_LINE_STYLES
from app.models.case_graph import CaseGraphRelationType
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_NAME = re.compile(r'^[a-z][a-z0-9_]{1,63}$')
_STIX_RELATIONSHIP = re.compile(r'^[a-z][a-z0-9-]{0,63}$')
_LABEL_MAX = 128
_DESCRIPTION_MAX = 4000
_DEFAULT_STIX = 'related-to'


def _serialize(relation_type, usages):
    data = case_graph_relation_type_serialize(relation_type)
    data['edge_count'] = usages.get(relation_type.id, 0)
    return data


def case_graph_relation_types_list() -> list:
    """Every relation type, ordered, with the number of stored edges
    using it."""
    usages = case_graph_db_relation_type_usages()
    return [_serialize(relation_type, usages) for relation_type in case_graph_db_relation_types()]


def _get(relation_type_id):
    relation_type = case_graph_db_relation_type_get(relation_type_id)
    if relation_type is None:
        raise ObjectNotFoundError()
    return relation_type


def _validate_name(value, exclude_id=None):
    if not isinstance(value, str) or not _NAME.match(value.strip()):
        raise BusinessProcessingError('name must be 2-64 lower case letters, digits or underscores, '
                                      'starting with a letter')
    value = value.strip()
    existing = case_graph_db_relation_type_by_name(value)
    if existing is not None and existing.id != exclude_id:
        raise BusinessProcessingError(f'A relation type named {value!r} already exists')
    return value


def _validate_label(value):
    if not isinstance(value, str) or not value.strip():
        raise BusinessProcessingError('label is required')
    if len(value.strip()) > _LABEL_MAX:
        raise BusinessProcessingError(f'label must be at most {_LABEL_MAX} characters')
    return value.strip()


def _validate_description(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('description must be a string')
    if len(value) > _DESCRIPTION_MAX:
        raise BusinessProcessingError(f'description must be at most {_DESCRIPTION_MAX} characters')
    return value.strip()


def _validate_body(body, relation_type, creating):
    if not isinstance(body, dict):
        raise BusinessProcessingError('A JSON object is required')
    if 'name' in body or creating:
        if relation_type.is_system and body.get('name') != relation_type.name:
            raise BusinessProcessingError('The name of a system relation type cannot change')
        relation_type.name = _validate_name(body.get('name'), exclude_id=relation_type.id)
    if 'label' in body or creating:
        relation_type.label = _validate_label(body.get('label'))
    if 'description' in body:
        relation_type.description = _validate_description(body['description'])
    if 'directed' in body or creating:
        directed = body.get('directed', True)
        if not isinstance(directed, bool):
            raise BusinessProcessingError('directed must be a boolean')
        relation_type.directed = directed
    if 'color' in body or creating:
        color = body.get('color') or 'slate'
        if color not in ASSET_FLAG_COLORS:
            raise BusinessProcessingError(f'color must be one of {", ".join(ASSET_FLAG_COLORS)}')
        relation_type.color = color
    if 'line_style' in body or creating:
        line_style = body.get('line_style') or 'solid'
        if line_style not in CASE_GRAPH_LINE_STYLES:
            raise BusinessProcessingError(f'line_style must be one of {", ".join(CASE_GRAPH_LINE_STYLES)}')
        relation_type.line_style = line_style
    if 'stix_relationship' in body or creating:
        stix = body.get('stix_relationship') or _DEFAULT_STIX
        if not isinstance(stix, str) or not _STIX_RELATIONSHIP.match(stix):
            raise BusinessProcessingError('stix_relationship must be a STIX relationship type (e.g. related-to)')
        relation_type.stix_relationship = stix


def _commit(message):
    if not case_graph_db_commit():
        raise BusinessProcessingError(message)


def case_graph_relation_types_create(body) -> dict:
    relation_type = CaseGraphRelationType()
    relation_type.is_system = False
    _validate_body(body, relation_type, creating=True)
    relation_type.sort_order = (case_graph_db_relation_type_max_sort() or 0) + 1
    case_graph_db_add(relation_type)
    _commit('Unable to create the relation type')
    track_activity(f'graph relation type #{relation_type.id} "{relation_type.name}" created', ctx_less=True)
    return _serialize(relation_type, {})


def case_graph_relation_types_update(relation_type_id, body) -> dict:
    relation_type = _get(relation_type_id)
    _validate_body(body, relation_type, creating=False)
    _commit('Unable to update the relation type')
    track_activity(f'graph relation type #{relation_type.id} "{relation_type.name}" updated', ctx_less=True)
    return _serialize(relation_type, {relation_type.id: case_graph_db_relation_type_usage(relation_type.id)})


def case_graph_relation_types_delete(relation_type_id) -> None:
    relation_type = _get(relation_type_id)
    if relation_type.is_system:
        raise BusinessProcessingError('A system relation type cannot be deleted')
    count = case_graph_db_relation_type_usage(relation_type.id)
    if count:
        raise BusinessProcessingError(f'The relation type is used by {count} edge{"" if count == 1 else "s"}')
    name = relation_type.name
    case_graph_db_delete(relation_type)
    _commit('The relation type is in use')
    track_activity(f'graph relation type #{relation_type_id} "{name}" deleted', ctx_less=True)


def case_graph_relation_types_reorder(ids) -> list:
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
        raise BusinessProcessingError('ids must be a list of integers')
    relation_types = case_graph_db_relation_types()
    if len(ids) != len(set(ids)) or set(ids) != {relation_type.id for relation_type in relation_types}:
        raise BusinessProcessingError('ids must list every relation type exactly once')
    by_id = {relation_type.id: relation_type for relation_type in relation_types}
    for position, relation_type_id in enumerate(ids):
        by_id[relation_type_id].sort_order = position
    _commit('Unable to reorder the relation types')
    track_activity('graph relation types reordered', ctx_less=True)
    return case_graph_relation_types_list()
