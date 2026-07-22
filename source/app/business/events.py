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

from datetime import datetime

from app.db import db
from app.blueprints.iris_user import iris_current_user
from app.models.assets import CompromiseStatus
from app.models.cases import CasesEvent
from app.models.errors import ObjectNotFoundError
from app.util import add_obj_history_entry
from app.datamgmt.states import get_timeline_state
from app.datamgmt.states import update_timeline_state
from app.datamgmt.case.case_events_db import get_case_event_timelines_map
from app.datamgmt.case.case_events_db import get_case_events_comments_count
from app.datamgmt.case.case_events_db import get_case_iocs_light
from app.datamgmt.case.case_events_db import get_events_categories
from app.datamgmt.case.case_events_db import get_filtered_case_events
from app.datamgmt.case.case_events_db import save_event_category
from app.datamgmt.case.case_events_db import update_event_assets
from app.models.errors import BusinessProcessingError
from app.datamgmt.case.case_events_db import update_event_iocs
from app.datamgmt.case.case_events_db import get_case_event
from app.datamgmt.case.case_events_db import delete_event
from app.iris_engine.utils.common import parse_bf_date_format
from app.iris_engine.utils.tracker import track_activity
from app.iris_engine.utils.collab import collab_notify
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.business.case_timelines import attach_event_to_default_timeline_if_unset
from app.business.case_timelines import set_event_timelines


def events_create(case_identifier, event: CasesEvent, event_category_id, event_assets,
                  event_iocs, sync_iocs_assets, timeline_ids=None) -> CasesEvent:

    event.case_id = case_identifier
    event.event_added = datetime.utcnow()
    event.user_id = iris_current_user.id

    add_obj_history_entry(event, 'created')

    db.session.add(event)
    update_timeline_state(case_identifier)
    db.session.commit()

    save_event_category(event.event_id, event_category_id)

    setattr(event, 'event_category_id', event_category_id)

    success, log = update_event_assets(event.event_id, case_identifier, event_assets, event_iocs, sync_iocs_assets)
    if not success:
        raise BusinessProcessingError('Error while saving linked assets', data=log)

    success, log = update_event_iocs(event.event_id, case_identifier, event_iocs)
    if not success:
        raise BusinessProcessingError('Error while saving linked iocs', data=log)

    setattr(event, 'event_category_id', event_category_id)

    if timeline_ids is None:
        # Caller didn't specify — keep the legacy "all events appear on
        # the timeline view" behaviour by stamping the default timeline.
        attach_event_to_default_timeline_if_unset(event.event_id, case_identifier)
    else:
        set_event_timelines(event.event_id, case_identifier, timeline_ids)

    event = call_modules_hook('on_postload_event_create', event, caseid=case_identifier)

    track_activity(f'added event "{event.event_title}"', caseid=case_identifier)
    return event


def events_get(identifier) -> CasesEvent:
    event = get_case_event(identifier)
    if not event:
        raise ObjectNotFoundError()
    return event


def events_update(event: CasesEvent, event_category_id, event_assets, event_iocs,
                  event_sync_iocs_assets, timeline_ids=None) -> CasesEvent:
    add_obj_history_entry(event, 'updated')

    update_timeline_state(event.case_id)
    db.session.commit()

    save_event_category(event.event_id, event_category_id)

    setattr(event, 'event_category_id', event_category_id)

    success, log = update_event_assets(event.event_id, event.case_id, event_assets, event_iocs, event_sync_iocs_assets)
    if not success:
        raise BusinessProcessingError('Error while saving linked assets', data=log)

    success, log = update_event_iocs(event.event_id, event.case_id, event_iocs)
    if not success:
        raise BusinessProcessingError('Error while saving linked iocs', data=log)

    if timeline_ids is not None:
        set_event_timelines(event.event_id, event.case_id, timeline_ids)

    event = call_modules_hook('on_postload_event_update', event, caseid=event.case_id)

    track_activity(f"updated event \"{event.event_title}\"", caseid=event.case_id)
    return event


def events_delete(user, event: CasesEvent):
    delete_event(user.id, event)

    call_modules_hook('on_postload_event_delete', event.event_id, caseid=event.case_id)
    collab_notify(event.case_id, 'events', 'deletion', event.event_id)
    track_activity(f'deleted event "{event.event_title}" in timeline', event.case_id)


def events_list_filtered(case_identifier, filters, page: int = 1, per_page: int = 0):
    """List timeline events for a case, applying the given filter dimensions.

    Returns a dict with keys `tim`, `assets`, `iocs`, `categories`, `state`,
    `comments_map`, `pagination`. Pagination preserves parent/child lineage
    across pages by grafting ancestors and descendants of any event on the
    requested slice into the returned page.
    """
    page = max(1, int(page or 1))
    per_page = int(per_page or 0)
    if per_page < 0:
        per_page = 0
    if per_page > 500:
        per_page = 500

    assets_filter_values = filters.get('assets')
    assets_id_filter = filters.get('assets_id')
    iocs_filter_values = filters.get('iocs')

    # Date parsing lives in the business layer — the persistence layer can't
    # depend on iris_engine (import-linter).
    filters = dict(filters)
    for date_key in ('start_date', 'end_date'):
        raw = filters.get(date_key)
        if isinstance(raw, str) and raw:
            try:
                filters[date_key] = parse_bf_date_format(raw)
            except Exception:
                filters[date_key] = None

    timeline, assets_cache, iocs_cache = get_filtered_case_events(case_identifier, filters)

    assets_map: dict[int, int] = {}
    cache: dict[int, list] = {}

    for asset in assets_cache:
        if asset.asset_id not in cache:
            cache[asset.asset_id] = [asset.asset_name, asset.type]

        name_hit = assets_filter_values and asset.asset_name.lower() in assets_filter_values
        id_hit = assets_id_filter and asset.asset_id in assets_id_filter
        if name_hit or id_hit:
            assets_map[asset.event_id] = assets_map.get(asset.event_id, 0) + 1

    assets_intersection: list[int] = []
    filter_count = 0
    if assets_filter_values:
        filter_count += len(assets_filter_values)
    if assets_id_filter:
        filter_count += len(assets_id_filter)

    for event_id, hit_count in assets_map.items():
        if hit_count == filter_count:
            assets_intersection.append(event_id)

    event_ids_in_scope = [row.event_id for row in timeline]
    timelines_by_event = get_case_event_timelines_map(event_ids_in_scope)

    iocs_intersection: list[int] = []
    if iocs_filter_values:
        for ioc in iocs_cache:
            if ioc.event_id not in iocs_intersection and ioc.ioc_value.lower() in iocs_filter_values:
                iocs_intersection.append(ioc.event_id)

    tim: list[dict] = []
    events_list: list[int] = []
    for row in timeline:
        if (assets_filter_values is not None or assets_id_filter is not None) \
                and row.event_id not in assets_intersection:
            continue

        if iocs_filter_values is not None and row.event_id not in iocs_intersection:
            continue

        ras = row._asdict()
        ras['event_date'] = ras['event_date'].strftime('%Y-%m-%dT%H:%M:%S.%f')
        ras['event_date_wtz'] = ras['event_date_wtz'].strftime('%Y-%m-%dT%H:%M:%S.%f') \
            if ras['event_date_wtz'] else None
        ras['event_added'] = ras['event_added'].strftime('%Y-%m-%dT%H:%M:%S')

        if row.event_id not in events_list:
            events_list.append(row.event_id)

        ras['assets'] = [
            {
                'id': asset.asset_id,
                'name': f'{asset.asset_name} ({asset.type})',
                'asset_name': asset.asset_name,
                'asset_type': asset.type,
                'ip': asset.asset_ip,
                'description': asset.asset_description,
                'compromised': asset.asset_compromise_status_id == CompromiseStatus.compromised.value
            }
            for asset in assets_cache
            if asset.event_id == ras['event_id']
        ]

        event_iocs = []
        for ioc in iocs_cache:
            if ioc.event_id == ras['event_id']:
                if ioc.ioc_id not in cache:
                    cache[ioc.ioc_id] = [ioc.ioc_value]
                event_iocs.append({
                    'id': ioc.ioc_id,
                    'name': f'{ioc.ioc_value}',
                    'ioc_value': ioc.ioc_value,
                    'description': ioc.ioc_description
                })
        ras['iocs'] = event_iocs
        ras['timeline_ids'] = timelines_by_event.get(ras['event_id'], [])

        tim.append(ras)

    total = len(tim)
    if per_page > 0:
        last_page = max(1, (total + per_page - 1) // per_page)
        if page > last_page:
            page = last_page
        start = (page - 1) * per_page
        end = start + per_page
        tim_page = tim[start:end]
        next_page = page + 1 if page < last_page else None

        # Drag in descendants that live on later pages AND ancestors that
        # live on earlier pages, so any event on this slice ships with its
        # full lineage — the SPA promotes events with unknown parent_event_id
        # to roots otherwise.
        all_by_id: dict[int, dict] = {event['event_id']: event for event in tim}
        children_by_parent: dict[int, list[dict]] = {}
        for event in tim:
            parent_id = event.get('parent_event_id')
            if parent_id is None:
                continue
            children_by_parent.setdefault(parent_id, []).append(event)

        page_ids = {event['event_id'] for event in tim_page}

        queue = list(page_ids)
        while queue:
            parent_id = queue.pop()
            for child in children_by_parent.get(parent_id, ()):
                cid = child['event_id']
                if cid in page_ids:
                    continue
                page_ids.add(cid)
                tim_page.append(child)
                queue.append(cid)

        queue = list(page_ids)
        while queue:
            event_id = queue.pop()
            event = all_by_id.get(event_id)
            if not event:
                continue
            parent_id = event.get('parent_event_id')
            if parent_id is None or parent_id in page_ids:
                continue
            parent = all_by_id.get(parent_id)
            if parent is None:
                continue
            page_ids.add(parent_id)
            tim_page.append(parent)
            queue.append(parent_id)
    else:
        last_page = 1
        page = 1
        tim_page = tim
        next_page = None

    pagination = {
        'total': total,
        'per_page': per_page if per_page > 0 else total,
        'current_page': page,
        'last_page': last_page,
        'next_page': next_page
    }

    events_comments_map: dict[int, list[int]] = {}
    events_comments_set = get_case_events_comments_count(events_list)
    for k, v in events_comments_set:
        events_comments_map.setdefault(k, []).append(v)

    iocs = get_case_iocs_light(case_identifier)

    return {
        'tim': tim_page,
        'comments_map': events_comments_map,
        'assets': cache,
        'iocs': [ioc._asdict() for ioc in iocs],
        'categories': [cat.name for cat in get_events_categories()],
        'state': get_timeline_state(caseid=case_identifier),
        'pagination': pagination
    }
