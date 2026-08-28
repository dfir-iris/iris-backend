#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure LLM event dataclasses and base types.

No DB, no Flask context, no network — everything is pure Python
construction and equality.
"""

from unittest import TestCase

from app.iris_engine.llm.providers.base import (
    ChatMessage,
    Error,
    MessageEnd,
    TextDelta,
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
)


class TestTextDelta(TestCase):

    def test_stores_text(self):
        self.assertEqual('hello', TextDelta('hello').text)

    def test_empty_string_allowed(self):
        self.assertEqual('', TextDelta('').text)

    def test_frozen(self):
        td = TextDelta('x')
        with self.assertRaises(Exception):
            td.text = 'y'  # type: ignore

    def test_equality(self):
        self.assertEqual(TextDelta('a'), TextDelta('a'))

    def test_inequality(self):
        self.assertNotEqual(TextDelta('a'), TextDelta('b'))


class TestToolUseStart(TestCase):

    def test_stores_id_and_name(self):
        ev = ToolUseStart('tid-1', 'my_tool')
        self.assertEqual('tid-1', ev.tool_use_id)
        self.assertEqual('my_tool', ev.name)

    def test_frozen(self):
        ev = ToolUseStart('x', 'y')
        with self.assertRaises(Exception):
            ev.name = 'z'  # type: ignore

    def test_equality(self):
        self.assertEqual(ToolUseStart('a', 'b'), ToolUseStart('a', 'b'))


class TestToolUseDelta(TestCase):

    def test_stores_id_and_partial_json(self):
        ev = ToolUseDelta('tid', '{"key":')
        self.assertEqual('tid', ev.tool_use_id)
        self.assertEqual('{"key":', ev.partial_json)

    def test_frozen(self):
        ev = ToolUseDelta('x', 'y')
        with self.assertRaises(Exception):
            ev.partial_json = 'z'  # type: ignore


class TestToolUseEnd(TestCase):

    def test_stores_all_fields(self):
        ev = ToolUseEnd('tid', 'get_ioc', {'ioc_id': 1})
        self.assertEqual('tid', ev.tool_use_id)
        self.assertEqual('get_ioc', ev.name)
        self.assertEqual({'ioc_id': 1}, ev.arguments)

    def test_empty_arguments_allowed(self):
        ev = ToolUseEnd('tid', 'tool', {})
        self.assertEqual({}, ev.arguments)

    def test_frozen(self):
        ev = ToolUseEnd('x', 'y', {})
        with self.assertRaises(Exception):
            ev.name = 'z'  # type: ignore


class TestMessageEnd(TestCase):

    def test_stop_reason_required(self):
        ev = MessageEnd('end_turn')
        self.assertEqual('end_turn', ev.stop_reason)

    def test_optional_fields_default_to_none(self):
        ev = MessageEnd('end_turn')
        self.assertIsNone(ev.prompt_tokens)
        self.assertIsNone(ev.completion_tokens)
        self.assertIsNone(ev.cache_read_tokens)
        self.assertIsNone(ev.cache_creation_tokens)

    def test_all_fields_stored(self):
        ev = MessageEnd('tool_use', prompt_tokens=100, completion_tokens=50,
                        cache_read_tokens=20, cache_creation_tokens=10)
        self.assertEqual(100, ev.prompt_tokens)
        self.assertEqual(50, ev.completion_tokens)
        self.assertEqual(20, ev.cache_read_tokens)
        self.assertEqual(10, ev.cache_creation_tokens)

    def test_frozen(self):
        ev = MessageEnd('end_turn')
        with self.assertRaises(Exception):
            ev.stop_reason = 'x'  # type: ignore


class TestError(TestCase):

    def test_message_stored(self):
        ev = Error('something broke')
        self.assertEqual('something broke', ev.message)

    def test_provider_code_defaults_to_none(self):
        ev = Error('err')
        self.assertIsNone(ev.provider_code)

    def test_provider_code_stored(self):
        ev = Error('err', provider_code='overloaded_error')
        self.assertEqual('overloaded_error', ev.provider_code)

    def test_frozen(self):
        ev = Error('msg')
        with self.assertRaises(Exception):
            ev.message = 'other'  # type: ignore


class TestToolSpec(TestCase):

    def test_stores_all_fields(self):
        schema = {'type': 'object', 'properties': {}}
        ts = ToolSpec('my_tool', 'Does a thing', schema)
        self.assertEqual('my_tool', ts.name)
        self.assertEqual('Does a thing', ts.description)
        self.assertIs(schema, ts.input_schema)

    def test_frozen(self):
        ts = ToolSpec('x', 'y', {})
        with self.assertRaises(Exception):
            ts.name = 'z'  # type: ignore


class TestChatMessage(TestCase):

    def test_role_stored(self):
        msg = ChatMessage(role='user')
        self.assertEqual('user', msg.role)

    def test_content_defaults_to_empty_list(self):
        msg = ChatMessage(role='user')
        self.assertEqual([], msg.content)

    def test_content_stored(self):
        blocks = [{'type': 'text', 'text': 'hi'}]
        msg = ChatMessage(role='user', content=blocks)
        self.assertEqual(blocks, msg.content)

    def test_default_content_is_independent_across_instances(self):
        m1 = ChatMessage(role='user')
        m2 = ChatMessage(role='user')
        m1.content.append({'type': 'text', 'text': 'mutation'})
        self.assertEqual([], m2.content)

    def test_frozen(self):
        msg = ChatMessage(role='user')
        with self.assertRaises(Exception):
            msg.role = 'assistant'  # type: ignore
