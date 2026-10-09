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

"""v2 endpoints for AI workflows: definitions, catalogue, runs.

Reads need `ai_workflows_read` or `ai_workflows_write`, mutations (and
`/validate`) need `ai_workflows_write`. Workflows are visible to their
owner and to administrators; runs follow the visibility rule of
`app.business.ai_workflows.ai_workflows_user_can_see_run`. Anything the
caller cannot see answers 404. The API-key scope of the request bounds
the runs it starts. The public callback / inbound-trigger endpoints live
in `ai_workflows_public`.
"""

import json

from flask import Blueprint
from flask import g
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.ai_workflow_blocks import ai_workflow_blocks_create
from app.business.ai_workflow_blocks import ai_workflow_blocks_delete
from app.business.ai_workflow_blocks import ai_workflow_blocks_export
from app.business.ai_workflow_blocks import ai_workflow_blocks_get
from app.business.ai_workflow_blocks import ai_workflow_blocks_import
from app.business.ai_workflow_blocks import ai_workflow_blocks_list
from app.business.ai_workflow_blocks import ai_workflow_blocks_update
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_authoring_guide
from app.business.ai_workflows import ai_workflows_cancel
from app.business.ai_workflows import ai_workflows_catalogue
from app.business.ai_workflows import ai_workflows_create
from app.business.ai_workflows import ai_workflows_delete
from app.business.ai_workflows import ai_workflows_export
from app.business.ai_workflows import ai_workflows_export_run
from app.business.ai_workflows import ai_workflows_get
from app.business.ai_workflows import ai_workflows_get_run
from app.business.ai_workflows import ai_workflows_get_version
from app.business.ai_workflows import ai_workflows_import
from app.business.ai_workflows import ai_workflows_library
from app.business.ai_workflows import ai_workflows_list
from app.business.ai_workflows import ai_workflows_list_inbound_events
from app.business.ai_workflows import ai_workflows_list_runs
from app.business.ai_workflows import ai_workflows_list_versions
from app.business.ai_workflows import ai_workflows_node_event
from app.business.ai_workflows import ai_workflows_node_events
from app.business.ai_workflows import ai_workflows_node_stats
from app.business.ai_workflows import ai_workflows_replay_event
from app.business.ai_workflows import ai_workflows_test_node
from app.business.ai_workflows import ai_workflows_rerun
from app.business.ai_workflows import ai_workflows_rotate_inbound_token
from app.business.ai_workflows import ai_workflows_rotate_signing_secret
from app.business.ai_workflows import ai_workflows_run_manual
from app.business.ai_workflows import ai_workflows_update
from app.business.ai_workflows import ai_workflows_validate_definition
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_READ = (Permissions.ai_workflows_read, Permissions.ai_workflows_write)
_WRITE = (Permissions.ai_workflows_write,)
# Workflow definitions (graph included) are small; refuse more early
_MAX_DEFINITION_BYTES = 2 * 1024 * 1024


def _definition_body():
    """`(body, None)`, or `(None, 413 response)` when the definition body
    is over the cap. At most cap + 1 bytes are read, so a body without
    Content-Length (chunked) is bounded too."""
    if (request.content_length or 0) > _MAX_DEFINITION_BYTES:
        return None, response_api_error('Payload too large', status=413)
    raw = request.stream.read(_MAX_DEFINITION_BYTES + 1) or b''
    if len(raw) > _MAX_DEFINITION_BYTES:
        return None, response_api_error('Payload too large', status=413)
    try:
        body = json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        body = None
    return (body if isinstance(body, dict) else {}), None


def _scope_mask():
    """Scope mask of the API key the request authenticated with (None:
    session, or a key without a scope)."""
    mask = getattr(getattr(g, 'api_key_row', None), 'scope_mask', None)
    return int(mask) if mask is not None else None


def _is_admin() -> bool:
    return ac_current_user_has_permission(Permissions.server_administrator)


def _int_arg(name, default=None):
    """`request.args` integer; an unparsable value raises a 400."""
    value = request.args.get(name)
    if value is None or value == '':
        return default
    try:
        return int(value)
    except ValueError:
        raise BusinessProcessingError(f'Invalid {name}', data={name: ['Must be an integer']})


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiWorkflowsForbiddenError as e:
        return response_api_error(e.get_message(), data=e.get_data(), status=403)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


ai_workflows_blueprint = Blueprint('ai_workflows_rest_v2', __name__, url_prefix='/ai-workflows')


# ---- Static routes (before /<int:identifier>) ------------------------------

@ai_workflows_blueprint.get('/catalogue')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Node types, tools, hooks, keystore names and LLM status for the workflow editor')
def get_ai_workflows_catalogue_route():
    return _handle(lambda: ai_workflows_catalogue(iris_current_user.id))


@ai_workflows_blueprint.post('/validate')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'], summary='Validate a workflow graph and trigger without saving it')
def validate_ai_workflow_route():
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_validate_definition(body))


@ai_workflows_blueprint.get('/runs')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List the AI workflow runs the user can see',
         query_params=[('workflow_id', 'integer'), ('status', 'string'), ('entity_type', 'string'),
                       ('entity_id', 'integer'), ('page', 'integer'), ('per_page', 'integer')])
def list_ai_workflow_runs_route():
    def _operation():
        return ai_workflows_list_runs(
            iris_current_user.id, _is_admin(),
            workflow_id=_int_arg('workflow_id'),
            status=request.args.get('status') or None,
            entity_type=request.args.get('entity_type') or None,
            entity_id=_int_arg('entity_id'),
            page=_int_arg('page', 1),
            per_page=_int_arg('per_page', 25),
            scope_mask=_scope_mask(),
        )
    return _handle(_operation)


@ai_workflows_blueprint.get('/runs/<run_uuid>')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Get an AI workflow run with its steps, tool calls, LLM calls, waits and suggestions')
def get_ai_workflow_run_route(run_uuid):
    return _handle(lambda: ai_workflows_get_run(run_uuid, iris_current_user.id, _is_admin(), _scope_mask()))


@ai_workflows_blueprint.post('/runs/<run_uuid>/cancel')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'], summary='Cancel an active AI workflow run')
def cancel_ai_workflow_run_route(run_uuid):
    return _handle(lambda: ai_workflows_cancel(run_uuid, iris_current_user.id, _is_admin(), _scope_mask()))


@ai_workflows_blueprint.post('/runs/<run_uuid>/rerun')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'],
         summary='Start a new run with the trigger of an existing run (acts as the workflow owner)')
def rerun_ai_workflow_run_route(run_uuid):
    return _handle(lambda: ai_workflows_rerun(run_uuid, iris_current_user.id, _is_admin(), _scope_mask()),
                   created=True)


@ai_workflows_blueprint.get('/runs/<run_uuid>/export')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Export the full JSON trace of a run (administrator or workflow owner)')
def export_ai_workflow_run_route(run_uuid):
    return _handle(lambda: ai_workflows_export_run(run_uuid, iris_current_user.id, _is_admin(), _scope_mask()))


@ai_workflows_blueprint.get('/inbound-events')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List inbound callback / webhook attempts (administrator or workflow owner)',
         query_params=[('workflow_id', 'integer'), ('limit', 'integer')])
def list_ai_workflow_inbound_events_route():
    def _operation():
        return ai_workflows_list_inbound_events(
            iris_current_user.id, _is_admin(),
            workflow_id=_int_arg('workflow_id'),
            limit=_int_arg('limit', 100),
        )
    return _handle(_operation)


@ai_workflows_blueprint.get('/authoring-guide')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'],
         summary='Markdown guide to write workflows and blocks as JSON (for people and LLMs), with the live catalogue')
def get_ai_workflows_authoring_guide_route():
    return _handle(lambda: ai_workflows_authoring_guide(iris_current_user.id))


@ai_workflows_blueprint.get('/library')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'],
         summary='The workflow library: shipped workflows to add as they are or after an edit, with what the caller '
                 'lacks to run each')
def get_ai_workflows_library_route():
    return _handle(lambda: ai_workflows_library(iris_current_user.id))


@ai_workflows_blueprint.post('/import')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'],
         summary='Import a workflow from its JSON document (created inactive, owned by the importer)')
def import_ai_workflow_route():
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_import(body, iris_current_user.id, _is_admin()), created=True)


# ---- Saved blocks ------------------------------------------------------------

@ai_workflows_blueprint.get('/blocks')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List the saved blocks the user can insert (own and shared)')
def list_ai_workflow_blocks_route():
    return _handle(lambda: ai_workflow_blocks_list(iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.post('/blocks')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'], summary='Save a block of nodes for reuse')
def create_ai_workflow_block_route():
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflow_blocks_create(body, iris_current_user.id, _is_admin()), created=True)


@ai_workflows_blueprint.post('/blocks/import')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'], summary='Import a saved block from its JSON document')
def import_ai_workflow_block_route():
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflow_blocks_import(body, iris_current_user.id, _is_admin()), created=True)


@ai_workflows_blueprint.get('/blocks/<int:block_id>')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Get a saved block')
def get_ai_workflow_block_route(block_id):
    return _handle(lambda: ai_workflow_blocks_get(block_id, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.put('/blocks/<int:block_id>')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'], summary='Update a saved block (owner or administrator)')
def put_ai_workflow_block_route(block_id):
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflow_blocks_update(block_id, body, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.delete('/blocks/<int:block_id>')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='deleted', tags=['AiWorkflows'], summary='Delete a saved block (owner or administrator)')
def delete_ai_workflow_block_route(block_id):
    try:
        ai_workflow_blocks_delete(block_id, iris_current_user.id, _is_admin())
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiWorkflowsForbiddenError as e:
        return response_api_error(e.get_message(), data=e.get_data(), status=403)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()


@ai_workflows_blueprint.get('/blocks/<int:block_id>/export')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Export a saved block as a JSON document (detected secrets become keystore references)')
def export_ai_workflow_block_route(block_id):
    return _handle(lambda: ai_workflow_blocks_export(block_id, iris_current_user.id, _is_admin()))


# ---- Workflows -------------------------------------------------------------

@ai_workflows_blueprint.get('')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List AI workflows (own workflows, all for administrators) with 24h run counts')
def list_ai_workflows_route():
    return _handle(lambda: ai_workflows_list(iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.post('')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'], summary='Create an AI workflow')
def create_ai_workflow_route():
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_create(body, iris_current_user.id, _is_admin()), created=True)


@ai_workflows_blueprint.get('/<int:identifier>')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Get an AI workflow')
def get_ai_workflow_route(identifier):
    return _handle(lambda: ai_workflows_get(identifier, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.put('/<int:identifier>')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'], summary='Update an AI workflow (a definition change creates a new version)')
def put_ai_workflow_route(identifier):
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_update(identifier, body, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.delete('/<int:identifier>')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='deleted', tags=['AiWorkflows'], summary='Delete an AI workflow (active runs are cancelled, history kept)')
def delete_ai_workflow_route(identifier):
    try:
        ai_workflows_delete(identifier, iris_current_user.id, _is_admin())
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiWorkflowsForbiddenError as e:
        return response_api_error(e.get_message(), data=e.get_data(), status=403)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()


@ai_workflows_blueprint.get('/<int:identifier>/export')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'],
         summary='Export a workflow as a JSON document: detected secrets become keystore references')
def export_ai_workflow_route(identifier):
    return _handle(lambda: ai_workflows_export(identifier, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.get('/<int:identifier>/node-stats')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Number of events each node of a workflow processed, by status')
def get_ai_workflow_node_stats_route(identifier):
    return _handle(lambda: ai_workflows_node_stats(identifier, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.get('/<int:identifier>/nodes/<node_id>/events')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List the events a node of a workflow processed, newest first',
         query_params=[('status', 'string'), ('page', 'integer'), ('per_page', 'integer')])
def list_ai_workflow_node_events_route(identifier, node_id):
    def _operation():
        return ai_workflows_node_events(
            identifier, node_id, iris_current_user.id, _is_admin(),
            status=request.args.get('status') or None,
            page=_int_arg('page', 1),
            per_page=_int_arg('per_page', 25),
            scope_mask=_scope_mask(),
        )
    return _handle(_operation)


@ai_workflows_blueprint.get('/<int:identifier>/nodes/<node_id>/events/<int:step_id>')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Get an event a node processed, with the run context the node saw')
def get_ai_workflow_node_event_route(identifier, node_id, step_id):
    return _handle(lambda: ai_workflows_node_event(identifier, node_id, step_id, iris_current_user.id, _is_admin(),
                                                   _scope_mask()))


@ai_workflows_blueprint.post('/<int:identifier>/nodes/<node_id>/events/<int:step_id>/replay')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'],
         summary='Replay an event through the current workflow, from its node (acts as the workflow owner)')
def replay_ai_workflow_node_event_route(identifier, node_id, step_id):
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_replay_event(identifier, node_id, step_id, body, iris_current_user.id,
                                                     _is_admin(), _scope_mask()), created=True)


@ai_workflows_blueprint.post('/<int:identifier>/test-node')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'],
         summary='Execute one node definition on its own, on an earlier event or a supplied context (acts as the '
                 'workflow owner)')
def test_ai_workflow_node_route(identifier):
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_test_node(identifier, body, iris_current_user.id, _is_admin(),
                                                  _scope_mask()), created=True)


@ai_workflows_blueprint.get('/<int:identifier>/versions')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='List the versions of an AI workflow')
def list_ai_workflow_versions_route(identifier):
    return _handle(lambda: ai_workflows_list_versions(identifier, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.get('/<int:identifier>/versions/<int:version>')
@ac_api_requires(*_READ)
@api_doc(tags=['AiWorkflows'], summary='Get one version snapshot of an AI workflow')
def get_ai_workflow_version_route(identifier, version):
    return _handle(lambda: ai_workflows_get_version(identifier, version, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.post('/<int:identifier>/run')
@ac_api_requires(*_WRITE)
@api_doc(response_shape='created', tags=['AiWorkflows'],
         summary='Run an AI workflow manually; the run acts as the workflow owner')
def run_ai_workflow_route(identifier):
    body, refused = _definition_body()
    if refused is not None:
        return refused
    return _handle(lambda: ai_workflows_run_manual(identifier, body, iris_current_user.id, _is_admin(),
                                                   _scope_mask()),
                   created=True)


@ai_workflows_blueprint.post('/<int:identifier>/inbound-token')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'],
         summary='Rotate the inbound webhook token and signing secret of a workflow (both shown once)')
def rotate_ai_workflow_inbound_token_route(identifier):
    return _handle(lambda: ai_workflows_rotate_inbound_token(identifier, iris_current_user.id, _is_admin()))


@ai_workflows_blueprint.post('/<int:identifier>/signing-secret')
@ac_api_requires(*_WRITE)
@api_doc(tags=['AiWorkflows'], summary='Create or rotate the webhook signing secret of a workflow (shown once)')
def rotate_ai_workflow_signing_secret_route(identifier):
    return _handle(lambda: ai_workflows_rotate_signing_secret(identifier, iris_current_user.id, _is_admin()))
