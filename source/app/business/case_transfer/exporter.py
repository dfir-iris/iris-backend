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

"""Serialise one case into a transfer bundle."""

import datetime
import uuid as uuid_module
from pathlib import Path

from app import app
from app.business.case_transfer import crypto
from app.business.case_transfer.manifest import CASE_ENTRY
from app.business.case_transfer.manifest import LOOKUPS_ENTRY
from app.business.case_transfer.manifest import MANIFEST_ENTRY
from app.business.case_transfer.manifest import PRINCIPALS_ENTRY
from app.business.case_transfer.manifest import BundleWriter
from app.business.case_transfer.manifest import build_manifest
from app.business.case_transfer.staging import transfer_workspace
from app.datamgmt.case_transfer.transfer_db import read_case_bundle
from app.datamgmt.case_transfer.transfer_db import read_datastore_blob_rows
from app.datamgmt.case_transfer.transfer_db import read_lookups
from app.datamgmt.case_transfer.transfer_db import read_principals
from app.logger import logger
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _flush_collab_docs(case_identifier, note_ids):
    """Push any live editor state back into the columns we are about to read.

    Note bodies and the case summary live in a Yjs CRDT (`collab_doc.y_state`);
    `notes.note_content` is only authoritative once a flush has rendered the
    CRDT back into it. Exporting without this step silently ships whatever the
    text was the last time someone closed the editor, which is exactly the kind
    of data loss nobody notices until the case matters.
    """
    from app.business.collab import flush_to_source

    doc_names = [f'case-summary:{case_identifier}']
    doc_names += [f'note:{note_id}' for note_id in note_ids]

    for doc_name in doc_names:
        try:
            flush_to_source(doc_name)
        except Exception:
            # A doc that cannot be rendered must not sink the export — the
            # column still holds the last good content.
            logger.warning(f'Case transfer: could not flush collab doc {doc_name}')


def _blob_source_path(datastore_file):
    """Resolve a datastore blob on disk, refusing anything outside the store.

    Same containment check as `datastore_get_local_file_path`: a `file_local_name`
    that points outside `DATASTORE_PATH` means the row was seeded maliciously
    (GHSA-qhqj-8qw6-wp8v), and we will not read it just because we are exporting.
    """
    if not datastore_file.file_local_name:
        return None

    datastore_root = Path(app.config['DATASTORE_PATH']).resolve()
    try:
        file_path = Path(datastore_file.file_local_name).resolve()
    except OSError:
        return None

    if datastore_root not in file_path.parents and datastore_root != file_path:
        logger.warning(f'Case transfer: datastore file {datastore_file.file_id} '
                       f'resolves outside the datastore — skipped')
        return None

    if not file_path.is_file():
        logger.warning(f'Case transfer: datastore blob missing on disk for '
                       f'file {datastore_file.file_id} — exported as metadata only')
        return None

    return file_path


def build_case_archive(case_identifier, exported_by, include_blobs=True, passphrase=None):
    """Build a bundle for `case_identifier`.

    Returns `(archive_path, download_name, workspace)`. The caller owns the
    workspace and must clean it up once the response has been sent.
    """
    case_row, entities, _ = read_case_bundle(case_identifier)
    if case_row is None:
        raise ObjectNotFoundError()

    note_ids = [int(note['_ref'].split(':', 1)[1]) for note in entities.get('note', [])]
    _flush_collab_docs(case_identifier, note_ids)

    # Re-read after flushing so the freshly rendered markdown is what travels.
    case_row, entities, collector = read_case_bundle(case_identifier)

    principals = read_principals(collector.principal_ids)
    lookups = read_lookups(collector.lookup_ids)

    workspace = transfer_workspace()
    archive_path = workspace / 'bundle.iris'

    try:
        with BundleWriter(archive_path) as bundle:
            bundle.add_json(CASE_ENTRY, {'case': case_row, 'entities': entities})
            bundle.add_json(PRINCIPALS_ENTRY, principals)
            bundle.add_json(LOOKUPS_ENTRY, lookups)

            if include_blobs:
                for datastore_file in read_datastore_blob_rows(case_identifier):
                    source_path = _blob_source_path(datastore_file)
                    if source_path is None:
                        continue
                    bundle.add_blob(str(datastore_file.file_uuid), source_path)

            counts = {key: len(rows) for key, rows in entities.items()}
            bundle.add_json(MANIFEST_ENTRY, build_manifest(
                case_uuid=case_row['case_uuid'],
                source_organisation=app.config.get('ORGANISATION_NAME') or '',
                iris_version=app.config.get('IRIS_VERSION'),
                exported_by=exported_by,
                exported_at=datetime.datetime.utcnow().isoformat(),
                counts=counts,
                blob_index=bundle.blob_index,
                include_blobs=include_blobs,
            ))
    except BusinessProcessingError:
        workspace.discard()
        raise
    except Exception:
        workspace.discard()
        logger.exception(f'Case transfer: export of case {case_identifier} failed')
        raise BusinessProcessingError('Could not build the case archive')

    download_name = f'case-{case_row["case_uuid"]}.iris'

    if passphrase:
        sealed_path = workspace / 'bundle.iris.enc'
        try:
            crypto.seal_archive(archive_path, sealed_path, passphrase)
        except Exception:
            workspace.discard()
            # Never echo the passphrase, or anything derived from it, into a log.
            logger.exception(f'Case transfer: could not encrypt export of case {case_identifier}')
            raise BusinessProcessingError('Could not encrypt the case archive')

        _unlink(archive_path)
        return sealed_path, f'{download_name}.enc', workspace

    return archive_path, download_name, workspace


def _unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def new_export_token():
    return uuid_module.uuid4().hex
