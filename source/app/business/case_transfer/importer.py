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

"""Reading a transfer bundle back into a case.

Two steps on purpose. `inspect_upload` stages the archive and reports what it
would do — which users it recognised, which reference data it would create,
what the case contains — without touching anything. `apply_import` then does the
work using the decisions the operator made in between.

Splitting it that way is what makes the missing-user problem tractable: the
operator sees the real identities and the reference counts before committing to
a mapping, rather than discovering after the fact that half the case is
attributed to whoever ran the import.

Everything in the archive is treated as hostile. Paths are never taken from it,
ids are never taken from it, and blobs are checked against the manifest digest
before the transaction is allowed to commit.
"""

import datetime
import re
import uuid as uuid_module
from pathlib import Path

from app import app
from app.business.case_transfer import crypto
from app.business.case_transfer.manifest import CASE_ENTRY
from app.business.case_transfer.manifest import LOOKUPS_ENTRY
from app.business.case_transfer.manifest import MANIFEST_ENTRY
from app.business.case_transfer.manifest import PRINCIPALS_ENTRY
from app.business.case_transfer.manifest import BundleFormatError
from app.business.case_transfer.manifest import BundleReader
from app.business.case_transfer.manifest import BundleTooLargeError
from app.business.case_transfer.manifest import validate_manifest
from app.business.case_transfer.resolvers import inspect_lookups
from app.business.case_transfer.resolvers import inspect_principals
from app.business.case_transfer.resolvers import resolve_lookups
from app.business.case_transfer.resolvers import resolve_principals
from app.business.case_transfer.staging import resolve as resolve_workspace
from app.business.case_transfer.staging import sweep_expired
from app.business.case_transfer.staging import transfer_workspace
from app.datamgmt.case_transfer.transfer_db import commit_transfer
from app.datamgmt.case_transfer.transfer_db import create_case_row
from app.datamgmt.case_transfer.transfer_db import rollback_transfer
from app.datamgmt.case_transfer.transfer_db import write_case_bundle
from app.datamgmt.case_transfer.transfer_spec import ENTITIES_BY_KEY
from app.datamgmt.manage.manage_groups_db import add_case_access_to_group
from app.datamgmt.manage.manage_groups_db import get_group
from app.datamgmt.manage.manage_groups_db import get_group_with_members
from app.datamgmt.manage.manage_users_db import get_user
from app.iris_engine.access_control.utils import ac_recompute_effective_ac_from_users_list
from app.iris_engine.access_control.utils import ac_set_new_case_access
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.logger import logger
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_ARCHIVE_NAME = 'bundle.iris'
_UPLOAD_NAME = 'upload.bin'
_UPLOAD_CHUNK = 1024 * 1024

# Case names are stored with their own id as a prefix (`#42 - Ransomware`). The
# imported case gets a new id, so the stale prefix is stripped and re-applied.
_CASE_NAME_PREFIX_RE = re.compile(r'^#\d+\s+-\s+')
_CASE_NAME_MAX_LENGTH = 256


def _max_archive_bytes():
    return int(app.config['CASE_TRANSFER_MAX_ARCHIVE_BYTES'])


def _stage_upload(stream, workspace):
    """Stream the upload to disk, refusing to grow past the configured cap.

    Checked while writing rather than from a Content-Length header, which the
    client controls and can simply lie about.
    """
    limit = _max_archive_bytes()
    target = workspace / _UPLOAD_NAME
    written = 0

    with open(target, 'wb') as handle:
        while True:
            chunk = stream.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise BundleTooLargeError('Uploaded archive exceeds the configured size limit')
            handle.write(chunk)

    if written == 0:
        raise BundleFormatError('Uploaded archive is empty')

    return target


def _prepare_archive(workspace, upload_path, passphrase):
    """Decrypt if needed, and leave a plain zip at the canonical archive path.

    Detection is by content, not by filename: an encrypted bundle renamed to
    `.iris` still has to be decrypted, and a plaintext one named `.enc` still
    has to open. Once this returns, nothing downstream knows or cares whether
    encryption was involved.
    """
    archive_path = workspace / _ARCHIVE_NAME

    if not crypto.is_encrypted(upload_path):
        upload_path.rename(archive_path)
        return archive_path

    if not passphrase:
        raise crypto.ArchiveEncryptedError()

    crypto.open_archive(upload_path, archive_path, passphrase)
    _discard_file(upload_path)
    return archive_path


def _read_documents(archive_path):
    with BundleReader(archive_path, _max_archive_bytes()) as bundle:
        manifest = validate_manifest(bundle.read_json(MANIFEST_ENTRY))
        case_document = bundle.read_json(CASE_ENTRY)
        principals = bundle.read_json(PRINCIPALS_ENTRY)
        lookups = bundle.read_json(LOOKUPS_ENTRY)

    if not isinstance(case_document, dict):
        raise BundleFormatError('case.json is not an object')

    case_row = case_document.get('case')
    entities = case_document.get('entities')
    if not isinstance(case_row, dict) or not isinstance(entities, dict):
        raise BundleFormatError('case.json is missing the case or its entities')

    if not isinstance(principals, list):
        raise BundleFormatError('principals.json is not a list')
    if not isinstance(lookups, dict):
        raise BundleFormatError('lookups.json is not an object')

    # Anything the exporter did not produce is dropped rather than rejected, so
    # a bundle from a slightly newer IRIS still imports what this version knows
    # how to store.
    entities = {key: rows for key, rows in entities.items()
                if key in ENTITIES_BY_KEY and isinstance(rows, list)}

    return manifest, case_row, entities, principals, lookups


def inspect_upload(stream, owner_id, passphrase=None):
    """Stage an uploaded archive and report what importing it would do."""
    sweep_expired()
    workspace = transfer_workspace()

    try:
        upload_path = _stage_upload(stream, workspace)
        archive_path = _prepare_archive(workspace, upload_path, passphrase)
        manifest, case_row, entities, principals, lookups = _read_documents(archive_path)

        principal_report = inspect_principals(principals, case_row, entities)
        lookup_report = inspect_lookups(lookups)
    except BusinessProcessingError:
        workspace.discard()
        raise
    except Exception:
        workspace.discard()
        logger.exception('Case transfer: could not inspect the uploaded archive')
        raise BusinessProcessingError('Could not read the uploaded archive')

    workspace.write_metadata({'owner_id': owner_id, 'archive': _ARCHIVE_NAME})

    return {
        'staging_token': workspace.token,
        'manifest': {
            'format_version': manifest.get('format_version'),
            'source_organisation': manifest.get('source_organisation'),
            'source_iris_version': manifest.get('source_iris_version'),
            'source_case_uuid': manifest.get('source_case_uuid'),
            'exported_at': manifest.get('exported_at'),
            'exported_by': manifest.get('exported_by'),
            'include_blobs': manifest.get('include_blobs'),
        },
        'case': {
            'name': case_row.get('name'),
            'soc_id': case_row.get('soc_id'),
            'description': case_row.get('description'),
        },
        'counts': {key: len(rows) for key, rows in entities.items()},
        'datastore_blobs': len(manifest.get('blobs') or {}),
        'principals': principal_report,
        'lookups': lookup_report,
        'warnings': _collect_warnings(manifest, entities),
    }


def _collect_warnings(manifest, entities):
    warnings = []

    blobs = manifest.get('blobs') or {}
    ds_files = entities.get('dsfile') or []
    if ds_files and not blobs:
        warnings.append(f'{len(ds_files)} Datastore file(s) were exported as metadata only — '
                        f'their content is not in this archive')
    else:
        missing = [row for row in ds_files if row.get('_blob') not in blobs]
        if missing:
            warnings.append(f'{len(missing)} Datastore file(s) have no content in this archive '
                            f'and will be imported as metadata only')

    if manifest.get('source_iris_version') and manifest['source_iris_version'] != app.config.get('IRIS_VERSION'):
        warnings.append(f'Exported from IRIS {manifest["source_iris_version"]}, '
                        f'importing into {app.config.get("IRIS_VERSION")}')

    return warnings


def discard_staged(token, owner_id):
    workspace = resolve_workspace(token)
    _assert_owner(workspace, owner_id)
    workspace.discard()


def _assert_owner(workspace, owner_id):
    """A staging token is a capability — only the uploader may use it.

    Reported as "not found" rather than "forbidden" so a token cannot be probed
    for existence by someone who does not hold it.
    """
    metadata = workspace.read_metadata()
    if metadata.get('owner_id') != owner_id:
        raise ObjectNotFoundError()


def _case_name(raw_name, new_case_identifier):
    base = _CASE_NAME_PREFIX_RE.sub('', (raw_name or 'Imported case').strip()) or 'Imported case'
    prefix = f'#{new_case_identifier} - '
    return f'{prefix}{base}'[:_CASE_NAME_MAX_LENGTH]


def _record_provenance(case, manifest, principal_report):
    """Write who and what this case came from into its own history.

    Once the import commits, the bundle is gone. If we do not record the source
    case uuid and the identities that were collapsed onto the importer here,
    there is nowhere left to look it up.
    """
    history = case.modification_history if isinstance(case.modification_history, dict) else {}
    timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()

    collapsed = [entry for entry in principal_report if entry['action'] == 'importer']
    created = [entry for entry in principal_report if entry.get('created')]

    history[str(timestamp)] = {
        'action': 'imported from another IRIS instance',
        'source_case_uuid': manifest.get('source_case_uuid'),
        'source_organisation': manifest.get('source_organisation'),
        'source_iris_version': manifest.get('source_iris_version'),
        'exported_at': manifest.get('exported_at'),
        'exported_by': manifest.get('exported_by'),
        'attributed_to_importer': [entry['source_login'] for entry in collapsed],
        'placeholder_users_created': [entry['source_login'] for entry in created],
    }
    case.modification_history = history


def _restore_blobs(bundle, manifest, datastore_rows):
    """Fill in the datastore files whose bytes the archive actually carries.

    `datastore_rows` is the `(row, payload)` list `write_case_bundle` produced,
    so each freshly inserted file row is already paired with the bundle entry
    naming its blob, and already carries the uuid and the target-side path the
    insert computed for it. The archive has no say in where anything lands.

    Returns the paths written, so the caller can undo them if the transaction
    does not make it.
    """
    blob_index = manifest.get('blobs') or {}
    written_paths = []

    for datastore_file, payload in datastore_rows:
        blob_key = payload.get('_blob')
        index_entry = blob_index.get(blob_key)
        if index_entry is None or not bundle.has_blob(blob_key):
            # Metadata-only export, or a file whose bytes were unreadable at
            # export time. The row still belongs in the case; it just has
            # nothing behind it.
            continue

        target_path = Path(datastore_file.file_local_name)
        bundle.extract_blob(blob_key, target_path, index_entry.get('sha256'))
        written_paths.append(target_path)

    return written_paths


def apply_import(token, owner_id, principal_decisions=None, lookup_decisions=None,
                 customer_identifier=None, group_grants=None, may_create_placeholders=False):
    """Turn a staged bundle into a real case. Returns the new `Cases` row."""
    workspace = resolve_workspace(token)
    _assert_owner(workspace, owner_id)

    archive_path = workspace / _ARCHIVE_NAME
    manifest, case_row, entities, principals, lookups = _read_documents(archive_path)

    written_paths = []
    try:
        lookup_ref_map, created_lookups = resolve_lookups(
            lookups, lookup_decisions, customer_identifier)

        principal_ref_map, principal_report = resolve_principals(
            principals, principal_decisions, owner_id, may_create_placeholders)

        ref_map = {}
        ref_map.update(lookup_ref_map)
        ref_map.update(principal_ref_map)

        if case_row.get('client_id') and ref_map.get(case_row['client_id']) is None:
            raise BusinessProcessingError('Could not resolve the customer for this case')

        case = create_case_row(case_row, ref_map, owner_id, uuid_module.uuid4())
        case.name = _case_name(case_row.get('name'), case.case_id)
        _record_provenance(case, manifest, principal_report)

        written_rows = write_case_bundle(case.case_id, entities, ref_map)

        with BundleReader(archive_path, _max_archive_bytes()) as bundle:
            written_paths = _restore_blobs(bundle, manifest,
                                           written_rows.get('dsfile') or [])

        commit_transfer()
    except Exception:
        rollback_transfer()
        # Blobs live outside the transaction, so nothing else will clean them up.
        for path in written_paths:
            _discard_file(path)
        raise

    workspace.discard()

    _grant_access(case, owner_id, group_grants)

    # Modules only learn about a case through this hook, exactly as they do for
    # a case created by hand.
    case = call_modules_hook('on_postload_case_create', case)
    track_activity(f'case "{case.name}" imported from an archive',
                   caseid=case.case_id, ctx_less=False)

    return case, {
        'created_lookups': created_lookups,
        'principals': principal_report,
        'blobs_restored': len(written_paths),
    }


def _grant_access(case, owner_id, group_grants):
    """The importer gets full access; anyone else only if asked for explicitly.

    The source instance's ACL never travels — it names groups and users that
    mean nothing here. Access to an imported case is decided on the target,
    from the target's own directory.
    """
    importer = get_user(owner_id)
    ac_set_new_case_access(importer, case.case_id, case.client_id)

    for grant in group_grants or []:
        group = get_group(grant.get('group_id'))
        if group is None:
            raise BusinessProcessingError(f'Unknown group {grant.get("group_id")}')

        _, logs = add_case_access_to_group(group, [case.case_id], grant.get('access_level'))
        refreshed = get_group_with_members(group.group_id)
        if refreshed is not None:
            ac_recompute_effective_ac_from_users_list(refreshed.group_members)
        logger.info(f'Case transfer: granted group {group.group_id} access to '
                    f'case {case.case_id} ({logs})')


def _discard_file(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning(f'Case transfer: could not remove {path}')
