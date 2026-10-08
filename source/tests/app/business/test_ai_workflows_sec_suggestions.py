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

"""Security fixes of suggestion resolution: write tools only, arguments
pinned to the suggestion's objects, the result and answer kept from
everyone but the resolver, the feature switch, the API-key scope and
the visibility of suggestions without an entity."""

from unittest.mock import patch

from app import app
from app.business.ai_suggestions import ai_suggestions_accept
from app.business.ai_suggestions import ai_suggestions_answer
from app.business.ai_suggestions import ai_suggestions_check_action
from app.business.ai_suggestions import ai_suggestions_dismiss
from app.business.ai_suggestions import ai_suggestions_get
from app.business.ai_suggestions import ai_suggestions_list
from app.business.ai_workflows import AiWorkflowsDisabledError
from app.business.ai_workflows import ai_workflows_suggestion_public
from app.models.ai_workflows import SUGGESTION_ACCEPTED
from app.models.ai_workflows import SUGGESTION_INFO_REQUEST
from app.models.ai_workflows import SUGGESTION_OPEN
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from tests.app.business.test_ai_suggestions import _ANALYST
from tests.app.business.test_ai_suggestions import _SuggestionsTestCase
from tests.app.business.test_ai_suggestions import _suggestion

_OTHER = 13


def _action(tool='update_alert', **arguments):
    return {'tool': tool, 'arguments': arguments}


class TestsCheckAction(_SuggestionsTestCase):

    def _refused(self, suggestion, action):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_check_action(suggestion, action)
        return context.exception.get_data()

    def test_own_entity_should_be_allowed(self):
        suggestion = _suggestion()
        self.assertEqual(('update_alert', {'alert_id': 4}), ai_suggestions_check_action(suggestion, _action(alert_id=4)))

    def test_other_alert_should_be_refused(self):
        errors = self._refused(_suggestion(), _action(alert_id=5))
        self.assertIn('alert_id', errors)

    def test_string_ids_should_be_compared_as_ids(self):
        ai_suggestions_check_action(_suggestion(), _action(alert_identifier='4'))
        self._refused(_suggestion(), _action(alert_identifier='5'))

    def test_nested_and_listed_ids_should_be_checked(self):
        errors = self._refused(_suggestion(), _action(payload={'items': [{'case_id': 9}]}))
        self.assertIn('payload.items.0.case_id', errors)
        self._refused(_suggestion(case_id=2), _action(case_ids=[2, 3]))

    def test_related_refs_and_columns_should_be_allowed(self):
        suggestion = _suggestion(case_id=2, related_refs=[{'type': 'case', 'id': 3}, {'type': 'war_room', 'id': '8'}])
        ai_suggestions_check_action(suggestion, _action(case_ids=[2, 3], war_room_id=8, alert_id=4))

    def test_customer_ids_should_be_pinned(self):
        self._refused(_suggestion(), _action(customer_id=1))
        ai_suggestions_check_action(_suggestion(customer_id=1), _action(customer_id=1))

    def test_too_deep_arguments_should_be_refused(self):
        arguments = {}
        current = arguments
        for _ in range(12):
            current['x'] = {}
            current = current['x']
        self._refused(_suggestion(), {'tool': 'update_alert', 'arguments': arguments})

    def test_non_write_tool_should_be_refused(self):
        self.classification.return_value = None
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_check_action(_suggestion(), _action(alert_id=4))

    def test_accept_should_refuse_an_action_outside_the_suggestion_without_claiming(self):
        suggestion = self._add(_suggestion(proposed_action=_action(alert_id=99)))
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_accept(1, _ANALYST)
        self.execute.assert_not_called()
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)
        self.commits.assert_not_called()


class TestsResolverOnlyData(_SuggestionsTestCase):

    def test_wait_payload_should_not_carry_the_result(self):
        wait = self._add_wait()
        self._add(_suggestion(wait_id=wait.id))
        ai_suggestions_accept(1, _ANALYST)
        payload = self.resume.call_args.args[1]
        self.assertNotIn('result', payload)
        self.assertEqual(_ANALYST, payload['accepted_by']['id'])
        self.lock_wait.assert_called_once_with(wait.id)

    def test_resolver_should_see_the_result(self):
        data = ai_suggestions_accept(self._add(_suggestion()).id, _ANALYST)
        self.assertEqual({'alert_id': 4}, data['result'])
        self.assertFalse(data['resolution_hidden'])

    def test_others_should_see_who_resolved_but_not_the_result(self):
        self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST)
        data = ai_suggestions_get(1, _OTHER)
        self.assertIsNone(data['result'])
        self.assertTrue(data['resolution_hidden'])
        self.assertEqual(_ANALYST, data['resolved_by_id'])
        with patch('app.business.ai_suggestions.ai_workflows_db_list_suggestions',
                   return_value=list(self.suggestions.values())):
            listed = ai_suggestions_list(_OTHER, status='all')
        self.assertIsNone(listed[0]['result'])

    def test_answer_should_be_hidden_from_others(self):
        suggestion = self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST, wait_id=self._add_wait().id,
                                           proposed_action=None))
        ai_suggestions_answer(1, _ANALYST, {'reason': 'r', 'confirmed': True})
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)
        self.assertIsNotNone(ai_workflows_suggestion_public(suggestion, _ANALYST)['answer'])
        self.assertIsNone(ai_workflows_suggestion_public(suggestion, _OTHER)['answer'])

    def test_socket_push_should_be_redacted(self):
        self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST)
        pushed = self.emit.call_args.args[0]
        self.assertIsNone(pushed.result)
        self.assertEqual(1, pushed.id)

    def test_hook_should_get_the_full_suggestion(self):
        self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST)
        self.assertEqual({'alert_id': 4}, self.hook.call_args.kwargs['data']['result'])


class TestsFeatureSwitch(_SuggestionsTestCase):

    def test_resolution_should_be_refused_when_disabled(self):
        self._add(_suggestion())
        with patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': False}):
            for call in (lambda: ai_suggestions_accept(1, _ANALYST), lambda: ai_suggestions_dismiss(1, _ANALYST),
                         lambda: ai_suggestions_answer(1, _ANALYST, {})):
                with self.assertRaises(AiWorkflowsDisabledError):
                    call()
        self.execute.assert_not_called()

    def test_reads_should_still_work_when_disabled(self):
        self._add(_suggestion())
        with patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': False}):
            self.assertEqual(1, ai_suggestions_get(1, _ANALYST)['id'])


class TestsScopeAndVisibility(_SuggestionsTestCase):

    def test_key_without_the_alert_scope_should_not_find_an_alert_suggestion(self):
        self._add(_suggestion())
        scope = Permissions.war_rooms_read.value
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_accept(1, _ANALYST, scope_mask=scope)
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_get(1, _ANALYST, scope_mask=scope)
        self.execute.assert_not_called()

    def test_scope_should_cover_related_refs(self):
        self._add(_suggestion(entity_type='case', entity_id=1, related_refs=[{'type': 'war_room', 'id': 1}]))
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_get(1, _ANALYST, scope_mask=0)

    def test_suggestion_without_audience_or_entity_should_be_admin_only(self):
        self._add(_suggestion(entity_type=None, entity_id=None))
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_get(1, _ANALYST)
        self.is_admin = True
        self.assertEqual(1, ai_suggestions_get(1, _ANALYST)['id'])

    def test_suggestion_with_an_audience_should_follow_the_engine_check(self):
        self._add(_suggestion(entity_type=None, entity_id=None, audience_user_ids=[_ANALYST]))
        self.assertEqual(1, ai_suggestions_get(1, _ANALYST)['id'])
