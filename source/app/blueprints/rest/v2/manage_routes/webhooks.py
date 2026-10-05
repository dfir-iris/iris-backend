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

"""v2 endpoints for native webhooks. Server administrators only.

Secrets (secret headers / query params, auth secret, signing secret)
are write-only: responses carry `has_value` / `has_auth_secret` /
`has_signing_secret` instead, and a write that omits them keeps the
stored value. See `app.business.webhooks` for the exact rules.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.webhooks import webhooks_create
from app.business.webhooks import webhooks_delete
from app.business.webhooks import webhooks_events
from app.business.webhooks import webhooks_get_delivery
from app.business.webhooks import webhooks_get_public
from app.business.webhooks import webhooks_legacy_import
from app.business.webhooks import webhooks_legacy_status
from app.business.webhooks import webhooks_list
from app.business.webhooks import webhooks_list_deliveries
from app.business.webhooks import webhooks_preview
from app.business.webhooks import webhooks_redeliver
from app.business.webhooks import webhooks_settings
from app.business.webhooks import webhooks_test
from app.business.webhooks import webhooks_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


def _sandbox_request():
    """`(config, webhook_id, event)` from a preview / test request body."""
    body = _body()
    config = body.get('webhook')
    return (config if isinstance(config, dict) else {}), body.get('webhook_id'), body.get('event')


webhooks_blueprint = Blueprint('webhooks_rest_v2', __name__, url_prefix='/webhooks')


@webhooks_blueprint.get('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='List webhooks with their last delivery and 24h delivery counts')
def list_webhooks_route():
    return _handle(webhooks_list)


@webhooks_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['ManageWebhooks'], summary='Create a webhook')
def create_webhook_route():
    body = _body()
    return _handle(lambda: webhooks_create(body, iris_current_user.id), created=True)


@webhooks_blueprint.get('/events')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='List the events a webhook can subscribe to')
def list_webhook_events_route():
    return _handle(webhooks_events)


@webhooks_blueprint.get('/settings')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Server settings that affect webhooks')
def get_webhook_settings_route():
    return _handle(webhooks_settings)


@webhooks_blueprint.post('/preview')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Render the request a webhook configuration would send, without sending it')
def preview_webhook_route():
    config, webhook_id, event = _sandbox_request()
    return _handle(lambda: webhooks_preview(config, webhook_id=webhook_id, event_name=event))


@webhooks_blueprint.post('/test')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Send a sample event with a webhook configuration and return the response')
def test_webhook_route():
    config, webhook_id, event = _sandbox_request()
    return _handle(lambda: webhooks_test(config, webhook_id=webhook_id, event_name=event))


@webhooks_blueprint.get('/legacy-module')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='State of the IrisWebHooks module configuration')
def get_webhooks_legacy_module_route():
    return _handle(webhooks_legacy_status)


@webhooks_blueprint.post('/legacy-module/import')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Import the IrisWebHooks module configuration as disabled native webhooks')
def import_webhooks_legacy_module_route():
    return _handle(lambda: webhooks_legacy_import(iris_current_user.id))


@webhooks_blueprint.get('/deliveries/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Get a webhook delivery with its request and response')
def get_webhook_delivery_route(identifier):
    return _handle(lambda: webhooks_get_delivery(identifier))


@webhooks_blueprint.post('/deliveries/<int:identifier>/redeliver')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['ManageWebhooks'], summary='Queue a new delivery of the same payload')
def redeliver_webhook_delivery_route(identifier):
    return _handle(lambda: webhooks_redeliver(identifier), created=True)


@webhooks_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Get a webhook')
def get_webhook_route(identifier):
    return _handle(lambda: webhooks_get_public(identifier))


@webhooks_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='Update a webhook (fields left out keep their value)')
def put_webhook_route(identifier):
    body = _body()
    return _handle(lambda: webhooks_update(identifier, body))


@webhooks_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageWebhooks'], summary='Delete a webhook and its delivery log')
def delete_webhook_route(identifier):
    try:
        webhooks_delete(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()


@webhooks_blueprint.get('/<int:identifier>/deliveries')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageWebhooks'], summary='List the deliveries of a webhook, newest first')
def list_webhook_deliveries_route(identifier):
    args = request.args
    return _handle(lambda: webhooks_list_deliveries(
        identifier,
        status=args.get('status') or None,
        event=args.get('event') or None,
        page=args.get('page', 1, type=int),
        per_page=args.get('per_page', 25, type=int),
    ))
