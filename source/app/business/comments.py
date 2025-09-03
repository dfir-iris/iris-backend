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
from datetime import datetime

from flask_sqlalchemy.pagination import Pagination

from app import db
from app.business.alerts import alerts_exists
from app.business.errors import ObjectNotFoundError
from app.business.errors import BusinessProcessingError
from app.datamgmt.case.case_comments import get_case_comment
from app.datamgmt.comments import get_filtered_alert_comments
from app.datamgmt.comments import get_filtered_asset_comments
from app.datamgmt.comments import get_filtered_evidence_comments
from app.datamgmt.comments import get_filtered_ioc_comments
from app.datamgmt.comments import get_filtered_note_comments
from app.datamgmt.comments import get_filtered_task_comments
from app.datamgmt.comments import get_filtered_event_comments
from app.iris_engine.access_control.iris_user import iris_current_user
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.models import Comments
from app.models.pagination_parameters import PaginationParameters


def comments_get_filtered_by_alert(current_user, alert_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    if not alerts_exists(current_user, alert_identifier):
        raise ObjectNotFoundError()

    return get_filtered_alert_comments(alert_identifier, pagination_parameters)


def comments_get_filtered_by_asset(asset_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_asset_comments(asset_identifier, pagination_parameters)


def comments_get_filtered_by_evidence(evidence_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_evidence_comments(evidence_identifier, pagination_parameters)


def comments_get_filtered_by_ioc(ioc_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_ioc_comments(ioc_identifier, pagination_parameters)


def comments_get_filtered_by_note(note_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_note_comments(note_identifier, pagination_parameters)


def comments_get_filtered_by_task(taks_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_task_comments(taks_identifier, pagination_parameters)


def comments_get_filtered_by_event(event_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_event_comments(event_identifier, pagination_parameters)


def comments_update_for_case(comment_text, comment_id, object_type, caseid) -> Comments:
    comment = get_case_comment(comment_id, caseid)
    if not comment:
        raise BusinessProcessingError('Invalid comment ID')

    if hasattr(iris_current_user, 'id') and iris_current_user.id is not None:
        if comment.comment_user_id != iris_current_user.id:
            raise BusinessProcessingError('Permission denied')

    comment.comment_text = comment_text
    comment.comment_update_date = datetime.utcnow()

    db.session.commit()

    hook = object_type
    if hook.endswith('s'):
        hook = hook[:-1]

    call_modules_hook(f'on_postload_{hook}_comment_update', data=comment, caseid=caseid)

    track_activity(f"comment {comment.comment_id} on {object_type} edited", caseid=caseid)
    return comment
