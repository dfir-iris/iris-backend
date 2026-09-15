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

"""Business layer for the DIM ("dispatch-in-module") hooks REST API —
listing manually-triggerable hooks for a resource type and invoking a
hook against a batch of targets.

Kept pure so v2 blueprints can call it without touching sqlalchemy
directly (import-linter contract). All DB access is delegated to
`app.datamgmt.dim_hooks`.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from app.datamgmt.dim_hooks import list_manual_hook_options
from app.datamgmt.dim_hooks import resolve_hook_target
from app.datamgmt.dim_hooks import supported_hook_target_types
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.models.errors import BusinessProcessingError


ALERT_MANUAL_HOOK_NAME = 'on_manual_trigger_alert'


@dataclass
class HookInvocationResult:
    queued: int
    logs: List[str]


def list_hook_options_for(hook_type: str) -> List[Dict[str, Any]]:
    """Registered manual hooks that target `hook_type` (e.g. `ioc`)."""
    return list_manual_hook_options(hook_type)


def invoke_hook_for_case(
    caseid: int,
    hook_name: str,
    hook_ui_name: str,
    module_name: str,
    data_type: str,
    targets: Sequence[Any],
) -> HookInvocationResult:
    """Resolve `targets` to ORM rows and dispatch a manual hook against
    them via `call_modules_hook`.

    Skips (with a log entry) targets that don't exist or that reference
    an unsupported `data_type`. Raises `BusinessProcessingError` for
    caller-side mistakes the route should surface as a 400 (missing
    field, non-integer target, unknown data_type with zero valid
    targets).
    """
    if not hook_name:
        raise BusinessProcessingError('Missing hook_name')
    if not data_type:
        raise BusinessProcessingError('Missing data type')
    if not targets:
        raise BusinessProcessingError('Missing targets')

    if data_type not in supported_hook_target_types():
        raise BusinessProcessingError(f'Data type {data_type} not supported')

    logs: List[str] = []
    obj_targets: List[Any] = []
    for raw in targets:
        try:
            target_id = int(raw)
        except (TypeError, ValueError):
            raise BusinessProcessingError('Invalid target')

        obj = resolve_hook_target(data_type, target_id, caseid)
        if obj is None:
            logs.append(f'Object ID {target_id} not found')
            continue
        obj_targets.append(obj)

    if obj_targets:
        call_modules_hook(
            hook_name,
            obj_targets,
            caseid=caseid,
            hook_ui_name=hook_ui_name,
            module_name=module_name,
        )

    return HookInvocationResult(queued=len(obj_targets), logs=logs)


def invoke_hook_for_alerts(
    hook_name: str,
    hook_ui_name: str,
    module_name: str,
    alerts: Sequence[Any],
    logs: Sequence[str] = (),
) -> HookInvocationResult:
    """Dispatch a manual hook against a batch of already-loaded alerts.

    Alerts sit outside any case, so there is no case to resolve targets
    against — the caller loads each alert through the usual tenant check
    and hands the rows over, along with a `logs` line per target it had
    to skip. An empty `alerts` therefore means "every target was skipped",
    not a caller mistake, and reports queued=0 rather than raising.
    `caseid` is passed as None to `call_modules_hook`, which only uses it
    to label the row in the module tasks table.
    """
    if not hook_name:
        raise BusinessProcessingError('Missing hook_name')

    # Manual alert hooks are registered under exactly one well-known
    # name. Without this, a caller could hand alerts to a module that
    # registered for, say, `on_manual_trigger_ioc` and expects an Ioc.
    if hook_name != ALERT_MANUAL_HOOK_NAME:
        raise BusinessProcessingError(f'Hook {hook_name} is not an alert hook')

    if not alerts:
        return HookInvocationResult(queued=0, logs=list(logs))

    call_modules_hook(
        hook_name,
        list(alerts),
        caseid=None,
        hook_ui_name=hook_ui_name,
        module_name=module_name,
    )

    return HookInvocationResult(queued=len(alerts), logs=list(logs))
