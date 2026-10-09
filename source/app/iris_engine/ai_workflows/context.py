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

"""Run context, templates and condition rules.

The run context persisted on `AiWorkflowRun.context` is
`{trigger, entity, nodes: {<id>: {output, port}}, vars}`. Templates see
it plus `run`, `now` and `key('NAME')`; they render in the same sandboxed
Jinja environment as webhooks. `key()` returns the plain value only in the
fields of an HTTP request (URL, query, headers, body); everywhere else a
secret entry renders as a `[secret:NAME]` placeholder, so a secret cannot
be copied into a variable, a suggestion or a prompt.
"""

import datetime
import json
import re
from urllib.parse import urlsplit

from app.iris_engine.webhooks.render import WebhookRenderError
from app.iris_engine.webhooks.render import webhooks_eval_condition
from app.iris_engine.webhooks.render import webhooks_render_template

# Largest rendered template: prompts, bodies and suggestion texts all
# stay far below it
MAX_RENDERED_CHARS = 256 * 1024

_SINGLE_EXPRESSION = re.compile(r'^\s*\{\{(?:(?!\{\{|\}\}|\{%|\{#).)*\}\}\s*$', re.DOTALL)
_URL_ORIGIN = re.compile(r'^\s*(https?)://([^/?#\\]*)', re.IGNORECASE)

RULE_OPERATORS = ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'not_contains', 'in', 'exists',
                  'not_exists', 'truthy', 'falsy')


class AiWorkflowTemplateError(Exception):

    def __init__(self, field, message):
        super().__init__(f'{field}: {message}')
        self.field = field
        self.message = message


def ai_workflows_context_initial(trigger_type, payload, entity_type, entity_id, sub_entity) -> dict:
    hook = None
    if isinstance(payload, dict):
        hook = payload.get('event') or payload.get('hook')
    return {
        'trigger': {
            'type': trigger_type,
            'hook': hook if isinstance(hook, str) else None,
            'payload': payload,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'sub_entity': sub_entity,
        },
        'entity': {},
        'nodes': {},
        'vars': {},
    }


def ai_workflows_context_template(run, resolver, for_llm=False, extra=None, reveal_keys=False) -> dict:
    """Variables for a template rendered in `run`. `key()` returns the
    plain value of a secret entry only with `reveal_keys` (the fields of
    an HTTP request), a `[secret:NAME]` placeholder otherwise."""
    context = dict(run.context or {})

    def key(name):
        if reveal_keys and not for_llm:
            return resolver.get(name)
        return resolver.reveal_for_llm(name)

    context['key'] = key
    context['run'] = {
        'uuid': str(run.uuid) if run.uuid else None,
        'workflow_id': run.workflow_id,
        'workflow_name': run.workflow_name,
        'version': run.workflow_version,
        'dry_run': bool(run.is_dry_run),
    }
    context['now'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if extra:
        context.update(extra)
    return context


def ai_workflows_context_render(source, context, field, for_llm=False) -> str:
    """Render a template of a node. `for_llm` (an agent prompt) escapes
    `<` in every interpolated value: data cannot open or close the tags
    fencing untrusted input in the prompt."""
    if source is None:
        return ''
    if not isinstance(source, str):
        source = str(source)
    try:
        return webhooks_render_template(source, context, field, max_output=MAX_RENDERED_CHARS, fence_values=for_llm)
    except WebhookRenderError as e:
        raise AiWorkflowTemplateError(field, e.message)


def ai_workflows_context_url_origin(template):
    """`scheme://host[:port]` written literally at the start of a URL
    template, lower-cased. Raises `AiWorkflowTemplateError` when the
    scheme or the host is (even partly) a template: interpolated data
    must not choose where a request goes."""
    template = template or ''
    match = _URL_ORIGIN.match(template)
    if not match:
        if '{' in template.split('://', 1)[0] or template.lstrip().startswith('{'):
            raise AiWorkflowTemplateError('url', 'the scheme and host must be written literally, not templated')
        raise AiWorkflowTemplateError('url', 'the URL must start with http:// or https://')
    scheme, netloc = match.group(1), match.group(2)
    if '{' in netloc or '}' in netloc or '%' in netloc:
        raise AiWorkflowTemplateError('url', 'the scheme and host must be written literally, not templated')
    if not netloc:
        raise AiWorkflowTemplateError('url', 'the URL has no host')
    return f'{scheme.lower()}://{netloc.lower()}'


def ai_workflows_context_check_url_origin(template, rendered):
    """Raise `AiWorkflowTemplateError` unless the rendered URL keeps
    the literal scheme and host of its template."""
    expected = ai_workflows_context_url_origin(template)
    try:
        parts = urlsplit((rendered or '').strip())
    except ValueError as e:
        raise AiWorkflowTemplateError('url', f'invalid URL ({e})')
    actual = f'{parts.scheme.lower()}://{parts.netloc.lower()}'
    if actual != expected:
        raise AiWorkflowTemplateError('url', f'the rendered URL goes to {actual}, not {expected}')


def ai_workflows_context_untrusted(source, content) -> str:
    """`content` (text or JSON-able) fenced as untrusted data for a
    model. `<` is escaped inside so the content cannot close the fence
    or open a tag of its own."""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    content = content.replace('<', '\\u003c')
    source = re.sub(r'[^A-Za-z0-9_.:-]', '_', str(source or 'unknown'))[:64]
    return f'<untrusted_input source="{source}">\n{content}\n</untrusted_input>'


def ai_workflows_context_path_reference(value):
    """The dotted path of a `{"$path": "nodes.x.output.y"}` reference, or None."""
    if isinstance(value, dict) and len(value) == 1 and isinstance(value.get('$path'), str):
        return value['$path'].strip()
    return None


def _json_copy(value):
    """`value` as plain JSON data; what is not JSON (functions) becomes null."""
    try:
        return json.loads(json.dumps(value, default=lambda _o: None))
    except (TypeError, ValueError):
        return None


def ai_workflows_context_render_value(value, context, field):
    """Render every string inside `value` (dicts, lists) as a template.
    `{"$path": "dotted.path"}` is replaced by the value found at that path
    of the context, structure included (null when missing): the way to pass
    a list or an object — an IOC's enrichment, a parsed response — to a tool
    the workflow author chose to give it to."""
    if isinstance(value, str):
        return ai_workflows_context_render(value, context, field)
    path = ai_workflows_context_path_reference(value)
    if path is not None:
        found = ai_workflows_context_get_path(context, path)
        if found is _MISSING:
            return None
        rendered = _json_copy(found)
        if len(json.dumps(rendered)) > MAX_RENDERED_CHARS:
            raise AiWorkflowTemplateError(field, f'The value at {path} is too large')
        return rendered
    if isinstance(value, dict):
        return {k: ai_workflows_context_render_value(v, context, f'{field}.{k}') for k, v in value.items()}
    if isinstance(value, list):
        return [ai_workflows_context_render_value(v, context, f'{field}.{i}') for i, v in enumerate(value)]
    return value


def ai_workflows_context_render_arguments(arguments, context, field):
    """Tool arguments: strings are rendered, then a rendered string that
    is a JSON number, boolean, list or object is decoded, so
    `{{ trigger.entity_id }}` gives the integer a tool expects."""
    rendered = ai_workflows_context_render_value(arguments, context, field)
    return _decode_scalars(rendered, arguments)


def _decode_scalars(rendered, original):
    """Decode a rendered string to a JSON number, boolean or null — only
    when its template is exactly one `{{ expression }}`. Never to a list
    or an object: interpolated data must not grow new structure (extra
    arguments, nested ids) in a tool call."""
    if ai_workflows_context_path_reference(original) is not None:
        return rendered
    if isinstance(rendered, dict):
        return {k: _decode_scalars(v, (original or {}).get(k) if isinstance(original, dict) else None)
                for k, v in rendered.items()}
    if isinstance(rendered, list):
        return [_decode_scalars(v, original[i] if isinstance(original, list) and i < len(original) else None)
                for i, v in enumerate(rendered)]
    if isinstance(rendered, str) and isinstance(original, str) and _SINGLE_EXPRESSION.match(original):
        text = rendered.strip()
        if text and (text[0] == '-' or text[0].isdigit() or text in ('true', 'false', 'null')):
            try:
                value = json.loads(text)
            except ValueError:
                return rendered
            if value is None or isinstance(value, (bool, int, float)):
                return value
    return rendered


def ai_workflows_context_eval_expression(expression, context) -> bool:
    try:
        return webhooks_eval_condition(expression, context)
    except WebhookRenderError as e:
        raise AiWorkflowTemplateError('expression', e.message)


def ai_workflows_context_get_path(context, path):
    """Value at a dotted path (`nodes.agent.output.verdict`, `items.0`);
    `_MISSING` when a segment does not exist."""
    current = context
    for segment in str(path or '').split('.'):
        if segment == '':
            continue
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.lstrip('-').isdigit() and -len(current) <= int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


class _Missing:

    def __repr__(self):
        return '<missing>'


_MISSING = _Missing()


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _equals(left, right) -> bool:
    if left == right:
        return True
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        return a == b
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    return str(left) == str(right) if left is not None and right is not None else False


def _contains(container, item) -> bool:
    if isinstance(container, str):
        return str(item) in container
    if isinstance(container, (list, tuple, set)):
        return any(_equals(element, item) for element in container)
    if isinstance(container, dict):
        return str(item) in container
    return False


def _compare(left, right, operator) -> bool:
    a, b = _number(left), _number(right)
    if a is None or b is None:
        if not isinstance(left, str) or not isinstance(right, str):
            return False
        a, b = left, right
    return {
        'gt': a > b,
        'gte': a >= b,
        'lt': a < b,
        'lte': a <= b,
    }[operator]


def ai_workflows_context_eval_rule(rule, context) -> bool:
    operator = rule.get('operator') or 'eq'
    value = ai_workflows_context_get_path(context, rule.get('path'))
    expected = rule.get('value')
    if operator == 'exists':
        return value is not _MISSING and value is not None
    if operator == 'not_exists':
        return value is _MISSING or value is None
    if value is _MISSING:
        value = None
    if operator == 'truthy':
        return bool(value)
    if operator == 'falsy':
        return not value
    if operator == 'eq':
        return _equals(value, expected)
    if operator == 'ne':
        return not _equals(value, expected)
    if operator in ('gt', 'gte', 'lt', 'lte'):
        return _compare(value, expected, operator)
    if operator == 'contains':
        return _contains(value, expected)
    if operator == 'not_contains':
        return not _contains(value, expected)
    if operator == 'in':
        if isinstance(expected, str):
            expected = [item.strip() for item in expected.split(',')]
        return _contains(expected, value)
    raise AiWorkflowTemplateError('rules', f'Unknown operator {operator}')


def ai_workflows_context_eval_rules(rules, logic, context) -> bool:
    """`and` / `or` of the rules; no rule is true."""
    rules = [r for r in (rules or []) if isinstance(r, dict)]
    if not rules:
        return True
    results = (ai_workflows_context_eval_rule(rule, context) for rule in rules)
    if (logic or 'and') == 'or':
        return any(results)
    return all(results)
