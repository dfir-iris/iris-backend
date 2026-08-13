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

"""Optional AES-256-GCM envelope around a finished case-transfer bundle.

Deliberately knows nothing about the bundle: it seals and opens an opaque byte
stream, so the zip logic in `manifest.py` never has to be passphrase-aware.

Container layout::

    magic        b'IRISENC1'                       8 bytes
    version      uint8                             1 byte
    scrypt n/r/p uint32 x 3, big endian           12 bytes
    salt                                          16 bytes
    chunk_size   uint32, big endian                4 bytes
    nonce_prefix                                   4 bytes
    ------------------------------------------------ 45-byte header, plaintext
    frames       [uint32 length][ciphertext||tag] repeated

Why this shape rather than a password-protected zip: the WinZip AES spec derives
its key with PBKDF2 at 1000 iterations, which is far too cheap against offline
cracking of an operator-chosen passphrase. scrypt is memory-hard, and that is
precisely the threat model for an archive that gets emailed around.

Three details carry real security weight:

* The whole plaintext header is bound into every frame as AAD, so an attacker
  cannot downgrade the scrypt cost parameters and then brute-force cheaply.
* The nonce is `prefix || counter`, so it is unique per frame under a given key.
  Reusing a nonce under GCM is catastrophic, and a counter makes that structural
  rather than a matter of luck.
* The last frame is flagged as final *in its AAD*. Without that, an attacker
  could lop frames off the end and every surviving frame would still
  authenticate individually — silent truncation of a case.
"""

import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.models.errors import BusinessProcessingError

MAGIC = b'IRISENC1'
_VERSION = 1

# scrypt at n=2**15, r=8, p=1 costs roughly 32 MB and ~100 ms per guess. High
# enough to make offline cracking painful, low enough that a worker importing a
# bundle does not stall.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_LEN = 16
_KEY_LEN = 32

_NONCE_PREFIX_LEN = 4
_TAG_LEN = 16
_CHUNK_SIZE = 1024 * 1024

# magic | version | scrypt n, r, p | salt | chunk_size | nonce_prefix
_HEADER_STRUCT = struct.Struct('>8sBIII16sI4s')
HEADER_LEN = _HEADER_STRUCT.size

_FRAME_LEN_STRUCT = struct.Struct('>I')
_AAD_SUFFIX_STRUCT = struct.Struct('>QB')


class ArchiveEncryptedError(BusinessProcessingError):
    """The archive is encrypted but no passphrase was supplied."""

    def __init__(self):
        super().__init__('Archive is encrypted — a passphrase is required')


class ArchiveDecryptionError(BusinessProcessingError):
    """Wrong passphrase, or the archive has been tampered with.

    Deliberately does not distinguish the two. Telling a caller which one it was
    hands them an oracle, and there is nothing useful they could do differently.
    """

    def __init__(self):
        super().__init__('Could not decrypt archive — wrong passphrase or corrupted file')


def is_encrypted(path: Path) -> bool:
    """Detect the envelope by magic bytes rather than by file extension.

    A bundle saved as `.iris` may well be encrypted, and one saved as `.iris.enc`
    may not be. Sniffing the content is the only answer that is always right.
    """
    try:
        with open(path, 'rb') as source:
            return source.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode('utf-8'))


def _aad(header: bytes, counter: int, is_final: bool) -> bytes:
    return header + _AAD_SUFFIX_STRUCT.pack(counter, 1 if is_final else 0)


def _nonce(prefix: bytes, counter: int) -> bytes:
    # 4-byte random prefix + 8-byte counter = the 12 bytes AES-GCM wants.
    return prefix + struct.pack('>Q', counter)


def seal_archive(source_path: Path, target_path: Path, passphrase: str) -> Path:
    """Encrypt `source_path` into `target_path`. Returns `target_path`.

    Streams in `_CHUNK_SIZE` frames so a multi-GB bundle never has to be held in
    memory.
    """
    if not passphrase:
        raise BusinessProcessingError('A passphrase is required to encrypt the archive')

    salt = os.urandom(_SALT_LEN)
    nonce_prefix = os.urandom(_NONCE_PREFIX_LEN)
    key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    aesgcm = AESGCM(key)

    header = _HEADER_STRUCT.pack(MAGIC, _VERSION, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
                                 salt, _CHUNK_SIZE, nonce_prefix)

    with open(source_path, 'rb') as plain, open(target_path, 'wb') as sealed:
        sealed.write(header)

        counter = 0
        # Read one chunk ahead so we know which frame is the last one and can
        # flag it before writing. An empty input still produces exactly one
        # (empty, final) frame, so every valid container ends the same way.
        pending = plain.read(_CHUNK_SIZE)
        while True:
            lookahead = plain.read(_CHUNK_SIZE)
            is_final = not lookahead

            frame = aesgcm.encrypt(_nonce(nonce_prefix, counter), pending,
                                   _aad(header, counter, is_final))
            sealed.write(_FRAME_LEN_STRUCT.pack(len(frame)))
            sealed.write(frame)

            if is_final:
                break

            pending = lookahead
            counter += 1

    return target_path


def open_archive(source_path: Path, target_path: Path, passphrase: str) -> Path:
    """Decrypt `source_path` into `target_path`. Returns `target_path`.

    Raises `ArchiveEncryptedError` if there is no passphrase, and
    `ArchiveDecryptionError` for a wrong passphrase, tampering, or truncation.
    On any failure the partial output is removed — a half-written bundle must
    never be left where the importer could pick it up.
    """
    if not passphrase:
        raise ArchiveEncryptedError()

    try:
        with open(source_path, 'rb') as sealed:
            header = sealed.read(HEADER_LEN)
            if len(header) != HEADER_LEN:
                raise ArchiveDecryptionError()

            magic, version, n, r, p, salt, chunk_size, nonce_prefix = _HEADER_STRUCT.unpack(header)
            if magic != MAGIC or version != _VERSION:
                raise ArchiveDecryptionError()

            # Cap the advertised cost and frame size before acting on them. The
            # header is authenticated, but only *after* we have already used
            # these numbers to derive a key and size a buffer — so an attacker
            # who never expects to pass authentication could still burn the
            # server's memory with an absurd n or chunk_size.
            if n > _SCRYPT_N or r > _SCRYPT_R * 4 or p > _SCRYPT_P * 4:
                raise ArchiveDecryptionError()
            if not 0 < chunk_size <= _CHUNK_SIZE * 16:
                raise ArchiveDecryptionError()

            key = _derive_key(passphrase, salt, n, r, p)
            aesgcm = AESGCM(key)
            max_frame = chunk_size + _TAG_LEN

            with open(target_path, 'wb') as plain:
                counter = 0
                saw_final = False
                while True:
                    raw_len = sealed.read(_FRAME_LEN_STRUCT.size)
                    if not raw_len:
                        break
                    if len(raw_len) != _FRAME_LEN_STRUCT.size:
                        raise ArchiveDecryptionError()

                    (frame_len,) = _FRAME_LEN_STRUCT.unpack(raw_len)
                    if not _TAG_LEN <= frame_len <= max_frame:
                        raise ArchiveDecryptionError()

                    frame = sealed.read(frame_len)
                    if len(frame) != frame_len:
                        raise ArchiveDecryptionError()

                    # Try non-final first: every frame but one is non-final, so
                    # this is the common path. A frame that authenticates under
                    # the final AAD instead is genuinely the last one.
                    try:
                        chunk = aesgcm.decrypt(_nonce(nonce_prefix, counter), frame,
                                               _aad(header, counter, False))
                    except InvalidTag:
                        chunk = aesgcm.decrypt(_nonce(nonce_prefix, counter), frame,
                                               _aad(header, counter, True))
                        saw_final = True

                    plain.write(chunk)

                    if saw_final:
                        # Trailing bytes after the final frame mean the file was
                        # appended to; refuse rather than silently ignore them.
                        if sealed.read(1):
                            raise ArchiveDecryptionError()
                        break

                    counter += 1

                if not saw_final:
                    raise ArchiveDecryptionError()

    except InvalidTag:
        _discard(target_path)
        raise ArchiveDecryptionError()
    except ArchiveDecryptionError:
        _discard(target_path)
        raise
    except (OSError, struct.error):
        _discard(target_path)
        raise ArchiveDecryptionError()

    return target_path


def _discard(path: Path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
