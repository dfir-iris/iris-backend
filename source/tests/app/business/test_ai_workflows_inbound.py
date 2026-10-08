#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Authentication of the public AI workflow endpoints: bearer token,
HMAC signature, single use, size cap, and the event logged for every
attempt on a known target. The DB helpers and the engine are patched."""

import datetime
import hashlib
import json
import time
import uuid
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app import app
from app.business.ai_workflows_inbound import AiWorkflowsInboundError
from app.business.ai_workflows_inbound import ai_workflows_inbound_callback
from app.business.ai_workflows_inbound import ai_workflows_inbound_parse
from app.business.ai_workflows_inbound import ai_workflows_inbound_sign
from app.business.ai_workflows_inbound import ai_workflows_inbound_trigger
from app.models.ai_workflows import INBOUND_CALLBACK
from app.models.ai_workflows import INBOUND_TRIGGER
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.ai_workflows import TRIGGER_WEBHOOK
from app.models.ai_workflows import WAIT_CALLBACK
from app.models.ai_workflows import WAIT_PENDING
from app.models.ai_workflows import WAIT_RESOLVED
from app.models.ai_workflows import WAIT_USER_INPUT

_BUSINESS = 'app.business.ai_workflows_inbound'
_TOKEN = 'callback-token-123'
_SECRET = 'signing-secret-456'
_IP = '203.0.113.9'


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _wait(**overrides):
    values = {
        'id': 5,
        'uuid': uuid.uuid4(),
        'run_id': 3,
        'run': SimpleNamespace(workflow_id=7),
        'kind': WAIT_CALLBACK,
        'token_hash': _hash(_TOKEN),
        'status': WAIT_PENDING,
        'expires_at': _now() + datetime.timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _workflow(**overrides):
    values = {
        'id': 7,
        'uuid': uuid.uuid4(),
        'owner_id': 42,
        'trigger_type': TRIGGER_WEBHOOK,
        'trigger_config': {},
        'is_active': True,
        'inbound_token_hash': _hash(_TOKEN),
        'inbound_signing_secret': f'enc:{_SECRET}',
        'max_runs_per_hour': 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _decrypt(value):
    return value[4:] if value and value.startswith('enc:') else None


def _bearer(token=_TOKEN):
    return {'Authorization': f'Bearer {token}'}


def _signed(body, key=_TOKEN, timestamp=None, token=_TOKEN):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    headers = _bearer(token)
    headers['X-IRIS-Timestamp'] = timestamp
    headers['X-IRIS-Signature'] = ai_workflows_inbound_sign(key, timestamp, body)
    return headers


class InboundTestCase(TestCase):
    """Shared harness (also used by the security tests)."""

    def setUp(self):
        self.events = []
        self.wait = _wait()
        self.workflow = _workflow()
        self.resume = MagicMock()
        self.run = SimpleNamespace(id=99, uuid=uuid.uuid4(), status='running')
        self.insert_run = MagicMock(return_value=self.run)
        self.skip_run = MagicMock()
        self.denial = MagicMock(return_value=None)
        self.count_runs = MagicMock(return_value=0)
        self.signature_seen = MagicMock(return_value=False)
        self.lock_workflow = MagicMock(side_effect=lambda _id: self.workflow)
        self.lock_wait = MagicMock(side_effect=lambda _id=None, **_kwargs: (SimpleNamespace(id=3), self.wait))
        self.commit = MagicMock()
        self.rollback = MagicMock()
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_add', side_effect=self.events.append),
            patch(f'{_BUSINESS}.ai_workflows_db_commit', self.commit),
            patch(f'{_BUSINESS}.ai_workflows_db_rollback', self.rollback),
            patch(f'{_BUSINESS}.ai_workflows_db_utcnow', side_effect=_now),
            patch(f'{_BUSINESS}.ai_workflows_db_get_wait_by_uuid',
                  side_effect=lambda u, **_kwargs: self.wait if self.wait and u == self.wait.uuid else None),
            patch(f'{_BUSINESS}.ai_workflows_db_get_by_uuid',
                  side_effect=lambda u: self.workflow if self.workflow and u == self.workflow.uuid else None),
            patch(f'{_BUSINESS}.ai_workflows_db_count_runs_since', self.count_runs),
            patch(f'{_BUSINESS}.ai_workflows_db_lock_workflow', self.lock_workflow),
            patch(f'{_BUSINESS}.ai_workflows_business_db_signature_seen', self.signature_seen),
            patch(f'{_BUSINESS}.ai_workflows_engine_hash_token', side_effect=_hash),
            patch(f'{_BUSINESS}.ai_workflows_engine_resume_wait', self.resume),
            patch(f'{_BUSINESS}.ai_workflows_engine_lock_run_then_wait', self.lock_wait),
            patch(f'{_BUSINESS}.ai_workflows_engine_access_denial', self.denial),
            patch(f'{_BUSINESS}.ai_workflows_engine_insert_run', self.insert_run),
            patch(f'{_BUSINESS}.ai_workflows_engine_skip_run', self.skip_run),
            patch(f'{_BUSINESS}.decrypt_secret', side_effect=_decrypt),
            patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': True, 'AI_WORKFLOWS_MAX_INBOUND_BYTES': 1024}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _assert_logged(self, kind, status, reason_part=None):
        self.assertEqual(1, len(self.events))
        event = self.events[0]
        self.assertEqual(kind, event.kind)
        self.assertEqual(status, event.status)
        self.assertEqual(_IP, event.source_ip)
        if reason_part is not None:
            self.assertIn(reason_part, event.reason)
        return event

    def _callback_refused(self, headers, body=b'{}', wait_uuid=None, status=401):
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_callback(wait_uuid or str(self.wait.uuid), headers, body, _IP)
        self.assertEqual(status, context.exception.status)
        self.resume.assert_not_called()
        return context.exception

    def _trigger_refused(self, headers, body=b'{}', status=401):
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_trigger(str(self.workflow.uuid), headers, body, _IP)
        self.assertEqual(status, context.exception.status)
        self.insert_run.assert_not_called()
        return context.exception


class TestsCallback(InboundTestCase):

    def test_valid_token_should_resume_the_wait_with_the_parsed_body(self):
        body = json.dumps({'verdict': 'malicious'}).encode()
        result = ai_workflows_inbound_callback(str(self.wait.uuid), _bearer(), body, _IP)
        self.assertEqual({'status': 'accepted'}, result)
        self.resume.assert_called_once_with(self.wait, {'verdict': 'malicious'}, source_ip=_IP)
        event = self._assert_logged(INBOUND_CALLBACK, 'accepted')
        self.assertEqual(hashlib.sha256(body).hexdigest(), event.payload_sha256)
        self.assertEqual(len(body), event.payload_bytes)
        self.assertEqual(self.wait.id, event.wait_id)
        self.assertEqual(7, event.workflow_id)

    def test_bad_token_should_be_rejected_and_logged(self):
        self._callback_refused(_bearer('wrong'))
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'bad token')

    def test_missing_token_should_be_rejected_and_logged(self):
        self._callback_refused({})
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'missing bearer token')

    def test_unknown_wait_should_be_rejected_without_an_event_row(self):
        self._callback_refused(_bearer(), wait_uuid=str(uuid.uuid4()))
        self.assertEqual([], self.events)

    def test_malformed_wait_uuid_should_be_rejected_without_an_event_row(self):
        self._callback_refused(_bearer(), wait_uuid='not-a-uuid')
        self.assertEqual([], self.events)

    def test_replay_on_a_resolved_wait_should_be_rejected_and_logged(self):
        self.wait.status = WAIT_RESOLVED
        self._callback_refused(_bearer())
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'resolved')

    def test_expired_wait_should_be_rejected_and_logged(self):
        self.wait.expires_at = _now() - datetime.timedelta(seconds=1)
        self._callback_refused(_bearer())
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'expired')

    def test_user_input_wait_should_not_be_resolvable_by_callback(self):
        self.wait.kind = WAIT_USER_INPUT
        self._callback_refused(_bearer())
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'not a callback')

    def test_valid_signature_should_be_accepted(self):
        body = b'{"a": 1}'
        ai_workflows_inbound_callback(str(self.wait.uuid), _signed(body), body, _IP)
        self.resume.assert_called_once()
        self._assert_logged(INBOUND_CALLBACK, 'accepted')

    def test_bad_signature_should_be_rejected_and_logged(self):
        body = b'{"a": 1}'
        headers = _signed(b'{"a": 2}')
        self._callback_refused(headers, body=body)
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'signature mismatch')

    def test_signature_with_another_key_should_be_rejected(self):
        body = b'{}'
        self._callback_refused(_signed(body, key='other'), body=body)
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'signature mismatch')

    def test_expired_signature_should_be_rejected_and_logged(self):
        body = b'{}'
        headers = _signed(body, timestamp=int(time.time()) - 301)
        self._callback_refused(headers, body=body)
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'tolerance')

    def test_incomplete_signature_should_be_rejected(self):
        headers = _bearer()
        headers['X-IRIS-Timestamp'] = str(int(time.time()))
        self._callback_refused(headers)
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'incomplete')

    def test_body_over_the_cap_should_return_413_and_be_logged(self):
        body = b'x' * 1025
        self._callback_refused(_bearer(), body=body, status=413)
        event = self._assert_logged(INBOUND_CALLBACK, 'rejected', 'payload over')
        self.assertEqual(1025, event.payload_bytes)
        self.assertEqual(self.wait.id, event.wait_id)

    def test_declared_length_over_the_cap_should_return_413(self):
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_callback(str(self.wait.uuid), _bearer(), b'{}', _IP, content_length=10_000)
        self.assertEqual(413, context.exception.status)

    def test_disabled_feature_should_reject_callbacks(self):
        with patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': False}):
            self._callback_refused(_bearer())
        self._assert_logged(INBOUND_CALLBACK, 'rejected', 'disabled')

    def test_engine_failure_should_be_logged_as_error(self):
        self.resume.side_effect = RuntimeError('boom')
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_callback(str(self.wait.uuid), _bearer(), b'{}', _IP)
        self.assertEqual(500, context.exception.status)
        self._assert_logged(INBOUND_CALLBACK, 'error', 'resume failed')


class TestsParse(TestCase):

    def test_json_body_should_be_parsed(self):
        self.assertEqual({'a': [1]}, ai_workflows_inbound_parse(b'{"a": [1]}'))

    def test_non_json_body_should_fall_back_to_text(self):
        self.assertEqual('plain text', ai_workflows_inbound_parse(b'plain text'))

    def test_empty_body_should_be_none(self):
        self.assertIsNone(ai_workflows_inbound_parse(b''))


class TestsTrigger(InboundTestCase):

    def test_valid_token_should_start_a_run_as_the_owner(self):
        result = ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"x": 1}', _IP)
        self.assertEqual({'run_uuid': str(self.run.uuid), 'status': 'accepted'}, result)
        args, kwargs = self.insert_run.call_args
        self.assertEqual((self.workflow, TRIGGER_WEBHOOK), args)
        self.assertEqual({'x': 1}, kwargs['payload'])
        self.assertIsNone(kwargs['entity_type'])
        self.assertIsNone(kwargs['denial'])
        self.assertEqual(42, self.denial.call_args.args[1])
        event = self._assert_logged(INBOUND_TRIGGER, 'accepted')
        self.assertEqual(99, event.run_id)

    def test_entity_should_be_extracted_from_the_payload(self):
        self.workflow.trigger_config = {'entity_type': 'alert', 'entity_id_path': 'alert.id'}
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"alert": {"id": "12"}}', _IP)
        kwargs = self.insert_run.call_args.kwargs
        self.assertEqual(('alert', 12), (kwargs['entity_type'], kwargs['entity_id']))
        self.assertEqual((self.workflow, 42, 'alert', 12), self.denial.call_args.args)

    def test_missing_entity_id_should_be_refused(self):
        self.workflow.trigger_config = {'entity_type': 'alert', 'entity_id_path': 'id'}
        self._trigger_refused(_bearer(), body=b'{}', status=400)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'no entity id')

    def test_hourly_run_limit_should_give_429_and_count_a_skip(self):
        self.count_runs.return_value = 60
        self._trigger_refused(_bearer(), status=429)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'rate limit')
        self.assertEqual(7, self.count_runs.call_args.args[0])
        self.skip_run.assert_called_once_with(self.workflow, TRIGGER_WEBHOOK, 'webhook rate limit')

    def test_under_the_hourly_run_limit_should_start_a_run(self):
        self.count_runs.return_value = 59
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{}', _IP)
        self.insert_run.assert_called_once()

    def test_bad_token_should_not_count_runs(self):
        self._trigger_refused(_bearer('nope'))
        self.count_runs.assert_not_called()

    def test_bad_token_should_be_rejected_and_logged(self):
        self._trigger_refused(_bearer('nope'))
        event = self._assert_logged(INBOUND_TRIGGER, 'rejected', 'bad token')
        self.assertEqual(7, event.workflow_id)

    def test_workflow_without_token_should_reject_everything(self):
        self.workflow.inbound_token_hash = None
        self._trigger_refused(_bearer())
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'bad token')

    def test_non_webhook_workflow_should_be_rejected(self):
        self.workflow.trigger_type = TRIGGER_MANUAL
        self._trigger_refused(_bearer())
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'not webhook')

    def test_inactive_workflow_should_be_rejected(self):
        self.workflow.is_active = False
        self._trigger_refused(_bearer())
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'inactive')

    def test_required_signature_missing_should_be_rejected(self):
        self.workflow.trigger_config = {'require_signature': True}
        self._trigger_refused(_bearer())
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'signature required')

    def test_required_signature_with_the_signing_secret_should_be_accepted(self):
        self.workflow.trigger_config = {'require_signature': True}
        body = b'{"k": "v"}'
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _signed(body, key=_SECRET), body, _IP)
        self.insert_run.assert_called_once()

    def test_body_over_the_cap_should_return_413_and_be_logged(self):
        self._trigger_refused(_bearer(), body=b'y' * 2000, status=413)
        event = self._assert_logged(INBOUND_TRIGGER, 'rejected', 'payload over')
        self.assertEqual(7, event.workflow_id)

    def test_unknown_workflow_should_be_rejected_without_an_event_row(self):
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_trigger(str(uuid.uuid4()), _bearer(), b'{}', _IP)
        self.assertEqual(401, context.exception.status)
        self.assertEqual([], self.events)
