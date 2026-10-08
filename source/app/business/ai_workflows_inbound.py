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

"""Unauthenticated inbound requests of AI workflows.

- `callback`: an external system an async `http_request` node called
  answers on `/api/v2/ai-workflows/callbacks/<wait_uuid>` with the
  per-wait bearer token. Single use: only a pending, unexpired wait is
  resolved.
- `trigger`: an external system starts a webhook-triggered workflow on
  `/api/v2/ai-workflows/hooks/<workflow_uuid>` with the workflow inbound
  token. The run acts as the workflow owner.

Both authenticate with a bearer token compared by hash, plus an HMAC
signature over the raw body:

    X-IRIS-Timestamp: <unix seconds>   (within 300 s of now)
    X-IRIS-Signature: sha256=<hex HMAC-SHA256(key, X-IRIS-Timestamp + "." + body)>

The timestamp is signed exactly as sent in the header (the raw string).
- triggers: the key is the workflow signing secret (created with the
  inbound token, rotatable on its own, shown once), never the bearer
  token. The signature is mandatory when the trigger config sets
  `require_signature`, and a signature already accepted for the
  workflow within the tolerance window is refused (replay).
- callbacks: the signature is optional; the key is the callback token.

Every attempt on a known wait / workflow, accepted or not, is logged as
an `AiWorkflowInboundEvent` with the reason; attempts on unknown or
malformed ids only go to the application log. The caller sees a uniform
401 for any token, signature, expiry or unknown-object problem, and a
trigger answers 202 whether or not the workflow owner can access the
entity the payload names (a refused run is recorded as skipped), so
nothing can be probed. JSON bodies are parsed strictly (no NaN /
Infinity, integers within 64 bits, nesting depth at most 32: 400
otherwise); a body that is not JSON is kept as text.
"""

import datetime
import hashlib
import hmac
import json
import math
import time
import uuid

from app import app
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_signature_seen
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_count_runs_since
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_by_uuid
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_wait_by_uuid
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_lock_workflow
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_rollback
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_access_denial
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_hash_token
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_insert_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_lock_run_then_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_skip_run
from app.iris_engine.mail.secrets import decrypt_secret
from app.logger import logger
from app.models.ai_workflows import AiWorkflowInboundEvent
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import INBOUND_CALLBACK
from app.models.ai_workflows import INBOUND_TRIGGER
from app.models.ai_workflows import TRIGGER_WEBHOOK
from app.models.ai_workflows import WAIT_CALLBACK
from app.models.ai_workflows import WAIT_PENDING


INBOUND_ACCEPTED = 'accepted'
INBOUND_REJECTED = 'rejected'
INBOUND_ERROR = 'error'

SIGNATURE_TOLERANCE_SECONDS = 300
_SIGNATURE_PREFIX = 'sha256='
_MAX_TIMESTAMP_CHARS = 32

# Strict JSON
_MAX_JSON_DEPTH = 32
_MAX_JSON_INT = 2 ** 63
_MAX_JSON_INT_CHARS = 20

# A webhook payload bigger than this is stored on the run as a summary
# (size, sha256, text preview): the run row and context stay small
_MAX_STORED_PAYLOAD_BYTES = 256 * 1024
_STORED_PREVIEW_CHARS = 16 * 1024


class AiWorkflowsInboundError(Exception):
    """A refused inbound request. `status` is what the caller sees,
    `reason` is only logged."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class _UnsafeJson(ValueError):
    pass


def ai_workflows_inbound_max_bytes() -> int:
    return int(app.config.get('AI_WORKFLOWS_MAX_INBOUND_BYTES') or 1024 * 1024)


# ---- Verification ----------------------------------------------------------

def _bearer(headers):
    value = (headers.get('Authorization') or '').strip()
    if not value[:7].lower() == 'bearer ':
        return None
    token = value[7:].strip()
    return token or None


def _token_matches(token, expected_hash) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(ai_workflows_engine_hash_token(token), expected_hash)


def ai_workflows_inbound_sign(key, timestamp, raw_body) -> str:
    """`sha256=<hex>`: the value of `X-IRIS-Signature` for this body.
    `timestamp` is the `X-IRIS-Timestamp` header exactly as sent."""
    digest = hmac.new(str(key).encode(), f'{timestamp}.'.encode() + (raw_body or b''), hashlib.sha256).hexdigest()
    return f'{_SIGNATURE_PREFIX}{digest}'


def _check_signature(headers, key, raw_body, required, now=None):
    """sha256 of the verified signature, or None when there was none to
    verify (optional and absent). Raises a 401 when the signature is
    missing (and required), stale or wrong, or when there is no key to
    verify it with."""
    timestamp = headers.get('X-IRIS-Timestamp')
    signature = headers.get('X-IRIS-Signature')
    if not timestamp and not signature:
        if required:
            raise AiWorkflowsInboundError(401, 'signature required but missing')
        return None
    if not timestamp or not signature:
        raise AiWorkflowsInboundError(401, 'incomplete signature headers')
    if not key:
        raise AiWorkflowsInboundError(401, 'signed request but no signing secret is configured')
    timestamp = str(timestamp)
    if len(timestamp) > _MAX_TIMESTAMP_CHARS:
        raise AiWorkflowsInboundError(401, 'invalid signature timestamp')
    try:
        ts = int(timestamp.strip())
    except ValueError:
        raise AiWorkflowsInboundError(401, 'invalid signature timestamp')
    now = int(time.time()) if now is None else now
    if abs(now - ts) > SIGNATURE_TOLERANCE_SECONDS:
        raise AiWorkflowsInboundError(401, 'signature timestamp outside the tolerance')
    expected = ai_workflows_inbound_sign(key, timestamp, raw_body)
    if not hmac.compare_digest(expected.encode(), str(signature).strip().lower().encode()):
        raise AiWorkflowsInboundError(401, 'signature mismatch')
    return hashlib.sha256(expected.encode()).hexdigest()


def _signing_key(workflow):
    encrypted = getattr(workflow, 'inbound_signing_secret', None)
    if not encrypted:
        return None
    return decrypt_secret(encrypted) or None


# ---- Parsing ---------------------------------------------------------------

def _reject_constant(name):
    raise _UnsafeJson(f'{name} is not allowed')


def _parse_int(text):
    if len(text) > _MAX_JSON_INT_CHARS:
        raise _UnsafeJson('integer out of range')
    value = int(text)
    if not -_MAX_JSON_INT <= value < _MAX_JSON_INT:
        raise _UnsafeJson('integer out of range')
    return value


def _parse_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise _UnsafeJson('non-finite number')
    return value


def _too_deep(value) -> bool:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            children = current.values()
        elif isinstance(current, list):
            children = current
        else:
            continue
        if depth > _MAX_JSON_DEPTH:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def ai_workflows_inbound_parse(raw_body):
    """JSON when it parses, else the body as text. Raises a 400 for JSON
    with NaN / Infinity, an integer outside 64 bits, or nesting deeper
    than 32."""
    if not raw_body:
        return None
    text = raw_body.decode('utf-8', errors='replace')
    try:
        value = json.loads(text, parse_constant=_reject_constant, parse_int=_parse_int, parse_float=_parse_float)
    except _UnsafeJson as e:
        raise AiWorkflowsInboundError(400, f'unsafe JSON: {e}')
    except (RecursionError, OverflowError):
        raise AiWorkflowsInboundError(400, 'unsafe JSON: too deep or too large')
    except ValueError:
        return text
    if _too_deep(value):
        raise AiWorkflowsInboundError(400, f'unsafe JSON: nested deeper than {_MAX_JSON_DEPTH}')
    return value


def _stored_payload(payload, raw_body):
    """What the run keeps of the trigger payload."""
    if len(raw_body or b'') <= _MAX_STORED_PAYLOAD_BYTES:
        return payload
    return {
        'truncated': True,
        'bytes': len(raw_body),
        'sha256': hashlib.sha256(raw_body).hexdigest(),
        'preview': raw_body[:_STORED_PREVIEW_CHARS].decode('utf-8', errors='replace'),
    }


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _dig(payload, path):
    current = payload
    for part in str(path).split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


# ---- Logging ---------------------------------------------------------------

def _event(kind, status, reason, source_ip, raw_body, payload_bytes, workflow_id=None, wait_id=None, run_id=None,
           signature_sha256=None) -> AiWorkflowInboundEvent:
    return AiWorkflowInboundEvent(
        kind=kind,
        workflow_id=workflow_id,
        wait_id=wait_id,
        run_id=run_id,
        source_ip=(source_ip or '')[:64] or None,
        status=status,
        reason=(str(reason)[:1000] if reason else None),
        payload_sha256=hashlib.sha256(raw_body or b'').hexdigest() if raw_body is not None else None,
        payload_bytes=payload_bytes or 0,
        signature_sha256=signature_sha256,
    )


def _log(kind, status, reason, source_ip, raw_body, payload_bytes, **context):
    try:
        ai_workflows_db_add(_event(kind, status, reason, source_ip, raw_body, payload_bytes, **context))
        ai_workflows_db_commit()
    except Exception:
        logger.exception('Could not log an AI workflow inbound event')
        ai_workflows_db_rollback()


def _log_unknown(kind, identifier, source_ip):
    """Unknown or malformed ids write no row: a scan cannot fill the
    event table."""
    logger.info(f'AI workflow inbound {kind} refused: unknown {identifier!r:.80} from {source_ip}')


def _check_size(raw_body, content_length):
    """`raw_body` is read up to max + 1 bytes, so a larger length means
    the body is over the cap even if the client lied about it."""
    limit = ai_workflows_inbound_max_bytes()
    if (content_length or 0) > limit or len(raw_body or b'') > limit:
        raise AiWorkflowsInboundError(413, f'payload over {limit} bytes')


# ---- Callback --------------------------------------------------------------

def ai_workflows_inbound_callback(wait_uuid, headers, raw_body, source_ip, content_length=None) -> dict:
    """Resolve a pending callback wait. Returns the 202 body; raises
    AiWorkflowsInboundError otherwise. Logs either way (known waits)."""
    raw_body = raw_body or b''
    size = max(len(raw_body), content_length or 0)
    parsed = _parse_uuid(wait_uuid)
    wait = ai_workflows_db_get_wait_by_uuid(parsed) if parsed is not None else None
    if wait is None:
        _log_unknown(INBOUND_CALLBACK, wait_uuid, source_ip)
        raise AiWorkflowsInboundError(401, 'unknown wait')
    context = {'wait_id': wait.id, 'run_id': wait.run_id,
               'workflow_id': wait.run.workflow_id if wait.run is not None else None}
    try:
        _check_size(raw_body, content_length)
        if not app.config.get('AI_WORKFLOWS_ENABLED', True):
            raise AiWorkflowsInboundError(401, 'AI workflows are disabled')
        if wait.kind != WAIT_CALLBACK:
            raise AiWorkflowsInboundError(401, f'wait is not a callback ({wait.kind})')
        token = _bearer(headers)
        if token is None:
            raise AiWorkflowsInboundError(401, 'missing bearer token')
        if not _token_matches(token, wait.token_hash):
            raise AiWorkflowsInboundError(401, 'bad token')
        _check_signature(headers, token, raw_body, required=False)
        payload = ai_workflows_inbound_parse(raw_body)
        # The run, then the wait; the status is only trusted under the locks
        _run, wait = ai_workflows_engine_lock_run_then_wait(wait.id)
        if wait is None:
            raise AiWorkflowsInboundError(401, 'wait deleted')
        if wait.status != WAIT_PENDING:
            raise AiWorkflowsInboundError(401, f'wait is {wait.status} (replay or late callback)')
        if wait.expires_at is not None and wait.expires_at <= ai_workflows_db_utcnow():
            raise AiWorkflowsInboundError(401, 'wait expired')
        try:
            ai_workflows_engine_resume_wait(wait, payload, source_ip=source_ip)
            ai_workflows_db_commit()
        except Exception:
            logger.exception(f'Resuming AI workflow wait {wait.uuid} from a callback failed')
            ai_workflows_db_rollback()
            raise AiWorkflowsInboundError(500, 'resume failed')
    except AiWorkflowsInboundError as e:
        ai_workflows_db_rollback()
        status = INBOUND_ERROR if e.status >= 500 else INBOUND_REJECTED
        _log(INBOUND_CALLBACK, status, e.reason, source_ip, raw_body, size, **context)
        raise
    _log(INBOUND_CALLBACK, INBOUND_ACCEPTED, None, source_ip, raw_body, size, **context)
    return {'status': 'accepted'}


# ---- Trigger ---------------------------------------------------------------

def _trigger_entity(workflow, payload):
    """(entity_type, entity_id) the trigger config extracts from the
    payload, or (None, None). Whether the entity exists or the owner can
    access it is left to the run start (a refused run is skipped), so the
    answer is the same either way."""
    config = workflow.trigger_config or {}
    entity_type = config.get('entity_type')
    path = config.get('entity_id_path')
    if not entity_type or not path:
        return None, None
    if entity_type not in ENTITY_TYPES:
        raise AiWorkflowsInboundError(400, f'workflow entity type {entity_type} is unknown')
    raw = _dig(payload, path)
    if isinstance(raw, bool) or raw is None:
        raise AiWorkflowsInboundError(400, f'no entity id at {path}')
    try:
        entity_id = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise AiWorkflowsInboundError(400, f'entity id at {path} is not an integer')
    if not -_MAX_JSON_INT <= entity_id < _MAX_JSON_INT:
        raise AiWorkflowsInboundError(400, f'entity id at {path} is out of range')
    return entity_type, entity_id


def _over_rate(workflow) -> bool:
    """`max_runs_per_hour` also bounds what a token holder can start."""
    limit = workflow.max_runs_per_hour or 0
    if limit <= 0:
        return False
    since = ai_workflows_db_utcnow() - datetime.timedelta(hours=1)
    return ai_workflows_db_count_runs_since(workflow.id, since) >= limit


def _check_workflow(workflow, headers):
    token = _bearer(headers)
    if token is None:
        raise AiWorkflowsInboundError(401, 'missing bearer token')
    if not _token_matches(token, workflow.inbound_token_hash):
        raise AiWorkflowsInboundError(401, 'bad token')
    if workflow.trigger_type != TRIGGER_WEBHOOK:
        raise AiWorkflowsInboundError(401, f'workflow trigger is {workflow.trigger_type}, not webhook')
    if not workflow.is_active:
        raise AiWorkflowsInboundError(401, 'workflow is inactive')
    if not app.config.get('AI_WORKFLOWS_ENABLED', True):
        raise AiWorkflowsInboundError(401, 'AI workflows are disabled')


def _start_locked(workflow_id, signature_sha256, denial, entity_type, entity_id, payload, event):
    """Under the workflow lock: replay and rate checks, then the run and
    its accepted event in one transaction."""
    workflow = ai_workflows_db_lock_workflow(workflow_id)
    if workflow is None or not workflow.is_active:
        raise AiWorkflowsInboundError(401, 'workflow is inactive')
    if signature_sha256 is not None:
        since = ai_workflows_db_utcnow() - datetime.timedelta(seconds=2 * SIGNATURE_TOLERANCE_SECONDS)
        if ai_workflows_business_db_signature_seen(workflow.id, signature_sha256, since):
            raise AiWorkflowsInboundError(401, 'signature replay')
    if _over_rate(workflow):
        ai_workflows_engine_skip_run(workflow, TRIGGER_WEBHOOK, 'webhook rate limit')
        raise AiWorkflowsInboundError(429, f'rate limit of {workflow.max_runs_per_hour} runs per hour reached')
    if denial is not None:
        event.reason = f'run refused: {denial}'[:1000]
    # Committed with the run, while the lock is held: a concurrent replay
    # of the same signature sees it
    ai_workflows_db_add(event)
    try:
        run = ai_workflows_engine_insert_run(workflow, TRIGGER_WEBHOOK, denial=denial, entity_type=entity_type,
                                             entity_id=entity_id, payload=payload)
    except Exception:
        logger.exception(f'Starting AI workflow #{workflow_id} from an inbound webhook failed')
        ai_workflows_db_rollback()
        raise AiWorkflowsInboundError(500, 'run start failed')
    event.run_id = run.id
    ai_workflows_db_commit()
    return run


def ai_workflows_inbound_trigger(workflow_uuid, headers, raw_body, source_ip, content_length=None) -> dict:
    """Start a webhook-triggered run as the workflow owner. Returns the
    202 body `{run_uuid, status: "accepted"}` — also when the owner may
    not act on the entity (the run is recorded as skipped); raises
    AiWorkflowsInboundError otherwise. Logs either way (known
    workflows)."""
    raw_body = raw_body or b''
    size = max(len(raw_body), content_length or 0)
    parsed = _parse_uuid(workflow_uuid)
    workflow = ai_workflows_db_get_by_uuid(parsed) if parsed is not None else None
    if workflow is None:
        _log_unknown(INBOUND_TRIGGER, workflow_uuid, source_ip)
        raise AiWorkflowsInboundError(401, 'unknown workflow')
    workflow_id = workflow.id
    try:
        _check_size(raw_body, content_length)
        _check_workflow(workflow, headers)
        required = bool((workflow.trigger_config or {}).get('require_signature'))
        signature_sha256 = _check_signature(headers, _signing_key(workflow), raw_body, required=required)
        payload = ai_workflows_inbound_parse(raw_body)
        entity_type, entity_id = _trigger_entity(workflow, payload)
        # May commit (access caches): before the workflow lock is taken
        denial = ai_workflows_engine_access_denial(workflow, workflow.owner_id, entity_type, entity_id)
        event = _event(INBOUND_TRIGGER, INBOUND_ACCEPTED, None, source_ip, raw_body, size, workflow_id=workflow_id,
                       signature_sha256=signature_sha256)
        run = _start_locked(workflow_id, signature_sha256, denial, entity_type, entity_id,
                            _stored_payload(payload, raw_body), event)
    except AiWorkflowsInboundError as e:
        ai_workflows_db_rollback()
        status = INBOUND_ERROR if e.status >= 500 else INBOUND_REJECTED
        _log(INBOUND_TRIGGER, status, e.reason, source_ip, raw_body, size, workflow_id=workflow_id)
        raise
    return {'run_uuid': str(run.uuid), 'status': 'accepted'}
