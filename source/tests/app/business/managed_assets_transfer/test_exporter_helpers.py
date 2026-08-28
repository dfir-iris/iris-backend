#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in managed_assets_transfer/exporter.py."""

from unittest import TestCase
from unittest.mock import MagicMock

from app.business.managed_assets_transfer.exporter import (
    _row_dict,
    _sanitize_cell,
)


class TestSanitizeCell(TestCase):

    def test_none_returns_empty_string(self):
        self.assertEqual('', _sanitize_cell(None))

    def test_normal_string_returned_as_is(self):
        self.assertEqual('hello', _sanitize_cell('hello'))

    def test_equals_prefix_escaped(self):
        result = _sanitize_cell('=SUM(A1:A10)')
        self.assertEqual("'=SUM(A1:A10)", result)

    def test_plus_prefix_escaped(self):
        result = _sanitize_cell('+1')
        self.assertEqual("'+1", result)

    def test_minus_prefix_escaped(self):
        result = _sanitize_cell('-1')
        self.assertEqual("'-1", result)

    def test_at_prefix_escaped(self):
        result = _sanitize_cell('@SUM')
        self.assertEqual("'@SUM", result)

    def test_tab_prefix_escaped(self):
        result = _sanitize_cell('\t+cmd')
        self.assertTrue(result.startswith("'"))

    def test_cr_prefix_escaped(self):
        result = _sanitize_cell('\r+cmd')
        self.assertTrue(result.startswith("'"))

    def test_integer_coerced_to_string(self):
        self.assertEqual('42', _sanitize_cell(42))

    def test_safe_string_not_prefixed(self):
        result = _sanitize_cell('10.0.0.1')
        self.assertFalse(result.startswith("'"))

    def test_empty_string_returned_as_empty(self):
        self.assertEqual('', _sanitize_cell(''))


class TestRowDict(TestCase):

    def _make_asset(self, **kwargs):
        defaults = {
            'client_id': 1,
            'name': 'server-01',
            'asset_type_id': 2,
            'description': 'Test server',
            'criticality': 'high',
            'environment': 'production',
            'owner': 'ops',
            'location': 'dc1',
            'tags': 'web,backend',
            'ip': '10.0.0.1',
            'domain': 'example.com',
            'is_active': True,
            'source': 'manual',
            'custom_attributes': {'tier': '1'},
        }
        defaults.update(kwargs)
        asset = MagicMock()
        for key, value in defaults.items():
            setattr(asset, key, value)
        return asset

    def test_name_included(self):
        asset = self._make_asset(name='web-01')
        row = _row_dict(asset, {1: 'ACME'}, {2: 'Linux Server'})
        self.assertEqual('web-01', row['name'])

    def test_client_name_resolved(self):
        asset = self._make_asset(client_id=5)
        row = _row_dict(asset, {5: 'BigCorp'}, {})
        self.assertEqual('BigCorp', row['client_name'])

    def test_missing_client_name_defaults_empty(self):
        asset = self._make_asset(client_id=99)
        row = _row_dict(asset, {}, {})
        self.assertEqual('', row['client_name'])

    def test_asset_type_resolved(self):
        asset = self._make_asset(asset_type_id=3)
        row = _row_dict(asset, {}, {3: 'Windows Server'})
        self.assertEqual('Windows Server', row['asset_type'])

    def test_missing_type_defaults_empty(self):
        asset = self._make_asset(asset_type_id=99)
        row = _row_dict(asset, {}, {})
        self.assertEqual('', row['asset_type'])

    def test_all_expected_keys_present(self):
        asset = self._make_asset()
        row = _row_dict(asset, {1: 'ACME'}, {2: 'Linux Server'})
        for key in ('client_name', 'name', 'asset_type', 'description', 'criticality',
                    'environment', 'owner', 'location', 'tags', 'ip', 'domain',
                    'is_active', 'source', 'custom_attributes'):
            with self.subTest(key=key):
                self.assertIn(key, row)

    def test_custom_attributes_passed_through(self):
        asset = self._make_asset(custom_attributes={'tier': '2'})
        row = _row_dict(asset, {1: 'X'}, {2: 'Y'})
        self.assertEqual({'tier': '2'}, row['custom_attributes'])
