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

from flask import g, has_request_context
from flask_login import current_user
from werkzeug.local import LocalProxy


class TokenUser:
    """A class that mimics the Flask-Login current_user interface for token auth"""
    def __init__(self, user_data):
        self.id = user_data['user_id']
        self.user = user_data['user_login']
        self.name = user_data['user_name']
        self.email = user_data['user_email']
        self.is_authenticated = True
        self.is_active = True
        self.is_anonymous = False


def _get_current_user():
    """
    Returns a compatible user object for both session and token auth
    For token auth, uses data from g.auth_user
    For session auth, returns Flask current_user
    """
    if has_request_context():
        if hasattr(g, 'auth_user'):
            return TokenUser(g.auth_user)
        return current_user

    return None


iris_current_user = LocalProxy(lambda: _get_current_user())
