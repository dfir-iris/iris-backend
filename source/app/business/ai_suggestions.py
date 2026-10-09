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

"""AI suggestions: the analyst side of AI workflows.

A suggestion is visible to a user when `ai_workflows_suggestions_user_can_see`
says so (entity access, related refs access and audience; administrators
see all), bounded by the API-key scope of the request. A suggestion with
neither an audience nor an entity is visible to administrators only.
Anything not visible answers 404, so ids cannot be probed.

Accepting runs the proposed action as the accepting analyst, through the
MCP dispatcher and therefore with that analyst's own permissions, ACLs
and API-key scope. Only a write tool can be accepted, and every object
id its arguments name (case, alert, war room, cluster, customer) must be
the suggestion's own entity or one of its related refs. The row is
locked and flipped out of `open` before the tool runs, so two concurrent
accepts can never both execute; a failing tool puts it back to `open`.

What the analyst did (the action result, the answer) is shown to them
only; the rest of the audience sees who resolved it. Resolving needs
`AI_WORKFLOWS_ENABLED`. Locks: the run, then its wait.
"""

from app.business.ai_workflows import ai_workflows_require_enabled
from app.business.ai_workflows import ai_workflows_suggestion_public
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_run_by_uuid
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_suggestion
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_suggestions
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_open_suggestions_for_entities
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_open_suggestions_for_wait
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_rollback
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_summary
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_lock_run_then_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_scope_allows
from app.iris_engine.ai_workflows.suggestions import SEVERITIES
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_emit
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_serialize
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_user_can_see
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_WRITE
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_classification
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_execute
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.logger import logger
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_ALERT_CLUSTER
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import EXEC_ACCEPTED_BY_USER
from app.models.ai_workflows import SUGGESTION_ACCEPTED
from app.models.ai_workflows import SUGGESTION_DISMISSED
from app.models.ai_workflows import SUGGESTION_DRY_RUN
from app.models.ai_workflows import SUGGESTION_EXPIRED
from app.models.ai_workflows import SUGGESTION_INFO_REQUEST
from app.models.ai_workflows import SUGGESTION_OPEN
from app.models.ai_workflows import WAIT_PENDING
from app.models.authorization import ac_has_permission_server_administrator
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


SUGGESTION_STATUSES = (SUGGESTION_OPEN, SUGGESTION_ACCEPTED, SUGGESTION_DISMISSED, SUGGESTION_EXPIRED,
                       SUGGESTION_DRY_RUN)

# Ports of the waiting node the resolution leaves through
_PORT_ANSWERED = 'answered'
_PORT_TIMEOUT = 'timeout'

_MAX_NOTE = 4000
_MAX_TEXT_ANSWER = 20000
_MAX_COUNT_IDS = 500
_DEFAULT_LIST = 200
_MAX_LIST = 500
_NO_ENTITY = 'none'
_FIELD_TYPES = ('text', 'textarea', 'number', 'boolean', 'select')

# Argument names (at any depth of the tool arguments) that name an
# object an accepted action acts on, and the kind of object
_CUSTOMER = 'customer'
_PINNED_ARGUMENTS = {
    'case_identifier': ENTITY_CASE,
    'case_id': ENTITY_CASE,
    'cid': ENTITY_CASE,
    'target_case_id': ENTITY_CASE,
    'case_ids': ENTITY_CASE,
    'alert_identifier': ENTITY_ALERT,
    'alert_id': ENTITY_ALERT,
    'alert_identifiers': ENTITY_ALERT,
    'alert_ids': ENTITY_ALERT,
    'war_room_id': ENTITY_WAR_ROOM,
    'war_room_identifier': ENTITY_WAR_ROOM,
    'cluster_id': ENTITY_ALERT_CLUSTER,
    'alert_cluster_id': ENTITY_ALERT_CLUSTER,
    'cluster_identifier': ENTITY_ALERT_CLUSTER,
    'customer_id': _CUSTOMER,
    'customer_identifier': _CUSTOMER,
    'case_customer': _CUSTOMER,
    'case_customer_id': _CUSTOMER,
    'alert_customer_id': _CUSTOMER,
    'client_id': _CUSTOMER,
}
_MAX_ARGUMENT_DEPTH = 8


# ---- Helpers ---------------------------------------------------------------

def _note(note):
    if note is None:
        return None
    if not isinstance(note, str):
        raise BusinessProcessingError('Invalid note', data={'note': ['Must be a string']})
    note = note.strip()
    if len(note) > _MAX_NOTE:
        raise BusinessProcessingError('Invalid note', data={'note': [f'At most {_MAX_NOTE} characters']})
    return note or None


def _ref_list(related_refs) -> list:
    """(type, id) of the related refs that point at an entity."""
    refs = []
    for ref in related_refs or []:
        if not isinstance(ref, dict) or ref.get('type') not in ENTITY_TYPES:
            continue
        try:
            refs.append((ref.get('type'), int(ref.get('id'))))
        except (TypeError, ValueError):
            continue
    return refs


def _is_admin(user_id) -> bool:
    user = ai_workflows_db_get_user(user_id) if user_id else None
    return user is not None and ac_has_permission_server_administrator(ac_get_effective_permissions_of_user(user))


def _visible(suggestion, user_id, scope_mask=None) -> bool:
    if not ai_workflows_suggestions_user_can_see(user_id, suggestion):
        return False
    types = [t for t, _ in _ref_list(suggestion.related_refs)]
    if suggestion.entity_type:
        types.append(suggestion.entity_type)
    if not all(ai_workflows_entities_scope_allows(t, scope_mask) for t in types):
        return False
    if suggestion.audience_user_ids is None and not types:
        # Nothing restricts it: never "everyone"
        return _is_admin(user_id)
    return True


def _get_visible(suggestion_id, user_id, lock=False, scope_mask=None):
    suggestion = ai_workflows_db_get_suggestion(suggestion_id, lock=lock)
    if suggestion is None or not _visible(suggestion, user_id, scope_mask):
        if lock:
            ai_workflows_db_rollback()
        raise ObjectNotFoundError()
    return suggestion


def _require_open(suggestion):
    if suggestion.status != SUGGESTION_OPEN:
        ai_workflows_db_rollback()
        raise BusinessProcessingError(f'This suggestion is already {suggestion.status}',
                                      data={'status': suggestion.status})


def _allowed_ids(suggestion) -> dict:
    """{object kind: ids an accepted action may name}."""
    allowed = {ENTITY_CASE: set(), ENTITY_ALERT: set(), ENTITY_WAR_ROOM: set(), ENTITY_ALERT_CLUSTER: set(),
               _CUSTOMER: set()}
    if suggestion.entity_type in allowed and suggestion.entity_id is not None:
        allowed[suggestion.entity_type].add(int(suggestion.entity_id))
    for column, kind in (('case_id', ENTITY_CASE), ('alert_id', ENTITY_ALERT), ('war_room_id', ENTITY_WAR_ROOM),
                         ('customer_id', _CUSTOMER)):
        value = getattr(suggestion, column, None)
        if value is not None:
            allowed[kind].add(int(value))
    for ref_type, ref_id in _ref_list(suggestion.related_refs):
        allowed[ref_type].add(ref_id)
    return allowed


def _as_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    return None


def _pinned_values(arguments, path='', depth=0):
    """(path, kind, value) of every pinned argument, at any depth."""
    if depth > _MAX_ARGUMENT_DEPTH:
        yield path or 'arguments', None, None
        return
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            child = f'{path}.{key}' if path else str(key)
            kind = _PINNED_ARGUMENTS.get(str(key).lower())
            if kind is not None and value is not None:
                for item in value if isinstance(value, list) else [value]:
                    yield child, kind, item
            elif isinstance(value, (dict, list)):
                yield from _pinned_values(value, child, depth + 1)
    elif isinstance(arguments, list):
        for index, item in enumerate(arguments):
            yield from _pinned_values(item, f'{path}.{index}', depth + 1)


def ai_suggestions_check_action(suggestion, action) -> tuple:
    """(tool, arguments) an accept may execute; raises
    BusinessProcessingError when the tool is not a write tool or the
    arguments name an object outside the suggestion."""
    tool = action.get('tool')
    if not isinstance(tool, str) or ai_workflows_tools_classification(tool) != CLASSIFICATION_WRITE:
        raise BusinessProcessingError('The proposed action is not a write tool and cannot be accepted')
    arguments = action.get('arguments') or {}
    if not isinstance(arguments, dict):
        raise BusinessProcessingError('The proposed action has invalid arguments')
    allowed = _allowed_ids(suggestion)
    errors = {}
    for path, kind, value in _pinned_values(arguments):
        if kind is None:
            errors[path] = ['Arguments are nested too deeply']
            continue
        if _as_id(value) not in allowed[kind]:
            errors[path] = [f'Names a {kind.replace("_", " ")} this suggestion is not about']
    if errors:
        raise BusinessProcessingError('The proposed action targets objects outside this suggestion', data=errors)
    return tool, arguments


def _activity_prefix(suggestion):
    run = suggestion.run
    if run is None:
        return f'[ai-suggestion:{suggestion.id}]'
    return f'[ai-workflow:{run.workflow_id}@v{run.workflow_version} run:{run.uuid}] [ai-suggestion:{suggestion.id}]'


def _track(suggestion, message, user_id):
    ctx_less = suggestion.case_id is None and suggestion.war_room_id is None
    track_activity(f'{_activity_prefix(suggestion)} {message}', caseid=suggestion.case_id,
                   war_room_id=suggestion.war_room_id, ctx_less=ctx_less, user_id_override=user_id)


def _fire_hook(hook_name, suggestion, data):
    try:
        call_modules_hook(hook_name, data=data, caseid=suggestion.case_id)
    except Exception:
        logger.exception(f'{hook_name} failed for AI suggestion #{suggestion.id}')


class _Redacted:
    """The suggestion without what its resolver did with it: socket
    pushes go to the whole audience."""
    result = None
    answer = None

    def __init__(self, suggestion):
        self._suggestion = suggestion

    def __getattr__(self, name):
        return getattr(self._suggestion, name)


def _publish(suggestion, hook_name, user_id):
    """Hook + live update, after the commit. Returns the suggestion as
    the resolving user sees it."""
    _fire_hook(hook_name, suggestion, ai_workflows_suggestions_serialize(suggestion))
    try:
        ai_workflows_suggestions_emit(_Redacted(suggestion), 'updated')
    except Exception:
        logger.exception(f'Socket update failed for AI suggestion #{suggestion.id}')
    return ai_workflows_suggestion_public(suggestion, user_id)


def _expire_siblings(wait_id, keep_id):
    """Other open suggestions on the same wait can no longer resolve it.
    Flushes nothing: the caller commits."""
    expired = []
    for sibling in ai_workflows_db_open_suggestions_for_wait(wait_id):
        if sibling.id == keep_id:
            continue
        sibling.status = SUGGESTION_EXPIRED
        sibling.resolved_at = ai_workflows_db_utcnow()
        expired.append(sibling)
    return expired


def _resume_wait(suggestion, payload, user_id, port):
    """Resolve the wait the suggestion is linked to, when still pending.
    Returns the siblings it expired (already committed)."""
    if not suggestion.wait_id:
        return []
    # The run, then the wait: the order of every other resolution path
    _run, wait = ai_workflows_engine_lock_run_then_wait(suggestion.wait_id)
    if wait is None or wait.status != WAIT_PENDING:
        ai_workflows_db_commit()
        return []
    expired = _expire_siblings(wait.id, suggestion.id)
    ai_workflows_engine_resume_wait(wait, payload, resolved_by_id=user_id, port=port)
    ai_workflows_db_commit()
    return expired


def _publish_expired(expired):
    for sibling in expired:
        try:
            ai_workflows_suggestions_emit(sibling, 'updated')
        except Exception:
            logger.exception(f'Socket update failed for AI suggestion #{sibling.id}')


# ---- Read ------------------------------------------------------------------

def ai_suggestions_list(user_id, entity_type=None, entity_id=None, status=None, run_uuid=None,
                        scope_mask=None, workflow_id=None, severity=None, mine=False, limit=None) -> list:
    """Newest first. `entity_type` `none`: the suggestions about no
    entity. `mine`: only those addressed to the user — always the case
    for a non-administrator, who also sees the ones addressed to no one
    on an entity they can access."""
    without_entity = entity_type == _NO_ENTITY
    if without_entity:
        entity_type = None
    if entity_type and entity_type not in ENTITY_TYPES:
        raise BusinessProcessingError('Invalid entity type', data={'entity_type': [f'Unknown {entity_type}']})
    if severity and severity not in SEVERITIES:
        raise BusinessProcessingError('Invalid severity filter', data={'severity': [f'Unknown severity {severity}']})
    if not status:
        statuses = [SUGGESTION_OPEN]
    elif status == 'all':
        statuses = None
    elif status in SUGGESTION_STATUSES:
        statuses = [status]
    else:
        raise BusinessProcessingError('Invalid status filter', data={'status': [f'Unknown status {status}']})
    run_id = None
    if run_uuid:
        run = ai_workflows_db_get_run_by_uuid(run_uuid)
        if run is None:
            return []
        run_id = run.id
    limit = max(1, min(int(limit or _DEFAULT_LIST), _MAX_LIST))
    is_admin = _is_admin(user_id)
    audience = {}
    if mine or not is_admin:
        audience = {'audience_user_id': user_id, 'with_unaddressed': not mine}
    rows = ai_workflows_db_list_suggestions(entity_type=entity_type or None, entity_id=entity_id,
                                            statuses=statuses, run_id=run_id, limit=limit, workflow_id=workflow_id,
                                            severity=severity or None, without_entity=without_entity, **audience)
    return [ai_workflows_suggestion_public(s, user_id) for s in rows if _visible(s, user_id, scope_mask)]


def ai_suggestions_counts(user_id, entity_type, entity_ids, scope_mask=None) -> dict:
    """{"<entity_id>": open suggestions the user can see}."""
    if entity_type not in ENTITY_TYPES:
        raise BusinessProcessingError('Invalid entity type', data={'entity_type': ['An entity type is required']})
    ids = []
    for value in entity_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            raise BusinessProcessingError('Invalid entity ids', data={'entity_ids': ['Comma-separated integers']})
    ids = sorted(set(ids))[:_MAX_COUNT_IDS]
    counts = {str(i): 0 for i in ids}
    for suggestion in ai_workflows_db_open_suggestions_for_entities(entity_type, ids):
        if _visible(suggestion, user_id, scope_mask):
            key = str(suggestion.entity_id)
            counts[key] = counts.get(key, 0) + 1
    return counts


def ai_suggestions_get(suggestion_id, user_id, scope_mask=None) -> dict:
    return ai_workflows_suggestion_public(_get_visible(suggestion_id, user_id, scope_mask=scope_mask), user_id)


# ---- Resolution ------------------------------------------------------------

def ai_suggestions_accept(suggestion_id, user_id, note=None, scope_mask=None) -> dict:
    ai_workflows_require_enabled()
    note = _note(note)
    suggestion = _get_visible(suggestion_id, user_id, lock=True, scope_mask=scope_mask)
    _require_open(suggestion)
    if suggestion.kind == SUGGESTION_INFO_REQUEST:
        ai_workflows_db_rollback()
        raise BusinessProcessingError('An information request is answered, not accepted')
    action = suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else None
    tool = None
    arguments = None
    if action and action.get('tool') is not None:
        try:
            tool, arguments = ai_suggestions_check_action(suggestion, action)
        except BusinessProcessingError:
            ai_workflows_db_rollback()
            raise

    # Claim the row before anything else commits: the tool execution
    # commits on its own, which would release the lock while still `open`
    suggestion.status = SUGGESTION_ACCEPTED
    suggestion.resolved_by_id = user_id
    suggestion.resolved_at = ai_workflows_db_utcnow()
    suggestion.resolution_note = note
    ai_workflows_db_commit()

    if tool is not None:
        try:
            # Runs as the accepting analyst; the identity keeps the scope
            # of the API key this request authenticated with
            result = ai_workflows_tools_execute(
                user_id, tool, dict(arguments),
                run=suggestion.run, suggestion=suggestion, execution_mode=EXEC_ACCEPTED_BY_USER,
            )
        except Exception as e:
            logger.exception(f'Accepting AI suggestion #{suggestion.id} failed')
            result = {'ok': False, 'result': None, 'error': str(e) or e.__class__.__name__, 'tool_call_id': None}
        if not result.get('ok'):
            ai_workflows_db_rollback()
            suggestion.status = SUGGESTION_OPEN
            suggestion.resolved_by_id = None
            suggestion.resolved_at = None
            suggestion.resolution_note = None
            suggestion.result_tool_call_id = result.get('tool_call_id')
            ai_workflows_db_commit()
            _track(suggestion, f'accepting failed: {tool}: {result.get("error")}', user_id)
            raise BusinessProcessingError(f'The proposed action failed: {result.get("error")}',
                                          data={'tool_call_id': result.get('tool_call_id')})
        suggestion.result = result.get('result')
        suggestion.result_tool_call_id = result.get('tool_call_id')
        ai_workflows_db_commit()

    tool_text = f' ({tool}, tool call #{suggestion.result_tool_call_id})' if tool else ''
    _track(suggestion, f'AI suggestion "{suggestion.title}" accepted{tool_text}', user_id)
    users = ai_workflows_db_user_summary([user_id])
    # The action result stays with the analyst: the run (and so the
    # workflow owner) learns that it was accepted, not what it returned
    expired = _resume_wait(suggestion, {
        'accepted': True,
        'suggestion_id': suggestion.id,
        'tool_call_id': suggestion.result_tool_call_id,
        'accepted_by': users.get(user_id),
        'note': note,
    }, user_id, None)
    data = _publish(suggestion, 'on_postload_ai_suggestion_accept', user_id)
    _publish_expired(expired)
    return data


def ai_suggestions_dismiss(suggestion_id, user_id, note=None, scope_mask=None) -> dict:
    ai_workflows_require_enabled()
    note = _note(note)
    suggestion = _get_visible(suggestion_id, user_id, lock=True, scope_mask=scope_mask)
    _require_open(suggestion)
    suggestion.status = SUGGESTION_DISMISSED
    suggestion.resolved_by_id = user_id
    suggestion.resolved_at = ai_workflows_db_utcnow()
    suggestion.resolution_note = note
    ai_workflows_db_commit()
    _track(suggestion, f'AI suggestion "{suggestion.title}" dismissed', user_id)
    users = ai_workflows_db_user_summary([user_id])
    # A dismissed question gets no answer: the run leaves through `timeout`
    port = _PORT_TIMEOUT if suggestion.kind == SUGGESTION_INFO_REQUEST else None
    expired = _resume_wait(suggestion, {
        'dismissed': True,
        'suggestion_id': suggestion.id,
        'dismissed_by': users.get(user_id),
        'note': note,
    }, user_id, port)
    data = _publish(suggestion, 'on_postload_ai_suggestion_dismiss', user_id)
    _publish_expired(expired)
    return data


def _form_fields(form_schema) -> list:
    if isinstance(form_schema, dict):
        fields = form_schema.get('fields')
    else:
        fields = form_schema
    return [f for f in fields or [] if isinstance(f, dict) and f.get('name')]


def _option_values(options) -> list:
    values = []
    for option in options or []:
        if isinstance(option, dict):
            values.append(option.get('value', option.get('label')))
        else:
            values.append(option)
    return values


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_field(field, value):
    """The validated value, or raises ValueError with the reason."""
    kind = field.get('type') or 'text'
    if kind not in _FIELD_TYPES:
        kind = 'text'
    if kind in ('text', 'textarea'):
        if not isinstance(value, str):
            raise ValueError('Must be a string')
        if len(value) > _MAX_TEXT_ANSWER:
            raise ValueError(f'At most {_MAX_TEXT_ANSWER} characters')
        return value
    if kind == 'number':
        if isinstance(value, bool):
            raise ValueError('Must be a number')
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                raise ValueError('Must be a number')
            return int(number) if number.is_integer() else number
        raise ValueError('Must be a number')
    if kind == 'boolean':
        if not isinstance(value, bool):
            raise ValueError('Must be true or false')
        return value
    options = _option_values(field.get('options'))
    if value not in options:
        raise ValueError('Not one of the allowed options')
    return value


def ai_suggestions_validate_answer(form_schema, answer) -> dict:
    """The cleaned answer; raises BusinessProcessingError with
    `data = {field: [message]}`."""
    if not isinstance(answer, dict):
        raise BusinessProcessingError('Invalid answer', data={'answer': ['Must be an object']})
    fields = _form_fields(form_schema)
    known = {f['name'] for f in fields}
    errors = {}
    for name in answer:
        if name not in known:
            errors.setdefault(name, []).append('Unknown field')
    cleaned = {}
    for field in fields:
        name = field['name']
        value = answer.get(name)
        if _is_empty(value):
            if field.get('required') and not (field.get('type') == 'boolean' and value is False):
                errors.setdefault(name, []).append('This field is required')
            continue
        try:
            cleaned[name] = _coerce_field(field, value)
        except ValueError as e:
            errors.setdefault(name, []).append(str(e))
    if errors:
        raise BusinessProcessingError('Invalid answer', data=errors)
    return cleaned


def ai_suggestions_answer(suggestion_id, user_id, answer, note=None, scope_mask=None) -> dict:
    ai_workflows_require_enabled()
    note = _note(note)
    suggestion = _get_visible(suggestion_id, user_id, lock=True, scope_mask=scope_mask)
    _require_open(suggestion)
    if suggestion.kind != SUGGESTION_INFO_REQUEST:
        ai_workflows_db_rollback()
        raise BusinessProcessingError('Only information requests take an answer')
    try:
        cleaned = ai_suggestions_validate_answer(suggestion.form_schema, answer)
    except BusinessProcessingError:
        ai_workflows_db_rollback()
        raise
    users = ai_workflows_db_user_summary([user_id])
    suggestion.status = SUGGESTION_ACCEPTED
    suggestion.answer = cleaned
    suggestion.resolved_by_id = user_id
    suggestion.resolved_at = ai_workflows_db_utcnow()
    suggestion.resolution_note = note
    ai_workflows_db_commit()
    _track(suggestion, f'AI information request "{suggestion.title}" answered', user_id)
    expired = _resume_wait(suggestion, {'answer': cleaned, 'answered_by': users.get(user_id)},
                           user_id, _PORT_ANSWERED)
    data = _publish(suggestion, 'on_postload_ai_suggestion_accept', user_id)
    _publish_expired(expired)
    return data
