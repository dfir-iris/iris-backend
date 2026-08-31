#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Regression test for _register_mail_beat_schedule worker-startup crash.

On_after_finalize fires before a Flask app context exists. The signal
handler must push one before touching the DB (via _current_interval_sec
→ get_imap_config → ServerSettings.query). Without the app_context()
guard the worker crashed with:
    RuntimeError: Working outside of application context.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestRegisterMailBeatSchedule(TestCase):

    def test_callable_without_app_context_does_not_raise(self):
        from app.iris_engine.mail.inbound import _register_mail_beat_schedule

        sender = MagicMock()
        sender.conf.beat_schedule = {}

        # Patch _current_interval_sec so no real DB query is needed,
        # and patch flask_app.app_context to verify it is entered.
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=None)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch('app.iris_engine.mail.inbound.flask_app') as mock_flask_app, \
             patch('app.iris_engine.mail.inbound._current_interval_sec', return_value=300):
            mock_flask_app.app_context.return_value = mock_ctx
            _register_mail_beat_schedule(sender=sender, signal=MagicMock())

        mock_flask_app.app_context.assert_called_once()

    def test_registers_beat_entry(self):
        from app.iris_engine.mail.inbound import _register_mail_beat_schedule, _BEAT_ENTRY

        sender = MagicMock()
        sender.conf.beat_schedule = {}

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=None)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch('app.iris_engine.mail.inbound.flask_app') as mock_flask_app, \
             patch('app.iris_engine.mail.inbound._current_interval_sec', return_value=300):
            mock_flask_app.app_context.return_value = mock_ctx
            _register_mail_beat_schedule(sender=sender, signal=MagicMock())

        self.assertIn(_BEAT_ENTRY, sender.conf.beat_schedule)
