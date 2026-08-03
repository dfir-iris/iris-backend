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

"""v2 endpoints for admin-managed top banners.

Two audiences meet in this blueprint. The `/active` endpoint returns the
currently-active banner list to *any* authenticated user — that's what
the SPA polls to render the top strip on every page. Every other route
is admin-only (`Permissions.server_administrator`) and manages the CRUD.
"""

from flask import Blueprint
from flask import request
from marshmallow import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.banners import banners_create
from app.business.banners import banners_delete
from app.business.banners import banners_get
from app.business.banners import banners_list
from app.business.banners import banners_list_active
from app.business.banners import banners_update
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import BannerSchema


class BannersOperations:

    def __init__(self):
        self._schema = BannerSchema()

    def list_active(self):
        banners = banners_list_active()
        return response_api_success(self._schema.dump(banners, many=True))

    def list_all(self):
        banners = banners_list()
        return response_api_success(self._schema.dump(banners, many=True))

    def create(self):
        try:
            request_data = request.get_json() or {}
            banner = self._schema.load(request_data)
            banners_create(banner, iris_current_user)
            return response_api_created(self._schema.dump(banner))
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def read(self, identifier):
        try:
            banner = banners_get(identifier)
            return response_api_success(self._schema.dump(banner))
        except ObjectNotFoundError:
            return response_api_not_found()

    def update(self, identifier):
        try:
            banner = banners_get(identifier)
            request_data = request.get_json() or {}
            # Partial=True — the SPA sends only edited fields, and the
            # marshmallow validators (`Length`, `OneOf`) still fire on
            # anything that IS present.
            self._schema.load(request_data, instance=banner, partial=True)
            banners_update(banner)
            return response_api_success(self._schema.dump(banner))
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)
        except ObjectNotFoundError:
            return response_api_not_found()

    @staticmethod
    def delete(identifier):
        try:
            banner = banners_get(identifier)
            banners_delete(banner)
            return response_api_deleted()
        except ObjectNotFoundError:
            return response_api_not_found()


banners_blueprint = Blueprint('banners_rest_v2', __name__, url_prefix='/banners')

banners_operations = BannersOperations()


@banners_blueprint.get('/active')
@ac_api_requires()
@api_doc(response=BannerSchema, tags=['ManageBanners'],
         summary='List currently-active banners (any authenticated user)')
def list_active_banners_route():
    return banners_operations.list_active()


@banners_blueprint.get('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response=BannerSchema, tags=['ManageBanners'],
         summary='List all banners')
def list_banners_route():
    return banners_operations.list_all()


@banners_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(request=BannerSchema, response=BannerSchema, response_shape='created',
         tags=['ManageBanners'], summary='Create a banner')
def create_banner_route():
    return banners_operations.create()


@banners_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response=BannerSchema, tags=['ManageBanners'],
         summary='Get a banner')
def get_banner_route(identifier):
    return banners_operations.read(identifier)


@banners_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(request=BannerSchema, response=BannerSchema,
         tags=['ManageBanners'], summary='Update a banner')
def put_banner_route(identifier):
    return banners_operations.update(identifier)


@banners_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageBanners'],
         summary='Delete a banner')
def delete_banner_route(identifier):
    return banners_operations.delete(identifier)
