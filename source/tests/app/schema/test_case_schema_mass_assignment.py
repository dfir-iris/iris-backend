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

"""Mass-assignment regression tests for `CaseSchemaForAPIV2`.

`CaseSchemaForAPIV2` is `load_instance=True`. marshmallow-sqlalchemy
resolves a `load()` that passes no `instance=` by fetching the row whose
primary key it finds in the *request body* and setattr-ing every supplied
field onto it (`load_instance_mixin.make_instance`). `POST /api/v2/cases`
legitimately passes no `instance=`.

So while `case_id` was a loadable property, any user holding only
`Permissions.standard_user` could POST a case with someone else's
`case_id` and have the create path adopt that case: overwrite its name,
description, SOC id and customer, then hand itself `full_access` via
`ac_set_new_case_access` while resetting every legitimate responder on
that case to `deny_all`.

These tests pin the primary key — and the other server-owned columns —
shut on input. No DB is needed: the session is stubbed, which is enough
because the point is whether the lookup is reachable at all.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'source'))

os.environ.setdefault('POSTGRES_SERVER', 'localhost')

from app import app

from sqlalchemy import inspect as sa_inspect

from app.models.cases import Cases
from app.schema.marshables import CaseSchemaForAPIV2


def _ctx():
    """Return a pushed Flask application context."""
    ctx = app.app_context()
    ctx.push()
    return ctx


_APP_CTX = _ctx()

VICTIM_CASE_ID = 42

# Columns the server owns. A client must never be able to set them, but
# they must still be serialised on the way out — hence dump_only rather
# than Meta.exclude.
SERVER_OWNED_FIELDS = (
    'case_id',
    'case_uuid',
    'user_id',
    'open_date',
    'modification_history',
)

# Columns a full_access caller may legitimately assign. Listed so that
# locking down the ones above cannot quietly take these with it.
CLIENT_SETTABLE_FIELDS = (
    'owner_id',
    'reviewer_id',
    'state_id',
    'review_status_id',
    'severity_id',
    'classification_id',
    'close_date',
    'status_id',
)


class _SessionStub:
    """Stands in for `db.session`; only `.get()` is ever reached."""

    def __init__(self, rows):
        self._rows = rows
        self.get_calls = []

    def get(self, model, filters):
        self.get_calls.append((model, filters))
        return self._rows.get(filters.get('case_id'))


def _victim_case():
    """Case 42 as it sits in the DB before the attacker's request.

    Built through the instrumentation rather than `Cases(...)` because
    `Cases.__init__` reads `iris_current_user`, which has no meaning here.
    This is how the ORM itself hydrates a row loaded from the database.
    """
    case = sa_inspect(Cases).class_manager.new_instance()
    case.case_id = VICTIM_CASE_ID
    case.name = '#42 - Ransomware at MegaCorp'
    case.description = 'Confidential IR engagement'
    case.soc_id = 'SOC-MEGACORP-001'
    case.client_id = 1
    case.owner_id = 99
    case.user_id = 99
    case.modification_history = {'1': 'created by analyst 99'}
    return case


class TestCaseSchemaServerOwnedFields(unittest.TestCase):
    """The server-owned columns must not be loadable."""

    def setUp(self):
        self.schema = CaseSchemaForAPIV2()

    def test_server_owned_fields_are_dump_only(self):
        for field_name in SERVER_OWNED_FIELDS:
            with self.subTest(field=field_name):
                field = self.schema.fields.get(field_name)
                self.assertIsNotNone(field, f'{field_name} is not a field at all')
                self.assertTrue(field.dump_only,
                                f'{field_name} is loadable — mass assignment is open')

    def test_server_owned_fields_are_still_serialised(self):
        # dump_only, not excluded: the API still returns them.
        for field_name in SERVER_OWNED_FIELDS:
            with self.subTest(field=field_name):
                self.assertFalse(self.schema.fields[field_name].load_only)

    def test_client_settable_fields_stay_loadable(self):
        for field_name in CLIENT_SETTABLE_FIELDS:
            with self.subTest(field=field_name):
                field = self.schema.fields.get(field_name)
                self.assertIsNotNone(field, f'{field_name} is not a field at all')
                self.assertFalse(field.dump_only,
                                 f'{field_name} should remain settable by a full_access caller')


class TestCaseSchemaDoesNotAdoptForeignCase(unittest.TestCase):
    """A create payload carrying someone else's case_id must not adopt it."""

    def setUp(self):
        self.schema = CaseSchemaForAPIV2()
        self.victim = _victim_case()
        self.session = _SessionStub({VICTIM_CASE_ID: self.victim})
        self.attacker = SimpleNamespace(id=7, user='bob')

    def _load(self, **overrides):
        payload = {
            'case_name': 'pwned',
            'case_description': 'attacker controlled',
            'case_soc_id': 'zz',
            'case_customer_id': 7,
        }
        payload.update(overrides)
        # `verify_customer` @pre_load queries Client — the attacker supplies
        # a customer they legitimately hold. `iris_current_user` is read by
        # `Cases.__init__` when a genuinely new row is built.
        with patch('app.schema.marshables.Client') as client_model, \
                patch('app.models.cases.iris_current_user', self.attacker):
            client_model.query.filter.return_value.first.return_value = object()
            return self.schema.load(payload, session=self.session)

    def test_foreign_case_id_does_not_return_the_victim_row(self):
        loaded = self._load(case_id=VICTIM_CASE_ID)
        self.assertIsNot(loaded, self.victim)
        self.assertIsNone(loaded.case_id)

    def test_foreign_case_id_never_triggers_a_primary_key_lookup(self):
        self._load(case_id=VICTIM_CASE_ID)
        self.assertEqual([], self.session.get_calls)

    def test_victim_case_is_left_untouched(self):
        self._load(case_id=VICTIM_CASE_ID,
                   modification_history={'1': 'nothing to see here'})
        self.assertEqual('#42 - Ransomware at MegaCorp', self.victim.name)
        self.assertEqual('Confidential IR engagement', self.victim.description)
        self.assertEqual('SOC-MEGACORP-001', self.victim.soc_id)
        self.assertEqual(1, self.victim.client_id)
        self.assertEqual(99, self.victim.owner_id)
        self.assertEqual({'1': 'created by analyst 99'}, self.victim.modification_history)

    def test_modification_history_is_not_client_writable(self):
        # The case audit trail. A DFIR actor must not be able to edit the
        # record of their own actions on a case.
        loaded = self._load(modification_history={'1': 'forged'})
        self.assertIsNone(loaded.modification_history)

    def test_case_uuid_is_not_client_writable(self):
        loaded = self._load(case_uuid='00000000-0000-0000-0000-000000000000')
        self.assertNotEqual('00000000-0000-0000-0000-000000000000', str(loaded.case_uuid))

    def test_a_normal_create_still_works(self):
        # Locking down the server-owned columns must not break a plain
        # create: a brand-new transient row, no primary key, client-supplied
        # content preserved.
        #
        # `description` is asserted rather than `name` on purpose —
        # `Cases.__init__` assigns name/soc_id/client_id/state_id with a
        # stray trailing comma, so those land as 1-tuples. Pre-existing and
        # unrelated to mass assignment; not pinned here either way.
        loaded = self._load()
        self.assertIsInstance(loaded, Cases)
        self.assertEqual('attacker controlled', loaded.description)
        self.assertIsNone(loaded.case_id)
        self.assertEqual([], self.session.get_calls)


if __name__ == '__main__':
    unittest.main()
