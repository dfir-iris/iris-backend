#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _sentry_set_tag in iris_engine/observability/request_id.py.

_sentry_set_tag is a best-effort helper that silently swallows errors.
Tested without Sentry installed (the import will fail → the function
still silently does nothing — that's the intended behaviour).
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.iris_engine.observability.request_id import _sentry_set_tag


class TestSentrySetTag(TestCase):

    def test_no_sentry_does_not_raise(self):
        # Without sentry_sdk installed the ImportError is swallowed
        with patch.dict('sys.modules', {'sentry_sdk': None}):
            _sentry_set_tag('key', 'value')  # must not raise

    def test_sentry_set_tag_called_when_available(self):
        mock_sdk = MagicMock()
        with patch.dict('sys.modules', {'sentry_sdk': mock_sdk}):
            _sentry_set_tag('request_id', 'abc123')
        mock_sdk.set_tag.assert_called_once_with('request_id', 'abc123')

    def test_sentry_exception_suppressed(self):
        mock_sdk = MagicMock()
        mock_sdk.set_tag.side_effect = RuntimeError('Sentry is broken')
        with patch.dict('sys.modules', {'sentry_sdk': mock_sdk}):
            _sentry_set_tag('key', 'value')  # must not raise
