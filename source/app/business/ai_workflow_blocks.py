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


"""Saved blocks of AI workflows: fragments of graph (nodes and the edges
between them) saved once — "look a hash up on VirusTotal" — and inserted
into any workflow from the editor palette.

A block is a template, never run as such: inserting it copies its nodes
into a workflow, where they are validated against that workflow's
trigger and write allowlist. Visibility: a user sees their own blocks
and the shared ones (an administrator sees all of them); only the owner
or an administrator changes or deletes one. A block cannot hold a
literal credential: HTTP requests must reference the keystore with
`key("NAME")`, so sharing a block never shares a secret.
"""

from app import app
from app.datamgmt.ai_workflows.ai_workflows_blocks_db import ai_workflows_blocks_db_get
from app.datamgmt.ai_workflows.ai_workflows_blocks_db import ai_workflows_blocks_db_list
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_delete
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_summary
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_import_warnings
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_fragment
from app.iris_engine.ai_workflows.portable import PortableError
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_export_block
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_literal_secrets
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_block
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_tools
from app.iris_engine.utils.tracker import track_activity
from app.models.ai_workflows import AiWorkflowBlock
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_MAX_NAME = 255
_MAX_DESCRIPTION = 10_000
_MAX_CATEGORY = 64


def _iso(value):
    return value.isoformat() if value is not None else None


def _error(node_id, field, message):
    return {'node_id': node_id, 'field': field, 'message': message}


def _raise_invalid(errors):
    raise BusinessProcessingError('Invalid block', data={'errors': errors})


def _serialize(block, users=None, user_id=None, is_admin=False) -> dict:
    definition = block.definition or {}
    nodes = definition.get('nodes') or []
    users = users if users is not None else ai_workflows_db_user_summary([block.owner_id])
    return {
        'id': block.id,
        'uuid': str(block.uuid) if block.uuid else None,
        'name': block.name,
        'description': block.description,
        'category': block.category,
        'is_shared': bool(block.is_shared),
        'owner_id': block.owner_id,
        'owner': users.get(block.owner_id),
        'definition': {'nodes': nodes, 'edges': definition.get('edges') or []},
        'requirements': {'keystore': ai_workflows_portable_key_references(nodes),
                         'tools': ai_workflows_portable_tools(nodes)},
        'can_edit': bool(is_admin or (user_id is not None and block.owner_id == user_id)),
        'created_at': _iso(block.created_at),
        'updated_at': _iso(block.updated_at),
    }


def _visible(block, user_id, is_admin) -> bool:
    return bool(is_admin or block.owner_id == user_id or block.is_shared)


def _get_visible(block_id, user_id, is_admin) -> AiWorkflowBlock:
    block = ai_workflows_blocks_db_get(block_id)
    if block is None or not _visible(block, user_id, is_admin):
        raise ObjectNotFoundError()
    return block


def _get_editable(block_id, user_id, is_admin) -> AiWorkflowBlock:
    block = _get_visible(block_id, user_id, is_admin)
    if not is_admin and block.owner_id != user_id:
        # Visible because shared, but not theirs
        raise AiWorkflowsForbiddenError('Only the owner of a block or an administrator can change it')
    return block


def _text(body, field, limit, errors, required=False, default=None):
    value = body.get(field, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            errors.append(_error(None, field, f'{field} is required'))
        return None
    if not isinstance(value, str):
        errors.append(_error(None, field, f'{field} must be a string'))
        return None
    value = value.strip()
    if len(value) > limit:
        errors.append(_error(None, field, f'{field} is at most {limit} characters'))
    return value


def _validate(body, existing=None) -> dict:
    if not isinstance(body, dict):
        _raise_invalid([_error(None, None, 'The body must be a JSON object')])
    current = {
        'name': existing.name,
        'description': existing.description,
        'category': existing.category,
        'is_shared': bool(existing.is_shared),
        'definition': existing.definition,
    } if existing is not None else {}
    merged = {**current, **body}
    errors = []
    name = _text(merged, 'name', _MAX_NAME, errors, required=True)
    description = _text(merged, 'description', _MAX_DESCRIPTION, errors)
    category = _text(merged, 'category', _MAX_CATEGORY, errors)
    is_shared = merged.get('is_shared', False)
    if not isinstance(is_shared, bool):
        errors.append(_error(None, 'is_shared', 'is_shared must be a boolean'))
    definition = merged.get('definition')
    errors.extend(ai_workflows_graph_validate_fragment(definition))
    if isinstance(definition, dict) and isinstance(definition.get('nodes'), list):
        for secret in ai_workflows_portable_literal_secrets(definition['nodes']):
            errors.append(_error(secret['node_id'], secret['field'],
                                 'A block cannot hold a literal credential: store it in the keystore and '
                                 'reference it with key("NAME")'))
    if errors:
        _raise_invalid(errors)
    return {
        'name': name,
        'description': description,
        'category': category,
        'is_shared': is_shared,
        'definition': {'nodes': definition['nodes'], 'edges': definition.get('edges') or []},
    }


def ai_workflow_blocks_list(user_id, is_admin) -> list:
    blocks = ai_workflows_blocks_db_list(user_id=None if is_admin else user_id)
    users = ai_workflows_db_user_summary([b.owner_id for b in blocks])
    return [_serialize(b, users, user_id, is_admin) for b in blocks]


def ai_workflow_blocks_get(block_id, user_id, is_admin) -> dict:
    return _serialize(_get_visible(block_id, user_id, is_admin), user_id=user_id, is_admin=is_admin)


def ai_workflow_blocks_create(body, user_id, is_admin) -> dict:
    attributes = _validate(body)
    block = AiWorkflowBlock(**attributes, owner_id=user_id, created_by_id=user_id)
    ai_workflows_db_add(block)
    ai_workflows_db_commit()
    track_activity(f'AI workflow block #{block.id} "{block.name}" created', ctx_less=True)
    return _serialize(block, user_id=user_id, is_admin=is_admin)


def ai_workflow_blocks_update(block_id, body, user_id, is_admin) -> dict:
    block = _get_editable(block_id, user_id, is_admin)
    for key, value in _validate(body, existing=block).items():
        setattr(block, key, value)
    ai_workflows_db_commit()
    track_activity(f'AI workflow block #{block.id} "{block.name}" updated', ctx_less=True)
    return _serialize(block, user_id=user_id, is_admin=is_admin)


def ai_workflow_blocks_delete(block_id, user_id, is_admin) -> None:
    block = _get_editable(block_id, user_id, is_admin)
    name = block.name
    ai_workflows_db_delete(block)
    track_activity(f'AI workflow block #{block_id} "{name}" deleted', ctx_less=True)


def ai_workflow_blocks_export(block_id, user_id, is_admin) -> dict:
    block = _get_visible(block_id, user_id, is_admin)
    definition = block.definition or {}
    return ai_workflows_portable_export_block({
        'name': block.name,
        'description': block.description,
        'category': block.category,
        'nodes': definition.get('nodes') or [],
        'edges': definition.get('edges') or [],
    }, exported_at=_iso(ai_workflows_db_utcnow()), instance_version=app.config.get('IRIS_VERSION'))


def ai_workflow_blocks_import(document, user_id, is_admin) -> dict:
    """Create a personal (not shared) block from a portable document or a
    bare `{name, nodes, edges}`."""
    try:
        data = ai_workflows_portable_read_block(document)
    except PortableError as e:
        _raise_invalid([_error(None, None, str(e))])
    body = {
        'name': data.get('name'),
        'description': data.get('description'),
        'category': data.get('category'),
        'is_shared': False,
        'definition': {'nodes': data.get('nodes'), 'edges': data.get('edges') or []},
    }
    block = ai_workflow_blocks_create(body, user_id, is_admin)
    nodes = body['definition']['nodes']
    return {'block': block, 'warnings': ai_workflows_import_warnings(user_id, nodes)}
