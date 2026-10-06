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

"""Permission grants of migration c7f2d9a3e1b6 (op / schema probes mocked)."""

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.models.authorization import Permissions


_PATH = Path(__file__).resolve().parents[2] / 'app' / 'alembic' / 'versions' / 'c7f2d9a3e1b6_war_room_vulnerabilities.py'


def _load_migration():
    spec = importlib.util.spec_from_file_location('_migration_c7f2d9a3e1b6', _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _group_statements(op):
    return [str(call.args[0]) for call in op.execute.call_args_list if 'groups' in str(call.args[0])]


class TestWarRoomVulnerabilitiesMigration(TestCase):

    def setUp(self):
        self.migration = _load_migration()

    def test_bits_match_the_permission_model(self):
        self.assertEqual(Permissions.vulnerabilities_read.value, self.migration._PERM_VULNERABILITIES_READ)
        self.assertEqual(Permissions.vulnerabilities_create.value, self.migration._PERM_VULNERABILITIES_CREATE)
        self.assertEqual(Permissions.server_administrator.value, self.migration._PERM_SERVER_ADMINISTRATOR)
        self.assertEqual(Permissions.standard_user.value, self.migration._PERM_STANDARD_USER)

    def test_upgrade_grants_create_to_administrator_groups_only(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True), \
                patch.object(self.migration, 'index_exists', return_value=True):
            self.migration.upgrade()
        statements = _group_statements(op)
        self.assertEqual(2, len(statements))
        read = Permissions.vulnerabilities_read.value
        both = read | Permissions.vulnerabilities_create.value
        admin = Permissions.server_administrator.value
        standard = Permissions.standard_user.value
        self.assertEqual(
            f'UPDATE groups SET group_permissions = group_permissions | {both} '
            f'WHERE group_permissions & {admin} != 0', statements[0])
        self.assertEqual(
            f'UPDATE groups SET group_permissions = group_permissions | {read} '
            f'WHERE group_permissions & {admin} = 0 AND group_permissions & {standard} != 0', statements[1])

    def test_downgrade_only_clears_the_new_bits(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True):
            self.migration.downgrade()
        statements = _group_statements(op)
        both = Permissions.vulnerabilities_read.value | Permissions.vulnerabilities_create.value
        self.assertEqual(
            [f'UPDATE groups SET group_permissions = group_permissions & ~{both} '
             f'WHERE group_permissions & {both} != 0'], statements)
        op.drop_table.assert_called_once_with('war_room_vulnerability')

    def test_upgrade_without_groups_table_touches_no_permission(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', side_effect=lambda name: name != 'groups'), \
                patch.object(self.migration, 'index_exists', return_value=True):
            self.migration.upgrade()
        self.assertEqual([], _group_statements(op))
