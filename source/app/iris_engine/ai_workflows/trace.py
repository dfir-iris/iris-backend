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

"""Trace prefix stamped on everything a workflow run does on IRIS
objects (activity log entries, tool-call audit), so a change can be
traced back to the workflow, its version, the run and the step."""


def ai_workflows_trace_prefix(run, step=None) -> str:
    """`[ai-workflow:<wf_id>@v<ver> run:<uuid> step:<step_id>]`."""
    if run is None:
        return '[ai-workflow]'
    head = f'[ai-workflow:{run.workflow_id}@v{run.workflow_version} run:{run.uuid}'
    if step is not None:
        head = f'{head} step:{step.id}'
    return f'{head}]'
