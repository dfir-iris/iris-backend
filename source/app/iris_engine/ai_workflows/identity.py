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

"""Acting as a user outside of a request.

Tools, access checks and activity tracking read the principal from
`g` (`iris_current_user`, the permission mask cached by the access
control layer). `ai_workflows_identity` puts the run-as user there for
the duration of a block — pushing a throwaway request context when the
caller (a Celery task) has none — and restores whatever was there
before, so an analyst's own request is unaffected when a suggestion
executes inside it.

The run id and chain depth are published on `g` too: the trigger
listener reads them to suppress workflow → event → workflow loops.

The identity is refused when the user is gone, deactivated or barred
from MCP (`mcp_allowed`): runs act through the MCP tool dispatcher, and
the request-less principal set here does not carry that flag itself. A
run started through a scoped API key keeps that scope: `run.scope_mask`
is applied the way `_apply_api_key_scope_mask` applies a key's mask.
"""

import contextlib

from flask import current_app
from flask import g
from flask import has_request_context

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user

_SAVED_ATTRIBUTES = (
    'auth_user',
    'auth_token_user_id',
    'auth_user_permissions',
    'api_key_row',
    'token_user',
    'api_user',
    'auth_session_id',
    'ai_workflow_run_id',
    'ai_workflow_chain_depth',
)

_UNSET = object()


class AiWorkflowIdentityError(Exception):
    pass


class _ScopeRow:
    """Stands in for the `UserApiKey` row `_apply_api_key_scope_mask`
    reads the scope mask from."""

    def __init__(self, scope_mask):
        self.scope_mask = scope_mask


def ai_workflows_identity_check_user(user):
    """Raise AiWorkflowIdentityError when `user` may not act."""
    if user is None or not getattr(user, 'active', True):
        raise AiWorkflowIdentityError('The user the run acts as does not exist or is deactivated')
    if getattr(user, 'mcp_allowed', True) is False:
        raise AiWorkflowIdentityError(f'MCP is disabled for user {getattr(user, "user", None) or user.id}: '
                                      f'the run cannot act as this user')


def _caller_scope_mask(user_id):
    """Scope mask of the API key the current request authenticated with,
    when it is that of `user_id` itself (an analyst accepting a
    suggestion): acting as yourself never widens your key's scope."""
    row = getattr(g, 'api_key_row', None)
    mask = getattr(row, 'scope_mask', None)
    if mask is None or getattr(g, 'auth_token_user_id', None) != user_id:
        return None
    return int(mask)


def _combine(*masks):
    effective = None
    for mask in masks:
        if mask is None:
            continue
        effective = int(mask) if effective is None else effective & int(mask)
    return effective


def _save():
    return {name: getattr(g, name, _UNSET) for name in _SAVED_ATTRIBUTES}


def _restore(saved):
    for name, value in saved.items():
        if value is _UNSET:
            g.pop(name, None)
        else:
            setattr(g, name, value)


@contextlib.contextmanager
def _request_context():
    if has_request_context():
        yield
        return
    # Flask reuses the current app context when there is one, so `g` and
    # the database session are those of the caller
    with current_app.test_request_context('/ai-workflows/run'):
        yield


def _set_chain(run):
    g.ai_workflow_run_id = run.id
    g.ai_workflow_chain_depth = run.chain_depth or 0


@contextlib.contextmanager
def ai_workflows_identity(user_id, run=None):
    """Act as `user_id` (and, with `run`, inside that run) for the block.
    The permissions are the user's own, ANDed with the run's `scope_mask`
    and with the caller's API key scope when the caller is that user."""
    user = ai_workflows_db_get_user(user_id) if user_id else None
    ai_workflows_identity_check_user(user)
    with _request_context():
        scope_mask = _combine(getattr(run, 'scope_mask', None) if run is not None else None,
                              _caller_scope_mask(user.id))
        saved = _save()
        try:
            for name in ('api_key_row', 'api_user', 'auth_session_id', 'token_user',
                         'ai_workflow_run_id', 'ai_workflow_chain_depth'):
                g.pop(name, None)
            g.auth_user = {
                'user_id': user.id,
                'user_login': user.user,
                'user_name': user.name,
                'user_email': user.email,
            }
            g.auth_token_user_id = user.id
            permissions = ac_get_effective_permissions_of_user(user)
            if scope_mask is not None:
                permissions &= scope_mask
                g.api_key_row = _ScopeRow(scope_mask)
            g.auth_user_permissions = permissions
            if run is not None:
                _set_chain(run)
            yield user
        finally:
            _restore(saved)


@contextlib.contextmanager
def ai_workflows_identity_chain(run):
    """Only mark the block as part of `run` (hooks fired by the engine
    itself), keeping the current principal."""
    with _request_context():
        saved_run = getattr(g, 'ai_workflow_run_id', _UNSET)
        saved_depth = getattr(g, 'ai_workflow_chain_depth', _UNSET)
        try:
            _set_chain(run)
            yield
        finally:
            _restore({'ai_workflow_run_id': saved_run, 'ai_workflow_chain_depth': saved_depth})
