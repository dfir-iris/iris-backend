#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

import json
from datetime import datetime
from typing import Optional

from app.business.access_controls import access_controls_user_has_customer_access
from app.business.managed_assets import managed_assets_observe_alert
from app.business.managed_assets import managed_assets_observe_case
from app.db import db
from app import socket_io
from app.models.alerts import Alert
from app.models.alerts import AlertStatus
from app.models.cases import Cases
from app.models.iocs import Ioc
from app.models.assets import CaseAssets
from flask import g, has_request_context
from flask_login import current_user as _flask_current_user
from app.datamgmt.alerts.alerts_db import cache_similar_alert
from app.datamgmt.alerts.alerts_db import delete_similar_alert_cache
from app.datamgmt.alerts.alerts_db import delete_related_alerts_cache
from app.datamgmt.alerts.alerts_db import get_alert_by_id
from app.datamgmt.alerts.alerts_db import delete_alert
from app.datamgmt.alerts.alerts_db import get_filtered_alerts
from app.datamgmt.alerts.alerts_db import get_filtered_alert_groups
from app.datamgmt.alerts.alerts_db import ALERT_SORT_COLUMNS
from app.datamgmt.alerts.alerts_db import get_related_alerts_details
from app.datamgmt.alerts.alerts_db import get_assets_with_cases
from app.datamgmt.alerts.alerts_db import get_iocs_with_cases
from app.datamgmt.alerts.alerts_db import create_case_from_alert
from app.datamgmt.alerts.alerts_db import create_case_from_alerts
from app.datamgmt.alerts.alerts_db import merge_alert_in_case
from app.datamgmt.alerts.alerts_db import unmerge_alert_from_case
from app.datamgmt.lucene.alert_fields import alert_field_catalogue
from app.datamgmt.case.case_assets_db import case_assets_db_exists
from app.datamgmt.case.case_db import get_case
from app.datamgmt.states import update_assets_state
from app.datamgmt.states import update_ioc_state
from app.iris_engine.access_control.utils import ac_set_new_case_access
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.util import add_obj_history_entry
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


class _TokenUserView:
    def __init__(self, user_data):
        self.id = user_data['user_id']
        self.user = user_data['user_login']
        self.name = user_data['user_name']
        self.email = user_data['user_email']
        self.is_authenticated = True
        self.is_active = True
        self.is_anonymous = False


def _resolve_caller():
    if has_request_context() and hasattr(g, 'auth_user'):
        return _TokenUserView(g.auth_user)
    return _flask_current_user


def alerts_search(start_date, end_date, source_start_date, source_end_date, title, description,
                  status, severity, owner, source, tags, case_identifier, customer_identifier, classification, alert_identifiers,
                  assets, iocs, resolution_status, source_reference, custom_conditions, user_identifier_filter, page, per_page, sort,
                  cluster_identifier=None, order_by=None, query=None, query_user_identifier=None):

    return get_filtered_alerts(
        start_date,
        end_date,
        source_start_date,
        source_end_date,
        title,
        description,
        status,
        severity,
        owner,
        source,
        tags,
        case_identifier,
        customer_identifier,
        classification,
        alert_identifiers,
        assets,
        iocs,
        resolution_status,
        page,
        per_page,
        sort,
        user_identifier_filter,
        source_reference,
        custom_conditions,
        cluster_id=cluster_identifier,
        order_by=order_by,
        query=query,
        query_user_identifier=query_user_identifier,
    )


def alerts_search_grouped(start_date, end_date, source_start_date, source_end_date, title, description,
                          status, severity, owner, source, tags, case_identifier, customer_identifier, classification,
                          alert_identifiers, assets, iocs, resolution_status, source_reference, custom_conditions,
                          user_identifier_filter, page, per_page, sort, cluster_identifier=None, order_by=None,
                          query=None, query_user_identifier=None):
    """Same filters as `alerts_search`, but the page is made of queue units:
    one per alert cluster holding a matching alert, plus one per matching
    alert that is in no cluster. See `get_filtered_alert_groups`.
    """
    return get_filtered_alert_groups(
        start_date,
        end_date,
        source_start_date,
        source_end_date,
        title,
        description,
        status,
        severity,
        owner,
        source,
        tags,
        case_identifier,
        customer_identifier,
        classification,
        alert_identifiers,
        assets,
        iocs,
        resolution_status,
        page,
        per_page,
        sort,
        user_identifier_filter,
        source_reference,
        custom_conditions,
        cluster_id=cluster_identifier,
        order_by=order_by,
        query=query,
        query_user_identifier=query_user_identifier,
    )


def alerts_search_vocabulary():
    """The fields the search bar understands, as plain data.

    One catalogue feeds the compiler, autocomplete and the OpenAPI
    description, so an alias the backend knows but the client does not
    offer cannot happen — an alias nobody can find is an alias nobody uses.
    """
    return alert_field_catalogue()


def alerts_create(alert: Alert, iocs: list[Ioc], assets: list[CaseAssets]) -> Alert:

    alert.alert_creation_time = datetime.utcnow()

    alert.iocs = iocs
    alert.assets = assets

    db.session.add(alert)
    db.session.commit()

    add_obj_history_entry(alert, 'Alert created')

    cache_similar_alert(alert.alert_customer_id, assets, iocs, alert.alert_id, alert.alert_source_event_time)

    alert = call_modules_hook('on_postload_alert_create', alert)

    track_activity(f"created alert #{alert.alert_id} - {alert.alert_title}", ctx_less=True)

    socket_io.emit('new_alert', json.dumps({
        'alert_id': alert.alert_id
    }), namespace='/alerts')

    _enqueue_rule_evaluation(alert.alert_id)

    # Register the alert's assets in the customer's registry. Never raises.
    managed_assets_observe_alert(alert.alert_id)

    return alert


def _enqueue_rule_evaluation(alert_id: int) -> None:
    """Fire the async rule evaluator. Import is deferred so a broken/
    unregistered Celery worker doesn't crash the request path — the
    ImportError branch logs and swallows so alert ingestion still
    succeeds even if the cluster-rules feature is disabled or the
    worker module fails to load."""
    try:
        from app.iris_engine.cluster_rules.tasks import evaluate_alert_rules
        evaluate_alert_rules.delay(alert_id)
    except Exception:  # rule evaluation must never block alert ingestion
        from app.logger import logger
        logger.exception('Failed to enqueue rule evaluation for alert #%s', alert_id)


def _get(user, permissions, identifier, fallback_customer_access=None) -> Optional[Alert]:
    alert = get_alert_by_id(identifier)
    if not alert:
        return None
    if not access_controls_user_has_customer_access(
        user,
        permissions,
        alert.alert_customer_id,
        fallback_customer_access=fallback_customer_access
    ):
        return None
    return alert


def alerts_get(user, permissions, identifier, fallback_customer_access=None) -> Alert:
    alert = _get(user, permissions, identifier, fallback_customer_access=fallback_customer_access)
    if not alert:
        raise ObjectNotFoundError()
    return alert


# TODO this is presentation code, should be in the presentation layer (frontend) rather than in the persistence layer!
def _get_font(in_dark_mode) -> str:
    if in_dark_mode:
        return '12px verdana white'
    return ''


# TODO this is presentation code, should be in the presentation layer (frontend) rather than in the persistence layer!
def _get_icon_alert(alert_color) -> dict[str, str]:
    return {
        'face': 'FontAwesome',
        'code': '\uf0f3',
        'color': alert_color,
        'weight': 'bold'
    }


# TODO this is presentation code, should be in the presentation layer (frontend) rather than in the persistence layer!
def _get_icon_ioc(in_dark_mode) -> dict[str, str]:
    return {
        'face': 'FontAwesome',
        'code': '\ue4a8',
        'color': 'white' if in_dark_mode else '',
        'weight': "bold"
    }


# TODO this is presentation code, should be in the presentation layer (frontend) rather than in the persistence layer!
def _get_icon_case(is_closed) -> dict[str, str]:
    return {
        'face': 'FontAwesome',
        'code': '\uf0b1',
        'color': '#c95029' if is_closed else '#4cba4f'
    }


def _create_alert_node(alert_id, alert_info, in_dark_mode):
    alert_color = '#c95029' if alert_info['alert'].status.status_name in ['Closed', 'Merged', 'Escalated'] else ''
    alert_resolution_title = f'[{alert_info["alert"].resolution_status.resolution_status_name}]\n' if alert_info[
        "alert"].resolution_status else ""
    alert_node = {
        'id': f'alert_{alert_id}',
        'label': f'[Closed]{alert_resolution_title} {alert_info["alert"].alert_title}' if alert_color != '' else f'{alert_resolution_title}{alert_info["alert"].alert_title}',
        'title': f'{alert_info["alert"].alert_description}',
        'group': 'alert',
        'shape': 'icon',
        'icon': _get_icon_alert(alert_color),
        'font': _get_font(in_dark_mode)
    }
    return alert_node


def _create_ioc_node(in_dark_mode, ioc_value):
    return {
        'id': f'ioc_{ioc_value}',
        'label': ioc_value,
        'group': 'ioc',
        'shape': 'icon',
        'icon': _get_icon_ioc(in_dark_mode),
        'font': _get_font(in_dark_mode)
    }


def _create_asset_node(asset_id, asset_info, in_dark_mode):
    return {
        'id': f'asset_{asset_id}',
        'label': asset_id,
        'group': 'asset',
        'shape': 'image',
        'image': '/static/assets/img/graph/' + asset_info['icon'],
        'font': _get_font(in_dark_mode)
    }


def _create_case_node(case_id, description, close_date, in_dark_mode):
    return {
        'id': f'case_{case_id}',
        'label': f'[Closed] Case #{case_id}' if close_date else f'Case #{case_id}',
        'title': description,
        'group': 'case',
        'shape': 'icon',
        'icon': _get_icon_case(close_date),
        'font': _get_font(in_dark_mode)
    }


def _build_related_alerts_graph(alerts_dict, open_cases, closed_cases, customer_id, in_dark_mode):
    nodes = []
    edges = []

    added_assets = set()
    added_iocs = set()

    for alert_id, alert_info in alerts_dict.items():
        alert_node = _create_alert_node(alert_id, alert_info, in_dark_mode)
        nodes.append(alert_node)

        for asset_info in alert_info['assets']:
            asset_id = asset_info['asset_name']

            if asset_id not in added_assets:
                nodes.append(_create_asset_node(asset_id, asset_info, in_dark_mode))
                added_assets.add(asset_id)

            edges.append({
                'from': f'alert_{alert_id}',
                'to': f'asset_{asset_id}'
            })

        for ioc_value in alert_info['iocs']:
            if ioc_value not in added_iocs:
                nodes.append(_create_ioc_node(in_dark_mode, ioc_value))
                added_iocs.add(ioc_value)

            edges.append({
                'from': f'alert_{alert_id}',
                'to': f'ioc_{ioc_value}',
                'dashes': True
            })

    if open_cases or closed_cases:

        cases_data = {}

        matching_ioc_cases = get_iocs_with_cases(added_iocs, customer_id, open_cases, closed_cases)
        for case_id, ioc_value, case_name, close_date, case_desc in matching_ioc_cases:
            if case_id not in cases_data:
                cases_data[case_id] = {'name': case_name, 'matching_ioc': [], 'matching_assets': [],
                                       'close_date': close_date, 'description': case_desc}
            cases_data[case_id]['matching_ioc'].append(ioc_value)

        matching_asset_cases = get_assets_with_cases(added_assets, customer_id, open_cases, closed_cases)
        for case_id, asset_name, case_name, close_date, case_desc in matching_asset_cases:
            if case_id not in cases_data:
                cases_data[case_id] = {'name': case_name, 'matching_ioc': [], 'matching_assets': [],
                                       'close_date': close_date, 'description': case_desc}
            cases_data[case_id]['matching_assets'].append(asset_name)

        added_cases = set()
        for case_id in cases_data:
            case_data = cases_data[case_id]
            if case_id not in added_cases:
                close_date = case_data.get('close_date')
                description = case_data.get('description')
                nodes.append(_create_case_node(case_id, description, close_date, in_dark_mode))
                added_cases.add(case_id)

            for ioc_value in case_data['matching_ioc']:
                edges.append({
                    'from': f'ioc_{ioc_value}',
                    'to': f'case_{case_id}',
                    'dashes': True
                })

            for asset_name in case_data['matching_assets']:
                edges.append({
                    'from': f'asset_{asset_name}',
                    'to': f'case_{case_id}',
                    'dashes': True
                })

    return {
        'nodes': nodes,
        'edges': edges
    }


def alerts_get_related(user, alert, open_alerts, closed_alerts, open_cases, closed_cases, days_back, number_of_results):
    assets = alert.assets
    iocs = alert.iocs
    if not assets and not iocs:
        return {
            'nodes': [],
            'edges': []
        }

    in_dark_mode = getattr(user, 'in_dark_mode', False)
    alerts_dict = get_related_alerts_details(alert.alert_customer_id, assets, iocs, open_alerts, closed_alerts,
                                             days_back, number_of_results)
    return _build_related_alerts_graph(alerts_dict, open_cases, closed_cases, alert.alert_customer_id, in_dark_mode)


def alerts_exists(user, permissions, identifier, fallback_customer_access=None) -> bool:
    alert = _get(user, permissions, identifier, fallback_customer_access=fallback_customer_access)

    return alert is not None


def alerts_update(alert: Alert, updated_alert: Alert, activity_data) -> Alert:
    from datetime import datetime as _dt

    do_resolution_hook = False
    do_status_hook = False

    resolution_changed = (
        alert.alert_resolution_status_id != updated_alert.alert_resolution_status_id
    )
    if resolution_changed:
        do_resolution_hook = True
    if alert.alert_status_id != updated_alert.alert_status_id:
        do_status_hook = True

    # Maintain resolved_at as the authoritative "when did the analyst
    # first pick a verdict" timestamp. Set on null→non-null transition,
    # clear on non-null→null revert. Left untouched when the resolution
    # is edited between two non-null values so we keep the *first*
    # resolution time (MTTR should measure how long it took to reach a
    # verdict, not how many times the verdict got tweaked).
    if resolution_changed:
        if (
            alert.alert_resolution_status_id is None
            and updated_alert.alert_resolution_status_id is not None
            and updated_alert.resolved_at is None
        ):
            updated_alert.resolved_at = _dt.utcnow()
        elif updated_alert.alert_resolution_status_id is None:
            updated_alert.resolved_at = None

    # Bump date_update on every write. add_obj_history_entry below also
    # stamps a modification_history key, but a dedicated column is much
    # cheaper to sort/index than a JSON scan and matches the pattern on
    # cases/notes/etc.
    updated_alert.date_update = _dt.utcnow()

    updated_alert = call_modules_hook('on_postload_alert_update', updated_alert)

    if do_resolution_hook:
        updated_alert = call_modules_hook('on_postload_alert_resolution_update', updated_alert)

    if do_status_hook:
        updated_alert = call_modules_hook('on_postload_alert_status_update', updated_alert)

    if activity_data:
        activity_data_as_string = ','.join(activity_data)
        track_activity(f'updated alert #{alert.alert_id}: {activity_data_as_string}', ctx_less=True)
        add_obj_history_entry(updated_alert, f'updated alert: {activity_data_as_string}')
    else:
        track_activity(f'updated alert #{alert.alert_id}', ctx_less=True)
        add_obj_history_entry(updated_alert, 'updated alert')

    db.session.commit()
    _enqueue_rule_evaluation(updated_alert.alert_id)
    return updated_alert


def alerts_get_ioc(alert: Alert, ioc_identifier: int) -> Ioc:
    """Return the IOC `ioc_identifier`, looked up through `alert`.

    Going through the alert's own collection is the authorization
    boundary: an IOC which is not attached to an alert the caller may
    see has to be indistinguishable from one that does not exist.
    """
    for ioc in alert.iocs:
        if ioc.ioc_id == ioc_identifier:
            return ioc

    raise ObjectNotFoundError()


def alerts_get_asset(alert: Alert, asset_identifier: int) -> CaseAssets:
    """Return the asset `asset_identifier`, looked up through `alert`. See `alerts_get_ioc`."""
    for asset in alert.assets:
        if asset.asset_id == asset_identifier:
            return asset

    raise ObjectNotFoundError()


def _updated_object_detail(activity_data) -> str:
    if not activity_data:
        return ''
    return f': {", ".join(activity_data)}'


def alerts_update_ioc(alert: Alert, ioc: Ioc, activity_data) -> Ioc:
    """Persist analyst-supplied details (description, tags, TLP, enrichment) on an alert's IOC."""
    caller = _resolve_caller()
    ioc.user_id = caller.id

    # Escalation hands the very same IOC row over to the case rather than
    # copying it (see create_case_from_alert), so an edit made from the
    # alert can land on case data. Bump the case's object state or the
    # case IOC list keeps serving its cached copy.
    if ioc.case_id is not None:
        update_ioc_state(ioc.case_id, userid=caller.id)

    detail = _updated_object_detail(activity_data)
    add_obj_history_entry(ioc, f'updated ioc{detail}')
    # Mirrored onto the alert too: the alert timeline is where an analyst
    # looks to see how the alert's observables were documented, and the
    # IOC's own history is not rendered there.
    add_obj_history_entry(alert, f'updated IOC "{ioc.ioc_value}"{detail}')

    db.session.commit()

    ioc = call_modules_hook('on_postload_ioc_update', ioc, caseid=ioc.case_id)

    track_activity(f'updated IOC "{ioc.ioc_value}" of alert #{alert.alert_id}{detail}',
                   caseid=ioc.case_id, ctx_less=ioc.case_id is None)

    return ioc


def alerts_update_asset(alert: Alert, asset: CaseAssets, activity_data) -> CaseAssets:
    """Persist analyst-supplied details (description, IP, domain, tags, enrichment) on an alert's asset."""
    caller = _resolve_caller()

    # Same shared-row caveat as IOCs — see alerts_update_ioc. The
    # uniqueness rule only exists within a case: two alerts naming the
    # same host legitimately hold two rows with a null case_id.
    if asset.case_id is not None:
        if case_assets_db_exists(asset):
            db.session.rollback()
            raise BusinessProcessingError('Asset with same value and type already exists')

        update_assets_state(asset.case_id, userid=caller.id)

    detail = _updated_object_detail(activity_data)
    asset.date_update = datetime.utcnow()
    add_obj_history_entry(asset, f'updated asset{detail}')
    add_obj_history_entry(alert, f'updated asset "{asset.asset_name}"{detail}')

    db.session.commit()

    asset = call_modules_hook('on_postload_asset_update', asset, caseid=asset.case_id)

    track_activity(f'updated asset "{asset.asset_name}" of alert #{alert.alert_id}{detail}',
                   caseid=asset.case_id, ctx_less=asset.case_id is None)

    # A rename or retype is a new registry identity — re-observe so the
    # customer's asset registry follows the edit. Never raises.
    managed_assets_observe_alert(alert.alert_id)

    return asset


def alerts_delete(alert: Alert):

    delete_similar_alert_cache(alert.alert_id)
    delete_related_alerts_cache([alert.alert_id])
    delete_alert(alert)

    call_modules_hook('on_postload_alert_delete', alert.alert_id)
    track_activity(f'delete alert #{alert.alert_id}', ctx_less=True)


def _resolve_alert_status_id(status_name: str) -> Optional[int]:
    row = AlertStatus.query.filter_by(status_name=status_name).first()
    return row.status_id if row else None


def alerts_escalate(alert: Alert, iocs_import_list: Optional[list] = None,
                    assets_import_list: Optional[list] = None, note: Optional[str] = None,
                    import_as_event: bool = False, case_tags: Optional[str] = None,
                    case_title: Optional[str] = None,
                    case_template_id: Optional[int] = None) -> Cases:
    escalated_id = _resolve_alert_status_id('Escalated')
    if escalated_id is not None:
        alert.alert_status_id = escalated_id
    db.session.commit()

    case = create_case_from_alert(
        alert,
        iocs_list=iocs_import_list,
        assets_list=assets_import_list,
        note=note,
        import_as_event=import_as_event,
        case_tags=case_tags,
        case_title=case_title,
        template_id=case_template_id,
    )
    if not case:
        raise BusinessProcessingError('Failed to create case from alert')

    ac_set_new_case_access(_resolve_caller(), case.case_id, case.client_id)
    case = call_modules_hook('on_postload_case_create', case)

    add_obj_history_entry(case, 'created')
    track_activity(f'new case {case.name} created from alert', ctx_less=True)
    add_obj_history_entry(alert, f'Alert escalated to case #{case.case_id}')
    call_modules_hook('on_postload_alert_escalate', alert)

    # Observed against the *case*, not the alert: escalation may create
    # assets from `assets_import_list` that were never on the alert, and
    # it flips an alert-only asset's `case_id` in place. Only the case's
    # own asset set is guaranteed to contain all of them.
    managed_assets_observe_case(case.case_id)

    return case


def alerts_merge(alert: Alert, target_case_id: int,
                 iocs_import_list: Optional[list] = None,
                 assets_import_list: Optional[list] = None, note: Optional[str] = None,
                 import_as_event: bool = False,
                 case_tags: Optional[str] = None) -> Cases:
    case = get_case(target_case_id)
    if case is None:
        raise ObjectNotFoundError()

    merged_id = _resolve_alert_status_id('Merged')
    if merged_id is not None:
        alert.alert_status_id = merged_id
    db.session.commit()

    merge_alert_in_case(
        alert,
        case,
        iocs_list=iocs_import_list,
        assets_list=assets_import_list,
        note=note,
        import_as_event=import_as_event,
        case_tags=case_tags,
    )
    call_modules_hook('on_postload_alert_merge', alert, caseid=target_case_id)

    track_activity(f'merge alert #{alert.alert_id} into existing case #{target_case_id}',
                   caseid=target_case_id)
    add_obj_history_entry(alert, f'Alert merged into existing case #{target_case_id}')

    managed_assets_observe_case(case.case_id)

    return case


def alerts_unmerge(alert: Alert, target_case_id: int) -> tuple[Alert, str]:
    case = get_case(target_case_id)
    if case is None:
        raise ObjectNotFoundError()

    success, message = unmerge_alert_from_case(alert, case)
    if not success:
        raise BusinessProcessingError(message)

    track_activity(f'unmerge alert #{alert.alert_id} from case #{target_case_id}',
                   caseid=target_case_id)
    add_obj_history_entry(alert, f'Alert unmerged from case #{target_case_id}')
    call_modules_hook('on_postload_alert_unmerge', alert)
    return alert, message


def alerts_batch_merge(alert_ids: list, target_case_id: int,
                       iocs_import_list: Optional[list] = None,
                       assets_import_list: Optional[list] = None, note: Optional[str] = None,
                       import_as_event: bool = False,
                       case_tags: Optional[str] = None) -> Cases:
    case = get_case(target_case_id)
    if case is None:
        raise ObjectNotFoundError()

    merged_id = _resolve_alert_status_id('Merged')

    for alert_id in alert_ids:
        alert = get_alert_by_id(alert_id)
        if not alert:
            continue

        if merged_id is not None:
            alert.alert_status_id = merged_id
        db.session.commit()

        merge_alert_in_case(
            alert,
            case,
            iocs_list=iocs_import_list,
            assets_list=assets_import_list,
            note=None,
            import_as_event=import_as_event,
            case_tags=case_tags,
        )
        add_obj_history_entry(alert, f'Alert merged into existing case #{target_case_id}')
        call_modules_hook('on_postload_alert_merge', alert)

    if note:
        case.description += (
            f"\n\n### Escalation note\n\n{note}\n\n"
            if case.description else f"\n\n{note}\n\n"
        )
        db.session.commit()

    track_activity(f'batched merge alerts {alert_ids} into existing case #{target_case_id}',
                   caseid=target_case_id)

    # Once for the batch: the statement is set-based over the case's whole
    # asset set, so running it per alert would repeat identical work.
    managed_assets_observe_case(case.case_id)

    return case


def alerts_batch_escalate(alert_ids: list,
                          iocs_import_list: Optional[list] = None,
                          assets_import_list: Optional[list] = None,
                          note: Optional[str] = None, import_as_event: bool = False,
                          case_tags: Optional[str] = None,
                          case_title: Optional[str] = None,
                          case_template_id: Optional[int] = None) -> Cases:
    # NOTE: the legacy route marked alerts as "Merged" here (see
    # alerts_routes.py:906). Preserved verbatim to avoid altering behavior
    # in this migration; if it should be "Escalated", that's a separate fix.
    merged_id = _resolve_alert_status_id('Merged')

    alerts_list = []
    for alert_id in alert_ids:
        alert = get_alert_by_id(alert_id)
        if not alert:
            continue

        if merged_id is not None:
            alert.alert_status_id = merged_id
        db.session.commit()
        alert = call_modules_hook('on_postload_alert_escalate', alert)
        alerts_list.append(alert)

    case = create_case_from_alerts(
        alerts_list,
        iocs_import_list,
        assets_import_list,
        case_title,
        note,
        import_as_event,
        case_tags,
        case_template_id,
    )
    if not case:
        raise BusinessProcessingError('Failed to create case from alerts')

    ac_set_new_case_access(_resolve_caller(), case.case_id, case.client_id)
    case = call_modules_hook('on_postload_case_create', case)

    add_obj_history_entry(case, 'created')
    track_activity(f'new case {case.name} created from alerts', caseid=case.case_id)

    for alert in alerts_list:
        add_obj_history_entry(alert, f'Alert escalated into new case #{case.case_id}')

    managed_assets_observe_case(case.case_id)

    return case
