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

"""Persistence of the investigation graph of a case.

The `*_rows` readers return plain dicts (one query per kind, no per-row
query), so the assembly in `app.business.case_graph_assembly` stays a
pure function over plain data.
"""

from sqlalchemy import and_
from sqlalchemy import distinct
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.db import db
from app.models.alerts import Alert
from app.models.alerts import AlertCaseAssociation
from app.models.alerts import Severity
from app.models.assets import AnalysisStatus
from app.models.assets import AssetFlag
from app.models.assets import AssetsType
from app.models.assets import CaseAssetFlag
from app.models.assets import CaseAssets
from app.models.assets import alert_assets_association
from app.models.case_graph import CaseGraphDismissedSuggestion
from app.models.case_graph import CaseGraphEdge
from app.models.case_graph import CaseGraphEdgeEvent
from app.models.case_graph import CaseGraphNode
from app.models.case_graph import CaseGraphRelationType
from app.models.case_graph import CaseGraphView
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.evidences import CaseReceivedFile
from app.models.evidences import EvidenceTypes
from app.models.iocs import Ioc
from app.models.iocs import Tlp
from app.models.iocs import alert_iocs_association
from app.models.managed_assets import ManagedAsset
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import CaseTasks
from app.models.models import EventCategory
from app.models.models import IocAssetLink
from app.models.models import IocType
from app.models.models import Notes
from app.models.models import TaskStatus


# ------------------------------------------------------------- Session --

def case_graph_db_add(obj):
    """Add and flush (the id is needed right away)."""
    db.session.add(obj)
    db.session.flush()
    return obj


def case_graph_db_delete(obj):
    db.session.delete(obj)
    db.session.flush()


def case_graph_db_commit():
    """Commit; False (rolled back) on an integrity error."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def case_graph_db_rollback():
    db.session.rollback()


# ------------------------------------------------------ Relation types --

def case_graph_db_relation_types():
    return CaseGraphRelationType.query.order_by(CaseGraphRelationType.sort_order,
                                                CaseGraphRelationType.id).all()


def case_graph_db_relation_type_get(relation_type_id):
    return CaseGraphRelationType.query.filter(CaseGraphRelationType.id == relation_type_id).first()


def case_graph_db_relation_type_by_name(name):
    return CaseGraphRelationType.query.filter(func.lower(CaseGraphRelationType.name) == name.lower()).first()


def case_graph_db_relation_type_usage(relation_type_id):
    return CaseGraphEdge.query.filter(CaseGraphEdge.relation_type_id == relation_type_id).count()


def case_graph_db_relation_type_usages():
    rows = (db.session.query(CaseGraphEdge.relation_type_id, func.count(CaseGraphEdge.edge_id))
            .group_by(CaseGraphEdge.relation_type_id).all())
    return {row[0]: row[1] for row in rows}


def case_graph_db_relation_type_max_sort():
    return db.session.query(func.max(CaseGraphRelationType.sort_order)).scalar()


# --------------------------------------------------------------- Edges --

def case_graph_db_edge_get(case_id, edge_id):
    return CaseGraphEdge.query.filter(CaseGraphEdge.case_id == case_id,
                                      CaseGraphEdge.edge_id == edge_id).first()


def case_graph_db_edge_event_ids(edge_ids):
    """`{edge_id: [event_id, ...]}`."""
    if not edge_ids:
        return {}
    rows = (CaseGraphEdgeEvent.query.with_entities(CaseGraphEdgeEvent.edge_id, CaseGraphEdgeEvent.event_id)
            .filter(CaseGraphEdgeEvent.edge_id.in_(list(edge_ids)))
            .order_by(CaseGraphEdgeEvent.event_id).all())
    result = {}
    for row in rows:
        result.setdefault(row.edge_id, []).append(row.event_id)
    return result


def case_graph_db_edge_set_events(edge_id, event_ids):
    CaseGraphEdgeEvent.query.filter(CaseGraphEdgeEvent.edge_id == edge_id).delete(synchronize_session=False)
    for event_id in event_ids:
        db.session.add(CaseGraphEdgeEvent(edge_id=edge_id, event_id=event_id))
    db.session.flush()


def case_graph_db_edges_touching(case_id, kind, object_ids):
    """Stored edges of the case with an endpoint among `kind:object_ids`."""
    object_ids = list(object_ids)
    if not object_ids:
        return []
    return CaseGraphEdge.query.filter(
        CaseGraphEdge.case_id == case_id,
        or_(and_(CaseGraphEdge.source_kind == kind, CaseGraphEdge.source_id.in_(object_ids)),
            and_(CaseGraphEdge.target_kind == kind, CaseGraphEdge.target_id.in_(object_ids)))
    ).all()


def case_graph_db_delete_edges_touching(kind, object_ids, case_id=None):
    """Delete (flushed, not committed) the stored edges touching
    `kind:object_ids`; the number deleted."""
    object_ids = list(object_ids)
    if not object_ids:
        return 0
    condition = or_(and_(CaseGraphEdge.source_kind == kind, CaseGraphEdge.source_id.in_(object_ids)),
                    and_(CaseGraphEdge.target_kind == kind, CaseGraphEdge.target_id.in_(object_ids)))
    if case_id is not None:
        condition = and_(CaseGraphEdge.case_id == case_id, condition)
    edge_ids = [row[0] for row in CaseGraphEdge.query.with_entities(CaseGraphEdge.edge_id).filter(condition).all()]
    if not edge_ids:
        return 0
    CaseGraphEdgeEvent.query.filter(CaseGraphEdgeEvent.edge_id.in_(edge_ids)).delete(synchronize_session=False)
    CaseGraphEdge.query.filter(CaseGraphEdge.edge_id.in_(edge_ids)).delete(synchronize_session=False)
    db.session.flush()
    return len(edge_ids)


def case_graph_db_edge_rows(case_id):
    """Stored edges of the case as dicts, with their `event_ids`."""
    edges = CaseGraphEdge.query.filter(CaseGraphEdge.case_id == case_id).order_by(CaseGraphEdge.edge_id).all()
    events = case_graph_db_edge_event_ids([edge.edge_id for edge in edges])
    return [case_graph_db_edge_as_dict(edge, events.get(edge.edge_id, [])) for edge in edges]


def case_graph_db_edge_as_dict(edge, event_ids):
    return {
        'edge_id': edge.edge_id,
        'edge_uuid': str(edge.edge_uuid) if edge.edge_uuid else None,
        'case_id': edge.case_id,
        'source_kind': edge.source_kind,
        'source_id': edge.source_id,
        'target_kind': edge.target_kind,
        'target_id': edge.target_id,
        'relation_type_id': edge.relation_type_id,
        'directed': bool(edge.directed),
        'label': edge.label,
        'description': edge.description,
        'confidence': edge.confidence,
        'first_seen': edge.first_seen,
        'last_seen': edge.last_seen,
        'tlp_id': edge.tlp_id,
        'origin': edge.origin,
        'review_state': edge.review_state,
        'custom_attributes': edge.custom_attributes,
        'created_by_id': edge.created_by_id,
        'created_at': edge.created_at,
        'updated_at': edge.updated_at,
        'event_ids': list(event_ids),
    }


# --------------------------------------------------------------- Nodes --

def case_graph_db_node_get(case_id, node_id):
    return CaseGraphNode.query.filter(CaseGraphNode.case_id == case_id, CaseGraphNode.node_id == node_id).first()


def case_graph_db_node_rows(case_id):
    nodes = CaseGraphNode.query.filter(CaseGraphNode.case_id == case_id).order_by(CaseGraphNode.node_id).all()
    return [case_graph_db_node_as_dict(node) for node in nodes]


def case_graph_db_node_as_dict(node):
    return {
        'node_id': node.node_id,
        'node_uuid': str(node.node_uuid) if node.node_uuid else None,
        'case_id': node.case_id,
        'label': node.label,
        'node_type': node.node_type,
        'description': node.description,
        'icon': node.icon,
        'color': node.color,
        'tlp_id': node.tlp_id,
        'custom_attributes': node.custom_attributes,
        'created_by_id': node.created_by_id,
        'created_at': node.created_at,
        'updated_at': node.updated_at,
    }


# --------------------------------------------------------------- Views --

def case_graph_db_view_get(case_id, view_id):
    return CaseGraphView.query.filter(CaseGraphView.case_id == case_id, CaseGraphView.view_id == view_id).first()


def case_graph_db_views_visible(case_id, user_id):
    return (CaseGraphView.query
            .with_entities(CaseGraphView.view_id, CaseGraphView.view_uuid, CaseGraphView.case_id,
                           CaseGraphView.name, CaseGraphView.description, CaseGraphView.owner_id,
                           CaseGraphView.is_shared, CaseGraphView.is_report_default, CaseGraphView.state,
                           CaseGraphView.created_at, CaseGraphView.updated_at)
            .filter(CaseGraphView.case_id == case_id,
                    or_(CaseGraphView.owner_id == user_id, CaseGraphView.is_shared.is_(True)))
            .order_by(CaseGraphView.name, CaseGraphView.view_id).all())


def case_graph_db_views_clear_report_default(case_id, except_view_id=None):
    query = CaseGraphView.query.filter(CaseGraphView.case_id == case_id, CaseGraphView.is_report_default.is_(True))
    if except_view_id is not None:
        query = query.filter(CaseGraphView.view_id != except_view_id)
    query.update({CaseGraphView.is_report_default: False}, synchronize_session=False)


# --------------------------------------------------------- Suggestions --

def case_graph_db_dismissed_keys(case_id):
    """Keys of the graph suggestions dismissed in the case (for §4.8)."""
    rows = (CaseGraphDismissedSuggestion.query.with_entities(CaseGraphDismissedSuggestion.suggestion_key)
            .filter(CaseGraphDismissedSuggestion.case_id == case_id).all())
    return {row[0] for row in rows}


def case_graph_db_dismiss_suggestions(case_id, user_id, keys, dismissed_at):
    """Record the dismissal of `keys` (flushed, not committed); the
    number of keys that were not dismissed yet."""
    already = case_graph_db_dismissed_keys(case_id)
    added = 0
    for key in keys:
        if key in already:
            continue
        db.session.add(CaseGraphDismissedSuggestion(case_id=case_id, suggestion_key=key, dismissed_by_id=user_id,
                                                    dismissed_at=dismissed_at))
        already.add(key)
        added += 1
    db.session.flush()
    return added


def case_graph_db_report_default_snapshot(case_id):
    """Snapshot (PNG data URL) of the report-default view of the case, or None."""
    row = (CaseGraphView.query.with_entities(CaseGraphView.snapshot)
           .filter(CaseGraphView.case_id == case_id, CaseGraphView.is_report_default.is_(True),
                   CaseGraphView.is_shared.is_(True))
           .order_by(CaseGraphView.updated_at.desc()).first())
    return row[0] if row is not None else None


# ------------------------------------------------- Graph source data --

def case_graph_db_tlps():
    return {row.tlp_id: row.tlp_name for row in Tlp.query.with_entities(Tlp.tlp_id, Tlp.tlp_name).all()}


def case_graph_db_asset_rows(case_id):
    rows = (CaseAssets.query.with_entities(
        CaseAssets.asset_id, CaseAssets.asset_name, CaseAssets.asset_ip, CaseAssets.asset_domain,
        CaseAssets.asset_description, CaseAssets.asset_tags, CaseAssets.asset_compromise_status_id,
        CaseAssets.asset_type_id, CaseAssets.asset_enrichment,
        AssetsType.asset_name.label('type_name'),
        AssetsType.asset_icon_compromised, AssetsType.asset_icon_not_compromised,
        AnalysisStatus.name.label('analysis_status'))
        .outerjoin(AssetsType, AssetsType.asset_id == CaseAssets.asset_type_id)
        .outerjoin(AnalysisStatus, AnalysisStatus.id == CaseAssets.analysis_status_id)
        .filter(CaseAssets.case_id == case_id)
        .order_by(CaseAssets.asset_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_asset_flag_rows(case_id):
    rows = (CaseAssetFlag.query.with_entities(
        CaseAssetFlag.asset_id, AssetFlag.id, AssetFlag.name, AssetFlag.color, AssetFlag.kind)
        .join(AssetFlag, AssetFlag.id == CaseAssetFlag.flag_id)
        .filter(CaseAssetFlag.case_id == case_id)
        .order_by(AssetFlag.sort_order, AssetFlag.id).all())
    return [row._asdict() for row in rows]


def case_graph_db_ioc_rows(case_id):
    rows = (Ioc.query.with_entities(
        Ioc.ioc_id, Ioc.ioc_value, Ioc.ioc_description, Ioc.ioc_tags, Ioc.ioc_type_id, Ioc.ioc_enrichment,
        IocType.type_name, Tlp.tlp_name)
        .outerjoin(IocType, IocType.type_id == Ioc.ioc_type_id)
        .outerjoin(Tlp, Tlp.tlp_id == Ioc.ioc_tlp_id)
        .filter(Ioc.case_id == case_id)
        .order_by(Ioc.ioc_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_event_rows(case_id):
    rows = (CasesEvent.query.with_entities(
        CasesEvent.event_id, CasesEvent.event_title, CasesEvent.event_date, CasesEvent.event_color,
        CasesEvent.event_in_graph, CasesEvent.parent_event_id, CasesEvent.event_tags,
        CasesEvent.event_content, CasesEvent.event_raw, CasesEvent.event_source,
        EventCategory.name.label('category_name'))
        .outerjoin(CaseEventCategory, CaseEventCategory.event_id == CasesEvent.event_id)
        .outerjoin(EventCategory, EventCategory.id == CaseEventCategory.category_id)
        .filter(CasesEvent.case_id == case_id)
        .order_by(CasesEvent.event_date, CasesEvent.event_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_event_dates(case_id, event_ids):
    """`{event_id: {event_id, event_date}}` of events of the case."""
    event_ids = list(event_ids)
    if not event_ids:
        return {}
    rows = (CasesEvent.query.with_entities(CasesEvent.event_id, CasesEvent.event_date)
            .filter(CasesEvent.case_id == case_id, CasesEvent.event_id.in_(event_ids)).all())
    return {row.event_id: row._asdict() for row in rows}


def case_graph_db_event_link_rows(case_id):
    """`{event_id, kind, object_id, role}` of every event link of the case."""
    assets = (CaseEventsAssets.query.with_entities(
        CaseEventsAssets.event_id, CaseEventsAssets.asset_id, CaseEventsAssets.role)
        .filter(CaseEventsAssets.case_id == case_id)
        .order_by(CaseEventsAssets.id).all())
    iocs = (CaseEventsIoc.query.with_entities(
        CaseEventsIoc.event_id, CaseEventsIoc.ioc_id, CaseEventsIoc.role)
        .filter(CaseEventsIoc.case_id == case_id)
        .order_by(CaseEventsIoc.id).all())
    links = [{'event_id': row.event_id, 'kind': 'asset', 'object_id': row.asset_id, 'role': row.role}
             for row in assets]
    links.extend({'event_id': row.event_id, 'kind': 'ioc', 'object_id': row.ioc_id, 'role': row.role}
                 for row in iocs)
    return links


def case_graph_db_ioc_asset_link_rows(case_id):
    rows = (IocAssetLink.query.with_entities(IocAssetLink.ioc_id, IocAssetLink.asset_id)
            .join(Ioc, Ioc.ioc_id == IocAssetLink.ioc_id)
            .join(CaseAssets, CaseAssets.asset_id == IocAssetLink.asset_id)
            .filter(Ioc.case_id == case_id, CaseAssets.case_id == case_id)
            .order_by(IocAssetLink.ioc_asset_link_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_evidence_rows(case_id):
    rows = (CaseReceivedFile.query.with_entities(
        CaseReceivedFile.id, CaseReceivedFile.filename, CaseReceivedFile.file_hash,
        CaseReceivedFile.file_description, CaseReceivedFile.date_added, CaseReceivedFile.acquisition_date,
        EvidenceTypes.name.label('type_name'))
        .outerjoin(EvidenceTypes, EvidenceTypes.id == CaseReceivedFile.type_id)
        .filter(CaseReceivedFile.case_id == case_id)
        .order_by(CaseReceivedFile.id).all())
    return [row._asdict() for row in rows]


def case_graph_db_task_rows(case_id):
    rows = (CaseTasks.query.with_entities(
        CaseTasks.id, CaseTasks.task_title, CaseTasks.task_description, CaseTasks.task_tags,
        CaseTasks.task_open_date, CaseTasks.task_close_date, TaskStatus.status_name)
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(CaseTasks.task_case_id == case_id)
        .order_by(CaseTasks.id).all())
    return [row._asdict() for row in rows]


def case_graph_db_note_rows(case_id):
    rows = (Notes.query.with_entities(
        Notes.note_id, Notes.note_title, Notes.note_creationdate, Notes.note_lastupdate)
        .filter(Notes.note_case_id == case_id)
        .order_by(Notes.note_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_alert_rows(case_id):
    rows = (Alert.query.with_entities(
        Alert.alert_id, Alert.alert_title, Alert.alert_source_event_time, Alert.alert_tags,
        Severity.severity_name)
        .join(AlertCaseAssociation, AlertCaseAssociation.alert_id == Alert.alert_id)
        .outerjoin(Severity, Severity.severity_id == Alert.alert_severity_id)
        .filter(AlertCaseAssociation.case_id == case_id)
        .order_by(Alert.alert_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_alert_link_rows(case_id, alert_ids):
    """`{alert_id, kind, object_id}` of the case assets / IOCs attached
    to the alerts."""
    alert_ids = list(alert_ids)
    if not alert_ids:
        return []
    assets = (db.session.query(alert_assets_association.c.alert_id, alert_assets_association.c.asset_id)
              .join(CaseAssets, CaseAssets.asset_id == alert_assets_association.c.asset_id)
              .filter(alert_assets_association.c.alert_id.in_(alert_ids), CaseAssets.case_id == case_id)
              .all())
    iocs = (db.session.query(alert_iocs_association.c.alert_id, alert_iocs_association.c.ioc_id)
            .join(Ioc, Ioc.ioc_id == alert_iocs_association.c.ioc_id)
            .filter(alert_iocs_association.c.alert_id.in_(alert_ids), Ioc.case_id == case_id)
            .all())
    links = [{'alert_id': row[0], 'kind': 'asset', 'object_id': row[1]} for row in assets]
    links.extend({'alert_id': row[0], 'kind': 'ioc', 'object_id': row[1]} for row in iocs)
    return links


def case_graph_db_ioc_cross_case_rows(case_id):
    """`{object_id: ioc_id, case_id: other_case_id}` for every other case
    holding an IOC with the same value and type (readable or not)."""
    other = aliased(Ioc)
    rows = (db.session.query(Ioc.ioc_id, other.case_id)
            .join(other, and_(other.ioc_value == Ioc.ioc_value, other.ioc_type_id == Ioc.ioc_type_id))
            .filter(Ioc.case_id == case_id, other.case_id.isnot(None), other.case_id != case_id)
            .distinct().all())
    return [{'object_id': row[0], 'case_id': row[1]} for row in rows]


def _normalized_asset_name(column):
    """Identity of a case asset in the asset registry. Must match
    `managed_assets_normalize_name` and `_normalized_case_asset_name` of
    `manage_managed_assets_db`."""
    return func.lower(func.btrim(func.regexp_replace(column, r'\s+', ' ', 'g')))


def case_graph_db_asset_managed_rows(case_id):
    """`{asset_id, managed_asset_id, criticality}` of the case assets
    covered by a managed asset of the case customer."""
    rows = (db.session.query(CaseAssets.asset_id, ManagedAsset.managed_asset_id, ManagedAsset.criticality)
            .join(Cases, Cases.case_id == CaseAssets.case_id)
            .join(ManagedAsset, and_(ManagedAsset.client_id == Cases.client_id,
                                     ManagedAsset.asset_type_id == CaseAssets.asset_type_id,
                                     ManagedAsset.normalized_name == _normalized_asset_name(CaseAssets.asset_name)))
            .filter(CaseAssets.case_id == case_id).all())
    return [row._asdict() for row in rows]


def case_graph_db_asset_cross_case_rows(case_id):
    """`{object_id: asset_id, case_id: other_case_id}` for every other case
    of the same customer holding an asset of the same managed asset."""
    other = aliased(CaseAssets)
    other_case = aliased(Cases)
    rows = (db.session.query(CaseAssets.asset_id, other.case_id)
            .join(Cases, Cases.case_id == CaseAssets.case_id)
            .join(ManagedAsset, and_(ManagedAsset.client_id == Cases.client_id,
                                     ManagedAsset.asset_type_id == CaseAssets.asset_type_id,
                                     ManagedAsset.normalized_name == _normalized_asset_name(CaseAssets.asset_name)))
            .join(other, and_(other.asset_type_id == CaseAssets.asset_type_id,
                              _normalized_asset_name(other.asset_name) == ManagedAsset.normalized_name))
            .join(other_case, and_(other_case.case_id == other.case_id,
                                   other_case.client_id == ManagedAsset.client_id))
            .filter(CaseAssets.case_id == case_id, other.case_id != case_id)
            .distinct().all())
    return [{'object_id': row[0], 'case_id': row[1]} for row in rows]


def case_graph_db_case_rows(case_ids):
    case_ids = list(case_ids)
    if not case_ids:
        return []
    rows = Cases.query.with_entities(Cases.case_id, Cases.name).filter(Cases.case_id.in_(case_ids)).all()
    return [row._asdict() for row in rows]


# ------------------------------------------------- Endpoint existence --

_KIND_COLUMNS = {
    'asset': (CaseAssets.asset_id, CaseAssets.case_id),
    'ioc': (Ioc.ioc_id, Ioc.case_id),
    'event': (CasesEvent.event_id, CasesEvent.case_id),
    'evidence': (CaseReceivedFile.id, CaseReceivedFile.case_id),
    'task': (CaseTasks.id, CaseTasks.task_case_id),
    'note': (Notes.note_id, Notes.note_case_id),
    'node': (CaseGraphNode.node_id, CaseGraphNode.case_id),
}


def case_graph_db_existing_ids(case_id, kind, object_ids):
    """The ids among `object_ids` of objects of `kind` in the case."""
    object_ids = list(object_ids)
    if not object_ids:
        return set()
    if kind == 'alert':
        rows = (AlertCaseAssociation.query.with_entities(AlertCaseAssociation.alert_id)
                .filter(AlertCaseAssociation.case_id == case_id,
                        AlertCaseAssociation.alert_id.in_(object_ids)).all())
        return {row[0] for row in rows}
    columns = _KIND_COLUMNS.get(kind)
    if columns is None:
        return set()
    id_column, case_column = columns
    rows = db.session.query(distinct(id_column)).filter(case_column == case_id, id_column.in_(object_ids)).all()
    return {row[0] for row in rows}
