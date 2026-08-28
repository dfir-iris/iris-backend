#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in datamgmt/custom_dashboard/custom_dashboard_db.py.

_resolve_shared_flag is a pure dict → bool function; no DB context needed.
"""

from unittest import TestCase

from app.datamgmt.custom_dashboard.custom_dashboard_db import _resolve_shared_flag


class TestResolveSharedFlag(TestCase):

    # ── bool inputs ──────────────────────────────────────────────────────────

    def test_bool_true(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': True}))

    def test_bool_false(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': False}))

    # ── string inputs ─────────────────────────────────────────────────────────

    def test_string_true_lowercase(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': 'true'}))

    def test_string_true_uppercase(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': 'TRUE'}))

    def test_string_one(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': '1'}))

    def test_string_yes(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': 'yes'}))

    def test_string_on(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': 'on'}))

    def test_string_false(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': 'false'}))

    def test_string_zero(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': '0'}))

    def test_string_no(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': 'no'}))

    # ── int inputs ────────────────────────────────────────────────────────────

    def test_int_nonzero_is_true(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': 1}))

    def test_int_zero_is_false(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': 0}))

    def test_int_negative_is_true(self):
        self.assertTrue(_resolve_shared_flag({'is_shared': -1}))

    # ── missing / None ────────────────────────────────────────────────────────

    def test_missing_key_is_false(self):
        self.assertFalse(_resolve_shared_flag({}))

    def test_none_value_is_false(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': None}))

    def test_list_value_is_false(self):
        self.assertFalse(_resolve_shared_flag({'is_shared': [True]}))
