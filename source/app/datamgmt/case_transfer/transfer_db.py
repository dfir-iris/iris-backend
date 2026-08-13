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

"""Reads a case out of the database into bundle shape, and writes one back.

The whole point of this module is that no primary key ever survives a transfer.
Reading replaces every id with a `"<entity>:<source_pk>"` ref; writing allocates
fresh ids and rebuilds the graph through a ref map. Nothing the bundle contains
is ever used as a key, a path, or a column that was not named in
`transfer_spec` — which is what stops a hostile bundle from writing where it
should not.
"""

import uuid

from app.db import db
from app.datamgmt.case_transfer.transfer_spec import CASE_SPEC
from app.datamgmt.case_transfer.transfer_spec import ENTITIES
from app.datamgmt.case_transfer.transfer_spec import LOOKUPS
from app.datamgmt.case_transfer.transfer_spec import USER_REF_PREFIX
from app.models.authorization import User
from app.models.cases import Cases
from app.models.errors import ObjectNotFoundError

_ROOT_PARENT_SENTINEL = 0


def make_ref(kind, identifier):
    return f'{kind}:{identifier}'


class BundleCollector:
    """Accumulates the user and reference-data ids seen while reading a case."""

    def __init__(self):
        self.principal_ids = set()
        self.lookup_ids = {key: set() for key in LOOKUPS}


def _read_row(row, spec, collector):
    """Turn one ORM row into a bundle dict, rewriting every id into a ref."""
    payload = {}

    if spec.pk is not None:
        payload['_ref'] = make_ref(spec.key, getattr(row, spec.pk))

    if spec.blob_column is not None:
        blob_key = getattr(row, spec.blob_column)
        payload['_blob'] = str(blob_key) if blob_key is not None else None

    for column in spec.fields:
        payload[column] = getattr(row, column)

    for column in spec.user_refs:
        value = getattr(row, column)
        payload[column] = None
        if value is not None:
            collector.principal_ids.add(value)
            payload[column] = make_ref(USER_REF_PREFIX, value)

    for column, entity_key in spec.entity_refs.items():
        value = getattr(row, column)
        payload[column] = make_ref(entity_key, value) if value is not None else None

    for column, lookup_key in spec.lookup_refs.items():
        value = getattr(row, column)
        payload[column] = None
        if value is not None:
            collector.lookup_ids[lookup_key].add(value)
            payload[column] = make_ref(lookup_key, value)

    for column in spec.self_refs:
        value = getattr(row, column)
        # The datastore root uses 0 rather than NULL to mean "no parent".
        # Normalise it away so the importer has a single case to handle.
        if value in (None, _ROOT_PARENT_SENTINEL):
            payload[column] = None
        else:
            payload[column] = make_ref(spec.key, value)

    return payload


def _entity_query(spec, case_identifier):
    """Scope an entity to the case.

    Entities without their own `case_id` column (link tables, note revisions)
    are reached through the parent they belong to, so a link row can never be
    pulled in from a case the caller has no access to.
    """
    if spec.case_column is not None:
        return spec.model.query.filter(getattr(spec.model, spec.case_column) == case_identifier)

    from app.models.assets import CaseAssets
    from app.models.cases import CasesEvent
    from app.models.evidences import CaseReceivedFile
    from app.models.iocs import Ioc
    from app.models.models import CaseTasks
    from app.models.models import Notes

    joins = {
        'note_revision': (Notes, 'note_id', 'note_id', Notes.note_case_id),
        'ioc_asset_link': (Ioc, 'ioc_id', 'ioc_id', Ioc.case_id),
        'event_timeline': (CasesEvent, 'event_id', 'event_id', CasesEvent.case_id),
        'event_category': (CasesEvent, 'event_id', 'event_id', CasesEvent.case_id),
        'task_assignee': (CaseTasks, 'task_id', 'id', CaseTasks.task_case_id),
        'event_comment': (CasesEvent, 'comment_event_id', 'event_id', CasesEvent.case_id),
        'task_comment': (CaseTasks, 'comment_task_id', 'id', CaseTasks.task_case_id),
        'ioc_comment': (Ioc, 'comment_ioc_id', 'ioc_id', Ioc.case_id),
        'asset_comment': (CaseAssets, 'comment_asset_id', 'asset_id', CaseAssets.case_id),
        'evidence_comment': (CaseReceivedFile, 'comment_evidence_id', 'id', CaseReceivedFile.case_id),
        'note_comment': (Notes, 'comment_note_id', 'note_id', Notes.note_case_id),
    }

    parent_model, local_column, parent_column, case_column = joins[spec.key]
    return spec.model.query.join(
        parent_model,
        getattr(spec.model, local_column) == getattr(parent_model, parent_column)
    ).filter(case_column == case_identifier)


def read_case_bundle(case_identifier):
    """Read the whole case. Returns `(case_row, entities, collector)`."""
    collector = BundleCollector()

    case = Cases.query.filter(Cases.case_id == case_identifier).first()
    if case is None:
        return None, {}, collector

    case_row = _read_row(case, CASE_SPEC, collector)
    case_row['case_uuid'] = str(case.case_uuid)

    entities = {}
    for spec in ENTITIES:
        rows = _entity_query(spec, case_identifier).all()
        entities[spec.key] = [_read_row(row, spec, collector) for row in rows]

    return case_row, entities, collector


def read_datastore_blob_rows(case_identifier):
    """The `DataStoreFile` ORM rows, needed for on-disk paths at export time."""
    from app.models.models import DataStoreFile
    return DataStoreFile.query.filter(DataStoreFile.file_case_id == case_identifier).all()


def read_principals(user_ids):
    """The identity fields a target instance can match a user on."""
    if not user_ids:
        return []

    users = User.query.filter(User.id.in_(list(user_ids))).all()
    return [{
        'ref': make_ref(USER_REF_PREFIX, user.id),
        'uuid': str(user.uuid),
        'login': user.user,
        'name': user.name,
        'email': user.email,
        'external_id': user.external_id,
    } for user in users]


def read_lookups(lookup_ids):
    """Reference data by name, so the target can match or recreate it."""
    payload = {}

    for key, identifiers in lookup_ids.items():
        spec = LOOKUPS[key]
        payload[key] = []
        if not identifiers:
            continue

        pk_column = getattr(spec.model, spec.pk)
        for row in spec.model.query.filter(pk_column.in_(list(identifiers))).all():
            entry = {
                'ref': make_ref(key, getattr(row, spec.pk)),
                'name': getattr(row, spec.name_column),
            }
            for column in spec.extra_columns:
                entry[column] = getattr(row, column, None)
            payload[key].append(entry)

    return payload


def find_users_by_identity(uuids, external_ids, emails, logins):
    """One query per identity axis, for the resolver's match cascade."""
    matches = {'uuid': {}, 'external_id': {}, 'email': {}, 'login': {}}

    if uuids:
        for user in User.query.filter(User.uuid.in_(list(uuids))).all():
            matches['uuid'][str(user.uuid)] = user.id
    if external_ids:
        for user in User.query.filter(User.external_id.in_(list(external_ids))).all():
            matches['external_id'][user.external_id] = user.id
    if emails:
        for user in User.query.filter(User.email.in_(list(emails))).all():
            matches['email'][user.email] = user.id
    if logins:
        for user in User.query.filter(User.user.in_(list(logins))).all():
            matches['login'][user.user] = user.id

    return matches


def existing_user_ids(user_ids):
    """Filter an operator-supplied set of target user ids down to the real ones.

    A mapping decision arrives from a request body, so the id in it is only a
    claim until this says otherwise.
    """
    if not user_ids:
        return set()

    rows = User.query.with_entities(User.id).filter(User.id.in_(list(user_ids))).all()
    return {row.id for row in rows}


def find_user_id_by_external_id(external_id):
    if not external_id:
        return None

    user = User.query.filter(User.external_id == external_id).first()
    return user.id if user is not None else None


def email_exists(email):
    return db.session.query(
        User.query.filter(User.email == email).exists()
    ).scalar()


def find_lookup_ids_by_name(lookup_key, names):
    spec = LOOKUPS[lookup_key]
    if not names:
        return {}

    name_column = getattr(spec.model, spec.name_column)
    rows = spec.model.query.filter(name_column.in_(list(names))).all()
    return {getattr(row, spec.name_column): getattr(row, spec.pk) for row in rows}


def lookup_row_exists(lookup_key, identifier):
    """Confirm an operator-chosen reference-data row is real before we point at it."""
    spec = LOOKUPS[lookup_key]
    pk_column = getattr(spec.model, spec.pk)
    return db.session.query(
        spec.model.query.filter(pk_column == identifier).exists()
    ).scalar()


def create_lookup_row(lookup_key, entry):
    """Create a missing reference-data row from its bundle entry.

    Only the columns named in the spec are copied, so a bundle cannot set a
    column the format was never meant to carry.

    The name goes in through the constructor rather than being assigned to a
    bare `spec.model()`. Some reference-data models declare their own `__init__`
    — `Tags` requires the title and stamps the creation date the rest of IRIS
    reads back — while the others get SQLAlchemy's declarative constructor,
    which accepts any column by name. Going through it either way means an
    imported row is built exactly the way this instance builds one by hand.
    """
    spec = LOOKUPS[lookup_key]
    row = spec.model(**{spec.name_column: entry.get('name')})
    for column in spec.extra_columns:
        if column in entry:
            setattr(row, column, entry.get(column))

    db.session.add(row)
    db.session.flush()
    return getattr(row, spec.pk)


def login_exists(login):
    return db.session.query(
        User.query.filter(User.user == login).exists()
    ).scalar()


def create_placeholder_user(login, name, email, external_id, password_hash):
    """A disabled stand-in for a user who does not exist on this instance.

    `active=False` and an unusable password mean the row can hold history
    without ever being able to authenticate. `external_id` carries the source
    identity so a later import of another case from the same instance re-matches
    this same placeholder instead of creating a second one.
    """
    user = User(user=login, name=name, email=email, password=password_hash,
                active=False, external_id=external_id)
    db.session.add(user)
    db.session.flush()
    return user.id


def create_case_row(payload, ref_map, importer_user_id, new_case_uuid):
    """Insert the `cases` row for an import.

    `Cases.__init__` cannot be trusted to build this: it stores several columns
    as one-element tuples and it reads the owner off the request context. We use
    it only to get a properly instrumented instance, then reassign every column
    from the bundle.
    """
    importer = User.query.filter(User.id == importer_user_id).first()
    if importer is None:
        raise ObjectNotFoundError()

    case = Cases(name='imported', user=importer)

    for column in CASE_SPEC.fields:
        setattr(case, column, payload.get(column))

    for column in CASE_SPEC.user_refs:
        setattr(case, column, ref_map.get(payload.get(column)))

    for column in CASE_SPEC.lookup_refs:
        setattr(case, column, ref_map.get(payload.get(column)))

    # Ownership is a target-instance decision, never the bundle's.
    case.user_id = importer_user_id
    case.owner_id = importer_user_id
    case.case_uuid = new_case_uuid

    if case.status_id is None:
        case.status_id = 0

    db.session.add(case)
    db.session.flush()
    return case


def commit_transfer():
    db.session.commit()


def rollback_transfer():
    db.session.rollback()


def write_case_bundle(new_case_identifier, entities, ref_map):
    """Insert every entity row, resolving refs as we go.

    `ref_map` starts populated with the user and lookup decisions and is
    extended with each entity's freshly allocated ids.

    Returns `{entity key: [(row, payload), ...]}` so the caller can keep working
    with rows it just created — the datastore importer needs to pair each new
    file row with the bundle payload naming its blob, and re-querying would give
    it rows with no way back to the archive.
    """
    written = {}

    for spec in ENTITIES:
        rows = entities.get(spec.key) or []
        deferred = []

        for payload in rows:
            row = spec.model()

            for column in spec.fields:
                setattr(row, column, payload.get(column))

            for column in spec.user_refs:
                setattr(row, column, ref_map.get(payload.get(column)))

            for column in spec.entity_refs:
                setattr(row, column, ref_map.get(payload.get(column)))

            for column in spec.lookup_refs:
                setattr(row, column, ref_map.get(payload.get(column)))

            # Self-references are left null on the first pass: the row they
            # point at may not exist yet, and ordering within an entity is not
            # guaranteed. The second pass below fills them in.
            for column in spec.self_refs:
                setattr(row, column, None)

            if spec.case_column is not None:
                setattr(row, spec.case_column, new_case_identifier)

            db.session.add(row)
            deferred.append((row, payload))

        if spec.key == 'dsfile':
            _prepare_datastore_files(new_case_identifier, deferred)

        db.session.flush()

        for row, payload in deferred:
            if spec.pk is not None:
                ref_map[payload['_ref']] = getattr(row, spec.pk)

        if spec.self_refs:
            for row, payload in deferred:
                for column in spec.self_refs:
                    setattr(row, column, ref_map.get(payload.get(column)))
            db.session.flush()

        if spec.key == 'dspath':
            _restore_datastore_root(new_case_identifier, deferred)

        written[spec.key] = deferred

    return written


def _prepare_datastore_files(new_case_identifier, rows):
    """Give every imported file a fresh uuid and a place of its own on this instance.

    `file_local_name` is NOT NULL but never travels in the bundle — it is an
    absolute path on the source filesystem, so the target has to compute its
    own. It has to happen here, before the flush, and for every row rather than
    only the ones whose bytes are in the archive: a metadata-only import still
    has to satisfy the constraint, and the row it produces is exactly the
    "virtual entry" state the datastore already knows how to report for a file
    that is no longer on disk.

    The uuid is regenerated first because the path is named after it, so two
    imports of the same bundle cannot land on the same file.
    """
    from app.datamgmt.datastore.datastore_db import datastore_get_standard_path

    for row, _ in rows:
        row.file_uuid = uuid.uuid4()
        row.file_local_name = datastore_get_standard_path(row, new_case_identifier).as_posix()


def _restore_datastore_root(new_case_identifier, rows):
    """Put the datastore root back the way the rest of IRIS expects it.

    Reading normalised the root's `0` parent to NULL; undo that here so the
    imported tree is indistinguishable from one `init_ds_tree` built. The root
    folder is also named after its case, so it is retitled for the new id.
    """
    for row, _ in rows:
        if not row.path_is_root:
            continue
        row.path_parent_id = _ROOT_PARENT_SENTINEL
        row.path_name = f'Case {new_case_identifier}'


def datastore_files_for_case(case_identifier):
    from app.models.models import DataStoreFile
    return DataStoreFile.query.filter(
        DataStoreFile.file_case_id == case_identifier
    ).all()
