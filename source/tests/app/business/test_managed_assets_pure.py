#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in business/managed_assets.py and dim_hooks.py."""

from unittest import TestCase

from app.business.managed_assets import managed_assets_normalize_name
from app.business.dim_hooks import HookInvocationResult


class TestManagedAssetsNormalizeName(TestCase):

    def test_lowercases(self):
        self.assertEqual('web-server', managed_assets_normalize_name('WEB-SERVER'))

    def test_strips_leading_trailing_whitespace(self):
        self.assertEqual('host', managed_assets_normalize_name('  host  '))

    def test_collapses_internal_whitespace(self):
        self.assertEqual('web server 01', managed_assets_normalize_name('web  server   01'))

    def test_tabs_and_newlines_collapsed(self):
        self.assertEqual('web server', managed_assets_normalize_name('web\t\nserver'))

    def test_none_returns_empty_string(self):
        self.assertEqual('', managed_assets_normalize_name(None))

    def test_empty_string_returns_empty_string(self):
        self.assertEqual('', managed_assets_normalize_name(''))

    def test_whitespace_only_returns_empty_string(self):
        self.assertEqual('', managed_assets_normalize_name('   '))

    def test_already_normalized_unchanged(self):
        self.assertEqual('server01', managed_assets_normalize_name('server01'))

    def test_mixed_case_lowercased(self):
        self.assertEqual('sqlserver', managed_assets_normalize_name('SQLServer'))


class TestHookInvocationResult(TestCase):

    def test_queued_stored(self):
        result = HookInvocationResult(queued=3, logs=[])
        self.assertEqual(3, result.queued)

    def test_logs_stored(self):
        logs = ['msg1', 'msg2']
        result = HookInvocationResult(queued=0, logs=logs)
        self.assertEqual(logs, result.logs)

    def test_zero_queued_is_valid(self):
        result = HookInvocationResult(queued=0, logs=[])
        self.assertEqual(0, result.queued)

    def test_logs_can_be_empty(self):
        result = HookInvocationResult(queued=1, logs=[])
        self.assertEqual([], result.logs)
