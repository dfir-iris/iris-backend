#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in business/case_transfer/importer.py."""

from unittest import TestCase

from app.business.case_transfer.importer import _case_name


class TestCaseName(TestCase):

    def test_basic_name_gets_prefixed(self):
        result = _case_name('Ransomware Attack', 42)
        self.assertEqual('#42 - Ransomware Attack', result)

    def test_existing_prefix_stripped_and_new_applied(self):
        result = _case_name('#7 - Old Case Name', 99)
        self.assertEqual('#99 - Old Case Name', result)

    def test_none_raw_name_uses_default(self):
        result = _case_name(None, 10)
        self.assertEqual('#10 - Imported case', result)

    def test_empty_raw_name_uses_default(self):
        result = _case_name('', 5)
        self.assertEqual('#5 - Imported case', result)

    def test_whitespace_only_name_uses_default(self):
        result = _case_name('   ', 3)
        self.assertEqual('#3 - Imported case', result)

    def test_long_name_truncated_to_256(self):
        long_name = 'x' * 300
        result = _case_name(long_name, 1)
        self.assertLessEqual(len(result), 256)

    def test_prefix_stripped_even_with_multi_digit_id(self):
        result = _case_name('#12345 - Some incident', 1)
        self.assertEqual('#1 - Some incident', result)

    def test_name_without_prefix_kept_intact(self):
        result = _case_name('Fresh Case', 7)
        self.assertEqual('#7 - Fresh Case', result)

    def test_result_starts_with_hash_and_id(self):
        result = _case_name('Phishing', 8)
        self.assertTrue(result.startswith('#8 - '))
