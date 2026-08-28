#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for FLOW_TARGET_* constants in models/investigation_flows.py."""

from unittest import TestCase

from app.models.investigation_flows import (
    FLOW_TARGET_ALERT,
    FLOW_TARGET_BOTH,
    FLOW_TARGET_CLUSTER,
    FLOW_TARGETS,
)


class TestFlowTargetConstants(TestCase):

    def test_alert_value(self):
        self.assertEqual('alert', FLOW_TARGET_ALERT)

    def test_cluster_value(self):
        self.assertEqual('alert_cluster', FLOW_TARGET_CLUSTER)

    def test_both_value(self):
        self.assertEqual('both', FLOW_TARGET_BOTH)

    def test_flow_targets_contains_alert(self):
        self.assertIn(FLOW_TARGET_ALERT, FLOW_TARGETS)

    def test_flow_targets_contains_cluster(self):
        self.assertIn(FLOW_TARGET_CLUSTER, FLOW_TARGETS)

    def test_flow_targets_contains_both(self):
        self.assertIn(FLOW_TARGET_BOTH, FLOW_TARGETS)

    def test_flow_targets_has_three_members(self):
        self.assertEqual(3, len(FLOW_TARGETS))

    def test_flow_targets_all_unique(self):
        self.assertEqual(len(FLOW_TARGETS), len(set(FLOW_TARGETS)))
