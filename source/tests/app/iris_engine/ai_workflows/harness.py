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

"""Shared fixtures of the AI workflow engine tests: an in-memory store
standing in for the persistence layer, a keystore resolver with one
secret, and a scripted LLM provider. Nothing touches the database."""

import contextlib
import itertools
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app as _app
from app.iris_engine.llm.providers.base import MessageEnd
from app.iris_engine.llm.providers.base import TextDelta
from app.iris_engine.llm.providers.base import ToolUseEnd
from app.iris_engine.webhooks.render import MASK
from app.models.ai_workflows import AiWorkflowRun
from app.models.ai_workflows import AiWorkflowRunStep
from app.models.ai_workflows import AiWorkflowWait
from app.models.ai_workflows import WAIT_PENDING

OWNER_ID = 3
SECRET_NAME = 'api_token'
SECRET_VALUE = 'sk-live-0123456789abcdef'

_ENGINE = 'app.iris_engine.ai_workflows.engine'
_NODES = 'app.iris_engine.ai_workflows.nodes'
_AGENT = 'app.iris_engine.ai_workflows.agent'


class FakeResolver:
    """One secret entry; masks it once resolved, like the real one."""

    def __init__(self):
        self.used = set()

    def get(self, name):
        if name != SECRET_NAME:
            raise KeyError(name)
        self.used.add(name)
        return SECRET_VALUE

    def reveal_for_llm(self, name):
        # The real resolver arms masking for placeholder-only secrets too
        self.used.add(name)
        return f'[secret:{name}]'

    def mask(self, obj):
        if not self.used:
            return obj
        if isinstance(obj, str):
            return obj.replace(SECRET_VALUE, MASK)
        if isinstance(obj, dict):
            return {self.mask(k): self.mask(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self.mask(v) for v in obj)
        return obj

    def check_url(self, _url):
        return None


class FakeProvider:
    """Replays one scripted turn (a list of events) per call and keeps
    what it was sent."""

    name = 'fake'

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []

    def stream_completion(self, *, model, system, messages, tools):
        self.calls.append({'model': model, 'system': system, 'messages': list(messages),
                           'tools': [t.name for t in tools]})
        events = self._turns.pop(0) if self._turns else [TextDelta('done'), MessageEnd('end_turn', 1, 1)]
        yield from events


def text_turn(text, tokens=10):
    return [TextDelta(text), MessageEnd('end_turn', tokens, tokens)]


def tool_turn(tool_name, arguments, tool_use_id='tu1'):
    return [ToolUseEnd(tool_use_id, tool_name, arguments), MessageEnd('tool_use', 5, 5)]


class MemoryStore:
    """Runs, steps, waits and suggestions keyed by id."""

    def __init__(self):
        self._ids = itertools.count(1)
        self.runs = {}
        self.steps = {}
        self.waits = {}
        self.suggestions = []
        self.tool_calls = []
        self.llm_calls = []

    def add(self, obj):
        if getattr(obj, 'id', None) is None:
            obj.id = next(self._ids)
        if isinstance(obj, AiWorkflowRun):
            self.runs[obj.id] = obj
        elif isinstance(obj, AiWorkflowRunStep):
            self.steps[obj.id] = obj
        elif isinstance(obj, AiWorkflowWait):
            self.waits[obj.id] = obj
        else:
            self.llm_calls.append(obj)
        return obj

    def get_run(self, run_id, lock=False):
        return self.runs.get(run_id)

    def get_wait(self, wait_id, lock=False):
        return self.waits.get(wait_id)

    def create_wait(self, run_id, node_id, kind, wait_uuid, token_hash, expires_at):
        return self.add(AiWorkflowWait(uuid=wait_uuid, run_id=run_id, node_id=node_id, kind=kind,
                                       token_hash=token_hash, status=WAIT_PENDING, expires_at=expires_at)).id

    def cancel_wait(self, wait_id):
        wait = self.waits.get(wait_id)
        if wait is not None and wait.status == WAIT_PENDING:
            wait.status = 'cancelled'

    def get_step(self, step_id):
        return self.steps.get(step_id)

    def run_steps(self, run_id):
        return sorted((s for s in self.steps.values() if s.run_id == run_id), key=lambda s: s.seq)

    def next_step_seq(self, run_id):
        return len(self.run_steps(run_id)) + 1

    def pending_waits(self, run_id):
        return [w for w in self.waits.values() if w.run_id == run_id and w.status == WAIT_PENDING]

    def steps_with_status(self, run_id, status):
        return [s for s in self.run_steps(run_id) if s.status == status]

    def create_suggestion(self, run, step, **fields):
        suggestion = SimpleNamespace(id=next(self._ids), run_id=run.id, step_id=step.id if step else None,
                                     is_dry_run=bool(run.is_dry_run), **fields)
        self.suggestions.append(suggestion)
        return suggestion

    def record_tool(self, tool_name, arguments, **kwargs):
        mask = kwargs.get('mask') or (lambda value: value)
        row = SimpleNamespace(id=next(self._ids), tool_name=tool_name, arguments=mask(arguments),
                              execution_mode=kwargs.get('execution_mode'),
                              suggestion_id=getattr(kwargs.get('suggestion'), 'id', None))
        self.tool_calls.append(row)
        return row


def workflow(graph, *, trigger_type='manual', trigger_config=None, allowlist=None, workflow_id=11, **extra):
    fields = {
        'id': workflow_id,
        'uuid': None,
        'name': 'Triage',
        'description': None,
        'version': 1,
        'trigger_type': trigger_type,
        'trigger_config': trigger_config or {},
        'customer_scope': [],
        'graph': graph,
        'owner_id': OWNER_ID,
        'write_tool_allowlist': allowlist or [],
        'max_runs_per_hour': 0,
        'token_budget_per_run': 0,
        'suggestion_audience': 'entity',
        'is_active': True,
        'last_fired_at': None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def chain(*nodes, ports=None):
    """A linear graph: trigger -> nodes..., each wired through `out`
    unless `ports[node_id]` says otherwise."""
    ports = ports or {}
    all_nodes = [{'id': 'trigger', 'type': 'trigger', 'config': {}}] + list(nodes)
    edges = []
    for index, (source, target) in enumerate(zip(all_nodes, all_nodes[1:])):
        edges.append({'id': f'e{index}', 'source': source['id'], 'target': target['id'],
                      'source_port': ports.get(source['id'], 'out')})
    return {'nodes': all_nodes, 'edges': edges}


class EngineTestCase(TestCase):
    """Patches the persistence layer, identity, keystore and queue of
    the engine with the in-memory store."""

    def setUp(self):
        self.store = MemoryStore()
        self.resolver = FakeResolver()
        self.enqueued = []
        self.published = []
        self.tool_results = {}
        self.executed = []
        user = SimpleNamespace(id=OWNER_ID, active=True, user='owner', name='Owner', email='o@example.org')
        store = self.store

        def _execute(user_id, tool_name, arguments, **kwargs):
            self.executed.append((tool_name, arguments, kwargs.get('execution_mode')))
            row = store.record_tool(tool_name, arguments, **kwargs)
            return {'ok': True, 'result': self.tool_results.get(tool_name, {'done': True}), 'error': None,
                    'tool_call_id': row.id}

        patches = {
            f'{_ENGINE}.ai_workflows_db_add': store.add,
            f'{_ENGINE}.ai_workflows_db_commit': lambda: None,
            f'{_ENGINE}.ai_workflows_db_get_run': store.get_run,
            f'{_ENGINE}.ai_workflows_db_get_step': store.get_step,
            f'{_ENGINE}.ai_workflows_db_get_wait': store.get_wait,
            f'{_NODES}.ai_workflows_runtime_db_create_wait': store.create_wait,
            f'{_NODES}.ai_workflows_runtime_db_cancel_wait': store.cancel_wait,
            f'{_ENGINE}.ai_workflows_db_get_user': lambda user_id: user if user_id == OWNER_ID else None,
            f'{_ENGINE}.ai_workflows_db_next_step_seq': store.next_step_seq,
            f'{_ENGINE}.ai_workflows_db_open_suggestions_for_wait': lambda _wait_id: [],
            f'{_ENGINE}.ai_workflows_db_pending_waits': store.pending_waits,
            f'{_ENGINE}.ai_workflows_db_settle_session': lambda: False,
            f'{_ENGINE}.ai_workflows_db_steps_with_status': store.steps_with_status,
            f'{_ENGINE}.ai_workflows_entities_customer': lambda _type, _id: 1,
            f'{_ENGINE}.ai_workflows_entities_user_can_access': lambda _user, _type, _id: True,
            f'{_ENGINE}.ai_workflows_keystore_resolver': lambda _user_id: self.resolver,
            f'{_ENGINE}.ai_workflows_identity': lambda _user_id, _run=None: contextlib.nullcontext(),
            f'{_ENGINE}.ai_workflows_suggestions_create': store.create_suggestion,
            f'{_ENGINE}.ai_workflows_suggestions_emit': lambda _suggestion, _action: None,
            f'{_ENGINE}._enqueue': lambda run_id, _countdown=None: self.enqueued.append(run_id),
            f'{_ENGINE}._publish_complete': self.published.append,
            f'{_NODES}.ai_workflows_entities_snapshot': lambda _type, _id: {'id': _id, 'title': 'Phishing'},
            f'{_ENGINE}.ai_workflows_entities_snapshot': lambda _type, _id: {'id': _id, 'title': 'Phishing'},
            f'{_NODES}.ai_workflows_suggestions_create': store.create_suggestion,
            f'{_NODES}.ai_workflows_tools_execute': _execute,
            f'{_NODES}.ai_workflows_tools_record': store.record_tool,
            f'{_AGENT}.ai_workflows_db_add': store.add,
            f'{_AGENT}.ai_workflows_db_commit': lambda: None,
            f'{_AGENT}.ai_workflows_suggestions_create': store.create_suggestion,
            f'{_AGENT}.ai_workflows_tools_execute': _execute,
            f'{_AGENT}.ai_workflows_tools_record': store.record_tool,
            f'{_AGENT}._policy': lambda _run: (None, 0),
            f'{_AGENT}.ai_workflows_db_heartbeat_run': lambda _run_id, _now: True,
            f'{_AGENT}.ai_workflows_db_run_status_fresh': lambda _run_id: 'running',
            f'{_AGENT}._check_daily_budgets': lambda _cfg, _user_id: None,
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        context = _app.app_context()
        context.push()
        self.addCleanup(context.pop)

    def use_provider(self, *turns):
        provider = FakeProvider(turns)
        cfg = SimpleNamespace(enabled=True, model='fake-model', provider='fake', redact_ips=False,
                              redact_emails=False, redact_hashes=False, daily_token_budget_per_user=0,
                              daily_token_budget_org=0)
        for target, replacement in ((f'{_AGENT}.load_config', lambda _policy: cfg),
                                    (f'{_AGENT}.get_llm_provider', lambda _cfg: provider)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return provider

    def start(self, wf, *, entity_type='case', entity_id=42, dry_run=False):
        from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
        return ai_workflows_engine_start_run(wf, 'manual', run_as_user_id=OWNER_ID, triggered_by_user_id=OWNER_ID,
                                             entity_type=entity_type, entity_id=entity_id, dry_run=dry_run)

    def run_to_rest(self, wf, **kwargs):
        from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
        run = self.start(wf, **kwargs)
        for _ in range(10):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def steps(self, run):
        return self.store.run_steps(run.id)
