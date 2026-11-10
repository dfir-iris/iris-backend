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

"""Node types: the catalogue the editor shows and their executors.

An executor gets a `NodeContext` (the run, its step, the keystore
resolver, the write allowlist) and the node config, and returns a
`NodeResult`: the output stored under `nodes.<id>.output`, the port the
run leaves through, and optionally a wait to park on (async HTTP, a
question to the analysts, a delay). It runs as the run-as user. A raised
exception leaves through the node's `error` port when it has one and
fails the run otherwise.

`key()` gives a keystore value only in the fields of an `http_request`
node (elsewhere it renders `[secret:NAME]`); the entries an earlier node
used are restored in every node so that they stay masked and their host
restrictions keep applying.
"""

import base64
import datetime
import hashlib
import json
import posixpath
import threading
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlencode
from urllib.parse import urlsplit

from flask import current_app

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_owner_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_last_finished_run
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_ids_by_names
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.datamgmt.ai_workflows.ai_workflows_runtime_db import ai_workflows_runtime_db_cancel_wait
from app.datamgmt.ai_workflows.ai_workflows_runtime_db import ai_workflows_runtime_db_create_wait
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_proxies
from app.iris_engine.ai_workflows.agent import ai_workflows_agent_check_identifier
from app.iris_engine.ai_workflows.agent import ai_workflows_agent_pin_arguments
from app.iris_engine.ai_workflows.agent import ai_workflows_agent_run
from app.iris_engine.ai_workflows.context import MAX_RENDERED_CHARS
from app.iris_engine.ai_workflows.context import AiWorkflowTemplateError
from app.iris_engine.ai_workflows.context import ai_workflows_context_check_url_origin
from app.iris_engine.ai_workflows.context import ai_workflows_context_render_url_base
from app.iris_engine.ai_workflows.context import ai_workflows_context_split_url
from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_expression
from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_rules
from app.iris_engine.ai_workflows.context import ai_workflows_context_get_path
from app.iris_engine.ai_workflows.context import ai_workflows_context_path_value
from app.iris_engine.ai_workflows.context import ai_workflows_context_render
from app.iris_engine.ai_workflows.context import ai_workflows_context_render_arguments
from app.iris_engine.ai_workflows.context import ai_workflows_context_template
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_snapshot
from app.iris_engine.ai_workflows.sandbox import DEFAULT_MAX_STEPS
from app.iris_engine.ai_workflows.sandbox import DEFAULT_TIMEOUT_SECONDS
from app.iris_engine.ai_workflows.sandbox import MAX_STEPS
from app.iris_engine.ai_workflows.sandbox import MAX_TIMEOUT_SECONDS
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_execute
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_user_can_access
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_create
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_neutralise_links
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_READ
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_WRITE
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_classification
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_execute
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_record
from app.iris_engine.webhooks.render import MASK
from app.iris_engine.webhooks.render import webhooks_render_request
from app.iris_engine.webhooks.sender import webhooks_send
from app.models.ai_workflows import AUDIENCE_ENTITY
from app.models.ai_workflows import AUDIENCE_OWNER
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import EXEC_ALLOWLISTED_WRITE
from app.models.ai_workflows import EXEC_AUTO_READ
from app.models.ai_workflows import EXEC_SUGGESTED
from app.models.ai_workflows import RUN_FAILED
from app.models.ai_workflows import RUN_SKIPPED
from app.models.ai_workflows import RUN_SUCCEEDED
from app.models.ai_workflows import SUGGESTION_GENERIC_ACTION
from app.models.ai_workflows import SUGGESTION_INFO_REQUEST
from app.models.ai_workflows import WAIT_CALLBACK
from app.models.ai_workflows import WAIT_DELAY
from app.models.ai_workflows import WAIT_USER_INPUT
from app.models.ai_workflows import WAIT_WORKFLOW
from app.models.notifications import NOTIFICATION_SEVERITIES

NODE_TRIGGER = 'trigger'
NODE_AI_AGENT = 'ai_agent'
NODE_CONDITION = 'condition'
NODE_HTTP_REQUEST = 'http_request'
NODE_ASK_ANALYST = 'ask_analyst'
NODE_FIND_RELATED = 'find_related'
NODE_FIND_WAR_ROOM_TASKS = 'find_war_room_tasks'
NODE_SUGGEST = 'suggest'
NODE_ACTION = 'action'
NODE_NOTIFY = 'notify'
NODE_DELAY = 'delay'
NODE_SET_VARIABLES = 'set_variables'
NODE_PYTHON = 'python'
NODE_STOP = 'stop'
NODE_RUN_WORKFLOW = 'run_workflow'
# `workflow_id` of a run_workflow node calling the workflow it belongs to:
# no id to fix after an import or an export
RUN_WORKFLOW_SELF = 'self'
NODE_NOTE = 'note'

NOTE_COLORS = ('yellow', 'blue', 'green', 'pink', 'purple', 'gray')

PORT_OUT = 'out'
PORT_ERROR = 'error'
PORT_TIMEOUT = 'timeout'
PORT_TRUE = 'true'
PORT_FALSE = 'false'
PORT_ANSWERED = 'answered'
PORT_FOUND = 'found'
PORT_NONE = 'none'

# Field types an `ask_analyst` form may use (rendered by the UI)
FORM_FIELD_TYPES = ('text', 'textarea', 'number', 'boolean', 'select', 'multiselect', 'date')

# `delivery` of a `notify` node: a notification in the bell, a toast that
# pops up in the browsers of the users connected, or both
NOTIFY_NOTIFICATION = 'notification'
NOTIFY_TOAST = 'toast'
NOTIFY_DELIVERIES = (NOTIFY_NOTIFICATION, NOTIFY_TOAST, 'both')
# `timeout_seconds` of a toast: 0 keeps it until it is closed
MAX_TOAST_SECONDS = 3600

_MAX_WAIT_MINUTES = 60 * 24 * 30
_MAX_HTTP_TIMEOUT = 120
_MAX_DRY_RUN_BODY = 10_000
# The whole response is read (up to the sender's 1 MiB) so a large JSON
# answer still parses; a body kept as text is cut to _MAX_TEXT_BODY
_MAX_HTTP_RESPONSE_CHARS = 1024 * 1024
_MAX_TEXT_BODY = 64 * 1024

# `auth_type` of an HTTP request fetching its bearer token from an OAuth 2
# token endpoint (client credentials grant) before sending
AUTH_OAUTH2_CLIENT_CREDENTIALS = 'oauth2_client_credentials'
HTTP_AUTH_TYPES = ('none', 'basic', 'bearer', AUTH_OAUTH2_CLIENT_CREDENTIALS)
# How the client authenticates to the token endpoint: client_secret_post
# or client_secret_basic
OAUTH_CLIENT_AUTH = ('body', 'basic')
# Tokens are reused until shortly before they expire, across runs: the
# cache key is derived from the client secret, so only a run holding it
# gets the token
_OAUTH_CACHE_MAX = 256
_OAUTH_EXPIRY_MARGIN_SECONDS = 60
_OAUTH_DEFAULT_EXPIRES_IN = 3600
_OAUTH_MAX_ERROR = 300
_oauth_tokens = {}
_oauth_tokens_lock = threading.Lock()

# Paths of this instance's own inbound endpoints: a request to them would
# start (hooks) or resume (callbacks) runs, i.e. loop
_HOOKS_PATH = '/api/v2/ai-workflows/hooks/'
_CALLBACKS_PATH = '/api/v2/ai-workflows/callbacks/'


class AiWorkflowNodeError(Exception):
    pass


@dataclass
class NodeResult:
    output: dict = None
    port: str = PORT_OUT
    # Persisted on the step as its input (masked)
    input: dict = None
    # Park the run: {kind, minutes, token_hash?, uuid?, suggestion?: {fields}, timeout_output?}
    wait: dict = None
    # Stop node: final run status and reason
    stop_status: str = None
    stop_reason: str = None
    # Top-level context keys to replace (`entity`, `vars`)
    context_updates: dict = field(default_factory=dict)


class NodeContext:
    """What an executor works with. Tokens spent are added to the run."""

    def __init__(self, run, step, node, resolver):
        self.run = run
        self.step = step
        self.node = node
        self.resolver = resolver
        snapshot = run.definition_snapshot or {}
        self.allowlist = set(snapshot.get('write_tool_allowlist') or [])
        self.snapshot = snapshot
        self.tokens = 0
        # What the node works on, set by the executor as soon as it is
        # known: the step keeps it as its input even when the node fails
        self.input = None
        # Entries used by earlier nodes: masked here too, and their host
        # restrictions apply to this node's requests
        restore = getattr(resolver, 'restore', None)
        used = getattr(run, 'used_key_names', None)
        if restore is not None and used:
            restore(list(used))

    def template_context(self, for_llm=False, extra=None, reveal_keys=False) -> dict:
        """Template variables; `key()` gives the plain secret only with
        `reveal_keys` (the fields of an HTTP request)."""
        return ai_workflows_context_template(self.run, self.resolver, for_llm=for_llm, extra=extra,
                                             reveal_keys=reveal_keys)

    def render(self, source, field_name, extra=None) -> str:
        return ai_workflows_context_render(source, self.template_context(extra=extra), field_name)

    def mask(self, obj):
        return self.resolver.mask(obj)

    def add_tokens(self, count):
        count = int(count or 0)
        self.tokens += count
        self.run.tokens_used = (self.run.tokens_used or 0) + count


def _node_spec(node_type, label, description, category, ports, config_defaults):
    return {
        'type': node_type,
        'label': label,
        'description': description,
        'category': category,
        'ports': list(ports),
        'config_defaults': config_defaults,
    }


_CATALOGUE = (
    _node_spec(NODE_TRIGGER, 'Trigger', 'Where a run starts: an event, a schedule, a manual run, an inbound '
               'webhook or a calling workflow, set on the node. A workflow may hold several.', 'trigger',
               (PORT_OUT,), {'trigger_type': 'manual'}),
    _node_spec(NODE_AI_AGENT, 'AI agent', 'Runs the AI assistant with a prompt and a set of tools. Reads run; '
               'writes run only when allowlisted on the workflow, otherwise they become suggestions.', 'ai',
               (PORT_OUT, PORT_ERROR),
               {'prompt': '', 'tools': [], 'output_schema': None, 'max_turns': 6, 'max_tool_calls': 10,
                'timeout_minutes': 10, 'model': '', 'include_entity': True}),
    _node_spec(NODE_CONDITION, 'Condition', 'Branches on an expression or on rules over the run context.',
               'logic', (PORT_TRUE, PORT_FALSE),
               {'mode': 'rules', 'expression': '', 'logic': 'and', 'rules': []}),
    _node_spec(NODE_HTTP_REQUEST, 'HTTP request', 'Calls an external system. In async mode the run waits '
               'for the system to call back `callback.url` with `callback.token`.', 'integration',
               (PORT_OUT, PORT_ERROR, PORT_TIMEOUT),
               {'method': 'POST', 'url': '', 'query_params': [], 'auth_type': 'none', 'auth_username': '',
                'auth_secret': '', 'oauth_token_url': '', 'oauth_client_id': '', 'oauth_client_secret': '',
                'oauth_scope': '', 'oauth_client_auth': 'body', 'headers': [], 'body_mode': 'default',
                'body_template': '',
                'content_type': 'application/json', 'mode': 'sync', 'timeout_seconds': 15,
                'wait_timeout_minutes': 60, 'verify_tls': True, 'use_proxy': True, 'response_format': 'json'}),
    _node_spec(NODE_ASK_ANALYST, 'Ask an analyst', 'Asks the audience a question with a form and waits for '
               'the answer.', 'human', (PORT_ANSWERED, PORT_TIMEOUT),
               {'title': '', 'question': '', 'fields': [], 'fields_from': '', 'timeout_minutes': 1440,
                'audience': AUDIENCE_ENTITY, 'user_ids': [], 'popup': True}),
    _node_spec(NODE_FIND_RELATED, 'Find related', 'Finds alerts and cases related to the alert of the run, '
               'or searches IRIS for a value.', 'iris', (PORT_OUT, PORT_ERROR),
               {'source': 'alert', 'days_back': 30, 'open_alerts': True, 'closed_alerts': False,
                'open_cases': True, 'closed_cases': False, 'number_of_nodes': 100, 'search_value': ''}),
    _node_spec(NODE_FIND_WAR_ROOM_TASKS, 'Find war room tasks', 'Lists the tasks of the war room created '
               'since the last run, or in the last minutes.', 'iris', (PORT_FOUND, PORT_NONE),
               {'since': 'last_run', 'minutes': 60}),
    _node_spec(NODE_SUGGEST, 'Suggest', 'Proposes something to the analysts, optionally with actions they '
               'accept in one click, all or a few of them.', 'human', (PORT_OUT,),
               {'kind': SUGGESTION_GENERIC_ACTION, 'title': '', 'body': '', 'proposed_action': None, 'actions': [],
                'confidence': None, 'severity': None, 'audience': AUDIENCE_ENTITY, 'user_ids': []}),
    _node_spec(NODE_ACTION, 'Action', 'Runs an IRIS tool. Write tools must be on the workflow allowlist.',
               'iris', (PORT_OUT, PORT_ERROR), {'tool': '', 'arguments': {}}),
    _node_spec(NODE_NOTIFY, 'Notify', 'Sends an in-app notification, a toast, or both.', 'human', (PORT_OUT,),
               {'audience': AUDIENCE_ENTITY, 'user_ids': [], 'users_from': '', 'title': '', 'body': '',
                'about': None, 'delivery': NOTIFY_NOTIFICATION, 'severity': 'info', 'timeout_seconds': 5}),
    _node_spec(NODE_DELAY, 'Delay', 'Waits before continuing.', 'flow', (PORT_OUT,), {'minutes': 5}),
    _node_spec(NODE_SET_VARIABLES, 'Set variables', 'Stores values under `vars` for later nodes.', 'flow',
               (PORT_OUT,), {'variables': []}),
    _node_spec(NODE_PYTHON, 'Python transform', 'Transforms data with a short script in a restricted Python '
               'subset (no imports, files, network or attributes). The value of `result` (or of a top-level '
               '`return`) is the output.', 'flow', (PORT_OUT, PORT_ERROR),
               {'code': 'result = {}', 'inputs': [], 'timeout_seconds': DEFAULT_TIMEOUT_SECONDS,
                'max_steps': DEFAULT_MAX_STEPS}),
    _node_spec(NODE_STOP, 'Stop', 'Ends the run with a status.', 'flow', (),
               {'status': RUN_SUCCEEDED, 'reason': ''}),
    _node_spec(NODE_RUN_WORKFLOW, 'Run workflow', 'Runs another workflow (one with a "Called by a workflow" '
               'trigger) on the same entity, as the same user, with `trigger.payload.inputs` set from the '
               'inputs. In wait mode the run continues once it ends, with its status, `vars` and last output.',
               'flow', (PORT_OUT, PORT_ERROR, PORT_TIMEOUT),
               {'workflow_id': None, 'inputs': [], 'mode': 'wait', 'timeout_minutes': 60}),
    _node_spec(NODE_NOTE, 'Note', 'Documents a part of the workflow on the canvas. Never connected and never '
               'run.', 'annotation', (), {'text': '', 'color': NOTE_COLORS[0], 'width': 260, 'height': 160}),
)

NODE_PORTS = {spec['type']: tuple(spec['ports']) for spec in _CATALOGUE}


def ai_workflows_nodes_catalogue() -> list:
    return json.loads(json.dumps(_CATALOGUE))


def ai_workflows_nodes_is_waiting(node) -> bool:
    """Whether a node parks the run (and so may close a cycle)."""
    node_type = node.get('type')
    config = node.get('config') or {}
    if node_type == NODE_HTTP_REQUEST:
        return config.get('mode') == 'async'
    if node_type == NODE_DELAY:
        # A 0-minute delay passes straight through: it does not park
        return _int(config.get('minutes'), 5, 0, _MAX_WAIT_MINUTES) > 0
    if node_type == NODE_RUN_WORKFLOW:
        return config.get('mode', 'wait') != 'async'
    return node_type == NODE_ASK_ANALYST


def _int(value, default, low, high):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return min(max(value, low), high)


# ---- Executors --------------------------------------------------------------

def _trigger(ctx, _config):
    run = ctx.run
    entity = ai_workflows_entities_snapshot(run.entity_type, run.entity_id) if run.entity_type else {}
    trigger = (run.context or {}).get('trigger') or {}
    return NodeResult(
        input=trigger,
        output={
            'type': trigger.get('type'),
            'node_id': trigger.get('node_id') or (ctx.node or {}).get('id'),
            'hook': trigger.get('hook'),
            'entity_type': run.entity_type,
            'entity_id': run.entity_id,
            'sub_entity': run.sub_entity,
        },
        context_updates={'entity': ctx.mask(entity)},
    )


def _ai_agent(ctx, config):
    ctx.input = {'prompt': config.get('prompt'), 'tools': config.get('tools') or []}
    output = ai_workflows_agent_run(ctx, config)
    return NodeResult(output=output, input=ctx.input)


def _condition(ctx, config):
    context = ctx.template_context()
    if (config.get('mode') or 'rules') == 'expression':
        ctx.input = {'expression': config.get('expression')}
        result = ai_workflows_context_eval_expression(config.get('expression'), context)
    else:
        # Each rule with the value it found at its path
        rules = [rule for rule in config.get('rules') or [] if isinstance(rule, dict)]
        ctx.input = {'logic': config.get('logic') or 'and', 'rules': [
            {**rule, 'actual': ai_workflows_context_path_value(context, rule.get('path'))} for rule in rules
        ]}
        result = ai_workflows_context_eval_rules(config.get('rules'), config.get('logic'), context)
    return NodeResult(output={'result': bool(result)}, port=PORT_TRUE if result else PORT_FALSE,
                      input=ctx.input)


def _text_body(text):
    if len(text) <= _MAX_TEXT_BODY:
        return text
    return f'{text[:_MAX_TEXT_BODY]}\n… [truncated, {len(text)} characters]'


def _parse_body(text, response_format):
    if text is None:
        return text
    if response_format == 'text':
        return _text_body(text)
    try:
        return json.loads(text)
    except ValueError:
        return _text_body(text)


def _http_config(config, template):
    """The node config with the secret-bearing values pre-rendered: the
    webhook renderer sends `secret` entries verbatim."""
    rendered = dict(config)
    for name in ('auth_secret', 'auth_username'):
        rendered[name] = ai_workflows_context_render(config.get(name) or '', template, name)
    for key in ('headers', 'query_params'):
        entries = []
        for index, entry in enumerate(config.get(key) or []):
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            if entry.get('secret'):
                entry['value'] = ai_workflows_context_render(entry.get('value') or '', template, f'{key}.{index}')
            entries.append(entry)
        rendered[key] = entries
    return rendered


def _config_hosts(value) -> set:
    """Host names of the URLs in a config value (a URL, a list or a
    comma / space separated string of them)."""
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = str(value or '').replace(',', ' ').split()
    hosts = set()
    for item in items:
        try:
            host = urlsplit(item if '://' in item else f'//{item}').hostname
        except ValueError:
            continue
        if host:
            hosts.add(host.lower().rstrip('.'))
    return hosts


def _own_hosts() -> set:
    hosts = set()
    for name in ('AI_WORKFLOWS_CALLBACK_BASE_URL', 'IRIS_ALLOW_ORIGIN', 'IRIS_ALLOWED_ORIGINS'):
        hosts |= _config_hosts(current_app.config.get(name))
    return hosts


def _normalised_path(path) -> str:
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.replace('\\', '/')
    normalised = posixpath.normpath(path or '/')
    # normpath keeps a leading `//`
    normalised = '/' + normalised.lstrip('/')
    return f'{normalised}/' if path.endswith('/') and not normalised.endswith('/') else normalised


def _refuse_own_endpoints(url):
    """A request must not reach an AI workflow inbound hook (of any
    instance: it would start runs, possibly this one again) nor a
    callback of this instance (it would resume a run)."""
    parts = urlsplit(url)
    path = _normalised_path(parts.path).lower()
    if _HOOKS_PATH in path or path.endswith(_HOOKS_PATH.rstrip('/')):
        raise AiWorkflowNodeError('Requests to AI workflow inbound hooks are refused')
    host = (parts.hostname or '').lower().rstrip('.')
    if (_CALLBACKS_PATH in path or path.endswith(_CALLBACKS_PATH.rstrip('/'))) and host in _own_hosts():
        raise AiWorkflowNodeError('Requests to the AI workflow callbacks of this instance are refused')


def _scrub_token(value, token):
    """`value` without the callback token, a bearer credential that is
    never persisted (only its hash is)."""
    if not token or value is None:
        return value
    return json.loads(json.dumps(value, default=str).replace(token, '[callback-token]'))


def _dry_run_body(body):
    if body is None:
        return None
    if isinstance(body, bytes):
        body = body.decode('utf-8', errors='replace')
    body = str(body)
    if len(body) > _MAX_DRY_RUN_BODY:
        body = f'{body[:_MAX_DRY_RUN_BODY]}… [truncated]'
    return body


def _prepare_wait(ctx, wait_uuid, token_hash, minutes, kind=WAIT_CALLBACK):
    """Commit the wait before the request goes out or the called run
    starts, so a fast callback or called run finds it. Only when the engine adopts a prepared wait
    (`spec['wait_id']`); None otherwise or when it could not be created
    (the engine then creates it when the node returns)."""
    from app.iris_engine.ai_workflows import engine
    if not getattr(engine, 'AI_WORKFLOWS_ENGINE_ADOPTS_PREPARED_WAITS', False):
        return None
    try:
        return ai_workflows_runtime_db_create_wait(
            ctx.run.id, ctx.node.get('id') if isinstance(ctx.node, dict) else None, kind, wait_uuid,
            token_hash, ai_workflows_db_utcnow() + datetime.timedelta(minutes=minutes))
    except Exception as e:
        current_app.logger.warning(f'AI workflow run {ctx.run.uuid}: could not prepare the {kind} wait '
                                   f'({e.__class__.__name__})')
        return None


def _cancel_prepared_wait(ctx, wait_id):
    if wait_id is None:
        return
    try:
        ai_workflows_runtime_db_cancel_wait(wait_id)
    except Exception as e:
        current_app.logger.warning(f'AI workflow run {ctx.run.uuid}: could not cancel the wait '
                                   f'#{wait_id} ({e.__class__.__name__})')


def _oauth_cache_get(key):
    with _oauth_tokens_lock:
        entry = _oauth_tokens.get(key)
        if entry is None:
            return None
        token, expires_at = entry
        if expires_at <= time.monotonic():
            del _oauth_tokens[key]
            return None
        return token


def _oauth_cache_put(key, token, expires_in):
    lifetime = expires_in - _OAUTH_EXPIRY_MARGIN_SECONDS
    if lifetime <= 0:
        return
    now = time.monotonic()
    with _oauth_tokens_lock:
        if len(_oauth_tokens) >= _OAUTH_CACHE_MAX:
            for stale in [k for k, (_, expires_at) in _oauth_tokens.items() if expires_at <= now]:
                del _oauth_tokens[stale]
            while len(_oauth_tokens) >= _OAUTH_CACHE_MAX:
                # Oldest first
                del _oauth_tokens[next(iter(_oauth_tokens))]
        _oauth_tokens[key] = (token, now + lifetime)


def _oauth_cache_drop(key):
    with _oauth_tokens_lock:
        _oauth_tokens.pop(key, None)


def _oauth_request(ctx, config, template) -> dict:
    """The rendered and checked token request of an
    `oauth2_client_credentials` HTTP request: `{request, log, key}`."""
    from app.iris_engine.ai_workflows.keystore import KeystoreError

    try:
        base_template, rest_template = ai_workflows_context_split_url(config.get('oauth_token_url'))
        base = ai_workflows_context_render_url_base(base_template, template)
        rendered = webhooks_render_request({'method': 'POST', 'url': rest_template, 'body_mode': 'none'}, template,
                                           user_agent='IRIS-AI-Workflows', quote_url_values=True,
                                           max_output=MAX_RENDERED_CHARS)
        values = {name: ai_workflows_context_render(config.get(name) or '', template, name)
                  for name in ('oauth_client_id', 'oauth_client_secret', 'oauth_scope')}
    except AiWorkflowTemplateError as e:
        raise AiWorkflowNodeError(ctx.mask(f'OAuth token request could not be rendered — '
                                           f'{e.field or "oauth_token_url"}: {e.message}'))
    if rendered['errors']:
        details = '; '.join(f'oauth_token_url: {e["message"]}' for e in rendered['errors'])
        raise AiWorkflowNodeError(ctx.mask(f'OAuth token request could not be rendered — {details}'))
    url = f'{base}{rendered["url"]}'
    log_url = f'{base}{rendered["log_url"]}'
    if urlsplit(url).scheme.lower() != 'https':
        # The client secret travels in the request
        raise AiWorkflowNodeError(ctx.mask(f'The OAuth token URL must be https: {log_url}'))
    try:
        ai_workflows_context_check_url_origin(base, url)
    except AiWorkflowTemplateError as e:
        raise AiWorkflowNodeError(ctx.mask(f'Invalid OAuth token URL: {e.message}'))
    _refuse_own_endpoints(url)
    try:
        ctx.resolver.check_url(url)
    except KeystoreError as e:
        raise AiWorkflowNodeError(ctx.mask(str(e)))
    client_id = values['oauth_client_id'].strip()
    client_secret = values['oauth_client_secret']
    scope = values['oauth_scope'].strip()
    if not client_id or not client_secret:
        raise AiWorkflowNodeError('The OAuth client ID and client secret must not render empty')

    client_auth = config.get('oauth_client_auth') or 'body'
    form = {'grant_type': 'client_credentials'}
    if scope:
        form['scope'] = scope
    headers = {
        'User-Agent': 'IRIS-AI-Workflows',
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    if client_auth == 'basic':
        # RFC 6749 §2.3.1: form-encoded before the Basic encoding
        raw = f'{quote(client_id, safe="")}:{quote(client_secret, safe="")}'
        headers['Authorization'] = f'Basic {base64.b64encode(raw.encode("utf-8")).decode("ascii")}'
    else:
        form['client_id'] = client_id
        form['client_secret'] = client_secret
    key = hashlib.sha256(json.dumps([url, client_id, client_secret, scope, client_auth]).encode('utf-8')).hexdigest()
    return {
        'request': {
            'method': 'POST',
            'url': url,
            'headers': headers,
            'body': urlencode(form),
            'log_url': log_url,
            'log_headers': {name: MASK if name == 'Authorization' else value for name, value in headers.items()},
        },
        'log': {'token_url': log_url, 'client_id': client_id, 'scope': scope, 'client_auth': client_auth},
        'key': key,
    }


def _oauth_error(ctx, oauth, detail):
    detail = str(detail)
    if len(detail) > _OAUTH_MAX_ERROR:
        detail = f'{detail[:_OAUTH_MAX_ERROR]}…'
    return AiWorkflowNodeError(ctx.mask(f'OAuth token request to {oauth["log"]["token_url"]} failed: {detail}'))


def _oauth_token(ctx, config, oauth) -> str:
    """A bearer token from the cache or the token endpoint. Never
    persisted: masked from every output of the run."""
    token = _oauth_cache_get(oauth['key'])
    if token is None:
        use_proxy = bool(config.get('use_proxy', True))
        sent = webhooks_send(
            oauth['request'],
            verify_tls=bool(config.get('verify_tls', True)),
            timeout=_int(config.get('timeout_seconds'), 15, 1, _MAX_HTTP_TIMEOUT),
            follow_redirects=False,
            proxies=webhooks_db_proxies() if use_proxy else None,
            use_proxy=use_proxy,
            allow_private=bool(current_app.config.get('AI_WORKFLOWS_ALLOW_PRIVATE_EGRESS', False)),
            max_response_chars=_MAX_TEXT_BODY,
        )
        try:
            data = json.loads(sent.get('response_body') or '')
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}
        if not sent.get('success'):
            detail = sent.get('error') or 'no response'
            if data.get('error'):
                detail = f'{detail} — {data.get("error")}: {data.get("error_description") or ""}'.rstrip(': ')
            raise _oauth_error(ctx, oauth, detail)
        token = data.get('access_token')
        if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
            raise _oauth_error(ctx, oauth, 'the response holds no usable access_token')
        token_type = data.get('token_type')
        if token_type and str(token_type).lower() != 'bearer':
            raise _oauth_error(ctx, oauth, f'unsupported token type {str(token_type)[:32]}')
        try:
            expires_in = int(data.get('expires_in', _OAUTH_DEFAULT_EXPIRES_IN))
        except (TypeError, ValueError):
            expires_in = _OAUTH_DEFAULT_EXPIRES_IN
        _oauth_cache_put(oauth['key'], token, min(expires_in, 24 * 3600))
    ctx.resolver.add_ephemeral(token)
    return token


def _http_request(ctx, config):
    from app.iris_engine.ai_workflows.engine import ai_workflows_engine_callback_url
    from app.iris_engine.ai_workflows.engine import ai_workflows_engine_hash_token
    from app.iris_engine.ai_workflows.engine import ai_workflows_engine_new_token
    from app.iris_engine.ai_workflows.keystore import KeystoreError

    run = ctx.run
    is_async = config.get('mode') == 'async' and not run.is_dry_run
    extra = {}
    wait_uuid = None
    token = None
    if is_async:
        wait_uuid = uuid.uuid4()
        token = ai_workflows_engine_new_token()
        extra['callback'] = {'url': ai_workflows_engine_callback_url(str(wait_uuid)), 'token': token}
    # The one place `key()` gives the plain value
    template = ctx.template_context(extra=extra, reveal_keys=True)
    template['event'] = template.get('trigger', {}).get('hook') or 'ai_workflow'
    template['payload'] = {
        'run': template['run'],
        'trigger': {k: v for k, v in (template.get('trigger') or {}).items() if k != 'payload'},
        'entity': template.get('entity'),
        'vars': template.get('vars'),
        'callback': extra.get('callback'),
    }
    oauth = None
    if config.get('auth_type') == AUTH_OAUTH2_CLIENT_CREDENTIALS:
        # The client credentials only go to the token endpoint: their
        # host restrictions bind it, not the request nor the next steps
        with ctx.resolver.confined():
            oauth = _oauth_request(ctx, config, template)
        config = {**config, 'auth_type': 'none'}
    try:
        # The scheme and host render unquoted (literal or `key()` only),
        # the path and query with their interpolated values percent-encoded
        base_template, rest_template = ai_workflows_context_split_url(config.get('url'))
        base = ai_workflows_context_render_url_base(base_template, template)
    except AiWorkflowTemplateError as e:
        raise AiWorkflowNodeError(ctx.mask(f'Invalid request URL: {e.message}'))
    try:
        request = webhooks_render_request(_http_config({**config, 'url': rest_template}, template), template,
                                          user_agent='IRIS-AI-Workflows', quote_url_values=True,
                                          max_output=MAX_RENDERED_CHARS)
    except AiWorkflowTemplateError as e:
        raise AiWorkflowNodeError(ctx.mask(f'Request could not be rendered — {e.field}: {e.message}'))
    request['url'] = f'{base}{request["url"]}'
    request['log_url'] = f'{base}{request["log_url"]}'
    if oauth is not None:
        # The token replaces any Authorization header; set once it is known
        for headers in (request['headers'], request['log_headers']):
            for name in [name for name in headers if name.lower() == 'authorization']:
                del headers[name]
        request['log_headers']['Authorization'] = MASK
    logged = ctx.mask(_scrub_token({'method': request['method'], 'url': request['log_url'],
                                    'headers': request['log_headers']}, token))
    if oauth is not None:
        logged['oauth'] = ctx.mask(oauth['log'])
    ctx.input = logged
    if request['errors']:
        details = '; '.join(f'{e["field"]}: {e["message"]}' for e in request['errors'])
        raise AiWorkflowNodeError(ctx.mask(_scrub_token(f'Request could not be rendered — {details}', token)))
    if not request['url']:
        raise AiWorkflowNodeError('The request URL is empty')
    try:
        # Data rendered into the path or query cannot change where it goes
        ai_workflows_context_check_url_origin(base, request['url'])
    except AiWorkflowTemplateError as e:
        raise AiWorkflowNodeError(ctx.mask(f'Invalid request URL: {e.message}'))
    _refuse_own_endpoints(request['url'])
    try:
        ctx.resolver.check_url(request['url'])
    except KeystoreError as e:
        raise AiWorkflowNodeError(ctx.mask(str(e)))

    if run.is_dry_run:
        # A dry run never reaches the remote system
        preview = ctx.mask(_scrub_token({**logged, 'body': _dry_run_body(request.get('body'))}, token))
        return NodeResult(output={'dry_run': True, 'request': preview}, input=logged)

    if oauth is not None:
        request['headers']['Authorization'] = f'Bearer {_oauth_token(ctx, config, oauth)}'

    minutes = _int(config.get('wait_timeout_minutes'), 60, 1, _MAX_WAIT_MINUTES)
    token_hash = ai_workflows_engine_hash_token(token) if is_async else None
    wait_id = _prepare_wait(ctx, wait_uuid, token_hash, minutes) if is_async else None

    use_proxy = bool(config.get('use_proxy', True))
    try:
        sent = webhooks_send(
            request,
            verify_tls=bool(config.get('verify_tls', True)),
            timeout=_int(config.get('timeout_seconds'), 15, 1, _MAX_HTTP_TIMEOUT),
            # A redirect could carry keystore values to a host they are not allowed on
            follow_redirects=False,
            proxies=webhooks_db_proxies() if use_proxy else None,
            use_proxy=use_proxy,
            allow_private=bool(current_app.config.get('AI_WORKFLOWS_ALLOW_PRIVATE_EGRESS', False)),
            max_response_chars=_MAX_HTTP_RESPONSE_CHARS,
        )
        body = _parse_body(sent.get('response_body'), config.get('response_format') or 'json')
        # The remote system may echo the callback token back
        body = _scrub_token(body, token)
        output = ctx.mask(_scrub_token({
            'status_code': sent.get('status_code'),
            'headers': sent.get('response_headers') or {},
            'body': body,
        }, token))
    except Exception:
        _cancel_prepared_wait(ctx, wait_id)
        raise
    if oauth is not None and sent.get('status_code') == 401:
        # Revoked or no longer valid: the next request fetches a new one
        _oauth_cache_drop(oauth['key'])
    if not sent.get('success'):
        _cancel_prepared_wait(ctx, wait_id)
        error = str(sent.get('error') or '')
        output['error'] = ctx.mask(_scrub_token(sent.get('error'), token))
        port = PORT_TIMEOUT if error.startswith('Timed out') else PORT_ERROR
        return NodeResult(output=output, port=port, input=logged)
    if not is_async:
        return NodeResult(output=output, input=logged)
    wait = {
        'kind': WAIT_CALLBACK,
        'uuid': wait_uuid,
        'token_hash': token_hash,
        'minutes': minutes,
    }
    if wait_id is not None:
        wait['wait_id'] = wait_id
    return NodeResult(output={'status_code': output.get('status_code'), 'body': output.get('body')}, input=logged,
                      wait=wait)


def _form_fields(ctx, config) -> list:
    fields = config.get('fields') or []
    if config.get('fields_from'):
        found = ai_workflows_context_get_path(ctx.template_context(), config.get('fields_from'))
        if isinstance(found, dict):
            found = found.get('fields')
        if isinstance(found, list):
            fields = found
    clean = []
    for item in fields:
        if isinstance(item, str) and item.strip():
            # `fields_from` may resolve to a plain list of field names
            item = {'name': item.strip(), 'type': 'text'}
        if not isinstance(item, dict) or not item.get('name'):
            continue
        entry = {
            'name': str(item.get('name'))[:64],
            'label': str(item.get('label') or item.get('name'))[:255],
            'type': item.get('type') if item.get('type') in FORM_FIELD_TYPES else 'text',
            'required': bool(item.get('required')),
        }
        if isinstance(item.get('options'), list):
            entry['options'] = item['options'][:100]
        if item.get('help'):
            entry['help'] = str(item['help'])[:1000]
        clean.append(entry)
    return clean[:30]


def _ask_analyst(ctx, config):
    title = ctx.mask(ctx.render(config.get('title') or '', 'title')).strip() or 'Information requested'
    question = ctx.mask(ctx.render(config.get('question') or '', 'question')).strip()
    fields = {
        'kind': SUGGESTION_INFO_REQUEST,
        'title': title,
        'body': question or None,
        # `popup`: the UI opens the form in a dialog for its audience,
        # wherever they are in the app
        'form_schema': {'fields': _form_fields(ctx, config), 'popup': config.get('popup', True) is not False},
    }
    minutes = _int(config.get('timeout_minutes'), 1440, 1, _MAX_WAIT_MINUTES)
    if ctx.run.is_dry_run:
        # Nobody answers a dry run: record the question and move on
        suggestion = ai_workflows_suggestions_create(ctx.run, ctx.step, **fields)
        return NodeResult(output={'answer': None, 'answered_by': None, 'dry_run': True,
                                  'suggestion_id': suggestion.id}, port=PORT_TIMEOUT)
    users, note = _addressed_audience(ctx, config, 'question')
    if users:
        fields['audience_user_ids'] = users
    return NodeResult(output={'note': note} if note else {},
                      wait={'kind': WAIT_USER_INPUT, 'minutes': minutes, 'suggestion': fields})


def _addressed_audience(ctx, config, subject) -> tuple:
    """(users, note): who a question or suggestion goes to — None for the
    workflow's suggestion audience — and why that is not the configured
    audience, if so. `triggerer` is the user who started the run or whose
    action fired the event. When nobody configured can be reached it goes
    to the workflow's suggestion audience rather than to no one."""
    run = ctx.run
    audience = config.get('audience') or AUDIENCE_ENTITY
    if audience == AUDIENCE_OWNER:
        candidates = [ctx.snapshot.get('owner_id')]
    elif audience == 'triggerer':
        candidates = [run.triggered_by_user_id, _event_actor_id(run)]
    elif audience == 'users':
        candidates = [u for u in config.get('user_ids') or [] if isinstance(u, int) and not isinstance(u, bool)]
    else:
        return None, None
    users = []
    for user_id in candidates:
        if user_id and user_id not in users and _notify_reachable(run, user_id):
            users.append(user_id)
    if users:
        return users, None
    return None, f'None of the users of the audience is active and can access the entity: the {subject} went ' \
                 'to the workflow suggestion audience instead'


def _find_related(ctx, config):
    run = ctx.run
    source = config.get('source') or 'alert'
    if source == 'search':
        value = ctx.render(config.get('search_value') or '', 'search_value').strip()
        if not value:
            raise AiWorkflowNodeError('The search value is empty')
        from app.business.search import SUPPORTED_SEARCH_TYPES
        tool = 'iris_search'
        arguments = {'value': value, 'types': list(SUPPORTED_SEARCH_TYPES)}
    else:
        if run.entity_type != ENTITY_ALERT or run.entity_id is None:
            raise AiWorkflowNodeError('Finding related alerts needs a run on an alert')
        tool = 'iris_alerts_related_get'
        arguments = {
            'alert_identifier': int(run.entity_id),
            'open_alerts': bool(config.get('open_alerts', True)),
            'closed_alerts': bool(config.get('closed_alerts', False)),
            'open_cases': bool(config.get('open_cases', True)),
            'closed_cases': bool(config.get('closed_cases', False)),
            'days_back': _int(config.get('days_back'), 30, 1, 365),
            'number_of_nodes': _int(config.get('number_of_nodes'), 100, 1, 1000),
        }
    # Searches stay within the customers of the run
    arguments = ai_workflows_agent_pin_arguments(run, tool, arguments)
    result = ai_workflows_tools_execute(run.run_as_user_id, tool, arguments, run=run, step=ctx.step,
                                        execution_mode=EXEC_AUTO_READ, mask=ctx.mask)
    if not result['ok']:
        raise AiWorkflowNodeError(result['error'])
    return NodeResult(output={'source': source, 'result': result['result']}, input=arguments)


def _parse_iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _find_war_room_tasks(ctx, config):
    run = ctx.run
    if run.entity_type != ENTITY_WAR_ROOM or run.entity_id is None:
        raise AiWorkflowNodeError('Finding war room tasks needs a run on a war room')
    since = None
    if (config.get('since') or 'last_run') == 'last_run' and run.workflow_id is not None:
        previous = ai_workflows_db_last_finished_run(run.workflow_id, run.entity_type, run.entity_id,
                                                     exclude_run_id=run.id)
        since = previous.started_at if previous is not None else None
    if since is None:
        since = ai_workflows_db_utcnow() - datetime.timedelta(minutes=_int(config.get('minutes'), 60, 1, 525600))
    result = ai_workflows_tools_execute(run.run_as_user_id, 'iris_war_room_tasks_list',
                                        {'war_room_id': int(run.entity_id)}, run=run, step=ctx.step,
                                        execution_mode=EXEC_AUTO_READ, mask=ctx.mask)
    if not result['ok']:
        raise AiWorkflowNodeError(result['error'])
    tasks = (result['result'] or {}).get('tasks') if isinstance(result['result'], dict) else None
    recent = []
    for task in tasks or []:
        created = _parse_iso(task.get('created_at')) if isinstance(task, dict) else None
        if created is not None and created >= since:
            recent.append(task)
    return NodeResult(output={'tasks': recent, 'count': len(recent), 'since': since.isoformat()},
                      port=PORT_FOUND if recent else PORT_NONE)


def _proposed_action(ctx, action, template):
    if not isinstance(action, dict) or not action.get('tool'):
        return None
    tool = action.get('tool')
    classification = ai_workflows_tools_classification(tool)
    if not classification:
        raise AiWorkflowNodeError(f'Unknown tool {tool}')
    if classification != CLASSIFICATION_WRITE:
        raise AiWorkflowNodeError(f'A proposed action must be a write tool; {tool} is not')
    arguments = ai_workflows_context_render_arguments(action.get('arguments') or {}, template, 'proposed_action')
    return ctx.mask({'tool': tool, 'arguments': ai_workflows_agent_pin_arguments(ctx.run, tool, arguments)})


def _proposed_actions(ctx, actions, template):
    """The actions of a bundled suggestion whose `when` holds (no `when`:
    always), each `{label, tool, arguments}`."""
    bundle = []
    for action in actions or []:
        if not isinstance(action, dict) or not action.get('tool'):
            continue
        when = str(action.get('when') or '').strip()
        if when and not ai_workflows_context_eval_expression(when, template):
            continue
        proposed = _proposed_action(ctx, action, template)
        label = ai_workflows_context_render(action.get('label') or '', template, 'actions.label')
        bundle.append({**proposed, 'label': ctx.mask(label).strip()})
    return bundle


def _suggest(ctx, config):
    template = ctx.template_context()
    fields = {
        'kind': config.get('kind') or SUGGESTION_GENERIC_ACTION,
        'title': ctx.mask(ai_workflows_context_render(config.get('title') or '', template, 'title')).strip(),
        'body': ctx.mask(ai_workflows_context_render(config.get('body') or '', template, 'body')).strip() or None,
        'confidence': config.get('confidence'),
        'severity': config.get('severity'),
    }
    actions = _proposed_actions(ctx, config.get('actions'), template) if config.get('actions') else None
    if actions is None:
        fields['proposed_action'] = _proposed_action(ctx, config.get('proposed_action'), template)
    else:
        fields['proposed_actions'] = actions
    # A dry run only ever goes to whoever started it
    users, note = (None, None) if ctx.run.is_dry_run else _addressed_audience(ctx, config, 'suggestion')
    if users:
        fields['audience_user_ids'] = users
    suggestion = ai_workflows_suggestions_create(ctx.run, ctx.step, **fields)
    # Who it was addressed to, so the run shows where it went
    output = {'suggestion_id': suggestion.id, 'audience': list(getattr(suggestion, 'audience_user_ids', None) or [])}
    if actions is not None:
        output['actions'] = [action['label'] or action['tool'] for action in actions]
    if note:
        output['note'] = note
    return NodeResult(output=output)


def _action(ctx, config):
    run = ctx.run
    tool = config.get('tool')
    classification = ai_workflows_tools_classification(tool)
    if classification is None:
        raise AiWorkflowNodeError(f'Unknown or unclassified tool {tool}')
    arguments = ai_workflows_context_render_arguments(config.get('arguments') or {}, ctx.template_context(),
                                                      'arguments')
    arguments = ai_workflows_agent_pin_arguments(run, tool, arguments)
    ctx.input = {'tool': tool, 'arguments': arguments}
    if classification == CLASSIFICATION_WRITE and (run.is_dry_run or tool not in ctx.allowlist):
        # Dry run, or allowlist changed since the graph was validated: ask
        suggestion = ai_workflows_suggestions_create(
            run, ctx.step, kind=SUGGESTION_GENERIC_ACTION,
            title=f'{"[dry run] " if run.is_dry_run else ""}{tool}',
            proposed_action=ctx.mask({'tool': tool, 'arguments': arguments}),
        )
        row = ai_workflows_tools_record(tool, arguments, classification=CLASSIFICATION_WRITE,
                                        execution_mode=EXEC_SUGGESTED, acting_user_id=run.run_as_user_id,
                                        run=run, step=ctx.step, suggestion=suggestion, mask=ctx.mask)
        return NodeResult(output={'ok': True, 'executed': False, 'suggestion_id': suggestion.id,
                                  'tool_call_id': row.id, 'result': None}, input={'tool': tool, 'arguments': arguments})
    mode = EXEC_AUTO_READ if classification == CLASSIFICATION_READ else EXEC_ALLOWLISTED_WRITE
    result = ai_workflows_tools_execute(run.run_as_user_id, tool, arguments, run=run, step=ctx.step,
                                        execution_mode=mode, mask=ctx.mask)
    if not result['ok']:
        raise AiWorkflowNodeError(result['error'])
    return NodeResult(output={'ok': True, 'executed': True, 'result': result['result'],
                              'tool_call_id': result['tool_call_id']},
                      input={'tool': tool, 'arguments': arguments})


def _event_actor_id(run):
    """The user whose action fired an event run (the IOC creator, …)."""
    payload = run.trigger_payload if isinstance(run.trigger_payload, dict) else {}
    actor = payload.get('actor') if isinstance(payload.get('actor'), dict) else {}
    actor_id = actor.get('id')
    return actor_id if isinstance(actor_id, int) and not isinstance(actor_id, bool) else None


# At most this many users read from `users_from`
_MAX_USERS_FROM = 50

# Entities a notification can be about, and the argument the run scope
# checks their identifier as
_NOTIFY_ABOUT = {ENTITY_CASE: 'case_identifier', ENTITY_ALERT: 'alert_identifier', ENTITY_WAR_ROOM: 'war_room_id'}


def _notify_target(ctx, config) -> tuple:
    """(entity_type, entity_id) the notification is about: `about`, an
    entity in the scope of the run (a case or war room the workflow
    created, …), or else the run's entity."""
    about = config.get('about')
    if not isinstance(about, dict) or not about.get('entity_type'):
        return ctx.run.entity_type, ctx.run.entity_id
    entity_type = about.get('entity_type')
    if entity_type not in _NOTIFY_ABOUT:
        raise AiWorkflowNodeError(f'A notification cannot be about a {entity_type}')
    value = ai_workflows_context_render_arguments(about.get('entity_id'), ctx.template_context(), 'about.entity_id')
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AiWorkflowNodeError(f'about.entity_id is not the identifier of a {entity_type}: {value!r}')
    ai_workflows_agent_check_identifier(ctx.run, _NOTIFY_ABOUT[entity_type], value)
    return entity_type, value


def _notify_audience(ctx, config, target) -> tuple:
    """(users, note): the active users of the audience who can read the
    entity the notification is about (`target`), and why the list is not
    the configured one, if so. `entity` is the entity's owner (a war
    room: its members) and the user who triggered the run, or whose
    action fired the event; when none of them can be reached, the
    workflow owner is told instead. `triggerer` is only that user."""
    run = ctx.run
    owner_id = ctx.snapshot.get('owner_id')
    audience = config.get('audience') or AUDIENCE_ENTITY
    entity_type, entity_id = target
    unknown = []
    if audience == AUDIENCE_OWNER:
        candidates = [owner_id]
    elif audience == 'triggerer':
        candidates = [run.triggered_by_user_id, _event_actor_id(run)]
    elif audience == 'users':
        candidates = [u for u in config.get('user_ids') or [] if isinstance(u, int) and not isinstance(u, bool)]
        found, unknown = _notify_users_from(ctx, config)
        candidates += found
    elif entity_type:
        candidates = list(ai_workflows_db_entity_owner_ids(entity_type, entity_id)) \
            + [run.triggered_by_user_id, _event_actor_id(run)]
    else:
        candidates = [owner_id]
    users = []
    for user_id in candidates:
        if user_id and user_id not in users and _notify_reachable(run, user_id, target):
            users.append(user_id)
    if audience == 'users' and unknown:
        return users, f'No user is named {", ".join(unknown[:10])}'
    if users or not candidates:
        return users, None if users else 'No user to notify'
    if audience == AUDIENCE_ENTITY and owner_id and _notify_reachable(run, owner_id, target):
        return [owner_id], 'Nobody on the entity could be reached: the workflow owner was notified instead'
    return [], 'None of the users of the audience is active and can access the entity'


def _notify_users_from(ctx, config) -> tuple:
    """(user ids, names that are no user) from `users_from`: a template
    or a `$path` giving a user id, login or email, a comma-separated list
    of them, a list of them, or of objects with a `user_id` or an `id`
    (a user returned by a tool)."""
    source = config.get('users_from')
    if source in (None, ''):
        return [], []
    value = ai_workflows_context_render_arguments(source, ctx.template_context(), 'users_from')
    ids = []
    names = []
    for item in (value if isinstance(value, list) else [value])[:_MAX_USERS_FROM]:
        if isinstance(item, dict):
            item = item.get('user_id', item.get('id'))
        if isinstance(item, int) and not isinstance(item, bool):
            ids.append(item)
        elif isinstance(item, str):
            for part in (p.strip() for p in item.split(',')):
                if part.isdigit():
                    ids.append(int(part))
                elif part:
                    names.append(part)
    found = ai_workflows_db_user_ids_by_names(names[:_MAX_USERS_FROM])
    return ids + [found[n] for n in names if n in found], [n for n in names if n not in found]


def _notify_reachable(run, user_id, target=None) -> bool:
    """An active user who can read `target` (the run's entity by
    default). Without an entity there is nothing to check access
    against: only active accounts."""
    entity_type, entity_id = target if target is not None else (run.entity_type, run.entity_id)
    if not entity_type or entity_id is None:
        user = ai_workflows_db_get_user(user_id)
        return bool(user is not None and user.active)
    return ai_workflows_entities_user_can_access(user_id, entity_type, entity_id)


def _notify(ctx, config):
    from app.iris_engine.ai_workflows.suggestions import _entity_link
    from app.iris_engine.notifications.service import notify_many
    from app.iris_engine.notifications.service import toast_many

    run = ctx.run
    title = ai_workflows_suggestions_neutralise_links(
        ctx.mask(ctx.render(config.get('title') or '', 'title')).strip() or run.workflow_name)
    body = ai_workflows_suggestions_neutralise_links(ctx.mask(ctx.render(config.get('body') or '', 'body')).strip()
                                                     or None)
    target = _notify_target(ctx, config)
    users, note = _notify_audience(ctx, config, target)
    about = {'about': {'entity_type': target[0], 'entity_id': target[1]}} \
        if target != (run.entity_type, run.entity_id) else {}
    delivery = config.get('delivery') if config.get('delivery') in NOTIFY_DELIVERIES else NOTIFY_NOTIFICATION
    severity = config.get('severity') if config.get('severity') in NOTIFICATION_SEVERITIES else None
    shown = {'delivery': delivery, 'severity': severity}
    if run.is_dry_run:
        return NodeResult(output={'notified': [], 'would_notify': users, 'title': title, 'body': body, **shown,
                                  **about, **({'note': note} if note else {})})
    if users:
        link = _entity_link(*target)
        if delivery != NOTIFY_TOAST:
            notify_many(users, 'ai_suggestion', title[:250], body=body[:2000] if body else None, link=link,
                        source_type='ai_workflow_run', source_id=run.id, severity=severity)
        if delivery != NOTIFY_NOTIFICATION:
            toast_many(users, title[:250], body=body[:2000] if body else None, link=link, severity=severity,
                       timeout_seconds=_int(config.get('timeout_seconds'), 5, 0, MAX_TOAST_SECONDS))
    return NodeResult(output={'notified': users, **shown, **about, **({'note': note} if note else {})})


def _delay(_ctx, config):
    minutes = _int(config.get('minutes'), 5, 0, _MAX_WAIT_MINUTES)
    if minutes == 0:
        return NodeResult(output={})
    return NodeResult(output={}, wait={'kind': WAIT_DELAY, 'minutes': minutes})


def _set_variables(ctx, config):
    template = ctx.template_context()
    variables = dict((ctx.run.context or {}).get('vars') or {})
    # The templates; the output holds what they rendered to
    ctx.input = {str(item['name']): item.get('value') for item in config.get('variables') or []
                 if isinstance(item, dict) and item.get('name')}
    assigned = {}
    for index, item in enumerate(config.get('variables') or []):
        if not isinstance(item, dict) or not item.get('name'):
            continue
        value = ai_workflows_context_render_arguments(item.get('value'), template, f'variables.{index}')
        assigned[str(item['name'])] = ctx.mask(value)
        template['vars'] = {**variables, **assigned}
    variables.update(assigned)
    return NodeResult(output=assigned, input=ctx.input, context_updates={'vars': variables})


def _python(ctx, config):
    if not current_app.config.get('AI_WORKFLOWS_PYTHON_ENABLED', True):
        raise AiWorkflowNodeError('Python transforms are disabled on this instance (AI_WORKFLOWS_PYTHON_ENABLED)')
    template = ctx.template_context()
    inputs = {}
    for index, item in enumerate(config.get('inputs') or []):
        if isinstance(item, dict) and item.get('name'):
            inputs[str(item['name'])] = ai_workflows_context_render_arguments(item.get('value'), template,
                                                                                f'inputs.{index}')
    ctx.input = {'inputs': inputs}
    # Plain data only: the keystore never reaches a script
    variables = ctx.mask({
        'inputs': inputs,
        'trigger': template.get('trigger'),
        'entity': template.get('entity'),
        'nodes': template.get('nodes'),
        'vars': template.get('vars'),
        'run': template.get('run'),
    })
    outcome = ai_workflows_sandbox_execute(
        config.get('code') or '', variables,
        max_steps=_int(config.get('max_steps'), DEFAULT_MAX_STEPS, 1000, MAX_STEPS),
        timeout_seconds=_int(config.get('timeout_seconds'), DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS),
    )
    logs = ctx.mask(outcome.get('logs') or [])
    if not outcome.get('ok'):
        line = outcome.get('line')
        where = f' (line {line})' if line else ''
        raise AiWorkflowNodeError(ctx.mask(f'Script {outcome.get("kind") or "error"}{where}: {outcome.get("error")}')
                                  + (f' — logs: {" | ".join(logs)[:1000]}' if logs else ''))
    return NodeResult(output={'result': ctx.mask(outcome.get('result')), 'logs': logs,
                              'steps': outcome.get('steps')},
                      input=ctx.input)


def _stop(ctx, config):
    status = RUN_FAILED if config.get('status') == RUN_FAILED else RUN_SUCCEEDED
    reason = ctx.mask(ctx.render(config.get('reason') or '', 'reason')).strip() or None
    return NodeResult(output={'status': status, 'reason': reason}, port=None, stop_status=status,
                      stop_reason=reason)


def _run_workflow(ctx, config):
    from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_start_from_workflow

    run = ctx.run
    workflow_id = config.get('workflow_id')
    if workflow_id == RUN_WORKFLOW_SELF:
        workflow_id = run.workflow_id
    template = ctx.template_context()
    inputs = {}
    for index, item in enumerate(config.get('inputs') or []):
        if isinstance(item, dict) and item.get('name'):
            inputs[str(item['name'])] = ai_workflows_context_render_arguments(item.get('value'), template,
                                                                                f'inputs.{index}')
    inputs = ctx.mask(inputs)
    waits = config.get('mode', 'wait') != 'async'
    called = {'workflow_id': workflow_id, 'mode': 'wait' if waits else 'async', 'inputs': inputs}
    if run.is_dry_run:
        # A dry run starts nothing
        return NodeResult(output={'dry_run': True, **called}, input=called)

    minutes = _int(config.get('timeout_minutes'), 60, 1, _MAX_WAIT_MINUTES)
    wait_uuid = uuid.uuid4() if waits else None
    wait_id = None
    if waits:
        # The called run may end before this node returns
        wait_id = _prepare_wait(ctx, wait_uuid, None, minutes, kind=WAIT_WORKFLOW)
        if wait_id is None:
            raise AiWorkflowNodeError('The wait for the called workflow could not be prepared')
    try:
        child, reason = ai_workflows_triggers_start_from_workflow(run, ctx.node.get('id'), workflow_id, inputs,
                                                                  wait_uuid=wait_uuid)
    except Exception:
        _cancel_prepared_wait(ctx, wait_id)
        raise
    if child is None or child.status == RUN_SKIPPED:
        _cancel_prepared_wait(ctx, wait_id)
        raise AiWorkflowNodeError(reason or (child.error if child is not None else None)
                                  or 'The workflow could not be started')
    output = {'run_uuid': str(child.uuid), 'workflow_id': child.workflow_id, 'workflow_name': child.workflow_name}
    if not waits:
        return NodeResult(output={**output, 'status': child.status}, input=called)
    return NodeResult(output=output, input=called,
                      wait={'kind': WAIT_WORKFLOW, 'uuid': wait_uuid, 'minutes': minutes, 'wait_id': wait_id})


_EXECUTORS = {
    NODE_TRIGGER: _trigger,
    NODE_AI_AGENT: _ai_agent,
    NODE_CONDITION: _condition,
    NODE_HTTP_REQUEST: _http_request,
    NODE_ASK_ANALYST: _ask_analyst,
    NODE_FIND_RELATED: _find_related,
    NODE_FIND_WAR_ROOM_TASKS: _find_war_room_tasks,
    NODE_SUGGEST: _suggest,
    NODE_ACTION: _action,
    NODE_NOTIFY: _notify,
    NODE_DELAY: _delay,
    NODE_SET_VARIABLES: _set_variables,
    NODE_PYTHON: _python,
    NODE_STOP: _stop,
    NODE_RUN_WORKFLOW: _run_workflow,
}


def ai_workflows_nodes_execute(ctx, node) -> NodeResult:
    executor = _EXECUTORS.get(node.get('type'))
    if executor is None:
        raise AiWorkflowNodeError(f'Unknown node type {node.get("type")}')
    return executor(ctx, node.get('config') or {})
