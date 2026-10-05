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
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from jinja2 import ChainableUndefined
from jinja2 import TemplateError
from jinja2 import Undefined
from jinja2.sandbox import SandboxedEnvironment

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


def _environment():
    env = SandboxedEnvironment(undefined=ChainableUndefined, autoescape=False, keep_trailing_newline=True)
    env.filters['tojson'] = _tojson
    env.filters['json_escape'] = _json_escape
    env.filters['link'] = _link
    env.filters['pluck'] = _pluck
    return env


_ENV = _environment()


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


def webhooks_render_template(source, context, field='template') -> str:
    try:
        return _ENV.from_string(source or '').render(**context)
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


def webhooks_render_request(config, context, signing_secret=None, user_agent='IRIS', timestamp=None) -> dict:
    """Build the request for `config` (a dict shaped like the API body,
    secret values decrypted) in `context`.

    Returns `{method, url, headers, body, log_url, log_headers, errors}`.
    `errors` lists `{field, message}`; when non-empty the request must
    not be sent, but every field that did render is returned so the
    preview can show as much as possible.
    """
    errors = []

    def render(source, field):
        try:
            return webhooks_render_template(source, context, field)
        except WebhookRenderError as e:
            errors.append({'field': e.field, 'message': e.message})
            return ''

    method = (config.get('method') or 'POST').upper()
    url = render(config.get('url'), 'url').strip()

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
