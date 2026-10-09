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

"""The workflow library: the shipped workflows, what each needs, and how
the authoring guide refers to them."""

from unittest import TestCase
from unittest.mock import patch

from app.business.ai_workflows import ai_workflows_library
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_render
from app.iris_engine.ai_workflows.library import ai_workflows_library_entries
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow

_CATALOGUE = {'node_types': [], 'trigger_types': [], 'hooks': [], 'entity_types': [], 'tools': [], 'keystore': []}


class TestsAiWorkflowsLibraryEntries(TestCase):

    def test_every_shipped_workflow_should_be_listed(self):
        self.assertEqual(['detection_rule_feedback', 'virustotal_ioc_enrichment', 'ioc_estate_hunt', 'case_kickoff',
                          'alert_sla_watchdog', 'shift_handover', 'alert_triage'],
                         [e['id'] for e in ai_workflows_library_entries()])

    def test_blocks_should_not_be_listed(self):
        self.assertNotIn('virustotal_lookup', [e['id'] for e in ai_workflows_library_entries()])

    def test_entries_should_be_sorted_by_category_then_name(self):
        entries = ai_workflows_library_entries()
        keys = [(e['category'].lower(), e['name'].lower()) for e in entries]
        self.assertEqual(sorted(keys), keys)
        self.assertEqual({'Detection engineering', 'Enrichment', 'Hunting', 'Investigation', 'Operations', 'Triage'},
                         {e['category'] for e in entries})

    def test_entries_should_tell_what_each_workflow_is(self):
        entries = {e['id']: e for e in ai_workflows_library_entries()}
        triage = entries['alert_triage']
        self.assertEqual(('alert_triage.workflow.json', 'Alert triage first pass', 'event', True),
                         (triage['file'], triage['name'], triage['trigger_type'], triage['uses_ai']))
        self.assertEqual(['on_postload_alert_create'], triage['trigger_config']['hooks'])
        self.assertEqual(triage['document']['requirements'], triage['requirements'])
        self.assertFalse(entries['alert_sla_watchdog']['uses_ai'])
        self.assertEqual(['SIEM_API_KEY'], entries['ioc_estate_hunt']['requirements']['keystore'])

    def test_documents_should_import_as_they_are(self):
        for entry in ai_workflows_library_entries():
            body = ai_workflows_portable_read_workflow(entry['document'])
            self.assertEqual(entry['name'], body['name'])
            self.assertNotIn('category', body)

    def test_missing_category_should_be_other(self):
        examples = [{'file': 'x.workflow.json', 'kind': 'workflow', 'name': 'X',
                     'document': {'format': 'iris-ai-workflow', 'workflow': {'name': 'X', 'graph': {}}}}]
        with patch('app.iris_engine.ai_workflows.library.ai_workflows_guide_examples', return_value=examples):
            [entry] = ai_workflows_library_entries()
        self.assertEqual(('x', 'Other', False, {'keystore': [], 'tools': []}),
                         (entry['id'], entry['category'], entry['uses_ai'], entry['requirements']))


class TestsAiWorkflowsLibraryBusiness(TestCase):

    def test_entries_should_carry_what_the_caller_lacks(self):
        calls = []

        def _warnings(user_id, nodes, allowlist):
            calls.append((user_id, len(nodes), allowlist))
            return [{'field': 'keystore', 'message': 'missing'}] if any(
                n['type'] == 'http_request' for n in nodes) else []

        with patch('app.business.ai_workflows.ai_workflows_import_warnings', _warnings):
            entries = {e['id']: e for e in ai_workflows_library(5)}
        self.assertEqual([{'field': 'keystore', 'message': 'missing'}], entries['ioc_estate_hunt']['warnings'])
        self.assertEqual([], entries['case_kickoff']['warnings'])
        self.assertEqual({5}, {c[0] for c in calls})
        self.assertEqual(len(entries), len(calls))


class TestsAiWorkflowsGuideLibrary(TestCase):

    def test_guide_should_embed_the_virustotal_pair_and_list_the_rest(self):
        guide = ai_workflows_guide_render(_CATALOGUE)
        shipped, library = guide.split('## Workflow library')
        self.assertIn('### VirusTotal IOC enrichment (`virustotal_ioc_enrichment.workflow.json`, workflow)', shipped)
        self.assertIn('(`virustotal_lookup.block.json`, block)', shipped)
        self.assertIn('`GET /api/v2/ai-workflows/library`', library)
        for example in ai_workflows_guide_examples():
            if example['file'] in ('virustotal_ioc_enrichment.workflow.json', 'virustotal_lookup.block.json'):
                self.assertNotIn(example['file'], library)
            else:
                self.assertIn(f'- **{example["name"]}** (`{example["file"]}`, workflow) — ', library)
                self.assertNotIn(example['file'], shipped.split('## Shipped examples')[1])
