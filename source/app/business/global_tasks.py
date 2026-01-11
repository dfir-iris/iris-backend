#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from app.models.models import GlobalTasks
from datetime import datetime
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.datamgmt.db_operations import db_create


def global_tasks_create(user, global_task: GlobalTasks) -> GlobalTasks:
    global_task.task_userid_update = user.id
    global_task.task_open_date = datetime.utcnow()
    global_task.task_last_update = datetime.utcnow()
    global_task.task_last_update = datetime.utcnow()

    db_create(global_task)

    global_task = call_modules_hook('on_postload_global_task_create', data=global_task)
    track_activity(f'created new global task "{global_task.task_title}"')

    return global_task
