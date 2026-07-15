#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Persistence helpers for war-room chat.

Query builders and schema-probe helpers that would otherwise force the
business layer to import sqlalchemy directly. Kept behavior-preserving
one-for-one with the callers.
"""

from sqlalchemy import and_, desc, func, or_, text as _text

from app.db import db
from app.models.war_rooms import WarRoomChatMessage
from app.models.war_rooms import WarRoomChatPollOption
from app.models.war_rooms import WarRoomChatPollVote


def probe_column_exists(sql):
    """Run a schema probe SQL, returning (ok, exception_or_none).

    Kept generic so the caller supplies the exact SELECT — the caller
    already knows what column/table it's probing and what to log on
    failure.
    """
    try:
        with db.engine.connect() as conn:
            conn.execute(_text(sql))
        return True, None
    except Exception as e:
        return False, e


def build_case_activity_query(attached_ids, before_dt, search, limit):
    from app.models.authorization import User
    from app.models.models import UserActivity

    q = (
        db.session.query(
            UserActivity.id,
            UserActivity.user_id,
            UserActivity.case_id,
            UserActivity.activity_date,
            UserActivity.activity_desc,
            User.user.label('user_login'),
            User.name.label('user_name'),
        )
        .outerjoin(User, User.id == UserActivity.user_id)
        .filter(and_(
            UserActivity.case_id.in_(attached_ids),
            UserActivity.display_in_ui == True,
            UserActivity.activity_desc.notlike('[Unbound]%'),
            UserActivity.activity_desc.notlike('Started a search for %'),
            UserActivity.activity_desc.notlike('Updated global task %'),
            UserActivity.activity_desc.notlike('Created new global task %'),
            UserActivity.activity_desc.notlike('Started a new case creation %'),
        ))
    )
    if before_dt is not None:
        q = q.filter(UserActivity.activity_date < before_dt)
    needle = search.strip() if isinstance(search, str) else None
    if needle:
        q = q.filter(UserActivity.activity_desc.ilike(f'%{needle}%'))

    return q.order_by(desc(UserActivity.activity_date)).limit(limit).all()


def apply_topic_filter(query, topic_ids, main_id):
    wants_main = main_id is not None and main_id in topic_ids
    clauses = [WarRoomChatMessage.topic_id.in_(list(topic_ids))]
    if wants_main:
        clauses.append(WarRoomChatMessage.topic_id.is_(None))
    return query.filter(or_(*clauses))


def trace_pin_filter(pin_kinds):
    """Build the OR clause used when the pin column exists.

    Returns a clause that can be added to `.filter()` via positional
    arg (the caller controls the whole filter list).
    """
    return or_(
        WarRoomChatMessage.kind.in_(pin_kinds),
        WarRoomChatMessage.is_pinned.is_(True),
    )


def build_threads_query(war_room_id, limit):
    from app.models.authorization import User

    reply_stats = (
        db.session.query(
            WarRoomChatMessage.parent_message_id.label('root_id'),
            func.count(WarRoomChatMessage.message_id).label('reply_count'),
            func.max(WarRoomChatMessage.created_at).label('last_reply_at'),
        )
        .filter(WarRoomChatMessage.war_room_id == war_room_id)
        .filter(WarRoomChatMessage.parent_message_id.isnot(None))
        .group_by(WarRoomChatMessage.parent_message_id)
        .subquery()
    )

    q = (
        db.session.query(
            WarRoomChatMessage.message_id,
            WarRoomChatMessage.war_room_id,
            WarRoomChatMessage.author_id,
            WarRoomChatMessage.body,
            WarRoomChatMessage.kind,
            WarRoomChatMessage.thread_title,
            WarRoomChatMessage.created_at,
            WarRoomChatMessage.deleted_at,
            User.user.label('author_login'),
            User.name.label('author_name'),
            reply_stats.c.reply_count,
            reply_stats.c.last_reply_at,
        )
        .outerjoin(User, User.id == WarRoomChatMessage.author_id)
        .outerjoin(reply_stats,
                   reply_stats.c.root_id == WarRoomChatMessage.message_id)
        .filter(WarRoomChatMessage.war_room_id == war_room_id)
        .filter(WarRoomChatMessage.parent_message_id.is_(None))
        .filter(
            (reply_stats.c.reply_count.isnot(None)) |
            (WarRoomChatMessage.thread_title.isnot(None))
        )
        .order_by(
            func.coalesce(reply_stats.c.last_reply_at,
                          WarRoomChatMessage.created_at).desc()
        )
        .limit(limit)
    )
    return q.all()


def poll_option_vote_counts(poll_id):
    return dict(
        db.session.query(
            WarRoomChatPollOption.option_id,
            func.count(WarRoomChatPollVote.option_id),
        )
        .outerjoin(WarRoomChatPollVote,
                   WarRoomChatPollVote.option_id == WarRoomChatPollOption.option_id)
        .filter(WarRoomChatPollOption.poll_id == poll_id)
        .group_by(WarRoomChatPollOption.option_id)
        .all()
    )
