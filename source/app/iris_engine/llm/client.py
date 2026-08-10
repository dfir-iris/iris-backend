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


def load_config(policy=None) -> ChatbotConfig:
    """Snapshot the chatbot settings from the singleton row.

    When `policy` is a `ChatbotPolicy` row, every scalar field on the
    policy overrides the corresponding `ServerSettings.chatbot_*`
    value — a customer-bound conversation uses THAT customer's provider,
    model, budgets, redaction toggles, etc. `enabled` still comes from
    server settings (an admin must have Yuki turned on globally before
    per-customer routing is possible).
    """
    s = get_srv_settings()
    raw_key = None
    if policy is not None and policy.api_key:
        raw_key = policy.api_key
    else:
        raw_key = getattr(s, 'chatbot_api_key', None) or ''
    api_key = ''
    if raw_key:
        try:
            api_key = decrypt_secret(raw_key)
        except Exception:
            logger.exception('failed to decrypt chatbot api_key')
            api_key = ''

    def _pick(policy_attr: str, settings_attr: str, cast, default):
        # A policy field is considered "unset" only when None or empty
        # string — 0 is a valid explicit value (e.g. "unlimited" budget
        # or "disabled cap"), so falling through on 0 would silently
        # promote the customer to the server-wide default. That's
        # exactly the leak the policy layer exists to prevent.
        if policy is not None:
            val = getattr(policy, policy_attr, None)
            if val is not None and val != '':
                try:
                    return cast(val)
                except (TypeError, ValueError):
                    pass
        raw = getattr(s, settings_attr, default)
        try:
            return cast(raw) if raw not in (None, '') else default
        except (TypeError, ValueError):
            return default

    def _pick_bool(policy_attr: str, settings_attr: str, default: bool):
        if policy is not None:
            return bool(getattr(policy, policy_attr, default))
        return bool(getattr(s, settings_attr, default))

    return ChatbotConfig(
        enabled=bool(getattr(s, 'chatbot_enabled', False)),
        provider=_pick('provider', 'chatbot_provider', str, ''),
        api_key=api_key,
        model=_pick('model', 'chatbot_model', str, ''),
        base_url=_pick('base_url', 'chatbot_base_url', str, ''),
        max_turns_per_conversation=_pick(
            'max_turns_per_conversation',
            'chatbot_max_turns_per_conversation', int, 25),
        max_tool_calls_per_turn=_pick(
            'max_tool_calls_per_turn',
            'chatbot_max_tool_calls_per_turn', int, 8),
        auto_execute_read_tools=_pick_bool(
            'auto_execute_read_tools',
            'chatbot_auto_execute_read_tools', True),
        auto_approve_write_tools=_pick_bool(
            'auto_approve_write_tools',
            'chatbot_auto_approve_write_tools', False),
        daily_token_budget_per_user=_pick(
            'daily_token_budget_per_user',
            'chatbot_daily_token_budget_per_user', int, 500_000),
        daily_token_budget_org=_pick(
            'daily_token_budget_org',
            'chatbot_daily_token_budget_org', int, 10_000_000),
        redact_ips=_pick_bool('redact_ips', 'chatbot_redact_ips', False),
        redact_emails=_pick_bool(
            'redact_emails', 'chatbot_redact_emails', False),
        redact_hashes=_pick_bool(
            'redact_hashes', 'chatbot_redact_hashes', False),
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
