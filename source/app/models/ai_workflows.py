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

"""AI workflows.

An `AiWorkflow` is a node graph (trigger → AI agent / condition / HTTP
call / ask analyst / suggest …) executed by the Celery worker as a
durable state machine: every node execution is one `AiWorkflowRunStep`,
waiting nodes park the run on an `AiWorkflowWait` and never block a
worker.

Runs act as a real user — the workflow owner, whatever the trigger (a
manual run by someone else still acts as the owner, whose permissions the
results are written under) — so the permission
and case / war room / customer boundaries of that user bound everything
the run can read or change. Write tools outside the workflow allowlist
never execute: they become `AiSuggestion` rows an analyst accepts (the
action then runs as that analyst).

Traceability: the run keeps the definition snapshot it executed, each
step its input and output, each tool call its arguments, result and
acting user, each LLM call its request and response. None of these rows
is editable through the API; they only go away with the retention purge.

`AiKeystoreEntry` holds the keys and values flows use (API tokens for
external systems, tenant ids…). Secret values are Fernet-encrypted, never
returned by the API, never shown to the model and masked in every log.
"""

import uuid

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import db


TRIGGER_EVENT = 'event'
TRIGGER_CRON = 'cron'
TRIGGER_MANUAL = 'manual'
TRIGGER_WEBHOOK = 'webhook'
TRIGGER_TYPES = (TRIGGER_EVENT, TRIGGER_CRON, TRIGGER_MANUAL, TRIGGER_WEBHOOK)

RUN_RUNNING = 'running'
RUN_WAITING = 'waiting'
RUN_SUCCEEDED = 'succeeded'
RUN_FAILED = 'failed'
RUN_CANCELLED = 'cancelled'
RUN_SKIPPED = 'skipped'
RUN_STATUSES = (RUN_RUNNING, RUN_WAITING, RUN_SUCCEEDED, RUN_FAILED, RUN_CANCELLED, RUN_SKIPPED)
RUN_ACTIVE_STATUSES = (RUN_RUNNING, RUN_WAITING)

STEP_SUCCEEDED = 'succeeded'
STEP_FAILED = 'failed'
STEP_WAITING = 'waiting'
STEP_RESUMED = 'resumed'

EXEC_AUTO_READ = 'auto_read'
EXEC_ALLOWLISTED_WRITE = 'allowlisted_write'
EXEC_SUGGESTED = 'suggested'
EXEC_ACCEPTED_BY_USER = 'accepted_by_user'
EXEC_DENIED = 'denied'

WAIT_CALLBACK = 'callback'
WAIT_USER_INPUT = 'user_input'
WAIT_DELAY = 'delay'

WAIT_PENDING = 'pending'
WAIT_RESOLVED = 'resolved'
WAIT_EXPIRED = 'expired'
WAIT_CANCELLED = 'cancelled'

ENTITY_ALERT = 'alert'
ENTITY_ALERT_CLUSTER = 'alert_cluster'
ENTITY_CASE = 'case'
ENTITY_WAR_ROOM = 'war_room'
ENTITY_TYPES = (ENTITY_ALERT, ENTITY_ALERT_CLUSTER, ENTITY_CASE, ENTITY_WAR_ROOM)

SUGGESTION_CREATE_CASE = 'create_case'
SUGGESTION_MERGE_INTO_CASE = 'merge_into_case'
SUGGESTION_RELATED_ALERTS = 'related_alerts'
SUGGESTION_DRAFT_REPLY = 'draft_reply'
SUGGESTION_INFO_REQUEST = 'info_request'
SUGGESTION_GENERIC_ACTION = 'generic_action'
SUGGESTION_KINDS = (SUGGESTION_CREATE_CASE, SUGGESTION_MERGE_INTO_CASE, SUGGESTION_RELATED_ALERTS,
                    SUGGESTION_DRAFT_REPLY, SUGGESTION_INFO_REQUEST, SUGGESTION_GENERIC_ACTION)

SUGGESTION_OPEN = 'open'
SUGGESTION_ACCEPTED = 'accepted'
SUGGESTION_DISMISSED = 'dismissed'
SUGGESTION_EXPIRED = 'expired'
SUGGESTION_DRY_RUN = 'dry_run'

AUDIENCE_ENTITY = 'entity'
AUDIENCE_OWNER = 'owner'
AUDIENCES = (AUDIENCE_ENTITY, AUDIENCE_OWNER)

KEYSTORE_PERSONAL = 'personal'
KEYSTORE_SHARED = 'shared'
KEYSTORE_SCOPES = (KEYSTORE_PERSONAL, KEYSTORE_SHARED)

INBOUND_TRIGGER = 'trigger'
INBOUND_CALLBACK = 'callback'


class AiWorkflow(db.Model):
    __tablename__ = 'ai_workflow'

    id = Column(BigInteger, primary_key=True)
    uuid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)

    trigger_type = Column(String(16), nullable=False, default=TRIGGER_MANUAL)
    # event: {hooks: [...], condition, dedup_window_seconds, skip_if_active}
    # cron: {cron, target: none|war_rooms|open_cases, max_targets}
    # webhook: {hmac_keystore_entry}
    trigger_config = Column(JSONB, nullable=False, default=dict)
    # Customer ids the workflow is limited to; null/empty = every customer
    # the owner can access
    customer_scope = Column(JSONB, nullable=True)
    # {nodes: [{id, type, label, config, position}], edges: [{id, source, target, sourceHandle}]}
    graph = Column(JSONB, nullable=False, default=dict)

    # The user event / cron / webhook runs act as
    owner_id = Column(ForeignKey('user.id', ondelete='RESTRICT'), nullable=False)
    # MCP write tools the agent and action nodes may execute directly
    write_tool_allowlist = Column(JSONB, nullable=False, default=list)
    max_runs_per_hour = Column(Integer, nullable=False, default=60)
    token_budget_per_run = Column(Integer, nullable=False, default=50000)
    suggestion_audience = Column(String(16), nullable=False, default=AUDIENCE_ENTITY)

    version = Column(Integer, nullable=False, default=1)
    # sha256 of the bearer token inbound webhook triggers present
    inbound_token_hash = Column(String(64), nullable=True)
    # Fernet-encrypted HMAC key for X-IRIS-Signature, distinct from the
    # bearer token; shown once at rotation
    inbound_signing_secret = Column(Text, nullable=True)
    last_fired_at = Column(DateTime, nullable=True)
    # Suppressed triggers (dedup, still active, hourly cap, chain depth) are
    # counted here rather than stored as full run rows
    skipped_count = Column(BigInteger, nullable=False, default=0)
    last_skip_reason = Column(String(64), nullable=True)
    last_skipped_at = Column(DateTime, nullable=True)

    created_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    owner = relationship('User', foreign_keys=[owner_id])
    created_by = relationship('User', foreign_keys=[created_by_id])


class AiWorkflowVersion(db.Model):
    """Definition history: one row per saved version, never updated."""
    __tablename__ = 'ai_workflow_version'
    __table_args__ = (UniqueConstraint('workflow_id', 'version', name='uq_ai_workflow_version'),)

    id = Column(BigInteger, primary_key=True)
    workflow_id = Column(ForeignKey('ai_workflow.id', ondelete='CASCADE'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    note = Column(Text, nullable=True)
    created_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    created_by = relationship('User')


class AiWorkflowRun(db.Model):
    __tablename__ = 'ai_workflow_run'
    __table_args__ = (
        Index('ix_ai_workflow_run_workflow_started', 'workflow_id', 'started_at'),
        Index('ix_ai_workflow_run_entity', 'entity_type', 'entity_id'),
        Index('ix_ai_workflow_run_status', 'status'),
        Index('ix_ai_workflow_run_dedup', 'workflow_id', 'dedup_key'),
        Index('ix_ai_workflow_run_workflow_started_counted', 'workflow_id', 'started_at',
              postgresql_where=text("status <> 'skipped'")),
        Index('ix_ai_workflow_run_parent', 'parent_run_id'),
    )

    id = Column(BigInteger, primary_key=True)
    uuid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    workflow_id = Column(ForeignKey('ai_workflow.id', ondelete='SET NULL'), nullable=True)
    workflow_name = Column(String(255), nullable=False)
    workflow_version = Column(Integer, nullable=False)
    # The exact definition this run executes; edits never affect it
    definition_snapshot = Column(JSONB, nullable=False)

    status = Column(String(16), nullable=False, default=RUN_RUNNING)
    trigger_type = Column(String(16), nullable=False)
    trigger_payload = Column(JSONB, nullable=True)
    # {trigger, entity, nodes: {<id>: {output, port}}, vars, last}
    context = Column(JSONB, nullable=False, default=dict)
    # Node ids still to execute, in order
    pending_nodes = Column(JSONB, nullable=False, default=list)
    waiting_node_id = Column(String(64), nullable=True)
    # Set while a worker executes a step; a tick re-enqueues stale ones
    is_executing = Column(Boolean, nullable=False, default=False)
    executing_since = Column(DateTime, nullable=True)

    entity_type = Column(String(32), nullable=True)
    entity_id = Column(BigInteger, nullable=True)
    # Finer-grained object inside the entity, e.g. {type: war_room_task, id}
    sub_entity = Column(JSONB, nullable=True)
    customer_id = Column(ForeignKey('client.client_id', ondelete='SET NULL'), nullable=True)

    run_as_user_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    # Workflow owner when the run started: who may read the run even after
    # an ownership transfer
    owner_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    # API key scope mask of the triggering credential, ANDed into the run
    # identity's permissions; null = unrestricted
    scope_mask = Column(BigInteger, nullable=True)
    triggered_by_user_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    dedup_key = Column(String(255), nullable=True)
    chain_depth = Column(Integer, nullable=False, default=0)
    parent_run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='SET NULL'), nullable=True)
    is_dry_run = Column(Boolean, nullable=False, default=False)
    # Keystore names resolved so far: their allowed_hosts bind every later
    # HTTP node and their values are masked everywhere in the run
    used_key_names = Column(JSONB, nullable=False, default=list)
    requeued_at = Column(DateTime, nullable=True)

    tokens_used = Column(Integer, nullable=False, default=0)
    step_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    finished_at = Column(DateTime, nullable=True)

    workflow = relationship('AiWorkflow')


class AiWorkflowRunStep(db.Model):
    """One node execution (append-only ledger)."""
    __tablename__ = 'ai_workflow_run_step'
    __table_args__ = (Index('ix_ai_workflow_run_step_run_seq', 'run_id', 'seq'),)

    id = Column(BigInteger, primary_key=True)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='CASCADE'), nullable=False)
    seq = Column(Integer, nullable=False)
    node_id = Column(String(64), nullable=False)
    node_type = Column(String(32), nullable=False)
    node_label = Column(String(255), nullable=True)
    status = Column(String(16), nullable=False)
    input = Column(JSONB, nullable=True)
    output = Column(JSONB, nullable=True)
    port = Column(String(32), nullable=True)
    error = Column(Text, nullable=True)
    tokens_used = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)


class AiWorkflowToolCall(db.Model):
    __tablename__ = 'ai_workflow_tool_call'
    __table_args__ = (Index('ix_ai_workflow_tool_call_run', 'run_id'),
                      Index('ix_ai_workflow_tool_call_step', 'step_id'),
                      Index('ix_ai_workflow_tool_call_suggestion', 'suggestion_id'),
                      Index('ix_ai_workflow_tool_call_created', 'created_at'))

    id = Column(BigInteger, primary_key=True)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='CASCADE'), nullable=True)
    step_id = Column(ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'), nullable=True)
    suggestion_id = Column(ForeignKey('ai_suggestion.id', ondelete='SET NULL'), nullable=True)
    tool_name = Column(String(128), nullable=False)
    arguments = Column(JSONB, nullable=True)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    classification = Column(String(16), nullable=False)
    execution_mode = Column(String(32), nullable=False)
    acting_user_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class AiWorkflowLlmCall(db.Model):
    """LLM egress record: what left IRIS and what came back."""
    __tablename__ = 'ai_workflow_llm_call'
    __table_args__ = (Index('ix_ai_workflow_llm_call_run', 'run_id'),
                      Index('ix_ai_workflow_llm_call_step', 'step_id'),
                      Index('ix_ai_workflow_llm_call_created', 'created_at'))

    id = Column(BigInteger, primary_key=True)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='CASCADE'), nullable=True)
    step_id = Column(ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'), nullable=True)
    user_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    provider = Column(String(32), nullable=True)
    model = Column(String(128), nullable=True)
    policy_id = Column(BigInteger, nullable=True)
    restriction_level = Column(String(32), nullable=True)
    redacted = Column(Boolean, nullable=False, default=False)
    request_snapshot = Column(JSONB, nullable=True)
    response_snapshot = Column(JSONB, nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    bytes_sent = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class AiWorkflowWait(db.Model):
    __tablename__ = 'ai_workflow_wait'
    __table_args__ = (Index('ix_ai_workflow_wait_status_expires', 'status', 'expires_at'),)

    id = Column(BigInteger, primary_key=True)
    uuid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='CASCADE'), nullable=False, index=True)
    node_id = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False)
    # sha256 of the callback token; never the token itself
    token_hash = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default=WAIT_PENDING)
    expires_at = Column(DateTime, nullable=True)
    resolved_payload = Column(JSONB, nullable=True)
    resolved_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    source_ip = Column(String(64), nullable=True)
    payload_sha256 = Column(String(64), nullable=True)
    suggestion_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    run = relationship('AiWorkflowRun')


class AiSuggestion(db.Model):
    __tablename__ = 'ai_suggestion'
    __table_args__ = (
        Index('ix_ai_suggestion_entity_status', 'entity_type', 'entity_id', 'status'),
        Index('ix_ai_suggestion_run', 'run_id'),
        Index('ix_ai_suggestion_step', 'step_id'),
        Index('ix_ai_suggestion_wait', 'wait_id'),
    )

    id = Column(BigInteger, primary_key=True)
    uuid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='SET NULL'), nullable=True)
    step_id = Column(ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'), nullable=True)
    workflow_id = Column(ForeignKey('ai_workflow.id', ondelete='SET NULL'), nullable=True)

    entity_type = Column(String(32), nullable=True)
    entity_id = Column(BigInteger, nullable=True)
    sub_entity = Column(JSONB, nullable=True)
    case_id = Column(ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=True)
    war_room_id = Column(ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=True)
    alert_id = Column(ForeignKey('alerts.alert_id', ondelete='CASCADE'), nullable=True)
    customer_id = Column(ForeignKey('client.client_id', ondelete='SET NULL'), nullable=True)

    kind = Column(String(32), nullable=False, default=SUGGESTION_GENERIC_ACTION)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=True)
    # {tool, arguments} run as the accepting analyst, or null
    proposed_action = Column(JSONB, nullable=True)
    # info_request fields: [{name, label, type, required, options}]
    form_schema = Column(JSONB, nullable=True)
    # Other objects the suggestion refers to: [{type, id, title}]; the
    # suggestion is only shown to users who can access all of them
    related_refs = Column(JSONB, nullable=True)
    confidence = Column(Float, nullable=True)
    severity = Column(String(16), nullable=True)

    status = Column(String(16), nullable=False, default=SUGGESTION_OPEN)
    wait_id = Column(ForeignKey('ai_workflow_wait.id', ondelete='SET NULL'), nullable=True)
    resolved_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)
    answer = Column(JSONB, nullable=True)
    result = Column(JSONB, nullable=True)
    result_tool_call_id = Column(BigInteger, nullable=True)
    # Users the suggestion targets; null = everyone with entity access
    audience_user_ids = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    resolved_by = relationship('User')
    run = relationship('AiWorkflowRun')


class AiKeystoreEntry(db.Model):
    __tablename__ = 'ai_keystore_entry'
    __table_args__ = (Index('ix_ai_keystore_entry_name', 'name'),)

    id = Column(BigInteger, primary_key=True)
    # Referenced from templates as {{ key('NAME') }}
    name = Column(String(128), nullable=False)
    # Ciphertext when is_secret
    value = Column(Text, nullable=False)
    is_secret = Column(Boolean, nullable=False, default=True)
    description = Column(Text, nullable=True)
    scope = Column(String(16), nullable=False, default=KEYSTORE_PERSONAL)
    # Personal entries: the only user that can use them
    owner_id = Column(ForeignKey('user.id', ondelete='CASCADE'), nullable=True)
    # Shared entries: groups whose members may use them; empty = everyone
    allowed_group_ids = Column(JSONB, nullable=False, default=list)
    # Hosts a secret may be sent to; empty = any host
    allowed_hosts = Column(JSONB, nullable=False, default=list)
    created_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    last_used_at = Column(DateTime, nullable=True)

    owner = relationship('User', foreign_keys=[owner_id])


class AiWorkflowInboundEvent(db.Model):
    """Every inbound request on the public endpoints, accepted or not."""
    __tablename__ = 'ai_workflow_inbound_event'
    __table_args__ = (Index('ix_ai_workflow_inbound_event_created', 'created_at'),
                      Index('ix_ai_workflow_inbound_event_workflow', 'workflow_id', 'created_at'),
                      Index('ix_ai_workflow_inbound_event_wait', 'wait_id'),
                      Index('ix_ai_workflow_inbound_event_run', 'run_id'),
                      Index('ix_ai_workflow_inbound_event_signature', 'signature_sha256'))

    id = Column(BigInteger, primary_key=True)
    kind = Column(String(16), nullable=False)
    workflow_id = Column(ForeignKey('ai_workflow.id', ondelete='SET NULL'), nullable=True)
    wait_id = Column(ForeignKey('ai_workflow_wait.id', ondelete='SET NULL'), nullable=True)
    run_id = Column(ForeignKey('ai_workflow_run.id', ondelete='SET NULL'), nullable=True)
    source_ip = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False)
    reason = Column(Text, nullable=True)
    payload_sha256 = Column(String(64), nullable=True)
    payload_bytes = Column(Integer, nullable=False, default=0)
    # sha256 of an accepted X-IRIS-Signature, for replay rejection
    signature_sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
