#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Persistence helpers for the war-room decision register.

Query builders live here so the business layer stays free of direct
sqlalchemy imports (import-linter contract). Behavioural notes are on
the business-layer callers in `business/war_room_decisions.py`.
"""

from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import Cases
from app.models.errors import BusinessProcessingError
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomChatMessage
from app.models.war_rooms import WarRoomDecision
from app.models.war_rooms import WarRoomDecisionApprover
from app.models.war_rooms import WarRoomDecisionAsset
from app.models.war_rooms import WarRoomDecisionCase
from app.models.war_rooms import WarRoomMember


# Decisions inserted by the register itself post a `kind='decision'`
# system row in the chat stream; those must never be offered back as
# "promote to register" candidates.
_REGISTER_REF_TYPE = 'war_room_decision'
_CANDIDATES_LIMIT = 200


def _escape_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def war_room_decisions_db_list(war_room_id, statuses=None, q=None, case_id=None):
    query = WarRoomDecision.query.filter(WarRoomDecision.war_room_id == war_room_id)
    if statuses:
        query = query.filter(WarRoomDecision.status.in_(statuses))
    if q:
        query = query.filter(WarRoomDecision.title.ilike(f'%{_escape_like(q)}%', escape='\\'))
    if case_id is not None:
        query = query.filter(exists().where(
            WarRoomDecisionCase.decision_id == WarRoomDecision.decision_id,
            WarRoomDecisionCase.case_id == case_id,
        ))
    return query.order_by(WarRoomDecision.number.desc()).all()


def war_room_decisions_db_get(war_room_id, decision_id):
    return WarRoomDecision.query.filter(
        WarRoomDecision.war_room_id == war_room_id,
        WarRoomDecision.decision_id == decision_id,
    ).first()


def war_room_decisions_db_attached_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .all()
    )
    return {r.case_id for r in rows}


def war_room_decisions_db_asset_case_ids(asset_ids):
    """`{asset_id: case_id}` for the assets that exist."""
    if not asset_ids:
        return {}
    rows = (
        db.session.query(CaseAssets.asset_id, CaseAssets.case_id)
        .filter(CaseAssets.asset_id.in_(list(asset_ids)))
        .all()
    )
    return {r.asset_id: r.case_id for r in rows}


def war_room_decisions_db_member_ids(war_room_id, user_ids):
    if not user_ids:
        return set()
    rows = (
        db.session.query(WarRoomMember.user_id)
        .filter(WarRoomMember.war_room_id == war_room_id,
                WarRoomMember.user_id.in_(list(user_ids)))
        .all()
    )
    return {r.user_id for r in rows}


def war_room_decisions_db_users_by_handle(handle, limit=20):
    """(id, user, name) rows whose login or display name equals `handle`
    (case-insensitive), lowest id first. Callers filter on room access."""
    lowered = handle.lower()
    return (
        db.session.query(User.id, User.user, User.name)
        .filter(or_(func.lower(User.user) == lowered, func.lower(User.name) == lowered))
        .order_by(User.id)
        .limit(limit)
        .all()
    )


def war_room_decisions_db_chat_message_is_candidate(war_room_id, message_id):
    """True when the message is a live `/decision` row of this room not
    yet linked to a decision (and not a register system row)."""
    linked = db.session.query(WarRoomDecision.decision_id).filter(
        WarRoomDecision.chat_message_id == message_id
    )
    if db.session.query(linked.exists()).scalar():
        return False
    return db.session.query(
        WarRoomChatMessage.query.filter(
            WarRoomChatMessage.message_id == message_id,
            WarRoomChatMessage.war_room_id == war_room_id,
            WarRoomChatMessage.kind == 'decision',
            WarRoomChatMessage.deleted_at.is_(None),
            or_(WarRoomChatMessage.ref_type.is_(None),
                WarRoomChatMessage.ref_type != _REGISTER_REF_TYPE),
        ).exists()
    ).scalar()


def war_room_decisions_db_chat_candidates(war_room_id):
    linked = exists().where(WarRoomDecision.chat_message_id == WarRoomChatMessage.message_id)
    return (
        db.session.query(
            WarRoomChatMessage.message_id,
            WarRoomChatMessage.body,
            WarRoomChatMessage.created_at,
            WarRoomChatMessage.author_id,
            User.name.label('author_name'),
        )
        .outerjoin(User, User.id == WarRoomChatMessage.author_id)
        .filter(
            WarRoomChatMessage.war_room_id == war_room_id,
            WarRoomChatMessage.kind == 'decision',
            WarRoomChatMessage.deleted_at.is_(None),
            or_(WarRoomChatMessage.ref_type.is_(None),
                WarRoomChatMessage.ref_type != _REGISTER_REF_TYPE),
            ~linked,
        )
        .order_by(WarRoomChatMessage.created_at.desc())
        .limit(_CANDIDATES_LIMIT)
        .all()
    )


def _next_number(war_room_id):
    current = (
        db.session.query(func.max(WarRoomDecision.number))
        .filter(WarRoomDecision.war_room_id == war_room_id)
        .scalar()
    )
    return (current or 0) + 1


def _add_links(decision_id, approver_ids, case_ids, asset_ids):
    for user_id in approver_ids:
        db.session.add(WarRoomDecisionApprover(decision_id=decision_id, user_id=user_id))
    for case_id in case_ids:
        db.session.add(WarRoomDecisionCase(decision_id=decision_id, case_id=case_id))
    for asset_id in asset_ids:
        db.session.add(WarRoomDecisionAsset(decision_id=decision_id, asset_id=asset_id))


def war_room_decisions_db_insert(build_decision, approver_ids, case_ids, asset_ids,
                                 superseded_id=None):
    """Insert a decision with the next per-room number and commit.

    `build_decision` is a zero-arg factory returning a fresh, unsaved
    `WarRoomDecision`: on a numbering race (unique constraint hit) the
    session is rolled back, which expunges pending objects, so a second
    attempt needs brand new instances.
    """
    for attempt in range(2):
        decision = build_decision()
        decision.number = _next_number(decision.war_room_id)
        try:
            db.session.add(decision)
            db.session.flush()
            _add_links(decision.decision_id, approver_ids, case_ids, asset_ids)
            if superseded_id is not None:
                WarRoomDecision.query.filter(
                    WarRoomDecision.decision_id == superseded_id
                ).update({'status': 'superseded', 'updated_at': func.now()},
                         synchronize_session=False)
            db.session.commit()
            return decision
        except IntegrityError:
            db.session.rollback()
            if attempt == 1:
                break
    raise BusinessProcessingError('Could not register the decision, please retry')


def war_room_decisions_db_get_approver(decision_id, user_id):
    return WarRoomDecisionApprover.query.filter(
        WarRoomDecisionApprover.decision_id == decision_id,
        WarRoomDecisionApprover.user_id == user_id,
    ).first()


def war_room_decisions_db_approver_rows(decision_ids):
    if not decision_ids:
        return []
    return (
        db.session.query(
            WarRoomDecisionApprover.decision_id,
            WarRoomDecisionApprover.user_id,
            WarRoomDecisionApprover.verdict,
            WarRoomDecisionApprover.comment,
            WarRoomDecisionApprover.responded_at,
            User.name.label('user_name'),
        )
        .outerjoin(User, User.id == WarRoomDecisionApprover.user_id)
        .filter(WarRoomDecisionApprover.decision_id.in_(list(decision_ids)))
        .order_by(User.name.asc())
        .all()
    )


def war_room_decisions_db_verdicts(decision_id):
    rows = (
        db.session.query(WarRoomDecisionApprover.verdict)
        .filter(WarRoomDecisionApprover.decision_id == decision_id)
        .all()
    )
    return [r.verdict for r in rows]


def war_room_decisions_db_replace_approvers(decision_id, approver_ids):
    """Set the approver list; retained approvers keep their verdict."""
    wanted = set(approver_ids)
    existing = WarRoomDecisionApprover.query.filter(
        WarRoomDecisionApprover.decision_id == decision_id
    ).all()
    kept = set()
    for row in existing:
        if row.user_id in wanted:
            kept.add(row.user_id)
        else:
            db.session.delete(row)
    for user_id in approver_ids:
        if user_id not in kept:
            db.session.add(WarRoomDecisionApprover(decision_id=decision_id, user_id=user_id))


def war_room_decisions_db_replace_cases(decision_id, case_ids):
    WarRoomDecisionCase.query.filter(
        WarRoomDecisionCase.decision_id == decision_id
    ).delete(synchronize_session=False)
    for case_id in case_ids:
        db.session.add(WarRoomDecisionCase(decision_id=decision_id, case_id=case_id))


def war_room_decisions_db_replace_assets(decision_id, asset_ids):
    WarRoomDecisionAsset.query.filter(
        WarRoomDecisionAsset.decision_id == decision_id
    ).delete(synchronize_session=False)
    for asset_id in asset_ids:
        db.session.add(WarRoomDecisionAsset(decision_id=decision_id, asset_id=asset_id))


def war_room_decisions_db_case_links(decision_ids):
    if not decision_ids:
        return []
    return (
        db.session.query(WarRoomDecisionCase.decision_id, WarRoomDecisionCase.case_id)
        .filter(WarRoomDecisionCase.decision_id.in_(list(decision_ids)))
        .order_by(WarRoomDecisionCase.case_id.asc())
        .all()
    )


def war_room_decisions_db_asset_links(decision_ids):
    if not decision_ids:
        return []
    return (
        db.session.query(
            WarRoomDecisionAsset.decision_id,
            WarRoomDecisionAsset.asset_id,
            CaseAssets.asset_name,
            CaseAssets.case_id,
        )
        .join(CaseAssets, CaseAssets.asset_id == WarRoomDecisionAsset.asset_id)
        .filter(WarRoomDecisionAsset.decision_id.in_(list(decision_ids)))
        .order_by(WarRoomDecisionAsset.asset_id.asc())
        .all()
    )


def war_room_decisions_db_case_names(case_ids):
    if not case_ids:
        return {}
    rows = (
        db.session.query(Cases.case_id, Cases.name)
        .filter(Cases.case_id.in_(list(case_ids)))
        .all()
    )
    return {r.case_id: r.name for r in rows}


def war_room_decisions_db_user_names(user_ids):
    if not user_ids:
        return {}
    rows = (
        db.session.query(User.id, User.name)
        .filter(User.id.in_(list(user_ids)))
        .all()
    )
    return {r.id: r.name for r in rows}


def war_room_decisions_db_numbers(decision_ids):
    if not decision_ids:
        return {}
    rows = (
        db.session.query(WarRoomDecision.decision_id, WarRoomDecision.number)
        .filter(WarRoomDecision.decision_id.in_(list(decision_ids)))
        .all()
    )
    return {r.decision_id: r.number for r in rows}


def war_room_decisions_db_superseded_by(decision_ids):
    """`{superseded decision_id: newest superseding decision_id}`."""
    if not decision_ids:
        return {}
    rows = (
        db.session.query(WarRoomDecision.supersedes_id, func.max(WarRoomDecision.decision_id))
        .filter(WarRoomDecision.supersedes_id.in_(list(decision_ids)))
        .group_by(WarRoomDecision.supersedes_id)
        .all()
    )
    return {r[0]: r[1] for r in rows}


def war_room_decisions_db_delete(decision):
    db.session.delete(decision)
    db.session.commit()
