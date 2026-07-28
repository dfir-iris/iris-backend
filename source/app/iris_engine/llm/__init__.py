#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""LLM provider adapters for the case-chat feature.

Structure:
  * providers/base.py       — LLMProvider protocol + LLMEvent hierarchy
  * providers/anthropic.py  — Anthropic /v1/messages streaming adapter
  * providers/openai.py     — OpenAI /v1/chat/completions streaming adapter
  * providers/ollama.py     — Ollama /api/chat streaming adapter
  * client.py               — get_llm_provider() reads ServerSettings, returns
                              a configured adapter
  * reload.py               — reload_llm_client(app) invalidates cache
  * redaction.py            — IP/email/hash scrubber applied to tool_result
                              content before it enters an LLM request
  * system_prompt.py        — the single canonical system prompt including
                              the untrusted-content clause

The provider adapters share a common wire model — Anthropic-shaped
content blocks — so persisted messages round-trip losslessly regardless
of which provider was on when the message was recorded.
"""
