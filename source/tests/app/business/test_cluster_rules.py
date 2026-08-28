#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure (no-DB) helpers in business.cluster_rules.

Only `_dedupe_key` is tested here — it is the only function in this
module that is self-contained enough to run without a live database.

Key properties verified:
  * The key is a hex digest (SHA-256 → 64 characters, lowercase hex).
  * Two calls with identical inputs produce the same key (determinism).
  * Changing the rule id changes the key.
  * Changing a group_by field value changes the key.
  * Adding / removing group_by fields changes the key.
  * A zero or negative time_window_seconds skips bucketing (key is stable
    across repeated calls in the same process run).
  * A positive time_window_seconds adds a time-bucket segment (the
    function is called twice in the same second, so both calls land in
    the same bucket and produce identical keys).
"""

import hashlib
import re
from unittest import TestCase

from app.business.cluster_rules import _dedupe_key


# ---------------------------------------------------------------------------
# Minimal stubs — no ORM required
# ---------------------------------------------------------------------------

class _Alert:
    def __init__(self, alert_id=1, alert_customer_id=10, **kwargs):
        self.alert_id = alert_id
        self.alert_customer_id = alert_customer_id
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Rule:
    def __init__(self, rule_id=1, conditions=None):
        self.rule_id = rule_id
        self.rule_conditions = conditions or {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDedupeKey(TestCase):

    def test_returns_hex_string(self):
        key = _dedupe_key(_Alert(), _Rule())
        self.assertIsInstance(key, str)
        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', key), key)

    def test_deterministic_with_no_group_by(self):
        alert = _Alert(alert_id=5, alert_customer_id=99)
        rule = _Rule(rule_id=7, conditions={})
        self.assertEqual(_dedupe_key(alert, rule), _dedupe_key(alert, rule))

    def test_different_rule_id_produces_different_key(self):
        alert = _Alert()
        key1 = _dedupe_key(alert, _Rule(rule_id=1))
        key2 = _dedupe_key(alert, _Rule(rule_id=2))
        self.assertNotEqual(key1, key2)

    def test_different_group_by_value_produces_different_key(self):
        rule = _Rule(conditions={'group_by': ['alert_customer_id']})
        alert_a = _Alert(alert_customer_id=1)
        alert_b = _Alert(alert_customer_id=2)
        self.assertNotEqual(
            _dedupe_key(alert_a, rule),
            _dedupe_key(alert_b, rule),
        )

    def test_same_group_by_value_produces_same_key(self):
        rule = _Rule(conditions={'group_by': ['alert_customer_id']})
        alert_a = _Alert(alert_id=1, alert_customer_id=42)
        alert_b = _Alert(alert_id=2, alert_customer_id=42)  # different alert, same customer
        self.assertEqual(
            _dedupe_key(alert_a, rule),
            _dedupe_key(alert_b, rule),
        )

    def test_extra_group_by_field_changes_key(self):
        rule_without = _Rule(conditions={'group_by': ['alert_customer_id']})
        rule_with = _Rule(conditions={'group_by': ['alert_customer_id', 'alert_title']})
        alert = _Alert(alert_customer_id=1, alert_title='malware')
        self.assertNotEqual(
            _dedupe_key(alert, rule_without),
            _dedupe_key(alert, rule_with),
        )

    def test_empty_group_by_stable_across_calls(self):
        alert = _Alert()
        rule = _Rule(conditions={'group_by': []})
        self.assertEqual(_dedupe_key(alert, rule), _dedupe_key(alert, rule))

    def test_zero_time_window_no_bucket_segment(self):
        rule_zero = _Rule(conditions={'group_by': [], 'time_window_seconds': 0})
        rule_none = _Rule(conditions={'group_by': []})
        alert = _Alert()
        # Both should produce the same key — zero window behaves like no window
        self.assertEqual(_dedupe_key(alert, rule_zero), _dedupe_key(alert, rule_none))

    def test_negative_time_window_no_bucket_segment(self):
        rule_neg = _Rule(conditions={'group_by': [], 'time_window_seconds': -5})
        rule_none = _Rule(conditions={'group_by': []})
        alert = _Alert()
        self.assertEqual(_dedupe_key(alert, rule_neg), _dedupe_key(alert, rule_none))

    def test_positive_time_window_produces_deterministic_bucket(self):
        # Two calls within the same second must land in the same bucket
        rule = _Rule(conditions={'group_by': [], 'time_window_seconds': 3600})
        alert = _Alert()
        self.assertEqual(_dedupe_key(alert, rule), _dedupe_key(alert, rule))

    def test_positive_time_window_differs_from_no_window(self):
        rule_windowed = _Rule(conditions={'group_by': [], 'time_window_seconds': 3600})
        rule_none = _Rule(conditions={'group_by': []})
        alert = _Alert()
        self.assertNotEqual(_dedupe_key(alert, rule_windowed), _dedupe_key(alert, rule_none))

    def test_missing_group_by_field_on_alert_uses_none_value(self):
        # getattr(alert, 'nonexistent_field', None) returns None — must not crash
        rule = _Rule(conditions={'group_by': ['nonexistent_field']})
        alert = _Alert()
        key = _dedupe_key(alert, rule)
        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', key))

    def test_key_is_sha256_of_pipe_joined_parts(self):
        # Verify the hash value matches hand-computed expectations for simple case
        rule = _Rule(rule_id=1, conditions={})
        alert = _Alert()
        # With no group_by and no time window, key_parts = ['1']
        expected_raw = '1'
        expected_hash = hashlib.sha256(expected_raw.encode('utf-8')).hexdigest()
        self.assertEqual(expected_hash, _dedupe_key(alert, rule))

    def test_multiple_group_by_fields_joined_with_pipe(self):
        rule = _Rule(rule_id=42, conditions={'group_by': ['alert_customer_id', 'alert_title']})
        alert = _Alert(alert_customer_id=7, alert_title='Phishing')
        # Reconstruct what the function should build
        raw = '42|alert_customer_id=7|alert_title=Phishing'
        expected = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        self.assertEqual(expected, _dedupe_key(alert, rule))
