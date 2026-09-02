#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for `group_alert_units` — the pure half of the cluster-grouped
alert queue (`get_filtered_alert_groups`).

The SQL half needs a database; this covers the rules that decide what a
queue row contains: which alerts hang under which cluster, the member cap,
and the de-duplication of the hydration list.
"""

from unittest import TestCase

from app.datamgmt.alerts.alerts_db import group_alert_units


class TestGroupAlertUnits(TestCase):

    def test_orphan_alerts_become_their_own_units(self):
        units, hydrate = group_alert_units([('alert', 7), ('alert', 9)], [])

        self.assertEqual([{'kind': 'alert', 'alert_id': 7},
                          {'kind': 'alert', 'alert_id': 9}], units)
        self.assertEqual([7, 9], hydrate)

    def test_cluster_unit_collects_its_matching_members(self):
        units, hydrate = group_alert_units(
            [('cluster', 3)],
            [(3, 11), (3, 12), (3, 13)]
        )

        self.assertEqual(1, len(units))
        self.assertEqual('cluster', units[0]['kind'])
        self.assertEqual(3, units[0]['cluster_id'])
        self.assertEqual([11, 12, 13], units[0]['alert_ids'])
        self.assertEqual(3, units[0]['alerts_total'])
        self.assertFalse(units[0]['alerts_truncated'])
        self.assertEqual([11, 12, 13], hydrate)

    def test_unit_order_is_preserved(self):
        units, _ = group_alert_units(
            [('alert', 1), ('cluster', 5), ('alert', 2)],
            [(5, 50)]
        )

        self.assertEqual(['alert', 'cluster', 'alert'], [u['kind'] for u in units])

    def test_clustered_alert_is_not_also_listed_at_top_level(self):
        # The whole point of the grouped queue: an alert inside a cluster is
        # only reachable through it. The SQL only emits a top-level unit for
        # alerts with no membership, so a member id never shows up as an
        # 'alert' unit here.
        units, _ = group_alert_units([('cluster', 4)], [(4, 42)])

        self.assertEqual([], [u for u in units if u['kind'] == 'alert'])

    def test_alert_in_two_clusters_appears_under_each(self):
        units, hydrate = group_alert_units(
            [('cluster', 1), ('cluster', 2)],
            [(1, 99), (2, 99), (2, 100)]
        )

        self.assertEqual([99], units[0]['alert_ids'])
        self.assertEqual([99, 100], units[1]['alert_ids'])
        # ...but is only loaded once.
        self.assertEqual([99, 100], hydrate)

    def test_members_are_capped_and_the_true_count_is_reported(self):
        members = [(8, i) for i in range(100, 160)]

        units, hydrate = group_alert_units([('cluster', 8)], members, member_limit=50)

        self.assertEqual(50, len(units[0]['alert_ids']))
        self.assertEqual(60, units[0]['alerts_total'])
        self.assertTrue(units[0]['alerts_truncated'])
        self.assertEqual(50, len(hydrate))

    def test_cluster_with_no_matching_member_yields_an_empty_row(self):
        # Shouldn't happen — the SQL only emits a cluster unit when one of
        # its alerts matched — but an empty row must not blow up.
        units, hydrate = group_alert_units([('cluster', 6)], [])

        self.assertEqual([], units[0]['alert_ids'])
        self.assertEqual(0, units[0]['alerts_total'])
        self.assertFalse(units[0]['alerts_truncated'])
        self.assertEqual([], hydrate)

    def test_empty_page(self):
        self.assertEqual(([], []), group_alert_units([], []))
