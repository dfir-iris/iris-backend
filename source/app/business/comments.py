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

from flask_sqlalchemy.pagination import Pagination

from app.business.alerts import alerts_exists
from app.business.errors import ObjectNotFoundError
from app.datamgmt.comments import get_filtered_alert_comments
from app.datamgmt.comments import get_filtered_asset_comments
from app.datamgmt.comments import get_filtered_evidence_comments
from app.models.pagination_parameters import PaginationParameters


def comments_get_filtered_by_alert(current_user, alert_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    if not alerts_exists(current_user, alert_identifier):
        raise ObjectNotFoundError()

    return get_filtered_alert_comments(alert_identifier, pagination_parameters)


def comments_get_filtered_by_asset(asset_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_asset_comments(asset_identifier, pagination_parameters)


def comments_get_filtered_by_evidence(evidence_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    return get_filtered_evidence_comments(evidence_identifier, pagination_parameters)
