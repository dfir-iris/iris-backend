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

"""v2 REST routes for the customer-bounded asset registry (Manage > Assets).

Mounted under `/api/v2/manage/managed-assets`::

    GET    /                          list the registry
    POST   /                          create a registry entry
    GET    /<id>                      one entry plus its visible derived facts
    PUT    /<id>                      update metadata
    DELETE /<id>                      delete an entry (audit survives)
    GET    /<id>/sightings            the case / alert observations behind it
    GET    /<id>/timeline             case timeline events referencing it
    GET    /<id>/audit                who changed what, and when
    GET    /audit                     the same, across the whole registry
    POST   /export                    download the registry as CSV or JSON
    POST   /import/inspect            stage an upload, report what it would do
    POST   /import                    apply a staged upload
    DELETE /import/<token>            throw a staged upload away
    POST   /reconcile                 backfill one customer from cases/alerts

Two access decisions run on every request. The permission bit
(`asset_manager_read` / `asset_manager_write`) is checked by
`ac_api_requires`; customer membership is checked separately, because
holding the permission says nothing about *which* customers the caller
may see.

Reads of a single asset answer **404, not 403**, when the caller cannot
see its customer. They have already proven they hold the permission, so
a 403 would confirm that asset #472 exists under a customer they cannot
read — turning the id space into an enumerable map of other tenants'
inventories. Writes that name a customer explicitly (`/import/inspect`,
`/import`, `/reconcile`) answer 403 instead: the customer id came from
the caller, so refusing it discloses nothing they did not already send.

Export is a POST rather than a GET for the same reason as
`blueprints/rest/v2/case_transfer.py`: the filter payload names
customers and hostnames, and a GET would leave that in access logs,
proxies and browser history.
"""

from datetime import datetime

from flask import Blueprint
from flask import Response
from flask import request
from marshmallow.exceptions import ValidationError
from werkzeug.utils import secure_filename

from app import app
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_has_customer_access
from app.blueprints.access_controls import ac_current_user_permissions_mask
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_paginated
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.parsing import parse_boolean
from app.blueprints.rest.parsing import parse_pagination_parameters
from app.iris_engine.demo_builder import demo_mode_over_upload_cap
from app.iris_engine.demo_builder import demo_mode_upload_cap_message
from app.business.managed_assets import managed_assets_audit
from app.business.managed_assets import managed_assets_audit_log
from app.business.managed_assets import managed_assets_create
from app.business.managed_assets import managed_assets_delete
from app.business.managed_assets import managed_assets_export
from app.business.managed_assets import managed_assets_get
from app.business.managed_assets import managed_assets_reconcile
from app.business.managed_assets import managed_assets_search
from app.business.managed_assets import managed_assets_sightings
from app.business.managed_assets import managed_assets_summary
from app.business.managed_assets import managed_assets_timeline
from app.business.managed_assets import managed_assets_timeline_count
from app.business.managed_assets import managed_assets_update
from app.business.managed_assets import managed_assets_viewer_scope
from app.business.managed_assets_transfer.exporter import EXPORT_FORMATS
from app.business.managed_assets_transfer.exporter import export_assets
from app.business.managed_assets_transfer.importer import CONFLICT_POLICIES
from app.business.managed_assets_transfer.importer import IMPORT_FORMATS
from app.business.managed_assets_transfer.importer import apply_staged
from app.business.managed_assets_transfer.importer import discard_staged
from app.business.managed_assets_transfer.importer import inspect_upload
from app.business.managed_assets_transfer.importer import staged_metadata
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.managed_assets import MANAGED_ASSET_SORTABLE_FIELDS
from app.models.pagination_parameters import PaginationParameters
from app.schema.marshables import ManagedAssetAuditSchema
from app.schema.marshables import ManagedAssetDetailSchema
from app.schema.marshables import ManagedAssetImportReportSchema
from app.schema.marshables import ManagedAssetSightingSchema
from app.schema.marshables import ManagedAssetTimelineSchema

# Server-owned or identity-bearing fields. `client_id` and
# `asset_type_id` are part of the dedup identity together with the name:
# letting a PUT move either one would silently re-home the asset under a
# different customer and orphan every audit entry pointing at it.
# `normalized_name` is derived, never supplied.
_MANAGED_ASSET_READONLY_FIELDS = frozenset({
    'managed_asset_id',
    'managed_asset_uuid',
    'client_id',
    'asset_type_id',
    'normalized_name',
    'created_at',
    'created_by',
    'updated_at',
    'updated_by',
})

# Identity is settable exactly once, at create time. Everything else in
# the readonly set is server-owned at all times.
_CREATE_READONLY_FIELDS = _MANAGED_ASSET_READONLY_FIELDS - {'client_id', 'asset_type_id'}

_SIGHTING_KINDS = ('case', 'alert')
_UPLOAD_FIELD = 'file'

# `parse_pagination_parameters` applies no ceiling at all, so
# `?per_page=1000000` is accepted verbatim today. Clamp here rather than
# in the shared parser — every other endpoint would inherit the change.
_MAX_PER_PAGE = app.config.get('MANAGED_ASSETS_MAX_PER_PAGE', 100)
_MAX_SIGHTINGS_PER_PAGE = 200


class _RequestError(Exception):
    """A malformed query string or body, reported as a 400."""


def _caller():
    """The `(user, permissions)` pair the business scope helper expects.

    `ac_current_user_permissions_mask` rather than `session['permissions']`
    so the API-key auth path — which keeps the mask on `flask.g` —
    resolves to the caller's true permissions.
    """
    return iris_current_user, ac_current_user_permissions_mask()


def _scope():
    return managed_assets_viewer_scope(*_caller())


def _strip_readonly(payload, readonly=_MANAGED_ASSET_READONLY_FIELDS):
    if not isinstance(payload, dict):
        return payload
    return {k: v for k, v in payload.items() if k not in readonly}


def _int_list(name):
    """Repeated integer query parameter, rejecting anything non-integer.

    `request.args.getlist(name, type=int)` drops unparseable values
    silently, which would turn `?client_id=abc` into "no customer
    filter" — a wider result set than the caller asked for.
    """
    values = []
    for raw in request.args.getlist(name):
        try:
            values.append(int(raw))
        except ValueError:
            raise _RequestError(f'Invalid integer value for "{name}": {raw}')
    return values


def _optional_boolean(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        return parse_boolean(raw)
    except ValueError:
        raise _RequestError(f'Invalid boolean value for "{name}": {raw}')


def _request_filters():
    return {
        'client_id': _int_list('client_id'),
        'asset_type_id': _int_list('asset_type_id'),
        'criticality': request.args.getlist('criticality'),
        'environment': request.args.getlist('environment'),
        'tag': request.args.getlist('tag'),
        'owner': request.args.get('owner'),
        'search': request.args.get('search'),
        'is_active': _optional_boolean('is_active'),
        'has_sightings': _optional_boolean('has_sightings'),
        'compromised': _optional_boolean('compromised'),
    }


def _body_filters(body):
    """Filters supplied in an export body, in the shape datamgmt expects."""
    filters = body.get('filters')
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise _RequestError('"filters" must be an object')

    def _as_list(key):
        value = filters.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise _RequestError(f'"{key}" must be a list')
        return value

    def _as_boolean(key):
        value = filters.get(key)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise _RequestError(f'"{key}" must be a boolean')
        return value

    return {
        'client_id': _as_list('client_id'),
        'asset_type_id': _as_list('asset_type_id'),
        'criticality': _as_list('criticality'),
        'environment': _as_list('environment'),
        'tag': _as_list('tag'),
        'owner': filters.get('owner'),
        'search': filters.get('search'),
        'is_active': _as_boolean('is_active'),
        'has_sightings': _as_boolean('has_sightings'),
        'compromised': _as_boolean('compromised'),
    }


def _pagination(default_order_by, max_per_page):
    """Pagination parameters with an allowlisted sort and a clamped size.

    `paginate` in `datamgmt/filtering.py` guards ordering with
    `hasattr(model, order_by)`, which also matches `query`, `metadata`
    and every relationship — `?order_by=query` is a 500 today. Reject
    unknown sort keys here instead of widening that helper.
    """
    parameters = parse_pagination_parameters(request, default_order_by=default_order_by)

    order_by = parameters.get_order_by()
    if order_by and order_by not in MANAGED_ASSET_SORTABLE_FIELDS:
        raise _RequestError(f'Cannot sort on "{order_by}"')

    if parameters.get_page() < 1:
        raise _RequestError('Page must be 1 or greater')
    if parameters.get_per_page() < 1:
        raise _RequestError('per_page must be 1 or greater')

    # `PaginationParameters` is immutable, so clamping means rebuilding it.
    return PaginationParameters(
        parameters.get_page(),
        min(parameters.get_per_page(), max_per_page),
        order_by,
        parameters.get_direction(),
    )


def _decorate(assets, scope, timeline_counts=None):
    """Attach the visible-only derived facts to each row before dumping.

    They are plain Python attributes on the mapped instance — unmapped,
    so nothing is persisted — which lets one schema serialise both the
    stored columns and the computed ones.
    """
    summary = managed_assets_summary(assets, scope)
    for asset in assets:
        for field, value in summary[asset.managed_asset_id].items():
            setattr(asset, field, value)
        asset.timeline_event_count = (timeline_counts or {}).get(asset.managed_asset_id)
    return assets


def _scope_envelope(scope):
    """The caller-level disclosure flag carried by every read response.

    `restricted` is a property of the *reader*, not of any asset, so it
    discloses nothing: it tells the UI that sighting counts and
    timestamps cover only what this reader can open, and lets it say so
    rather than presenting a partial count as a total.
    """
    return {'restricted': scope.is_restricted()}


def _paginated_with_scope(schema, pagination, scope):
    """The standard paginated envelope plus the caller's scope flag.

    `response_api_paginated` builds and serialises the body in one step,
    and its return value is an already-encoded `Response` — mutating
    `response.json` would change a parsed copy, not the bytes on the
    wire. So the envelope is rebuilt here. Keep the five standard keys
    byte-identical to the shared helper; only `scope` is additional.
    """
    return response_api_success({
        'total': pagination.total,
        'data': schema.dump(pagination.items, many=True),
        'last_page': pagination.pages,
        'current_page': pagination.page,
        'next_page': pagination.next_num if pagination.has_next else None,
        'scope': _scope_envelope(scope),
    })


def _export_filename(export_format):
    stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    return secure_filename(f'managed-assets-{stamp}.{export_format}')


class ManagedAssetsOperations:

    def __init__(self):
        self._schema = ManagedAssetDetailSchema()
        self._sighting_schema = ManagedAssetSightingSchema()
        self._timeline_schema = ManagedAssetTimelineSchema()
        self._audit_schema = ManagedAssetAuditSchema()
        self._import_schema = ManagedAssetImportReportSchema()

    def list(self):
        scope = _scope()
        try:
            pagination_parameters = _pagination('name', _MAX_PER_PAGE)
            filters = _request_filters()
        except _RequestError as exception:
            return response_api_error(str(exception))

        pagination = managed_assets_search(scope, filters, pagination_parameters)
        _decorate(pagination.items, scope)

        return _paginated_with_scope(self._schema, pagination, scope)

    def create(self):
        user, _ = _caller()
        payload = _strip_readonly(request.get_json(silent=True) or {},
                                  readonly=_CREATE_READONLY_FIELDS)

        # Membership is checked before the schema runs: a caller who
        # cannot see the customer gets a flat refusal rather than a
        # validation report describing that customer's asset types.
        if not ac_current_user_has_customer_access(payload.get('client_id')):
            return ac_api_return_access_denied()

        try:
            asset = self._schema.load(payload)
        except ValidationError as exception:
            return response_api_error('Data error', data=exception.messages)

        try:
            managed_assets_create(asset, user)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        _decorate([asset], _scope())
        return response_api_created(self._schema.dump(asset))

    def read(self, identifier):
        scope = _scope()
        try:
            asset = managed_assets_get(identifier, scope)
        except ObjectNotFoundError:
            return response_api_not_found()

        counts = {asset.managed_asset_id: managed_assets_timeline_count(asset, scope)}
        _decorate([asset], scope, timeline_counts=counts)

        payload = self._schema.dump(asset)
        payload['scope'] = _scope_envelope(scope)
        return response_api_success(payload)

    def update(self, identifier):
        user, _ = _caller()
        scope = _scope()
        try:
            asset = managed_assets_get(identifier, scope)
        except ObjectNotFoundError:
            return response_api_not_found()

        payload = _strip_readonly(request.get_json(silent=True) or {})
        try:
            # partial=True — the SPA sends only edited fields, and the
            # validators still fire on everything that IS present.
            self._schema.load(payload, instance=asset, partial=True)
        except ValidationError as exception:
            return response_api_error('Data error', data=exception.messages)

        try:
            managed_assets_update(asset, user)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        _decorate([asset], scope)
        return response_api_success(self._schema.dump(asset))

    def delete(self, identifier):
        user, _ = _caller()
        try:
            asset = managed_assets_get(identifier, _scope())
        except ObjectNotFoundError:
            return response_api_not_found()

        try:
            managed_assets_delete(asset, user)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        return response_api_deleted()

    def sightings(self, identifier):
        scope = _scope()
        try:
            asset = managed_assets_get(identifier, scope)
        except ObjectNotFoundError:
            return response_api_not_found()

        kind = request.args.get('kind')
        if kind is not None and kind not in _SIGHTING_KINDS:
            return response_api_error(f'Unknown sighting kind "{kind}"')

        try:
            pagination_parameters = _pagination(None, _MAX_SIGHTINGS_PER_PAGE)
        except _RequestError as exception:
            return response_api_error(str(exception))

        pagination = managed_assets_sightings(asset, scope, kind, pagination_parameters)

        return _paginated_with_scope(self._sighting_schema, pagination, scope)

    def timeline(self, identifier):
        scope = _scope()
        try:
            asset = managed_assets_get(identifier, scope)
        except ObjectNotFoundError:
            return response_api_not_found()

        try:
            pagination_parameters = _pagination(None, _MAX_SIGHTINGS_PER_PAGE)
        except _RequestError as exception:
            return response_api_error(str(exception))

        pagination = managed_assets_timeline(asset, scope, pagination_parameters)

        return _paginated_with_scope(self._timeline_schema, pagination, scope)

    def audit(self, identifier):
        scope = _scope()
        try:
            asset = managed_assets_get(identifier, scope)
        except ObjectNotFoundError:
            return response_api_not_found()

        try:
            pagination_parameters = _pagination(None, _MAX_SIGHTINGS_PER_PAGE)
        except _RequestError as exception:
            return response_api_error(str(exception))

        pagination = managed_assets_audit(asset, pagination_parameters)
        return response_api_paginated(self._audit_schema, pagination)

    def audit_log(self):
        scope = _scope()
        try:
            pagination_parameters = _pagination(None, _MAX_SIGHTINGS_PER_PAGE)
            client_filter = _int_list('client_id')
        except _RequestError as exception:
            return response_api_error(str(exception))

        pagination = managed_assets_audit_log(scope, client_filter, pagination_parameters)
        return response_api_paginated(self._audit_schema, pagination)

    @staticmethod
    def export():
        scope = _scope()
        body = request.get_json(silent=True) or {}

        export_format = body.get('format', 'csv')
        if export_format not in EXPORT_FORMATS:
            return response_api_error(f'Unsupported export format "{export_format}"')

        try:
            filters = _body_filters(body)
        except _RequestError as exception:
            return response_api_error(str(exception))

        limit = app.config.get('MANAGED_ASSETS_MAX_EXPORT_ROWS', 50000)
        try:
            assets = managed_assets_export(scope, filters, limit)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        payload, content_type, extension = export_assets(assets, export_format)

        # `nosniff` matters here: without it a browser may decide a CSV
        # of attacker-influenced hostnames is HTML and render it in the
        # IRIS origin. `attachment` keeps it out of the tab entirely.
        return Response(
            payload,
            mimetype=content_type,
            headers={
                'Content-Type': content_type,
                'Content-Disposition': f'attachment; filename="{_export_filename(extension)}"',
                'X-Content-Type-Options': 'nosniff',
            },
        )

    def import_inspect(self):
        uploaded = request.files.get(_UPLOAD_FIELD)
        if uploaded is None:
            return response_api_error(f'No file supplied — expected a `{_UPLOAD_FIELD}` file field')

        if demo_mode_over_upload_cap(uploaded):
            return response_api_error(demo_mode_upload_cap_message())

        try:
            client_id = int(request.form.get('client_id', ''))
        except (TypeError, ValueError):
            return response_api_error('A valid customer id is required')

        if not ac_current_user_has_customer_access(client_id):
            return ac_api_return_access_denied()

        import_format = request.form.get('format', 'csv')
        if import_format not in IMPORT_FORMATS:
            return response_api_error(f'Unsupported import format "{import_format}"')

        try:
            report = inspect_upload(uploaded.stream, owner_id=iris_current_user.id,
                                    client_id=client_id, import_format=import_format)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        return response_api_success(self._import_schema.dump(report))

    def import_apply(self):
        user, _ = _caller()
        body = request.get_json(silent=True) or {}

        token = body.get('staging_token')
        if not token:
            return response_api_error('No staging token supplied')

        on_conflict = body.get('on_conflict', 'skip')
        if on_conflict not in CONFLICT_POLICIES:
            return response_api_error(f'Unknown conflict policy "{on_conflict}"')

        # Re-authorise against the customer *pinned at inspect*, not one
        # named in this body. Group membership can be revoked between the
        # two calls, and the apply must not honour access the caller had
        # when they uploaded but no longer has.
        try:
            metadata = staged_metadata(token, owner_id=iris_current_user.id)
        except ObjectNotFoundError:
            return response_api_not_found()

        if not ac_current_user_has_customer_access(metadata.get('client_id')):
            return ac_api_return_access_denied()

        try:
            report = apply_staged(token, owner_id=iris_current_user.id,
                                  on_conflict=on_conflict, user=user)
        except ObjectNotFoundError:
            return response_api_not_found()
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        return response_api_success(self._import_schema.dump(report))

    @staticmethod
    def import_discard(token):
        try:
            discard_staged(token, owner_id=iris_current_user.id)
        except ObjectNotFoundError:
            return response_api_not_found()

        return response_api_deleted()

    @staticmethod
    def reconcile():
        user, _ = _caller()
        body = request.get_json(silent=True) or {}

        try:
            client_id = int(body.get('client_id'))
        except (TypeError, ValueError):
            return response_api_error('A valid customer id is required')

        if not ac_current_user_has_customer_access(client_id):
            return ac_api_return_access_denied()

        try:
            created = managed_assets_reconcile(client_id, user)
        except BusinessProcessingError as exception:
            return response_api_error(exception.get_message(), data=exception.get_data())

        return response_api_success({'created': created})


managed_assets_blueprint = Blueprint('managed_assets_rest_v2', __name__,
                                     url_prefix='/managed-assets')

managed_assets_operations = ManagedAssetsOperations()


@managed_assets_blueprint.get('')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetDetailSchema, response_shape='paginated',
         tags=['ManagedAssets'],
         # Spelled out rather than referenced from `_LIST_QUERY_PARAMS`:
         # `scripts/generate_openapi.py` reads this decorator with `ast`
         # and cannot resolve a name, an f-string or a `join()`.
         query_params=[('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer', 'Rows per page (clamped server-side)'),
                       ('order_by', 'string',
                        'One of: created_at, criticality, domain, environment, ip, is_active, '
                        'location, managed_asset_id, name, owner, source, updated_at'),
                       ('sort_dir', 'string', 'asc or desc'),
                       ('client_id', 'integer[]', 'Restrict to these customers'),
                       ('asset_type_id', 'integer[]', 'Restrict to these asset types'),
                       ('criticality', 'string[]', 'Restrict to these criticalities'),
                       ('environment', 'string[]', 'Restrict to these environments'),
                       ('tag', 'string[]',
                        'Substring match on the tags field, one filter per tag'),
                       ('owner', 'string', 'Substring match on the owner field'),
                       ('search', 'string',
                        'Substring match on name, ip, domain and description'),
                       ('is_active', 'boolean', 'Only active or only retired assets'),
                       ('has_sightings', 'boolean',
                        'Only assets with (or without) visible sightings'),
                       ('compromised', 'boolean',
                        'Only assets seen compromised in a visible sighting')],
         summary='List registry assets visible to the caller')
def list_managed_assets_route():
    """List the asset registry across every customer the caller can see.

    Assets are deduplicated per customer on a normalized name, so
    `SRV-DC01` and `srv-dc01` are one entry here even though they remain
    two separate rows inside their individual cases.
    """
    return managed_assets_operations.list()


@managed_assets_blueprint.post('')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(request=ManagedAssetDetailSchema, response=ManagedAssetDetailSchema,
         response_shape='created', tags=['ManagedAssets'],
         summary='Create a registry asset')
def create_managed_asset_route():
    return managed_assets_operations.create()


@managed_assets_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetDetailSchema, tags=['ManagedAssets'],
         summary='Get a registry asset and its visible sighting summary')
def get_managed_asset_route(identifier):
    return managed_assets_operations.read(identifier)


@managed_assets_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(request=ManagedAssetDetailSchema, response=ManagedAssetDetailSchema,
         tags=['ManagedAssets'], summary='Update a registry asset')
def put_managed_asset_route(identifier):
    """Update the metadata of a registry asset.

    `client_id`, `asset_type_id` and `normalized_name` are ignored if
    supplied: they form the dedup identity and changing one would
    orphan the asset's audit trail.
    """
    return managed_assets_operations.update(identifier)


@managed_assets_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(response_shape='deleted', tags=['ManagedAssets'],
         summary='Delete a registry asset')
def delete_managed_asset_route(identifier):
    """Delete a registry asset. Its audit history survives the deletion."""
    return managed_assets_operations.delete(identifier)


@managed_assets_blueprint.get('/<int:identifier>/sightings')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetSightingSchema, response_shape='paginated',
         tags=['ManagedAssets'],
         query_params=[('kind', 'string', 'Restrict to "case" or "alert" sightings'),
                       ('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer',
                        f'Rows per page (clamped to {_MAX_SIGHTINGS_PER_PAGE})')],
         summary='List the case and alert observations behind a registry asset')
def get_managed_asset_sightings_route(identifier):
    """Only observations the caller may read are returned."""
    return managed_assets_operations.sightings(identifier)


@managed_assets_blueprint.get('/<int:identifier>/timeline')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetTimelineSchema, response_shape='paginated',
         tags=['ManagedAssets'],
         query_params=[('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer',
                        f'Rows per page (clamped to {_MAX_SIGHTINGS_PER_PAGE})')],
         summary='List case timeline events referencing a registry asset')
def get_managed_asset_timeline_route(identifier):
    """Only events in cases the caller may read are returned."""
    return managed_assets_operations.timeline(identifier)


@managed_assets_blueprint.get('/<int:identifier>/audit')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetAuditSchema, response_shape='paginated',
         tags=['ManagedAssets'],
         query_params=[('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer',
                        f'Rows per page (clamped to {_MAX_SIGHTINGS_PER_PAGE})')],
         summary='List the change history of a registry asset')
def get_managed_asset_audit_route(identifier):
    return managed_assets_operations.audit(identifier)


@managed_assets_blueprint.get('/audit')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(response=ManagedAssetAuditSchema, response_shape='paginated',
         tags=['ManagedAssets'],
         query_params=[('client_id', 'integer[]', 'Restrict to these customers'),
                       ('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer', 'Rows per page (clamped server-side)')],
         summary='List the change history of the whole visible registry')
def get_managed_assets_audit_log_route():
    """The change log across every customer the caller can read.

    Deletions are only visible here: an entry's asset id is nulled when
    the asset goes, so the per-asset endpoint has nothing left to look
    up. Entries carry the asset name and the acting login as snapshots
    so they stay readable after both are gone.
    """
    return managed_assets_operations.audit_log()


@managed_assets_blueprint.post('/export')
@ac_api_requires(Permissions.asset_manager_read)
@api_doc(tags=['ManagedAssets'],
         summary='Export the visible registry as a CSV or JSON attachment')
def export_managed_assets_route():
    """Download the registry as a file.

    Body (JSON, all optional):
        format   str   "csv" (default) or "json"
        filters  dict  same keys as the list endpoint's query string
    """
    return managed_assets_operations.export()


@managed_assets_blueprint.post('/import/inspect')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(response=ManagedAssetImportReportSchema, tags=['ManagedAssets'],
         summary='Upload a registry file and report what importing it would do')
def inspect_managed_assets_import_route():
    """Stage an uploaded file without writing anything.

    Multipart form:
        file       file  a CSV or JSON registry export
        client_id  int   the customer the rows belong to
        format     str   "csv" (default) or "json"

    Only flat text is accepted — no archives — so there is no
    decompression step and no archive-bomb class to defend against.
    """
    return managed_assets_operations.import_inspect()


@managed_assets_blueprint.post('/import')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(response=ManagedAssetImportReportSchema, tags=['ManagedAssets'],
         summary='Apply a previously staged registry import')
def apply_managed_assets_import_route():
    """Write the staged rows into the registry, in one transaction.

    Body (JSON):
        staging_token  str  from `/import/inspect`
        on_conflict    str  "skip" (default) or "update"

    Writes only to the registry: no case or alert asset is created.
    """
    return managed_assets_operations.import_apply()


@managed_assets_blueprint.delete('/import/<token>')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(response_shape='deleted', tags=['ManagedAssets'],
         summary='Discard a staged registry import')
def discard_managed_assets_import_route(token):
    return managed_assets_operations.import_discard(token)


@managed_assets_blueprint.post('/reconcile')
@ac_api_requires(Permissions.asset_manager_write)
@api_doc(tags=['ManagedAssets'],
         summary='Backfill one customer registry from its cases and alerts')
def reconcile_managed_assets_route():
    """Register any case or alert asset missing from a customer's registry.

    Body (JSON):
        client_id  int  the customer to reconcile

    The registry is fed automatically as cases and alerts are created;
    this repairs the only way it can drift — a missing row — after an
    import, a restore, or a failed ingest hook.
    """
    return managed_assets_operations.reconcile()
