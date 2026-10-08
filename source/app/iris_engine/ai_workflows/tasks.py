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

"""Celery side of AI workflows.

- `iris.ai_workflows.step`: execute the pending nodes of a run.
- `iris.ai_workflows.trigger`: start the runs an IRIS event triggers.
- `iris.ai_workflows.tick` (every minute): fire due schedules, expire
  waits, recover runs a lost worker or broker message left behind.
- `iris.ai_workflows.prune` (daily): retention purge.
"""

import datetime
import logging

from app import app
from app import celery
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_expired_waits
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_prune
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_rollback
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_stale_executing_runs
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_stuck_running_runs
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_expire_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_lock_run_then_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_recover_stale
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_requeue
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_cron_tick
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_process_event
from app.models.ai_workflows import WAIT_PENDING

logger = logging.getLogger(__name__)

_TICK_BEAT_ENTRY = 'iris-ai-workflows-tick'
_PRUNE_BEAT_ENTRY = 'iris-ai-workflows-prune'

# A worker holding a run longer than this is presumed dead (LLM agents
# can take minutes; the step refreshes `executing_since` per node and
# long nodes send heartbeats)
_STALE_EXECUTING = datetime.timedelta(minutes=30)
# A `running` run nobody touched for this long lost its queue message;
# a requeued run is not requeued again before this long either
_STUCK_RUNNING = datetime.timedelta(minutes=15)


def _enabled() -> bool:
    return bool(app.config.get('AI_WORKFLOWS_ENABLED', True))


@celery.task(name='iris.ai_workflows.step')
def ai_workflows_task_step(run_id):
    try:
        ai_workflows_engine_step(run_id)
    except Exception:
        logger.exception(f'AI workflow run #{run_id} step task failed')
        ai_workflows_db_rollback()


@celery.task(name='iris.ai_workflows.trigger')
def ai_workflows_task_trigger(event, workflow_ids, chain_depth=None, parent_run_id=None):
    if not _enabled():
        return []
    return ai_workflows_triggers_process_event(event, workflow_ids, chain_depth, parent_run_id)


def _expire_waits(now) -> int:
    count = 0
    for candidate in ai_workflows_db_expired_waits(now):
        try:
            # Run first, then the wait: the lock order of every path
            _run, wait = ai_workflows_engine_lock_run_then_wait(candidate.id)
            if wait is None or wait.status != WAIT_PENDING:
                ai_workflows_db_commit()
                continue
            ai_workflows_engine_expire_wait(wait)
            count += 1
        except Exception:
            logger.exception(f'Could not expire AI workflow wait #{candidate.id}')
            ai_workflows_db_rollback()
    return count


def _recover(now) -> dict:
    """Recover runs whose worker died, then re-enqueue runs that lost
    their queue message (both bounded per tick)."""
    counts = {'stale': 0, 'requeued': 0}
    before = now - _STALE_EXECUTING
    for run in ai_workflows_db_stale_executing_runs(before):
        try:
            logger.warning(f'AI workflow run {run.uuid} was left executing; recovering it')
            ai_workflows_engine_recover_stale(run, before)
            counts['stale'] += 1
        except Exception:
            logger.exception(f'Could not recover AI workflow run #{run.id}')
            ai_workflows_db_rollback()
    for run in ai_workflows_db_stuck_running_runs(now - _STUCK_RUNNING):
        try:
            ai_workflows_engine_requeue(run)
            counts['requeued'] += 1
        except Exception:
            logger.exception(f'Could not requeue AI workflow run #{run.id}')
            ai_workflows_db_rollback()
    return counts


@celery.task(name='iris.ai_workflows.tick')
def ai_workflows_task_tick():
    if not _enabled():
        return None
    now = ai_workflows_db_utcnow()
    result = {}
    for name, job in (('cron', lambda: len(ai_workflows_triggers_cron_tick(now))),
                      ('expired_waits', lambda: _expire_waits(now)),
                      ('recovery', lambda: _recover(now))):
        try:
            result[name] = job()
        except Exception:
            logger.exception(f'AI workflows tick: {name} failed')
            ai_workflows_db_rollback()
    return result


@celery.task(name='iris.ai_workflows.prune')
def ai_workflows_task_prune():
    days = int(app.config.get('AI_WORKFLOWS_RETENTION_DAYS') or 90)
    counts = ai_workflows_db_prune(ai_workflows_db_utcnow() - datetime.timedelta(days=days))
    logger.info(f'Pruned AI workflow records older than {days} days: {counts}')
    return counts


@celery.on_after_finalize.connect
def _register_ai_workflows_beat_schedule(sender, **_kwargs):
    from celery.schedules import crontab

    sender.conf.beat_schedule = dict(sender.conf.beat_schedule or {})
    sender.conf.beat_schedule[_TICK_BEAT_ENTRY] = {
        'task': 'iris.ai_workflows.tick',
        'schedule': crontab(),
    }
    sender.conf.beat_schedule[_PRUNE_BEAT_ENTRY] = {
        'task': 'iris.ai_workflows.prune',
        'schedule': crontab(hour=3, minute=41),
    }
