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

"""Export and import of workflows, saved blocks, and the events each
node processed (counts, listing, replay). DB helpers and the engine are
patched."""

import datetime
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.ai_workflow_blocks import ai_workflow_blocks_create
from app.business.ai_workflow_blocks import ai_workflow_blocks_delete
from app.business.ai_workflow_blocks import ai_workflow_blocks_export
from app.business.ai_workflow_blocks import ai_workflow_blocks_get
from app.business.ai_workflow_blocks import ai_workflow_blocks_import
from app.business.ai_workflow_blocks import ai_workflow_blocks_list
from app.business.ai_workflow_blocks import ai_workflow_blocks_update
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_export
from app.business.ai_workflows import ai_workflows_import
from app.business.ai_workflows import ai_workflows_node_event
from app.business.ai_workflows import ai_workflows_node_events
from app.business.ai_workflows import ai_workflows_node_stats
from app.business.ai_workflows import ai_workflows_replay_event
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples
from app.models.ai_workflows import AiWorkflowBlock
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from tests.app.business.test_ai_workflows import _ADMIN
from tests.app.business.test_ai_workflows import _BUSINESS
from tests.app.business.test_ai_workflows import _OTHER
from tests.app.business.test_ai_workflows import _OWNER
from tests.app.business.test_ai_workflows import _WorkflowsTestCase
from tests.app.business.test_ai_workflows import _body
from tests.app.business.test_ai_workflows import _stored_workflow

_BLOCKS = 'app.business.ai_workflow_blocks'
_TOKEN = 'abcdef0123456789abcdef'
_NOW = datetime.datetime(2026, 10, 9, 10, 0)


def _http(node_id='call', **config):
    return {'id': node_id, 'type': 'http_request', 'config': {'method': 'GET', 'url': 'https://x.example.org',
                                                              **config}}


def _graph():
    return {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}},
                      _http(auth_type='bearer', auth_secret=_TOKEN),
                      _http('vt', headers=[{'name': 'x-apikey', 'value': '{{ key("VT") }}', 'secret': True}])],
            'edges': [{'id': 'e1', 'source': 'trigger', 'target': 'call', 'source_port': 'out'}]}


class _PortableTestCase(_WorkflowsTestCase):

    def setUp(self):
        super().setUp()
        self.keystore = []
        for target, replacement in ((f'{_BUSINESS}.ai_workflows_keystore_visible_entries',
                                     lambda _user_id: self.keystore),
                                    (f'{_BUSINESS}.ai_workflows_db_utcnow', lambda: _NOW)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)


class TestsWorkflowExportImport(_PortableTestCase):

    def test_export_should_scrub_secrets(self):
        self.workflows[3] = _stored_workflow(graph=_graph(), trigger_config={'inbound_token': 'x'})
        document = ai_workflows_export(3, _OWNER, False)
        self.assertNotIn(_TOKEN, str(document))
        self.assertNotIn('inbound_token', document['workflow']['trigger_config'])
        self.assertEqual(['CALL_AUTH_SECRET', 'VT'], document['requirements']['keystore'])
        self.assertEqual(_NOW.isoformat(), document['exported_at'])

    def test_export_of_a_workflow_of_someone_else_should_be_not_found(self):
        self.workflows[3] = _stored_workflow()
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_export(3, _OTHER, False)
        self.assertEqual('iris-ai-workflow', ai_workflows_export(3, _ADMIN, True)['format'])

    def test_import_should_create_an_inactive_workflow_owned_by_the_importer(self):
        self.keystore = [SimpleNamespace(name='VT')]
        document = {'format': 'iris-ai-workflow', 'format_version': 1,
                    'workflow': _body(graph=_graph(), is_active=True, owner_id=_OTHER)}
        result = ai_workflows_import(document, _OWNER, False)
        self.assertFalse(result['workflow']['is_active'])
        self.assertEqual(_OWNER, result['workflow']['owner_id'])
        self.assertEqual([('call', 'auth_secret')], [(w['node_id'], w['field']) for w in result['warnings']])

    def test_import_should_warn_about_missing_keystore_entries(self):
        result = ai_workflows_import(_body(graph=_graph()), _OWNER, False)
        self.assertTrue(any(w['field'] == 'keystore' and 'VT' in w['message'] for w in result['warnings']))

    def test_shipped_examples_should_import(self):
        tools = [{'name': name, 'classification': 'write' if 'update' in name else 'read'}
                 for name in ('iris_case_iocs_get', 'iris_case_iocs_update')]
        with patch(f'{_BUSINESS}.ai_workflows_tools_catalogue', return_value=tools):
            for example in ai_workflows_guide_examples():
                if example['kind'] != 'workflow':
                    continue
                with self.subTest(example['file']):
                    result = ai_workflows_import(example['document'], _OWNER, False)
                    self.assertFalse(result['workflow']['is_active'])

    def test_unreadable_document_should_be_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_import({'format': 'iris-ai-workflow-block', 'format_version': 1, 'block': {}}, _OWNER,
                                False)


def _block(block_id=5, owner_id=_OWNER, is_shared=False, nodes=None):
    block = AiWorkflowBlock(name='Lookup', description=None, category='enrichment', is_shared=is_shared,
                            owner_id=owner_id, definition={'nodes': nodes or [_http('vt')], 'edges': []})
    block.id = block_id
    block.uuid = uuid.uuid4()
    return block


class TestsBlocks(_PortableTestCase):

    def setUp(self):
        super().setUp()
        self.blocks = {}
        self.deleted = []
        for target, replacement in (
                (f'{_BLOCKS}.ai_workflows_blocks_db_get', lambda i: self.blocks.get(i)),
                (f'{_BLOCKS}.ai_workflows_blocks_db_list',
                 lambda user_id=None: [b for b in self.blocks.values()
                                       if user_id is None or b.owner_id == user_id or b.is_shared]),
                (f'{_BLOCKS}.ai_workflows_db_add', self.added.append),
                (f'{_BLOCKS}.ai_workflows_db_commit', lambda: None),
                (f'{_BLOCKS}.ai_workflows_db_delete', self.deleted.append),
                (f'{_BLOCKS}.ai_workflows_db_user_summary', lambda _ids: {}),
                (f'{_BLOCKS}.ai_workflows_db_utcnow', lambda: _NOW),
                (f'{_BLOCKS}.track_activity', MagicMock())):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_create_should_refuse_a_literal_credential(self):
        body = {'name': 'Lookup', 'definition': {'nodes': [_http(auth_type='bearer', auth_secret=_TOKEN)],
                                                 'edges': []}}
        with self.assertRaises(BusinessProcessingError) as context:
            ai_workflow_blocks_create(body, _OWNER, False)
        self.assertEqual('auth_secret', context.exception.get_data()['errors'][0]['field'])

    def test_create_should_refuse_a_trigger_node(self):
        body = {'name': 'Lookup', 'definition': {'nodes': [{'id': 't', 'type': 'trigger', 'config': {}}],
                                                 'edges': []}}
        with self.assertRaises(BusinessProcessingError):
            ai_workflow_blocks_create(body, _OWNER, False)

    def test_create_should_give_the_requirements(self):
        body = {'name': ' Lookup ', 'definition': {'nodes': [_http('vt', headers=[
            {'name': 'x-apikey', 'value': '{{ key("VT") }}', 'secret': True}])], 'edges': []}}
        data = ai_workflow_blocks_create(body, _OWNER, False)
        self.assertEqual(('Lookup', _OWNER, False), (data['name'], data['owner_id'], data['is_shared']))
        self.assertEqual({'keystore': ['VT'], 'tools': []}, data['requirements'])
        self.assertTrue(data['can_edit'])

    def test_private_blocks_should_be_hidden_and_shared_ones_read_only(self):
        self.blocks = {5: _block(5), 6: _block(6, is_shared=True)}
        self.assertEqual([6], [b['id'] for b in ai_workflow_blocks_list(_OTHER, False)])
        self.assertEqual([5, 6], [b['id'] for b in ai_workflow_blocks_list(_ADMIN, True)])
        with self.assertRaises(ObjectNotFoundError):
            ai_workflow_blocks_get(5, _OTHER, False)
        self.assertFalse(ai_workflow_blocks_get(6, _OTHER, False)['can_edit'])
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflow_blocks_update(6, {'name': 'Mine'}, _OTHER, False)
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflow_blocks_delete(6, _OTHER, False)

    def test_owner_should_update_and_delete(self):
        self.blocks = {5: _block(5)}
        self.assertEqual('Renamed', ai_workflow_blocks_update(5, {'name': 'Renamed'}, _OWNER, False)['name'])
        ai_workflow_blocks_delete(5, _OWNER, False)
        self.assertEqual([self.blocks[5]], self.deleted)

    def test_export_then_import_should_give_a_private_copy(self):
        self.blocks = {6: _block(6, owner_id=_OTHER, is_shared=True)}
        document = ai_workflow_blocks_export(6, _OWNER, False)
        self.assertEqual('iris-ai-workflow-block', document['format'])
        result = ai_workflow_blocks_import(document, _OWNER, False)
        self.assertEqual((_OWNER, False), (result['block']['owner_id'], result['block']['is_shared']))
        self.assertEqual(document['block']['nodes'], result['block']['definition']['nodes'])


def _step(step_id, node_id='py', status='succeeded', run_id=1, seq=2):
    return SimpleNamespace(id=step_id, seq=seq, node_id=node_id, node_type='python', node_label=None,
                           status=status, input={}, output={'result': step_id}, port='out', error=None,
                           tokens_used=0, started_at=_NOW, ended_at=_NOW, run_id=run_id)


def _source_run(run_id=1, entity_id=4, is_dry_run=False):
    return SimpleNamespace(id=run_id, uuid=uuid.uuid4(), workflow_id=3, workflow_name='Stored', workflow_version=1,
                           status='failed', trigger_type='manual', entity_type='alert', entity_id=entity_id,
                           sub_entity=None, run_as_user_id=_OWNER, triggered_by_user_id=_OWNER, owner_id=_OWNER,
                           is_dry_run=is_dry_run, tokens_used=0, step_count=2, error=None, waiting_node_id=None,
                           started_at=_NOW, updated_at=_NOW, finished_at=_NOW, chain_depth=0, parent_run_id=None,
                           replayed_from_step_id=None, workflow=SimpleNamespace(owner_id=_OWNER),
                           definition_snapshot={'owner_id': _OWNER})


class TestsNodeEvents(_PortableTestCase):

    def setUp(self):
        super().setUp()
        self.workflows[3] = _stored_workflow(graph={'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}},
                                                              {'id': 'py', 'type': 'python', 'config': {}}],
                                                    'edges': []})
        self.runs = {1: _source_run(1, entity_id=4), 2: _source_run(2, entity_id=5)}
        self.steps = {10: _step(10, run_id=1), 11: _step(11, run_id=2), 12: _step(12, status='resumed')}
        self.replay = MagicMock(return_value=_source_run(9))
        self.replay_context = MagicMock(return_value={'vars': {}})
        candidates = [SimpleNamespace(id=11, entity_type='alert', entity_id=5),
                      SimpleNamespace(id=10, entity_type='alert', entity_id=4)]
        self.stats = MagicMock(return_value={'py': {'counts': {'succeeded': 3, 'failed': 1}, 'last_at': _NOW}})
        self.candidates = MagicMock(return_value=candidates)
        for target, replacement in (
                ('ai_workflows_business_db_node_stats', self.stats),
                ('ai_workflows_business_db_node_step_candidates', self.candidates),
                ('ai_workflows_business_db_steps_by_ids', lambda ids: [self.steps[i] for i in ids]),
                ('ai_workflows_business_db_runs_by_ids', lambda ids: [self.runs[i] for i in ids]),
                ('ai_workflows_db_get_step', lambda i: self.steps.get(i)),
                ('ai_workflows_db_get_run', lambda i: self.runs.get(i)),
                ('ai_workflows_db_list_steps', lambda _run_id: list(self.steps.values())),
                ('ai_workflows_db_entity_title', lambda _type, _id: 'Alert'),
                ('ai_workflows_engine_replay_step', self.replay),
                ('ai_workflows_engine_replay_context', self.replay_context)):
            patcher = patch(f'{_BUSINESS}.{target}', replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stats_should_count_the_events_of_each_node(self):
        stats = ai_workflows_node_stats(3, _OWNER, False)
        self.assertEqual({'total': 4, 'counts': {'succeeded': 3, 'failed': 1}, 'last_at': _NOW.isoformat()},
                         stats['nodes']['py'])
        self.assertEqual(_OWNER, self.stats.call_args.args[1])
        ai_workflows_node_stats(3, _ADMIN, True)
        self.assertIsNone(self.stats.call_args.args[1])
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_node_stats(3, _OTHER, False)

    def test_events_should_only_list_the_runs_the_user_is_involved_in(self):
        ai_workflows_node_events(3, 'py', _OWNER, False)
        self.assertEqual(_OWNER, self.candidates.call_args.kwargs['involved_user_id'])
        ai_workflows_node_events(3, 'py', _ADMIN, True)
        self.assertIsNone(self.candidates.call_args.kwargs['involved_user_id'])

    def test_events_should_be_paginated_newest_first(self):
        page = ai_workflows_node_events(3, 'py', _OWNER, False, per_page=1)
        self.assertEqual((2, [11], 2), (page['total'], [e['id'] for e in page['data']], page['next_page']))
        self.assertEqual(2, page['data'][0]['run']['id'])
        self.assertEqual([10], [e['id'] for e in ai_workflows_node_events(3, 'py', _OWNER, False, page=2,
                                                                         per_page=1)['data']])

    def test_events_on_entities_the_user_cannot_access_should_be_hidden(self):
        self.can_access.side_effect = lambda _user, _type, entity_id: entity_id == 4
        page = ai_workflows_node_events(3, 'py', _OWNER, False)
        self.assertEqual((1, [10]), (page['total'], [e['id'] for e in page['data']]))
        self.assertEqual(2, ai_workflows_node_events(3, 'py', _ADMIN, True)['total'])

    def test_unknown_status_filter_should_be_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_node_events(3, 'py', _OWNER, False, status='resumed')

    def test_event_should_hold_the_context_the_node_saw(self):
        event = ai_workflows_node_event(3, 'py', 10, _OWNER, False)
        self.assertEqual((10, {'vars': {}}), (event['id'], event['context']))

    def test_event_of_another_node_or_a_resumed_row_should_be_not_found(self):
        for node_id, step_id in (('other', 10), ('py', 12), ('py', 99)):
            with self.subTest(step_id=step_id), self.assertRaises(ObjectNotFoundError):
                ai_workflows_node_event(3, node_id, step_id, _OWNER, False)

    def test_replay_should_start_a_run_from_the_node(self):
        result = ai_workflows_replay_event(3, 'py', 10, {}, _OWNER, False)
        self.assertEqual(9, result['id'])
        _workflow, source, step, _steps = self.replay.call_args.args
        self.assertEqual((1, 10), (source.id, step.id))
        self.assertEqual((_OWNER, False), (self.replay.call_args.kwargs['triggered_by_user_id'],
                                           self.replay.call_args.kwargs['dry_run']))

    def test_replay_should_default_to_the_dry_run_flag_of_the_source(self):
        self.runs[1].is_dry_run = True
        ai_workflows_replay_event(3, 'py', 10, {}, _OWNER, False)
        self.assertTrue(self.replay.call_args.kwargs['dry_run'])
        ai_workflows_replay_event(3, 'py', 10, {'dry_run': False}, _OWNER, False)
        self.assertFalse(self.replay.call_args.kwargs['dry_run'])

    def test_replay_should_be_refused_when_the_node_is_gone_or_the_body_is_wrong(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_replay_event(3, 'py', 10, {'dry_run': 'yes'}, _OWNER, False)
        self.workflows[3].graph = {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}}], 'edges': []}
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_replay_event(3, 'py', 10, {}, _OWNER, False)
        self.replay.assert_not_called()

    def test_replay_on_an_entity_the_user_lost_should_be_not_found(self):
        self.can_access.return_value = False
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_replay_event(3, 'py', 10, {}, _OWNER, False)
