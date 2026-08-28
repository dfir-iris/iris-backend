#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for managed-asset import staging helpers.

ImportWorkspace and the resolve token-validation logic are tested here
without a live Flask app — the staging root is overridden via
`unittest.mock.patch` so all directory operations go into a tmpdir.

The key invariants verified:
  * The token format gate in `resolve` accepts only 32-char hex strings.
  * `ImportWorkspace.__truediv__` blocks path-traversal names.
  * write_metadata / read_metadata round-trip arbitrary JSON-serialisable dicts.
  * `assert_owner` accepts the right owner and rejects a different one.
  * read_metadata on a missing file raises ObjectNotFoundError.
"""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.business.managed_assets_transfer.staging import (
    ImportWorkspace,
    _TOKEN_RE,
    UPLOAD_NAME,
    assert_owner,
    resolve,
)
from app.models.errors import ObjectNotFoundError


# ---------------------------------------------------------------------------
# Token regex
# ---------------------------------------------------------------------------

class TestTokenRegex(TestCase):

    def test_valid_32_char_hex(self):
        self.assertIsNotNone(_TOKEN_RE.match('a' * 32))

    def test_valid_hex_with_digits(self):
        self.assertIsNotNone(_TOKEN_RE.match('0123456789abcdef' * 2))

    def test_too_short_rejected(self):
        self.assertIsNone(_TOKEN_RE.match('a' * 31))

    def test_too_long_rejected(self):
        self.assertIsNone(_TOKEN_RE.match('a' * 33))

    def test_uppercase_rejected(self):
        # Token regex is strictly lowercase hex
        self.assertIsNone(_TOKEN_RE.match('A' * 32))

    def test_non_hex_char_rejected(self):
        self.assertIsNone(_TOKEN_RE.match('g' * 32))

    def test_empty_rejected(self):
        self.assertIsNone(_TOKEN_RE.match(''))

    def test_path_separator_rejected(self):
        self.assertIsNone(_TOKEN_RE.match('../' + 'a' * 29))


# ---------------------------------------------------------------------------
# ImportWorkspace.__truediv__  (path-traversal guard)
# ---------------------------------------------------------------------------

class TestImportWorkspaceTruediv(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._ws = ImportWorkspace('a' * 32, self._root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_safe_name_resolves_under_root(self):
        result = self._ws / 'file.bin'
        self.assertEqual(self._root / 'file.bin', result)

    def test_upload_name_constant_resolves(self):
        result = self._ws / UPLOAD_NAME
        self.assertEqual(self._root / UPLOAD_NAME, result)

    def test_path_traversal_dot_dot_slash_blocked(self):
        with self.assertRaises(ValueError):
            _ = self._ws / '../etc/passwd'

    def test_path_traversal_backslash_blocked(self):
        with self.assertRaises(ValueError):
            _ = self._ws / 'a\\b'

    def test_dot_blocked(self):
        with self.assertRaises(ValueError):
            _ = self._ws / '.'

    def test_double_dot_blocked(self):
        with self.assertRaises(ValueError):
            _ = self._ws / '..'


# ---------------------------------------------------------------------------
# ImportWorkspace.write_metadata / read_metadata
# ---------------------------------------------------------------------------

class TestImportWorkspaceMetadata(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._ws = ImportWorkspace('b' * 32, self._root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_simple_dict(self):
        self._ws.write_metadata({'owner_id': 7, 'filename': 'assets.zip'})
        result = self._ws.read_metadata()
        self.assertEqual({'owner_id': 7, 'filename': 'assets.zip'}, result)

    def test_round_trip_nested_dict(self):
        payload = {'a': {'b': [1, 2, 3]}, 'c': True}
        self._ws.write_metadata(payload)
        self.assertEqual(payload, self._ws.read_metadata())

    def test_round_trip_empty_dict(self):
        self._ws.write_metadata({})
        self.assertEqual({}, self._ws.read_metadata())

    def test_read_metadata_missing_file_raises(self):
        with self.assertRaises(ObjectNotFoundError):
            self._ws.read_metadata()

    def test_read_metadata_corrupt_json_raises(self):
        meta_path = self._root / 'staging.json'
        meta_path.write_text('{not valid json}', encoding='utf-8')
        with self.assertRaises(ObjectNotFoundError):
            self._ws.read_metadata()

    def test_write_overwrites_previous(self):
        self._ws.write_metadata({'v': 1})
        self._ws.write_metadata({'v': 2})
        self.assertEqual({'v': 2}, self._ws.read_metadata())


# ---------------------------------------------------------------------------
# resolve — token validation gate
# ---------------------------------------------------------------------------

class TestResolve(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_root(self):
        return patch(
            'app.business.managed_assets_transfer.staging._staging_root',
            return_value=self._root,
        )

    def test_valid_token_with_existing_dir(self):
        token = 'c' * 32
        (self._root / token).mkdir()
        with self._patch_root():
            ws = resolve(token)
        self.assertEqual(token, ws.token)

    def test_valid_token_missing_dir_raises(self):
        token = 'd' * 32
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve(token)

    def test_non_string_token_raises(self):
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve(None)  # type: ignore

    def test_short_token_raises(self):
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve('a' * 31)

    def test_long_token_raises(self):
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve('a' * 33)

    def test_path_traversal_token_raises(self):
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve('../' + 'a' * 29)

    def test_uppercase_token_raises(self):
        with self._patch_root():
            with self.assertRaises(ObjectNotFoundError):
                resolve('A' * 32)


# ---------------------------------------------------------------------------
# assert_owner
# ---------------------------------------------------------------------------

class TestAssertOwner(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._ws = ImportWorkspace('e' * 32, self._root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_matching_owner_returns_metadata(self):
        self._ws.write_metadata({'owner_id': 42, 'filename': 'pack.zip'})
        meta = assert_owner(self._ws, 42)
        self.assertEqual(42, meta['owner_id'])

    def test_wrong_owner_raises_not_found(self):
        self._ws.write_metadata({'owner_id': 42})
        with self.assertRaises(ObjectNotFoundError):
            assert_owner(self._ws, 99)

    def test_missing_metadata_raises_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            assert_owner(self._ws, 1)

    def test_owner_id_none_in_metadata_raises_when_owner_given(self):
        # If metadata has no owner_id field, metadata.get('owner_id') returns
        # None, which won't equal any real user id.
        self._ws.write_metadata({'filename': 'x.zip'})
        with self.assertRaises(ObjectNotFoundError):
            assert_owner(self._ws, 1)
