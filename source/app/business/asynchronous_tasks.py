#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

import io
import json
import pickle

from app.datamgmt.asynchronous_tasks import search_asynchronous_tasks_paginated
from app.datamgmt.asynchronous_tasks import get_asynchronous_task_by_id
from iris_interface.IrisInterfaceStatus import IIStatus


class _RefusedClass:
    """Inert placeholder swapped in for any class the result blob names
    other than ``IIStatus``.

    Standing something harmless in, rather than refusing the whole blob,
    is deliberate: ``task_hook_wrapper`` returns whatever the module
    returned, and the bundled modules put the merged SQLAlchemy objects
    straight into ``IIStatus.data``. A real success blob therefore names
    a dozen ORM/SQLAlchemy classes that have nothing to do with the only
    thing this module reads off it — ``is_success()``. Raising on those
    would quietly demote every hook task to a failure row.
    """

    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        pass


class _RestrictedUnpickler(pickle.Unpickler):
    """``pickle.Unpickler`` that can only ever build an ``IIStatus``.

    ``celery_taskmeta.result`` holds pickled bytes, so a plain
    ``pickle.loads`` hands arbitrary code execution to anything able to
    write that column (a worker, or direct database access). Constraining
    ``find_class`` keeps the worst case at "the row renders as a failure"
    instead of "the web process runs the blob".
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refused_class_names = set()

    def find_class(self, module, name):
        if module == IIStatus.__module__ and name == IIStatus.__name__:
            return IIStatus
        self.refused_class_names.add(name)
        return _RefusedClass


def _read_task_result(blob):
    """Deserialize a ``celery_taskmeta.result`` blob without trusting it.

    Returns ``(result, refused_class_names)``. The second element lets
    the caller tell "this blob referenced classes we would not build"
    from "this blob was an ``IIStatus`` all along".
    """
    unpickler = _RestrictedUnpickler(io.BytesIO(blob))
    return unpickler.load(), unpickler.refused_class_names


def _loads_task_kwargs(raw):
    """Decode the ``kwargs`` column into a dict.

    Celery writes this one with the configured ``result_serializer``
    (json — see ``CeleryConfig``), not pickle, so ``json.loads`` is both
    the correct and the safe reader here. Unreadable kwargs are not worth
    failing a read-only view over, hence the empty dict.
    """
    if not raw or raw == b'{}':
        return {}
    try:
        return json.loads(raw.decode('utf-8')) or {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _get_engine_name(task):
    if not task.name:
        return 'No engine. Unrecoverable shadow failure'
    return task.name


def _get_success(task_result: IIStatus):
    if task_result.is_success():
        return 'Success'
    return 'Failure'


class _MissingTask:
    """Stand-in for a task id that has no ``celery_taskmeta`` row.

    ``celery.AsyncResult`` answered unknown ids with a synthetic PENDING
    result rather than failing, and the legacy Dim page calls
    ``dim_tasks_get`` without checking existence first — so keep handing
    it something renderable.
    """

    name = None
    status = 'PENDING'
    result = None
    kwargs = None
    date_done = None
    traceback = None


_LEGACY_TASK_DETAILS = {
    'Danger': 'This task was executed in a previous version of IRIS and the status cannot be read anymore.',
    'Note': 'All the data readable by the current IRIS version is displayed in the table.',
    'Additional information': 'The results of this tasks were stored in a pickled Class which does not exists '
                              'anymore in current IRIS version.'
}


def dim_tasks_get(task_identifier):
    """Detail view for a single Dim task.

    Reads the ``celery_taskmeta`` row directly instead of going through
    ``celery.AsyncResult``. Celery maps ``result`` as a ``PickleType``,
    so merely touching ``AsyncResult.info`` unpickles the column with no
    restriction whatsoever — arbitrary code execution for anyone able to
    write that row. Fetching the row ourselves puts the blob through
    ``_read_task_result`` instead.
    """
    row = get_asynchronous_task_by_id(task_identifier) or _MissingTask()

    result = None
    refused_class_names = frozenset()
    if row.result:
        try:
            result, refused_class_names = _read_task_result(row.result)
        except Exception:
            # A corrupt blob is not worth 500-ing a read-only view over;
            # it falls through to the "no valid IIStatus" branch below.
            pass

    if IIStatus.__name__ in refused_class_names:
        # The blob names an IIStatus we can no longer import: an IRIS
        # version whose status class lived elsewhere. Saying "Failure"
        # would be a lie — the task may well have succeeded, we just
        # cannot read its verdict.
        return dict(_LEGACY_TASK_DETAILS)

    user = None
    module_name = None
    hook_name = None
    case_identifier = None
    if row.name and ('task_hook_wrapper' in row.name or 'pipeline_dispatcher' in row.name):
        kwargs = _loads_task_kwargs(row.kwargs)
        module_name = kwargs.get('module_name')
        hook_name = kwargs.get('hook_name')
        user = kwargs.get('init_user')
        case_identifier = kwargs.get('caseid')

    if isinstance(result, IIStatus):
        success = _get_success(result)
        logs = result.get_logs()
    else:
        success = 'Failure'
        user = 'Shadow Iris'
        logs = ['Task did not returned a valid IIStatus object']

    return {
        'Task ID': task_identifier,
        'Task finished on': row.date_done,
        'Task state': (row.status or 'PENDING').lower(),
        'Engine': _get_engine_name(row),
        'Module name': module_name,
        'Hook name': hook_name,
        'Case ID': case_identifier,
        'Success': success,
        'User': user,
        'Logs': logs,
        'Traceback': row.traceback
    }


def _project_row(row):
    """Turn one CeleryTaskMeta record into a flat dict suitable for the
    Dim Tasks listing page.

    ``date_done`` is an ISO 8601 string (or ``None``) so the frontend
    doesn't have to know about Python datetimes; ``case_id`` is split
    out from the human ``case`` label so the UI can build a
    `/case/<id>` link without parsing a string; and we never propagate
    exceptions raised by ``_read_task_result`` — a corrupt result blob
    just leaves the row labelled as a failure rather than 500-ing the
    page.
    """
    tkp = {
        'task_id': row.task_id,
        'state': row.status,
        'case': '',
        'case_id': None,
        'module': row.name,
        'date_done': row.date_done.isoformat() if row.date_done else None,
        'user': 'Unknown',
    }

    try:
        _ = row.result
    except AttributeError:
        # Legacy task — pickled by an old IRIS version that no longer
        # exists. Leave the bare row in place; the detail endpoint
        # surfaces a proper "legacy" message.
        return tkp

    if row.name is not None and 'task_hook_wrapper' in row.name:
        task_name = f'{row.kwargs}::{row.kwargs}'
    else:
        task_name = row.name

    user = None
    case_name = None
    case_identifier = None
    kwargs = _loads_task_kwargs(row.kwargs)
    if kwargs:
        user = kwargs.get('init_user')
        case_identifier = kwargs.get('caseid')
        if case_identifier is not None:
            case_name = f'Case #{case_identifier}'
        module_name = kwargs.get('module_name')
        hook_name = kwargs.get('hook_name')
        task_name = f'{module_name}::{hook_name}'

    try:
        result, _ = _read_task_result(row.result) if row.result else (None, frozenset())
    except Exception:
        result = None

    if isinstance(result, IIStatus):
        try:
            success = result.is_success()
        except Exception:
            success = None
    else:
        success = None

    tkp['state'] = 'success' if success else (row.status or 'failure')
    tkp['user'] = user if user else 'Shadow Iris'
    tkp['module'] = task_name
    tkp['case'] = case_name if case_name else ''
    tkp['case_id'] = case_identifier

    return tkp


def asynchronous_tasks_list(page=1, per_page=25, search_value=None, status=None):
    """Paginated Dim Tasks listing.

    Returns a tuple ``(items, paginated)`` where ``items`` is the list of
    projected dicts and ``paginated`` is the SQLAlchemy ``Pagination``
    object — same handshake as ``list_activities_paginated`` so the
    endpoint can build the standard envelope without re-counting.
    """
    paginated = search_asynchronous_tasks_paginated(
        page=page,
        per_page=per_page,
        search_value=search_value,
        status=status,
    )
    items = [_project_row(r) for r in paginated.items]
    return items, paginated


def asynchronous_task_get_by_id(task_id):
    """Find one CeleryTaskMeta by Celery task id and project it.

    Returns ``None`` if the row doesn't exist (so the endpoint can 404).
    """
    row = get_asynchronous_task_by_id(task_id)
    if row is None:
        return None
    return _project_row(row)
