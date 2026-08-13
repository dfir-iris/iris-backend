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

"""The case-transfer bundle container: a zip with a fixed, closed set of entries.

Knows nothing about IRIS models — it moves JSON documents and opaque blobs, and
enforces the rules that make reading an attacker-supplied archive safe. Keeping
it model-free is what lets it be tested without a database.

Layout::

    manifest.json          format version, provenance, row counts, blob index
    case.json              every exported row, PKs rewritten to bundle refs
    principals.json        every referenced user
    lookups.json           every referenced reference-data row, by name
    datastore/<uuid>       raw blobs, flat

The entry namespace is an allowlist, not a filter. Nothing is ever derived from
a name found inside the archive: blob names must match a UUID exactly, and the
extraction path is always built from data the *target* instance produced. That
closes zip-slip structurally rather than by sanitising strings.
"""

import hashlib
import json
import re
import zipfile
from pathlib import Path

from app.models.errors import BusinessProcessingError

FORMAT_VERSION = 1

MANIFEST_ENTRY = 'manifest.json'
CASE_ENTRY = 'case.json'
PRINCIPALS_ENTRY = 'principals.json'
LOOKUPS_ENTRY = 'lookups.json'
DATASTORE_PREFIX = 'datastore/'

_JSON_ENTRIES = (MANIFEST_ENTRY, CASE_ENTRY, PRINCIPALS_ENTRY, LOOKUPS_ENTRY)

# Blob entries are named by UUID and nothing else. Anchored, so `../` or a
# nested path can never match.
_BLOB_NAME_RE = re.compile(r'^datastore/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
                           r'-[0-9a-f]{4}-[0-9a-f]{12}$')

# A case with more than this many datastore files is well beyond anything
# legitimate and is more likely an attempt to exhaust inodes or file handles.
_MAX_ENTRIES = 100_000

_READ_CHUNK = 1024 * 1024


class BundleFormatError(BusinessProcessingError):
    """The archive is not a well-formed case-transfer bundle."""


class BundleTooLargeError(BusinessProcessingError):
    """The archive exceeds the configured size cap, compressed or expanded."""


class BundleWriter:
    """Streams a bundle to disk. Use as a context manager.

    JSON is deflated; blobs are stored. Datastore content is very often already
    compressed (zips of samples, images, packed binaries) so deflating it again
    costs CPU for no gain on a potentially multi-GB export.
    """

    def __init__(self, path: Path):
        self._path = Path(path)
        self._zip = None
        self._blobs = {}

    def __enter__(self):
        self._zip = zipfile.ZipFile(self._path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._zip is not None:
            self._zip.close()
        # A failed export must not leave a half-written bundle where a later
        # request could hand it to an operator as if it were complete.
        if exc_type is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
        return False

    def add_json(self, entry_name: str, payload):
        if entry_name not in _JSON_ENTRIES:
            raise BundleFormatError(f'Refusing to write unknown bundle entry {entry_name}')
        self._zip.writestr(entry_name, json.dumps(payload, default=str, ensure_ascii=False),
                           zipfile.ZIP_DEFLATED)

    def add_blob(self, file_uuid: str, source_path: Path) -> dict:
        """Stream a datastore blob in, returning its `{size, sha256}` index entry."""
        entry_name = f'{DATASTORE_PREFIX}{file_uuid}'
        if not _BLOB_NAME_RE.match(entry_name):
            raise BundleFormatError(f'Invalid datastore blob name {file_uuid}')

        digest = hashlib.sha256()
        size = 0
        info = zipfile.ZipInfo(entry_name)
        info.compress_type = zipfile.ZIP_STORED

        with open(source_path, 'rb') as source, self._zip.open(info, 'w') as target:
            while True:
                chunk = source.read(_READ_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)

        index = {'size': size, 'sha256': digest.hexdigest()}
        self._blobs[str(file_uuid)] = index
        return index

    @property
    def blob_index(self) -> dict:
        return dict(self._blobs)


class BundleReader:
    """Reads a bundle, refusing anything that does not fit the closed layout.

    `max_bytes` caps the *cumulative decompressed* size across all entries, which
    is the number a zip bomb inflates — the compressed size on disk was already
    checked at upload time.
    """

    def __init__(self, path: Path, max_bytes: int):
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._zip = None
        self._names = set()

    def __enter__(self):
        try:
            self._zip = zipfile.ZipFile(self._path, 'r')
        except (zipfile.BadZipFile, OSError):
            raise BundleFormatError('Archive is not a readable zip bundle')

        self._validate_namespace()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._zip is not None:
            self._zip.close()
        return False

    def _validate_namespace(self):
        infos = self._zip.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise BundleTooLargeError(f'Bundle holds more than {_MAX_ENTRIES} entries')

        declared_total = 0
        for info in infos:
            name = info.filename
            if name not in _JSON_ENTRIES and not _BLOB_NAME_RE.match(name):
                # Covers `../`, absolute paths, symlink-ish names, nested
                # directories and stray files in one rule.
                raise BundleFormatError(f'Unexpected entry in bundle: {name}')
            declared_total += info.file_size
            self._names.add(name)

        if declared_total > self._max_bytes:
            raise BundleTooLargeError('Bundle expands beyond the configured size limit')

        for required in (MANIFEST_ENTRY, CASE_ENTRY, PRINCIPALS_ENTRY, LOOKUPS_ENTRY):
            if required not in self._names:
                raise BundleFormatError(f'Bundle is missing {required}')

    def read_json(self, entry_name: str):
        if entry_name not in _JSON_ENTRIES:
            raise BundleFormatError(f'Refusing to read unknown bundle entry {entry_name}')
        try:
            with self._zip.open(entry_name, 'r') as source:
                return json.loads(source.read().decode('utf-8'))
        except (KeyError, ValueError, UnicodeDecodeError):
            raise BundleFormatError(f'{entry_name} is not valid JSON')

    def has_blob(self, file_uuid: str) -> bool:
        return f'{DATASTORE_PREFIX}{file_uuid}' in self._names

    def extract_blob(self, file_uuid: str, target_path: Path, expected_sha256: str) -> int:
        """Stream one blob out to `target_path`, verifying sha256 as it goes.

        The target path is supplied by the caller and built from local data — the
        archive never influences where bytes land. On digest mismatch the partial
        file is removed and the caller is expected to abort the whole import; a
        blob that does not match its manifest means the bundle is corrupt or has
        been tampered with, and neither is safe to half-apply.
        """
        entry_name = f'{DATASTORE_PREFIX}{file_uuid}'
        if entry_name not in self._names:
            raise BundleFormatError(f'Bundle is missing datastore blob {file_uuid}')

        digest = hashlib.sha256()
        written = 0
        target_path = Path(target_path)

        try:
            with self._zip.open(entry_name, 'r') as source, open(target_path, 'wb') as target:
                while True:
                    chunk = source.read(_READ_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self._max_bytes:
                        raise BundleTooLargeError('Datastore blob exceeds the size limit')
                    digest.update(chunk)
                    target.write(chunk)
        except (zipfile.BadZipFile, OSError):
            _discard(target_path)
            raise BundleFormatError(f'Could not read datastore blob {file_uuid}')
        except BundleTooLargeError:
            _discard(target_path)
            raise

        if digest.hexdigest() != expected_sha256:
            _discard(target_path)
            raise BundleFormatError(f'Checksum mismatch for datastore blob {file_uuid}')

        return written


def build_manifest(case_uuid, source_organisation, iris_version, exported_by,
                   exported_at, counts, blob_index, include_blobs) -> dict:
    """Assemble manifest.json.

    `source_organisation` is provenance for a human reading the file, nothing
    more. Re-matching an imported placeholder user across bundles keys off the
    source user's own UUID, which is already globally unique — so there is no
    need for an instance identifier the schema does not have.
    """
    return {
        'format_version': FORMAT_VERSION,
        'source_organisation': source_organisation,
        'source_iris_version': iris_version,
        'source_case_uuid': str(case_uuid),
        'exported_at': exported_at,
        'exported_by': exported_by,
        'include_blobs': include_blobs,
        'counts': counts,
        'blobs': blob_index,
    }


def validate_manifest(manifest: dict):
    if not isinstance(manifest, dict):
        raise BundleFormatError('manifest.json is not an object')

    version = manifest.get('format_version')
    if version != FORMAT_VERSION:
        # No forward compatibility shim yet. Saying which version we found makes
        # the operator-facing error actionable rather than mysterious.
        raise BundleFormatError(
            f'Unsupported bundle format version {version} — this instance reads version {FORMAT_VERSION}')

    if not isinstance(manifest.get('blobs'), dict):
        raise BundleFormatError('manifest.json is missing a blob index')

    return manifest


def _discard(path: Path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
