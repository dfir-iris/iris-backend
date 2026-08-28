#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _assert_visible in business/managed_assets.py.

Pure guard function — raises ObjectNotFoundError when the asset is
outside the caller's scope. No DB, no Flask context needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock

from app.business.managed_assets import _assert_visible
from app.models.errors import ObjectNotFoundError


def _scope(client_ids=None):
    scope = MagicMock()
    scope.get_client_ids.return_value = client_ids
    return scope


def _asset(client_id):
    asset = MagicMock()
    asset.client_id = client_id
    return asset


class TestAssertVisible(TestCase):

    def test_none_asset_raises_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            _assert_visible(None, _scope())

    def test_asset_with_no_client_id_restriction_returned(self):
        asset = _asset(client_id=5)
        result = _assert_visible(asset, _scope(client_ids=None))
        self.assertIs(asset, result)

    def test_asset_in_allowed_client_ids_returned(self):
        asset = _asset(client_id=3)
        result = _assert_visible(asset, _scope(client_ids=[1, 2, 3]))
        self.assertIs(asset, result)

    def test_asset_not_in_client_ids_raises_not_found(self):
        asset = _asset(client_id=9)
        with self.assertRaises(ObjectNotFoundError):
            _assert_visible(asset, _scope(client_ids=[1, 2, 3]))

    def test_single_client_id_match_accepted(self):
        asset = _asset(client_id=7)
        result = _assert_visible(asset, _scope(client_ids=[7]))
        self.assertIs(asset, result)

    def test_single_client_id_mismatch_raises(self):
        asset = _asset(client_id=7)
        with self.assertRaises(ObjectNotFoundError):
            _assert_visible(asset, _scope(client_ids=[8]))

    def test_empty_client_id_list_raises(self):
        # empty list → every client is excluded
        asset = _asset(client_id=1)
        with self.assertRaises(ObjectNotFoundError):
            _assert_visible(asset, _scope(client_ids=[]))
