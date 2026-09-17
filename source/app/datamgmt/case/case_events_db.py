#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

from sqlalchemy import and_

from app.datamgmt.db_operations import db_create
from app.datamgmt.db_operations import db_delete
from app.db import db
from app.datamgmt.states import update_timeline_state
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.cases import CaseEventTimeline
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.cases import CasesEvent
from app.models.comments import Comments
from app.models.comments import EventComments
from app.models.models import EventCategory
from app.models.iocs import Ioc
from app.models.models import IocAssetLink
from app.models.models import IocType
from app.models.authorization import User
from app.models.cases import Cases
from app.models.customers import Client
from sqlalchemy import or_


def get_case_events_assets_graph(caseid):
    events = CaseEventsAssets.query.with_entities(
        CaseEventsAssets.event_id,
        CasesEvent.event_uuid,
        CasesEvent.event_title,
        CaseAssets.asset_name,
        CaseAssets.asset_id,
        AssetsType.asset_name.label('type_name'),
        AssetsType.asset_icon_not_compromised,
        AssetsType.asset_icon_compromised,
        CasesEvent.event_color,
        CaseAssets.asset_compromise_status_id,
        CaseAssets.asset_description,
        CaseAssets.asset_ip,
        CasesEvent.event_date,
        CasesEvent.event_tags
    ).filter(and_(
        CaseEventsAssets.case_id == caseid,
        CasesEvent.event_in_graph == True
    )).join(
        CaseEventsAssets.event
    ).join(
        CaseEventsAssets.asset
    ).join(
        CaseAssets.asset_type
    ).all()

    return events


def get_case_events_ioc_graph(caseid):
    events = CaseEventsIoc.query.with_entities(
        CaseEventsIoc.event_id,
        CasesEvent.event_uuid,
        CasesEvent.event_title,
        CasesEvent.event_date,
        Ioc.ioc_id,
        Ioc.ioc_value,
        Ioc.ioc_description,
        IocType.type_name
    ).filter(and_(
        CaseEventsIoc.case_id == caseid,
        CasesEvent.event_in_graph == True
    )).join(
        CaseEventsIoc.event
    ).join(
        CaseEventsIoc.ioc
    ).join(
        Ioc.ioc_type
    ).all()

    return events


def get_events_categories():
    return EventCategory.query.with_entities(
        EventCategory.id,
        EventCategory.name
    ).all()


def get_default_cat():
    cat = EventCategory.query.with_entities(
        EventCategory.id,
        EventCategory.name
    ).filter(
        EventCategory.name == "Unspecified"
    ).first()

    return [cat._asdict()]


def get_case_event(event_id):
    return CasesEvent.query.filter(
        CasesEvent.event_id == event_id
    ).first()


def get_case_event_comments(event_id):
    return Comments.query.filter(
        EventComments.comment_event_id == event_id
    ).join(
        EventComments,
        Comments.comment_id == EventComments.comment_id
    ).order_by(
        Comments.comment_date.asc()
    ).all()


def get_case_events_comments_count(events_list):
    return EventComments.query.filter(
        EventComments.comment_event_id.in_(events_list)
    ).with_entities(
        EventComments.comment_event_id,
        EventComments.comment_id
    ).group_by(
        EventComments.comment_event_id,
        EventComments.comment_id
    ).all()


def get_case_event_comment(event_id, comment_id):
    return EventComments.query.filter(
        EventComments.comment_event_id == event_id,
        EventComments.comment_id == comment_id
    ).with_entities(
        Comments.comment_id,
        Comments.comment_text,
        Comments.comment_date,
        Comments.comment_update_date,
        Comments.comment_uuid,
        Comments.comment_user_id,
        Comments.comment_case_id,
        User.name,
        User.user
    ).join(
        EventComments.comment
    ).join(
        Comments.user
    ).first()


def delete_event_comment(user_identifier, event_id, comment_id):
    comment = Comments.query.filter(
        Comments.comment_id == comment_id,
        Comments.comment_user_id == user_identifier
    ).first()
    if not comment:
        return False, "You are not allowed to delete this comment"

    EventComments.query.filter(
        EventComments.comment_event_id == event_id,
        EventComments.comment_id == comment_id
    ).delete()

    db_delete(comment)

    return True, "Comment deleted"


def delete_events_comments_in_case(case_identifier):
    com_ids = EventComments.query.with_entities(
        EventComments.comment_id
    ).join(CasesEvent).filter(
        EventComments.comment_event_id == CasesEvent.event_id,
        CasesEvent.case_id == case_identifier
    ).all()

    com_ids = [c.comment_id for c in com_ids]
    EventComments.query.filter(EventComments.comment_id.in_(com_ids)).delete()
    Comments.query.filter(Comments.comment_id.in_(com_ids)).delete()


def add_comment_to_event(event_id, comment_id):
    ec = EventComments()
    ec.comment_event_id = event_id
    ec.comment_id = comment_id

    db_create(ec)


def delete_event_category(event_id):
    CaseEventCategory.query.filter(
        CaseEventCategory.event_id == event_id
    ).delete()


def get_event_category(event_id):
    cec = CaseEventCategory.query.filter(
        CaseEventCategory.event_id == event_id
    ).first()
    return cec


def save_event_category(event_id, category_id):
    CaseEventCategory.query.filter(
        CaseEventCategory.event_id == event_id
    ).delete()

    cec = CaseEventCategory()
    cec.event_id = event_id
    cec.category_id = category_id

    db_create(cec)


def get_event_assets_ids(event_id, caseid):
    assets_list = CaseEventsAssets.query.with_entities(
        CaseEventsAssets.asset_id
    ).filter(
        CaseEventsAssets.event_id == event_id,
        CaseEventsAssets.case_id == caseid
    ).all()

    return [x[0] for x in assets_list]


def get_event_iocs_ids(event_id, caseid):
    iocs_list = CaseEventsIoc.query.with_entities(
        CaseEventsIoc.ioc_id
    ).filter(
        CaseEventsIoc.event_id == event_id,
        CaseEventsIoc.case_id == caseid
    ).all()

    return [x[0] for x in iocs_list]


def update_event_assets(event_id, caseid, assets_list, iocs_list, sync_iocs_assets):

    CaseEventsAssets.query.filter(
        CaseEventsAssets.event_id == event_id,
        CaseEventsAssets.case_id == caseid
    ).delete()

    valid_assets = CaseAssets.query.with_entities(
        CaseAssets.asset_id
    ).filter(
        CaseAssets.asset_id.in_(assets_list),
        CaseAssets.case_id == caseid
    ).all()

    for asset in valid_assets:
        try:

            cea = CaseEventsAssets()
            cea.asset_id = int(asset.asset_id)
            cea.event_id = event_id
            cea.case_id = caseid

            db.session.add(cea)

            if sync_iocs_assets:
                for ioc in iocs_list:
                    link = IocAssetLink.query.filter(
                        IocAssetLink.asset_id == int(asset.asset_id),
                        IocAssetLink.ioc_id == int(ioc)
                    ).first()

                    if link is None:

                        ial = IocAssetLink()
                        ial.asset_id = int(asset.asset_id)
                        ial.ioc_id = int(ioc)

                        db.session.add(ial)

        except Exception as e:
            return False, str(e)

    db.session.commit()
    return True, ''


def update_event_iocs(event_id, caseid, iocs_list):

    CaseEventsIoc.query.filter(
        CaseEventsIoc.event_id == event_id,
        CaseEventsIoc.case_id == caseid
    ).delete()

    valid_iocs = Ioc.query.filter(
        Ioc.ioc_id.in_(iocs_list),
        Ioc.case_id == caseid
    ).all()

    for ioc in valid_iocs:
        try:

            cea = CaseEventsIoc()
            cea.ioc_id = int(ioc.ioc_id)
            cea.event_id = event_id
            cea.case_id = caseid

            db.session.add(cea)

        except Exception as e:
            return False, str(e)

    db.session.commit()
    return True, ''


def get_case_assets_for_tm(caseid):
    """
    Return a list of all assets linked to the current case
    :return: Tuple of assets
    """
    assets = [{'asset_name': '', 'asset_id': '0'}]

    assets_list = CaseAssets.query.with_entities(
        CaseAssets.asset_name,
        CaseAssets.asset_id,
        AssetsType.asset_name.label('type')
    ).filter(
        CaseAssets.case_id == caseid
    ).join(CaseAssets.asset_type).order_by(CaseAssets.asset_name).all()

    for asset in assets_list:
        assets.append({
            'asset_name': f'{asset.asset_name} ({asset.type})',
            'asset_id': asset.asset_id
        })

    return assets


def get_case_iocs_for_tm(caseid):
    iocs = [{'ioc_value': '', 'ioc_id': '0'}]

    iocs_list = Ioc.query.with_entities(
        Ioc.ioc_value,
        Ioc.ioc_id
    ).filter(
        Ioc.case_id == caseid
    ).order_by(
        Ioc.ioc_value
    ).all()

    for ioc in iocs_list:
        iocs.append({
            'ioc_value': f'{ioc.ioc_value}',
            'ioc_id': ioc.ioc_id
        })

    return iocs


def delete_event(user_identifier, event):
    case_identifier = event.case_id
    delete_event_category(event.event_id)

    CaseEventsAssets.query.filter(
        CaseEventsAssets.event_id == event.event_id,
        CaseEventsAssets.case_id == case_identifier
    ).delete()

    CaseEventsIoc.query.filter(
        CaseEventsIoc.event_id == event.event_id,
        CaseEventsIoc.case_id == case_identifier
    ).delete()

    com_ids = EventComments.query.with_entities(
        EventComments.comment_id
    ).filter(
        EventComments.comment_event_id == event.event_id
    ).all()

    com_ids = [c.comment_id for c in com_ids]
    EventComments.query.filter(EventComments.comment_id.in_(com_ids)).delete()

    Comments.query.filter(Comments.comment_id.in_(com_ids)).delete()

    db.session.commit()

    db.session.delete(event)
    update_timeline_state(case_identifier, user_identifier)

    db.session.commit()


def get_category_by_name(cat_name):
    return EventCategory.query.filter(
        EventCategory.name == cat_name,
    ).first()


def get_default_category():
    return EventCategory.query.with_entities(
        EventCategory.id,
        EventCategory.name
    ).filter(
        EventCategory.name == "Unspecified"
    ).first()


def get_events_by_case(case_identifier):
    return CasesEvent.query.filter(and_(
        CasesEvent.case_id == case_identifier,
        CasesEvent.event_in_summary
    )).order_by(
        CasesEvent.event_date
    ).all()


def get_filtered_case_events(case_identifier, filters):
    condition = (CasesEvent.case_id == case_identifier)

    assets = filters.get('assets')
    assets_id = filters.get('assets_id')
    iocs = filters.get('iocs')
    iocs_id = filters.get('iocs_id')
    tags = filters.get('tags')
    titles = filters.get('titles')
    sources = filters.get('sources')
    descriptions = filters.get('descriptions')
    raws = filters.get('raws')
    categories = filters.get('categories')
    event_ids = filters.get('event_ids')
    start_date = filters.get('start_date')
    end_date = filters.get('end_date')
    flag = filters.get('flag')

    if assets:
        assets = [asset.lower() for asset in assets]

    if assets_id:
        assets_id = [int(asset) for asset in assets_id]

    if iocs:
        iocs = [ioc.lower() for ioc in iocs]

    if iocs_id:
        iocs_id = [int(ioc) for ioc in iocs_id]

    if flag is not None:
        condition = and_(condition, CasesEvent.event_is_flagged == bool(flag))

    if tags:
        for tag in tags:
            condition = and_(condition,
                             CasesEvent.event_tags.ilike(f'%{tag}%'))

    if titles:
        for title in titles:
            condition = and_(condition,
                             CasesEvent.event_title.ilike(f'%{title}%'))

    if sources:
        for source in sources:
            condition = and_(condition,
                             CasesEvent.event_source.ilike(f'%{source}%'))

    if descriptions:
        for description in descriptions:
            condition = and_(condition,
                             CasesEvent.event_content.ilike(f'%{description}%'))

    if raws:
        for raw in raws:
            condition = and_(condition,
                             CasesEvent.event_raw.ilike(f'%{raw}%'))

    if start_date is not None:
        condition = and_(condition, CasesEvent.event_date >= start_date)

    if end_date is not None:
        condition = and_(condition, CasesEvent.event_date <= end_date)

    if categories:
        for category in categories:
            condition = and_(condition,
                             EventCategory.name == category)

    if event_ids:
        condition = and_(condition,
                         CasesEvent.event_id.in_(event_ids))

    timeline = CasesEvent.query.with_entities(
        CasesEvent.event_id,
        CasesEvent.event_uuid,
        CasesEvent.event_date,
        CasesEvent.event_date_wtz,
        CasesEvent.event_tz,
        CasesEvent.event_title,
        CasesEvent.event_color,
        CasesEvent.event_tags,
        CasesEvent.event_content,
        CasesEvent.event_in_summary,
        CasesEvent.event_in_graph,
        CasesEvent.event_is_flagged,
        CasesEvent.parent_event_id,
        User.user,
        CasesEvent.event_added,
        EventCategory.name.label('category_name')
    ).filter(condition).order_by(
        CasesEvent.event_date
    ).outerjoin(
        CasesEvent.category
    ).join(
        CasesEvent.user
    ).all()

    assets_cache_condition = (CaseEventsAssets.case_id == case_identifier)
    if assets_id:
        assets_cache_condition = and_(
            assets_cache_condition,
            CaseEventsAssets.asset_id.in_(assets_id)
        )

    assets_cache = (CaseAssets.query.with_entities(
        CaseEventsAssets.event_id,
        CaseAssets.asset_id,
        CaseAssets.asset_name,
        AssetsType.asset_name.label('type'),
        CaseAssets.asset_ip,
        CaseAssets.asset_description,
        CaseAssets.asset_compromise_status_id
    ).filter(
        assets_cache_condition
    ).join(CaseEventsAssets.asset)
                    .join(CaseAssets.asset_type).all())

    iocs_cache_condition = (CaseEventsIoc.case_id == case_identifier)
    if iocs_id:
        iocs_cache_condition = and_(
            iocs_cache_condition,
            CaseEventsIoc.ioc_id.in_(iocs_id)
        )

    iocs_cache = CaseEventsIoc.query.with_entities(
        CaseEventsIoc.event_id,
        CaseEventsIoc.ioc_id,
        Ioc.ioc_value,
        Ioc.ioc_description
    ).filter(
        iocs_cache_condition
    ).join(
        CaseEventsIoc.ioc
    ).all()

    return timeline, assets_cache, iocs_cache


def get_case_iocs_light(case_identifier):
    return Ioc.query.with_entities(
        Ioc.ioc_id,
        Ioc.ioc_value,
        Ioc.ioc_description,
    ).filter(
        Ioc.case_id == case_identifier
    ).all()


def get_case_event_timelines_map(event_ids):
    if not event_ids:
        return {}

    rows = (
        CaseEventTimeline.query
        .with_entities(CaseEventTimeline.event_id, CaseEventTimeline.timeline_id)
        .filter(CaseEventTimeline.event_id.in_(event_ids))
        .all()
    )

    timelines_by_event: dict[int, list[int]] = {}
    for r in rows:
        timelines_by_event.setdefault(r.event_id, []).append(r.timeline_id)
    return timelines_by_event


def search_events(search_value, accessible_case_ids=None):
    if accessible_case_ids is not None and not accessible_case_ids:
        return []

    scope_filter = CasesEvent.case_id.in_(accessible_case_ids) if accessible_case_ids is not None else and_()

    pattern = f'%{search_value}%'

    res = CasesEvent.query.with_entities(
        CasesEvent.event_id,
        CasesEvent.event_title,
        CasesEvent.event_content,
        CasesEvent.event_date,
        Cases.name.label("case_name"),
        Cases.case_id,
        Client.name.label("customer_name")
    ).filter(
        and_(
            or_(
                CasesEvent.event_title.ilike(pattern),
                CasesEvent.event_content.ilike(pattern)
            ),
            CasesEvent.case_id == Cases.case_id,
            Client.client_id == Cases.client_id,
            scope_filter
        )
    ).all()

    return [row._asdict() for row in res]
