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

"""Business layer for sharing war-room notes with attached cases.

Two deliveries:
  * `mirror` — every shared note is kept as a read-only case note in a
    locked `War room · <name>` directory of each target case. The war
    room is the source of truth: `war_room_note_shares_reconcile`
    computes the desired (source note, case) pairs from every mirror
    share and creates / rewrites / deletes case mirrors to match. It is
    idempotent and is triggered (best effort, after commit) by every
    write that can change the desired set.
  * `copy` — a one-time plain note per target case, in an unlocked
    directory of the same name. The share row is kept for audit and is
    never re-synced.

Authorization is NOT done here: the blueprint checks war-room write and
case `full_access` on the targets before calling in.
"""

import datetime
import logging

from app.business.notes import notes_create
from app.business.notes_directories import notes_directories_create
from app.datamgmt.war_rooms.war_room_note_shares_db import append_case_to_future_shares
from app.datamgmt.war_rooms.war_room_note_shares_db import bump_notes_state
from app.datamgmt.war_rooms.war_room_note_shares_db import create_mirror_directory
from app.datamgmt.war_rooms.war_room_note_shares_db import create_mirror_note
from app.datamgmt.war_rooms.war_room_note_shares_db import delete_directory
from app.datamgmt.war_rooms.war_room_note_shares_db import delete_mirror_note
from app.datamgmt.war_rooms.war_room_note_shares_db import directory_is_empty
from app.datamgmt.war_rooms.war_room_note_shares_db import find_copy_directory
from app.datamgmt.war_rooms.war_room_note_shares_db import get_default_note_custom_attributes
from app.datamgmt.war_rooms.war_room_note_shares_db import get_room_folder
from app.datamgmt.war_rooms.war_room_note_shares_db import get_room_note
from app.datamgmt.war_rooms.war_room_note_shares_db import get_share
from app.datamgmt.war_rooms.war_room_note_shares_db import get_user_login
from app.datamgmt.war_rooms.war_room_note_shares_db import get_user_names
from app.datamgmt.war_rooms.war_room_note_shares_db import get_war_room
from app.datamgmt.war_rooms.war_room_note_shares_db import list_attached_case_ids
from app.datamgmt.war_rooms.war_room_note_shares_db import list_cases_info
from app.datamgmt.war_rooms.war_room_note_shares_db import list_mirror_directories
from app.datamgmt.war_rooms.war_room_note_shares_db import list_mirror_index
from app.datamgmt.war_rooms.war_room_note_shares_db import list_mirror_notes
from app.datamgmt.war_rooms.war_room_note_shares_db import list_room_folders
from app.datamgmt.war_rooms.war_room_note_shares_db import list_room_notes
from app.datamgmt.war_rooms.war_room_note_shares_db import list_share_case_ids
from app.datamgmt.war_rooms.war_room_note_shares_db import list_shares
from app.datamgmt.war_rooms.war_room_note_shares_db import lock_war_room_note_shares
from app.datamgmt.war_rooms.war_room_note_shares_db import remove_case_from_shares
from app.datamgmt.war_rooms.war_room_note_shares_db import set_share_case_ids
from app.datamgmt.war_rooms.war_room_note_shares_db import update_mirror_note
from app.db import db
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.models import NoteDirectory
from app.models.models import Notes
from app.models.war_rooms import WarRoomNoteShare


_logger = logging.getLogger(__name__)

WAR_ROOM_NOTE_SHARE_SCOPES = ('all', 'cases')
WAR_ROOM_NOTE_SHARE_DELIVERIES = ('mirror', 'copy')

# `notes.note_title` is String(155).
_CASE_NOTE_TITLE_MAX = 155
_MAX_TARGET_CASES = 500
_MAX_COPY_CONTENT = 5 * 1024 * 1024


def war_room_note_shares_directory_name(war_room_name):
    """Name of the per-case directory holding a war room's shared notes."""
    return f'War room · {war_room_name or ""}'.strip()


def _case_note_title(title):
    title = (title or '').strip() or 'Untitled'
    return title[:_CASE_NOTE_TITLE_MAX]


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _current_user_id():
    try:
        from app.blueprints.iris_user import iris_current_user
        return iris_current_user.id
    except Exception:
        return None


# ------------------------------------------------------------ Parsing ----

def _parse_case_ids(raw_case_ids):
    if not isinstance(raw_case_ids, list):
        raise BusinessProcessingError('case_ids must be a list of case ids')
    if len(raw_case_ids) > _MAX_TARGET_CASES:
        raise BusinessProcessingError(f'At most {_MAX_TARGET_CASES} cases per share')
    case_ids = []
    for case_id in raw_case_ids:
        if not _is_int(case_id) or case_id <= 0:
            raise BusinessProcessingError('case_ids must be a list of case ids')
        if case_id not in case_ids:
            case_ids.append(case_id)
    return case_ids


def _parse_scope(scope):
    if scope not in WAR_ROOM_NOTE_SHARE_SCOPES:
        raise BusinessProcessingError("scope must be 'all' or 'cases'")
    return scope


def _check_attached(war_room_id, case_ids):
    attached = set(list_attached_case_ids(war_room_id))
    missing = [case_id for case_id in case_ids if case_id not in attached]
    if missing:
        raise BusinessProcessingError(
            f'Case(s) not attached to this war room: {", ".join(str(c) for c in missing)}'
        )


def war_room_note_shares_parse_source(war_room_id, note_id, folder_id):
    """Validate a share source: exactly one of note_id / folder_id, in this
    war room. Returns `(note_id, folder_id)`."""
    if (note_id is None) == (folder_id is None):
        raise BusinessProcessingError('Provide exactly one of note_id or folder_id')
    if note_id is not None:
        if not _is_int(note_id) or get_room_note(war_room_id, note_id) is None:
            raise BusinessProcessingError('Invalid note id for this war room')
        return note_id, None
    if not _is_int(folder_id) or get_room_folder(war_room_id, folder_id) is None:
        raise BusinessProcessingError('Invalid folder id for this war room')
    return None, folder_id


def war_room_note_shares_parse_create(war_room_id, raw):
    """Validate a create body (allow-listed fields only)."""
    note_id, folder_id = war_room_note_shares_parse_source(
        war_room_id, raw.get('note_id'), raw.get('folder_id'))
    scope = _parse_scope(raw.get('scope'))
    delivery = raw.get('delivery', 'mirror')
    if delivery not in WAR_ROOM_NOTE_SHARE_DELIVERIES:
        raise BusinessProcessingError("delivery must be 'mirror' or 'copy'")
    include_future = raw.get('include_future', False)
    if not isinstance(include_future, bool):
        raise BusinessProcessingError('include_future must be a boolean')
    case_ids = []
    if scope == 'cases':
        case_ids = _parse_case_ids(raw.get('case_ids'))
        if not case_ids:
            raise BusinessProcessingError('Select at least one case')
        _check_attached(war_room_id, case_ids)
    if delivery == 'copy':
        # A copy is one-shot: there is no future to include.
        include_future = False
    return {
        'note_id': note_id,
        'folder_id': folder_id,
        'scope': scope,
        'case_ids': case_ids,
        'include_future': include_future,
        'delivery': delivery,
    }


def war_room_note_shares_parse_update(war_room_id, share, raw):
    """Validate a PATCH body against `share`; returns the full new state
    `{scope, case_ids, include_future}`."""
    if share.delivery != 'mirror':
        raise BusinessProcessingError('Only mirror shares can be edited')
    scope = share.scope
    if 'scope' in raw:
        scope = _parse_scope(raw['scope'])
    include_future = share.include_future
    if 'include_future' in raw:
        if not isinstance(raw['include_future'], bool):
            raise BusinessProcessingError('include_future must be a boolean')
        include_future = raw['include_future']
    case_ids = list_share_case_ids([share.share_id]).get(share.share_id, [])
    if 'case_ids' in raw:
        case_ids = _parse_case_ids(raw['case_ids'])
        _check_attached(war_room_id, case_ids)
    if scope == 'cases' and not case_ids:
        raise BusinessProcessingError('Select at least one case')
    if scope == 'all':
        case_ids = []
    return {'scope': scope, 'case_ids': case_ids, 'include_future': include_future}


def war_room_note_shares_resolve_targets(war_room_id, scope, case_ids):
    """Current target cases of a share: every attached case for 'all',
    the listed ∩ attached cases for 'cases'. Order follows attachment."""
    return war_room_note_shares_resolve_targets_from(
        list_attached_case_ids(war_room_id), scope, case_ids)


def war_room_note_shares_current_targets(war_room_id, share):
    """Current target cases of an existing share."""
    case_ids = list_share_case_ids([share.share_id]).get(share.share_id, [])
    if share.delivery == 'copy':
        return case_ids
    return war_room_note_shares_resolve_targets(war_room_id, share.scope, case_ids)


def war_room_note_shares_resolve_targets_from(attached_case_ids, scope, case_ids):
    """Pure form of `war_room_note_shares_resolve_targets`."""
    if scope == 'all':
        return list(attached_case_ids)
    wanted = set(case_ids or [])
    return [case_id for case_id in attached_case_ids if case_id in wanted]


# ---------------------------------------------------------- Sources ----

def _children_map(folders):
    children = {}
    for folder in folders:
        children.setdefault(folder.parent_id, []).append(folder.id)
    return children


def _folder_subtree_ids(folder_id, children):
    seen = set()
    stack = [folder_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, []))
    return seen


def _share_source_note_ids(share, notes, children):
    """Ids of the war-room notes covered by a share (a note, or every note
    in a folder subtree), in note-id order."""
    if share.note_id is not None:
        return [share.note_id] if share.note_id in notes else []
    if share.folder_id is None:
        return []
    folder_ids = _folder_subtree_ids(share.folder_id, children)
    return sorted(note_id for note_id, note in notes.items() if note.folder_id in folder_ids)


def war_room_note_shares_compute_desired(shares, share_case_ids, attached_case_ids, notes, folders):
    """Pure diff input: the set of `(case_id, source_note_id)` pairs that
    must exist as mirrors.

    `shares`: mirror shares; `share_case_ids`: `{share_id: [case_id]}`;
    `notes`: `{note_id: WarRoomNote}`; `folders`: war-room folders.
    """
    attached = set(attached_case_ids)
    children = _children_map(folders)
    desired = set()
    for share in shares:
        if share.delivery != 'mirror':
            continue
        if share.scope == 'all':
            targets = attached
        else:
            targets = attached.intersection(share_case_ids.get(share.share_id, []))
        for note_id in _share_source_note_ids(share, notes, children):
            desired.update((case_id, note_id) for case_id in targets)
    return desired


# --------------------------------------------------------- Reconcile ----

def war_room_note_shares_reconcile(war_room_id, actor_id=None):
    """Bring every case mirror of `war_room_id` in line with its mirror shares.

    Idempotent. Concurrent calls for the same room are serialised by a
    transaction-scoped advisory lock, so the second one finds nothing
    left to do. Everything is written in one transaction; activity is
    logged per case afterwards. Returns a summary
    `{created, updated, deleted, directories_deleted}`.
    """
    summary = {'created': 0, 'updated': 0, 'deleted': 0, 'directories_deleted': 0}
    if actor_id is None:
        actor_id = _current_user_id()

    lock_war_room_note_shares(war_room_id)
    war_room = get_war_room(war_room_id)
    if war_room is None:
        db.session.commit()
        return summary

    shares = list_shares(war_room_id, delivery='mirror')
    mirrors = list_mirror_notes(war_room_id)
    directories = list_mirror_directories(war_room_id)
    if not shares and not mirrors and not directories:
        db.session.commit()
        return summary

    attached = list_attached_case_ids(war_room_id)
    share_case_ids = list_share_case_ids(
        [share.share_id for share in shares if share.scope == 'cases'])
    notes = {note.note_id: note for note in list_room_notes(war_room_id)}
    folders = list_room_folders(war_room_id)
    desired = war_room_note_shares_compute_desired(
        shares, share_case_ids, attached, notes, folders)

    directory_name = war_room_note_shares_directory_name(war_room.name)
    directories_by_case = {}
    for directory in directories:
        # Oldest first: a stray duplicate is emptied and removed below.
        directories_by_case.setdefault(directory.case_id, directory)

    actor_login = get_user_login(actor_id)
    per_case = {}

    def _count(case_id, key):
        per_case.setdefault(case_id, {'created': 0, 'updated': 0, 'deleted': 0})[key] += 1
        summary[key] += 1

    def _directory_for(case_id):
        directory = directories_by_case.get(case_id)
        if directory is None:
            directory = create_mirror_directory(case_id, war_room_id, directory_name)
            directories_by_case[case_id] = directory
        elif directory.name != directory_name:
            # War room renamed since the directory was created.
            directory.name = directory_name
        return directory

    existing = set()
    for mirror in mirrors:
        key = (mirror.note_case_id, mirror.mirror_source_note_id)
        if mirror.mirror_source_note_id is None or key not in desired or key in existing:
            delete_mirror_note(mirror)
            _count(mirror.note_case_id, 'deleted')
            continue
        existing.add(key)
        source = notes[mirror.mirror_source_note_id]
        directory = _directory_for(mirror.note_case_id)
        title = _case_note_title(source.title)
        stale = (mirror.note_title != title
                 or mirror.note_content != source.content
                 or mirror.directory_id != directory.id)
        if stale:
            content_changed = mirror.note_title != title or mirror.note_content != source.content
            update_mirror_note(mirror, title, source.content, directory.id, actor_id, actor_login)
            if content_changed:
                _count(mirror.note_case_id, 'updated')

    for case_id, note_id in sorted(desired - existing):
        source = notes[note_id]
        directory = _directory_for(case_id)
        create_mirror_note(case_id, directory.id, war_room_id, note_id,
                           _case_note_title(source.title), source.content,
                           actor_id, actor_login)
        _count(case_id, 'created')

    desired_cases = {case_id for case_id, _ in desired}
    for directory in directories:
        keep = directories_by_case.get(directory.case_id) is directory and directory.case_id in desired_cases
        if keep:
            continue
        if directory_is_empty(directory):
            delete_directory(directory)
            summary['directories_deleted'] += 1

    for case_id in per_case:
        bump_notes_state(case_id, actor_id)

    db.session.commit()

    for case_id, counts in per_case.items():
        parts = [f'{counts[key]} {key}' for key in ('created', 'updated', 'deleted') if counts[key]]
        try:
            track_activity(
                f'synced war room "{war_room.name}" shared notes ({", ".join(parts)})',
                caseid=case_id, user_id_override=actor_id,
            )
        except Exception:
            _logger.exception('war-room note share: activity tracking failed')
            db.session.rollback()
    return summary


def war_room_note_shares_reconcile_safe(war_room_id, actor_id=None):
    """Best-effort reconcile for trigger call sites. NEVER raises: the
    caller's own write is already committed, and a failed sync only
    leaves mirrors stale until the next trigger or a manual resync."""
    try:
        war_room_note_shares_reconcile(war_room_id, actor_id=actor_id)
        return True
    except Exception:
        _logger.exception('war-room note share: reconcile failed for war room %s', war_room_id)
        try:
            db.session.rollback()
        except Exception:
            _logger.exception('war-room note share: rollback failed')
        return False


def war_room_note_shares_on_case_attached(war_room_id, case_id, actor_id=None):
    """Attach trigger: extend `include_future` case-scoped shares with the
    new case, then reconcile. Never raises."""
    try:
        if append_case_to_future_shares(war_room_id, case_id):
            db.session.commit()
    except Exception:
        _logger.exception('war-room note share: extending shares failed')
        try:
            db.session.rollback()
        except Exception:
            _logger.exception('war-room note share: rollback failed')
    return war_room_note_shares_reconcile_safe(war_room_id, actor_id=actor_id)


def war_room_note_shares_on_case_detached(war_room_id, case_id, actor_id=None):
    """Detach trigger: forget the case in mirror shares, then reconcile
    (which removes the room's mirrors from that case). Never raises."""
    try:
        remove_case_from_shares(war_room_id, case_id)
        db.session.commit()
    except Exception:
        _logger.exception('war-room note share: pruning shares failed')
        try:
            db.session.rollback()
        except Exception:
            _logger.exception('war-room note share: rollback failed')
    return war_room_note_shares_reconcile_safe(war_room_id, actor_id=actor_id)


# --------------------------------------------------------------- CRUD ----

def war_room_note_shares_list(war_room_id, note_id=None, folder_id=None):
    return list_shares(war_room_id, note_id=note_id, folder_id=folder_id)


def war_room_note_shares_get(war_room_id, share_id):
    share = get_share(war_room_id, share_id)
    if share is None:
        raise ObjectNotFoundError()
    return share


def war_room_note_shares_source_notes(war_room_id, note_id=None, folder_id=None):
    """War-room notes covered by a note / folder source, note-id order."""
    notes = {note.note_id: note for note in list_room_notes(war_room_id)}
    children = _children_map(list_room_folders(war_room_id))
    probe = WarRoomNoteShare(note_id=note_id, folder_id=folder_id)
    return [notes[note_id] for note_id in _share_source_note_ids(probe, notes, children)]


def war_room_note_shares_create(war_room_id, parsed, created_by_id):
    """Create a share from a `war_room_note_shares_parse_create` result.

    Mirror: reconciles right away (best effort). Copy: copies now, and
    returns `(share, copy_results)` where copy_results is
    `{case_id: {status, note_id?, message?}}` (empty for mirror)."""
    war_room = get_war_room(war_room_id)
    if war_room is None:
        raise ObjectNotFoundError()

    share = WarRoomNoteShare()
    share.war_room_id = war_room_id
    share.note_id = parsed['note_id']
    share.folder_id = parsed['folder_id']
    share.scope = parsed['scope']
    share.include_future = parsed['include_future']
    share.delivery = parsed['delivery']
    share.created_by_id = created_by_id
    share.updated_at = datetime.datetime.utcnow()
    db.session.add(share)
    db.session.flush()

    targets = war_room_note_shares_resolve_targets(war_room_id, parsed['scope'], parsed['case_ids'])
    if parsed['delivery'] == 'copy':
        # Audit trail: where the copies went, whatever the scope.
        set_share_case_ids(share, targets)
    elif parsed['scope'] == 'cases':
        set_share_case_ids(share, parsed['case_ids'])
    db.session.commit()
    share_id = share.share_id

    source_label = _source_label(war_room_id, share)
    track_activity(
        f'shared war room {source_label} with cases ({share.delivery})',
        war_room_id=war_room_id,
    )

    copy_results = {}
    if share.delivery == 'copy':
        sources = war_room_note_shares_source_notes(war_room_id, share.note_id, share.folder_id)
        for case_id in targets:
            copy_results[case_id] = _copy_notes_into_case(war_room, case_id, sources)
    else:
        war_room_note_shares_reconcile_safe(war_room_id, actor_id=created_by_id)

    share = call_modules_hook('on_postload_war_room_note_share_create', war_room_note_shares_get(war_room_id, share_id))
    return share, copy_results


def war_room_note_shares_update(war_room_id, share, parsed, updated_by_id):
    share.scope = parsed['scope']
    share.include_future = parsed['include_future']
    share.updated_at = datetime.datetime.utcnow()
    set_share_case_ids(share, parsed['case_ids'] if parsed['scope'] == 'cases' else [])
    db.session.commit()
    share_id = share.share_id
    track_activity(
        f'updated war room note share #{share_id}',
        war_room_id=war_room_id,
    )
    war_room_note_shares_reconcile_safe(war_room_id, actor_id=updated_by_id)
    return call_modules_hook('on_postload_war_room_note_share_update', war_room_note_shares_get(war_room_id, share_id))


def war_room_note_shares_delete(war_room_id, share, deleted_by_id):
    deleted = {'war_room_id': war_room_id, 'share_id': share.share_id, 'note_id': share.note_id,
               'folder_id': share.folder_id, 'delivery': share.delivery}
    db.session.delete(share)
    db.session.commit()
    track_activity(f'removed war room note share #{deleted["share_id"]}', war_room_id=war_room_id)
    war_room_note_shares_reconcile_safe(war_room_id, actor_id=deleted_by_id)
    call_modules_hook('on_postload_war_room_note_share_delete', deleted)


def war_room_note_shares_resync(war_room_id, share, actor_id):
    """Force a reconcile. Unlike the triggers, failures surface."""
    share_id = share.share_id
    if share.delivery != 'mirror':
        raise BusinessProcessingError('Copy shares are one-time and cannot be re-synced')
    try:
        war_room_note_shares_reconcile(war_room_id, actor_id=actor_id)
    except Exception as e:
        _logger.exception('war-room note share: resync failed')
        db.session.rollback()
        raise BusinessProcessingError('Unable to synchronise the shared notes') from e
    return war_room_note_shares_get(war_room_id, share_id)


def _source_label(war_room_id, share):
    if share.note_id is not None:
        note = get_room_note(war_room_id, share.note_id)
        return f'note "{note.title}"' if note else f'note #{share.note_id}'
    folder = get_room_folder(war_room_id, share.folder_id)
    return f'folder "{folder.name}"' if folder else f'folder #{share.folder_id}'


# --------------------------------------------------------------- Copy ----

def _copy_directory(war_room, case_id):
    name = war_room_note_shares_directory_name(war_room.name)
    directory = find_copy_directory(case_id, name)
    if directory is None:
        directory = NoteDirectory(name=name, parent_id=None, case_id=case_id)
        notes_directories_create(directory)
    return directory


def _copy_one(directory_id, case_id, title, content):
    note = Notes()
    note.note_title = _case_note_title(title)
    note.note_content = content
    note.directory_id = directory_id
    note.custom_attributes = get_default_note_custom_attributes()
    return notes_create(note, case_id)


def _copy_notes_into_case(war_room, case_id, sources):
    try:
        directory = _copy_directory(war_room, case_id)
        note_ids = [
            _copy_one(directory.id, case_id, source.title, source.content).note_id
            for source in sources
        ]
    except Exception:
        _logger.exception('war-room note share: copy into case %s failed', case_id)
        db.session.rollback()
        return {'status': 'error', 'message': 'Unable to copy the note into this case'}
    result = {'status': 'copied'}
    if len(note_ids) == 1:
        result['note_id'] = note_ids[0]
    return result


def war_room_note_shares_copy_to_cases(war_room_id, title, content_md, case_ids, user_id):
    """Copy one markdown document as a plain (editable) note into each
    case, in its `War room · <name>` directory.

    Used by SitRep publishing. The caller blueprint checks `full_access`
    per case; here every case must be attached to the war room. Note
    attribution follows the request principal (`notes_create`), which is
    `user_id` for REST callers. Returns
    `[{case_id, status: 'copied'|'error', note_id?, message?}]`.
    """
    war_room = get_war_room(war_room_id)
    if war_room is None:
        raise ObjectNotFoundError()
    if not isinstance(content_md, str):
        content_md = ''
    if len(content_md) > _MAX_COPY_CONTENT:
        raise BusinessProcessingError('Content is too large to be copied')
    attached = list_attached_case_ids(war_room_id)
    if case_ids == 'all':
        case_ids = attached
    attached_set = set(attached)

    results = []
    seen = set()
    for case_id in case_ids or []:
        if not _is_int(case_id) or case_id in seen:
            continue
        seen.add(case_id)
        if case_id not in attached_set:
            results.append({'case_id': case_id, 'status': 'error',
                            'message': 'Case is not attached to this war room'})
            continue
        try:
            directory = _copy_directory(war_room, case_id)
            note = _copy_one(directory.id, case_id, title, content_md)
        except Exception:
            _logger.exception('war-room note copy into case %s failed (user %s)', case_id, user_id)
            db.session.rollback()
            results.append({'case_id': case_id, 'status': 'error',
                            'message': 'Unable to copy the note into this case'})
            continue
        results.append({'case_id': case_id, 'status': 'copied', 'note_id': note.note_id})
    return results


# ---------------------------------------------------------- Describe ----

def war_room_note_shares_describe(war_room_id, shares, copy_results=None):
    """Shape shares for the API, WITH case names (the blueprint redacts
    targets the caller cannot read). Each target:
    `{case_id, case_name, customer_id, customer_name, status, mirror_note_id?}`."""
    copy_results = copy_results or {}
    if not shares:
        return []
    attached = list_attached_case_ids(war_room_id)
    share_case_ids = list_share_case_ids([share.share_id for share in shares])
    notes = {note.note_id: note for note in list_room_notes(war_room_id)}
    folders = list_room_folders(war_room_id)
    folder_names = {folder.id: folder.name for folder in folders}
    children = _children_map(folders)
    mirror_index = list_mirror_index(war_room_id)
    user_names = get_user_names(share.created_by_id for share in shares)

    target_ids = {}
    for share in shares:
        if share.delivery == 'copy':
            # Where the copies went (audit), attached or not any more.
            target_ids[share.share_id] = share_case_ids.get(share.share_id, [])
        else:
            target_ids[share.share_id] = war_room_note_shares_resolve_targets_from(
                attached, share.scope, share_case_ids.get(share.share_id, []))
    cases_info = list_cases_info(case_id for ids in target_ids.values() for case_id in ids)

    described = []
    for share in shares:
        source_ids = _share_source_note_ids(share, notes, children)
        if share.note_id is not None:
            note = notes.get(share.note_id)
            target_title = note.title if note else None
        else:
            target_title = folder_names.get(share.folder_id)
        targets = []
        for case_id in target_ids[share.share_id]:
            info = cases_info.get(case_id, {})
            target = {
                'case_id': case_id,
                'case_name': info.get('case_name'),
                'customer_id': info.get('customer_id'),
                'customer_name': info.get('customer_name'),
            }
            if share.delivery == 'copy':
                result = copy_results.get(case_id)
                target['status'] = result['status'] if result else 'copied'
            else:
                synced = all((case_id, note_id) in mirror_index for note_id in source_ids)
                target['status'] = 'synced' if synced else 'pending'
                if share.note_id is not None:
                    target['mirror_note_id'] = mirror_index.get((case_id, share.note_id))
            targets.append(target)
        described.append({
            'share_id': share.share_id,
            'war_room_id': share.war_room_id,
            'note_id': share.note_id,
            'folder_id': share.folder_id,
            'target_title': target_title,
            'scope': share.scope,
            'include_future': share.include_future,
            'delivery': share.delivery,
            'case_ids': share_case_ids.get(share.share_id, []),
            'note_count': len(source_ids),
            'created_at': share.created_at.isoformat() if share.created_at else None,
            'created_by_id': share.created_by_id,
            'created_by_name': user_names.get(share.created_by_id),
            'updated_at': share.updated_at.isoformat() if share.updated_at else None,
            'targets': targets,
        })
    return described


def war_room_note_shares_cases_info(case_ids):
    """`{case_id: {case_name, customer_id, customer_name}}` (preview)."""
    return list_cases_info(case_ids)
