#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure system_prompt builder.

No DB, no Flask context — everything is pure string construction.
"""

from unittest import TestCase

from app.iris_engine.llm.system_prompt import system_prompt


class TestSystemPromptGlobal(TestCase):

    def test_global_scope_returns_string(self):
        result = system_prompt(case_id=None)
        self.assertIsInstance(result, str)

    def test_global_scope_is_not_empty(self):
        result = system_prompt(case_id=None)
        self.assertGreater(len(result), 0)

    def test_global_scope_does_not_mention_case(self):
        result = system_prompt(case_id=None)
        self.assertNotIn('scoped to IRIS case', result)

    def test_global_scope_does_not_mention_war_room(self):
        result = system_prompt(case_id=None)
        self.assertNotIn('scoped to IRIS war-room', result)

    def test_global_scope_contains_untrusted_content_clause(self):
        result = system_prompt(case_id=None)
        self.assertIn('untrusted', result.lower())

    def test_global_scope_no_read_only_clause_by_default(self):
        result = system_prompt(case_id=None)
        self.assertNotIn('read-only', result)


class TestSystemPromptCase(TestCase):

    def test_case_scoped_mentions_case_id(self):
        result = system_prompt(case_id=42)
        self.assertIn('case #42', result)

    def test_case_scoped_does_not_mention_war_room_scope(self):
        result = system_prompt(case_id=7)
        self.assertNotIn('scoped to IRIS war-room', result)

    def test_case_scoped_still_contains_base_content(self):
        result = system_prompt(case_id=1)
        self.assertIn('IRIS assistant', result)

    def test_different_case_ids_produce_different_prompts(self):
        p1 = system_prompt(case_id=1)
        p2 = system_prompt(case_id=2)
        self.assertNotEqual(p1, p2)

    def test_case_scoped_no_supply_identifier_instruction(self):
        result = system_prompt(case_id=5)
        self.assertIn('do not need to', result)


class TestSystemPromptWarRoom(TestCase):

    def test_war_room_scoped_mentions_war_room_id(self):
        result = system_prompt(case_id=None, war_room_id=99)
        self.assertIn('war-room #99', result)

    def test_war_room_scoped_does_not_mention_case_scope(self):
        result = system_prompt(case_id=None, war_room_id=99)
        self.assertNotIn('scoped to IRIS case', result)

    def test_war_room_takes_precedence_over_case(self):
        # war_room_id wins when both are supplied
        result = system_prompt(case_id=10, war_room_id=20)
        self.assertIn('war-room #20', result)
        self.assertNotIn('case #10', result)

    def test_different_war_room_ids_produce_different_prompts(self):
        p1 = system_prompt(case_id=None, war_room_id=1)
        p2 = system_prompt(case_id=None, war_room_id=2)
        self.assertNotEqual(p1, p2)


class TestSystemPromptReadOnly(TestCase):

    def test_read_only_flag_adds_read_only_clause(self):
        result = system_prompt(case_id=1, read_only_scope=True)
        self.assertIn('read-only', result)

    def test_read_only_global_scope(self):
        result = system_prompt(case_id=None, read_only_scope=True)
        self.assertIn('read-only', result)

    def test_read_only_war_room_scope(self):
        result = system_prompt(case_id=None, war_room_id=3, read_only_scope=True)
        self.assertIn('read-only', result)
        self.assertIn('war-room #3', result)

    def test_read_only_false_suppresses_clause(self):
        result = system_prompt(case_id=1, read_only_scope=False)
        self.assertNotIn('read-only access here', result)

    def test_read_only_appended_to_end(self):
        base = system_prompt(case_id=1, read_only_scope=False)
        with_ro = system_prompt(case_id=1, read_only_scope=True)
        self.assertTrue(with_ro.startswith(base))
