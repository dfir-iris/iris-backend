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

"""Investigation graph of a case: the derived + stored graph, its
exploration, the stored edges, the free nodes, paste, the saved views and
import / export.

Mounted under `/api/v2/cases/<case_identifier>/graph`. Reads need read
access to the case, writes full access."""

from flask import Blueprint
from flask import request
from marshmallow.exceptions import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.case_graph import case_graph_edge_create
from app.business.case_graph import case_graph_edge_delete
from app.business.case_graph import case_graph_edge_review
from app.business.case_graph import case_graph_edge_reverse
from app.business.case_graph import case_graph_edge_timeline_event_links
from app.business.case_graph import case_graph_edge_timeline_events
from app.business.case_graph import case_graph_edge_update
from app.business.case_graph import case_graph_export
from app.business.case_graph import case_graph_get
from app.business.case_graph import case_graph_import
from app.business.case_graph import case_graph_neighbours
from app.business.case_graph import case_graph_node_create
from app.business.case_graph import case_graph_node_delete
from app.business.case_graph import case_graph_node_get
from app.business.case_graph import case_graph_node_promote
from app.business.case_graph import case_graph_node_update
from app.business.case_graph import case_graph_paste
from app.business.case_graph import case_graph_paths
from app.business.case_graph import case_graph_gaps
from app.business.case_graph import case_graph_suggestions
from app.business.case_graph import case_graph_suggestions_accept
from app.business.case_graph import case_graph_suggestions_dismiss
from app.business.case_graph import case_graph_view_create
from app.business.case_graph import case_graph_view_delete
from app.business.case_graph import case_graph_view_get
from app.business.case_graph import case_graph_view_update
from app.business.case_graph import case_graph_views_list
from app.business.case_graph_ai import CaseGraphAiUnavailableError
from app.business.case_graph_ai import case_graph_ai_explain
from app.business.case_graph_ai import case_graph_ai_suggest
from app.business.case_graph_assembly import case_graph_parse_include
from app.business.cases import cases_exists
from app.iris_engine.module_handler.module_handler import call_deprecated_on_preload_modules_hook
from app.iris_engine.utils.collab import notify
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import CaseAssetsSchema
from app.schema.marshables import EventSchema
from app.schema.marshables import IocSchemaForAPIV2


case_graph_blueprint = Blueprint('case_graph_rest_v2', __name__, url_prefix='/<int:case_identifier>/graph')

_INCLUDE_PARAM = ('include', str, 'Comma list among events, evidences, tasks, notes, alerts, cross_case, '
                                  'suggestions (default: assets, IOCs and free nodes only)')
_REVIEW_STATE_PARAM = ('review_state', str, 'accepted (default) or all (adds pending and rejected stored edges)')


def _require_read_access(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(
        case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    ):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


def _require_full_access(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


def _readable_case_ids(case_ids):
    """Ids among `case_ids` the current user may read (cross-case nodes)."""
    granted = ac_fast_check_current_user_has_cases_access(
        case_ids, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
    return set(granted or {})


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except ValidationError as e:
        return response_api_error('Data error', data=e.normalized_messages())
    except CaseGraphAiUnavailableError as e:
        return response_api_error(e.get_message(), data=e.get_data(), status=409)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


def _handle_deleted(operation):
    try:
        operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()


def _query_int(name, default, minimum, maximum):
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BusinessProcessingError(f'{name} must be an integer')
    if not minimum <= value <= maximum:
        raise BusinessProcessingError(f'{name} must be between {minimum} and {maximum}')
    return value


def _query_bool(name, default):
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default
    return raw.strip().lower() in ('1', 'true', 'yes')


def _include():
    return case_graph_parse_include(request.args.get('include'))


def _review_state():
    return request.args.get('review_state') or 'accepted'


# ----------------------------------------------------------------- Graph --

@case_graph_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Get the investigation graph of a case (derived and stored edges)',
         query_params=[_INCLUDE_PARAM, _REVIEW_STATE_PARAM])
def get_case_graph(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_get(case_identifier, _include(), _review_state(), _readable_case_ids))


@case_graph_blueprint.get('/neighbours')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Nodes and edges within 1 to 3 hops of a node',
         query_params=[('node', str, 'Key of the node, e.g. asset:12'), ('hops', int, '1 (default) to 3'),
                       _INCLUDE_PARAM, _REVIEW_STATE_PARAM])
def get_case_graph_neighbours(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err

    def _operation():
        node = request.args.get('node')
        if not node:
            raise BusinessProcessingError('node is required')
        hops = _query_int('hops', 1, 1, 3)
        return case_graph_neighbours(case_identifier, node, hops, _include(), _review_state(), _readable_case_ids)
    return _handle(_operation)


@case_graph_blueprint.get('/paths')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Simple paths between two nodes, shortest first',
         query_params=[('source', str, 'Key of the start node'), ('target', str, 'Key of the end node'),
                       ('directed', bool, 'Follow the edge directions (default true)'),
                       ('max_paths', int, '1 to 50, default 5'), ('max_length', int, '1 to 12, default 8'),
                       _INCLUDE_PARAM, _REVIEW_STATE_PARAM])
def get_case_graph_paths(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err

    def _operation():
        source = request.args.get('source')
        target = request.args.get('target')
        if not source or not target:
            raise BusinessProcessingError('source and target are required')
        return case_graph_paths(case_identifier, source, target,
                                directed=_query_bool('directed', True),
                                max_paths=_query_int('max_paths', 5, 1, 50),
                                max_length=_query_int('max_length', 8, 1, 12),
                                include=_include(), review_state=_review_state(),
                                readable_case_ids_resolver=_readable_case_ids)
    return _handle(_operation)


# ----------------------------------------------------------------- Edges --

@case_graph_blueprint.get('/edges/timeline-events')
@ac_api_requires()
@api_doc(tags=['Case graph'],
         summary='Timeline events already recording a link between two nodes (linked to both, or cited by '
                 'a stored edge between them)',
         query_params=[('source', str, 'Key of one node'), ('target', str, 'Key of the other node')])
def get_case_graph_edge_timeline_events(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err

    def _operation():
        source = request.args.get('source')
        target = request.args.get('target')
        if not source or not target:
            raise BusinessProcessingError('source and target are required')
        return case_graph_edge_timeline_events(case_identifier, source, target)

    return _handle(_operation)


def _timeline_event(case_identifier, body):
    """The `timeline_event` of an edge creation request (event fields:
    `event_title`, `event_date`, `event_tz`, `event_category_id`,
    optional `event_content`…), loaded as the event routes do, its links
    to the edge endpoints set by the graph. None when absent."""
    fields = body.get('timeline_event')
    if fields is None:
        return None
    if not isinstance(fields, dict):
        raise BusinessProcessingError('timeline_event must be an object')
    links = case_graph_edge_timeline_event_links(case_identifier, body)
    data = dict(fields, **links)
    data.setdefault('event_source', 'Investigation graph')
    data.setdefault('event_in_graph', True)
    data = call_deprecated_on_preload_modules_hook('event_create', data, case_identifier)
    schema = EventSchema()
    event = schema.load(data)
    event.event_date, event.event_date_wtz = schema.validate_date(data.get('event_date'), data.get('event_tz'))
    return dict(links, event=event, category_id=data.get('event_category_id'))


@case_graph_blueprint.post('/edges')
@ac_api_requires()
@api_doc(response_shape='created', tags=['Case graph'],
         summary='Create a stored edge between two nodes of the case; with `timeline_event` (event fields), '
                 'also record it as a timeline event linking the endpoints, cited by the edge')
def create_case_graph_edge(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()

    def _operation():
        result = case_graph_edge_create(case_identifier, iris_current_user.id, body,
                                        timeline_event=_timeline_event(case_identifier, body))
        if result.get('timeline_event_id'):
            notify(case_identifier, 'events', 'updated', result['timeline_event_id'])
        return result

    return _handle(_operation, created=True)


@case_graph_blueprint.put('/edges/<int:edge_id>')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Update a stored edge (fields left out keep their value)')
def update_case_graph_edge(case_identifier, edge_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_edge_update(case_identifier, iris_current_user.id, edge_id, body))


@case_graph_blueprint.delete('/edges/<int:edge_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['Case graph'], summary='Delete a stored edge')
def delete_case_graph_edge(case_identifier, edge_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_graph_edge_delete(case_identifier, iris_current_user.id, edge_id))


@case_graph_blueprint.post('/edges/<int:edge_id>/review')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Accept or reject a stored edge ({decision: accept|reject})')
def review_case_graph_edge(case_identifier, edge_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_edge_review(case_identifier, iris_current_user.id, edge_id,
                                                  body.get('decision')))


@case_graph_blueprint.post('/edges/<int:edge_id>/reverse')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Swap the source and the target of a stored edge')
def reverse_case_graph_edge(case_identifier, edge_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_edge_reverse(case_identifier, iris_current_user.id, edge_id))


# ------------------------------------------------------------ Free nodes --

@case_graph_blueprint.post('/nodes')
@ac_api_requires()
@api_doc(response_shape='created', tags=['Case graph'], summary='Create a free node (actor, account, malware, ...)')
def create_case_graph_node(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_node_create(case_identifier, iris_current_user.id, body), created=True)


@case_graph_blueprint.get('/nodes/<int:node_id>')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Get a free node')
def get_case_graph_node(case_identifier, node_id):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_node_get(case_identifier, node_id))


@case_graph_blueprint.put('/nodes/<int:node_id>')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Update a free node (fields left out keep their value)')
def update_case_graph_node(case_identifier, node_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_node_update(case_identifier, iris_current_user.id, node_id, body))


@case_graph_blueprint.delete('/nodes/<int:node_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['Case graph'], summary='Delete a free node and its stored edges')
def delete_case_graph_node(case_identifier, node_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_graph_node_delete(case_identifier, iris_current_user.id, node_id))


def _promote(case_identifier, node_id, body):
    kind = body.get('kind')
    node = case_graph_node_get(case_identifier, node_id)
    data = {key: value for key, value in body.items() if key != 'kind'}
    if kind == 'asset':
        data.setdefault('asset_name', node['label'])
        if node.get('description'):
            data.setdefault('asset_description', node['description'])
        data = call_deprecated_on_preload_modules_hook('asset_create', data, case_identifier)
        ioc_links = data.get('ioc_links')
        case_object = CaseAssetsSchema().load(data)
        return case_graph_node_promote(case_identifier, iris_current_user, node_id, 'asset', case_object, ioc_links)
    if kind == 'ioc':
        data.setdefault('ioc_value', node['label'])
        if node.get('description'):
            data.setdefault('ioc_description', node['description'])
        if node.get('tlp_id'):
            data.setdefault('ioc_tlp_id', node['tlp_id'])
        data = call_deprecated_on_preload_modules_hook('ioc_create', data, case_identifier)
        data['case_id'] = case_identifier
        case_object = IocSchemaForAPIV2().load(data)
        return case_graph_node_promote(case_identifier, iris_current_user, node_id, 'ioc', case_object)
    raise BusinessProcessingError('kind must be asset or ioc')


@case_graph_blueprint.post('/nodes/<int:node_id>/promote')
@ac_api_requires()
@api_doc(tags=['Case graph'],
         summary='Turn a free node into an asset or an IOC ({kind, ...creation fields}); its edges follow')
def promote_case_graph_node(case_identifier, node_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: _promote(case_identifier, node_id, body), created=True)


# ----------------------------------------------------------------- Paste --

@case_graph_blueprint.post('/paste')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Create the IOCs found in a pasted text, optionally linked to a node')
def paste_case_graph(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_paste(case_identifier, iris_current_user.id, body))


# ---------------------------------------------------------- Intelligence --

@case_graph_blueprint.get('/suggestions')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Heuristic edge suggestions (text mentions, IP matches, alerts, enrichment)')
def get_case_graph_suggestions(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_suggestions(case_identifier))


@case_graph_blueprint.post('/suggestions/accept')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Store suggestions as accepted edges ({keys: [...]})')
def accept_case_graph_suggestions(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_suggestions_accept(case_identifier, iris_current_user.id, body.get('keys')))


@case_graph_blueprint.post('/suggestions/dismiss')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Dismiss suggestions for good ({keys: [...]})')
def dismiss_case_graph_suggestions(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_suggestions_dismiss(case_identifier, iris_current_user.id, body.get('keys')))


@case_graph_blueprint.post('/suggestions/ai')
@ac_api_requires()
@api_doc(tags=['Case graph'],
         summary='Ask the LLM for edges supported by the timeline, stored as pending AI edges (409 without LLM)')
def ai_case_graph_suggestions(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_ai_suggest(case_identifier, iris_current_user.id))


@case_graph_blueprint.get('/gaps')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Investigation gaps of the graph, most severe first')
def get_case_graph_gaps(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_gaps(case_identifier))


@case_graph_blueprint.post('/explain')
@ac_api_requires()
@api_doc(tags=['Case graph'],
         summary='LLM narrative of the attack path ({view_id?, node_keys?}) as Markdown (409 without LLM)')
def explain_case_graph(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_ai_explain(case_identifier, iris_current_user.id, body))


# ----------------------------------------------------------------- Views --

@case_graph_blueprint.get('/views')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='List the graph views visible to the caller (own and shared), without snapshot')
def list_case_graph_views(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_views_list(case_identifier, iris_current_user.id))


@case_graph_blueprint.post('/views')
@ac_api_requires()
@api_doc(response_shape='created', tags=['Case graph'], summary='Save a graph view')
def create_case_graph_view(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_view_create(case_identifier, iris_current_user.id, body), created=True)


@case_graph_blueprint.get('/views/<int:view_id>')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Get a graph view, with its snapshot')
def get_case_graph_view(case_identifier, view_id):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_graph_view_get(case_identifier, iris_current_user.id, view_id))


@case_graph_blueprint.put('/views/<int:view_id>')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Update a graph view (fields left out keep their value)')
def update_case_graph_view(case_identifier, view_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_view_update(case_identifier, iris_current_user.id, view_id, body))


@case_graph_blueprint.delete('/views/<int:view_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['Case graph'], summary='Delete a graph view')
def delete_case_graph_view(case_identifier, view_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_graph_view_delete(case_identifier, iris_current_user.id, view_id))


# --------------------------------------------------------- Import/export --

@case_graph_blueprint.get('/export')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Export the graph as JSON, a STIX 2.1 bundle or GraphML',
         query_params=[('format', str, 'json (default), stix or graphml'), _INCLUDE_PARAM])
def export_case_graph(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    export_format = request.args.get('format') or 'json'
    return _handle(lambda: case_graph_export(case_identifier, iris_current_user.id, export_format, _include(),
                                             _readable_case_ids))


@case_graph_blueprint.post('/import')
@ac_api_requires()
@api_doc(tags=['Case graph'], summary='Import a JSON or STIX 2.1 graph: missing IOCs, free nodes and edges are created')
def import_case_graph(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_graph_import(case_identifier, iris_current_user.id, body))
