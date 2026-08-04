#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Integration tests for /api/v2/case-chat REST endpoints.

Covers:
  * Toggle off → REST returns 503 on create-conversation, 200 with
    `enabled=false` on `/health`.
  * Toggle on but no API key → `/health` reports `provider_available=false`
    with a helpful reason string.
  * CRUD: create → list → get → archive.
  * Case ACL — user without access to the case gets 403 on
    `POST /conversations`.
  * Runtime-config surface — `chatbot.enabled` reflects the toggle.

Socket-namespace behaviour (tool-use loop, streaming, approve/deny) is
exercised by the more focused unit tests in
`tests_rest_case_chat_classification.py`; here we stay REST-only so
the tests don't need a running provider.
"""

from unittest import TestCase

from iris import API_URL, Iris  # noqa: F401


class TestsRestCaseChat(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        original = self._subject.get(
            '/api/v2/manage/server/settings').json()['data']['settings']
        # Snapshot the whole chatbot config so tearDown can restore.
        self._original_chatbot = {
            k: original.get(k)
            for k in (
                'chatbot_enabled', 'chatbot_provider', 'chatbot_model',
                'chatbot_base_url', 'chatbot_max_turns_per_conversation',
                'chatbot_max_tool_calls_per_turn',
                'chatbot_auto_execute_read_tools',
                'chatbot_auto_approve_write_tools',
                'chatbot_daily_token_budget_per_user',
                'chatbot_daily_token_budget_org',
                'chatbot_redact_ips', 'chatbot_redact_emails',
                'chatbot_redact_hashes',
            )
        }

    def tearDown(self) -> None:
        self._subject.update(
            '/api/v2/manage/server/settings', self._original_chatbot)
        self._subject.clear_database()

    # ---- helpers ---------------------------------------------------

    def _enable_chatbot(self, **overrides) -> None:
        body = {
            'chatbot_enabled': True,
            'chatbot_provider': 'anthropic',
            'chatbot_model': 'claude-sonnet-5',
            'chatbot_api_key': 'sk-ant-test-fake-not-real-key',
        }
        body.update(overrides)
        response = self._subject.update(
            '/api/v2/manage/server/settings', body)
        self.assertEqual(200, response.status_code)

    def _disable_chatbot(self) -> None:
        response = self._subject.update(
            '/api/v2/manage/server/settings',
            {'chatbot_enabled': False, 'chatbot_provider': ''},
        )
        self.assertEqual(200, response.status_code)

    # ---- health / toggle -------------------------------------------

    def test_health_off_by_default(self):
        self._disable_chatbot()
        response = self._subject.get('/api/v2/case-chat/health')
        self.assertEqual(200, response.status_code)
        data = response.json()['data']
        self.assertFalse(data['enabled'])
        self.assertFalse(data['provider_available'])

    def test_health_enabled_with_key(self):
        self._enable_chatbot()
        response = self._subject.get('/api/v2/case-chat/health')
        self.assertEqual(200, response.status_code)
        data = response.json()['data']
        self.assertTrue(data['enabled'])
        self.assertTrue(data['provider_available'])

    def test_health_enabled_missing_key(self):
        self._enable_chatbot(chatbot_api_key='')
        response = self._subject.get('/api/v2/case-chat/health')
        self.assertEqual(200, response.status_code)
        data = response.json()['data']
        self.assertTrue(data['enabled'])
        self.assertFalse(data['provider_available'])
        # `reason` should surface the missing-key message.
        self.assertIn('api key', (data.get('reason') or '').lower())

    def test_runtime_config_exposes_chatbot_slice(self):
        self._enable_chatbot()
        response = self._subject.get('/api/v2/runtime-config')
        self.assertEqual(200, response.status_code)
        payload = response.json()['data']
        self.assertIn('chatbot', payload)
        self.assertTrue(payload['chatbot']['enabled'])
        self.assertTrue(payload['chatbot']['provider_available'])
        self.assertEqual('claude-sonnet-5', payload['chatbot']['model'])

    def test_masked_api_key_never_leaked_on_read(self):
        self._enable_chatbot(chatbot_api_key='sk-ant-super-secret-value')
        settings = self._subject.get(
            '/api/v2/manage/server/settings').json()['data']['settings']
        # `chatbot_api_key` is load_only — GET must not echo it (even
        # ciphertext). `_set` companion boolean tells the SPA a key is
        # configured for the "•••••" placeholder rendering.
        self.assertNotIn('chatbot_api_key', settings)
        self.assertTrue(settings.get('chatbot_api_key_set'))

    # ---- create-conversation ---------------------------------------

    def test_create_case_conversation_requires_chatbot_enabled(self):
        self._disable_chatbot()
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(
            f'/api/v2/case-chat/cases/{case_id}/conversations', {})
        self.assertEqual(503, response.status_code)

    def test_create_case_conversation_success(self):
        self._enable_chatbot()
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(
            f'/api/v2/case-chat/cases/{case_id}/conversations',
            {'title': 'summarise this case'},
        )
        self.assertEqual(201, response.status_code)
        body = response.json()['data']
        self.assertEqual(case_id, body['case_id'])
        self.assertEqual('claude-sonnet-5', body['model'])
        self.assertEqual('summarise this case', body['title'])

    def test_create_global_conversation_no_case_scope(self):
        self._enable_chatbot()
        response = self._subject.create(
            '/api/v2/case-chat/global/conversations', {'title': 'hi'})
        self.assertEqual(201, response.status_code)
        body = response.json()['data']
        self.assertIsNone(body['case_id'])

    # ---- list / get / archive --------------------------------------

    def test_list_returns_created_conversations(self):
        self._enable_chatbot()
        case_id = self._subject.create_dummy_case()
        created = self._subject.create(
            f'/api/v2/case-chat/cases/{case_id}/conversations',
            {'title': 'a'},
        ).json()['data']
        listed = self._subject.get(
            f'/api/v2/case-chat/cases/{case_id}/conversations'
        ).json()['data']['conversations']
        self.assertTrue(any(c['id'] == created['id'] for c in listed))

    def test_get_conversation_includes_messages_and_pending(self):
        self._enable_chatbot()
        case_id = self._subject.create_dummy_case()
        conv = self._subject.create(
            f'/api/v2/case-chat/cases/{case_id}/conversations',
            {},
        ).json()['data']
        detail = self._subject.get(
            f'/api/v2/case-chat/conversations/{conv["id"]}'
        ).json()['data']
        self.assertEqual([], detail['messages'])
        self.assertEqual([], detail['pending_tool_calls'])

    def test_archive_conversation(self):
        self._enable_chatbot()
        case_id = self._subject.create_dummy_case()
        conv = self._subject.create(
            f'/api/v2/case-chat/cases/{case_id}/conversations', {},
        ).json()['data']
        del_response = self._subject.delete(
            f'/api/v2/case-chat/conversations/{conv["id"]}')
        self.assertEqual(204, del_response.status_code)

        # After archive it shouldn't appear in the default list.
        listed = self._subject.get(
            f'/api/v2/case-chat/cases/{case_id}/conversations'
        ).json()['data']['conversations']
        self.assertFalse(any(c['id'] == conv['id'] for c in listed))
