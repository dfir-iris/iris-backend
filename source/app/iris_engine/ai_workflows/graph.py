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

"""Workflow definition validation: the graph and the trigger config.

Errors are `{node_id, field, message}` dicts (`node_id` None for errors
about the graph as a whole or the trigger config) so the editor can
point at the faulty node.
"""

import json
import logging
import re

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_postload_hooks
from app.iris_engine.ai_workflows.context import RULE_OPERATORS
from app.iris_engine.ai_workflows.context import AiWorkflowTemplateError
from app.iris_engine.ai_workflows.context import ai_workflows_context_path_reference
from app.iris_engine.ai_workflows.context import ai_workflows_context_url_origin
from app.iris_engine.ai_workflows.cron import ai_workflows_cron_parse
from app.iris_engine.ai_workflows.nodes import FORM_FIELD_TYPES
from app.iris_engine.ai_workflows.nodes import NODE_ACTION
from app.iris_engine.ai_workflows.nodes import NODE_AI_AGENT
from app.iris_engine.ai_workflows.nodes import NODE_ASK_ANALYST
from app.iris_engine.ai_workflows.nodes import NODE_CONDITION
from app.iris_engine.ai_workflows.nodes import NODE_DELAY
from app.iris_engine.ai_workflows.nodes import NODE_FIND_RELATED
from app.iris_engine.ai_workflows.nodes import NODE_FIND_WAR_ROOM_TASKS
from app.iris_engine.ai_workflows.nodes import NODE_HTTP_REQUEST
from app.iris_engine.ai_workflows.nodes import NODE_NOTIFY
from app.iris_engine.ai_workflows.nodes import NODE_PORTS
from app.iris_engine.ai_workflows.nodes import NODE_PYTHON
from app.iris_engine.ai_workflows.nodes import NODE_SET_VARIABLES
from app.iris_engine.ai_workflows.nodes import NODE_STOP
from app.iris_engine.ai_workflows.nodes import NODE_SUGGEST
from app.iris_engine.ai_workflows.nodes import NODE_TRIGGER
from app.iris_engine.ai_workflows.nodes import ai_workflows_nodes_is_waiting
from app.iris_engine.ai_workflows.sandbox import MAX_STEPS
from app.iris_engine.ai_workflows.sandbox import MAX_TIMEOUT_SECONDS
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_check
from app.iris_engine.ai_workflows.suggestions import SEVERITIES
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_WRITE
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_classification
from app.iris_engine.webhooks.render import WebhookRenderError
from app.iris_engine.webhooks.render import webhooks_check_condition
from app.iris_engine.webhooks.render import webhooks_check_template
from app.models.ai_workflows import AUDIENCE_ENTITY
from app.models.ai_workflows import AUDIENCE_OWNER
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import RUN_FAILED
from app.models.ai_workflows import RUN_SUCCEEDED
from app.models.ai_workflows import SUGGESTION_KINDS
from app.models.ai_workflows import TRIGGER_CRON
from app.models.ai_workflows import TRIGGER_EVENT
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.ai_workflows import TRIGGER_TYPES
from app.models.ai_workflows import TRIGGER_WEBHOOK

logger = logging.getLogger(__name__)

CRON_TARGETS = ('none', 'war_rooms', 'cases')
HTTP_METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')

_MAX_NODES = 200
_MAX_BLOCK_NODES = 50
_MAX_EDGES = 1000
_MAX_DEFINITION_BYTES = 1024 * 1024
_MAX_CONFIG_STRING = 64 * 1024
_MAX_PROMPT = 32 * 1024
_MAX_LABEL = 255
# Editor only: the sides of a node its connectors are drawn on
_HANDLE_SIDES = ('left', 'top', 'right', 'bottom')
_MAX_HOOKS = 200
_PROMPT_FIELDS = ('prompt', 'system_prompt')
_MAX_DEDUP_MINUTES = 7 * 24 * 60
_MAX_CRON_TARGETS = 500
_NODE_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
# Names an object key of the editor (JavaScript) cannot hold safely
_RESERVED_NODE_IDS = ('__proto__', 'constructor', 'prototype')
_VARIABLE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_CONTEXT_PATH = re.compile(r'^[A-Za-z0-9_\-]{1,128}(?:\.[A-Za-z0-9_\-]{1,128}){0,31}$')


def _error(node_id, field, message) -> dict:
    return {'node_id': node_id, 'field': field, 'message': message}


def ai_workflows_graph_edge_port(edge) -> str:
    """The source port of an edge (`source_port`, or xyflow's
    `sourceHandle`); 'out' when unset."""
    return edge.get('source_port') or edge.get('sourceHandle') or 'out'


def ai_workflows_graph_nodes(graph) -> dict:
    """`{node_id: node}` of a (validated) graph."""
    nodes = (graph or {}).get('nodes') or []
    return {n['id']: n for n in nodes if isinstance(n, dict) and isinstance(n.get('id'), str)}


def ai_workflows_graph_trigger_id(graph):
    for node in (graph or {}).get('nodes') or []:
        if isinstance(node, dict) and node.get('type') == NODE_TRIGGER:
            return node.get('id')
    return None


def ai_workflows_graph_targets(graph, node_id, port) -> list:
    """Node ids the edges leaving `node_id` through `port` lead to, in
    edge order."""
    targets = []
    for edge in (graph or {}).get('edges') or []:
        if not isinstance(edge, dict) or edge.get('source') != node_id:
            continue
        if ai_workflows_graph_edge_port(edge) == port and edge.get('target') not in targets:
            targets.append(edge.get('target'))
    return targets


# ---- Trigger config -----------------------------------------------------------

def _int_in(value, low, high) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _postload_hook_names():
    try:
        return {name for name, _description in ai_workflows_db_postload_hooks()}
    except Exception:
        logger.exception('Could not list the postload hooks')
        return None


def _hook_errors(hooks) -> list:
    """Hooks must be distinct known postload hooks (or `*`); when the
    known hooks cannot be listed, the hooks cannot be verified and are
    refused."""
    if len(set(hooks)) != len(hooks):
        return [_error(None, 'trigger_config.hooks', 'Each event can only be selected once')]
    if len(hooks) > _MAX_HOOKS:
        return [_error(None, 'trigger_config.hooks', f'At most {_MAX_HOOKS} events')]
    errors = []
    known = None
    for hook in hooks:
        if hook == '*':
            continue
        if not hook.startswith('on_postload_'):
            errors.append(_error(None, 'trigger_config.hooks', f'{hook[:100]} is not a postload event'))
            continue
        if known is None:
            known = _postload_hook_names()
            if known is None:
                return [_error(None, 'trigger_config.hooks', 'The events could not be verified, retry later')]
            if len(hooks) > len(known) + 1:
                return [_error(None, 'trigger_config.hooks', f'At most {len(known)} events')]
        if hook not in known:
            errors.append(_error(None, 'trigger_config.hooks', f'Unknown event {hook[:100]}'))
    return errors


def _event_config_errors(config) -> list:
    errors = []
    hooks = config.get('hooks')
    if not isinstance(hooks, list) or not hooks or not all(isinstance(h, str) and h for h in hooks):
        errors.append(_error(None, 'trigger_config.hooks', 'Select at least one event'))
    else:
        errors.extend(_hook_errors(hooks))
    condition = config.get('condition')
    if condition not in (None, ''):
        if not isinstance(condition, str):
            errors.append(_error(None, 'trigger_config.condition', 'Must be a string'))
        else:
            try:
                webhooks_check_condition(condition)
            except WebhookRenderError as e:
                errors.append(_error(None, 'trigger_config.condition', e.message))
    dedup = config.get('dedup_minutes')
    if dedup is not None and not _int_in(dedup, 0, _MAX_DEDUP_MINUTES):
        errors.append(_error(None, 'trigger_config.dedup_minutes',
                             f'Must be an integer between 0 and {_MAX_DEDUP_MINUTES}'))
    return errors


def _cron_config_errors(config) -> list:
    errors = []
    expression = config.get('cron')
    if not isinstance(expression, str) or not expression.strip():
        errors.append(_error(None, 'trigger_config.cron', 'A cron expression is required'))
    else:
        try:
            ai_workflows_cron_parse(expression)
        except ValueError as e:
            errors.append(_error(None, 'trigger_config.cron', str(e)))
    target = config.get('target')
    if target is not None and target not in CRON_TARGETS:
        errors.append(_error(None, 'trigger_config.target', f'Must be one of {", ".join(CRON_TARGETS)}'))
    max_targets = config.get('max_targets')
    if max_targets is not None and not _int_in(max_targets, 1, _MAX_CRON_TARGETS):
        errors.append(_error(None, 'trigger_config.max_targets',
                             f'Must be an integer between 1 and {_MAX_CRON_TARGETS}'))
    return errors


def _manual_config_errors(config) -> list:
    entity_types = config.get('entity_types')
    if entity_types is None:
        return []
    if not isinstance(entity_types, list) or any(t not in ENTITY_TYPES for t in entity_types):
        return [_error(None, 'trigger_config.entity_types', f'Entity types are {", ".join(ENTITY_TYPES)}')]
    return []


def _webhook_config_errors(config) -> list:
    errors = []
    if not isinstance(config.get('require_signature', False), bool):
        errors.append(_error(None, 'trigger_config.require_signature', 'Must be a boolean'))
    entity_type = config.get('entity_type')
    if entity_type not in (None, '') and entity_type not in ENTITY_TYPES:
        errors.append(_error(None, 'trigger_config.entity_type', f'Entity types are {", ".join(ENTITY_TYPES)}'))
    path = config.get('entity_id_path')
    if path not in (None, '') and not isinstance(path, str):
        errors.append(_error(None, 'trigger_config.entity_id_path', 'Must be a dotted path'))
    if path and not entity_type:
        errors.append(_error(None, 'trigger_config.entity_type', 'Required with an entity id path'))
    return errors


def ai_workflows_graph_validate_trigger_config(trigger_type, trigger_config) -> list:
    if trigger_type not in TRIGGER_TYPES:
        return [_error(None, 'trigger_type', f'Trigger types are {", ".join(TRIGGER_TYPES)}')]
    if trigger_config is None:
        trigger_config = {}
    if not isinstance(trigger_config, dict):
        return [_error(None, 'trigger_config', 'Must be an object')]
    if trigger_type == TRIGGER_EVENT:
        return _event_config_errors(trigger_config)
    if trigger_type == TRIGGER_CRON:
        return _cron_config_errors(trigger_config)
    if trigger_type == TRIGGER_MANUAL:
        return _manual_config_errors(trigger_config)
    if trigger_type == TRIGGER_WEBHOOK:
        return _webhook_config_errors(trigger_config)
    return []


# ---- Node configs ---------------------------------------------------------------

class _Checker:
    """Collects the errors of one node."""

    def __init__(self, node_id, config, errors):
        self.node_id = node_id
        self.config = config
        self.errors = errors

    def add(self, field, message):
        self.errors.append(_error(self.node_id, field, message))

    def template(self, field, value=None):
        value = self.config.get(field) if value is None else value
        if value in (None, ''):
            return
        if not isinstance(value, str):
            self.add(field, 'Must be a string')
            return
        try:
            webhooks_check_template(value, field)
        except WebhookRenderError as e:
            self.add(field, e.message)

    def templates_in(self, field, value):
        """Every string inside a dict / list value; `{"$path": ...}`
        references must be dotted paths."""
        path = ai_workflows_context_path_reference(value)
        if path is not None:
            if not _CONTEXT_PATH.match(path):
                self.add(field, 'A $path is a dotted path of the run context, e.g. nodes.vt.output.body')
        elif isinstance(value, str):
            self.template(field, value)
        elif isinstance(value, dict):
            for key, item in value.items():
                self.templates_in(f'{field}.{key}', item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.templates_in(f'{field}.{index}', item)

    def required(self, field):
        value = self.config.get(field)
        if not isinstance(value, str) or not value.strip():
            self.add(field, 'Required')
            return False
        return True

    def one_of(self, field, allowed, default=None):
        value = self.config.get(field, default)
        if value not in allowed:
            self.add(field, f'Must be one of {", ".join(str(a) for a in allowed)}')

    def integer(self, field, low, high):
        value = self.config.get(field)
        if value is not None and not _int_in(value, low, high):
            self.add(field, f'Must be an integer between {low} and {high}')

    def tool(self, field, name, allowlist, must_run=False):
        classification = ai_workflows_tools_classification(name) if isinstance(name, str) and name else None
        if classification is None:
            self.add(field, f'Unknown tool {name}' if name else 'Select a tool')
            return None
        # No allowlist: a saved block, checked again once inserted in a workflow
        if must_run and allowlist is not None and classification == CLASSIFICATION_WRITE and name not in allowlist:
            self.add(field, f'{name} is a write tool not in the workflow allowlist (use a Suggest node to '
                            'propose it instead)')
        return classification


def _check_ai_agent(check, allowlist):
    check.required('prompt')
    check.template('prompt')
    tools = check.config.get('tools') or []
    if not isinstance(tools, list):
        check.add('tools', 'Must be a list of tool names')
    else:
        for index, name in enumerate(tools):
            check.tool(f'tools.{index}', name, allowlist)
    schema = check.config.get('output_schema')
    if schema not in (None, '', {}) and (not isinstance(schema, dict) or schema.get('type') not in (None, 'object')):
        check.add('output_schema', 'Must be a JSON schema of type object')
    check.integer('max_turns', 1, 20)
    check.integer('max_tool_calls', 0, 50)
    check.integer('timeout_minutes', 1, 60)


def _check_condition(check):
    mode = check.config.get('mode') or 'rules'
    check.one_of('mode', ('expression', 'rules'), 'rules')
    if mode == 'expression':
        expression = check.config.get('expression')
        if not isinstance(expression, str) or not expression.strip():
            check.add('expression', 'Required')
            return
        try:
            webhooks_check_condition(expression)
        except WebhookRenderError as e:
            check.add('expression', e.message)
        return
    check.one_of('logic', ('and', 'or'), 'and')
    rules = check.config.get('rules') or []
    if not isinstance(rules, list):
        check.add('rules', 'Must be a list')
        return
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not isinstance(rule.get('path'), str) or not rule.get('path').strip():
            check.add(f'rules.{index}.path', 'Required')
            continue
        if rule.get('operator') not in RULE_OPERATORS:
            check.add(f'rules.{index}.operator', f'Operators are {", ".join(RULE_OPERATORS)}')


def _check_http_request(check):
    config = check.config
    if (config.get('method') or 'POST').upper() not in HTTP_METHODS:
        check.add('method', f'Must be one of {", ".join(HTTP_METHODS)}')
    if check.required('url'):
        check.template('url')
        try:
            ai_workflows_context_url_origin(config['url'].strip())
        except AiWorkflowTemplateError as e:
            check.add('url', e.message)
    for field in ('auth_username', 'auth_secret', 'body_template'):
        check.template(field)
    for key in ('headers', 'query_params'):
        entries = config.get(key) or []
        if not isinstance(entries, list):
            check.add(key, 'Must be a list')
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                check.add(f'{key}.{index}', 'Must be an object')
                continue
            check.template(f'{key}.{index}', entry.get('value') or '')
    check.one_of('auth_type', ('none', 'basic', 'bearer'), 'none')
    check.one_of('body_mode', ('default', 'template', 'none'), 'default')
    check.one_of('mode', ('sync', 'async'), 'sync')
    check.one_of('response_format', ('json', 'text'), 'json')
    check.integer('timeout_seconds', 1, 120)
    check.integer('wait_timeout_minutes', 1, 60 * 24 * 30)


def _check_ask_analyst(check):
    check.template('title')
    if check.required('question'):
        check.template('question')
    fields = check.config.get('fields') or []
    if not isinstance(fields, list):
        check.add('fields', 'Must be a list')
        fields = []
    for index, field in enumerate(fields):
        if isinstance(field, str) and field.strip():
            # A bare name is a text field
            continue
        if not isinstance(field, dict) or not field.get('name'):
            check.add(f'fields.{index}.name', 'Required')
            continue
        if field.get('type', 'text') not in FORM_FIELD_TYPES:
            check.add(f'fields.{index}.type', f'Field types are {", ".join(FORM_FIELD_TYPES)}')
    if not fields and not check.config.get('fields_from'):
        check.add('fields', 'Add at least one field, or a context path to read them from')
    check.integer('timeout_minutes', 1, 60 * 24 * 30)


def _check_find_related(check):
    check.one_of('source', ('alert', 'search'), 'alert')
    if check.config.get('source') == 'search' and check.required('search_value'):
        check.template('search_value')
    check.integer('days_back', 1, 365)
    check.integer('number_of_nodes', 1, 1000)


def _check_suggest(check, allowlist):
    check.one_of('kind', SUGGESTION_KINDS, SUGGESTION_KINDS[-1])
    if check.required('title'):
        check.template('title')
    check.template('body')
    severity = check.config.get('severity')
    if severity not in (None, '') and severity not in SEVERITIES:
        check.add('severity', f'Must be one of {", ".join(SEVERITIES)}')
    action = check.config.get('proposed_action')
    if action not in (None, {}):
        if not isinstance(action, dict):
            check.add('proposed_action', 'Must be an object')
        else:
            classification = check.tool('proposed_action.tool', action.get('tool'), allowlist)
            if classification is not None and classification != CLASSIFICATION_WRITE:
                check.add('proposed_action.tool', f'{action.get("tool")} is not a write tool: a proposed action '
                                                  'must change something')
            check.templates_in('proposed_action.arguments', action.get('arguments') or {})


def _check_action(check, allowlist):
    classification = check.tool('tool', check.config.get('tool'), allowlist, must_run=True)
    arguments = check.config.get('arguments') or {}
    if not isinstance(arguments, dict):
        check.add('arguments', 'Must be an object')
    elif classification is not None:
        check.templates_in('arguments', arguments)


def _check_notify(check):
    check.one_of('audience', (AUDIENCE_ENTITY, AUDIENCE_OWNER, 'users'), AUDIENCE_ENTITY)
    if check.config.get('audience') == 'users':
        user_ids = check.config.get('user_ids')
        if not isinstance(user_ids, list) or not user_ids or not all(_int_in(u, 1, 2 ** 62) for u in user_ids):
            check.add('user_ids', 'Select at least one user')
    if check.required('title'):
        check.template('title')
    check.template('body')


def _check_set_variables(check):
    variables = check.config.get('variables') or []
    if not isinstance(variables, list):
        check.add('variables', 'Must be a list')
        return
    for index, item in enumerate(variables):
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) \
                or not _VARIABLE_NAME.match(item.get('name')):
            check.add(f'variables.{index}.name', 'Letters, digits and underscores, not starting with a digit')
            continue
        check.templates_in(f'variables.{index}.value', item.get('value'))


def _check_python(check):
    code = check.config.get('code')
    if not isinstance(code, str) or not code.strip():
        check.add('code', 'Required')
    else:
        for error in ai_workflows_sandbox_check(code)[:5]:
            line = f'line {error["line"]}: ' if error.get('line') else ''
            check.add('code', f'{line}{error["message"]}')
    inputs = check.config.get('inputs') or []
    if not isinstance(inputs, list):
        check.add('inputs', 'Must be a list')
    else:
        for index, item in enumerate(inputs):
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) \
                    or not _VARIABLE_NAME.match(item.get('name')) or item.get('name').startswith('_'):
                check.add(f'inputs.{index}.name', 'Letters, digits and underscores, not starting with a digit or _')
                continue
            check.templates_in(f'inputs.{index}.value', item.get('value'))
    check.integer('timeout_seconds', 1, MAX_TIMEOUT_SECONDS)
    check.integer('max_steps', 1000, MAX_STEPS)


def _size_errors(node_id, field, value, errors):
    """Strings over the config caps (prompts are capped tighter)."""
    if isinstance(value, str):
        limit = _MAX_PROMPT if field in _PROMPT_FIELDS else _MAX_CONFIG_STRING
        if len(value) > limit:
            errors.append(_error(node_id, field, f'At most {limit} characters'))
    elif isinstance(value, dict):
        for key, item in value.items():
            _size_errors(node_id, f'{field}.{key}' if field else str(key), item, errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _size_errors(node_id, f'{field}.{index}', item, errors)


def _check_node(node, allowlist, errors):
    config = node.get('config') or {}
    node_id = node.get('id')
    label = node.get('label')
    if label is not None and (not isinstance(label, str) or len(label) > _MAX_LABEL):
        errors.append(_error(node_id, 'label', f'A label is a string of at most {_MAX_LABEL} characters'))
    handles = node.get('handles')
    if handles is not None and (not isinstance(handles, dict)
                                or set(handles) - {'input', 'output'}
                                or any(side not in _HANDLE_SIDES for side in handles.values())):
        errors.append(_error(node_id, 'handles', 'handles is {"input": side, "output": side}, a side being '
                                                 f'one of {", ".join(_HANDLE_SIDES)}'))
    if not isinstance(config, dict):
        errors.append(_error(node_id, 'config', 'Must be an object'))
        return
    before = len(errors)
    _size_errors(node_id, '', config, errors)
    if len(errors) > before:
        # Do not parse oversized templates
        return
    check = _Checker(node_id, config, errors)
    node_type = node.get('type')
    if node_type == NODE_AI_AGENT:
        _check_ai_agent(check, allowlist)
    elif node_type == NODE_CONDITION:
        _check_condition(check)
    elif node_type == NODE_HTTP_REQUEST:
        _check_http_request(check)
    elif node_type == NODE_ASK_ANALYST:
        _check_ask_analyst(check)
    elif node_type == NODE_FIND_RELATED:
        _check_find_related(check)
    elif node_type == NODE_FIND_WAR_ROOM_TASKS:
        check.one_of('since', ('last_run', 'minutes'), 'last_run')
        check.integer('minutes', 1, 525600)
    elif node_type == NODE_SUGGEST:
        _check_suggest(check, allowlist)
    elif node_type == NODE_ACTION:
        _check_action(check, allowlist)
    elif node_type == NODE_NOTIFY:
        _check_notify(check)
    elif node_type == NODE_DELAY:
        check.integer('minutes', 0, 60 * 24 * 30)
    elif node_type == NODE_SET_VARIABLES:
        _check_set_variables(check)
    elif node_type == NODE_PYTHON:
        _check_python(check)
    elif node_type == NODE_STOP:
        check.one_of('status', (RUN_SUCCEEDED, RUN_FAILED), RUN_SUCCEEDED)
        check.template('reason')


# ---- Graph structure ------------------------------------------------------------

def _structure_errors(nodes, edges, fragment=False) -> tuple:
    """(errors, {node_id: node}, adjacency) of the graph shape. A
    `fragment` (saved block) has no trigger and needs no reachability."""
    errors = []
    by_id = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(_error(None, f'nodes.{index}', 'Must be an object'))
            continue
        node_id = node.get('id')
        if not isinstance(node_id, str) or not _NODE_ID.match(node_id):
            errors.append(_error(None, f'nodes.{index}.id', 'Node ids are 1-64 letters, digits, _ . : -'))
            continue
        if node_id in _RESERVED_NODE_IDS:
            errors.append(_error(None, f'nodes.{index}.id', f'{node_id} is reserved, choose another node id'))
            continue
        if node_id in by_id:
            errors.append(_error(node_id, 'id', f'Duplicate node id {node_id}'))
            continue
        if node.get('type') not in NODE_PORTS:
            errors.append(_error(node_id, 'type', f'Unknown node type {node.get("type")}'))
            continue
        by_id[node_id] = node

    triggers = [n for n in by_id.values() if n.get('type') == NODE_TRIGGER]
    if fragment and triggers:
        errors.append(_error(None, 'nodes', 'A block cannot hold a trigger node'))
    elif not fragment and len(triggers) != 1:
        errors.append(_error(None, 'nodes', 'A workflow needs exactly one trigger node'))

    adjacency = {node_id: [] for node_id in by_id}
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(_error(None, f'edges.{index}', 'Must be an object'))
            continue
        source = by_id.get(edge.get('source'))
        target = by_id.get(edge.get('target'))
        if source is None or target is None:
            errors.append(_error(None, f'edges.{index}', 'Edge between unknown nodes'))
            continue
        port = ai_workflows_graph_edge_port(edge)
        if port not in NODE_PORTS[source['type']]:
            errors.append(_error(source['id'], f'edges.{index}',
                                 f'{source["type"]} nodes have no "{port}" output'))
            continue
        if target.get('type') == NODE_TRIGGER:
            errors.append(_error(target['id'], f'edges.{index}', 'Nothing can lead back to the trigger'))
            continue
        bad_side = next((key for key in ('source_side', 'target_side')
                         if edge.get(key) is not None and edge.get(key) not in _HANDLE_SIDES), None)
        if bad_side:
            errors.append(_error(source['id'], f'edges.{index}.{bad_side}',
                                 f'{bad_side} is one of {", ".join(_HANDLE_SIDES)}'))
            continue
        adjacency[source['id']].append(target['id'])

    if not fragment and len(triggers) == 1:
        reached = {triggers[0]['id']}
        queue = [triggers[0]['id']]
        while queue:
            for target in adjacency[queue.pop()]:
                if target not in reached:
                    reached.add(target)
                    queue.append(target)
        for node_id in by_id:
            if node_id not in reached:
                errors.append(_error(node_id, None, 'Not reachable from the trigger'))

    return errors, by_id, adjacency


def _busy_cycle_nodes(by_id, adjacency) -> list:
    """Nodes on a cycle that has no waiting node (a run would spin):
    the cycles left once waiting nodes are removed."""
    remaining = {node_id for node_id, node in by_id.items() if not ai_workflows_nodes_is_waiting(node)}
    # Kahn: peel nodes without incoming edges; what is left sits on or behind a cycle
    incoming = {node_id: 0 for node_id in remaining}
    for source in remaining:
        for target in adjacency[source]:
            if target in remaining:
                incoming[target] += 1
    queue = [node_id for node_id, count in incoming.items() if count == 0]
    while queue:
        node_id = queue.pop()
        remaining.discard(node_id)
        for target in adjacency[node_id]:
            if target in incoming and target in remaining:
                incoming[target] -= 1
                if incoming[target] == 0:
                    queue.append(target)
    # Keep only nodes actually on a cycle (that can reach themselves)
    on_cycle = []
    for start in sorted(remaining):
        seen = set()
        stack = [t for t in adjacency[start] if t in remaining]
        while stack:
            node_id = stack.pop()
            if node_id == start:
                on_cycle.append(start)
                break
            if node_id in seen:
                continue
            seen.add(node_id)
            stack.extend(t for t in adjacency[node_id] if t in remaining)
    return on_cycle


def ai_workflows_graph_validate_fragment(fragment) -> list:
    """Errors of a saved block `{nodes, edges}`: the checks of a workflow
    graph but the trigger, reachability and the write allowlist (checked
    once the block is inserted into a workflow)."""
    if not isinstance(fragment, dict):
        return [_error(None, 'definition', 'Must be an object')]
    nodes = fragment.get('nodes')
    edges = fragment.get('edges') if fragment.get('edges') is not None else []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [_error(None, 'definition', 'A block needs a list of nodes and a list of edges')]
    if not nodes:
        return [_error(None, 'nodes', 'A block needs at least one node')]
    if len(nodes) > _MAX_BLOCK_NODES:
        return [_error(None, 'nodes', f'A block has at most {_MAX_BLOCK_NODES} nodes')]
    if len(edges) > _MAX_EDGES:
        return [_error(None, 'edges', f'A block has at most {_MAX_EDGES} edges')]
    try:
        size = len(json.dumps(fragment, default=str).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        return [_error(None, 'definition', 'The block is not serialisable')]
    if size > _MAX_DEFINITION_BYTES:
        return [_error(None, 'definition', f'The block is over {_MAX_DEFINITION_BYTES // 1024} KB')]
    errors, by_id, adjacency = _structure_errors(nodes, edges, fragment=True)
    for node_id in _busy_cycle_nodes(by_id, adjacency):
        errors.append(_error(node_id, None, 'On a loop without a waiting node (async HTTP request, ask an '
                                            'analyst or delay)'))
    for node in by_id.values():
        _check_node(node, None, errors)
    return errors


def ai_workflows_graph_validate_node(node, write_tool_allowlist) -> list:
    """Errors of a single node tested on its own: the checks of a node
    of a workflow whose write allowlist is `write_tool_allowlist`."""
    if not isinstance(node, dict):
        return [_error(None, 'node', 'Must be an object')]
    try:
        size = len(json.dumps(node, default=str).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        return [_error(None, 'node', 'The node is not serialisable')]
    if size > _MAX_DEFINITION_BYTES:
        return [_error(None, 'node', f'The node is over {_MAX_DEFINITION_BYTES // 1024} KB')]
    errors, by_id, _adjacency = _structure_errors([node], [], fragment=True)
    allowlist = set(n for n in write_tool_allowlist or [] if isinstance(n, str))
    for checked in by_id.values():
        _check_node(checked, allowlist, errors)
    return errors


def ai_workflows_graph_validate(graph, trigger_type, trigger_config, write_tool_allowlist) -> list:
    if not isinstance(graph, dict):
        return [_error(None, 'graph', 'Must be an object')]
    nodes = graph.get('nodes')
    edges = graph.get('edges') if graph.get('edges') is not None else []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [_error(None, 'graph', 'The graph needs a list of nodes and a list of edges')]
    if len(nodes) > _MAX_NODES:
        return [_error(None, 'graph', f'A workflow has at most {_MAX_NODES} nodes')]
    if len(edges) > _MAX_EDGES:
        return [_error(None, 'graph', f'A workflow has at most {_MAX_EDGES} edges')]
    try:
        size = len(json.dumps(graph, default=str).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        return [_error(None, 'graph', 'The graph is not serialisable')]
    if size > _MAX_DEFINITION_BYTES:
        return [_error(None, 'graph', f'The workflow definition is over {_MAX_DEFINITION_BYTES // 1024} KB')]

    errors = []
    allowlist = write_tool_allowlist if isinstance(write_tool_allowlist, list) else []
    if not isinstance(write_tool_allowlist, (list, type(None))):
        errors.append(_error(None, 'write_tool_allowlist', 'Must be a list of tool names'))
    for name in allowlist:
        if ai_workflows_tools_classification(name) != CLASSIFICATION_WRITE:
            errors.append(_error(None, 'write_tool_allowlist', f'{name} is not a write tool'))
    allowlist = set(n for n in allowlist if isinstance(n, str))

    structure, by_id, adjacency = _structure_errors(nodes, edges)
    errors.extend(structure)
    for node_id in _busy_cycle_nodes(by_id, adjacency):
        errors.append(_error(node_id, None, 'On a loop without a waiting node (async HTTP request, ask an '
                                            'analyst or delay)'))
    for node in by_id.values():
        _check_node(node, allowlist, errors)

    if trigger_type == TRIGGER_CRON and (trigger_config or {}).get('target') in (None, 'none'):
        for node in by_id.values():
            if node.get('type') in (NODE_FIND_RELATED, NODE_FIND_WAR_ROOM_TASKS) \
                    and (node.get('config') or {}).get('source') != 'search':
                errors.append(_error(node['id'], None, 'Needs an entity, but this schedule has no target'))
    return errors
