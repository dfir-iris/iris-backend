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

"""Suggestions of AI workflow runs: the per-run limit, related refs
checked against IRIS, links that cannot pass for IRIS links, and tool
errors masked before they are logged or recorded. The persistence
helpers are patched; nothing touches the database."""

import contextlib
import logging
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.suggestions import AiWorkflowSuggestionLimitError
from app.iris_engine.ai_workflows.suggestions import _publish_created
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_create
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_neutralise_links
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_execute

_SUGGESTIONS = 'app.iris_engine.ai_workflows.suggestions'
_TOOLS = 'app.iris_engine.ai_workflows.tools'

_TITLES = {('case', 1): 'Phishing wave', ('alert', 2): 'Suspicious login', ('case', 3): 'Other customer'}


def _run(**fields):
    values = {'id': 5, 'uuid': 'r', 'run_as_user_id': 3, 'entity_type': 'case', 'entity_id': 1, 'sub_entity': None,
              'workflow_id': 1, 'customer_id': 1, 'is_dry_run': True, 'definition_snapshot': {},
              'triggered_by_user_id': 9}
    values.update(fields)
    return SimpleNamespace(**values)


class TestsSuggestionCreation(TestCase):

    def setUp(self):
        self.count = 0
        self.added = []
        self.published = []
        patches = {
            f'{_SUGGESTIONS}._publish_created': lambda _run, suggestion: self.published.append(suggestion),
            f'{_SUGGESTIONS}.ai_workflows_db_add': self.added.append,
            f'{_SUGGESTIONS}.ai_workflows_db_commit': lambda: None,
            f'{_SUGGESTIONS}.ai_workflows_db_entity_owner_ids': lambda _type, _id: [],
            f'{_SUGGESTIONS}.ai_workflows_db_entity_title': lambda t, i: _TITLES.get((t, i)),
            f'{_SUGGESTIONS}.ai_workflows_entities_user_can_access': lambda _u, t, i: (t, i) != ('case', 3),
            f'{_SUGGESTIONS}.ai_workflows_runtime_db_count_run_suggestions': self._count,
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _count(self, run_id, exclude_kinds=()):
        self.excluded = exclude_kinds
        return self.count

    def test_sixth_suggestion_should_be_refused(self):
        self.count = 4
        ai_workflows_suggestions_create(_run(), None, title='Fifth')
        self.count = 5
        with self.assertRaises(AiWorkflowSuggestionLimitError):
            ai_workflows_suggestions_create(_run(), None, title='Sixth')
        self.assertEqual(1, len(self.added))
        self.assertEqual(('info_request',), self.excluded)

    def test_questions_to_analysts_should_not_count(self):
        self.count = 50
        ai_workflows_suggestions_create(_run(), None, kind='info_request', title='Is it real?')
        self.assertEqual(1, len(self.added))

    def test_related_refs_should_be_verified_and_retitled(self):
        refs = [{'type': 'case', 'id': 1, 'title': 'Click here: https://evil.example'},
                {'type': 'alert', 'id': 2},
                {'type': 'case', 'id': 1},
                {'type': 'case', 'id': 3, 'title': 'not visible'},
                {'type': 'case', 'id': 999, 'title': 'missing'}]
        suggestion = ai_workflows_suggestions_create(_run(), None, title='Related', related_refs=refs)
        self.assertEqual([{'type': 'case', 'id': 1, 'title': 'Phishing wave'},
                          {'type': 'alert', 'id': 2, 'title': 'Suspicious login'}], suggestion.related_refs)

    def test_refs_should_be_capped(self):
        refs = [{'type': 'alert', 'id': i} for i in range(1, 40)]
        with patch(f'{_SUGGESTIONS}.ai_workflows_db_entity_title', lambda _t, i: f'#{i}'):
            suggestion = ai_workflows_suggestions_create(_run(), None, title='Many', related_refs=refs)
        self.assertEqual(20, len(suggestion.related_refs))

    def test_title_and_body_links_should_be_neutralised(self):
        suggestion = ai_workflows_suggestions_create(_run(), None, title='[Open case](https://evil.example/x)',
                                                     body='See ![img](http://evil.example/p.png)')
        self.assertEqual('Open case (https://evil.example/x)', suggestion.title)
        self.assertEqual('See img (http://evil.example/p.png)', suggestion.body)

    def test_dry_run_suggestion_should_go_to_whoever_started_it(self):
        suggestion = ai_workflows_suggestions_create(_run(), None, title='Would close')
        self.assertEqual('dry_run', suggestion.status)
        self.assertEqual([9], suggestion.audience_user_ids)
        self.assertEqual([suggestion], self.published)

    def test_suggestion_should_go_to_the_run_as_user(self):
        suggestion = ai_workflows_suggestions_create(_run(is_dry_run=False), None, title='Close')
        self.assertEqual('open', suggestion.status)
        self.assertEqual([3], suggestion.audience_user_ids)
        self.assertEqual([suggestion], self.published)


class TestsSuggestionPublication(TestCase):

    def _publish(self, status):
        suggestion = SimpleNamespace(id=42, title='Close the alert', body=None, status=status,
                                     audience_user_ids=[9], case_id=None)
        with patch('app.iris_engine.notifications.service.notify_many') as notify, \
                patch(f'{_SUGGESTIONS}.ai_workflows_suggestions_emit') as emit, \
                patch(f'{_SUGGESTIONS}.ai_workflows_suggestions_serialize', return_value={}), \
                patch(f'{_SUGGESTIONS}.ai_workflows_identity_chain', lambda _run: contextlib.nullcontext()), \
                patch('app.iris_engine.module_handler.module_handler.call_modules_hook') as hook:
            _publish_created(_run(), suggestion)
        return notify, emit, hook

    def test_notification_should_link_to_the_inbox(self):
        notify, emit, hook = self._publish('open')
        self.assertEqual([9], notify.call_args.args[0])
        self.assertEqual('/suggestions?id=42', notify.call_args.kwargs['link'])
        self.assertEqual('created', emit.call_args.args[1])
        hook.assert_called_once()

    def test_dry_run_should_be_notified_without_the_hook(self):
        notify, emit, hook = self._publish('dry_run')
        self.assertTrue(notify.call_args.args[2].startswith('Suggestion (dry run): '))
        self.assertEqual('/suggestions?id=42', notify.call_args.kwargs['link'])
        emit.assert_called_once()
        hook.assert_not_called()


class TestsNeutraliseLinks(TestCase):

    def test_external_links_should_show_their_url(self):
        cases = {
            '[doc](https://evil.example/x?q=1)': 'doc (https://evil.example/x?q=1)',
            '[b](//evil.example/x)': 'b (//evil.example/x)',
            '[j](javascript:alert(1))': 'j (javascript:alert(1))',
            '[i ![x](https://a/i.png)](https://b/)': 'i x (https://a/i.png) (https://b/)',
            '[1]: https://evil/x': '1: (https://evil/x)',
            '[w](\\\\evil\\share)': 'w (\\\\evil\\share)',
        }
        for text, expected in cases.items():
            self.assertEqual(expected, ai_workflows_suggestions_neutralise_links(text), text)

    def test_relative_links_should_be_kept(self):
        for text in ('[case](/case/3)', '[top](#top)', '[q](?cid=1)', 'plain text', ''):
            self.assertEqual(text, ai_workflows_suggestions_neutralise_links(text), text)

    def test_none_should_stay_none(self):
        self.assertIsNone(ai_workflows_suggestions_neutralise_links(None))


class TestsToolErrorMasking(TestCase):

    def test_tool_error_should_be_masked_in_logs_and_records(self):
        recorded = []

        def _boom(_tool, _arguments):
            raise RuntimeError('upstream said: token sk-secret-value rejected')

        def _record(tool_name, arguments, **kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(id=1)

        patches = [
            patch('app.blueprints.rest.v2.mcp.dispatch.dispatch_tool_call', _boom),
            patch(f'{_TOOLS}.ai_workflows_identity', lambda _u, _r=None: contextlib.nullcontext()),
            patch(f'{_TOOLS}.ai_workflows_db_recover_session', lambda: None),
            patch(f'{_TOOLS}.ai_workflows_tools_record', _record),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.assertLogs(_TOOLS, level=logging.ERROR) as logs:
            result = ai_workflows_tools_execute(3, 'iris_cases_get', {'case_identifier': 1},
                                                execution_mode='auto_read',
                                                mask=lambda v: v.replace('sk-secret-value', '***')
                                                if isinstance(v, str) else v)
        self.assertFalse(result['ok'])
        self.assertNotIn('sk-secret-value', result['error'])
        self.assertNotIn('sk-secret-value', recorded[0]['error'])
        self.assertNotIn('sk-secret-value', '\n'.join(logs.output))
        self.assertNotIn('Traceback', '\n'.join(logs.output))
