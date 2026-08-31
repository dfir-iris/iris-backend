#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Regression test for the setup_periodic_update_checks signal handler.

Celery calls on_after_finalize receivers as receiver(signal=..., sender=<app>, **named).
The function must NOT declare 'self' as a required positional argument or the
worker crashes with:
    TypeError: setup_periodic_update_checks() missing 1 required positional argument: 'self'
"""

from unittest import TestCase
from unittest.mock import MagicMock


class TestSetupPeriodicUpdateChecks(TestCase):

    def test_callable_as_celery_signal_receiver(self):
        from app.iris_engine.updater.updater import setup_periodic_update_checks

        sender = MagicMock()
        signal = MagicMock()

        # Celery passes sender and signal as keyword arguments only.
        # This would raise TypeError before the fix.
        setup_periodic_update_checks(sender=sender, signal=signal)

        sender.add_periodic_task.assert_called_once()

    def test_callable_with_positional_sender_for_manual_invocation(self):
        from app.iris_engine.updater.updater import setup_periodic_update_checks

        sender = MagicMock()

        # The two manual call sites pass celery positionally: setup_periodic_update_checks(celery)
        setup_periodic_update_checks(sender)

        sender.add_periodic_task.assert_called_once()
