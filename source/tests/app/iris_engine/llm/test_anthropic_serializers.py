#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure Anthropic provider serializer functions.

_serialize_message and _serialize_tool are pure dict transformations —
no network, no DB, no Flask context.
"""

from unittest import TestCase

from app.iris_engine.llm.providers.anthropic import (
    _serialize_message,
    _serialize_tool,
)
from app.iris_engine.llm.providers.base import ChatMessage, ToolSpec


class TestSerializeMessage(TestCase):

    def test_user_role_preserved(self):
        msg = ChatMessage(role='user', content=[{'type': 'text', 'text': 'hi'}])
        result = _serialize_message(msg)
        self.assertEqual('user', result['role'])

    def test_assistant_role_preserved(self):
        msg = ChatMessage(role='assistant', content=[])
        result = _serialize_message(msg)
        self.assertEqual('assistant', result['role'])

    def test_tool_role_mapped_to_user(self):
        msg = ChatMessage(role='tool', content=[{'type': 'tool_result', 'tool_use_id': 'x'}])
        result = _serialize_message(msg)
        self.assertEqual('user', result['role'])

    def test_content_preserved(self):
        blocks = [{'type': 'text', 'text': 'hello world'}]
        msg = ChatMessage(role='user', content=blocks)
        result = _serialize_message(msg)
        self.assertEqual(blocks, result['content'])

    def test_empty_content_preserved(self):
        msg = ChatMessage(role='assistant', content=[])
        result = _serialize_message(msg)
        self.assertEqual([], result['content'])

    def test_result_has_role_and_content_keys(self):
        msg = ChatMessage(role='user')
        result = _serialize_message(msg)
        self.assertIn('role', result)
        self.assertIn('content', result)

    def test_result_has_no_extra_keys(self):
        msg = ChatMessage(role='user')
        result = _serialize_message(msg)
        self.assertEqual({'role', 'content'}, set(result.keys()))

    def test_multiple_content_blocks_preserved(self):
        blocks = [
            {'type': 'text', 'text': 'a'},
            {'type': 'tool_use', 'id': 'x', 'name': 'f', 'input': {}},
        ]
        msg = ChatMessage(role='assistant', content=blocks)
        result = _serialize_message(msg)
        self.assertEqual(2, len(result['content']))


class TestSerializeTool(TestCase):

    def test_name_preserved(self):
        ts = ToolSpec('get_ioc', 'Retrieve an IOC', {'type': 'object'})
        result = _serialize_tool(ts)
        self.assertEqual('get_ioc', result['name'])

    def test_description_preserved(self):
        ts = ToolSpec('t', 'My description', {})
        result = _serialize_tool(ts)
        self.assertEqual('My description', result['description'])

    def test_input_schema_preserved(self):
        schema = {'type': 'object', 'properties': {'ioc_id': {'type': 'integer'}}}
        ts = ToolSpec('t', 'd', schema)
        result = _serialize_tool(ts)
        self.assertEqual(schema, result['input_schema'])

    def test_result_keys(self):
        ts = ToolSpec('n', 'd', {})
        result = _serialize_tool(ts)
        self.assertEqual({'name', 'description', 'input_schema'}, set(result.keys()))

    def test_empty_schema_allowed(self):
        ts = ToolSpec('n', 'd', {})
        result = _serialize_tool(ts)
        self.assertEqual({}, result['input_schema'])
