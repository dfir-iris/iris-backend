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
from app import db
from app.models.authorization import User
from app.business.errors import BusinessProcessingError
from app.business.errors import ObjectNotFoundError
from app.datamgmt.manage.manage_users_db import get_active_user
from app.datamgmt.manage.manage_users_db import get_user_details_return_user
from app.datamgmt.manage.manage_users_db import get_active_user_by_login
from app.datamgmt.manage.manage_users_db import create_user
from app.datamgmt.manage.manage_users_db import update_user
from app.iris_engine.utils.tracker import track_activity


class UserData:
    def __init__(self, identifier):
        result = get_user_details_return_user(identifier)
        if result is not None:
            self.user, self.group, self.organisation, self.effective_permissions, self.cases_access, self.user_clients, self.primary_organisation_id, self.user_api_key = result
    
    def get_user(self):
        return self.user
    
    def get_others(self):
        return self.group, self.organisation, self.effective_permissions, self.cases_access, self.user_clients, self.primary_organisation_id, self.user_api_key

def users_reset_mfa(user_id: int = None):
    """
    Resets a user MFA by setting to none its MFA token
    """
    user = get_active_user(user_id=user_id)
    if user is None:
        raise BusinessProcessingError(f'User with id {user_id} is not found')

    user.mfa_secrets = None
    user.mfa_setup_complete = False

    db.session.commit()


def retrieve_user_by_username(username: str):
    """
    Retrieve the user object by username.

    :param username: Username
    :return: User object if found, None
    """
    user = get_active_user_by_login(username)
    if not user:
        track_activity(f'someone tried to log in with user \'{username}\', which does not exist',
                       ctx_less=True, display_in_ui=False)
    return user


def user_create(user: User, active) -> User:
    user = create_user(user.name,
                       user.user,
                       user.password,
                       user.email,
                       active,
                       None,
                       user.is_service_account)

    track_activity(f"created user {user.user}", ctx_less=True)
    return user


def user_get(identifier) -> User:
    data = UserData(identifier)
    user = data.get_user()
    if not user :
        raise ObjectNotFoundError()
    return data


def user_update(user: User, user_password: str = None) -> User:
    user = update_user(password=user_password, user=user)
    track_activity(f"updated user {user.user}", ctx_less=True)
    db.session.commit()
    return user
