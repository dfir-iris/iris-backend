#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

from app.models.models import ReviewStatus
from app.models.cases import Cases
from app.models.models import TaskStatus


def get_tasks_status():
    return TaskStatus.query.all()


def list_user_reviews(user_identifier):
    ct = Cases.query.with_entities(
        Cases.case_id,
        Cases.name,
        ReviewStatus.status_name,
        ReviewStatus.id.label('status_id')
    ).join(
        Cases.review_status
    ).filter(
        Cases.reviewer_id == user_identifier,
        ReviewStatus.status_name != 'Reviewed',
        ReviewStatus.status_name != 'Not reviewed'
    ).all()

    return ct


def get_task_status(task_status_id):
    ret = TaskStatus.query.filter(
        TaskStatus.id == task_status_id
    ).first()

    return ret


def list_user_cases(user_identifier, show_all=False):
    if show_all:
        return Cases.query.filter(
            Cases.owner_id == user_identifier
        ).all()

    return Cases.query.filter(
        Cases.owner_id == user_identifier,
        Cases.close_date == None
    ).all()
