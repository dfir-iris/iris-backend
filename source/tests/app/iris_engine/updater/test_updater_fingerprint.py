#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the update-archive fingerprint verifier.

`verify_archive_fingerprint` reads a file, computes SHA-256, and
compares it (case-insensitively) against the supplied hex digest.
No Flask app context required; the function only touches the
filesystem and the `update_log*` helpers which just emit to a socket
(harmless in test).
"""

import hashlib
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.updater.updater import verify_archive_fingerprint


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


class TestVerifyArchiveFingerprint(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        p = self._root / name
        p.write_bytes(data)
        return p

    def _run(self, archive: Path, digest: str) -> bool:
        with patch('app.iris_engine.updater.updater.update_log', side_effect=lambda *_: None), \
             patch('app.iris_engine.updater.updater.update_log_error', side_effect=lambda *_: None):
            return verify_archive_fingerprint(archive, digest)

    # -- Happy path -----------------------------------------------------------

    def test_correct_digest_returns_true(self):
        data = b'iris update payload'
        archive = self._write('update.zip', data)
        self.assertTrue(self._run(archive, _sha256_hex(data)))

    def test_correct_digest_lowercase_also_accepted(self):
        data = b'lowercase digest test'
        archive = self._write('update.zip', data)
        digest_lower = _sha256_hex(data).lower()
        self.assertTrue(self._run(archive, digest_lower))

    def test_correct_digest_mixed_case_accepted(self):
        data = b'mixed case test'
        archive = self._write('update.zip', data)
        digest = _sha256_hex(data)
        # Mix upper and lower
        mixed = digest[:32].lower() + digest[32:].upper()
        self.assertTrue(self._run(archive, mixed))

    def test_empty_file_correct_digest(self):
        archive = self._write('empty.zip', b'')
        self.assertTrue(self._run(archive, _sha256_hex(b'')))

    def test_large_file_correct_digest(self):
        data = b'x' * 100_000
        archive = self._write('big.zip', data)
        self.assertTrue(self._run(archive, _sha256_hex(data)))

    # -- Tamper detection -----------------------------------------------------

    def test_wrong_digest_returns_false(self):
        data = b'original content'
        archive = self._write('update.zip', data)
        wrong_digest = _sha256_hex(b'different content')
        self.assertFalse(self._run(archive, wrong_digest))

    def test_one_byte_changed_fails(self):
        data = b'original content'
        archive = self._write('update.zip', data)
        digest = _sha256_hex(data)
        # Flip one byte in the file after capturing the correct digest
        tampered = bytearray(data)
        tampered[0] ^= 0xFF
        archive.write_bytes(bytes(tampered))
        self.assertFalse(self._run(archive, digest))

    def test_extra_byte_appended_fails(self):
        data = b'original'
        archive = self._write('update.zip', data)
        digest = _sha256_hex(data)
        archive.write_bytes(data + b'\x00')
        self.assertFalse(self._run(archive, digest))

    def test_empty_digest_string_fails(self):
        data = b'content'
        archive = self._write('update.zip', data)
        self.assertFalse(self._run(archive, ''))

    def test_all_zeros_digest_fails(self):
        data = b'content'
        archive = self._write('update.zip', data)
        self.assertFalse(self._run(archive, '0' * 64))

    # -- Missing file ---------------------------------------------------------

    def test_missing_file_returns_false(self):
        missing = self._root / 'nonexistent.zip'
        self.assertFalse(self._run(missing, 'A' * 64))

    def test_path_to_directory_returns_false(self):
        # is_file() returns False for directories
        dir_path = self._root / 'subdir'
        dir_path.mkdir()
        self.assertFalse(self._run(dir_path, 'A' * 64))
