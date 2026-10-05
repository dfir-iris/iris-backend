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

"""Business layer for native webhooks (server administrators only).

Secret handling, shared by save, preview and test: the API never
returns a secret value. On write, a secret entry sent without a value
keeps the stored one (matched by name), a key absent from the body keeps
the stored `auth_secret` / `signing_secret`, and an explicit null or
empty string clears it. That lets the settings page round-trip a
webhook without ever holding its secrets.
"""

import datetime
import uuid

from app import app
from app.datamgmt.db_operations import db_create
from app.datamgmt.db_operations import db_delete
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_commit
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_create_delivery
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_delivery_counts
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_get
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_get_delivery
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_get_module
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_hook_names
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_last_deliveries
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_latest_payload
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_list
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_list_deliveries
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_list_enabled
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_subscribable_hooks
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_proxies
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_utcnow
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.utils.tracker import track_activity
from app.iris_engine.webhooks.delivery import webhooks_attempt
from app.iris_engine.webhooks.delivery import webhooks_config_from_model
from app.iris_engine.webhooks.delivery import webhooks_record_attempt
from app.iris_engine.webhooks.delivery import webhooks_render_for
from app.iris_engine.webhooks.delivery import webhooks_request_preview
from app.iris_engine.webhooks.dispatch import webhooks_build_event
from app.iris_engine.webhooks.dispatch import webhooks_current_actor
from app.iris_engine.webhooks.events import MANUAL_PREFIX
from app.iris_engine.webhooks.events import webhooks_build_catalogue
from app.iris_engine.webhooks.events import webhooks_event_label
from app.iris_engine.webhooks.events import webhooks_is_manual
from app.iris_engine.webhooks.events import webhooks_split_event
from app.iris_engine.webhooks.legacy import LEGACY_MODULE_NAME
from app.iris_engine.webhooks.legacy import webhooks_convert_legacy
from app.iris_engine.webhooks.legacy import webhooks_parse_legacy_config
from app.iris_engine.webhooks.payload import webhooks_make_event
from app.iris_engine.webhooks.render import WebhookRenderError
from app.iris_engine.webhooks.render import webhooks_check_condition
from app.iris_engine.webhooks.render import webhooks_check_template
from app.iris_engine.webhooks.render import webhooks_eval_condition
from app.iris_engine.webhooks.render import webhooks_template_context
from app.iris_engine.webhooks.sample import webhooks_sample_data
from app.iris_engine.webhooks.sender import webhooks_allow_private_egress
from app.iris_engine.webhooks.sender import webhooks_destination_error
from app.iris_engine.webhooks.sender import webhooks_env_proxy_configured
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.webhooks import ALL_EVENTS
from app.models.webhooks import AUTH_BASIC
from app.models.webhooks import AUTH_BEARER
from app.models.webhooks import AUTH_NONE
from app.models.webhooks import AUTH_TYPES
from app.models.webhooks import BODY_DEFAULT
from app.models.webhooks import BODY_MODES
from app.models.webhooks import BODY_TEMPLATE
from app.models.webhooks import DELIVERY_STATUSES
from app.models.webhooks import STATUS_FAILED
from app.models.webhooks import STATUS_PENDING
from app.models.webhooks import STATUS_SUCCESS
from app.models.webhooks import TRIGGER_MANUAL
from app.models.webhooks import TRIGGER_REDELIVER
from app.models.webhooks import TRIGGER_TEST
from app.models.webhooks import WEBHOOK_METHODS
from app.models.webhooks import Webhook


_MAX_TIMEOUT = 60
_MAX_RETRIES = 10
_DEFAULT_SAMPLE_EVENT = 'on_postload_case_create'
_SAMPLE_CASE = {'id': 1, 'name': '#1 - Sample case'}
_SAMPLE_INSTANCE_URL = 'https://iris.example.org'


# ---- Serialization ---------------------------------------------------------

def _iso(value):
    return value.isoformat() if value else None


def _public_entries(entries):
    return [
        {
            'name': entry.get('name') or '',
            'value': None if entry.get('secret') else (entry.get('value') or ''),
            'secret': bool(entry.get('secret')),
            'has_value': bool(entry.get('value')),
        }
        for entry in entries or []
    ]


def _last_delivery_public(row):
    if row is None:
        return None
    return {
        'status': row.status,
        'response_status': row.response_status,
        'event': row.event,
        'created_at': _iso(row.created_at),
        'error': row.error,
    }


def webhooks_public(webhook, last=None, counts=None) -> dict:
    return {
        'id': webhook.id,
        'name': webhook.name,
        'description': webhook.description,
        'enabled': webhook.enabled,
        'events': list(webhook.events or []),
        'manual_label': webhook.manual_label,
        'condition': webhook.condition,
        'method': webhook.method,
        'url': webhook.url,
        'query_params': _public_entries(webhook.query_params),
        'headers': _public_entries(webhook.headers),
        'auth_type': webhook.auth_type,
        'auth_username': webhook.auth_username,
        'has_auth_secret': bool(webhook.auth_secret),
        'body_mode': webhook.body_mode,
        'body_template': webhook.body_template,
        'content_type': webhook.content_type,
        'has_signing_secret': bool(webhook.signing_secret),
        'verify_tls': webhook.verify_tls,
        'timeout_seconds': webhook.timeout_seconds,
        'max_retries': webhook.max_retries,
        'follow_redirects': webhook.follow_redirects,
        'use_proxy': webhook.use_proxy,
        'created_by': {'id': webhook.created_by.id, 'name': webhook.created_by.name} if webhook.created_by else None,
        'created_at': _iso(webhook.created_at),
        'updated_at': _iso(webhook.updated_at),
        'last_delivery': _last_delivery_public(last),
        'deliveries_24h': counts or {},
    }


def webhooks_delivery_public(delivery, detailed=False) -> dict:
    result = {
        'id': delivery.id,
        'uuid': str(delivery.uuid) if delivery.uuid else None,
        'webhook_id': delivery.webhook_id,
        'event': delivery.event,
        'event_label': webhooks_event_label(delivery.event),
        'trigger': delivery.trigger,
        'status': delivery.status,
        'attempts': delivery.attempts,
        'response_status': delivery.response_status,
        'error': delivery.error,
        'duration_ms': delivery.duration_ms,
        'created_at': _iso(delivery.created_at),
        'completed_at': _iso(delivery.completed_at),
    }
    payload = delivery.payload or {}
    result['title'] = payload.get('title')
    if detailed:
        result.update({
            'request_method': delivery.request_method,
            'request_url': delivery.request_url,
            'request_headers': delivery.request_headers,
            'request_body': delivery.request_body,
            'response_headers': delivery.response_headers,
            'response_body': delivery.response_body,
            'payload': delivery.payload,
        })
    return result


# ---- Validation ------------------------------------------------------------

class _Errors:

    def __init__(self):
        self.messages = {}

    def add(self, field, message):
        self.messages.setdefault(field, []).append(message)

    def raise_if_any(self):
        if self.messages:
            raise BusinessProcessingError('Invalid webhook', data=self.messages)


def _as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _as_int(value, default, minimum, maximum, field, errors):
    if value is None or value == '':
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors.add(field, 'Must be a whole number')
        return default
    if number < minimum or number > maximum:
        errors.add(field, f'Must be between {minimum} and {maximum}')
    return number


def _template(value, field, errors):
    if value is None:
        return None
    if not isinstance(value, str):
        errors.add(field, 'Must be text')
        return None
    try:
        webhooks_check_template(value, field)
    except WebhookRenderError as e:
        errors.add(field, e.message)
    return value


def _entries(field, body, existing_entries, errors, case_insensitive):
    """Validated `[{name, value, secret}]`, secret values encrypted."""
    raw = body.get(field)
    if raw is None:
        return list(existing_entries or [])
    if not isinstance(raw, list):
        errors.add(field, 'Must be a list')
        return list(existing_entries or [])

    def key(name):
        return name.lower() if case_insensitive else name

    previous = {key(e.get('name') or ''): e for e in existing_entries or [] if e.get('secret')}
    result = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.add(field, f'Row {index + 1} is not an object')
            continue
        name = str(entry.get('name') or '').strip()
        value = entry.get('value')
        secret = _as_bool(entry.get('secret'), False)
        if not name:
            if value:
                errors.add(field, f'Row {index + 1} has a value but no name')
            continue
        if field == 'headers' and ('\r' in name or '\n' in name or ':' in name or ' ' in name):
            errors.add(field, f'Invalid header name "{name}"')
            continue
        if value is not None and not isinstance(value, str):
            value = str(value)

        if secret:
            if value is None or value == '':
                kept = previous.get(key(name))
                if kept is None or not kept.get('value'):
                    errors.add(field, f'Enter a value for the secret "{name}"')
                    continue
                result.append({'name': name, 'value': kept.get('value'), 'secret': True})
            else:
                result.append({'name': name, 'value': encrypt_secret(value), 'secret': True})
        else:
            _template(value or '', f'{field}.{index}', errors)
            result.append({'name': name, 'value': value or '', 'secret': False})
    return result


def _secret_field(field, body, existing_value):
    """Ciphertext to store: absent keeps, null / '' clears, text replaces."""
    if field not in body:
        return existing_value
    value = body.get(field)
    if value is None or value == '':
        return None
    return encrypt_secret(str(value))


def _webhooks_validate(body, existing=None, strict=True, check_destination=True) -> dict:
    """Model attributes for `body` merged over `existing`.

    `strict=False` (preview / test of an unsaved form) skips the checks
    that only matter for a stored webhook — name, events, destination.
    """
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid webhook', data={'_schema': ['Expected a JSON object']})
    errors = _Errors()

    def pick(field, default=None):
        if field in body:
            return body.get(field)
        if existing is not None:
            return getattr(existing, field)
        return default

    name = str(pick('name') or '').strip()
    if strict and not name:
        errors.add('name', 'A name is required')
    if len(name) > 255:
        errors.add('name', 'At most 255 characters')

    enabled = _as_bool(pick('enabled'), True)

    events = pick('events', [])
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        errors.add('events', 'Must be a list of event names')
        events = []
    events = list(dict.fromkeys(events))
    if ALL_EVENTS in events:
        # The wildcard stands for the postload events; manual triggers stay listed
        events = [ALL_EVENTS] + [e for e in events if webhooks_is_manual(e)]
    if strict:
        known = webhooks_db_hook_names()
        unknown = [e for e in events if e != ALL_EVENTS and e not in known]
        if unknown:
            errors.add('events', f'Unknown events: {", ".join(unknown)}')
    if strict and enabled and not events:
        errors.add('events', 'Select at least one event, or disable the webhook')

    condition = pick('condition')
    if isinstance(condition, str) and condition.strip():
        try:
            webhooks_check_condition(condition)
        except WebhookRenderError as e:
            errors.add('condition', e.message)
    else:
        condition = None

    method = str(pick('method', 'POST') or 'POST').upper()
    if method not in WEBHOOK_METHODS:
        errors.add('method', f'Must be one of {", ".join(WEBHOOK_METHODS)}')

    url = str(pick('url') or '').strip()
    if not url:
        if strict:
            errors.add('url', 'A URL is required')
    else:
        _template(url, 'url', errors)
        if '{' not in url:
            if strict and check_destination:
                refused = webhooks_destination_error(url)
                if refused:
                    errors.add('url', refused)
        elif not url.lower().startswith(('http://', 'https://')):
            errors.add('url', 'A templated URL must still start with http:// or https://')

    query_params = _entries('query_params', body, existing.query_params if existing else [], errors, False)
    headers = _entries('headers', body, existing.headers if existing else [], errors, True)

    auth_type = pick('auth_type', AUTH_NONE) or AUTH_NONE
    if auth_type not in AUTH_TYPES:
        errors.add('auth_type', f'Must be one of {", ".join(AUTH_TYPES)}')
    auth_username = (str(pick('auth_username') or '').strip() or None) if auth_type == AUTH_BASIC else None
    auth_secret = _secret_field('auth_secret', body, existing.auth_secret if existing else None)
    if auth_type == AUTH_NONE:
        auth_secret = None
    if auth_type == AUTH_BASIC and not auth_username:
        errors.add('auth_username', 'A username is required for basic authentication')
    if auth_type == AUTH_BEARER and not auth_secret:
        errors.add('auth_secret', 'A token is required for bearer authentication')

    body_mode = pick('body_mode', BODY_DEFAULT) or BODY_DEFAULT
    if body_mode not in BODY_MODES:
        errors.add('body_mode', f'Must be one of {", ".join(BODY_MODES)}')
    body_template = _template(pick('body_template'), 'body_template', errors)
    if body_mode == BODY_TEMPLATE and not (body_template or '').strip():
        errors.add('body_template', 'A body template is required in template mode')

    content_type = str(pick('content_type', 'application/json') or 'application/json').strip()
    if len(content_type) > 255 or '\r' in content_type or '\n' in content_type:
        errors.add('content_type', 'Invalid content type')

    signing_secret = _secret_field('signing_secret', body, existing.signing_secret if existing else None)

    timeout_seconds = _as_int(pick('timeout_seconds'), 10, 1, _MAX_TIMEOUT, 'timeout_seconds', errors)
    max_retries = _as_int(pick('max_retries'), 3, 0, _MAX_RETRIES, 'max_retries', errors)

    description = pick('description')
    description = (str(description).strip() or None) if description is not None else None

    manual_label = pick('manual_label')
    manual_label = (str(manual_label).strip() or None) if manual_label is not None else None
    if manual_label and len(manual_label) > 255:
        errors.add('manual_label', 'At most 255 characters')

    errors.raise_if_any()

    return {
        'name': name or 'Untitled webhook',
        'description': description,
        'enabled': enabled,
        'events': events,
        'manual_label': manual_label,
        'condition': condition,
        'method': method,
        'url': url,
        'query_params': query_params,
        'headers': headers,
        'auth_type': auth_type,
        'auth_username': auth_username,
        'auth_secret': auth_secret,
        'body_mode': body_mode,
        'body_template': body_template,
        'content_type': content_type,
        'signing_secret': signing_secret,
        'verify_tls': _as_bool(pick('verify_tls'), True),
        'timeout_seconds': timeout_seconds,
        'max_retries': max_retries,
        'follow_redirects': _as_bool(pick('follow_redirects'), False),
        'use_proxy': _as_bool(pick('use_proxy'), True),
    }


# ---- CRUD ------------------------------------------------------------------

def webhooks_list() -> list:
    webhooks = webhooks_db_list()
    ids = [w.id for w in webhooks]
    last = webhooks_db_last_deliveries(ids)
    counts = webhooks_db_delivery_counts(ids, webhooks_db_utcnow() - datetime.timedelta(hours=24))
    return [webhooks_public(w, last.get(w.id), counts.get(w.id)) for w in webhooks]


def webhooks_get(webhook_id) -> Webhook:
    webhook = webhooks_db_get(webhook_id)
    if webhook is None:
        raise ObjectNotFoundError()
    return webhook


def webhooks_get_public(webhook_id) -> dict:
    webhook = webhooks_get(webhook_id)
    last = webhooks_db_last_deliveries([webhook.id]).get(webhook.id)
    counts = webhooks_db_delivery_counts([webhook.id], webhooks_db_utcnow() - datetime.timedelta(hours=24))
    return webhooks_public(webhook, last, counts.get(webhook.id))


def webhooks_create(body, user_id) -> dict:
    attributes = _webhooks_validate(body)
    webhook = Webhook(**attributes)
    webhook.created_by_id = user_id
    db_create(webhook)
    track_activity(f'Webhook #{webhook.id} "{webhook.name}" created', ctx_less=True)
    return webhooks_public(webhook)


def webhooks_update(webhook_id, body) -> dict:
    webhook = webhooks_get(webhook_id)
    attributes = _webhooks_validate(body, existing=webhook)
    for key, value in attributes.items():
        setattr(webhook, key, value)
    webhooks_db_commit()
    track_activity(f'Webhook #{webhook.id} "{webhook.name}" updated', ctx_less=True)
    return webhooks_get_public(webhook.id)


def webhooks_delete(webhook_id) -> None:
    webhook = webhooks_get(webhook_id)
    name = webhook.name
    db_delete(webhook)
    track_activity(f'Webhook #{webhook_id} "{name}" deleted', ctx_less=True)


def webhooks_events() -> list:
    return webhooks_build_catalogue(webhooks_db_subscribable_hooks())


def webhooks_settings() -> dict:
    """Server-level facts the settings page explains to the admin."""
    return {
        'allow_private_egress': webhooks_allow_private_egress(),
        'instance_url_configured': bool(_instance_url()),
        # A boolean only: a proxy URL may carry credentials
        'proxy_configured': bool(webhooks_db_proxies()) or webhooks_env_proxy_configured(),
    }


# ---- Preview / test --------------------------------------------------------

def _instance_url():
    return app.config.get('IRIS_ALLOW_ORIGIN') or ''


def _sample_event(event_name, webhook_id=None) -> tuple:
    """`(event, source)` — the last real payload of this event if one was
    ever delivered, else a synthetic one."""
    payload = webhooks_db_latest_payload(event_name, webhook_id)
    if payload:
        return payload, 'last_delivery'
    object_type, action = webhooks_split_event(event_name)
    event = webhooks_make_event(
        event_name,
        webhooks_sample_data(object_type, action),
        case=_SAMPLE_CASE if object_type not in ('alert', 'alert_cluster', 'global_task', 'war_room') else None,
        actor=webhooks_current_actor(),
        instance_url=_instance_url() or _SAMPLE_INSTANCE_URL,
        version=_iris_version(),
    )
    return event, 'sample'


def _iris_version():
    return app.config.get('IRIS_VERSION') or ''


def _transient_config(body, webhook_id):
    existing = webhooks_get(webhook_id) if webhook_id else None
    attributes = _webhooks_validate(body or {}, existing=existing, strict=False)
    transient = Webhook(**attributes)
    transient.id = existing.id if existing else None
    config = webhooks_config_from_model(transient)
    return existing, config


def _pick_event(event_name, config):
    if event_name:
        return event_name
    events = [e for e in config.get('events') or [] if e != ALL_EVENTS]
    return events[0] if events else _DEFAULT_SAMPLE_EVENT


def webhooks_preview(body, webhook_id=None, event_name=None) -> dict:
    """Render the request an (unsaved) configuration would send."""
    existing, config = _transient_config(body, webhook_id)
    event_name = _pick_event(event_name, config)
    event, source = _sample_event(event_name, existing.id if existing else None)
    delivery_id = uuid.uuid4()

    condition = {'matches': True, 'error': None}
    if config.get('condition'):
        try:
            context = webhooks_template_context(event, delivery_id, {'id': config['id'], 'name': config['name']})
            condition['matches'] = webhooks_eval_condition(config['condition'], context)
        except WebhookRenderError as e:
            condition = {'matches': None, 'error': e.message}

    rendered = webhooks_render_for(config, event, delivery_id)
    return {
        'event': event_name,
        'sample_source': source,
        'condition': condition,
        'request': webhooks_request_preview(rendered),
        'context': webhooks_template_context(event, delivery_id, {'id': config['id'], 'name': config['name']}),
    }


def webhooks_test(body, webhook_id=None, event_name=None) -> dict:
    """Send the request synchronously and report what came back.

    Ignores `enabled` and the condition — the point is to try the
    receiver. Stored as a `test` delivery when the webhook exists.
    """
    existing, config = _transient_config(body, webhook_id)
    if config['url'] and '{' not in config['url']:
        refused = webhooks_destination_error(config['url'])
        if refused:
            raise BusinessProcessingError('Destination refused', data={'url': [refused]})

    event_name = _pick_event(event_name, config)
    event, source = _sample_event(event_name, existing.id if existing else None)

    delivery = None
    if existing is not None:
        delivery = webhooks_db_create_delivery(existing.id, event_name, TRIGGER_TEST, event, STATUS_PENDING)
    delivery_id = delivery.uuid if delivery is not None else uuid.uuid4()

    rendered, result = webhooks_attempt(config, event, delivery_id=delivery_id, proxies=webhooks_db_proxies())

    if delivery is not None:
        webhooks_record_attempt(delivery, rendered, result)
        delivery.status = STATUS_SUCCESS if result['success'] else STATUS_FAILED
        delivery.completed_at = webhooks_db_utcnow()
        webhooks_db_commit()

    return {
        'event': event_name,
        'sample_source': source,
        'delivery_id': delivery.id if delivery is not None else None,
        'request': webhooks_request_preview(rendered),
        'response': {
            'success': result['success'],
            'status_code': result['status_code'],
            'headers': result['response_headers'],
            'body': result['response_body'],
            'error': result['error'],
            'duration_ms': result['duration_ms'],
        },
    }


# ---- Deliveries ------------------------------------------------------------

def webhooks_list_deliveries(webhook_id, status=None, event=None, page=1, per_page=25) -> dict:
    webhooks_get(webhook_id)
    if status and status not in DELIVERY_STATUSES:
        raise BusinessProcessingError('Invalid status filter', data={'status': [f'Unknown status {status}']})
    per_page = max(1, min(int(per_page or 25), 100))
    page = max(1, int(page or 1))
    paginated = webhooks_db_list_deliveries(webhook_id, status=status, event=event, page=page, per_page=per_page)
    return {
        'total': paginated.total,
        'current_page': paginated.page,
        'last_page': paginated.pages,
        'next_page': paginated.next_num if paginated.has_next else None,
        'data': [webhooks_delivery_public(d) for d in paginated.items],
    }


def webhooks_get_delivery(delivery_id) -> dict:
    delivery = webhooks_db_get_delivery(delivery_id)
    if delivery is None:
        raise ObjectNotFoundError()
    return webhooks_delivery_public(delivery, detailed=True)


def webhooks_redeliver(delivery_id) -> dict:
    """Queue a new delivery of the same payload, whatever `enabled` says."""
    original = webhooks_db_get_delivery(delivery_id)
    if original is None:
        raise ObjectNotFoundError()
    if not original.payload:
        raise BusinessProcessingError('This delivery has no stored payload to send again')
    delivery = webhooks_db_create_delivery(original.webhook_id, original.event, TRIGGER_REDELIVER,
                                           original.payload, STATUS_PENDING)
    from app.iris_engine.webhooks.tasks import webhooks_deliver_task
    webhooks_deliver_task.delay(delivery.id)
    track_activity(f'Webhook #{original.webhook_id} delivery #{original.id} redelivered', ctx_less=True)
    return webhooks_delivery_public(delivery)


# ---- Manual triggers -------------------------------------------------------

# `module_name` of the webhook entries in the object menus. Not a module:
# the invoke routes see `webhook_id` and come here instead.
WEBHOOKS_MENU_MODULE_NAME = 'IRIS webhooks'


def webhooks_manual_options(hook_type) -> list:
    """Menu entries of the enabled webhooks subscribed to `on_manual_trigger_<hook_type>`.

    Same shape as the module entries, plus `webhook_id`.
    """
    hook_name = f'{MANUAL_PREFIX}{hook_type}'
    return [
        {
            'manual_hook_ui_name': webhook.manual_label or webhook.name,
            'hook_name': hook_name,
            'module_name': WEBHOOKS_MENU_MODULE_NAME,
            'webhook_id': webhook.id,
        }
        for webhook in webhooks_db_list_enabled()
        if hook_name in (webhook.events or [])
    ]


def webhooks_invoke_manual(webhook_id, hook_name, targets, caseid=None) -> int:
    """Queue one delivery per target object (already loaded and access-checked).

    One event per object keeps `data` shaped like the object's other
    events, so a template works for both. The condition still applies.
    """
    try:
        webhook = webhooks_db_get(int(webhook_id))
    except (TypeError, ValueError):
        webhook = None
    if (webhook is None or not webhook.enabled or not webhooks_is_manual(hook_name)
            or hook_name not in (webhook.events or [])):
        raise BusinessProcessingError('This webhook cannot be triggered on these objects')

    from app.iris_engine.webhooks.tasks import webhooks_dispatch_task
    actor = webhooks_current_actor()
    for target in targets:
        event = webhooks_build_event(hook_name, target, caseid=caseid, actor=actor)
        webhooks_dispatch_task.delay(event, [webhook.id], TRIGGER_MANUAL)
    return len(targets)


# ---- Legacy module import --------------------------------------------------

def _legacy_module_config(module):
    for parameter in module.module_config or []:
        if isinstance(parameter, dict) and parameter.get('param_name') == 'wh_configuration':
            return parameter.get('value') or parameter.get('default')
    return None


def webhooks_legacy_status() -> dict:
    module = webhooks_db_get_module(LEGACY_MODULE_NAME)
    if module is None:
        return {'installed': False, 'active': False, 'module_id': None, 'webhook_count': 0, 'error': None}
    status = {
        'installed': True,
        'active': bool(module.is_active),
        'module_id': module.id,
        'webhook_count': 0,
        'error': None,
    }
    try:
        config = webhooks_parse_legacy_config(_legacy_module_config(module))
        status['webhook_count'] = len([h for h in config.get('webhooks') or [] if isinstance(h, dict)])
    except ValueError as e:
        status['error'] = str(e)
    return status


def webhooks_legacy_import(user_id) -> dict:
    module = webhooks_db_get_module(LEGACY_MODULE_NAME)
    if module is None:
        raise BusinessProcessingError('The IrisWebHooks module is not installed')
    try:
        config = webhooks_parse_legacy_config(_legacy_module_config(module))
    except ValueError as e:
        raise BusinessProcessingError(str(e))

    created = []
    skipped = []
    existing_names = {webhook.name for webhook in webhooks_db_list()}
    for item in webhooks_convert_legacy(config, webhooks_db_hook_names()):
        body = item['webhook']
        if body['name'] in existing_names:
            # Already imported (or named alike): importing twice must not double deliveries
            skipped.append({'name': body['name'], 'errors': {'name': ['A webhook with this name already exists']},
                            'warnings': item['warnings']})
            continue
        try:
            attributes = _webhooks_validate(body, check_destination=False)
        except BusinessProcessingError as e:
            skipped.append({'name': body['name'], 'errors': e.get_data(), 'warnings': item['warnings']})
            continue
        webhook = Webhook(**attributes)
        webhook.created_by_id = user_id
        db_create(webhook)
        existing_names.add(webhook.name)
        created.append({'id': webhook.id, 'name': webhook.name, 'warnings': item['warnings']})

    legacy_url = (config.get('instance_url') or '').rstrip('/')
    notes = []
    if legacy_url and legacy_url != _instance_url().rstrip('/'):
        notes.append(f'The module used {legacy_url} as the IRIS URL. Native webhooks build links from '
                      f'IRIS_ALLOW_ORIGIN ({_instance_url() or "not set"}).')

    track_activity(f'{len(created)} webhook(s) imported from the IrisWebHooks module', ctx_less=True)
    return {'created': created, 'skipped': skipped, 'notes': notes}
