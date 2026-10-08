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

"""Security fixes of the public AI workflow endpoints: separate signing
secret over the raw timestamp, replay protection, no event row for
unknown targets, uniform 202, lock order, strict JSON, stored payload
cap."""

import hashlib
import hmac
import json
import time
import uuid
from unittest import TestCase

from app.business.ai_workflows_inbound import AiWorkflowsInboundError
from app.business.ai_workflows_inbound import ai_workflows_inbound_callback
from app.business.ai_workflows_inbound import ai_workflows_inbound_parse
from app.business.ai_workflows_inbound import ai_workflows_inbound_sign
from app.business.ai_workflows_inbound import ai_workflows_inbound_trigger
from app.models.ai_workflows import INBOUND_TRIGGER
from app.models.ai_workflows import WAIT_RESOLVED
from tests.app.business.test_ai_workflows_inbound import InboundTestCase
from tests.app.business.test_ai_workflows_inbound import _IP
from tests.app.business.test_ai_workflows_inbound import _SECRET
from tests.app.business.test_ai_workflows_inbound import _TOKEN
from tests.app.business.test_ai_workflows_inbound import _bearer
from tests.app.business.test_ai_workflows_inbound import _signed


class TestsSigningSecret(InboundTestCase):

    def test_signature_should_cover_the_raw_timestamp_string(self):
        body = b'{}'
        expected = hmac.new(b'k', b'0001700000000.' + body, hashlib.sha256).hexdigest()
        self.assertEqual(f'sha256={expected}', ai_workflows_inbound_sign('k', '0001700000000', body))

    def test_trigger_signed_with_the_bearer_token_should_be_rejected(self):
        body = b'{}'
        self._trigger_refused(_signed(body, key=_TOKEN), body=body)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'signature mismatch')

    def test_trigger_signed_with_the_signing_secret_should_be_accepted(self):
        body = b'{"a": 1}'
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _signed(body, key=_SECRET), body, _IP)
        self.insert_run.assert_called_once()

    def test_padded_timestamp_should_be_signed_as_sent(self):
        body = b'{}'
        timestamp = f'0{int(time.time())}'
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _signed(body, key=_SECRET, timestamp=timestamp),
                                     body, _IP)
        self.insert_run.assert_called_once()

    def test_reformatted_timestamp_should_not_verify(self):
        body = b'{}'
        timestamp = str(int(time.time()))
        headers = _signed(body, key=_SECRET, timestamp=timestamp)
        headers['X-IRIS-Timestamp'] = f'0{timestamp}'
        self._trigger_refused(headers, body=body)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'signature mismatch')

    def test_required_signature_without_a_signing_secret_should_be_rejected(self):
        self.workflow.inbound_signing_secret = None
        self.workflow.trigger_config = {'require_signature': True}
        body = b'{}'
        self._trigger_refused(_signed(body, key=_SECRET), body=body)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'no signing secret')

    def test_unsigned_trigger_should_pass_when_the_signature_is_optional(self):
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{}', _IP)
        self.insert_run.assert_called_once()
        self.signature_seen.assert_not_called()


class TestsReplay(InboundTestCase):

    def test_accepted_event_should_carry_the_signature_hash(self):
        body = b'{}'
        headers = _signed(body, key=_SECRET)
        ai_workflows_inbound_trigger(str(self.workflow.uuid), headers, body, _IP)
        event = self._assert_logged(INBOUND_TRIGGER, 'accepted')
        self.assertEqual(64, len(event.signature_sha256))
        self.assertEqual(event.signature_sha256, self.signature_seen.call_args.args[1])

    def test_seen_signature_should_be_refused_as_a_replay(self):
        self.signature_seen.return_value = True
        body = b'{}'
        self._trigger_refused(_signed(body, key=_SECRET), body=body)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'replay')

    def test_replay_check_should_run_under_the_workflow_lock(self):
        order = []
        self.lock_workflow.side_effect = lambda _id: order.append('lock') or self.workflow
        self.signature_seen.side_effect = lambda *_args: order.append('seen') or False
        self.insert_run.side_effect = lambda *_a, **_k: order.append('insert') or self.run
        body = b'{}'
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _signed(body, key=_SECRET), body, _IP)
        self.assertEqual(['lock', 'seen', 'insert'], order)

    def test_event_should_be_added_before_the_run_commits(self):
        added_before_insert = []
        self.insert_run.side_effect = lambda *_a, **_k: added_before_insert.append(len(self.events)) or self.run
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{}', _IP)
        self.assertEqual([1], added_before_insert)


class TestsUnknownTargets(InboundTestCase):

    def test_unknown_trigger_should_write_no_row(self):
        for identifier in (str(uuid.uuid4()), 'garbage', ''):
            with self.assertRaises(AiWorkflowsInboundError):
                ai_workflows_inbound_trigger(identifier, _bearer(), b'{}', _IP)
        self.assertEqual([], self.events)

    def test_unknown_callback_should_write_no_row(self):
        with self.assertRaises(AiWorkflowsInboundError):
            ai_workflows_inbound_callback(str(uuid.uuid4()), _bearer(), b'{}', _IP)
        self.assertEqual([], self.events)

    def test_known_workflow_rejection_should_still_be_logged(self):
        self._trigger_refused(_bearer('bad'))
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'bad token')


class TestsUniformStatus(InboundTestCase):

    def setUp(self):
        super().setUp()
        self.workflow.trigger_config = {'entity_type': 'case', 'entity_id_path': 'id'}

    def test_inaccessible_entity_should_answer_like_an_accessible_one(self):
        ok = ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"id": 1}', _IP)
        self.denial.return_value = 'owner cannot access case #2'
        refused = ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"id": 2}', _IP)
        self.assertEqual(set(ok), set(refused))
        self.assertEqual('accepted', refused['status'])
        self.assertEqual('owner cannot access case #2', self.insert_run.call_args.kwargs['denial'])

    def test_refused_run_reason_should_be_kept_on_the_owner_visible_event(self):
        self.denial.return_value = 'case #2 does not exist'
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"id": 2}', _IP)
        self.assertIn('does not exist', self.events[0].reason)

    def test_denial_should_be_computed_before_the_lock(self):
        order = []
        self.denial.side_effect = lambda *_a, **_k: order.append('denial')
        self.lock_workflow.side_effect = lambda _id: order.append('lock') or self.workflow
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"id": 1}', _IP)
        self.assertEqual(['denial', 'lock'], order)


class TestsLocking(InboundTestCase):

    def test_workflow_deactivated_while_waiting_for_the_lock_should_be_refused(self):
        def _lock(_id):
            self.workflow.is_active = False
            return self.workflow
        self.lock_workflow.side_effect = _lock
        self._trigger_refused(_bearer())
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'inactive')

    def test_rate_check_should_run_under_the_workflow_lock(self):
        order = []
        self.lock_workflow.side_effect = lambda _id: order.append('lock') or self.workflow
        self.count_runs.side_effect = lambda *_args: order.append('count') or 0
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{}', _IP)
        self.assertEqual(['lock', 'count'], order)

    def test_callback_should_lock_the_run_then_the_wait_and_recheck(self):
        def _lock(_id=None, **_kwargs):
            self.wait.status = WAIT_RESOLVED
            return None, self.wait
        self.lock_wait.side_effect = _lock
        self._callback_refused(_bearer())
        self.lock_wait.assert_called_once_with(self.wait.id)

    def test_callback_should_resume_the_wait_returned_under_the_lock(self):
        ai_workflows_inbound_callback(str(self.wait.uuid), _bearer(), b'{}', _IP)
        self.lock_wait.assert_called_once()
        self.resume.assert_called_once()


class TestsStoredPayload(InboundTestCase):

    def test_large_payload_should_be_stored_as_a_summary(self):
        import app.business.ai_workflows_inbound as inbound
        body = json.dumps({'blob': 'z' * 300}).encode()
        original = inbound._MAX_STORED_PAYLOAD_BYTES
        inbound._MAX_STORED_PAYLOAD_BYTES = 100
        try:
            ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), body, _IP)
        finally:
            inbound._MAX_STORED_PAYLOAD_BYTES = original
        stored = self.insert_run.call_args.kwargs['payload']
        self.assertTrue(stored['truncated'])
        self.assertEqual(len(body), stored['bytes'])
        self.assertEqual(hashlib.sha256(body).hexdigest(), stored['sha256'])

    def test_small_payload_should_be_stored_as_is(self):
        ai_workflows_inbound_trigger(str(self.workflow.uuid), _bearer(), b'{"a": 1}', _IP)
        self.assertEqual({'a': 1}, self.insert_run.call_args.kwargs['payload'])


class TestsStrictJson(TestCase):

    def _refused(self, body):
        with self.assertRaises(AiWorkflowsInboundError) as context:
            ai_workflows_inbound_parse(body)
        self.assertEqual(400, context.exception.status)

    def test_nan_and_infinity_should_be_refused(self):
        for body in (b'{"a": NaN}', b'[Infinity]', b'[-Infinity]', b'[1e999]'):
            self._refused(body)

    def test_out_of_range_integers_should_be_refused(self):
        self._refused(f'[{2 ** 63}]'.encode())
        self._refused(f'[{-2 ** 63 - 1}]'.encode())
        self._refused(b'[' + b'9' * 5000 + b']')

    def test_limits_should_be_accepted(self):
        self.assertEqual([2 ** 63 - 1, -2 ** 63], ai_workflows_inbound_parse(f'[{2 ** 63 - 1}, {-2 ** 63}]'.encode()))

    def test_depth_over_32_should_be_refused(self):
        self._refused(b'[' * 33 + b']' * 33)

    def test_depth_32_should_be_accepted(self):
        self.assertIsNotNone(ai_workflows_inbound_parse(b'[' * 32 + b']' * 32))

    def test_recursion_bomb_should_be_refused(self):
        self._refused(b'[' * 200_000 + b']' * 200_000)


class TestsStrictJsonTrigger(InboundTestCase):

    def test_trigger_with_unsafe_json_should_answer_400_and_start_nothing(self):
        self._trigger_refused(_bearer(), body=b'{"a": NaN}', status=400)
        self._assert_logged(INBOUND_TRIGGER, 'rejected', 'unsafe JSON')

    def test_entity_id_out_of_range_should_answer_400(self):
        self.workflow.trigger_config = {'entity_type': 'case', 'entity_id_path': 'id'}
        self._trigger_refused(_bearer(), body=b'{"id": "99999999999999999999999"}', status=400)
