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

"""The `python` node in a run, and `{"$path": ...}` argument references."""

import json
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.context import ai_workflows_context_render_arguments
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import SECRET_VALUE
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_WRITE_TOOL = 'iris_case_iocs_update'


def _python(node_id, code, inputs=None):
    return {'id': node_id, 'type': 'python', 'config': {'code': code, 'inputs': inputs or []}}


def _in_process(source, variables, max_steps=None, timeout_seconds=None):
    return ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds)


class TestsPythonNode(EngineTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute', _in_process)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_script_output_should_be_the_node_output(self):
        graph = chain(_python('py', "result = {'title': entity['title'].upper(), 'n': inputs['n'] * 2}",
                              inputs=[{'name': 'n', 'value': '{{ run.version }}'}]))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual({'title': 'PHISHING', 'n': 2}, run.context['nodes']['py']['output']['result'])

    def test_failing_script_should_fail_the_run_with_its_line(self):
        run = self.run_to_rest(workflow(chain(_python('py', "x = 1\nfail('bad input')"))))
        self.assertEqual('failed', run.status)
        self.assertIn('line 2', run.error)
        self.assertIn('bad input', run.error)

    def test_failing_script_should_leave_through_the_error_port(self):
        graph = chain(_python('py', 'x = 1 / 0'), {'id': 'end', 'type': 'stop', 'config': {}}, ports={'py': 'error'})
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual('error', self.steps(run)[1].port)

    def test_keystore_should_not_reach_scripts(self):
        graph = chain(_python('py', 'result = inputs',
                              inputs=[{'name': 'k', 'value': f'{{{{ key("{SECRET_NAME}") }}}}'}]))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual({'k': f'[secret:{SECRET_NAME}]'}, run.context['nodes']['py']['output']['result'])
        self.assertNotIn(SECRET_VALUE, json.dumps(run.context, default=str))

    def test_disabled_python_should_fail(self):
        from app import app
        app.config['AI_WORKFLOWS_PYTHON_ENABLED'] = False
        self.addCleanup(app.config.__setitem__, 'AI_WORKFLOWS_PYTHON_ENABLED', True)
        run = self.run_to_rest(workflow(chain(_python('py', 'result = 1'))))
        self.assertEqual('failed', run.status)
        self.assertIn('disabled', run.error)

    def test_path_reference_should_pass_structure_to_a_tool(self):
        graph = chain(_python('py', "result = {'enrichment': {'vt': {'malicious': 3}}, 'tags': 'vt:malicious'}"),
                      {'id': 'act', 'type': 'action',
                       'config': {'tool': _WRITE_TOOL,
                                  'arguments': {'ioc_identifier': '{{ trigger.entity_id }}',
                                                'payload': {'ioc_enrichment': {'$path': 'nodes.py.output.result.'
                                                                                        'enrichment'},
                                                            'ioc_tags': '{{ nodes.py.output.result.tags }}'}}}})
        run = self.run_to_rest(workflow(graph, allowlist=[_WRITE_TOOL]))
        self.assertEqual('succeeded', run.status, run.error)
        tool, arguments, _mode = self.executed[-1]
        self.assertEqual(_WRITE_TOOL, tool)
        self.assertEqual({'vt': {'malicious': 3}}, arguments['payload']['ioc_enrichment'])
        self.assertEqual('vt:malicious', arguments['payload']['ioc_tags'])


class TestsPythonNodeGraph(TestCase):

    def _errors(self, code, **config):
        graph = {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}},
                           {'id': 'py', 'type': 'python', 'config': {'code': code, **config}}],
                 'edges': [{'id': 'e', 'source': 'trigger', 'target': 'py', 'source_port': 'out'}]}
        return [(e['field'], e['message']) for e in ai_workflows_graph_validate(graph, 'manual', {}, [])]

    def test_valid_script_should_pass(self):
        self.assertEqual([], self._errors('result = 1', inputs=[{'name': 'value', 'value': '{{ run.uuid }}'}],
                                          timeout_seconds=5))

    def test_script_errors_should_be_reported_with_their_line(self):
        self.assertIn(('code', 'line 2: Import is not allowed'), self._errors('x = 1\nimport os'))
        self.assertIn(('code', 'Required'), self._errors(''))

    def test_bad_inputs_and_limits_should_be_reported(self):
        errors = self._errors('result = 1', inputs=[{'name': '_x', 'value': ''}], timeout_seconds=600)
        self.assertEqual({'inputs.0.name', 'timeout_seconds'}, {field for field, _m in errors})


class TestsPathReferences(TestCase):

    def test_path_should_give_the_value_with_its_structure(self):
        context = {'nodes': {'a': {'output': {'list': [1, {'x': 2}]}}}, 'key': lambda name: name}
        arguments = {'v': {'$path': 'nodes.a.output.list'}, 'missing': {'$path': 'nodes.b.output'},
                     'fn': {'$path': 'key'}, 'nested': [{'$path': 'nodes.a.output.list.1.x'}]}
        self.assertEqual({'v': [1, {'x': 2}], 'missing': None, 'fn': None, 'nested': [2]},
                         ai_workflows_context_render_arguments(arguments, context, 'arguments'))

    def test_other_dicts_should_still_be_rendered(self):
        context = {'vars': {'a': 'b'}}
        arguments = {'x': {'$path': 'vars.a', 'other': 1}}
        self.assertEqual({'x': {'$path': 'vars.a', 'other': 1}},
                         ai_workflows_context_render_arguments(arguments, context, 'arguments'))

    def test_invalid_path_should_be_refused_by_validation(self):
        graph = {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}},
                           {'id': 'v', 'type': 'set_variables',
                            'config': {'variables': [{'name': 'a', 'value': {'$path': '{{ x }}'}}]}}],
                 'edges': [{'id': 'e', 'source': 'trigger', 'target': 'v', 'source_port': 'out'}]}
        errors = ai_workflows_graph_validate(graph, 'manual', {}, [])
        self.assertEqual(['variables.0.value'], [e['field'] for e in errors])
