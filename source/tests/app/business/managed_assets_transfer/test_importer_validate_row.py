#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _validate_row in business/managed_assets_transfer/importer.py.

_validate_row is pure given a pre-built asset_types dict and seen set.
managed_assets_normalize_name is the only external call; it normalises
a string (lowercase, strip etc.) and is itself pure.
"""

from unittest import TestCase

from app.business.managed_assets_transfer.importer import _validate_row


ASSET_TYPES = {'server': 1, 'workstation': 2}


def _row(**kwargs):
    defaults = dict(
        name='test-server',
        asset_type='server',
        criticality='high',
        environment=None,
        is_active=None,
        custom_attributes=None,
    )
    defaults.update(kwargs)
    return defaults


class TestValidateRowSuccess(TestCase):

    def test_valid_row_returns_payload_and_entry(self):
        payload, entry = _validate_row(_row(), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNotNone(payload)
        self.assertEqual([], entry['errors'])

    def test_payload_has_name(self):
        payload, _ = _validate_row(_row(name='my-host'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertEqual('my-host', payload['name'])

    def test_payload_has_asset_type_id(self):
        payload, _ = _validate_row(_row(asset_type='workstation'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertEqual(2, payload['asset_type_id'])

    def test_criticality_defaulted_to_unknown_if_not_given(self):
        payload, _ = _validate_row(_row(criticality=None), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertEqual('unknown', payload['criticality'])

    def test_environment_none_is_allowed(self):
        payload, _ = _validate_row(_row(environment=None), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload['environment'])

    def test_valid_environment_stored(self):
        payload, _ = _validate_row(_row(environment='production'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertEqual('production', payload['environment'])

    def test_seen_updated_after_successful_row(self):
        seen = {}
        _validate_row(_row(), index=1, asset_types=ASSET_TYPES, seen=seen)
        self.assertEqual(1, len(seen))

    def test_entry_row_index_matches(self):
        _, entry = _validate_row(_row(), index=5, asset_types=ASSET_TYPES, seen={})
        self.assertEqual(5, entry['row'])

    def test_custom_attributes_valid_json_object(self):
        payload, _ = _validate_row(
            _row(custom_attributes='{"key": "val"}'), index=1, asset_types=ASSET_TYPES, seen={}
        )
        self.assertEqual({'key': 'val'}, payload['custom_attributes'])


class TestValidateRowErrors(TestCase):

    def test_missing_name_gives_error(self):
        payload, entry = _validate_row(_row(name=None), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload)
        self.assertTrue(any('name' in e for e in entry['errors']))

    def test_missing_asset_type_gives_error(self):
        payload, entry = _validate_row(_row(asset_type=None), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload)
        self.assertTrue(any('asset_type' in e for e in entry['errors']))

    def test_unknown_asset_type_gives_error(self):
        payload, entry = _validate_row(_row(asset_type='router'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload)
        self.assertTrue(any('unknown asset type' in e for e in entry['errors']))

    def test_invalid_criticality_gives_error(self):
        payload, entry = _validate_row(_row(criticality='extreme'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload)
        self.assertTrue(any('criticality' in e for e in entry['errors']))

    def test_invalid_environment_gives_error(self):
        payload, entry = _validate_row(_row(environment='outer-space'), index=1, asset_types=ASSET_TYPES, seen={})
        self.assertIsNone(payload)
        self.assertTrue(any('environment' in e for e in entry['errors']))

    def test_invalid_custom_attributes_json_gives_error(self):
        payload, entry = _validate_row(
            _row(custom_attributes='not-json'), index=1, asset_types=ASSET_TYPES, seen={}
        )
        self.assertIsNone(payload)
        self.assertTrue(any('custom_attributes' in e for e in entry['errors']))

    def test_non_object_custom_attributes_gives_error(self):
        payload, entry = _validate_row(
            _row(custom_attributes='[1,2,3]'), index=1, asset_types=ASSET_TYPES, seen={}
        )
        self.assertIsNone(payload)
        self.assertTrue(any('custom_attributes' in e for e in entry['errors']))

    def test_duplicate_row_gives_error(self):
        seen = {}
        _validate_row(_row(), index=1, asset_types=ASSET_TYPES, seen=seen)
        payload, entry = _validate_row(_row(), index=2, asset_types=ASSET_TYPES, seen=seen)
        self.assertIsNone(payload)
        self.assertTrue(any('duplicate' in e for e in entry['errors']))
