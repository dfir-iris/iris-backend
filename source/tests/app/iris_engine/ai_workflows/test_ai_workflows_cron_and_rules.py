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

"""Cron schedules and condition rules."""

import datetime
from unittest import TestCase

from app.iris_engine.ai_workflows.context import AiWorkflowTemplateError
from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_rule
from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_rules
from app.iris_engine.ai_workflows.cron import ai_workflows_cron_is_due
from app.iris_engine.ai_workflows.cron import ai_workflows_cron_parse


def _at(day, hour, minute):
    # 2026-10-05 is a Monday
    return datetime.datetime(2026, 10, day, hour, minute, 0)


class TestsCronParse(TestCase):

    def test_fields_should_expand(self):
        spec = ai_workflows_cron_parse('*/15 8-10 1,15 * mon-fri')
        self.assertEqual({0, 15, 30, 45}, set(spec.minutes))
        self.assertEqual({8, 9, 10}, set(spec.hours))
        self.assertEqual({1, 15}, set(spec.days))
        self.assertEqual(set(range(1, 13)), set(spec.months))
        self.assertEqual({1, 2, 3, 4, 5}, set(spec.weekdays))

    def test_names_steps_and_sunday_seven(self):
        spec = ai_workflows_cron_parse('5/20 0 * jan,jul 7')
        self.assertEqual({5, 25, 45}, set(spec.minutes))
        self.assertEqual({1, 7}, set(spec.months))
        self.assertEqual({0}, set(spec.weekdays))

    def test_invalid_expressions_should_raise(self):
        for expression in ('', '* * * *', '60 * * * *', '* 24 * * *', '*/0 * * * *', '5-1 * * * *',
                           'a * * * *', '1,,2 * * * *', None):
            with self.assertRaises(ValueError, msg=expression):
                ai_workflows_cron_parse(expression)

    def test_day_and_weekday_restricted_should_match_either(self):
        spec = ai_workflows_cron_parse('0 0 1 * mon')
        self.assertTrue(spec.matches(datetime.datetime(2026, 10, 1)))     # the 1st (a Thursday)
        self.assertTrue(spec.matches(datetime.datetime(2026, 10, 5)))     # a Monday
        self.assertFalse(spec.matches(datetime.datetime(2026, 10, 6)))

    def test_only_weekday_restricted_should_match_the_weekday(self):
        spec = ai_workflows_cron_parse('0 9 * * sat,sun')
        self.assertTrue(spec.matches(_at(10, 9, 0)))   # Saturday
        self.assertFalse(spec.matches(_at(9, 9, 0)))   # Friday


class TestsCronIsDue(TestCase):

    def test_never_fired_should_be_due_on_a_matching_minute_only(self):
        self.assertTrue(ai_workflows_cron_is_due('*/5 * * * *', _at(5, 10, 15), None))
        self.assertFalse(ai_workflows_cron_is_due('*/5 * * * *', _at(5, 10, 16), None))

    def test_should_not_fire_twice_in_a_minute(self):
        now = _at(5, 10, 15)
        self.assertFalse(ai_workflows_cron_is_due('* * * * *', now, now.replace(second=30)))

    def test_missed_minute_should_be_caught_up(self):
        # The 10:15 tick was late; at 10:17 the schedule is still due
        self.assertTrue(ai_workflows_cron_is_due('15 * * * *', _at(5, 10, 17), _at(5, 9, 15)))
        # Already fired at 10:15
        self.assertFalse(ai_workflows_cron_is_due('15 * * * *', _at(5, 10, 17), _at(5, 10, 15)))

    def test_long_outage_should_not_replay(self):
        # Last matching minute is more than an hour back
        self.assertFalse(ai_workflows_cron_is_due('0 3 * * *', _at(5, 10, 0), _at(4, 3, 0)))


class TestsConditionRules(TestCase):

    _CONTEXT = {
        'entity': {'severity': 4, 'title': 'Phishing campaign', 'tags': ['mail', 'ext'], 'owner': None},
        'nodes': {'agent': {'output': {'verdict': 'malicious', 'score': '87'}}},
        'vars': {'enabled': True, 'empty': ''},
    }

    def _rule(self, path, operator, value=None):
        return ai_workflows_context_eval_rule({'path': path, 'operator': operator, 'value': value}, self._CONTEXT)

    def test_equality_should_coerce_numbers_and_booleans(self):
        self.assertTrue(self._rule('entity.severity', 'eq', '4'))
        self.assertTrue(self._rule('vars.enabled', 'eq', 'true'))
        self.assertTrue(self._rule('nodes.agent.output.verdict', 'ne', 'benign'))

    def test_comparisons(self):
        self.assertTrue(self._rule('nodes.agent.output.score', 'gt', 80))
        self.assertTrue(self._rule('entity.severity', 'lte', 4))
        self.assertFalse(self._rule('entity.severity', 'lt', 'abc'))
        self.assertFalse(self._rule('entity.missing', 'gt', 1))

    def test_contains_and_in(self):
        self.assertTrue(self._rule('entity.title', 'contains', 'campaign'))
        self.assertTrue(self._rule('entity.tags', 'contains', 'ext'))
        self.assertTrue(self._rule('entity.tags', 'not_contains', 'internal'))
        self.assertTrue(self._rule('nodes.agent.output.verdict', 'in', 'suspicious, malicious'))
        self.assertTrue(self._rule('entity.severity', 'in', [3, 4]))

    def test_existence_and_truthiness(self):
        self.assertTrue(self._rule('entity.title', 'exists'))
        self.assertTrue(self._rule('entity.owner', 'not_exists'))
        self.assertTrue(self._rule('entity.nope.deeper', 'not_exists'))
        self.assertTrue(self._rule('vars.empty', 'falsy'))
        self.assertTrue(self._rule('entity.tags.0', 'truthy'))

    def test_unknown_operator_should_raise(self):
        with self.assertRaises(AiWorkflowTemplateError):
            self._rule('entity.title', 'approx', 'x')

    def test_logic(self):
        yes = {'path': 'entity.severity', 'operator': 'eq', 'value': 4}
        no = {'path': 'entity.severity', 'operator': 'eq', 'value': 1}
        self.assertFalse(ai_workflows_context_eval_rules([yes, no], 'and', self._CONTEXT))
        self.assertTrue(ai_workflows_context_eval_rules([yes, no], 'or', self._CONTEXT))
        self.assertTrue(ai_workflows_context_eval_rules([], 'and', self._CONTEXT))
