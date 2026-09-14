#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure (no-DB) helpers in business.cluster_rules.

`_dedupe_key` is self-contained. `_apply_create_cluster` is not, but its
branch selection is the whole point of the duplicate-cluster guard, so it
is exercised here with the two cluster lookups and the session patched
out — what matters is *which* branch runs, not what the DB does.

Key properties verified for `_dedupe_key`:
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
from contextlib import ExitStack
from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.cluster_rules import _apply_create_cluster
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


# 90 seconds apart, so they fall in different 60-second buckets.
_AT_BUCKET_START = datetime(2026, 4, 16, 5, 11, 0)
_AT_NEXT_BUCKET = datetime(2026, 4, 16, 5, 12, 30)


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

    def test_bucket_moves_with_wall_clock_not_with_the_alert(self):
        # The property behind the duplicate-cluster bug: the bucket comes
        # from utcnow(), so the *same* alert and rule yield different keys
        # either side of a window boundary. Documented rather than fixed —
        # `_apply_create_cluster` guards the consequence instead.
        rule = _Rule(conditions={'group_by': [], 'time_window_seconds': 60})
        alert = _Alert()

        with patch('app.business.cluster_rules.datetime') as clock:
            clock.utcnow.return_value = _AT_BUCKET_START
            first = _dedupe_key(alert, rule)
            clock.utcnow.return_value = _AT_NEXT_BUCKET
            second = _dedupe_key(alert, rule)

        self.assertNotEqual(first, second)


class _ClusterRule:
    def __init__(self, rule_id=1, rule_name='Nesting Malware', conditions=None):
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.rule_conditions = conditions or {}
        self.rule_action_config = {}


class TestApplyCreateClusterGuard(TestCase):
    """One rule must put one alert in at most one cluster.

    `_dedupe_key` buckets on wall-clock time, so re-evaluating an alert
    across a window boundary — routine, since evaluation is enqueued on
    every alert update and the Celery task retries — computes a key that
    matches nothing. Without a second check that opens a duplicate
    cluster holding the same alert, which the triage queue then renders
    as two identical-looking rows.
    """

    def setUp(self):
        self.alert = _Alert(
            alert_id=90268,
            alert_customer_id=3,
            alert_title="An active 'SuspServiceBin' malware was detected",
            alert_severity_id=2,
        )
        self.rule = _ClusterRule(rule_id=11)

    def _run(self, by_key=None, by_rule=None):
        """Call `_apply_create_cluster` with both lookups stubbed.

        Returns `(result, created)` where `created` is the mock standing
        in for the `AlertCluster` constructor — `created.called` is the
        assertion that matters.
        """
        with ExitStack() as stack:
            key_lookup = stack.enter_context(
                patch('app.business.cluster_rules.alert_cluster_open_matching',
                      return_value=by_key))
            rule_lookup = stack.enter_context(
                patch('app.business.cluster_rules.alert_cluster_open_for_rule_alert',
                      return_value=by_rule))
            created = stack.enter_context(
                patch('app.business.cluster_rules.AlertCluster'))
            stack.enter_context(patch('app.business.cluster_rules.db'))
            stack.enter_context(
                patch('app.business.alert_clusters.resolve_status_id', return_value=1))

            result = _apply_create_cluster(self.rule, self.alert)

        self.key_lookup = key_lookup
        self.rule_lookup = rule_lookup
        return result, created

    def test_matching_dedupe_key_reuses_that_cluster(self):
        existing = MagicMock(alerts=[])
        result, created = self._run(by_key=existing)
        self.assertIs(existing, result)
        self.assertFalse(created.called)

    def test_key_miss_but_rule_already_clustered_alert_does_not_create(self):
        # The regression: this is the path that produced two clusters
        # titled "Auto-created by rule: ..." both holding one alert.
        already = MagicMock(alerts=[])
        result, created = self._run(by_key=None, by_rule=already)
        self.assertIs(already, result)
        self.assertFalse(
            created.called,
            'a second cluster was opened for an alert this rule had already clustered',
        )

    def test_guard_is_scoped_to_this_rule_and_alert(self):
        self._run(by_key=None, by_rule=MagicMock(alerts=[]))
        self.rule_lookup.assert_called_once_with(self.rule.rule_id, self.alert.alert_id)

    def test_alert_is_attached_to_the_reused_cluster(self):
        already = MagicMock(alerts=[])
        self._run(by_key=None, by_rule=already)
        self.assertEqual([self.alert], already.alerts)

    def test_alert_already_a_member_is_not_appended_twice(self):
        member = MagicMock()
        member.alert_id = self.alert.alert_id
        already = MagicMock(alerts=[member])
        self._run(by_key=None, by_rule=already)
        self.assertEqual([member], already.alerts)

    def test_both_lookups_missing_still_creates_a_cluster(self):
        # The guard must not suppress the genuine first-time creation.
        result, created = self._run(by_key=None, by_rule=None)
        self.assertTrue(created.called)
        self.assertIs(created.return_value, result)

    def test_dedupe_key_lookup_is_still_tried_first(self):
        # Stacking by key is the normal path; the guard is the fallback.
        existing = MagicMock(alerts=[])
        self._run(by_key=existing)
        self.assertTrue(self.key_lookup.called)
        self.assertFalse(self.rule_lookup.called)
