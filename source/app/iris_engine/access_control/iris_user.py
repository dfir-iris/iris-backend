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


def get_current_user():
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


iris_current_user = LocalProxy(lambda: get_current_user())
