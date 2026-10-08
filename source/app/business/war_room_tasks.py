#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Business layer for war-room tasks.

Mirrors `case_tasks` but scoped to a war room. A war-room task can
optionally point at a source case (and a source case task) so the
operator can promote a per-case task into a war-room-level
coordination item without losing the link.

Task management extensions (subtasks, status via task_status, tags,
search): parents can have children but children cannot; the tags
column is free-form comma-separated to match the rest of Iris; the
status column reuses the shared `task_status` taxonomy.

Assignment: one person (`assignee_id`) and any number of teams of the
same war room (`team_ids`). Members of a newly assigned team are
notified.
"""

import datetime

from app.db import db
from app.datamgmt.war_rooms.war_room_tasks_db import apply_assignee_filter
from app.datamgmt.war_rooms.war_room_tasks_db import apply_due_range_filter
from app.datamgmt.war_rooms.war_room_tasks_db import apply_mine_filter
from app.datamgmt.war_rooms.war_room_tasks_db import apply_search_filter
from app.datamgmt.war_rooms.war_room_tasks_db import apply_tag_filter
from app.datamgmt.war_rooms.war_room_tasks_db import apply_team_filter
from app.datamgmt.war_rooms.war_room_tasks_db import base_task_query
from app.datamgmt.war_rooms.war_room_tasks_db import subtasks_supported as _subtasks_supported
from app.datamgmt.war_rooms.war_room_tasks_db import task_teams_db_for_tasks
from app.datamgmt.war_rooms.war_room_tasks_db import task_teams_db_replace
from app.datamgmt.war_rooms.war_room_tasks_db import task_teams_db_room_team_ids
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomTask


_TITLE_MAX_LEN = 1024
_MAX_TEAMS = 50


def _validate_title(title):
    if not isinstance(title, str):
        raise BusinessProcessingError('Task title must be a string')
    stripped = title.strip()
    if not stripped:
        raise BusinessProcessingError('Task title is required')
    if len(stripped) > _TITLE_MAX_LEN:
        raise BusinessProcessingError(
            f'Task title must be at most {_TITLE_MAX_LEN} characters'
        )
    return stripped


def _normalize_tags(tags):
    """Trim + de-duplicate a comma-separated tag string.

    Accepts either a list or a comma-separated string; always returns
    a comma-separated string (or None if empty). Preserves user order
    on the first occurrence of each tag.
    """
    if tags is None:
        return None
    if isinstance(tags, list):
        items = tags
    elif isinstance(tags, str):
        items = tags.split(',')
    else:
        raise BusinessProcessingError('tags must be a string or list')
    seen = set()
    out = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        t = raw.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return ','.join(out) if out else None


def _validate_team_ids(war_room_id, team_ids):
    """De-duplicated list of team ids, all teams of `war_room_id`."""
    if team_ids is None:
        return []
    if not isinstance(team_ids, list):
        raise BusinessProcessingError('team_ids must be a list of team ids')
    if not all(isinstance(t, int) and not isinstance(t, bool) and t > 0 for t in team_ids):
        raise BusinessProcessingError('team_ids must be a list of team ids')
    team_ids = list(dict.fromkeys(team_ids))
    if not team_ids:
        return []
    if len(team_ids) > _MAX_TEAMS:
        raise BusinessProcessingError(f'A task can be assigned to at most {_MAX_TEAMS} teams')
    unknown = set(team_ids) - task_teams_db_room_team_ids(war_room_id, team_ids)
    if unknown:
        listed = ', '.join(f'#{t}' for t in sorted(unknown))
        raise BusinessProcessingError(f'Not a team of this war room: {listed}')
    return team_ids


def _validate_assignee(war_room_id, assignee_id):
    """The person assignee must be a member of the room or able to read
    it; same error for an unknown user so ids cannot be probed."""
    if assignee_id is None:
        return None
    if not isinstance(assignee_id, int) or isinstance(assignee_id, bool) or assignee_id <= 0:
        raise BusinessProcessingError('assignee_id must be a user id')
    from app.business.war_room_decisions import war_room_decisions_is_participant
    if not war_room_decisions_is_participant(war_room_id, assignee_id):
        raise BusinessProcessingError('Unknown or non-member user')
    return assignee_id


def war_room_task_teams(task_ids):
    """{task_id: [{team_id, name, color}]}, teams sorted by name."""
    teams = {}
    if not task_ids:
        return teams
    for row in task_teams_db_for_tasks(task_ids):
        teams.setdefault(row.task_id, []).append(
            {'team_id': row.team_id, 'name': row.name, 'color': row.color}
        )
    return teams


def _notify_assigned_teams(task, team_ids, actor_id):
    """Tell the members of newly assigned teams. Silent on failure, like
    the mention notifications."""
    if not team_ids:
        return
    try:
        from app.business.war_room_teams import war_room_team_member_user_ids
        from app.iris_engine.notifications.service import notify_many

        user_ids = war_room_team_member_user_ids(task.war_room_id, team_ids)
        if not user_ids:
            return
        names = ', '.join(t['name'] for t in war_room_task_teams([task.task_id]).get(task.task_id, [])
                          if t['team_id'] in team_ids)
        notify_many(
            user_ids=list(user_ids),
            event_type='task_assigned',
            title=f'War-room task assigned to {names}',
            body=task.title,
            link=f'/war-rooms/{task.war_room_id}/tasks?task={task.task_id}',
            source_type='war_room_task',
            source_id=task.task_id,
            exclude_user_ids=[actor_id] if actor_id else [],
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'war-room task team notification failed')


def war_room_task_list(war_room_id, q=None, status_ids=None, tags=None,
                       assignee_ids=None, parent_task_id=None,
                       due_from=None, due_to=None, include_no_due=True,
                       include_closed=True, page=None, per_page=None,
                       team_ids=None, mine_user_id=None):
    """List tasks with optional search + filters.

    All filters are AND-combined. `q` matches title or description
    case-insensitively. `tags` is a list of tag strings; a task matches
    if any of its comma-separated tags matches (case-insensitive
    substring on the CSV, bracketed by commas so "foo" doesn't match
    "foobar"). `assignee_ids` can include `0` to mean Unassigned, and
    `team_ids` `0` to mean "no team". `mine_user_id` keeps the tasks
    assigned to that user or to one of their teams.
    `parent_task_id`: pass `0`/`None` for top-level only via the flag
    on the REST layer — this helper simply forwards the value; use
    `-1` to include everything (no parent filter).

    Pagination: when `page` is provided, return a dict envelope with
    `total`, `data`, `last_page`, `current_page`, `next_page`. When
    `page` is None, return the raw row list (back-compat for callers
    that want everything at once).

    Due-date filter semantics: rows are kept if their `due_at` falls
    within `[due_from, due_to]` (either endpoint may be None to make
    that side open-ended). Rows with no due date are kept when
    `include_no_due=True`, so a filter like "due this week" doesn't
    silently drop the untriaged backlog.
    """
    query = base_task_query(war_room_id)

    if q:
        query = apply_search_filter(query, q.strip().lower())

    if status_ids:
        query = query.filter(WarRoomTask.status_id.in_(status_ids))

    if assignee_ids:
        query = apply_assignee_filter(query, assignee_ids)

    if team_ids:
        query = apply_team_filter(query, team_ids)

    if mine_user_id:
        query = apply_mine_filter(query, mine_user_id)

    if tags:
        query = apply_tag_filter(query, tags)

    if parent_task_id is not None and parent_task_id != -1:
        if parent_task_id == 0 and _subtasks_supported():
            query = query.filter(WarRoomTask.parent_task_id.is_(None))
        elif _subtasks_supported():
            query = query.filter(
                WarRoomTask.parent_task_id == parent_task_id
            )

    if due_from is not None or due_to is not None:
        query = apply_due_range_filter(query, due_from, due_to, include_no_due)

    if not include_closed:
        query = query.filter(WarRoomTask.closed_at.is_(None))

    query = query.order_by(WarRoomTask.created_at.desc())

    if page is None:
        return query.all()

    per_page = max(1, min(int(per_page or 25), 200))
    page = max(1, int(page))
    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    last_page = max(1, (total + per_page - 1) // per_page)
    next_page = page + 1 if page < last_page else None
    return {
        'total': total,
        'data': rows,
        'last_page': last_page,
        'current_page': page,
        'next_page': next_page,
    }


def war_room_task_get(war_room_id, task_id):
    row = WarRoomTask.query.filter_by(
        war_room_id=war_room_id, task_id=task_id
    ).first()
    if row is None:
        raise ObjectNotFoundError()
    return row


def _resolve_parent(war_room_id, parent_task_id):
    """Fetch + validate a candidate parent task.

    A parent must (1) exist, (2) live in the same war room, and
    (3) not itself be a subtask (single-level tree). Returns the
    parent row or raises BusinessProcessingError.
    """
    if parent_task_id is None:
        return None
    if not _subtasks_supported():
        raise BusinessProcessingError(
            'Subtasks are not available on this database yet'
        )
    parent = WarRoomTask.query.filter_by(
        war_room_id=war_room_id, task_id=parent_task_id
    ).first()
    if parent is None:
        raise BusinessProcessingError('Parent task not found')
    if parent.parent_task_id is not None:
        raise BusinessProcessingError(
            'Cannot nest subtasks more than one level deep'
        )
    return parent


def _mention_text(title, description):
    # Mentions in either the title or the body — description carries
    # the TipTap HTML for the mention span; title is plain text but
    # extract works safely on either (yields empty set for pure text).
    return ' '.join(filter(None, [title, description]))


def _fire_mention_notifications(task, actor_id, is_update, previous_text=None):
    """Notify war-room members mentioned in a task's title or description.
    On an update, only the mentions the save added (`previous_text` is
    `_mention_text` before it), so editing the task doesn't re-ping them.

    Silent on failure — a broken notification pipeline must not fail a
    task write.
    """
    try:
        from app.iris_engine.notifications.mentions import mentions_added_user_ids
        from app.iris_engine.notifications.service import notify_many
        from app.models.war_rooms import WarRoomMember

        mentioned = mentions_added_user_ids(previous_text, _mention_text(task.title, task.description),
                                            task.war_room_id)
        if not mentioned:
            return

        member_ids = {
            row.user_id for row in
            WarRoomMember.query
            .filter(WarRoomMember.war_room_id == task.war_room_id)
            .filter(WarRoomMember.user_id.in_(mentioned))
            .all()
        }
        if not member_ids:
            return

        verb = 'updated' if is_update else 'created'
        notify_many(
            user_ids=list(member_ids),
            event_type='mention',
            title='You were mentioned in a war-room task',
            body=f'{verb}: {task.title}',
            link=f'/war-rooms/{task.war_room_id}/tasks?task={task.task_id}',
            source_type='war_room_task',
            source_id=task.task_id,
            exclude_user_ids=[actor_id] if actor_id else [],
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'war-room task mention notification failed')


def war_room_task_create(war_room_id, title, description=None,
                         status_id=None, assignee_id=None, due_at=None,
                         source_case_id=None, source_case_task_id=None,
                         tags=None, parent_task_id=None,
                         created_by_id=None, team_ids=None):
    title = _validate_title(title)
    team_ids = _validate_team_ids(war_room_id, team_ids)
    assignee_id = _validate_assignee(war_room_id, assignee_id)
    _resolve_parent(war_room_id, parent_task_id)
    task = WarRoomTask()
    task.war_room_id = war_room_id
    task.title = title
    task.description = description
    task.status_id = status_id
    task.assignee_id = assignee_id
    task.due_at = due_at
    task.source_case_id = source_case_id
    task.source_case_task_id = source_case_task_id
    task.tags = _normalize_tags(tags)
    task.created_by_id = created_by_id
    if _subtasks_supported():
        task.parent_task_id = parent_task_id
    db.session.add(task)
    if team_ids:
        db.session.flush()
        task_teams_db_replace(task.task_id, team_ids, created_by_id)
    db.session.commit()
    track_activity(f'created war room task "{task.title}"', war_room_id=war_room_id)
    _fire_mention_notifications(task, created_by_id, is_update=False)
    _notify_assigned_teams(task, team_ids, created_by_id)
    task = call_modules_hook('on_postload_war_room_task_create', task)
    return task


def war_room_task_update(war_room_id, task_id, **fields):
    # `updated_by_id` is metadata about the actor, not a column on the
    # task row. Pop it up front so the setattr loop below doesn't try
    # to write it back to the DB.
    updated_by_id = fields.pop('updated_by_id', None)
    task = war_room_task_get(war_room_id, task_id)
    team_ids = None
    if 'team_ids' in fields:
        team_ids = _validate_team_ids(war_room_id, fields.pop('team_ids'))
    if 'assignee_id' in fields and fields['assignee_id'] != task.assignee_id:
        # Only a new assignee is checked: re-sending the current one (who
        # may have left the room since) keeps working.
        _validate_assignee(war_room_id, fields['assignee_id'])
    prior_title = task.title
    prior_description = task.description
    if 'title' in fields and fields['title'] is not None:
        task.title = _validate_title(fields['title'])
    if 'parent_task_id' in fields:
        new_parent_id = fields.pop('parent_task_id')
        if new_parent_id == task.task_id:
            raise BusinessProcessingError('A task cannot be its own parent')
        if new_parent_id is not None and _subtasks_supported():
            # If this task already has subtasks, it cannot itself
            # become a child — that would break the single-level rule.
            has_children = db.session.query(
                WarRoomTask.query.filter_by(
                    parent_task_id=task.task_id
                ).exists()
            ).scalar()
            if has_children:
                raise BusinessProcessingError(
                    'Cannot demote a task with subtasks into a subtask'
                )
        _resolve_parent(war_room_id, new_parent_id)
        if _subtasks_supported():
            task.parent_task_id = new_parent_id
    for f in ('description', 'status_id', 'assignee_id', 'due_at',
              'source_case_id', 'source_case_task_id'):
        if f in fields:
            setattr(task, f, fields[f])
    if 'tags' in fields:
        task.tags = _normalize_tags(fields['tags'])
    added_team_ids = []
    if team_ids is not None:
        added_team_ids = task_teams_db_replace(task.task_id, team_ids, updated_by_id)
    db.session.commit()
    track_activity(f'updated war room task "{task.title}"', war_room_id=war_room_id)
    _notify_assigned_teams(task, added_team_ids, updated_by_id)
    # Fire only when the mention-carrying fields changed.
    if task.title != prior_title or task.description != prior_description:
        _fire_mention_notifications(task, updated_by_id, is_update=True,
                                    previous_text=_mention_text(prior_title, prior_description))
    task = call_modules_hook('on_postload_war_room_task_update', task)
    return task


def war_room_task_close(war_room_id, task_id, closed_by_id=None):
    task = war_room_task_get(war_room_id, task_id)
    task.closed_at = datetime.datetime.utcnow()
    task.closed_by_id = closed_by_id
    db.session.commit()
    track_activity(f'closed war room task "{task.title}"', war_room_id=war_room_id)
    task = call_modules_hook('on_postload_war_room_task_close', task)
    return task


def war_room_task_reopen(war_room_id, task_id):
    task = war_room_task_get(war_room_id, task_id)
    task.closed_at = None
    task.closed_by_id = None
    db.session.commit()
    track_activity(f'reopened war room task "{task.title}"', war_room_id=war_room_id)
    task = call_modules_hook('on_postload_war_room_task_reopen', task)
    return task


def war_room_task_delete(war_room_id, task_id):
    task = war_room_task_get(war_room_id, task_id)
    title = task.title
    db.session.delete(task)
    db.session.commit()
    track_activity(f'deleted war room task "{title}"', war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_task_delete',
                      {'war_room_id': war_room_id, 'task_id': task_id})


def war_room_task_used_tags(war_room_id):
    """Return the distinct set of tags already used on this room's tasks.

    Powers autocomplete on the tag input. Case is preserved on first
    occurrence.
    """
    rows = (
        db.session.query(WarRoomTask.tags)
        .filter(
            WarRoomTask.war_room_id == war_room_id,
            WarRoomTask.tags.isnot(None),
            WarRoomTask.tags != '',
        )
        .all()
    )
    seen = {}
    for (raw,) in rows:
        for t in (raw or '').split(','):
            t = t.strip()
            if not t:
                continue
            key = t.lower()
            if key not in seen:
                seen[key] = t
    return sorted(seen.values(), key=lambda s: s.lower())
