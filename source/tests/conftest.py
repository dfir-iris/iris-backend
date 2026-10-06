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

"""Unit tests run without a database: `call_modules_hook` looks the hook
up in it, so it is replaced by a pass-through in the business modules
that fire hooks from code the unit tests drive. A test asserting on a
hook patches it again, which takes precedence."""

from unittest.mock import patch

import pytest

_HOOKED_MODULES = (
    'app.business.vulnerabilities',
    'app.business.vulnerability_findings',
    'app.business.war_room_vulnerabilities',
    'app.business.war_room_decisions',
    'app.business.war_room_teams',
    'app.business.war_room_scope',
    'app.business.war_room_task_fan_out',
    'app.business.war_room_note_shares',
)


def _pass_through(_hook_name, data, **_kwargs):
    return data


@pytest.fixture(autouse=True)
def _no_hook_lookup():
    patchers = [patch(f'{module}.call_modules_hook', side_effect=_pass_through) for module in _HOOKED_MODULES]
    for patcher in patchers:
        patcher.start()
    yield
    for patcher in patchers:
        patcher.stop()
