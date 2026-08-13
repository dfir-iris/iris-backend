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

"""Unit tests for the bundle container.

This is the code that reads an attacker-supplied file, so most of what is
tested here is refusal: entry names outside the closed namespace, oversized
archives, blobs whose bytes do not match the digest they were shipped with.
"""

import json
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

from app.business.case_transfer import manifest as bundle_manifest
from app.business.case_transfer.manifest import BundleFormatError
from app.business.case_transfer.manifest import BundleReader
from app.business.case_transfer.manifest import BundleTooLargeError
from app.business.case_transfer.manifest import BundleWriter

_A_BLOB_UUID = '3f2504e0-4f89-11d3-9a0c-0305e82c3301'
_MAX_BYTES = 10 * 1024 * 1024


class TestCaseTransferManifest(TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)
        self._archive = self._root / 'bundle.iris'

    def tearDown(self):
        self._directory.cleanup()

    def _write_minimal_bundle(self, blob_content=None):
        with BundleWriter(self._archive) as bundle:
            bundle.add_json(bundle_manifest.CASE_ENTRY, {'case': {'name': 'a case'}, 'entities': {}})
            bundle.add_json(bundle_manifest.PRINCIPALS_ENTRY, [])
            bundle.add_json(bundle_manifest.LOOKUPS_ENTRY, {})

            if blob_content is not None:
                source = self._root / 'blob.bin'
                source.write_bytes(blob_content)
                bundle.add_blob(_A_BLOB_UUID, source)

            bundle.add_json(bundle_manifest.MANIFEST_ENTRY, bundle_manifest.build_manifest(
                case_uuid='2f0f6e42-0000-0000-0000-000000000000',
                source_organisation='Acme CERT',
                iris_version='v3.0.0',
                exported_by='administrator',
                exported_at='2026-01-01T00:00:00',
                counts={},
                blob_index=bundle.blob_index,
                include_blobs=blob_content is not None,
            ))
        return self._archive

    def _write_raw_zip(self, entries):
        with zipfile.ZipFile(self._archive, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return self._archive

    def _valid_entries(self):
        return {
            bundle_manifest.MANIFEST_ENTRY: json.dumps({
                'format_version': bundle_manifest.FORMAT_VERSION, 'blobs': {}}),
            bundle_manifest.CASE_ENTRY: json.dumps({'case': {}, 'entities': {}}),
            bundle_manifest.PRINCIPALS_ENTRY: json.dumps([]),
            bundle_manifest.LOOKUPS_ENTRY: json.dumps({}),
        }

    # ------------------------------------------------------------------
    # round trip
    # ------------------------------------------------------------------
    def test_round_trip_should_return_the_written_documents(self):
        self._write_minimal_bundle()
        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            case_document = bundle.read_json(bundle_manifest.CASE_ENTRY)
        self.assertEqual('a case', case_document['case']['name'])

    def test_manifest_should_carry_the_blob_index(self):
        self._write_minimal_bundle(blob_content=b'evidence bytes')
        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            manifest = bundle.read_json(bundle_manifest.MANIFEST_ENTRY)
        self.assertIn(_A_BLOB_UUID, manifest['blobs'])

    def test_extract_blob_should_write_the_original_bytes(self):
        content = b'evidence bytes'
        self._write_minimal_bundle(blob_content=content)

        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            manifest = bundle.read_json(bundle_manifest.MANIFEST_ENTRY)
            target = self._root / 'restored.bin'
            bundle.extract_blob(_A_BLOB_UUID, target, manifest['blobs'][_A_BLOB_UUID]['sha256'])

        self.assertEqual(content, target.read_bytes())

    def test_extract_blob_should_raise_on_a_digest_mismatch(self):
        self._write_minimal_bundle(blob_content=b'evidence bytes')
        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            with self.assertRaises(BundleFormatError):
                bundle.extract_blob(_A_BLOB_UUID, self._root / 'restored.bin', 'deadbeef')

    def test_extract_blob_should_leave_nothing_behind_on_a_digest_mismatch(self):
        self._write_minimal_bundle(blob_content=b'evidence bytes')
        target = self._root / 'restored.bin'
        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            with self.assertRaises(BundleFormatError):
                bundle.extract_blob(_A_BLOB_UUID, target, 'deadbeef')
        self.assertFalse(target.exists())

    # ------------------------------------------------------------------
    # refusals
    # ------------------------------------------------------------------
    def test_reader_should_reject_a_zip_slip_entry(self):
        entries = self._valid_entries()
        entries['../../etc/passwd'] = 'root:x:0:0'

        self._write_raw_zip(entries)
        with self.assertRaises(BundleFormatError):
            with BundleReader(self._archive, _MAX_BYTES):
                pass

    def test_reader_should_reject_an_absolute_path_entry(self):
        entries = self._valid_entries()
        entries['/etc/shadow'] = 'nope'

        self._write_raw_zip(entries)
        with self.assertRaises(BundleFormatError):
            with BundleReader(self._archive, _MAX_BYTES):
                pass

    def test_reader_should_reject_a_blob_name_that_is_not_a_uuid(self):
        entries = self._valid_entries()
        entries['datastore/../../escape'] = 'nope'

        self._write_raw_zip(entries)
        with self.assertRaises(BundleFormatError):
            with BundleReader(self._archive, _MAX_BYTES):
                pass

    def test_reader_should_reject_a_missing_required_entry(self):
        entries = self._valid_entries()
        del entries[bundle_manifest.PRINCIPALS_ENTRY]

        self._write_raw_zip(entries)
        with self.assertRaises(BundleFormatError):
            with BundleReader(self._archive, _MAX_BYTES):
                pass

    def test_reader_should_reject_an_archive_over_the_cap(self):
        entries = self._valid_entries()
        # Highly compressible, so the file on disk is tiny while the declared
        # decompressed size is not — exactly the zip-bomb shape.
        entries[f'datastore/{_A_BLOB_UUID}'] = 'A' * (2 * 1024 * 1024)

        self._write_raw_zip(entries)
        with self.assertRaises(BundleTooLargeError):
            with BundleReader(self._archive, 1024):
                pass

    def test_reader_should_reject_a_file_that_is_not_a_zip(self):
        self._archive.write_bytes(b'not a zip at all')
        with self.assertRaises(BundleFormatError):
            with BundleReader(self._archive, _MAX_BYTES):
                pass

    def test_reader_should_reject_invalid_json(self):
        entries = self._valid_entries()
        entries[bundle_manifest.CASE_ENTRY] = '{ this is not json'

        self._write_raw_zip(entries)
        with BundleReader(self._archive, _MAX_BYTES) as bundle:
            with self.assertRaises(BundleFormatError):
                bundle.read_json(bundle_manifest.CASE_ENTRY)

    def test_writer_should_refuse_an_unknown_json_entry(self):
        with self.assertRaises(BundleFormatError):
            with BundleWriter(self._archive) as bundle:
                bundle.add_json('secrets.json', {})

    def test_writer_should_remove_a_partial_archive_on_failure(self):
        with self.assertRaises(BundleFormatError):
            with BundleWriter(self._archive) as bundle:
                bundle.add_json(bundle_manifest.CASE_ENTRY, {})
                bundle.add_json('secrets.json', {})
        self.assertFalse(self._archive.exists())

    def test_writer_should_refuse_a_blob_name_that_is_not_a_uuid(self):
        source = self._root / 'blob.bin'
        source.write_bytes(b'x')
        with self.assertRaises(BundleFormatError):
            with BundleWriter(self._archive) as bundle:
                bundle.add_blob('../escape', source)

    # ------------------------------------------------------------------
    # manifest validation
    # ------------------------------------------------------------------
    def test_validate_manifest_should_reject_an_unknown_format_version(self):
        with self.assertRaises(BundleFormatError):
            bundle_manifest.validate_manifest({
                'format_version': bundle_manifest.FORMAT_VERSION + 1, 'blobs': {}})

    def test_validate_manifest_should_reject_a_missing_blob_index(self):
        with self.assertRaises(BundleFormatError):
            bundle_manifest.validate_manifest({'format_version': bundle_manifest.FORMAT_VERSION})

    def test_validate_manifest_should_accept_a_well_formed_manifest(self):
        manifest = {'format_version': bundle_manifest.FORMAT_VERSION, 'blobs': {}}
        self.assertEqual(manifest, bundle_manifest.validate_manifest(manifest))
