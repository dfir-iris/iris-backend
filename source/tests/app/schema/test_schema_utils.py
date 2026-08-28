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

"""Unit tests for app.schema.utils.

Pure-Python — no DB, no Flask context required.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from marshmallow import ValidationError

from app.schema.utils import assert_type_mml, file_sha256sum, stream_sha256sum, str_to_bool


# ===========================================================================
# str_to_bool
# ===========================================================================


class TestStrToBool(unittest.TestCase):

    def test_none_returns_false(self):
        self.assertFalse(str_to_bool(None))

    def test_bool_true_passthrough(self):
        self.assertTrue(str_to_bool(True))

    def test_bool_false_passthrough(self):
        self.assertFalse(str_to_bool(False))

    def test_int_zero_returns_false(self):
        self.assertFalse(str_to_bool(0))

    def test_int_one_returns_true(self):
        self.assertTrue(str_to_bool(1))

    def test_int_nonzero_returns_true(self):
        self.assertTrue(str_to_bool(42))

    def test_string_true_lowercase(self):
        self.assertTrue(str_to_bool("true"))

    def test_string_one(self):
        self.assertTrue(str_to_bool("1"))

    def test_string_yes(self):
        self.assertTrue(str_to_bool("yes"))

    def test_string_y(self):
        self.assertTrue(str_to_bool("y"))

    def test_string_t(self):
        self.assertTrue(str_to_bool("t"))

    def test_string_false_lowercase(self):
        self.assertFalse(str_to_bool("false"))

    def test_string_zero(self):
        self.assertFalse(str_to_bool("0"))

    def test_string_no(self):
        self.assertFalse(str_to_bool("no"))

    def test_case_insensitive_true(self):
        self.assertTrue(str_to_bool("TRUE"))
        self.assertTrue(str_to_bool("True"))
        self.assertTrue(str_to_bool("YES"))

    def test_case_insensitive_false(self):
        self.assertFalse(str_to_bool("FALSE"))
        self.assertFalse(str_to_bool("No"))


# ===========================================================================
# stream_sha256sum
# ===========================================================================


class TestStreamSha256sum(unittest.TestCase):

    def test_empty_bytes_known_digest(self):
        expected = hashlib.sha256(b"").hexdigest().upper()
        self.assertEqual(expected, stream_sha256sum(b""))

    def test_hello_bytes_known_digest(self):
        expected = hashlib.sha256(b"hello").hexdigest().upper()
        self.assertEqual(expected, stream_sha256sum(b"hello"))

    def test_returns_uppercase(self):
        result = stream_sha256sum(b"test")
        self.assertEqual(result, result.upper())

    def test_returns_64_hex_chars(self):
        result = stream_sha256sum(b"iris")
        self.assertEqual(64, len(result))
        self.assertTrue(all(c in "0123456789ABCDEF" for c in result))


# ===========================================================================
# file_sha256sum
# ===========================================================================


class TestFileSha256sum(unittest.TestCase):

    def test_missing_file_returns_none(self):
        self.assertIsNone(file_sha256sum("/nonexistent/path/to/file.bin"))

    def test_known_content_returns_correct_digest(self):
        content = b"iris test content"
        expected = hashlib.sha256(content).hexdigest().upper()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            self.assertEqual(expected, file_sha256sum(tmp_path))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_empty_file_returns_empty_digest(self):
        expected = hashlib.sha256(b"").hexdigest().upper()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            self.assertEqual(expected, file_sha256sum(tmp_path))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_returns_uppercase(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"uppercase check")
            tmp_path = tmp.name
        try:
            result = file_sha256sum(tmp_path)
            self.assertEqual(result, result.upper())
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ===========================================================================
# assert_type_mml
# ===========================================================================


class TestAssertTypeMml(unittest.TestCase):

    def test_happy_path_returns_true(self):
        self.assertTrue(assert_type_mml("hello", "field", str))

    def test_none_with_allow_none_true_returns_true(self):
        self.assertTrue(assert_type_mml(None, "field", str, allow_none=True))

    def test_none_with_allow_none_false_raises(self):
        with self.assertRaises(ValidationError):
            assert_type_mml(None, "field", str, allow_none=False)

    def test_none_default_allow_none_raises(self):
        # allow_none defaults to False
        with self.assertRaises(ValidationError):
            assert_type_mml(None, "field", str)

    def test_wrong_type_raises(self):
        # str(123) succeeds via coercion, so use a value whose coercion fails:
        # int("notanumber") raises ValueError which the function catches and
        # re-raises as ValidationError.
        with self.assertRaises(ValidationError):
            assert_type_mml("notanumber", "field", int)

    def test_max_len_within_bound_returns_true(self):
        self.assertTrue(assert_type_mml("ab", "field", str, max_len=5))

    def test_max_len_exceeded_raises(self):
        with self.assertRaises(ValidationError):
            assert_type_mml("toolongstring", "field", str, max_len=5)

    def test_max_val_within_bound_returns_true(self):
        self.assertTrue(assert_type_mml(5, "field", int, max_val=10))

    def test_max_val_exceeded_raises(self):
        with self.assertRaises(ValidationError):
            assert_type_mml(20, "field", int, max_val=10)

    def test_min_val_within_bound_returns_true(self):
        self.assertTrue(assert_type_mml(5, "field", int, min_val=1))

    def test_min_val_violated_raises(self):
        with self.assertRaises(ValidationError):
            assert_type_mml(0, "field", int, min_val=1)

    def test_all_constraints_in_bounds_returns_true(self):
        self.assertTrue(assert_type_mml(5, "field", int, max_val=10, min_val=1))

    def test_field_name_appears_in_error(self):
        try:
            assert_type_mml(None, "my_field", str)
        except ValidationError as exc:
            self.assertEqual("my_field", exc.field_name)


if __name__ == "__main__":
    unittest.main()
