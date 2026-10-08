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

"""v2 endpoints for AI suggestions.

Any authenticated user can read and resolve the suggestions they can see
(entity access + audience, see `app.business.ai_suggestions`), bounded by
the scope of the API key the request authenticated with; no AI workflow
permission is needed. Accepting a suggestion executes its proposed write
action as the current user, so the user's own permissions (and key
scope) apply to the action. Resolving answers 403 while
`AI_WORKFLOWS_ENABLED` is off.
"""

from flask import Blueprint
from flask import g
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.ai_suggestions import ai_suggestions_accept
from app.business.ai_suggestions import ai_suggestions_answer
from app.business.ai_suggestions import ai_suggestions_counts
from app.business.ai_suggestions import ai_suggestions_dismiss
from app.business.ai_suggestions import ai_suggestions_get
from app.business.ai_suggestions import ai_suggestions_list
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _int_arg(name):
    value = request.args.get(name)
    if value is None or value == '':
        return None
    try:
        return int(value)
    except ValueError:
        raise BusinessProcessingError(f'Invalid {name}', data={name: ['Must be an integer']})


def _scope_mask():
    """Scope mask of the API key the request authenticated with (None:
    session, or a key without a scope)."""
    mask = getattr(getattr(g, 'api_key_row', None), 'scope_mask', None)
    return int(mask) if mask is not None else None


def _handle(operation):
    try:
        return response_api_success(operation())
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiWorkflowsForbiddenError as e:
        return response_api_error(e.get_message(), data=e.get_data(), status=403)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())


ai_suggestions_blueprint = Blueprint('ai_suggestions_rest_v2', __name__, url_prefix='/ai-suggestions')


@ai_suggestions_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='List the AI suggestions the user can see',
         query_params=[('entity_type', 'string'), ('entity_id', 'integer'),
                       ('status', 'string', "Suggestion status, 'open' by default, 'all' for every status"),
                       ('run_uuid', 'string')])
def list_ai_suggestions_route():
    def _operation():
        return ai_suggestions_list(
            iris_current_user.id,
            entity_type=request.args.get('entity_type') or None,
            entity_id=_int_arg('entity_id'),
            status=request.args.get('status') or None,
            run_uuid=request.args.get('run_uuid') or None,
            scope_mask=_scope_mask(),
        )
    return _handle(_operation)


@ai_suggestions_blueprint.get('/counts')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='Count the open AI suggestions per entity',
         query_params=[('entity_type', 'string', 'Entity type', True),
                       ('entity_ids', 'string', 'Comma-separated entity ids', True)])
def count_ai_suggestions_route():
    raw = request.args.get('entity_ids') or ''
    entity_ids = [part.strip() for part in raw.split(',') if part.strip()]
    return _handle(lambda: ai_suggestions_counts(iris_current_user.id, request.args.get('entity_type'), entity_ids,
                                                         scope_mask=_scope_mask()))


@ai_suggestions_blueprint.get('/<int:identifier>')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='Get an AI suggestion')
def get_ai_suggestion_route(identifier):
    return _handle(lambda: ai_suggestions_get(identifier, iris_current_user.id, scope_mask=_scope_mask()))


@ai_suggestions_blueprint.post('/<int:identifier>/accept')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='Accept an AI suggestion: its proposed write action runs as the current user')
def accept_ai_suggestion_route(identifier):
    body = _body()
    return _handle(lambda: ai_suggestions_accept(identifier, iris_current_user.id, note=body.get('note'),
                                                 scope_mask=_scope_mask()))


@ai_suggestions_blueprint.post('/<int:identifier>/dismiss')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='Dismiss an AI suggestion')
def dismiss_ai_suggestion_route(identifier):
    body = _body()
    return _handle(lambda: ai_suggestions_dismiss(identifier, iris_current_user.id, note=body.get('note'),
                                                  scope_mask=_scope_mask()))


@ai_suggestions_blueprint.post('/<int:identifier>/answer')
@ac_api_requires()
@api_doc(tags=['AiSuggestions'], summary='Answer an AI information request and resume its workflow run')
def answer_ai_suggestion_route(identifier):
    body = _body()
    return _handle(lambda: ai_suggestions_answer(identifier, iris_current_user.id, body.get('answer'),
                                                 note=body.get('note'), scope_mask=_scope_mask()))
