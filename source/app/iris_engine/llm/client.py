#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Provider factory + config snapshot.

`get_llm_provider()` reads the singleton `ServerSettings` row, decrypts
`chatbot_api_key`, and returns a ready-to-call adapter. Callers pass the
result to their tool-use loop for one turn — no per-request caching in
v1 because the adapter is a thin object (an api_key + a base_url) and
the DB round-trip to read settings is cheap next to the LLM call
itself.

`ChatbotConfig` is a plain dataclass snapshot of the settings fields
the loop cares about, so downstream code doesn't have to reach into
`ServerSettings` (which pulls in SQLAlchemy) or worry about the row
mutating mid-turn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.business.server_settings import get_srv_settings
from app.iris_engine.llm.providers.anthropic import AnthropicProvider
from app.iris_engine.llm.providers.base import LLMProvider
from app.iris_engine.llm.providers.ollama import OllamaProvider
from app.iris_engine.llm.providers.openai import OpenAIProvider
from app.iris_engine.mail.secrets import decrypt_secret


logger = logging.getLogger('iris.chatbot.client')


@dataclass(frozen=True)
class ChatbotConfig:
    """Effective chatbot configuration for one loop iteration."""
    enabled: bool
    provider: str
    api_key: str
    model: str
    base_url: str
    max_turns_per_conversation: int
    max_tool_calls_per_turn: int
    auto_execute_read_tools: bool
    auto_approve_write_tools: bool
    daily_token_budget_per_user: int
    daily_token_budget_org: int
    redact_ips: bool
    redact_emails: bool
    redact_hashes: bool


class ChatbotDisabledError(Exception):
    """Raised when the chatbot is disabled or misconfigured. The socket
    handler surfaces the message via an `error` event."""


def load_config() -> ChatbotConfig:
    """Snapshot the chatbot settings from the singleton row."""
    s = get_srv_settings()
    raw_key = getattr(s, 'chatbot_api_key', None) or ''
    api_key = ''
    if raw_key:
        try:
            api_key = decrypt_secret(raw_key)
        except Exception:
            logger.exception('failed to decrypt chatbot_api_key')
            api_key = ''
    return ChatbotConfig(
        enabled=bool(getattr(s, 'chatbot_enabled', False)),
        provider=str(getattr(s, 'chatbot_provider', '') or ''),
        api_key=api_key,
        model=str(getattr(s, 'chatbot_model', '') or ''),
        base_url=str(getattr(s, 'chatbot_base_url', '') or ''),
        max_turns_per_conversation=int(
            getattr(s, 'chatbot_max_turns_per_conversation', 25) or 25),
        max_tool_calls_per_turn=int(
            getattr(s, 'chatbot_max_tool_calls_per_turn', 8) or 8),
        auto_execute_read_tools=bool(
            getattr(s, 'chatbot_auto_execute_read_tools', True)),
        auto_approve_write_tools=bool(
            getattr(s, 'chatbot_auto_approve_write_tools', False)),
        daily_token_budget_per_user=int(
            getattr(s, 'chatbot_daily_token_budget_per_user', 500_000) or 0),
        daily_token_budget_org=int(
            getattr(s, 'chatbot_daily_token_budget_org', 10_000_000) or 0),
        redact_ips=bool(getattr(s, 'chatbot_redact_ips', False)),
        redact_emails=bool(getattr(s, 'chatbot_redact_emails', False)),
        redact_hashes=bool(getattr(s, 'chatbot_redact_hashes', False)),
    )


def get_llm_provider(config: ChatbotConfig | None = None) -> LLMProvider:
    """Return an initialised provider adapter.

    Raises `ChatbotDisabledError` for a clean, catchable failure path
    when the chatbot is off, unconfigured, or points at an unknown
    provider name. The socket handler translates the exception into a
    single `error` event so the SPA can surface it inline.
    """
    cfg = config or load_config()
    if not cfg.enabled:
        raise ChatbotDisabledError('Chatbot is disabled on this server.')
    if not cfg.provider:
        raise ChatbotDisabledError(
            'Chatbot provider is not configured — pick anthropic, openai, '
            'or ollama in Settings → Server → Chatbot.'
        )
    if not cfg.model:
        raise ChatbotDisabledError(
            'Chatbot model is not configured — enter a model id in '
            'Settings → Server → Chatbot.'
        )

    provider = cfg.provider.strip().lower()
    if provider == 'anthropic':
        if not cfg.api_key:
            raise ChatbotDisabledError(
                'Anthropic requires an API key — set chatbot_api_key.')
        return AnthropicProvider(
            api_key=cfg.api_key, base_url=cfg.base_url or None)
    if provider == 'openai':
        if not cfg.api_key:
            raise ChatbotDisabledError(
                'OpenAI requires an API key — set chatbot_api_key.')
        return OpenAIProvider(
            api_key=cfg.api_key, base_url=cfg.base_url or None)
    if provider == 'ollama':
        # Ollama runs unauthenticated by default; api_key is optional.
        return OllamaProvider(
            api_key=cfg.api_key or None,
            base_url=cfg.base_url or None,
        )
    raise ChatbotDisabledError(f'Unknown provider: {cfg.provider!r}')
