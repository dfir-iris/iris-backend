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

"""The sandbox of the `python` node: what scripts may do, what they may
not reach, and the budgets that stop them."""

from unittest import TestCase

from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_check
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_execute
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run


def _run(source, variables=None, **kwargs):
    return ai_workflows_sandbox_run(source, variables or {}, **kwargs)


def _result(source, variables=None):
    outcome = _run(source, variables)
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


class TestsSandboxLanguage(TestCase):

    def test_result_variable_should_be_the_output(self):
        self.assertEqual({'a': 1}, _result("result = {'a': 1}"))

    def test_top_level_return_should_be_the_output(self):
        self.assertEqual(3, _result('return 1 + 2'))

    def test_no_result_should_give_none(self):
        self.assertIsNone(_result('x = 1'))

    def test_globals_should_be_the_given_variables(self):
        self.assertEqual('MD5', _result("result = inputs['type'].upper()", {'inputs': {'type': 'md5'}}))

    def test_control_flow_and_functions_should_work(self):
        source = '\n'.join((
            'def fact(n):',
            '    return 1 if n < 2 else n * fact(n - 1)',
            'total = 0',
            'for i in range(10):',
            '    if i % 2:',
            '        continue',
            '    total += i',
            'n = 0',
            'while True:',
            '    n += 1',
            '    if n > 3:',
            '        break',
            'result = [fact(5), total, n]',
        ))
        self.assertEqual([120, 20, 4], _result(source))

    def test_comprehensions_lambdas_and_unpacking_should_work(self):
        source = '\n'.join((
            "pairs = [(k, v) for k, v in {'a': 2, 'b': 1}.items() if v]",
            'first, second = sorted(pairs, key=lambda p: p[1])',
            "squares = {k: v * v for k, v in pairs}",
            "result = [first, second, squares, {x for x in [1, 1, 2]}]",
        ))
        self.assertEqual([['b', 1], ['a', 2], {'a': 4, 'b': 1}, [1, 2]], _result(source))

    def test_strings_and_fstrings_should_work(self):
        source = "x = 'A,b , c'\nresult = [p.strip().lower() for p in x.split(',')] + [f'{3.14159:.2f}', f'{7:03d}']"
        self.assertEqual(['a', 'b', 'c', '3.14', '007'], _result(source))

    def test_subscript_assignment_should_work(self):
        self.assertEqual({'k': [6, 2]}, _result("d = {'k': [1, 2]}\nd['k'][0] += 5\nresult = d"))

    def test_try_except_should_catch_script_errors(self):
        self.assertEqual("Missing key 'a'", _result("try:\n    x = {}['a']\nexcept Exception as e:\n    result = e"))
        self.assertTrue(_result("try:\n    int('x')\nexcept:\n    result = True"))

    def test_helpers_should_work(self):
        source = '\n'.join((
            "url_id = base64_encode('http://example.org/a', urlsafe=True, padding=False)",
            "data = json_parse('{\"a\": [1, 2]}')",
            "match = re_search('(\\\\d+)', 'abc 42')",
            "result = [url_id, data['a'], match['groups'][0], sha256('x')[:8], ip_info('10.0.0.1')['is_private'],",
            "          url_parse('https://e.org:8443/p?q=1')['host'], type_of(None), iso_datetime(1700000000),",
            "          iso_datetime('x')]",
        ))
        self.assertEqual(['aHR0cDovL2V4YW1wbGUub3JnL2E', [1, 2], '42', '2d711642', True, 'e.org', 'none',
                          '2023-11-14T22:13:20Z', None],
                         _result(source))

    def test_print_should_be_captured(self):
        outcome = _run("print('hello', 1)\nresult = 1")
        self.assertEqual(['hello 1'], outcome['logs'])

    def test_fail_should_end_the_script_and_not_be_catchable(self):
        outcome = _run("try:\n    fail('nope')\nexcept:\n    result = 'caught'")
        self.assertFalse(outcome['ok'])
        self.assertEqual('failed', outcome['kind'])
        self.assertEqual('nope', outcome['error'])
        self.assertEqual(2, outcome['line'])

    def test_errors_should_carry_the_line(self):
        outcome = _run("x = 1\ny = x / 0")
        self.assertFalse(outcome['ok'])
        self.assertEqual(2, outcome['line'])


class TestsSandboxContainment(TestCase):

    def _refused(self, source):
        self.assertTrue(ai_workflows_sandbox_check(source), source)
        self.assertFalse(_run(source)['ok'], source)

    def test_imports_should_be_refused(self):
        self._refused('import os')
        self._refused('from os import system')

    def test_attribute_access_should_be_refused(self):
        self._refused("x = ''.__class__")
        self._refused("x = (1).real")
        self._refused("x = [].append")

    def test_dunder_and_private_names_should_be_refused(self):
        self._refused("__import__('os')")
        self._refused("_x = 1")
        self._refused("def f(_a):\n    return _a")

    def test_unknown_or_private_methods_should_be_refused(self):
        self._refused("''.format(1)")
        self._refused("''.__add__('x')")
        self._refused("''.encode('utf-8')")

    def test_dangerous_statements_should_be_refused(self):
        for source in ('class A:\n    pass', 'global x', 'with x as y:\n    pass', "raise Exception('x')",
                       'async def f():\n    pass', 'del x', 'f(*args)', 'f(**kwargs)', 'x = yield 1',
                       '@d\ndef f():\n    pass', 'assert False', 'x := 1'):
            self.assertTrue(ai_workflows_sandbox_check(source), source)

    def test_python_builtins_should_not_exist(self):
        for name in ('open', 'eval', 'exec', 'compile', 'getattr', 'globals', 'vars', 'type', 'object',
                     'breakpoint', 'input', '__builtins__'):
            outcome = _run(f'result = {name}')
            self.assertFalse(outcome['ok'], name)

    def test_builtin_values_should_not_leak_python_objects(self):
        self.assertEqual('<builtin len>', _result('result = str(len)'))
        self.assertEqual('function', _result('def f():\n    pass\nresult = type_of(f)'))

    def test_percent_formatting_should_be_refused(self):
        self.assertFalse(_run("result = '%s' % 1")['ok'])

    def test_unknown_format_spec_should_be_refused(self):
        self.assertFalse(_run("result = f'{1:{\"9\" * 9}}'")['ok'])

    def test_output_should_be_plain_json(self):
        self.assertEqual({'t': [1, 2], 's': ['a'], 'f': None}, _result("result = {'t': (1, 2), 's': {'a'}, "
                                                                      "'f': float('inf')}"))
        self.assertEqual('<function f>', _result('def f():\n    pass\nresult = f'))

    def test_set_changed_while_iterated_should_not_raise(self):
        self.assertEqual([1, 2, 3], _result('s = {1, 2, 3}\nx = map(lambda v: s.add(v + 10), s)\nresult = '
                                            'sorted([v for v in s if v < 10])'))

    def test_complex_numbers_should_be_refused(self):
        outcome = _run('x = (-8) ** 0.5')
        self.assertEqual((False, 'error'), (outcome['ok'], outcome['kind']))

    def test_error_messages_should_be_bounded(self):
        outcome = _run("s = '<' * 100000\nx = f'{1:{s}}'")
        self.assertFalse(outcome['ok'])
        self.assertLess(len(outcome['error']), 600)


class TestsSandboxBudgets(TestCase):

    def _limit(self, source, **kwargs):
        outcome = _run(source, **kwargs)
        self.assertFalse(outcome['ok'], source)
        self.assertEqual('limit', outcome['kind'], outcome)
        return outcome

    def test_infinite_loop_should_hit_the_step_budget(self):
        self._limit('while True:\n    pass', max_steps=5000)

    def test_limit_errors_should_not_be_catchable(self):
        self._limit('try:\n    while True:\n        pass\nexcept:\n    result = 1', max_steps=5000)

    def test_deep_recursion_should_be_stopped(self):
        self._limit('def f(n):\n    return f(n + 1)\nf(0)')

    def test_big_values_should_be_refused(self):
        self._limit("x = 'a' * 10\nwhile True:\n    x = x + x")
        self._limit("x = 'a' * 100000000")
        self._limit('x = 2 ** 100000')
        self._limit('x = 1 << 100000')
        self._limit('x = list(range(10000000))')
        self._limit("x = 'ab'.replace('a', 'a' * 900000) * 2")
        self._limit("x = ' '.join(['a' * 1000] * 2000)")

    def test_self_referencing_values_should_be_stopped(self):
        self._limit('x = [1]\nx.append(x)\nresult = x')

    def test_time_budget_should_stop_the_script(self):
        self._limit('while True:\n    pass', max_steps=2_000_000, timeout_seconds=0.05)

    def test_oversized_source_should_be_refused(self):
        self.assertTrue(ai_workflows_sandbox_check('x = 1\n' * 5000))


class TestsSandboxProcess(TestCase):

    def test_execute_should_run_in_a_child_process(self):
        outcome = ai_workflows_sandbox_execute("print('hi')\nresult = sum(inputs['n'])", {'inputs': {'n': [1, 2]}},
                                               timeout_seconds=5)
        self.assertEqual({'ok': True, 'result': 3, 'logs': ['hi']}, {k: outcome[k] for k in ('ok', 'result', 'logs')})

    def test_execute_should_refuse_invalid_scripts_without_starting(self):
        outcome = ai_workflows_sandbox_execute('import os', {})
        self.assertEqual('invalid', outcome['kind'])

    def test_catastrophic_regex_should_be_killed(self):
        outcome = ai_workflows_sandbox_execute("x = re_search('(a+)+$', 'a' * 40 + 'b')", {}, timeout_seconds=1)
        self.assertFalse(outcome['ok'])
        self.assertEqual('limit', outcome['kind'])
