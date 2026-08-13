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

"""Unit tests for the case-transfer archive envelope.

These run without a database or a Flask context: `crypto.py` deliberately deals
only in byte streams. The multi-chunk cases matter most — the frame counter and
the final-frame flag only do anything past the 1 MiB boundary, and a bug there
would be invisible on the small archives most manual testing produces.
"""

import os
import tempfile
from pathlib import Path
from unittest import TestCase

from app.business.case_transfer import crypto

_PASSPHRASE = 'correct horse battery staple'


class TestCaseTransferCrypto(TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def _write(self, name, payload):
        path = self._root / name
        path.write_bytes(payload)
        return path

    def _round_trip(self, payload, passphrase=_PASSPHRASE):
        plain = self._write('plain.bin', payload)
        sealed = self._root / 'sealed.bin'
        crypto.seal_archive(plain, sealed, passphrase)

        opened = self._root / 'opened.bin'
        crypto.open_archive(sealed, opened, passphrase)
        return sealed, opened

    def test_round_trip_should_preserve_content(self):
        payload = b'a case bundle'
        _, opened = self._round_trip(payload)
        self.assertEqual(payload, opened.read_bytes())

    def test_round_trip_should_preserve_content_over_several_chunks(self):
        # 3.01 MiB — four frames, so the counter has to advance correctly and
        # only the last frame may carry the final flag.
        payload = os.urandom(3 * 1024 * 1024 + 10240)
        _, opened = self._round_trip(payload)
        self.assertEqual(payload, opened.read_bytes())

    def test_round_trip_should_preserve_an_empty_archive(self):
        _, opened = self._round_trip(b'')
        self.assertEqual(b'', opened.read_bytes())

    def test_sealed_archive_should_start_with_the_magic(self):
        sealed, _ = self._round_trip(b'anything')
        self.assertEqual(crypto.MAGIC, sealed.read_bytes()[:len(crypto.MAGIC)])

    def test_is_encrypted_should_be_true_for_a_sealed_archive(self):
        sealed, _ = self._round_trip(b'anything')
        self.assertTrue(crypto.is_encrypted(sealed))

    def test_is_encrypted_should_be_false_for_a_plain_file(self):
        plain = self._write('plain.zip', b'PK\x03\x04 not encrypted at all')
        self.assertFalse(crypto.is_encrypted(plain))

    def test_is_encrypted_should_be_false_for_a_missing_file(self):
        self.assertFalse(crypto.is_encrypted(self._root / 'nope.bin'))

    def test_open_should_raise_when_no_passphrase_is_supplied(self):
        sealed, _ = self._round_trip(b'anything')
        with self.assertRaises(crypto.ArchiveEncryptedError):
            crypto.open_archive(sealed, self._root / 'out.bin', '')

    def test_open_should_raise_on_a_wrong_passphrase(self):
        sealed, _ = self._round_trip(b'anything')
        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, self._root / 'out.bin', 'not the passphrase')

    def test_open_should_leave_nothing_behind_on_a_wrong_passphrase(self):
        sealed, _ = self._round_trip(b'anything')
        target = self._root / 'out.bin'
        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, target, 'not the passphrase')
        self.assertFalse(target.exists())

    def test_open_should_reject_a_flipped_ciphertext_byte(self):
        sealed, _ = self._round_trip(b'a case bundle worth tampering with')
        raw = bytearray(sealed.read_bytes())
        raw[-1] ^= 0xFF
        sealed.write_bytes(bytes(raw))

        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, self._root / 'out.bin', _PASSPHRASE)

    def test_open_should_reject_a_tampered_header(self):
        # The scrypt cost lives in the header. If the header were not bound in
        # as AAD, an attacker could drop it to n=2 and brute-force cheaply.
        sealed, _ = self._round_trip(b'a case bundle')
        raw = bytearray(sealed.read_bytes())
        raw[10] ^= 0x01
        sealed.write_bytes(bytes(raw))

        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, self._root / 'out.bin', _PASSPHRASE)

    def test_open_should_reject_a_truncated_archive(self):
        payload = os.urandom(2 * 1024 * 1024 + 512)
        sealed, _ = self._round_trip(payload)
        raw = sealed.read_bytes()

        # Lop off the final frame. Every surviving frame still authenticates on
        # its own — only the final-frame flag makes this detectable.
        sealed.write_bytes(raw[:crypto.HEADER_LEN + 4 + (1024 * 1024 + 16)])

        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, self._root / 'out.bin', _PASSPHRASE)

    def test_open_should_reject_trailing_bytes(self):
        sealed, _ = self._round_trip(b'a case bundle')
        with open(sealed, 'ab') as handle:
            handle.write(b'appended')

        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(sealed, self._root / 'out.bin', _PASSPHRASE)

    def test_open_should_reject_a_file_that_is_not_an_envelope(self):
        plain = self._write('plain.zip', b'PK\x03\x04 definitely a zip')
        with self.assertRaises(crypto.ArchiveDecryptionError):
            crypto.open_archive(plain, self._root / 'out.bin', _PASSPHRASE)

    def test_seal_should_require_a_passphrase(self):
        plain = self._write('plain.bin', b'anything')
        with self.assertRaises(Exception):
            crypto.seal_archive(plain, self._root / 'sealed.bin', '')

    def test_two_seals_of_the_same_input_should_differ(self):
        # Fresh salt and nonce prefix each time, so identical bundles must not
        # produce identical ciphertext.
        plain = self._write('plain.bin', b'a case bundle')
        first = self._root / 'first.bin'
        second = self._root / 'second.bin'
        crypto.seal_archive(plain, first, _PASSPHRASE)
        crypto.seal_archive(plain, second, _PASSPHRASE)

        self.assertNotEqual(first.read_bytes(), second.read_bytes())
