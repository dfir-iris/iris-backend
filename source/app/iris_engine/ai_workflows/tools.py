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

"""Workflow tool execution on top of the MCP tool registry.

Every call — read, allowlisted write, write an analyst accepted —
goes through `dispatch_tool_call` acting as a real user, so the
permission, case ACL and war room ACL checks are those of MCP, and is
recorded as an `AiWorkflowToolCall`. The MCP modules are blueprints:
they are imported inside the functions.
"""

import json
import logging
import time

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_recover_session
from app.iris_engine.ai_workflows.identity import ai_workflows_identity
from app.iris_engine.ai_workflows.trace import ai_workflows_trace_prefix
from app.iris_engine.utils.tracker import track_activity
from app.models.ai_workflows import AiWorkflowToolCall
from app.models.ai_workflows import EXEC_ACCEPTED_BY_USER

logger = logging.getLogger(__name__)

CLASSIFICATION_READ = 'read'
CLASSIFICATION_WRITE = 'write'

# Arguments carrying the scope of a call, pinned to the run's entity
SCOPE_ARGUMENTS = ('case_identifier', 'war_room_id')

_MAX_STORED_RESULT = 200_000


def ai_workflows_tools_registry():
    """`TOOL_REGISTRY` (importing the MCP package registers the tools)."""
    from app.blueprints.rest.v2.mcp.dispatch import TOOL_REGISTRY
    return TOOL_REGISTRY


def ai_workflows_tools_classification(tool_name):
    """'read', 'write', or None for an unknown / unclassified tool,
    which a workflow never runs."""
    from app.blueprints.rest.v2.mcp.classification import is_read_only
    from app.blueprints.rest.v2.mcp.classification import is_write
    if tool_name not in ai_workflows_tools_registry():
        return None
    if is_read_only(tool_name):
        return CLASSIFICATION_READ
    if is_write(tool_name):
        return CLASSIFICATION_WRITE
    return None


def ai_workflows_tools_input_schema(tool_name) -> dict:
    """The schema MCP validates against, scope arguments included."""
    from app.blueprints.rest.v2.mcp.dispatch import _augmented_input_schema
    spec = ai_workflows_tools_registry().get(tool_name)
    if spec is None:
        return {'type': 'object', 'properties': {}}
    return _augmented_input_schema(spec)


def ai_workflows_tools_catalogue() -> list:
    """Every classified, non-admin tool, for the editor. `enabled` tells
    whether the MCP settings currently expose it (a disabled tool fails
    at run time)."""
    from app.blueprints.rest.v2.mcp.registry import active_tool_names
    from app.business.server_settings import get_srv_settings
    try:
        settings = get_srv_settings()
        active = active_tool_names(settings.mcp_tool_allowlist or '', settings.mcp_tool_denylist or '')
    except Exception:
        logger.exception('Could not read the MCP tool settings')
        active = set()
    catalogue = []
    for name, spec in sorted(ai_workflows_tools_registry().items()):
        classification = ai_workflows_tools_classification(name)
        if classification is None or spec.admin_only:
            continue
        catalogue.append({
            'name': name,
            'description': spec.description,
            'classification': classification,
            'input_schema': ai_workflows_tools_input_schema(name),
            'enabled': name in active,
        })
    return catalogue


def ai_workflows_tools_json_safe(value):
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def _stored_result(result):
    safe = ai_workflows_tools_json_safe(result)
    if len(json.dumps(safe)) > _MAX_STORED_RESULT:
        return {'truncated': True, 'preview': json.dumps(safe)[:_MAX_STORED_RESULT]}
    return safe


def ai_workflows_tools_record(tool_name, arguments, *, classification, execution_mode, acting_user_id,
                              run=None, step=None, suggestion=None, result=None, error=None,
                              duration_ms=None, mask=None) -> AiWorkflowToolCall:
    """Audit row for a call that was or was not executed (a write turned
    into a suggestion is recorded as `suggested`)."""
    mask = mask or (lambda value: value)
    row = AiWorkflowToolCall(
        run_id=run.id if run is not None else None,
        step_id=step.id if step is not None else None,
        suggestion_id=suggestion.id if suggestion is not None else None,
        tool_name=str(tool_name)[:128],
        arguments=mask(ai_workflows_tools_json_safe(arguments)),
        result=mask(_stored_result(result)) if result is not None else None,
        error=mask(error) if error else None,
        classification=classification or CLASSIFICATION_WRITE,
        execution_mode=execution_mode,
        acting_user_id=acting_user_id,
        duration_ms=duration_ms,
    )
    return ai_workflows_db_add(row)


def ai_workflows_tools_execute(user_id, tool_name, arguments, *, run=None, step=None, suggestion=None,
                               execution_mode, mask=None) -> dict:
    """Run one tool as `user_id`. Returns `{ok, result, error,
    tool_call_id}`; never raises for a tool failure.

    `mask` (the run's keystore resolver `mask`) scrubs secrets out of
    what is persisted. An accepted suggestion runs as the analyst and
    outside the run's chain, so its own hooks may trigger workflows."""
    from app.blueprints.rest.v2.mcp.dispatch import MCPError
    from app.blueprints.rest.v2.mcp.dispatch import dispatch_tool_call

    arguments = arguments if isinstance(arguments, dict) else {}
    mask = mask or (lambda value: value)
    classification = ai_workflows_tools_classification(tool_name)
    started = time.monotonic()
    result = None
    error = None
    if classification is None:
        error = f'Unknown or unclassified tool: {tool_name}'
    else:
        chain_run = None if execution_mode == EXEC_ACCEPTED_BY_USER else run
        try:
            with ai_workflows_identity(user_id, chain_run):
                result = dispatch_tool_call(tool_name, dict(arguments))
        except MCPError as e:
            error = mask(e.message)
        except Exception as e:
            error = mask(str(e) or e.__class__.__name__)
            # No traceback: its frames and message may hold keystore values
            logger.error(f'AI workflow tool {tool_name} failed: {e.__class__.__name__}: {error}')
        if error is not None:
            # A tool that failed mid-commit leaves the session unusable
            ai_workflows_db_recover_session()
    duration_ms = int((time.monotonic() - started) * 1000)

    row = ai_workflows_tools_record(
        tool_name, arguments,
        classification=classification, execution_mode=execution_mode, acting_user_id=user_id,
        run=run, step=step, suggestion=suggestion, result=result, error=error,
        duration_ms=duration_ms, mask=mask,
    )

    if error is None and classification == CLASSIFICATION_WRITE:
        prefix = ai_workflows_trace_prefix(run, step)
        if suggestion is not None:
            prefix = f'{prefix} suggestion #{suggestion.id}'
        try:
            case_id = arguments.get('case_identifier')
            war_room_id = arguments.get('war_room_id')
            track_activity(f'{prefix} {tool_name} (tool call #{row.id})',
                           caseid=case_id if isinstance(case_id, int) else None,
                           war_room_id=war_room_id if isinstance(war_room_id, int) else None,
                           ctx_less=not isinstance(case_id, int) and not isinstance(war_room_id, int),
                           user_id_override=user_id)
        except Exception:
            logger.exception('Could not track an AI workflow tool call')

    return {
        'ok': error is None,
        'result': result,
        'error': error,
        'tool_call_id': row.id,
    }
