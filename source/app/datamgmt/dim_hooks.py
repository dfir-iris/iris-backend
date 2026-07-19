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

"""Persistence-layer helpers for the DIM ("dispatch-in-module") hooks
API — module registrations that end-users can trigger manually against
a case object from the SPA.

Kept sqlalchemy-only so `app.business.dim_hooks` can consume it without
tripping the "business layer must not import sqlalchemy" import-linter
contract.
"""

from typing import Any, List, Optional

from app.models.alerts import Alert
from app.models.assets import CaseAssets
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.evidences import CaseReceivedFile
from app.models.iocs import Ioc
from app.models.models import CaseTasks
from app.models.models import GlobalTasks
from app.models.models import IrisHook
from app.models.models import IrisModule
from app.models.models import IrisModuleHook
from app.models.models import Notes


def list_manual_hook_options(hook_type: str) -> List[dict]:
    """Return every active module-registered manual hook targeting
    `hook_type` (e.g. `ioc`, `asset`, `case`).

    Shape kept identical to the v1 endpoint so the SPA can consume it
    verbatim: a list of `{manual_hook_ui_name, hook_name, module_name}`
    dicts, one per registered hook.
    """
    rows = (
        IrisModuleHook.query.with_entities(
            IrisModuleHook.manual_hook_ui_name,
            IrisHook.hook_name,
            IrisModule.module_name,
        )
        .filter(
            IrisHook.hook_name == f'on_manual_trigger_{hook_type}',
            IrisModule.is_active == True,
        )
        .join(IrisHook, IrisHook.id == IrisModuleHook.hook_id)
        .join(IrisModule, IrisModule.id == IrisModuleHook.module_id)
        .all()
    )
    return [row._asdict() for row in rows]


_TARGET_LOADERS = {
    'ioc': lambda target, _caseid: Ioc.query.filter(Ioc.ioc_id == target).first(),
    'case': lambda _target, caseid: Cases.query.filter(Cases.case_id == caseid).first(),
    'asset': lambda target, caseid: CaseAssets.query.filter(
        CaseAssets.asset_id == target,
        CaseAssets.case_id == caseid,
    ).first(),
    'note': lambda target, caseid: Notes.query.filter(
        Notes.note_id == target,
        Notes.note_case_id == caseid,
    ).first(),
    'event': lambda target, caseid: CasesEvent.query.filter(
        CasesEvent.event_id == target,
        CasesEvent.case_id == caseid,
    ).first(),
    'task': lambda target, caseid: CaseTasks.query.filter(
        CaseTasks.id == target,
        CaseTasks.task_case_id == caseid,
    ).first(),
    'evidence': lambda target, caseid: CaseReceivedFile.query.filter(
        CaseReceivedFile.id == target,
        CaseReceivedFile.case_id == caseid,
    ).first(),
    'global_task': lambda target, _caseid: GlobalTasks.query.filter(
        GlobalTasks.id == target,
    ).first(),
    'alert': lambda target, _caseid: Alert.query.filter(Alert.alert_id == target).first(),
}


def resolve_hook_target(data_type: str, target: int, caseid: int) -> Optional[Any]:
    """Load the ORM row corresponding to a single hook target, scoped
    to the current case where applicable. Returns None when the row
    doesn't exist or the caller passes an unsupported `data_type`.
    """
    loader = _TARGET_LOADERS.get(data_type)
    if loader is None:
        return None
    return loader(target, caseid)


def supported_hook_target_types() -> List[str]:
    """The set of `data_type` values the hook invoker understands."""
    return list(_TARGET_LOADERS.keys())
