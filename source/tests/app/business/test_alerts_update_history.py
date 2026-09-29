#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the write ordering of business/alerts.py::alerts_update.

The modification_history entry has to be written *and committed* with
the field change itself, before the module hooks and before
track_activity (which commits on its own). Otherwise a status-only PUT
can persist the new status while losing the history line that explains
it — see the ordering comment in alerts_update.
"""

from unittest import TestCase
from unittest.mock import patch

from app.business.alerts import alerts_update


class _FakeAlert:

    def __init__(self, alert_id=42, status_id=2, resolution_id=None):
        self.alert_id = alert_id
        self.alert_status_id = status_id
        self.alert_resolution_status_id = resolution_id
        self.resolved_at = None
        self.date_update = None


class _Recorder:
    """Collects the sequence of side effects alerts_update performs."""

    def __init__(self):
        self.events = []

    def history(self, obj, entry):
        self.events.append(('history', entry))

    def commit(self):
        self.events.append(('commit', None))

    def hook(self, hook_name, data, **kwargs):
        self.events.append(('hook', hook_name))
        return data

    def activity(self, message, **kwargs):
        self.events.append(('activity', message))

    def enqueue(self, alert_identifier):
        self.events.append(('enqueue', alert_identifier))

    def names(self):
        return [name for name, _ in self.events]


class _AlertsUpdateTestCase(TestCase):

    def _run(self, pristine, updated, activity_data, hook=None):
        recorder = _Recorder()
        with patch('app.business.alerts.add_obj_history_entry', recorder.history), \
                patch('app.business.alerts.track_activity', recorder.activity), \
                patch('app.business.alerts.call_modules_hook', hook or recorder.hook), \
                patch('app.business.alerts._enqueue_rule_evaluation', recorder.enqueue), \
                patch('app.business.alerts.db') as database:
            database.session.commit = recorder.commit
            result = alerts_update(pristine, updated, activity_data)
        return recorder, result


class TestAlertsUpdateHistoryOrdering(_AlertsUpdateTestCase):

    def test_history_entry_is_committed_before_the_hooks_run(self):
        pristine = _FakeAlert(status_id=2)
        updated = _FakeAlert(status_id=4)
        recorder, _ = self._run(pristine, updated, ['"alert_status_id" from "2" to "4"'])

        names = recorder.names()
        self.assertEqual(['history', 'commit'], names[:2])
        self.assertLess(names.index('commit'), names.index('hook'))

    def test_history_entry_is_committed_before_track_activity(self):
        pristine = _FakeAlert(status_id=2)
        updated = _FakeAlert(status_id=4)
        recorder, _ = self._run(pristine, updated, ['"alert_status_id" from "2" to "4"'])

        names = recorder.names()
        self.assertLess(names.index('commit'), names.index('activity'))

    def test_history_entry_survives_a_raising_hook(self):
        def _raising_hook(hook_name, data, **kwargs):
            raise Exception(f'Hook name {hook_name} not found')

        recorder = _Recorder()
        with patch('app.business.alerts.add_obj_history_entry', recorder.history), \
                patch('app.business.alerts.track_activity', recorder.activity), \
                patch('app.business.alerts.call_modules_hook', _raising_hook), \
                patch('app.business.alerts._enqueue_rule_evaluation', recorder.enqueue), \
                patch('app.business.alerts.db') as database:
            database.session.commit = recorder.commit
            with self.assertRaises(Exception):
                alerts_update(_FakeAlert(status_id=2), _FakeAlert(status_id=4),
                              ['"alert_status_id" from "2" to "4"'])

        self.assertEqual(['history', 'commit'], recorder.names())

    def test_history_entry_carries_the_activity_data(self):
        pristine = _FakeAlert(status_id=2)
        updated = _FakeAlert(status_id=4)
        recorder, _ = self._run(pristine, updated, ['"alert_status_id" from "2" to "4"'])

        entries = [entry for name, entry in recorder.events if name == 'history']
        self.assertEqual(['updated alert: "alert_status_id" from "2" to "4"'], entries)

    def test_history_entry_without_activity_data_is_generic(self):
        recorder, _ = self._run(_FakeAlert(), _FakeAlert(), [])

        entries = [entry for name, entry in recorder.events if name == 'history']
        self.assertEqual(['updated alert'], entries)

    def test_activity_message_carries_the_alert_identifier(self):
        recorder, _ = self._run(_FakeAlert(alert_id=7), _FakeAlert(alert_id=7), [])

        messages = [entry for name, entry in recorder.events if name == 'activity']
        self.assertEqual(['updated alert #7'], messages)


class TestAlertsUpdateHooks(_AlertsUpdateTestCase):

    def test_status_change_triggers_the_status_hook(self):
        recorder, _ = self._run(_FakeAlert(status_id=2), _FakeAlert(status_id=4), [])

        hooks = [entry for name, entry in recorder.events if name == 'hook']
        self.assertIn('on_postload_alert_status_update', hooks)

    def test_unchanged_status_does_not_trigger_the_status_hook(self):
        recorder, _ = self._run(_FakeAlert(status_id=4), _FakeAlert(status_id=4), [])

        hooks = [entry for name, entry in recorder.events if name == 'hook']
        self.assertEqual(['on_postload_alert_update'], hooks)

    def test_resolution_change_triggers_the_resolution_hook(self):
        pristine = _FakeAlert(resolution_id=None)
        updated = _FakeAlert(resolution_id=3)
        recorder, _ = self._run(pristine, updated, [])

        hooks = [entry for name, entry in recorder.events if name == 'hook']
        self.assertIn('on_postload_alert_resolution_update', hooks)

    def test_rule_evaluation_is_enqueued_with_the_alert_identifier(self):
        recorder, _ = self._run(_FakeAlert(alert_id=7), _FakeAlert(alert_id=7), [])

        enqueued = [entry for name, entry in recorder.events if name == 'enqueue']
        self.assertEqual([7], enqueued)

    def test_a_module_rewriting_the_alert_is_returned_and_committed(self):
        replacement = _FakeAlert(alert_id=42, status_id=4)

        def _rewriting_hook(hook_name, data, **kwargs):
            return replacement

        recorder, result = self._run(_FakeAlert(status_id=2), _FakeAlert(status_id=4), [],
                                     hook=_rewriting_hook)

        self.assertIs(replacement, result)
        self.assertEqual(2, recorder.names().count('commit'))


class TestAlertsUpdateResolvedAt(_AlertsUpdateTestCase):

    def test_resolved_at_is_stamped_on_the_first_verdict(self):
        updated = _FakeAlert(resolution_id=3)
        self._run(_FakeAlert(resolution_id=None), updated, [])

        self.assertIsNotNone(updated.resolved_at)

    def test_resolved_at_is_cleared_when_the_verdict_is_reverted(self):
        updated = _FakeAlert(resolution_id=None)
        updated.resolved_at = 'some-timestamp'
        self._run(_FakeAlert(resolution_id=3), updated, [])

        self.assertIsNone(updated.resolved_at)

    def test_resolved_at_is_kept_when_the_verdict_is_edited(self):
        updated = _FakeAlert(resolution_id=5)
        updated.resolved_at = 'first-verdict-time'
        self._run(_FakeAlert(resolution_id=3), updated, [])

        self.assertEqual('first-verdict-time', updated.resolved_at)
