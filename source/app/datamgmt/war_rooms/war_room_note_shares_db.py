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

"""Persistence helpers for war-room note sharing (mirror / copy).

The reconciler in `business/war_room_note_shares.py` decides what has
to change; this module owns every query and every low-level write on
`notes` / `note_directory` for mirrors. Mirror writes deliberately
bypass the case-note business functions: those refuse writes to
mirrors (read-only enforcement) and depend on the request principal,
which is unbound when the reconcile is triggered by a collab flush on
socket disconnect.
"""

import datetime
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from sqlalchemy import func
from sqlalchemy import text

from app.db import db
from app.models.authorization import User
from app.models.cases import Cases
from app.models.collab import CollabDoc
from app.models.comments import Comments
from app.models.comments import NotesComments
from app.models.customers import Client
from app.models.models import CustomAttribute
from app.models.models import NoteDirectory
from app.models.models import NoteRevisions
from app.models.models import Notes
from app.models.models import NotesGroupLink
from app.models.models import ObjectState
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomNote
from app.models.war_rooms import WarRoomNoteFolder
from app.models.war_rooms import WarRoomNoteShare
from app.models.war_rooms import WarRoomNoteShareCase


# Namespace of the transaction-scoped advisory lock serialising the
# reconciles of one war room ('NS' for note shares).
_ADVISORY_LOCK_NAMESPACE = 0x4E53


# ------------------------------------------------------------ Locking ----

def lock_war_room_note_shares(war_room_id: int) -> None:
    """Serialise concurrent reconciles of the same war room.

    Transaction-scoped: released by the commit (or rollback) that ends
    the reconcile. A second reconcile waits, then sees the committed
    mirrors and has nothing left to do, so concurrent triggers never
    produce duplicate mirrors.
    """
    db.session.execute(
        text('SELECT pg_advisory_xact_lock(:namespace, :key)'),
        {'namespace': _ADVISORY_LOCK_NAMESPACE, 'key': int(war_room_id) % 2147483647},
    )


# ------------------------------------------------------------- Shares ----

def get_war_room(war_room_id: int) -> Optional[WarRoom]:
    return WarRoom.query.filter(WarRoom.war_room_id == war_room_id).first()


def list_shares(war_room_id: int, note_id: Optional[int] = None,
                folder_id: Optional[int] = None,
                delivery: Optional[str] = None) -> List[WarRoomNoteShare]:
    query = WarRoomNoteShare.query.filter(WarRoomNoteShare.war_room_id == war_room_id)
    if note_id is not None:
        query = query.filter(WarRoomNoteShare.note_id == note_id)
    if folder_id is not None:
        query = query.filter(WarRoomNoteShare.folder_id == folder_id)
    if delivery is not None:
        query = query.filter(WarRoomNoteShare.delivery == delivery)
    return query.order_by(WarRoomNoteShare.share_id.asc()).all()


def get_share(war_room_id: int, share_id: int) -> Optional[WarRoomNoteShare]:
    return WarRoomNoteShare.query.filter(
        WarRoomNoteShare.war_room_id == war_room_id,
        WarRoomNoteShare.share_id == share_id,
    ).first()


def list_share_case_ids(share_ids: Iterable[int]) -> Dict[int, List[int]]:
    share_ids = list(share_ids)
    result = {share_id: [] for share_id in share_ids}
    if not share_ids:
        return result
    rows = (
        db.session.query(WarRoomNoteShareCase.share_id, WarRoomNoteShareCase.case_id)
        .filter(WarRoomNoteShareCase.share_id.in_(share_ids))
        .order_by(WarRoomNoteShareCase.case_id.asc())
        .all()
    )
    for row in rows:
        result.setdefault(row.share_id, []).append(row.case_id)
    return result


def set_share_case_ids(share: WarRoomNoteShare, case_ids: Iterable[int]) -> None:
    """Replace the target rows of a share (no commit)."""
    WarRoomNoteShareCase.query.filter(
        WarRoomNoteShareCase.share_id == share.share_id
    ).delete(synchronize_session=False)
    for case_id in sorted(set(case_ids)):
        db.session.add(WarRoomNoteShareCase(share_id=share.share_id, case_id=case_id))


def append_case_to_future_shares(war_room_id: int, case_id: int) -> int:
    """Add `case_id` to every `scope='cases'` mirror share of the room
    flagged `include_future`. Returns the number of shares extended
    (no commit)."""
    shares = WarRoomNoteShare.query.filter(
        WarRoomNoteShare.war_room_id == war_room_id,
        WarRoomNoteShare.scope == 'cases',
        WarRoomNoteShare.include_future.is_(True),
        WarRoomNoteShare.delivery == 'mirror',
    ).all()
    extended = 0
    for share in shares:
        exists = WarRoomNoteShareCase.query.filter(
            WarRoomNoteShareCase.share_id == share.share_id,
            WarRoomNoteShareCase.case_id == case_id,
        ).first()
        if exists is None:
            db.session.add(WarRoomNoteShareCase(share_id=share.share_id, case_id=case_id))
            extended += 1
    return extended


def remove_case_from_shares(war_room_id: int, case_id: int) -> None:
    """Drop `case_id` from the target rows of every mirror share of the
    room (no commit). Copy shares keep their rows: they are the audit
    trail of where the copies went."""
    share_ids = [
        row.share_id for row in
        db.session.query(WarRoomNoteShare.share_id)
        .filter(WarRoomNoteShare.war_room_id == war_room_id,
                WarRoomNoteShare.delivery == 'mirror')
        .all()
    ]
    if not share_ids:
        return
    WarRoomNoteShareCase.query.filter(
        WarRoomNoteShareCase.share_id.in_(share_ids),
        WarRoomNoteShareCase.case_id == case_id,
    ).delete(synchronize_session=False)


# ------------------------------------------------- War-room side reads ----

def list_attached_case_ids(war_room_id: int) -> List[int]:
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(WarRoomCase.attached_at.asc(), WarRoomCase.case_id.asc())
        .all()
    )
    return [row.case_id for row in rows]


def list_room_notes(war_room_id: int) -> List[WarRoomNote]:
    return (
        WarRoomNote.query
        .filter(WarRoomNote.war_room_id == war_room_id)
        .order_by(WarRoomNote.note_id.asc())
        .all()
    )


def list_room_folders(war_room_id: int) -> List[WarRoomNoteFolder]:
    return (
        WarRoomNoteFolder.query
        .filter(WarRoomNoteFolder.war_room_id == war_room_id)
        .order_by(WarRoomNoteFolder.id.asc())
        .all()
    )


def get_room_note(war_room_id: int, note_id: int) -> Optional[WarRoomNote]:
    return WarRoomNote.query.filter(
        WarRoomNote.war_room_id == war_room_id,
        WarRoomNote.note_id == note_id,
    ).first()


def get_room_folder(war_room_id: int, folder_id: int) -> Optional[WarRoomNoteFolder]:
    return WarRoomNoteFolder.query.filter(
        WarRoomNoteFolder.war_room_id == war_room_id,
        WarRoomNoteFolder.id == folder_id,
    ).first()


def list_cases_info(case_ids: Iterable[int]) -> Dict[int, dict]:
    """`{case_id: {case_name, customer_id, customer_name}}` for existing cases."""
    case_ids = list(set(case_ids))
    if not case_ids:
        return {}
    rows = (
        db.session.query(
            Cases.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
        )
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .filter(Cases.case_id.in_(case_ids))
        .all()
    )
    return {
        row.case_id: {
            'case_name': row.case_name,
            'customer_id': row.customer_id,
            'customer_name': row.customer_name,
        }
        for row in rows
    }


def get_user_names(user_ids: Iterable[int]) -> Dict[int, str]:
    user_ids = [user_id for user_id in set(user_ids) if user_id is not None]
    if not user_ids:
        return {}
    rows = db.session.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
    return {row.id: row.name for row in rows}


def get_user_login(user_id: Optional[int]) -> Optional[str]:
    if user_id is None:
        return None
    row = db.session.query(User.user).filter(User.id == user_id).first()
    return row.user if row else None


# --------------------------------------------------- Case-side mirrors ----

def list_mirror_notes(war_room_id: int) -> List[Notes]:
    """Every case note mirroring a note of this war room, orphans included
    (source deleted, so `mirror_source_note_id` was SET NULL)."""
    return (
        Notes.query
        .filter(Notes.mirror_war_room_id == war_room_id)
        .order_by(Notes.note_id.asc())
        .all()
    )


def list_mirror_index(war_room_id: int) -> Dict[tuple, int]:
    """`{(case_id, source_note_id): mirror_note_id}` — cheap read for the
    share status columns."""
    rows = (
        db.session.query(Notes.note_id, Notes.note_case_id, Notes.mirror_source_note_id)
        .filter(Notes.mirror_war_room_id == war_room_id,
                Notes.mirror_source_note_id.isnot(None))
        .all()
    )
    return {(row.note_case_id, row.mirror_source_note_id): row.note_id for row in rows}


def list_mirror_directories(war_room_id: int) -> List[NoteDirectory]:
    return (
        NoteDirectory.query
        .filter(NoteDirectory.mirror_war_room_id == war_room_id)
        .order_by(NoteDirectory.id.asc())
        .all()
    )


def _default_note_custom_attributes():
    attribute = CustomAttribute.query.filter(CustomAttribute.attribute_for == 'note').first()
    return attribute.attribute_content if attribute else None


def _history_entry(note: Notes, user_id: Optional[int], user_login: Optional[str], action: str) -> None:
    """Same shape as `util.add_obj_history_entry`, without depending on
    the request principal."""
    timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()
    history = dict(note.modification_history) if isinstance(note.modification_history, dict) else {}
    history[timestamp] = {'user': user_login, 'user_id': user_id, 'action': action}
    note.modification_history = history


def _next_revision_number(note_id: int) -> int:
    current = (
        db.session.query(func.max(NoteRevisions.revision_number))
        .filter(NoteRevisions.note_id == note_id)
        .scalar()
    )
    return (current or 0) + 1


def create_mirror_directory(case_id: int, war_room_id: int, name: str) -> NoteDirectory:
    directory = NoteDirectory()
    directory.name = name
    directory.parent_id = None
    directory.case_id = case_id
    directory.mirror_war_room_id = war_room_id
    db.session.add(directory)
    db.session.flush()
    return directory


def create_mirror_note(case_id: int, directory_id: int, war_room_id: int,
                       source_note_id: int, title: str, content: Optional[str],
                       user_id: Optional[int], user_login: Optional[str]) -> Notes:
    """Insert a mirror note + its revision #1 + its history entry (no commit)."""
    now = datetime.datetime.utcnow()
    note = Notes()
    note.note_title = title
    note.note_content = content
    note.note_creationdate = now
    note.note_lastupdate = now
    note.note_user = user_id
    note.note_case_id = case_id
    note.directory_id = directory_id
    note.mirror_source_note_id = source_note_id
    note.mirror_war_room_id = war_room_id
    note.custom_attributes = _default_note_custom_attributes()
    _history_entry(note, user_id, user_login, 'created note (war room mirror)')
    db.session.add(note)
    db.session.flush()
    db.session.add(NoteRevisions(
        note_id=note.note_id,
        revision_number=1,
        note_title=note.note_title,
        note_content=note.note_content,
        note_user=user_id,
        revision_timestamp=now,
    ))
    return note


def update_mirror_note(note: Notes, title: str, content: Optional[str], directory_id: int,
                       user_id: Optional[int], user_login: Optional[str]) -> None:
    """Rewrite a stale mirror (no commit). A new revision is written when
    title or content changed, and the case collab snapshot is dropped so
    the next reader reseeds from the column."""
    content_changed = note.note_title != title or note.note_content != content
    note.directory_id = directory_id
    if not content_changed:
        return
    now = datetime.datetime.utcnow()
    note.note_title = title
    note.note_content = content
    note.note_lastupdate = now
    if user_id is not None:
        note.note_user = user_id
    _history_entry(note, user_id, user_login, 'updated note (war room mirror)')
    db.session.add(NoteRevisions(
        note_id=note.note_id,
        revision_number=_next_revision_number(note.note_id),
        note_title=title,
        note_content=content,
        note_user=user_id,
        revision_timestamp=now,
    ))
    delete_collab_doc(f'note:{note.note_id}')


def delete_collab_doc(doc_name: str) -> None:
    CollabDoc.query.filter(CollabDoc.doc_name == doc_name).delete(synchronize_session=False)


def delete_mirror_note(note: Notes) -> None:
    """Delete a mirror and everything hanging off it (no commit).

    Same teardown as `case_notes_db.delete_note`, minus the notes-state
    bump that needs the request principal (the reconciler bumps it once
    per case instead)."""
    note_id = note.note_id
    NotesGroupLink.query.filter(NotesGroupLink.note_id == note_id).delete(synchronize_session=False)
    comment_ids = [
        row.comment_id for row in
        db.session.query(NotesComments.comment_id)
        .filter(NotesComments.comment_note_id == note_id)
        .all()
    ]
    if comment_ids:
        NotesComments.query.filter(
            NotesComments.comment_id.in_(comment_ids)
        ).delete(synchronize_session=False)
        Comments.query.filter(Comments.comment_id.in_(comment_ids)).delete(synchronize_session=False)
    delete_collab_doc(f'note:{note_id}')
    # Revisions go with the ORM `versions` cascade (all, delete-orphan).
    db.session.delete(note)


def directory_is_empty(directory: NoteDirectory) -> bool:
    has_note = db.session.query(Notes.note_id).filter(Notes.directory_id == directory.id).first()
    if has_note is not None:
        return False
    has_child = (
        db.session.query(NoteDirectory.id)
        .filter(NoteDirectory.parent_id == directory.id)
        .first()
    )
    return has_child is None


def delete_directory(directory: NoteDirectory) -> None:
    db.session.delete(directory)


def bump_notes_state(case_id: int, user_id: Optional[int]) -> None:
    """Same effect as `states.update_notes_state`, with an explicit user."""
    state = ObjectState.query.filter(
        ObjectState.object_name == 'notes',
        ObjectState.object_case_id == case_id,
    ).first()
    now = datetime.datetime.utcnow()
    if state is not None:
        state.object_last_update = now
        state.object_state = state.object_state + 1
        state.object_updated_by_id = user_id
        return
    state = ObjectState()
    state.object_name = 'notes'
    state.object_state = 0
    state.object_last_update = now
    state.object_updated_by_id = user_id
    state.object_case_id = case_id
    db.session.add(state)


# ------------------------------------------------------- Copy delivery ----

def find_copy_directory(case_id: int, name: str) -> Optional[NoteDirectory]:
    """The plain (unlocked) top-level directory receiving copies."""
    return (
        NoteDirectory.query
        .filter(NoteDirectory.case_id == case_id,
                NoteDirectory.parent_id.is_(None),
                NoteDirectory.mirror_war_room_id.is_(None),
                NoteDirectory.name == name)
        .order_by(NoteDirectory.id.asc())
        .first()
    )


def get_default_note_custom_attributes():
    try:
        return _default_note_custom_attributes()
    except Exception:
        return None
