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

"""Suggestions: what a run proposes instead of doing.

A suggestion is attached to the run's entity and shown to its audience
(the entity's owners / war room members, or the workflow owner), never
to someone who cannot read the entity and every object it refers to.
Creating one notifies the audience in-app, pushes it on their socket
room and fires `on_postload_ai_suggestion_create`.

A run proposes at most `MAX_SUGGESTIONS_PER_RUN` suggestions (questions
to the analysts excepted). Their text is untrusted (model output): links
and images pointing outside IRIS are rendered inert, and the objects they
refer to are kept only when they exist and the run-as user can read
them, with their title read from IRIS rather than taken from the model.
"""

import logging
import re

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_owner_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_title
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_summary
from app.datamgmt.ai_workflows.ai_workflows_runtime_db import ai_workflows_runtime_db_count_run_suggestions
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_user_can_access
from app.iris_engine.ai_workflows.identity import ai_workflows_identity_chain
from app.models.ai_workflows import AUDIENCE_OWNER
from app.models.ai_workflows import AiSuggestion
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import SUGGESTION_DRY_RUN
from app.models.ai_workflows import SUGGESTION_GENERIC_ACTION
from app.models.ai_workflows import SUGGESTION_INFO_REQUEST
from app.models.ai_workflows import SUGGESTION_KINDS
from app.models.ai_workflows import SUGGESTION_OPEN
from app.models.authorization import ac_has_permission_server_administrator

logger = logging.getLogger(__name__)

SEVERITIES = ('info', 'low', 'medium', 'high', 'critical')

MAX_SUGGESTIONS_PER_RUN = 5
_MAX_REFS = 20

# `[text](url "title")` and `![alt](url)`: the url, optionally in <>
_INLINE_LINK = re.compile(r'(!?)\[((?:[^\[\]\\]|\\.)*)\]\(\s*<?([^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*)>?'
                          r'(?:\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\)))?\s*\)')
# `[label]: url` reference definitions
_REFERENCE_DEFINITION = re.compile(r'^( {0,3})\[([^\]]+)\]:[ \t]*<?(\S+?)>?(?=\s|$)', re.MULTILINE)
_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')

_ENTITY_LINKS = {
    'alert': '/alerts/{id}',
    'alert_cluster': '/alert-clusters/{id}',
    'case': '/case/{id}',
    'war_room': '/war-rooms/{id}',
}


def _entity_link(entity_type, entity_id):
    template = _ENTITY_LINKS.get(entity_type)
    return template.format(id=entity_id) if template and entity_id is not None else None


def ai_workflows_suggestions_inbox_link(suggestion_id) -> str:
    """The suggestion in the suggestions inbox."""
    return f'/suggestions?id={int(suggestion_id)}'


class AiWorkflowSuggestionLimitError(Exception):
    pass


def _is_relative(url) -> bool:
    """A link inside IRIS: a path, a query or an anchor — not a scheme
    (http:, javascript:, data:...) and not a protocol-relative `//host`."""
    url = url.strip()
    if not url:
        return True
    if url.startswith('//') or url.startswith('\\') or url.startswith('/\\'):
        return False
    return not _SCHEME.match(url)


def _inline_link(match) -> str:
    is_image, text, url = match.group(1), match.group(2), match.group(3)
    if _is_relative(url):
        return match.group(0)
    label = text.strip() or ('image' if is_image else 'link')
    return f'{label} ({url})'


def _reference_definition(match) -> str:
    indent, label, url = match.group(1), match.group(2), match.group(3)
    if _is_relative(url):
        return match.group(0)
    return f'{indent}{label}: ({url})'


def ai_workflows_suggestions_neutralise_links(text):
    """`text` (Markdown) with the links and images pointing outside IRIS
    written out as `text (url)`: an image is never fetched and a link
    never disguised, whatever the model was made to write."""
    if not isinstance(text, str) or ('](' not in text and ']:' not in text):
        return text
    # Nested links (`[![img](a)](b)`) unwrap one level per pass
    for _ in range(5):
        neutralised = _INLINE_LINK.sub(_inline_link, text)
        if neutralised == text:
            break
        text = neutralised
    return _REFERENCE_DEFINITION.sub(_reference_definition, text)


def _refs(related_refs) -> list:
    """`[{type, id, ...}]` entries that point at an entity type."""
    refs = []
    for ref in related_refs or []:
        if not isinstance(ref, dict) or ref.get('type') not in ENTITY_TYPES:
            continue
        try:
            ref_id = int(ref.get('id'))
        except (TypeError, ValueError):
            continue
        refs.append({**ref, 'id': ref_id})
    return refs


def _verified_refs(run, related_refs) -> list:
    """The refs pointing at an object that exists and the run-as user
    can read, with the title IRIS has for it."""
    verified = []
    seen = set()
    for ref in _refs(related_refs):
        key = (ref['type'], ref['id'])
        if key in seen:
            continue
        seen.add(key)
        if len(verified) >= _MAX_REFS:
            break
        try:
            title = ai_workflows_db_entity_title(ref['type'], ref['id'])
            if title is None or not ai_workflows_entities_user_can_access(run.run_as_user_id, ref['type'],
                                                                           ref['id']):
                continue
        except Exception:
            logger.exception(f'AI workflow run {run.uuid}: could not verify {ref["type"]} #{ref["id"]}')
            continue
        verified.append({'type': ref['type'], 'id': ref['id'], 'title': str(title)[:255]})
    return verified


def _check_limit(run, kind):
    """Refuse a suggestion past the per-run limit (questions to the
    analysts are not counted: they are the workflow's own waits)."""
    if kind == SUGGESTION_INFO_REQUEST or run.id is None:
        return
    count = ai_workflows_runtime_db_count_run_suggestions(run.id, exclude_kinds=(SUGGESTION_INFO_REQUEST,))
    if count >= MAX_SUGGESTIONS_PER_RUN:
        raise AiWorkflowSuggestionLimitError(
            f'This run already proposed {count} suggestions, the limit is {MAX_SUGGESTIONS_PER_RUN}')


def _confidence(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(value, 0.0), 1.0)


def _audience(run, entity_type, entity_id) -> list:
    snapshot = run.definition_snapshot or {}
    if run.is_dry_run:
        # Only whoever started the dry run looks at what it would suggest
        candidates = [run.triggered_by_user_id or snapshot.get('owner_id')]
    elif snapshot.get('suggestion_audience') == AUDIENCE_OWNER:
        candidates = [snapshot.get('owner_id')]
    else:
        candidates = list(ai_workflows_db_entity_owner_ids(entity_type, entity_id)) if entity_type else []
        candidates.append(run.run_as_user_id)
        if not entity_type:
            candidates.append(snapshot.get('owner_id'))
    audience = []
    for user_id in candidates:
        if user_id and user_id not in audience:
            audience.append(user_id)
    return audience


def _can_see_entities(user_id, suggestion) -> bool:
    if suggestion.entity_type and suggestion.entity_id is not None:
        if not ai_workflows_entities_user_can_access(user_id, suggestion.entity_type, suggestion.entity_id):
            return False
    return all(ai_workflows_entities_user_can_access(user_id, ref['type'], ref['id'])
               for ref in _refs(suggestion.related_refs))


def ai_workflows_suggestions_user_can_see(user_id, suggestion) -> bool:
    user = ai_workflows_db_get_user(user_id) if user_id else None
    if user is None:
        return False
    if ac_has_permission_server_administrator(ac_get_effective_permissions_of_user(user)):
        return True
    audience = suggestion.audience_user_ids
    if audience is not None and user_id not in audience:
        return False
    return _can_see_entities(user_id, suggestion)


def ai_workflows_suggestions_create(run, step, **fields) -> AiSuggestion:
    """Persist (and commit) a suggestion of `run`; fields are the
    AiSuggestion columns `kind, title, body, proposed_action, form_schema,
    related_refs, confidence, severity, status, wait_id`. Raises
    AiWorkflowSuggestionLimitError past `MAX_SUGGESTIONS_PER_RUN`."""
    kind = fields.get('kind') if fields.get('kind') in SUGGESTION_KINDS else SUGGESTION_GENERIC_ACTION
    _check_limit(run, kind)
    status = fields.get('status') or SUGGESTION_OPEN
    if run.is_dry_run:
        status = SUGGESTION_DRY_RUN
    severity = fields.get('severity')
    entity_type = run.entity_type
    entity_id = run.entity_id
    title = ai_workflows_suggestions_neutralise_links(
        (str(fields.get('title') or '').strip() or 'AI workflow suggestion'))[:1000]
    body = ai_workflows_suggestions_neutralise_links(fields.get('body') or None)

    suggestion = AiSuggestion(
        run_id=run.id,
        step_id=step.id if step is not None else None,
        workflow_id=run.workflow_id,
        entity_type=entity_type,
        entity_id=entity_id,
        sub_entity=run.sub_entity,
        case_id=entity_id if entity_type == ENTITY_CASE else None,
        war_room_id=entity_id if entity_type == ENTITY_WAR_ROOM else None,
        alert_id=entity_id if entity_type == ENTITY_ALERT else None,
        customer_id=run.customer_id,
        kind=kind,
        title=title,
        body=body or None,
        proposed_action=fields.get('proposed_action') or None,
        form_schema=fields.get('form_schema') or None,
        related_refs=_verified_refs(run, fields.get('related_refs')) or None,
        confidence=_confidence(fields.get('confidence')),
        severity=severity if severity in SEVERITIES else None,
        status=status,
        wait_id=fields.get('wait_id'),
    )
    ai_workflows_db_add(suggestion)
    suggestion.audience_user_ids = [user_id for user_id in _audience(run, entity_type, entity_id)
                                    if _can_see_entities(user_id, suggestion)]
    ai_workflows_db_commit()

    if status in (SUGGESTION_OPEN, SUGGESTION_DRY_RUN):
        _publish_created(run, suggestion)
    return suggestion


def _publish_created(run, suggestion):
    from app.iris_engine.module_handler.module_handler import call_modules_hook
    from app.iris_engine.notifications.service import notify_many

    dry_run = suggestion.status == SUGGESTION_DRY_RUN
    prefix = 'AI suggestion (dry run)' if dry_run else 'AI suggestion'
    try:
        notify_many(
            suggestion.audience_user_ids or [],
            'ai_suggestion',
            f'{prefix}: {suggestion.title}'[:250],
            body=(suggestion.body or '')[:1000] or None,
            link=ai_workflows_suggestions_inbox_link(suggestion.id),
            source_type='ai_suggestion',
            source_id=suggestion.id,
        )
    except Exception:
        logger.exception(f'Could not notify the audience of AI suggestion #{suggestion.id}')
    ai_workflows_suggestions_emit(suggestion, 'created')
    if dry_run:
        return
    try:
        with ai_workflows_identity_chain(run):
            call_modules_hook('on_postload_ai_suggestion_create', data=ai_workflows_suggestions_serialize(suggestion),
                              caseid=suggestion.case_id)
    except Exception:
        logger.exception(f'on_postload_ai_suggestion_create failed for AI suggestion #{suggestion.id}')


def _iso(value):
    return value.isoformat() if value is not None else None


def ai_workflows_suggestions_serialize(suggestion) -> dict:
    run = suggestion.run
    resolved_by = None
    if suggestion.resolved_by_id:
        resolved_by = ai_workflows_db_user_summary([suggestion.resolved_by_id]).get(suggestion.resolved_by_id)
    form_schema = suggestion.form_schema if isinstance(suggestion.form_schema, dict) else None
    if form_schema is not None and not isinstance(form_schema.get('fields'), list):
        form_schema = {**form_schema, 'fields': []}
    action = suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else None
    return {
        'id': suggestion.id,
        'uuid': str(suggestion.uuid) if suggestion.uuid else None,
        'run_id': suggestion.run_id,
        'run_uuid': str(run.uuid) if run is not None and run.uuid else None,
        'workflow_id': suggestion.workflow_id,
        'workflow_name': run.workflow_name if run is not None else None,
        'entity_type': suggestion.entity_type,
        'entity_id': suggestion.entity_id,
        'entity_title': ai_workflows_db_entity_title(suggestion.entity_type, suggestion.entity_id)
        if suggestion.entity_type else None,
        'sub_entity': suggestion.sub_entity,
        'kind': suggestion.kind,
        'title': suggestion.title,
        'body': suggestion.body,
        'proposed_action': action,
        'form_schema': form_schema,
        'related_refs': suggestion.related_refs or [],
        'confidence': suggestion.confidence,
        'severity': suggestion.severity,
        'status': suggestion.status,
        'created_at': _iso(suggestion.created_at),
        'resolved_at': _iso(suggestion.resolved_at),
        'resolved_by': resolved_by,
        'resolution_note': suggestion.resolution_note,
        'answer': suggestion.answer,
        'result': suggestion.result,
        'can_accept': suggestion.status == SUGGESTION_OPEN and suggestion.kind != SUGGESTION_INFO_REQUEST,
    }


def ai_workflows_suggestions_emit(suggestion, action):
    """Push `{action: created|updated, suggestion}` to each audience
    member's notification room. Never with the answer or the result:
    the whole audience gets the push, the REST view filters them per
    reader."""
    from app.iris_engine.notifications.service import _safe_socket_emit

    try:
        serialized = ai_workflows_suggestions_serialize(suggestion)
        serialized.update(answer=None, result=None)
        payload = {'action': action, 'suggestion': serialized}
    except Exception:
        logger.exception(f'Could not serialize AI suggestion #{suggestion.id}')
        return
    for user_id in suggestion.audience_user_ids or []:
        _safe_socket_emit('ai_suggestion', payload, room=f'user-{user_id}')
