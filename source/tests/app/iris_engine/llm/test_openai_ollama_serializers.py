#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure serializer functions in the OpenAI and Ollama providers.

No network, no DB, no Flask context — all pure dict transformations.
"""

from unittest import TestCase

from app.iris_engine.llm.providers.base import ChatMessage, ToolSpec
from app.iris_engine.llm.providers.openai import (
    _blocks_to_text as openai_blocks_to_text,
    _ours_to_openai_messages,
    _serialize_tool as openai_serialize_tool,
)
from app.iris_engine.llm.providers.ollama import (
    _blocks_to_text as ollama_blocks_to_text,
    _ours_to_ollama_messages,
    _serialize_tool as ollama_serialize_tool,
)


# ---------------------------------------------------------------------------
# _blocks_to_text (shared logic, same impl in both providers)
# ---------------------------------------------------------------------------

class TestBlocksToTextOpenAI(TestCase):

    def test_empty_list_returns_empty_string(self):
        self.assertEqual('', openai_blocks_to_text([]))

    def test_single_text_block_returned(self):
        self.assertEqual('hello', openai_blocks_to_text([{'type': 'text', 'text': 'hello'}]))

    def test_non_text_blocks_ignored(self):
        blocks = [{'type': 'tool_use', 'id': 'x', 'name': 'f', 'input': {}}]
        self.assertEqual('', openai_blocks_to_text(blocks))

    def test_multiple_text_blocks_concatenated(self):
        blocks = [{'type': 'text', 'text': 'foo'}, {'type': 'text', 'text': 'bar'}]
        self.assertEqual('foobar', openai_blocks_to_text(blocks))

    def test_missing_text_key_treated_as_empty(self):
        self.assertEqual('', openai_blocks_to_text([{'type': 'text'}]))

    def test_mixed_blocks_only_text_extracted(self):
        blocks = [
            {'type': 'text', 'text': 'A'},
            {'type': 'tool_use', 'name': 'f'},
            {'type': 'text', 'text': 'B'},
        ]
        self.assertEqual('AB', openai_blocks_to_text(blocks))


class TestBlocksToTextOllama(TestCase):

    def test_empty_list_returns_empty_string(self):
        self.assertEqual('', ollama_blocks_to_text([]))

    def test_single_text_block_returned(self):
        self.assertEqual('hi', ollama_blocks_to_text([{'type': 'text', 'text': 'hi'}]))

    def test_non_text_blocks_ignored(self):
        self.assertEqual('', ollama_blocks_to_text([{'type': 'image', 'url': 'x'}]))


# ---------------------------------------------------------------------------
# _serialize_tool — OpenAI and Ollama both use OpenAI function-call shape
# ---------------------------------------------------------------------------

class TestSerializeToolOpenAI(TestCase):

    def test_type_is_function(self):
        ts = ToolSpec('t', 'd', {})
        result = openai_serialize_tool(ts)
        self.assertEqual('function', result['type'])

    def test_function_name_preserved(self):
        ts = ToolSpec('my_tool', 'd', {})
        result = openai_serialize_tool(ts)
        self.assertEqual('my_tool', result['function']['name'])

    def test_function_description_preserved(self):
        ts = ToolSpec('t', 'My desc', {})
        result = openai_serialize_tool(ts)
        self.assertEqual('My desc', result['function']['description'])

    def test_input_schema_stored_as_parameters(self):
        schema = {'type': 'object', 'properties': {'x': {'type': 'integer'}}}
        ts = ToolSpec('t', 'd', schema)
        result = openai_serialize_tool(ts)
        self.assertEqual(schema, result['function']['parameters'])

    def test_top_level_keys(self):
        result = openai_serialize_tool(ToolSpec('n', 'd', {}))
        self.assertEqual({'type', 'function'}, set(result.keys()))


class TestSerializeToolOllama(TestCase):

    def test_type_is_function(self):
        ts = ToolSpec('t', 'd', {})
        result = ollama_serialize_tool(ts)
        self.assertEqual('function', result['type'])

    def test_function_name_preserved(self):
        ts = ToolSpec('ollama_tool', 'd', {})
        result = ollama_serialize_tool(ts)
        self.assertEqual('ollama_tool', result['function']['name'])

    def test_input_schema_stored_as_parameters(self):
        schema = {'type': 'object'}
        ts = ToolSpec('t', 'd', schema)
        result = ollama_serialize_tool(ts)
        self.assertEqual(schema, result['function']['parameters'])


# ---------------------------------------------------------------------------
# _ours_to_openai_messages
# ---------------------------------------------------------------------------

class TestOursToOpenAIMessages(TestCase):

    def test_user_message_returns_single_message(self):
        msg = ChatMessage(role='user', content=[{'type': 'text', 'text': 'hello'}])
        result = _ours_to_openai_messages(msg)
        self.assertEqual(1, len(result))

    def test_user_message_role_is_user(self):
        msg = ChatMessage(role='user', content=[])
        result = _ours_to_openai_messages(msg)
        self.assertEqual('user', result[0]['role'])

    def test_user_message_text_extracted(self):
        msg = ChatMessage(role='user', content=[{'type': 'text', 'text': 'what is 2+2?'}])
        result = _ours_to_openai_messages(msg)
        self.assertEqual('what is 2+2?', result[0]['content'])

    def test_tool_role_returns_user_messages(self):
        msg = ChatMessage(role='tool', content=[{
            'type': 'tool_result',
            'tool_use_id': 'tid-1',
            'content': [{'type': 'text', 'text': 'result'}],
        }])
        result = _ours_to_openai_messages(msg)
        self.assertGreater(len(result), 0)
        # Each message should have role 'tool'
        for m in result:
            self.assertEqual('tool', m['role'])


# ---------------------------------------------------------------------------
# _ours_to_ollama_messages
# ---------------------------------------------------------------------------

class TestOursToOllamaMessages(TestCase):

    def test_user_message_returns_single_message(self):
        msg = ChatMessage(role='user', content=[{'type': 'text', 'text': 'hello'}])
        result = _ours_to_ollama_messages(msg)
        self.assertEqual(1, len(result))

    def test_user_message_role_is_user(self):
        msg = ChatMessage(role='user', content=[])
        result = _ours_to_ollama_messages(msg)
        self.assertEqual('user', result[0]['role'])

    def test_user_message_text_extracted(self):
        msg = ChatMessage(role='user', content=[{'type': 'text', 'text': 'query'}])
        result = _ours_to_ollama_messages(msg)
        self.assertEqual('query', result[0]['content'])

    def test_tool_role_returns_tool_messages(self):
        msg = ChatMessage(role='tool', content=[{
            'type': 'tool_result',
            'tool_use_id': 'tid-1',
            'content': [{'type': 'text', 'text': 'data'}],
        }])
        result = _ours_to_ollama_messages(msg)
        self.assertGreater(len(result), 0)
        for m in result:
            self.assertEqual('tool', m['role'])
