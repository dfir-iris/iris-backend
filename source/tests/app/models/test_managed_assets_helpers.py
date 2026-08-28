#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in models/managed_assets.py."""

from unittest import TestCase

from app.models.managed_assets import (
    MANAGED_ASSET_CRITICALITIES,
    MANAGED_ASSET_ENVIRONMENTS,
    _sql_in_list,
)


class TestSqlInList(TestCase):

    def test_single_value(self):
        result = _sql_in_list(('critical',))
        self.assertEqual("'critical'", result)

    def test_multiple_values(self):
        result = _sql_in_list(('critical', 'high', 'medium'))
        self.assertEqual("'critical','high','medium'", result)

    def test_each_value_quoted(self):
        result = _sql_in_list(('a', 'b'))
        self.assertIn("'a'", result)
        self.assertIn("'b'", result)

    def test_empty_iterable_returns_empty_string(self):
        result = _sql_in_list(())
        self.assertEqual('', result)


class TestManagedAssetConstants(TestCase):

    def test_criticalities_contains_critical(self):
        self.assertIn('critical', MANAGED_ASSET_CRITICALITIES)

    def test_criticalities_contains_unknown(self):
        self.assertIn('unknown', MANAGED_ASSET_CRITICALITIES)

    def test_criticalities_is_tuple(self):
        self.assertIsInstance(MANAGED_ASSET_CRITICALITIES, tuple)

    def test_environments_contains_production(self):
        self.assertIn('production', MANAGED_ASSET_ENVIRONMENTS)

    def test_environments_contains_unknown(self):
        self.assertIn('unknown', MANAGED_ASSET_ENVIRONMENTS)

    def test_environments_is_tuple(self):
        self.assertIsInstance(MANAGED_ASSET_ENVIRONMENTS, tuple)

    def test_all_criticalities_are_lowercase(self):
        for c in MANAGED_ASSET_CRITICALITIES:
            with self.subTest(criticality=c):
                self.assertEqual(c, c.lower())

    def test_all_environments_are_lowercase(self):
        for e in MANAGED_ASSET_ENVIRONMENTS:
            with self.subTest(environment=e):
                self.assertEqual(e, e.lower())
