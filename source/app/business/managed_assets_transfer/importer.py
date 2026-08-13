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

"""Two-phase import of registry rows from CSV or JSON.

Phase one (`inspect`) stages the upload and reports, row by row, what
applying it would do. Phase two (`apply`) spends the staging token and
performs exactly that. Nothing is written during inspect — an operator
gets to see "this will overwrite 340 assets" before it happens, which is
the whole reason the import is not a single request.

The target customer is fixed by the inspect request and pinned into the
workspace metadata. It is re-authorised at apply, because group
membership can be revoked in between and a capability minted under the
old membership must not outlive it.
"""

import csv
import json

from app import app
from app.business.managed_assets import managed_assets_normalize_name
from app.business.managed_assets_transfer import staging
from app.datamgmt.case.assets_type import get_assets_types
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_add
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_add_audit
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_commit
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_diff
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_flush
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_get_many_by_identity
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_is_integrity_error
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_rollback
from app.datamgmt.manage.manage_tags_db import register_db_tags_from_string
from app.logger import logger
from app.models.errors import BusinessProcessingError
from app.models.managed_assets import MANAGED_ASSET_CRITICALITIES
from app.models.managed_assets import MANAGED_ASSET_ENVIRONMENTS
from app.models.managed_assets import ManagedAsset

IMPORT_FORMATS = ('csv', 'json')
CONFLICT_POLICIES = ('skip', 'update')

_UPLOAD_CHUNK = 256 * 1024
_MAX_COLUMNS = 64
_MAX_JSON_DEPTH = 4
_MAX_NAME_LENGTH = 512
_MAX_FIELD_LENGTH = 4096
# Reported per row; beyond this the report itself becomes the payload.
_MAX_REPORTED_ROWS = 500

# `_validate_row` pulls every field it understands by name, so any other
# column in the file is simply ignored. `client_name` is the one worth
# calling out: it is accepted so an exported file round-trips, but it is
# informational only — the customer comes from the authorised request,
# never from the file. A file that could retarget itself at another
# customer would make the access check at inspect meaningless.
_TEXT_FIELDS = ('description', 'owner', 'location', 'tags', 'ip', 'domain')

_TRUE_VALUES = ('true', 't', 'yes', 'y', '1')
_FALSE_VALUES = ('false', 'f', 'no', 'n', '0')


def _max_bytes():
    return int(app.config['MANAGED_ASSETS_MAX_IMPORT_BYTES'])


def _max_rows():
    return int(app.config['MANAGED_ASSETS_MAX_IMPORT_ROWS'])


def _stage_upload(stream, workspace):
    """Stream the upload to disk, refusing to grow past the configured cap.

    Counted while writing rather than read from `Content-Length`, which
    the client controls and can simply lie about.
    """
    limit = _max_bytes()
    target = workspace / staging.UPLOAD_NAME
    written = 0

    with open(target, 'wb') as handle:
        while True:
            chunk = stream.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise BusinessProcessingError(
                    f'Uploaded file exceeds the {limit} byte import limit'
                )
            handle.write(chunk)

    if written == 0:
        raise BusinessProcessingError('Uploaded file is empty')

    return target


def _read_text(path):
    try:
        # `utf-8-sig` drops the BOM our own CSV export writes, and any
        # BOM Excel adds when a user re-saves the file.
        return path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        raise BusinessProcessingError('Uploaded file is not valid UTF-8 text')


def _assert_json_depth(payload):
    """Reject deeply nested JSON before handing it to the parser.

    `json.loads` recurses, so a document nested a few hundred thousand
    levels deep is a stack overflow — a one-line request that takes the
    worker down. Checked by scanning the raw text, which costs one linear
    pass and never allocates the structure.
    """
    depth = 0
    in_string = False
    escaped = False

    for character in payload:
        if in_string:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character in '[{':
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise BusinessProcessingError(
                    f'JSON nesting exceeds the maximum depth of {_MAX_JSON_DEPTH}'
                )
        elif character in ']}':
            depth -= 1


def _parse_json(payload):
    _assert_json_depth(payload)
    try:
        document = json.loads(payload)
    except ValueError:
        raise BusinessProcessingError('Uploaded file is not valid JSON')

    rows = document.get('assets') if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise BusinessProcessingError('JSON import must be a list of assets, or {"assets": [...]}')

    limit = _max_rows()
    if len(rows) > limit:
        raise BusinessProcessingError(f'Import exceeds the maximum of {limit} rows')

    parsed = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BusinessProcessingError(f'Row {index + 1} is not an object')
        parsed.append(row)
    return parsed


def _parse_csv(payload):
    """Parse the CSV incrementally, refusing an over-wide or over-long file.

    Row-capped while iterating rather than after materialising, so a
    file one row over the limit is rejected at that row instead of after
    the whole thing is in memory.
    """
    # `field_size_limit` is process-global state in the stdlib, so it is
    # lowered for this parse only and restored in `finally` — leaving it
    # changed would alter how every other CSV reader in the process
    # behaves, including the legacy case-asset upload.
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(_MAX_FIELD_LENGTH * _MAX_COLUMNS)
    try:
        reader = csv.reader(payload.splitlines())
        try:
            header = next(reader)
        except StopIteration:
            raise BusinessProcessingError('Uploaded file is empty')

        if len(header) > _MAX_COLUMNS:
            raise BusinessProcessingError(f'CSV has more than {_MAX_COLUMNS} columns')

        header = [(column or '').strip().lower() for column in header]
        if 'name' not in header:
            raise BusinessProcessingError('CSV is missing the required "name" column')

        limit = _max_rows()
        rows = []
        for values in reader:
            if not any((value or '').strip() for value in values):
                continue
            if len(rows) >= limit:
                raise BusinessProcessingError(f'Import exceeds the maximum of {limit} rows')
            rows.append(dict(zip(header, values)))
        return rows
    finally:
        csv.field_size_limit(previous_limit)


def _clean(value, max_length=_MAX_FIELD_LENGTH):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # NUL cannot be stored in a Postgres text column and would abort the
    # whole transaction at flush time rather than fail one row.
    text = text.replace('\x00', '')
    return text[:max_length] or None


def _parse_bool(value, errors):
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return True
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    errors.append(f'is_active "{value}" is not a boolean')
    return True


def _parse_custom_attributes(value, errors):
    if value is None or value == '':
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        errors.append('custom_attributes is not valid JSON')
        return None
    if not isinstance(parsed, dict):
        errors.append('custom_attributes must be a JSON object')
        return None
    return parsed


def _validate_row(row, index, asset_types, seen):
    """Turn one raw row into `(payload, report_entry)`.

    `payload` is None whenever the row cannot be applied. Every problem
    is reported rather than raised: an operator fixing a 20 000-row file
    needs the whole list of bad rows, not the first one.
    """
    errors = []

    raw_name = _clean(row.get('name'), _MAX_NAME_LENGTH)
    normalized_name = managed_assets_normalize_name(raw_name)
    if not normalized_name:
        errors.append('name is required')

    type_name = _clean(row.get('asset_type'))
    asset_type_id = asset_types.get((type_name or '').lower())
    if type_name is None:
        errors.append('asset_type is required')
    elif asset_type_id is None:
        errors.append(f'unknown asset type "{type_name}"')

    criticality = (_clean(row.get('criticality')) or 'unknown').lower()
    if criticality not in MANAGED_ASSET_CRITICALITIES:
        errors.append(f'unknown criticality "{criticality}"')

    environment = _clean(row.get('environment'))
    if environment is not None:
        environment = environment.lower()
        if environment not in MANAGED_ASSET_ENVIRONMENTS:
            errors.append(f'unknown environment "{environment}"')

    is_active = _parse_bool(row.get('is_active'), errors)
    custom_attributes = _parse_custom_attributes(row.get('custom_attributes'), errors)

    entry = {
        'row': index,
        'name': raw_name,
        'asset_type': type_name,
        'action': 'error',
        'errors': errors,
    }

    if errors:
        return None, entry

    identity = (normalized_name, asset_type_id)
    if identity in seen:
        # A file that names the same asset twice is ambiguous, not
        # mergeable — the two rows may disagree on every other field.
        entry['errors'] = [f'duplicate of row {seen[identity]} in the same file']
        return None, entry
    seen[identity] = index

    payload = {
        'name': raw_name,
        'normalized_name': normalized_name,
        'asset_type_id': asset_type_id,
        'criticality': criticality,
        'environment': environment,
        'is_active': is_active,
        'custom_attributes': custom_attributes,
    }
    for field in _TEXT_FIELDS:
        payload[field] = _clean(row.get(field))

    return payload, entry


def _asset_type_index():
    return {name.lower(): identifier for identifier, name in get_assets_types() if name}


def _plan(rows, client_id):
    """Validate every row and decide create / update / error for each."""
    asset_types = _asset_type_index()
    seen = {}

    payloads = []
    entries = []
    for index, row in enumerate(rows, start=1):
        payload, entry = _validate_row(row, index, asset_types, seen)
        if payload is not None:
            payloads.append(payload)
        entries.append(entry)

    existing = managed_assets_db_get_many_by_identity(
        client_id,
        [(payload['normalized_name'], payload['asset_type_id']) for payload in payloads],
    )

    # `entries` holds one dict per input row, in order; the clean ones
    # line up one-for-one with `payloads`, and are the same objects, so
    # setting `action` here updates the report in place.
    for payload, entry in zip(payloads, [entry for entry in entries if not entry['errors']]):
        identity = (payload['normalized_name'], payload['asset_type_id'])
        entry['action'] = 'update' if identity in existing else 'create'

    return payloads, entries, existing


def _summarize(entries):
    counts = {'create': 0, 'update': 0, 'error': 0}
    for entry in entries:
        counts[entry['action']] = counts.get(entry['action'], 0) + 1
    return counts


def inspect_upload(stream, owner_id, client_id, import_format):
    """Stage an uploaded file and report what applying it would do."""
    if import_format not in IMPORT_FORMATS:
        raise BusinessProcessingError(f'Unsupported import format "{import_format}"')

    staging.sweep_expired()
    workspace = staging.import_workspace()

    try:
        upload_path = _stage_upload(stream, workspace)
        payload = _read_text(upload_path)
        rows = _parse_json(payload) if import_format == 'json' else _parse_csv(payload)
        _, entries, _existing = _plan(rows, client_id)
    except BusinessProcessingError:
        workspace.discard()
        raise
    except Exception:
        workspace.discard()
        logger.exception('Managed assets import: could not inspect the uploaded file')
        raise BusinessProcessingError('Could not read the uploaded file')

    workspace.write_metadata({
        'owner_id': owner_id,
        'client_id': client_id,
        'format': import_format,
    })

    return {
        'staging_token': workspace.token,
        'client_id': client_id,
        'format': import_format,
        'total_rows': len(entries),
        'counts': _summarize(entries),
        'rows': entries[:_MAX_REPORTED_ROWS],
        'rows_truncated': len(entries) > _MAX_REPORTED_ROWS,
    }


def staged_metadata(token, owner_id):
    """Metadata for a staged import, for the route's re-authorisation.

    Split out from `apply_staged` so the customer check happens against
    the *pinned* customer before any write is attempted, rather than
    being trusted from the apply request body.
    """
    workspace = staging.resolve(token)
    return staging.assert_owner(workspace, owner_id)


def discard_staged(token, owner_id):
    workspace = staging.resolve(token)
    staging.assert_owner(workspace, owner_id)
    workspace.discard()


def _apply_payload(asset, payload):
    asset.name = payload['name']
    asset.normalized_name = payload['normalized_name']
    asset.asset_type_id = payload['asset_type_id']
    asset.criticality = payload['criticality']
    asset.environment = payload['environment']
    asset.is_active = payload['is_active']
    if payload['custom_attributes'] is not None:
        asset.custom_attributes = payload['custom_attributes']
    for field in _TEXT_FIELDS:
        setattr(asset, field, payload[field])


def apply_staged(token, owner_id, on_conflict, user):
    """Spend a staging token: write the rows it described.

    One transaction for the whole file. A partially applied inventory is
    worse than a rejected one — the operator has no way to tell which
    half landed, and re-running would then double-apply the rest.
    """
    if on_conflict not in CONFLICT_POLICIES:
        raise BusinessProcessingError(f'Unsupported conflict policy "{on_conflict}"')

    workspace = staging.resolve(token)
    metadata = staging.assert_owner(workspace, owner_id)
    client_id = metadata.get('client_id')
    import_format = metadata.get('format')
    if not client_id or import_format not in IMPORT_FORMATS:
        raise BusinessProcessingError('Staged import is no longer usable')

    payload = _read_text(workspace / staging.UPLOAD_NAME)
    rows = _parse_json(payload) if import_format == 'json' else _parse_csv(payload)
    payloads, entries, existing = _plan(rows, client_id)

    created = 0
    updated = 0
    skipped = 0
    unchanged = 0
    # (asset, changes) pairs. The audit rows are written after the flush
    # because a newly created asset has no id until then, and an audit
    # entry that cannot name its asset records nothing useful.
    pending_audits = []
    # Tag strings of the rows we actually wrote, collected here so they
    # can be indexed once the import transaction has landed. Reading them
    # back off the assets afterwards would re-query every expired row.
    pending_tags = []

    try:
        for item in payloads:
            identity = (item['normalized_name'], item['asset_type_id'])
            asset = existing.get(identity)

            if asset is not None:
                if on_conflict == 'skip':
                    skipped += 1
                    continue
                _apply_payload(asset, item)
                # Read before the flush — SQLAlchemy resets each
                # attribute's history once the change is written.
                changes = managed_assets_db_diff(asset)
                if not changes:
                    unchanged += 1
                    continue
                asset.updated_by = getattr(user, 'id', None)
                pending_audits.append((asset, changes))
                pending_tags.append(item['tags'])
                updated += 1
                continue

            asset = ManagedAsset()
            asset.client_id = client_id
            asset.source = 'import'
            _apply_payload(asset, item)
            asset.created_by = getattr(user, 'id', None)
            asset.updated_by = getattr(user, 'id', None)
            managed_assets_db_add(asset)
            pending_audits.append((asset, None))
            pending_tags.append(item['tags'])
            created += 1

        managed_assets_db_flush()
        for asset, changes in pending_audits:
            managed_assets_db_add_audit(asset, 'import', changes, user, source='import')

        managed_assets_db_commit()
    except Exception as exception:
        managed_assets_db_rollback()
        if managed_assets_db_is_integrity_error(exception):
            raise BusinessProcessingError(
                'The import conflicts with an asset created since it was inspected. '
                'Re-inspect the file and try again.'
            )
        raise

    # Only once the import transaction has landed: `Tags.save()` commits,
    # and the whole point of the block above is that the file applies as
    # one transaction or not at all.
    for tags_value in pending_tags:
        register_db_tags_from_string(tags_value)

    workspace.discard()

    failed = [entry for entry in entries if entry['action'] == 'error']

    return {
        'client_id': client_id,
        'created': created,
        'updated': updated,
        'skipped': skipped,
        'unchanged': unchanged,
        'errors': len(failed),
        'rows': failed[:_MAX_REPORTED_ROWS],
    }
