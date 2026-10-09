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

"""Live progress of AI workflow runs, pushed over Socket.IO.

Each change (a node starts or settles, the run changes status) goes to
the run's room and its workflow's room on the `/notifications`
namespace. Joining a room checks the user may see that run or edit that
workflow (`ai_workflows_live_room`); a push only carries ids and
statuses, the details are read over REST."""

import logging


logger = logging.getLogger(__name__)

LIVE_EVENT = 'ai_workflow_run'


def ai_workflows_live_run_room(run_uuid) -> str:
    return f'ai-workflow-run-{run_uuid}'


def ai_workflows_live_workflow_room(workflow_id) -> str:
    return f'ai-workflow-{int(workflow_id)}'


def ai_workflows_live_payload(run, step=None) -> dict:
    payload = {
        'run_uuid': str(run.uuid) if run.uuid else None,
        'workflow_id': run.workflow_id,
        'status': run.status,
        'waiting_node_id': run.waiting_node_id,
        'step': None,
    }
    if step is not None:
        payload['step'] = {'id': step.id, 'seq': step.seq, 'node_id': step.node_id, 'status': step.status,
                           'port': step.port}
    return payload


def ai_workflows_live_emit(run, step=None):
    """Push the state of `run` (and of `step`); never raises."""
    from app.iris_engine.notifications.service import _safe_socket_emit

    if run is None:
        return
    try:
        payload = ai_workflows_live_payload(run, step)
    except Exception:
        logger.exception('Could not build the live state of an AI workflow run')
        return
    if payload['run_uuid']:
        _safe_socket_emit(LIVE_EVENT, payload, room=ai_workflows_live_run_room(payload['run_uuid']))
    if payload['workflow_id'] is not None:
        _safe_socket_emit(LIVE_EVENT, payload, room=ai_workflows_live_workflow_room(payload['workflow_id']))
