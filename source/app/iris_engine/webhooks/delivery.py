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

"""One delivery attempt: render, send, record.

Shared by the Celery task (event deliveries, with retries) and the
synchronous test send of the settings page. Works on a configuration
dict with secrets decrypted, so an unsaved form can be tested exactly
like a stored webhook.
"""

from app import app
from app.iris_engine.mail.secrets import decrypt_secret
from app.iris_engine.webhooks.render import webhooks_render_request
from app.iris_engine.webhooks.render import webhooks_template_context
from app.iris_engine.webhooks.sender import webhooks_send


_MAX_LOGGED_REQUEST_CHARS = 64 * 1024
_MAX_LOGGED_RESPONSE_HEADERS = 50


def _decrypt_entries(entries):
    result = []
    for entry in entries or []:
        value = entry.get('value')
        if entry.get('secret'):
            value = decrypt_secret(value) or ''
        result.append({'name': entry.get('name') or '', 'value': value or '', 'secret': bool(entry.get('secret'))})
    return result


def webhooks_config_from_model(webhook) -> dict:
    """The configuration of a stored webhook, secrets decrypted."""
    return {
        'id': webhook.id,
        'name': webhook.name,
        'enabled': webhook.enabled,
        'events': list(webhook.events or []),
        'condition': webhook.condition,
        'method': webhook.method,
        'url': webhook.url,
        'query_params': _decrypt_entries(webhook.query_params),
        'headers': _decrypt_entries(webhook.headers),
        'auth_type': webhook.auth_type,
        'auth_username': webhook.auth_username,
        'auth_secret': decrypt_secret(webhook.auth_secret),
        'body_mode': webhook.body_mode,
        'body_template': webhook.body_template,
        'content_type': webhook.content_type,
        'signing_secret': decrypt_secret(webhook.signing_secret),
        'verify_tls': webhook.verify_tls,
        'timeout_seconds': webhook.timeout_seconds,
        'max_retries': webhook.max_retries,
        'follow_redirects': webhook.follow_redirects,
        'use_proxy': webhook.use_proxy,
    }


def webhooks_user_agent():
    return f'IRIS-Webhooks/{app.config.get("IRIS_VERSION") or ""}'.rstrip('/')


def webhooks_render_for(config, event, delivery_id=None) -> dict:
    context = webhooks_template_context(event, delivery_id, {'id': config.get('id'), 'name': config.get('name')})
    return webhooks_render_request(config, context, signing_secret=config.get('signing_secret'),
                                   user_agent=webhooks_user_agent())


def _body_text(body):
    if body is None:
        return None
    text = body.decode('utf-8', errors='replace')
    if len(text) > _MAX_LOGGED_REQUEST_CHARS:
        return f'{text[:_MAX_LOGGED_REQUEST_CHARS]}\n… [truncated, {len(text)} characters]'
    return text


def webhooks_attempt(config, event, delivery_id=None, proxies=None) -> tuple:
    """Render and send. Returns `(rendered, result)`.

    A template error is a configuration problem: reported as a failed,
    non-retryable result without anything being sent.
    """
    rendered = webhooks_render_for(config, event, delivery_id)
    if rendered['errors']:
        message = '; '.join(f'{e["field"]}: {e["message"]}' for e in rendered['errors'])
        return rendered, {
            'success': False,
            'retryable': False,
            'status_code': None,
            'response_headers': None,
            'response_body': None,
            'error': f'Template error — {message}',
            'duration_ms': 0,
            'final_url': rendered['url'],
        }

    result = webhooks_send(
        rendered,
        verify_tls=bool(config.get('verify_tls', True)),
        timeout=int(config.get('timeout_seconds') or 10),
        follow_redirects=bool(config.get('follow_redirects')),
        proxies=proxies,
        use_proxy=bool(config.get('use_proxy', True)),
    )
    return rendered, result


def webhooks_record_attempt(delivery, rendered, result) -> None:
    """Copy the request (masked) and the response onto `delivery`."""
    delivery.attempts = (delivery.attempts or 0) + 1
    delivery.request_method = rendered['method']
    delivery.request_url = rendered['log_url']
    delivery.request_headers = rendered['log_headers']
    delivery.request_body = _body_text(rendered['body'])
    delivery.response_status = result['status_code']
    headers = result['response_headers']
    if headers is not None:
        headers = dict(list(headers.items())[:_MAX_LOGGED_RESPONSE_HEADERS])
    delivery.response_headers = headers
    delivery.response_body = result['response_body']
    delivery.error = result['error']
    delivery.duration_ms = result['duration_ms']


def webhooks_request_preview(rendered) -> dict:
    """The masked request, for the API."""
    return {
        'method': rendered['method'],
        'url': rendered['log_url'],
        'headers': rendered['log_headers'],
        'body': _body_text(rendered['body']),
        'errors': rendered['errors'],
    }
