#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from flask import Blueprint
from flask import request
from marshmallow.exceptions import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.access_controls import ac_api_return_access_denied
from app.business.events import events_convert_date
from app.business.events import events_create
from app.business.events import events_get
from app.business.events import events_get_assets_ids
from app.business.events import events_get_iocs_ids
from app.business.events import events_get_assets_roles
from app.business.events import events_get_iocs_roles
from app.business.events import events_list_filtered
from app.business.events import events_update
from app.business.events import events_delete
from app.business.events import events_set_flag
from app.business.events_search import EVENTS_SEARCH_DEFAULT_LIMIT
from app.business.events_search import events_search
from app.business.events_search import events_search_fields
from app.business.events_search import events_search_histogram_buckets
from app.business.events_search import events_search_parse_date
from app.business.events_search import events_search_vocabulary
from app.models.cases import CasesEvent
from app.models.case_graph import CASE_GRAPH_EVENT_ROLES
from app.schema.marshables import EventSchema
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.business.cases import cases_exists
from app.iris_engine.utils.collab import notify
from app.models.authorization import CaseAccessLevel
from app.iris_engine.module_handler.module_handler import call_deprecated_on_preload_modules_hook
from app.blueprints.iris_user import iris_current_user


class Events:

    def __init__(self):
        self._schema = EventSchema()

    @staticmethod
    def _check_event_and_case_identifier_match(event: CasesEvent, case_identifier):
        if event.case_id != case_identifier:
            raise BusinessProcessingError(f'Event {event.event_id} does not belong to case {case_identifier}')

    def _load(self, request_data, **kwargs):
        event = self._schema.load(request_data, **kwargs)
        event.event_date, event.event_date_wtz = self._schema.validate_date(request_data.get(u'event_date'),
                                                                            request_data.get(u'event_tz'))
        return event

    def list(self, case_identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()
        if not ac_fast_check_current_user_has_case_access(
                case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=case_identifier)

        filters = _read_filter_args(request.args)

        try:
            page = max(1, int(request.args.get('page', 1)))
        except (TypeError, ValueError):
            page = 1

        try:
            per_page = int(request.args.get('per_page', 0))
        except (TypeError, ValueError):
            per_page = 0

        payload = events_list_filtered(case_identifier, filters, page=page, per_page=per_page)
        return response_api_success(payload)

    def create(self, case_identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()
        if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=case_identifier)

        try:
            request_data = call_deprecated_on_preload_modules_hook('event_create', request.get_json(), case_identifier)

            event = self._load(request_data)

            event_category_id = request_data.get('event_category_id')
            event_assets = request_data.get('event_assets')
            event_iocs = request_data.get('event_iocs')
            sync_iocs_assets = request_data.get('event_sync_iocs_assets', False)
            timeline_ids = request_data.get('timeline_ids')

            event_assets_roles = _read_roles(request_data, 'event_assets_roles')
            event_iocs_roles = _read_roles(request_data, 'event_iocs_roles')

            event = events_create(case_identifier, event, event_category_id, event_assets,
                                  event_iocs, sync_iocs_assets, timeline_ids=timeline_ids,
                                  event_assets_roles=event_assets_roles, event_iocs_roles=event_iocs_roles)
            result = self._schema.dump(event)
            _stamp_roles(result, event)
            # Stamp the timeline membership on the response so the SPA
            # doesn't need a second round-trip to know where the event
            # ended up.
            from app.business.case_timelines import get_event_timeline_ids
            result['timeline_ids'] = get_event_timeline_ids(event.event_id)
            notify(case_identifier, 'events', 'updated', event.event_id, object_data=result)

            return response_api_created(result)

        except ValidationError as e:
            return response_api_error('Data error', data=e.normalized_messages())
        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())

    @staticmethod
    def convert_date(case_identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()
        if not ac_fast_check_current_user_has_case_access(
                case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=case_identifier)

        request_data = request.get_json(silent=True) or {}
        date_value = request_data.get('date_value')
        if not isinstance(date_value, str):
            return response_api_error('date_value must be a string')

        try:
            return response_api_success(events_convert_date(date_value))
        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())

    def read(self, case_identifier, identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()

        try:
            event = events_get(identifier)
            self._check_event_and_case_identifier_match(event, case_identifier)
            if not ac_fast_check_current_user_has_case_access(event.case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
                return ac_api_return_access_denied(caseid=event.case_id)

            result = self._schema.dump(event)
            # event_assets / event_iocs are association rows, not model
            # attributes: they're only present on the instance after a
            # schema load (create/update), so a plain read must stamp them.
            result['event_assets'] = events_get_assets_ids(event)
            result['event_iocs'] = events_get_iocs_ids(event)
            _stamp_roles(result, event)
            from app.business.case_timelines import get_event_timeline_ids
            result['timeline_ids'] = get_event_timeline_ids(event.event_id)
            return response_api_success(result)
        except ObjectNotFoundError:
            return response_api_not_found()
        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())

    def update(self, case_identifier, identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()

        try:
            event = events_get(identifier)
            if not ac_fast_check_current_user_has_case_access(event.case_id, [CaseAccessLevel.full_access]):
                return ac_api_return_access_denied(caseid=event.case_id)
            self._check_event_and_case_identifier_match(event, case_identifier)

            request_data = call_deprecated_on_preload_modules_hook('event_update', request.get_json(), case_identifier)

            request_data['event_id'] = event.event_id
            event = self._load(request_data, instance=event)
            event_category_id = request_data.get('event_category_id')
            event_assets = request_data.get('event_assets')
            event_iocs = request_data.get('event_iocs')
            event_sync_iocs_assets = request_data.get('event_sync_iocs_assets')
            timeline_ids = request_data.get('timeline_ids')

            event_assets_roles = _read_roles(request_data, 'event_assets_roles')
            event_iocs_roles = _read_roles(request_data, 'event_iocs_roles')

            event = events_update(event, event_category_id, event_assets, event_iocs,
                                  event_sync_iocs_assets, timeline_ids=timeline_ids,
                                  event_assets_roles=event_assets_roles, event_iocs_roles=event_iocs_roles)

            result = self._schema.dump(event)
            _stamp_roles(result, event)
            from app.business.case_timelines import get_event_timeline_ids
            result['timeline_ids'] = get_event_timeline_ids(event.event_id)
            notify(case_identifier, 'events', 'updated', identifier, object_data=result)

            return response_api_success(result)
        except ObjectNotFoundError:
            return response_api_not_found()
        except ValidationError as e:
            return response_api_error('Data error', data=e.normalized_messages())
        except BusinessProcessingError as e:
            return response_api_error('Data error', data=e.get_data())

    def delete(self, case_identifier, identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()

        try:
            event = events_get(identifier)
            if not ac_fast_check_current_user_has_case_access(event.case_id, [CaseAccessLevel.full_access]):
                return ac_api_return_access_denied(caseid=event.case_id)
            self._check_event_and_case_identifier_match(event, case_identifier)

            events_delete(iris_current_user, event)

            return response_api_deleted()
        except ObjectNotFoundError:
            return response_api_not_found()
        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())


def _read_roles(request_data, field):
    """`{object_id: role}` from the optional `event_assets_roles` /
    `event_iocs_roles` object of the request; None when absent (the
    roles already stored are then kept)."""
    value = request_data.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError(f'{field} must be an object', data={field: 'must be an object'})
    roles = {}
    for object_id, role in value.items():
        try:
            object_id = int(object_id)
        except (TypeError, ValueError):
            raise BusinessProcessingError(f'Invalid id {object_id!r} in {field}',
                                          data={field: f'invalid id {object_id!r}'})
        if role not in CASE_GRAPH_EVENT_ROLES:
            raise BusinessProcessingError(f'Invalid role {role!r} in {field}',
                                          data={field: f'role must be one of {", ".join(CASE_GRAPH_EVENT_ROLES)}'})
        roles[object_id] = role
    return roles


def _stamp_roles(result, event):
    result['event_assets_roles'] = {str(k): v for k, v in events_get_assets_roles(event).items()}
    result['event_iocs_roles'] = {str(k): v for k, v in events_get_iocs_roles(event).items()}


_MULTI_VALUE_FILTER_KEYS = (
    ('asset', 'assets'),
    ('ioc', 'iocs'),
    ('tag', 'tags'),
    ('title', 'titles'),
    ('source', 'sources'),
    ('description', 'descriptions'),
    ('raw', 'raws'),
    ('category', 'categories'),
)


def _read_filter_args(args):
    filters: dict = {}

    for query_key, filter_key in _MULTI_VALUE_FILTER_KEYS:
        values = args.getlist(query_key)
        if values:
            filters[filter_key] = values

    asset_ids = args.getlist('asset_id')
    if asset_ids:
        try:
            filters['assets_id'] = [int(v) for v in asset_ids]
        except (TypeError, ValueError):
            pass

    ioc_ids = args.getlist('ioc_id')
    if ioc_ids:
        try:
            filters['iocs_id'] = [int(v) for v in ioc_ids]
        except (TypeError, ValueError):
            pass

    event_ids = args.getlist('event_id')
    if event_ids:
        try:
            filters['event_ids'] = [int(v) for v in event_ids]
        except (TypeError, ValueError):
            pass

    start_date = args.get('start_date')
    if start_date:
        filters['start_date'] = start_date

    end_date = args.get('end_date')
    if end_date:
        filters['end_date'] = end_date

    flag = args.get('flag')
    if flag is not None:
        filters['flag'] = flag.lower() == 'true'

    return filters


events = Events()
case_events_blueprint = Blueprint('case_events_rest_v2', __name__, url_prefix='/<int:case_identifier>/events')


@case_events_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='List case events')
def list_events(case_identifier):
    return events.list(case_identifier)


@case_events_blueprint.post('')
@ac_api_requires()
@api_doc(request=EventSchema, response=EventSchema, response_shape='created',
         tags=['CaseEvents'], summary='Create a case event')
def create_event(case_identifier):
    return events.create(case_identifier)


@case_events_blueprint.post('/convert-date')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='Parse a free-form date into event date, time and timezone')
def convert_event_date(case_identifier):
    return events.convert_date(case_identifier)


@case_events_blueprint.get('/<int:identifier>')
@ac_api_requires()
@api_doc(response=EventSchema, tags=['CaseEvents'], summary='Get a case event')
def get_event(case_identifier, identifier):
    return events.read(case_identifier, identifier)


@case_events_blueprint.put('/<int:identifier>')
@ac_api_requires()
@api_doc(request=EventSchema, response=EventSchema, tags=['CaseEvents'],
         summary='Update a case event')
def update_event(case_identifier, identifier):
    return events.update(case_identifier, identifier)


@case_events_blueprint.delete('/<int:identifier>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['CaseEvents'], summary='Delete a case event')
def delete_event(case_identifier, identifier):
    return events.delete(case_identifier, identifier)


_SEARCH_QUERY_PARAM_DESCRIPTION = (
    'Lucene-style search expression, the grammar of the alert search bar. '
    '`category:Lateral*`, `asset:dc01`, `ioc:none`, `timeline:"Initial access"`, '
    '`date:[2026-01-01 TO 2026-01-31]`, `author:me`, `is:flagged`. A bare term '
    'searches title, description, source, tags and raw data. GET '
    '/cases/<id>/events/search-schema lists every alias. A malformed expression '
    'returns 400 with the character offset in `data.position`.'
)

_SEARCH_SCOPE_PARAMS = [
    ('query', 'string', _SEARCH_QUERY_PARAM_DESCRIPTION),
    ('start', 'string', 'ISO 8601 lower bound of the event date, inclusive'),
    ('end', 'string', 'ISO 8601 upper bound of the event date, exclusive'),
    ('timeline_id', 'integer', 'Keep events on any of these timelines; repeatable'),
]


def _read_search_scope(args):
    """The scope shared by the search, histogram and fields routes."""
    timeline_ids = []
    for value in args.getlist('timeline_id'):
        try:
            timeline_ids.append(int(value))
        except (TypeError, ValueError):
            raise BusinessProcessingError('timeline_id must be an integer')

    return {
        'query': args.get('query', ''),
        'start': events_search_parse_date(args.get('start'), 'start'),
        'end': events_search_parse_date(args.get('end'), 'end'),
        'timeline_ids': timeline_ids,
        'user_identifier': iris_current_user.id,
    }


def _search_read_access_denied(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(
            case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


# The `int` converter of `/<int:identifier>` cannot match the literal
# segments below, so their declaration order is irrelevant.
@case_events_blueprint.get('/search')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='Search case events, one keyset page at a time',
         query_params=_SEARCH_SCOPE_PARAMS + [
             ('order', 'string', "'asc' (default) or 'desc' on the event date"),
             ('cursor', 'string', 'Opaque `next_cursor` of the previous page'),
             ('limit', 'integer', 'Page size (default 200, at most 1000)'),
             ('full', 'boolean', 'Return descriptions and raw data whole rather than cut (exports)'),
         ])
def search_events_route(case_identifier):
    """Matching events with their links, comment and child counts.

    `total` is set on the first page only. Descriptions and raw data are
    cut at 4096 characters, flagged by `event_content_truncated` and
    `event_raw_truncated`; GET /events/<id> returns them whole, as does
    this route with `full=true`.
    """
    denied = _search_read_access_denied(case_identifier)
    if denied is not None:
        return denied

    try:
        limit = int(request.args.get('limit', EVENTS_SEARCH_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return response_api_error('limit must be an integer')

    order = request.args.get('order', 'asc')
    if order not in ('asc', 'desc'):
        return response_api_error("order must be 'asc' or 'desc'")

    try:
        result = events_search(case_identifier, descending=order == 'desc',
                               cursor=request.args.get('cursor'), limit=limit,
                               full_text=request.args.get('full', '').lower() in ('1', 'true'),
                               **_read_search_scope(request.args))
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())

    return response_api_success(result)


@case_events_blueprint.get('/histogram')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='Count matching case events per date bucket',
         query_params=_SEARCH_SCOPE_PARAMS)
def events_histogram_route(case_identifier):
    """Dense buckets over `[start, end)`, or over the matching events'
    own span when no range is given. The bucket width is picked so the
    span fits in about 120 bars."""
    denied = _search_read_access_denied(case_identifier)
    if denied is not None:
        return denied

    try:
        result = events_search_histogram_buckets(case_identifier, **_read_search_scope(request.args))
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())

    return response_api_success(result)


@case_events_blueprint.get('/fields')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='Most frequent values of the main fields of matching case events',
         query_params=_SEARCH_SCOPE_PARAMS)
def events_fields_route(case_identifier):
    denied = _search_read_access_denied(case_identifier)
    if denied is not None:
        return denied

    try:
        result = events_search_fields(case_identifier, **_read_search_scope(request.args))
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())

    return response_api_success(result)


@case_events_blueprint.get('/search-schema')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='List the fields the event search expression understands')
def events_search_schema_route(case_identifier):
    denied = _search_read_access_denied(case_identifier)
    if denied is not None:
        return denied

    return response_api_success({'fields': events_search_vocabulary()})


@case_events_blueprint.post('/flag')
@ac_api_requires()
@api_doc(tags=['CaseEvents'], summary='Flag or unflag several case events at once')
def flag_events_route(case_identifier):
    """Body: `{"event_ids": [..], "flagged": true}`."""
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_identifier)

    request_data = request.get_json(silent=True) or {}
    flagged = request_data.get('flagged')
    if not isinstance(flagged, bool):
        return response_api_error('flagged must be a boolean')

    try:
        changed = events_set_flag(case_identifier, request_data.get('event_ids'), flagged)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())

    return response_api_success({'changed': changed})
