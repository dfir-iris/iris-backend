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

"""Render a webhook into the HTTP request to send.

URL, query values, header values, the body template and the condition
are Jinja2 templates evaluated in a `SandboxedEnvironment` — they are
written by administrators but the data they interpolate (alert titles,
note content…) is not, and the sandbox keeps a template from reaching
Python internals. Undefined lookups are chainable and render empty, so
one template can serve events whose payloads differ.

Pure: the caller passes the configuration with secrets already
decrypted, and gets back the request plus a masked copy for the log.
"""

import base64
import contextvars
import hashlib
import hmac
import json
import operator
import re
import string
import time
from urllib.parse import parse_qsl
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from jinja2 import ChainableUndefined
from jinja2 import TemplateError
from jinja2 import Undefined
from jinja2 import pass_eval_context
from jinja2.compiler import CodeGenerator
from jinja2.filters import do_indent
from jinja2.filters import do_replace
from jinja2.sandbox import SandboxedEnvironment
from jinja2.sandbox import SecurityError

from app.models.webhooks import AUTH_BASIC
from app.models.webhooks import AUTH_BEARER
from app.models.webhooks import BODY_DEFAULT
from app.models.webhooks import BODY_NONE
from app.models.webhooks import BODY_TEMPLATE


MASK = '••••••'
_URL_MASK = '******'

_MAX_BODY_BYTES = 5 * 1024 * 1024
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ALWAYS_MASKED_HEADERS = ('authorization', 'proxy-authorization', 'x-iris-signature')


class WebhookRenderError(Exception):

    def __init__(self, field, message):
        super().__init__(f'{field}: {message}')
        self.field = field
        self.message = message


def _plain(value):
    if isinstance(value, Undefined):
        return None
    return value


def _tojson(value, indent=None):
    return json.dumps(_plain(value), ensure_ascii=False, default=str, indent=indent)


def _json_escape(value):
    """Escape for use *inside* a JSON string literal: `"{{ x | json_escape }}"`."""
    value = _plain(value)
    if value is None:
        return ''
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _link(url, text=None, style='markdown'):
    """`{{ url | link(title, 'slack') }}` — a link in the receiver's markup."""
    url = _plain(url)
    text = _plain(text) or url
    if not url:
        return text or ''
    if style == 'slack':
        return f'<{url}|{text}>'
    if style == 'html':
        return f'<a href="{url}">{text}</a>'
    if style == 'markdown':
        return f'[{text}]({url})'
    return url


def _get_path(value, keys):
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def _pluck(items, path=''):
    """Value at the dotted `path` of each item; a scalar when there is a
    single item. Mirrors the field mapping of the legacy webhooks module
    so imported configurations keep their output shape.
    """
    items = _plain(items)
    if items is None:
        return None
    if not isinstance(items, list):
        items = [items]
    keys = [k for k in (path or '').split('.') if k]
    values = [_get_path(item, keys) for item in items]
    values = ['' if v is None else v for v in values]
    if not keys:
        return values
    return values[0] if len(values) == 1 else values


# Resource limits. The sandbox stops a template from reaching Python
# internals but not from burning CPU or memory: `{{ 9 ** 9 ** 9 }}`,
# `{{ "x" * 10 ** 9 }}`, nested `range()` loops or a doubling `~` chain
# would each pin a worker. These caps are far above anything a real
# template needs.
_MAX_EXPONENT = 100
_MAX_INT_BITS = 4096
_MAX_REPEAT = 100_000
_MAX_WIDTH = 10_000
_MAX_STRING = 5 * 1024 * 1024
_RANGE_BUDGET = 100_000
_RENDER_SECONDS = 10

_BUDGET = contextvars.ContextVar('webhooks_render_budget', default=None)

_PERCENT_SPEC = re.compile(r'%(?:\([^)]*\))?[-#0 +]*(\*|\d+)?(?:\.(\*|\d+))?')
_WIDTH_ARGUMENT_METHODS = ('ljust', 'rjust', 'center', 'zfill')


class _Budget:

    def __init__(self):
        self.range_left = _RANGE_BUDGET
        self.deadline = time.monotonic() + _RENDER_SECONDS


def _tick():
    budget = _BUDGET.get()
    if budget is not None and time.monotonic() > budget.deadline:
        raise SecurityError(f'template took longer than {_RENDER_SECONDS}s to render')


def _limited(function):
    """Run `function` with a fresh render budget unless one is active."""
    def wrapper(*args, **kwargs):
        if _BUDGET.get() is not None:
            return function(*args, **kwargs)
        token = _BUDGET.set(_Budget())
        try:
            return function(*args, **kwargs)
        finally:
            _BUDGET.reset(token)
    return wrapper


def _check_size(value, limit, what):
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > _MAX_INT_BITS:
            raise SecurityError(f'{what} produces an integer larger than {_MAX_INT_BITS} bits')
    elif isinstance(value, (str, bytes, list, tuple)) and len(value) > limit:
        raise SecurityError(f'{what} produces more than {limit} items')
    return value


def _sequence(value):
    return isinstance(value, (str, bytes, list, tuple))


def _check_width(width, what):
    if isinstance(width, int) and width > _MAX_WIDTH:
        raise SecurityError(f'{what} width is limited to {_MAX_WIDTH}')


def _check_percent_format(fmt):
    for match in _PERCENT_SPEC.finditer(fmt):
        for part in match.groups():
            if part == '*':
                raise SecurityError('"*" widths are not allowed in format strings')
            if part and int(part) > _MAX_WIDTH:
                raise SecurityError(f'format widths are limited to {_MAX_WIDTH}')


def _check_brace_format(fmt):
    try:
        fields = list(string.Formatter().parse(fmt))
    except ValueError:
        return
    for _literal, _name, spec, _conversion in fields:
        if not spec:
            continue
        if '{' in spec:
            raise SecurityError('nested format fields are not allowed')
        for number in re.findall(r'\d+', spec):
            if int(number) > _MAX_WIDTH:
                raise SecurityError(f'format widths are limited to {_MAX_WIDTH}')


def _check_replace(text, old, new, count):
    occurrences = text.count(old) if old else len(text) + 1
    if count is not None and count >= 0:
        occurrences = min(occurrences, count)
    if len(text) + occurrences * max(0, len(new) - len(old)) > _MAX_STRING:
        raise SecurityError(f'replace() would produce more than {_MAX_STRING} characters')


def _safe_range(*args):
    rng = range(*args)
    budget = _BUDGET.get()
    if budget is not None:
        budget.range_left -= len(rng)
        if budget.range_left < 0:
            raise SecurityError(f'templates may iterate over at most {_RANGE_BUDGET} range() items')
    elif len(rng) > _RANGE_BUDGET:
        raise SecurityError(f'range() is limited to {_RANGE_BUDGET} items')
    return rng


def _center(value, width=80):
    _check_width(width, 'center')
    return str(_plain(value) if _plain(value) is not None else '').center(width)


def _indent(value, width=4, first=False, blank=False):
    text = str(_plain(value) if _plain(value) is not None else '')
    pad = width if isinstance(width, str) else ' ' * min(int(width), _MAX_WIDTH + 1)
    _check_width(len(pad), 'indent')
    if len(text) + (text.count('\n') + 1) * len(pad) > _MAX_STRING:
        raise SecurityError(f'indent would produce more than {_MAX_STRING} characters')
    return do_indent(text, width, first, blank)


def _format(value, *args, **kwargs):
    fmt = str(_plain(value) if _plain(value) is not None else '')
    _check_percent_format(fmt)
    return _check_size(fmt % (kwargs or args), _MAX_STRING, 'format')


@pass_eval_context
def _replace(eval_ctx, value, old, new, count=None):
    _check_replace(str(value), str(old), str(new), count)
    return do_replace(eval_ctx, value, old, new, count)


class _CodeGenerator(CodeGenerator):
    """Route `a ~ b` through the environment so its size is checked."""

    def visit_Concat(self, node, frame):
        self.write('environment.limited_concat((')
        for arg in node.nodes:
            self.visit(arg, frame)
            self.write(', ')
        self.write('))')


class _LimitedSandbox(SandboxedEnvironment):

    intercepted_binops = frozenset(['*', '**', '+', '%'])
    code_generator_class = _CodeGenerator

    def call_binop(self, context, operator_name, left, right):
        _tick()
        if operator_name == '**':
            if isinstance(right, (int, float)) and abs(right) > _MAX_EXPONENT:
                raise SecurityError(f'exponents are limited to {_MAX_EXPONENT}')
            return _check_size(operator.pow(left, right), _MAX_REPEAT, 'power')
        if operator_name == '*':
            for sequence, times in ((left, right), (right, left)):
                if _sequence(sequence) and isinstance(times, int) and len(sequence) * max(times, 0) > _MAX_REPEAT:
                    raise SecurityError(f'repetition is limited to {_MAX_REPEAT} items')
            return _check_size(operator.mul(left, right), _MAX_REPEAT, 'multiplication')
        if operator_name == '+':
            if _sequence(left) and _sequence(right) and len(left) + len(right) > _MAX_STRING:
                raise SecurityError(f'concatenation is limited to {_MAX_STRING} items')
            return _check_size(operator.add(left, right), _MAX_STRING, 'addition')
        if operator_name == '%':
            if isinstance(left, str):
                _check_percent_format(left)
            return _check_size(operator.mod(left, right), _MAX_STRING, 'format')
        return super().call_binop(context, operator_name, left, right)

    def limited_concat(self, values):
        _tick()
        parts = [str(v) for v in values]
        if sum(len(p) for p in parts) > _MAX_STRING:
            raise SecurityError(f'concatenation is limited to {_MAX_STRING} characters')
        return ''.join(parts)

    def getattr(self, obj, attribute):
        _tick()
        return super().getattr(obj, attribute)

    def getitem(self, obj, argument):
        _tick()
        return super().getitem(obj, argument)

    def wrap_str_format(self, value):
        wrapper = super().wrap_str_format(value)
        if wrapper is None:
            return None
        fmt = value.__self__

        def checked(*args, **kwargs):
            _check_brace_format(fmt)
            return _check_size(wrapper(*args, **kwargs), _MAX_STRING, 'format')
        return checked

    def call(self, context, obj, /, *args, **kwargs):
        _tick()
        owner = getattr(obj, '__self__', None)
        name = getattr(obj, '__name__', '')
        if isinstance(owner, str):
            if name in _WIDTH_ARGUMENT_METHODS and args:
                _check_width(args[0], name)
            elif name == 'expandtabs':
                size = args[0] if args else kwargs.get('tabsize', 8)
                if isinstance(size, int) and owner.count('\t') * size > _MAX_STRING:
                    raise SecurityError(f'expandtabs would produce more than {_MAX_STRING} characters')
            elif name == 'replace' and len(args) >= 2:
                _check_replace(owner, str(args[0]), str(args[1]), args[2] if len(args) > 2 else kwargs.get('count'))
        return super().call(context, obj, *args, **kwargs)


def _url_finalize(value):
    """Percent-encode every value interpolated into a URL template, so
    data cannot inject a path segment, a query or a fragment."""
    value = _plain(value)
    if value is None:
        return ''
    return quote(str(value), safe='')


def _fenced_finalize(value):
    """Escape `<` in every value interpolated into an LLM prompt, so data
    cannot open or close the tags that fence untrusted input."""
    return str(value).replace('<', '\\u003c')


def _environment(finalize=None):
    env = _LimitedSandbox(undefined=ChainableUndefined, autoescape=False, keep_trailing_newline=True,
                          finalize=finalize)
    env.globals['range'] = _safe_range
    env.filters['tojson'] = _tojson
    env.filters['json_escape'] = _json_escape
    env.filters['link'] = _link
    env.filters['pluck'] = _pluck
    env.filters['center'] = _center
    env.filters['indent'] = _indent
    env.filters['format'] = _format
    env.filters['replace'] = _replace
    return env


_ENV = _environment()
_URL_ENV = _environment(finalize=_url_finalize)
_FENCED_ENV = _environment(finalize=_fenced_finalize)


def webhooks_template_context(event, delivery_id=None, webhook=None) -> dict:
    """Variables available to every template.

    The envelope's top-level fields are variables themselves (`title`,
    `data`, `case`…); `payload` is the whole envelope and `items` is
    `data` as a list whatever the hook passed.
    """
    envelope = dict(event)
    envelope['delivery_id'] = str(delivery_id) if delivery_id else None
    envelope['webhook'] = webhook
    data = envelope.get('data')
    context = dict(envelope)
    context['payload'] = envelope
    context['items'] = data if isinstance(data, list) else ([] if data is None else [data])
    return context


@_limited
def webhooks_render_template(source, context, field='template', max_output=None, quote_values=False,
                             fence_values=False) -> str:
    """Render `source`. `max_output` caps the rendered size (characters);
    `quote_values` percent-encodes every interpolated value (URLs);
    `fence_values` escapes `<` in them (LLM prompts)."""
    env = _URL_ENV if quote_values else (_FENCED_ENV if fence_values else _ENV)
    try:
        template = env.from_string(source or '')
        if max_output is None:
            return template.render(**context)
        chunks = []
        size = 0
        for chunk in template.generate(**context):
            size += len(chunk)
            if size > max_output:
                raise WebhookRenderError(field, f'rendered output is larger than {max_output} characters')
            chunks.append(chunk)
        return ''.join(chunks)
    except WebhookRenderError:
        raise
    except TemplateError as e:
        raise WebhookRenderError(field, _template_error(e))
    except Exception as e:
        raise WebhookRenderError(field, str(e))


def _template_error(error):
    line = getattr(error, 'lineno', None)
    message = getattr(error, 'message', None) or str(error)
    return f'line {line}: {message}' if line else message


def webhooks_check_template(source, field='template'):
    """Syntax check without rendering; raises `WebhookRenderError`."""
    try:
        _ENV.parse(source or '')
    except TemplateError as e:
        raise WebhookRenderError(field, _template_error(e))


def _strip_braces(expression):
    expression = (expression or '').strip()
    if expression.startswith('{{') and expression.endswith('}}'):
        expression = expression[2:-2].strip()
    return expression


def webhooks_check_condition(expression):
    try:
        _ENV.compile_expression(_strip_braces(expression))
    except TemplateError as e:
        raise WebhookRenderError('condition', _template_error(e))


@_limited
def webhooks_eval_condition(expression, context) -> bool:
    """True when there is no condition or it evaluates truthy."""
    expression = _strip_braces(expression)
    if not expression:
        return True
    try:
        return bool(_ENV.compile_expression(expression, undefined_to_none=True)(**context))
    except TemplateError as e:
        raise WebhookRenderError('condition', _template_error(e))
    except Exception as e:
        raise WebhookRenderError('condition', str(e))


def _with_query(url, params):
    if not params:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + params
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def webhooks_sign(secret, timestamp, body) -> str:
    message = f'{timestamp}.'.encode('utf-8') + (body or b'')
    return f'sha256={hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()}'


def _entry_value(entry, render, field):
    """Secret values are sent verbatim: a token may contain `{{` or `{#`."""
    if entry.get('secret'):
        return entry.get('value') or ''
    return render(entry.get('value'), field)


def webhooks_render_request(config, context, signing_secret=None, user_agent='IRIS', timestamp=None,
                            quote_url_values=False, max_output=None) -> dict:
    """Build the request for `config` (a dict shaped like the API body,
    secret values decrypted) in `context`.

    `quote_url_values` percent-encodes the values interpolated into the
    URL template; `max_output` caps each rendered field (characters).

    Returns `{method, url, headers, body, log_url, log_headers, errors}`.
    `errors` lists `{field, message}`; when non-empty the request must
    not be sent, but every field that did render is returned so the
    preview can show as much as possible.
    """
    errors = []

    def render(source, field, quote_values=False):
        try:
            return webhooks_render_template(source, context, field, max_output=max_output,
                                            quote_values=quote_values)
        except WebhookRenderError as e:
            errors.append({'field': e.field, 'message': e.message})
            return ''

    method = (config.get('method') or 'POST').upper()
    url = render(config.get('url'), 'url', quote_values=quote_url_values).strip()

    params = []
    log_params = []
    for index, param in enumerate(config.get('query_params') or []):
        name = (param.get('name') or '').strip()
        if not name:
            continue
        value = _entry_value(param, render, f'query_params.{index}')
        params.append((name, value))
        log_params.append((name, _URL_MASK if param.get('secret') else value))

    headers = {
        'User-Agent': user_agent,
        'X-IRIS-Event': context.get('event') or '',
    }
    if context.get('delivery_id'):
        headers['X-IRIS-Delivery'] = context['delivery_id']
    secret_headers = set()

    auth_type = config.get('auth_type')
    if auth_type == AUTH_BASIC:
        raw = f'{config.get("auth_username") or ""}:{config.get("auth_secret") or ""}'
        headers['Authorization'] = f'Basic {base64.b64encode(raw.encode("utf-8")).decode("ascii")}'
    elif auth_type == AUTH_BEARER:
        headers['Authorization'] = f'Bearer {config.get("auth_secret") or ""}'

    for index, header in enumerate(config.get('headers') or []):
        name = (header.get('name') or '').strip()
        if not name:
            continue
        if not _HEADER_NAME.match(name):
            errors.append({'field': f'headers.{index}', 'message': f'invalid header name "{name}"'})
            continue
        value = _entry_value(header, render, f'headers.{index}')
        if '\r' in value or '\n' in value:
            errors.append({'field': f'headers.{index}', 'message': f'header "{name}" renders a line break'})
            continue
        for existing in [k for k in headers if k.lower() == name.lower()]:
            del headers[existing]
        headers[name] = value
        if header.get('secret'):
            secret_headers.add(name.lower())

    body_mode = config.get('body_mode') or BODY_DEFAULT
    body = None
    if body_mode == BODY_DEFAULT:
        body = _tojson(context.get('payload'), indent=None)
    elif body_mode == BODY_TEMPLATE:
        body = render(config.get('body_template'), 'body_template')
        content_type = (config.get('content_type') or '').lower()
        if body and 'json' in content_type and not any(e['field'] == 'body_template' for e in errors):
            try:
                json.loads(body)
            except ValueError as e:
                errors.append({'field': 'body_template', 'message': f'rendered body is not valid JSON ({e})'})
    elif body_mode != BODY_NONE:
        errors.append({'field': 'body_mode', 'message': f'unknown body mode {body_mode}'})

    body_bytes = body.encode('utf-8') if body is not None else None
    if body_bytes is not None and len(body_bytes) > _MAX_BODY_BYTES:
        errors.append({'field': 'body_template', 'message': 'rendered body is larger than 5 MB'})

    if body_bytes is not None and not any(k.lower() == 'content-type' for k in headers):
        headers['Content-Type'] = config.get('content_type') or 'application/json'

    if signing_secret:
        stamp = str(int(timestamp if timestamp is not None else time.time()))
        headers['X-IRIS-Timestamp'] = stamp
        headers['X-IRIS-Signature'] = webhooks_sign(signing_secret, stamp, body_bytes)

    log_headers = {
        name: MASK if (name.lower() in secret_headers or name.lower() in _ALWAYS_MASKED_HEADERS) else value
        for name, value in headers.items()
    }

    return {
        'method': method,
        'url': _with_query(url, params),
        'headers': headers,
        'body': body_bytes,
        'log_url': _with_query(url, log_params),
        'log_headers': log_headers,
        'errors': errors,
    }
