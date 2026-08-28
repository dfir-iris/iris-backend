#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure ChatbotConfig dataclass and ChatbotDisabledError."""

from unittest import TestCase

from app.iris_engine.llm.client import ChatbotConfig, ChatbotDisabledError


def _config(**overrides):
    defaults = dict(
        enabled=True,
        provider='anthropic',
        api_key='sk-test',
        model='claude-sonnet-5',
        base_url='https://api.anthropic.com',
        max_turns_per_conversation=20,
        max_tool_calls_per_turn=10,
        auto_execute_read_tools=True,
        auto_approve_write_tools=False,
        daily_token_budget_per_user=100_000,
        daily_token_budget_org=1_000_000,
        redact_ips=False,
        redact_emails=False,
        redact_hashes=False,
    )
    defaults.update(overrides)
    return ChatbotConfig(**defaults)


class TestChatbotConfig(TestCase):

    def test_enabled_stored(self):
        self.assertTrue(_config(enabled=True).enabled)
        self.assertFalse(_config(enabled=False).enabled)

    def test_provider_stored(self):
        self.assertEqual('openai', _config(provider='openai').provider)

    def test_api_key_stored(self):
        self.assertEqual('my-key', _config(api_key='my-key').api_key)

    def test_model_stored(self):
        self.assertEqual('gpt-4o', _config(model='gpt-4o').model)

    def test_base_url_stored(self):
        self.assertEqual('https://x.com', _config(base_url='https://x.com').base_url)

    def test_max_turns_stored(self):
        self.assertEqual(5, _config(max_turns_per_conversation=5).max_turns_per_conversation)

    def test_max_tool_calls_stored(self):
        self.assertEqual(3, _config(max_tool_calls_per_turn=3).max_tool_calls_per_turn)

    def test_auto_execute_read_tools_stored(self):
        self.assertTrue(_config(auto_execute_read_tools=True).auto_execute_read_tools)
        self.assertFalse(_config(auto_execute_read_tools=False).auto_execute_read_tools)

    def test_auto_approve_write_tools_stored(self):
        self.assertTrue(_config(auto_approve_write_tools=True).auto_approve_write_tools)

    def test_daily_token_budget_per_user_stored(self):
        self.assertEqual(50_000, _config(daily_token_budget_per_user=50_000).daily_token_budget_per_user)

    def test_daily_token_budget_org_stored(self):
        self.assertEqual(500_000, _config(daily_token_budget_org=500_000).daily_token_budget_org)

    def test_redact_ips_stored(self):
        self.assertTrue(_config(redact_ips=True).redact_ips)

    def test_redact_emails_stored(self):
        self.assertTrue(_config(redact_emails=True).redact_emails)

    def test_redact_hashes_stored(self):
        self.assertTrue(_config(redact_hashes=True).redact_hashes)

    def test_frozen(self):
        cfg = _config()
        with self.assertRaises(Exception):
            cfg.provider = 'ollama'  # type: ignore

    def test_equality_same_values(self):
        c1 = _config()
        c2 = _config()
        self.assertEqual(c1, c2)

    def test_inequality_different_provider(self):
        self.assertNotEqual(_config(provider='anthropic'), _config(provider='openai'))


class TestChatbotDisabledError(TestCase):

    def test_is_exception(self):
        self.assertIsInstance(ChatbotDisabledError('off'), Exception)

    def test_message_stored(self):
        err = ChatbotDisabledError('chatbot is disabled')
        self.assertEqual('chatbot is disabled', str(err))

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(ChatbotDisabledError):
            raise ChatbotDisabledError('disabled')
