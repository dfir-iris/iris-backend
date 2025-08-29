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

from app.models.models import Comments
from app.models.models import AssetComments
from app.models.pagination_parameters import PaginationParameters


def get_filtered_alert_comments(alert_identifier: int, pagination_parameters: PaginationParameters) -> Pagination:
    query = Comments.query.filter(Comments.comment_alert_id == alert_identifier)
    return query.paginate(page=pagination_parameters.get_page(), per_page=pagination_parameters.get_per_page())


def get_filtered_asset_comments(asset_id, pagination_parameters: PaginationParameters) -> Pagination:
    query = Comments.query.filter(
        AssetComments.comment_asset_id == asset_id
    ).with_entities(
        Comments
    ).join(AssetComments,
        Comments.comment_id == AssetComments.comment_id
    ).order_by(
        Comments.comment_date.asc()
    )
    return query.paginate(page=pagination_parameters.get_page(), per_page=pagination_parameters.get_per_page())
