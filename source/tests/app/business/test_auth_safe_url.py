#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _is_safe_url in business/auth.py.

This is a security-critical URL validation function (CWE-601 / open-redirect
guard). Comprehensive coverage of bypass attempts is intentional.
"""

from unittest import TestCase

from app.business.auth import _is_safe_url


class TestIsSafeUrl(TestCase):

    # ── valid relative paths ──────────────────────────────────────────────────

    def test_simple_path_is_safe(self):
        self.assertTrue(_is_safe_url('/dashboard'))

    def test_path_with_query_is_safe(self):
        self.assertTrue(_is_safe_url('/case?cid=1'))

    def test_path_with_fragment_is_safe(self):
        self.assertTrue(_is_safe_url('/case#section'))

    def test_nested_path_is_safe(self):
        self.assertTrue(_is_safe_url('/case/1/timeline'))

    def test_root_path_is_safe(self):
        self.assertTrue(_is_safe_url('/'))

    # ── non-string inputs ─────────────────────────────────────────────────────

    def test_none_is_not_safe(self):
        self.assertFalse(_is_safe_url(None))

    def test_empty_string_is_not_safe(self):
        self.assertFalse(_is_safe_url(''))

    def test_integer_is_not_safe(self):
        self.assertFalse(_is_safe_url(42))

    # ── absolute URLs (open-redirect attacks) ─────────────────────────────────

    def test_http_absolute_url_not_safe(self):
        self.assertFalse(_is_safe_url('http://evil.com/path'))

    def test_https_absolute_url_not_safe(self):
        self.assertFalse(_is_safe_url('https://evil.com'))

    def test_protocol_relative_double_slash_not_safe(self):
        self.assertFalse(_is_safe_url('//evil.com'))

    def test_no_leading_slash_not_safe(self):
        self.assertFalse(_is_safe_url('evil.com'))

    def test_attacker_dot_com_without_scheme_not_safe(self):
        # Classic bypass: no scheme, but resolves as host
        self.assertFalse(_is_safe_url('attacker.com?cid=1'))

    # ── control character injection ────────────────────────────────────────────

    def test_newline_in_url_not_safe(self):
        self.assertFalse(_is_safe_url('/path\nevil.com'))

    def test_carriage_return_in_url_not_safe(self):
        self.assertFalse(_is_safe_url('/path\revil.com'))

    def test_tab_in_url_not_safe(self):
        self.assertFalse(_is_safe_url('/path\tevil'))

    def test_null_byte_in_url_not_safe(self):
        self.assertFalse(_is_safe_url('/path\x00evil'))

    def test_other_control_char_not_safe(self):
        self.assertFalse(_is_safe_url('/path\x01evil'))

    # ── backslash bypass ──────────────────────────────────────────────────────

    def test_backslash_protocol_relative_not_safe(self):
        # Some browsers normalise /\ -> // (GHSA-vjc3-7jwv-j9qf)
        self.assertFalse(_is_safe_url('/\\evil.com'))

    def test_backslash_in_path_not_safe(self):
        self.assertFalse(_is_safe_url('/path\\evil'))

    # ── javascript: scheme ────────────────────────────────────────────────────

    def test_javascript_scheme_not_safe(self):
        self.assertFalse(_is_safe_url('javascript:alert(1)'))

    def test_javascript_path_not_safe(self):
        self.assertFalse(_is_safe_url('javascript://evil'))
