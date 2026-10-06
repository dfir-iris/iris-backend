#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Business layer for the war-room decision register (D-n).

A decision carries a title, rationale, owner, approvers (each with a
verdict), the attached cases / case assets it applies to and a target
date & time (`target_at`, naive UTC). Numbers are allocated per room
(`max + 1`). Authorization is NOT done here: the REST blueprint checks
war-room access, approver identity and case access before calling in.

No module hook is fired: there is no `on_postload_war_room_decision_*`
hook seeded in `post_init.py`, and `call_modules_hook` raises on an
unknown hook name.
"""

import datetime

from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_approver_rows
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_asset_case_ids
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_asset_links
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_attached_case_ids
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_case_links
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_case_names
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_chat_candidates
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_chat_message_is_candidate
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_delete
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_get
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_get_approver
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_insert
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_list
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_member_ids
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_numbers
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_replace_approvers
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_replace_assets
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_replace_cases
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_superseded_by
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_user_names
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_users_by_handle
from app.datamgmt.war_rooms.war_room_decisions_db import war_room_decisions_db_verdicts
from app.db import db
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WAR_ROOM_DECISION_STATUSES
from app.models.war_rooms import WAR_ROOM_DECISION_VERDICTS
from app.models.war_rooms import WarRoomDecision


_TITLE_MAX_LEN = 256
_RATIONALE_MAX_LEN = 20000
_COMMENT_MAX_LEN = 4000
_SEARCH_MAX_LEN = 256
_MAX_APPROVERS = 50
_MAX_CASES = 500
_MAX_ASSETS = 500
_TARGET_AT_MAX_LEN = 64
_CREATE_STATUSES = ('proposed', 'approved')
_OPEN_STATUSES = ('proposed', 'approved')
_DECIDED_STATUSES = ('approved', 'rejected')

PATCHABLE_FIELDS = ('title', 'rationale', 'status', 'target_at', 'owner_id',
                    'approver_ids', 'case_ids', 'asset_ids')


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


# ----------------------------------------------------------- Validation --

def war_room_decisions_parse_target_at(raw):
    """Parse an ISO 8601 date-time into a naive UTC datetime.

    Accepts values with `Z`, with an explicit offset (converted to UTC)
    or naive (taken as UTC already). `None` / `''` clear the value.
    """
    if raw is None or raw == '':
        return None
    if not isinstance(raw, str) or len(raw) > _TARGET_AT_MAX_LEN:
        raise BusinessProcessingError('target_at must be an ISO 8601 date-time string')
    value = raw.strip()
    if value.endswith(('Z', 'z')):
        value = f'{value[:-1]}+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise BusinessProcessingError('target_at must be an ISO 8601 date-time string')
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if parsed.year < 1970:
        raise BusinessProcessingError('target_at is out of range')
    return parsed


def war_room_decisions_parse_id_list(raw, field, max_len):
    """Validate a list of integer ids; returns a de-duplicated list
    preserving order. `None` means an empty list."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BusinessProcessingError(f'{field} must be a list of integers')
    out = []
    seen = set()
    for value in raw:
        if not _is_int(value) or value <= 0:
            raise BusinessProcessingError(f'{field} must be a list of integers')
        if value not in seen:
            seen.add(value)
            out.append(value)
    if len(out) > max_len:
        raise BusinessProcessingError(f'{field} accepts at most {max_len} entries')
    return out


def war_room_decisions_parse_asset_ids(raw):
    return war_room_decisions_parse_id_list(raw, 'asset_ids', _MAX_ASSETS)


def war_room_decisions_parse_case_ids(raw):
    return war_room_decisions_parse_id_list(raw, 'case_ids', _MAX_CASES)


def _validate_title(title):
    if not isinstance(title, str):
        raise BusinessProcessingError('Decision title is required')
    stripped = title.strip()
    if not stripped:
        raise BusinessProcessingError('Decision title is required')
    if len(stripped) > _TITLE_MAX_LEN:
        raise BusinessProcessingError(f'Decision title must be at most {_TITLE_MAX_LEN} characters')
    return stripped


def _validate_rationale(rationale):
    if rationale is None:
        return None
    if not isinstance(rationale, str):
        raise BusinessProcessingError('rationale must be a string')
    if len(rationale) > _RATIONALE_MAX_LEN:
        raise BusinessProcessingError(f'rationale must be at most {_RATIONALE_MAX_LEN} characters')
    return rationale if rationale.strip() else None


def _validate_status(status, allowed):
    if not isinstance(status, str) or status not in allowed:
        raise BusinessProcessingError(f'status must be one of {", ".join(allowed)}')
    return status


def _validate_optional_id(value, field):
    if value is None:
        return None
    if not _is_int(value) or value <= 0:
        raise BusinessProcessingError(f'{field} must be an integer')
    return value


_NON_MEMBER_MESSAGE = 'Unknown or non-member user'


def _participant_ids(war_room_id, user_ids):
    """Subset of `user_ids` that are members of the room or hold an
    effective (read or full) access on it — which also covers admins."""
    members = war_room_decisions_db_member_ids(war_room_id, user_ids)
    from app.business.war_rooms_access import ac_fast_check_user_has_war_room_access
    from app.models.authorization import WarRoomAccessLevel
    allowed = set()
    for user_id in user_ids:
        if user_id in members or ac_fast_check_user_has_war_room_access(
            user_id, war_room_id,
            [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access],
        ) is not None:
            allowed.add(user_id)
    return allowed


def war_room_decisions_is_participant(war_room_id, user_id):
    """True when `user_id` is a member of the room or can read it."""
    return user_id in _participant_ids(war_room_id, [user_id])


def war_room_decisions_resolve_participant_handle(war_room_id, handle):
    """`(user_id, label)` of the room participant whose login or display
    name is `handle`. Unknown users and users outside the room get the
    same error, so a handle cannot be used to probe the user directory."""
    h = (handle or '').strip()
    if not h:
        raise BusinessProcessingError('Empty user mention')
    rows = war_room_decisions_db_users_by_handle(h)
    allowed = _participant_ids(war_room_id, [row.id for row in rows]) if rows else set()
    for row in rows:
        if row.id in allowed:
            return row.id, (row.name or row.user)
    raise BusinessProcessingError(f'{_NON_MEMBER_MESSAGE} @{h}')


def _validate_participants(war_room_id, user_ids):
    """Owner and approvers must be members of the room or hold an
    effective (read or full) access on it — which also covers admins."""
    if not user_ids:
        return
    if set(user_ids) - _participant_ids(war_room_id, user_ids):
        raise BusinessProcessingError(_NON_MEMBER_MESSAGE)


def _validate_case_ids(war_room_id, case_ids):
    if not case_ids:
        return
    attached = war_room_decisions_db_attached_case_ids(war_room_id)
    for case_id in case_ids:
        if case_id not in attached:
            raise BusinessProcessingError(f'Case #{case_id} is not attached to this war room')


def _validate_asset_ids(war_room_id, asset_ids):
    if not asset_ids:
        return
    attached = war_room_decisions_db_attached_case_ids(war_room_id)
    mapping = war_room_decisions_db_asset_case_ids(asset_ids)
    for asset_id in asset_ids:
        if mapping.get(asset_id) not in attached:
            raise BusinessProcessingError(f'Asset #{asset_id} does not belong to an attached case')


def war_room_decisions_asset_case_ids(asset_ids):
    """`{asset_id: case_id}` so the blueprint can check case access."""
    return war_room_decisions_db_asset_case_ids(asset_ids)


# ---------------------------------------------------------- Computation --

def war_room_decisions_is_overdue(decision, now=None):
    if decision.target_at is None or decision.implemented_at is not None:
        return False
    if decision.status not in _OPEN_STATUSES:
        return False
    return decision.target_at < (now or _utcnow())


def war_room_decisions_status_after_vote(current_status, verdicts):
    """Return the status a vote leads to, or None when unchanged.

    Only a `proposed` decision moves: any rejection rejects it, all
    approvers approving approves it.
    """
    if current_status != 'proposed' or not verdicts:
        return None
    if any(v == 'rejected' for v in verdicts):
        return 'rejected'
    if all(v == 'approved' for v in verdicts):
        return 'approved'
    return None


def _apply_status(decision, status, user_id, now):
    if status == decision.status:
        return
    decision.status = status
    if status in _DECIDED_STATUSES:
        decision.decided_at = now
        decision.decided_by_id = user_id
    elif status == 'proposed':
        decision.decided_at = None
        decision.decided_by_id = None


# --------------------------------------------------------------- Reads ---

def war_room_decisions_list(war_room_id, status=None, q=None, case_id=None):
    statuses = None
    if status:
        statuses = [s for s in status if s in WAR_ROOM_DECISION_STATUSES]
        if not statuses:
            return []
    if q is not None:
        q = q.strip()[:_SEARCH_MAX_LEN] or None
    return war_room_decisions_db_list(war_room_id, statuses=statuses, q=q, case_id=case_id)


def war_room_decisions_get(war_room_id, decision_id):
    decision = war_room_decisions_db_get(war_room_id, decision_id)
    if decision is None:
        raise ObjectNotFoundError()
    return decision


def war_room_decisions_is_approver(decision, user_id):
    return war_room_decisions_db_get_approver(decision.decision_id, user_id) is not None


def war_room_decisions_chat_candidates(war_room_id):
    return [
        {
            'message_id': r.message_id,
            'body': r.body,
            'author_id': r.author_id,
            'author_name': r.author_name,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in war_room_decisions_db_chat_candidates(war_room_id)
    ]


# -------------------------------------------------------------- Writes ---

def war_room_decisions_create(war_room_id, raw, created_by_id):
    """Create a decision from a raw request dict (allow-listed)."""
    title = _validate_title(raw.get('title'))
    rationale = _validate_rationale(raw.get('rationale'))
    status = _validate_status(raw.get('status') or 'proposed', _CREATE_STATUSES)
    target_at = war_room_decisions_parse_target_at(raw.get('target_at'))
    owner_id = _validate_optional_id(raw.get('owner_id'), 'owner_id')
    approver_ids = war_room_decisions_parse_id_list(raw.get('approver_ids'), 'approver_ids', _MAX_APPROVERS)
    case_ids = war_room_decisions_parse_case_ids(raw.get('case_ids'))
    asset_ids = war_room_decisions_parse_asset_ids(raw.get('asset_ids'))
    supersedes_id = _validate_optional_id(raw.get('supersedes_id'), 'supersedes_id')
    chat_message_id = _validate_optional_id(raw.get('chat_message_id'), 'chat_message_id')

    participants = list(approver_ids)
    if owner_id is not None and owner_id not in participants:
        participants.append(owner_id)
    _validate_participants(war_room_id, participants)
    _validate_case_ids(war_room_id, case_ids)
    _validate_asset_ids(war_room_id, asset_ids)
    if supersedes_id is not None and war_room_decisions_db_get(war_room_id, supersedes_id) is None:
        raise BusinessProcessingError('supersedes_id must reference a decision of this war room')
    if chat_message_id is not None and \
            not war_room_decisions_db_chat_message_is_candidate(war_room_id, chat_message_id):
        raise BusinessProcessingError('chat_message_id must reference an unlinked /decision message of this war room')

    now = _utcnow()

    def _build():
        decision = WarRoomDecision()
        decision.war_room_id = war_room_id
        decision.title = title
        decision.rationale = rationale
        decision.status = status
        decision.target_at = target_at
        decision.owner_id = owner_id
        decision.supersedes_id = supersedes_id
        decision.chat_message_id = chat_message_id
        decision.created_by_id = created_by_id
        decision.created_at = now
        decision.updated_at = now
        if status == 'approved':
            decision.decided_at = now
            decision.decided_by_id = created_by_id
        return decision

    decision = war_room_decisions_db_insert(_build, approver_ids, case_ids, asset_ids,
                                            superseded_id=supersedes_id)
    track_activity(f'created decision D-{decision.number} "{decision.title}"', war_room_id=war_room_id)
    return call_modules_hook('on_postload_war_room_decision_create', decision)


def war_room_decisions_update(war_room_id, decision_id, raw, updated_by_id):
    """Partial update; only `PATCHABLE_FIELDS` are considered.

    Every field is validated before anything is written so a rejected
    request leaves the session clean.
    """
    decision = war_room_decisions_get(war_room_id, decision_id)
    now = _utcnow()

    changes = {}
    if 'title' in raw:
        changes['title'] = _validate_title(raw['title'])
    if 'rationale' in raw:
        changes['rationale'] = _validate_rationale(raw['rationale'])
    if 'target_at' in raw:
        changes['target_at'] = war_room_decisions_parse_target_at(raw['target_at'])
    if 'owner_id' in raw:
        changes['owner_id'] = _validate_optional_id(raw['owner_id'], 'owner_id')
        if changes['owner_id'] is not None:
            _validate_participants(war_room_id, [changes['owner_id']])
    approver_ids = None
    if 'approver_ids' in raw:
        approver_ids = war_room_decisions_parse_id_list(raw['approver_ids'], 'approver_ids', _MAX_APPROVERS)
        _validate_participants(war_room_id, approver_ids)
    case_ids = None
    if 'case_ids' in raw:
        case_ids = war_room_decisions_parse_id_list(raw['case_ids'], 'case_ids', _MAX_CASES)
        _validate_case_ids(war_room_id, case_ids)
    asset_ids = None
    if 'asset_ids' in raw:
        asset_ids = war_room_decisions_parse_asset_ids(raw['asset_ids'])
        _validate_asset_ids(war_room_id, asset_ids)
    status = None
    if 'status' in raw:
        status = _validate_status(raw['status'], WAR_ROOM_DECISION_STATUSES)

    for field, value in changes.items():
        setattr(decision, field, value)
    if approver_ids is not None:
        war_room_decisions_db_replace_approvers(decision.decision_id, approver_ids)
    if case_ids is not None:
        war_room_decisions_db_replace_cases(decision.decision_id, case_ids)
    if asset_ids is not None:
        war_room_decisions_db_replace_assets(decision.decision_id, asset_ids)
    if status is not None:
        _apply_status(decision, status, updated_by_id, now)

    decision.updated_at = now
    db.session.commit()
    track_activity(f'updated decision D-{decision.number} "{decision.title}"', war_room_id=war_room_id)
    return call_modules_hook('on_postload_war_room_decision_update', decision)


def war_room_decisions_vote(war_room_id, decision_id, user_id, verdict, comment=None):
    """Record the caller's verdict. The blueprint has already checked
    that the caller is a listed approver.

    Returns `(decision, status_changed)`.
    """
    if not isinstance(verdict, str) or verdict not in WAR_ROOM_DECISION_VERDICTS:
        raise BusinessProcessingError(f'verdict must be one of {", ".join(WAR_ROOM_DECISION_VERDICTS)}')
    if comment is not None:
        if not isinstance(comment, str):
            raise BusinessProcessingError('comment must be a string')
        if len(comment) > _COMMENT_MAX_LEN:
            raise BusinessProcessingError(f'comment must be at most {_COMMENT_MAX_LEN} characters')
        comment = comment.strip() or None

    decision = war_room_decisions_get(war_room_id, decision_id)
    approver = war_room_decisions_db_get_approver(decision.decision_id, user_id)
    if approver is None:
        raise BusinessProcessingError('Only listed approvers can vote on this decision')
    if decision.status == 'superseded':
        raise BusinessProcessingError('This decision has been superseded')

    now = _utcnow()
    approver.verdict = verdict
    approver.comment = comment
    approver.responded_at = now
    db.session.flush()

    new_status = war_room_decisions_status_after_vote(
        decision.status, war_room_decisions_db_verdicts(decision.decision_id)
    )
    if new_status is not None:
        _apply_status(decision, new_status, user_id, now)
    decision.updated_at = now
    db.session.commit()
    track_activity(f'voted {verdict} on decision D-{decision.number}', war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_decision_vote', {
        **_hook_ref(decision), 'status': decision.status, 'status_changed': new_status is not None,
        'voter_id': user_id, 'verdict': verdict, 'comment': comment,
    })
    return decision, new_status is not None


def war_room_decisions_set_implemented(war_room_id, decision_id, user_id, implemented):
    decision = war_room_decisions_get(war_room_id, decision_id)
    now = _utcnow()
    if implemented:
        if decision.implemented_at is None:
            decision.implemented_at = now
            decision.implemented_by_id = user_id
    else:
        decision.implemented_at = None
        decision.implemented_by_id = None
    decision.updated_at = now
    db.session.commit()
    verb = 'implemented' if implemented else 'reopened'
    track_activity(f'{verb} decision D-{decision.number}', war_room_id=war_room_id)
    hook = 'on_postload_war_room_decision_implement' if implemented else 'on_postload_war_room_decision_reopen'
    return call_modules_hook(hook, decision)


def war_room_decisions_delete(war_room_id, decision_id):
    decision = war_room_decisions_get(war_room_id, decision_id)
    deleted = _hook_ref(decision)
    war_room_decisions_db_delete(decision)
    track_activity(f'deleted decision D-{deleted["number"]}', war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_decision_delete', deleted)


def _hook_ref(decision) -> dict:
    """What hooks get of a decision when the row itself is gone or beside
    the point (vote, delete)."""
    return {'war_room_id': decision.war_room_id, 'decision_id': decision.decision_id,
            'number': decision.number, 'title': decision.title}


# ---------------------------------------------------------- Serializer ---

def _iso(value):
    return value.isoformat() if value else None


def war_room_decisions_referenced_case_ids(decisions):
    """Case ids a set of decisions points at (directly or via assets);
    the blueprint checks the caller's access on each."""
    ids = [d.decision_id for d in decisions]
    case_ids = {r.case_id for r in war_room_decisions_db_case_links(ids)}
    case_ids.update(r.case_id for r in war_room_decisions_db_asset_links(ids) if r.case_id is not None)
    return case_ids


def _load_context(decisions):
    ids = [d.decision_id for d in decisions]
    case_links = war_room_decisions_db_case_links(ids)
    asset_links = war_room_decisions_db_asset_links(ids)
    approver_rows = war_room_decisions_db_approver_rows(ids)

    user_ids = set()
    for d in decisions:
        user_ids.update(u for u in (d.owner_id, d.decided_by_id, d.created_by_id) if u)
    supersedes_ids = {d.supersedes_id for d in decisions if d.supersedes_id}

    ctx = {
        'cases': {},
        'assets': {},
        'approvers': {},
        'case_names': war_room_decisions_db_case_names({r.case_id for r in case_links}),
        'user_names': war_room_decisions_db_user_names(user_ids),
        'numbers': war_room_decisions_db_numbers(supersedes_ids),
        'superseded_by': war_room_decisions_db_superseded_by(ids),
    }
    for r in case_links:
        ctx['cases'].setdefault(r.decision_id, []).append(r.case_id)
    for r in asset_links:
        ctx['assets'].setdefault(r.decision_id, []).append(
            {'asset_id': r.asset_id, 'asset_name': r.asset_name, 'case_id': r.case_id}
        )
    for r in approver_rows:
        ctx['approvers'].setdefault(r.decision_id, []).append({
            'user_id': r.user_id,
            'user_name': r.user_name,
            'verdict': r.verdict,
            'comment': r.comment,
            'responded_at': _iso(r.responded_at),
        })
    return ctx


def _serialize_one(d, ctx, readable_case_ids, now):
    case_ids = ctx['cases'].get(d.decision_id, [])
    cases = []
    for case_id in case_ids:
        if case_id in readable_case_ids:
            cases.append({'case_id': case_id, 'case_name': ctx['case_names'].get(case_id), 'accessible': True})
        else:
            cases.append({'case_id': case_id, 'accessible': False})
    assets = []
    for a in ctx['assets'].get(d.decision_id, []):
        if a['case_id'] in readable_case_ids:
            assets.append({**a, 'accessible': True})
        else:
            assets.append({'asset_id': a['asset_id'], 'accessible': False})
    names = ctx['user_names']
    supersedes_number = ctx['numbers'].get(d.supersedes_id) if d.supersedes_id else None
    return {
        'decision_id': d.decision_id,
        'war_room_id': d.war_room_id,
        'number': d.number,
        'ref': f'D-{d.number}',
        'title': d.title,
        'rationale': d.rationale,
        'status': d.status,
        'target_at': _iso(d.target_at),
        'is_overdue': war_room_decisions_is_overdue(d, now),
        'owner_id': d.owner_id,
        'owner_name': names.get(d.owner_id) if d.owner_id else None,
        'supersedes_id': d.supersedes_id,
        'supersedes_ref': f'D-{supersedes_number}' if supersedes_number else None,
        'superseded_by_id': ctx['superseded_by'].get(d.decision_id),
        'chat_message_id': d.chat_message_id,
        'decided_at': _iso(d.decided_at),
        'decided_by_id': d.decided_by_id,
        'decided_by_name': names.get(d.decided_by_id) if d.decided_by_id else None,
        'implemented_at': _iso(d.implemented_at),
        'implemented_by_id': d.implemented_by_id,
        'created_at': _iso(d.created_at),
        'created_by_id': d.created_by_id,
        'created_by_name': names.get(d.created_by_id) if d.created_by_id else None,
        'updated_at': _iso(d.updated_at),
        'approvers': ctx['approvers'].get(d.decision_id, []),
        'case_ids': list(case_ids),
        'cases': cases,
        'asset_ids': [a['asset_id'] for a in ctx['assets'].get(d.decision_id, [])],
        'assets': assets,
    }


def war_room_decisions_serialize(decisions, readable_case_ids):
    """Serialize decisions in a batch (no N+1). Cases / assets outside
    `readable_case_ids` are reduced to their id and `accessible: False`."""
    if not decisions:
        return []
    ctx = _load_context(decisions)
    now = _utcnow()
    readable = set(readable_case_ids or ())
    return [_serialize_one(d, ctx, readable, now) for d in decisions]


def war_room_decisions_linked_asset_ids(decision_id):
    return {r.asset_id for r in war_room_decisions_db_asset_links([decision_id])}


def war_room_decisions_linked_case_ids(decision_id):
    return {r.case_id for r in war_room_decisions_db_case_links([decision_id])}
