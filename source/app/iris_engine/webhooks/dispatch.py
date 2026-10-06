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

"""Hook listener: turns `on_postload_*` hook fires into webhook deliveries.

Runs inside `call_modules_hook`, after the modules, in whatever process
fired the hook. It only does what needs the caller's objects — find the
subscribed webhooks, serialize the data into the event — and hands the
event to Celery as JSON. Conditions, delivery rows and HTTP all happen
in the worker, so a slow or broken receiver never slows a save.
"""

import logging

from app import app
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_case_summary
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_list_enabled
from app.iris_engine.module_handler.module_handler import register_builtin_hook_listener
from app.iris_engine.webhooks.events import webhooks_event_matches
from app.iris_engine.webhooks.events import webhooks_is_event
from app.iris_engine.webhooks.events import webhooks_is_manual
from app.iris_engine.webhooks.events import webhooks_split_event
from app.iris_engine.webhooks.payload import webhooks_case_id
from app.iris_engine.webhooks.payload import webhooks_make_event
from app.iris_engine.webhooks.payload import webhooks_serialize
# Module level on purpose: importing the tasks with `app` connects their
# beat schedule before the Celery worker finalizes.
from app.iris_engine.webhooks.tasks import webhooks_dispatch_task


logger = logging.getLogger(__name__)


def webhooks_current_actor():
    """`{id, login, name}` of the user behind the request, None outside one."""
    try:
        from app.blueprints.iris_user import iris_current_user
        user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
        if user is None or not getattr(user, 'id', None):
            return None
        return {
            'id': user.id,
            'login': getattr(user, 'user', None) or getattr(user, 'user_login', None),
            'name': getattr(user, 'name', None),
        }
    except Exception:
        return None


def webhooks_build_event(hook_name, data, caseid=None, actor=None):
    """The event for a hook fire, ready to be sent to Celery."""
    # Imported here: the business layer imports the module handler, which
    # imports this module.
    from app.business.vulnerabilities import vulnerabilities_mask_private
    # Like MCP, the LLM and case export: private catalogue entries never
    # leave the instance in full.
    serialized = vulnerabilities_mask_private(webhooks_serialize(data))
    object_type, _ = webhooks_split_event(hook_name)
    case = None
    case_id = webhooks_case_id(serialized, object_type, caseid)
    if case_id:
        try:
            case = webhooks_db_case_summary(int(case_id))
        except (TypeError, ValueError):
            case = None
    return webhooks_make_event(
        hook_name,
        serialized,
        case=case,
        actor=actor,
        instance_url=app.config.get('IRIS_ALLOW_ORIGIN') or '',
        version=app.config.get('IRIS_VERSION') or '',
    )


def webhooks_on_hook(hook_name, data, caseid=None, hook_ui_name=None):
    # Manual triggers name one webhook; they go through `webhooks_invoke_manual`
    if not webhooks_is_event(hook_name) or webhooks_is_manual(hook_name):
        return
    webhook_ids = [w.id for w in webhooks_db_list_enabled() if webhooks_event_matches(w.events, hook_name)]
    if not webhook_ids:
        return

    event = webhooks_build_event(hook_name, data, caseid=caseid, actor=webhooks_current_actor())

    webhooks_dispatch_task.delay(event, webhook_ids)


def webhooks_register_listener():
    register_builtin_hook_listener(webhooks_on_hook)
