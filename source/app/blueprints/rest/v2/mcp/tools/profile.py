#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the calling user's own profile."""
from __future__ import annotations

from flask import current_app

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.users import users_get
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.models.authorization import Permissions
from app.schema.marshables import UserSchemaForAPIV2


_user_schema = UserSchemaForAPIV2()


@mcp_tool(
    name='iris_me_get',
    description='Return the calling user\'s profile.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_me_get(_args: dict) -> dict:
    user = users_get(iris_current_user.id)
    return _user_schema.dump(user)


@mcp_tool(
    name='iris_me_context_get',
    description=(
        'Return the calling user\'s IRIS runtime context: server version, '
        'effective permission mask + human-readable permission names.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_me_context_get(_args: dict) -> dict:
    user = users_get(iris_current_user.id)
    mask = ac_get_effective_permissions_of_user(user) if user else 0
    if user is not None:
        mask |= Permissions.standard_user.value
    names = [p.name for p in Permissions if (mask & p.value) == p.value]
    return {
        'iris_version': current_app.config.get('IRIS_VERSION'),
        'permissions': {'mask': mask, 'names': names},
    }
