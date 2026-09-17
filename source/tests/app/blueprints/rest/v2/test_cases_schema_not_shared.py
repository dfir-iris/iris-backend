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

"""`CasesOperations` must not share a `CaseSchemaForAPIV2` between requests.

`cases_operations` is a module-level singleton, so anything it keeps on
`self` is shared by every concurrent request. `CaseSchemaForAPIV2` is
`load_instance=True`: `load(instance=...)` parks that instance on the schema
object for the duration of the call, and the `verify_customer` `@pre_load`
issues a DB query, which yields under gevent. A schema held on the singleton
could therefore be entered by request B while request A is parked inside its
own load, and B would finish against A's case.

What is pinned here is the invariant, not the race: a gevent interleaving
cannot be produced deterministically, and a test that pretended to would be
theatre. So — no schema state on the singleton, and one fresh schema per
call, asserted on every operation that loads or dumps one.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app import app
from app.blueprints.rest.v2.cases import CasesOperations
from app.blueprints.rest.v2.cases import cases_operations
from app.models.authorization import CaseAccessLevel

_CASE_IDENTIFIER = 11
_CUSTOMER_IDENTIFIER = 1

_CASES_MODULE = 'app.blueprints.rest.v2.cases'


class TestOperationsHoldsNoSchema(TestCase):
    """The singleton must carry no schema state at all."""

    def test_the_singleton_has_no_schema_attribute(self):
        self.assertFalse(hasattr(cases_operations, '_schema'))

    def test_the_class_has_no_schema_attribute(self):
        # A class attribute would be shared just as widely as an instance one.
        self.assertFalse(hasattr(CasesOperations, '_schema'))

    def test_the_singleton_carries_no_instance_state(self):
        self.assertEqual({}, vars(cases_operations))


class _SchemaIsolationTestCase(TestCase):
    """`CaseSchemaForAPIV2` hands out a distinct mock per instantiation."""

    def setUp(self):
        self.case = SimpleNamespace(
            case_id=_CASE_IDENTIFIER,
            client_id=_CUSTOMER_IDENTIFIER,
            state_id=1,
            reviewer_id=None
        )

        self.schemas = [MagicMock(name='schema-0'), MagicMock(name='schema-1')]
        for schema in self.schemas:
            schema.load.return_value = self.case
            schema.dump.return_value = {}

        self.schema_class = patch(f'{_CASES_MODULE}.CaseSchemaForAPIV2',
                                  side_effect=self.schemas).start()
        self.addCleanup(patch.stopall)

    def _assert_a_schema_per_request(self, method_name, uses_per_request):
        """Two calls, two schemas, and the work split evenly between them.

        `uses_per_request` is how many times one call touches its own schema —
        1 for the read-only operations, 2 for the ones that load then dump.
        """
        self.assertEqual(2, self.schema_class.call_count,
                         f'{method_name} reused a schema across the two requests')
        for index, schema in enumerate(self.schemas):
            uses = schema.load.call_count + schema.dump.call_count
            self.assertEqual(uses_per_request, uses,
                             f'{method_name} used schema-{index} {uses} times, '
                             f'expected {uses_per_request}')


class TestCreateBuildsItsOwnSchema(_SchemaIsolationTestCase):

    def setUp(self):
        super().setUp()
        patch(f'{_CASES_MODULE}.call_deprecated_on_preload_modules_hook',
              side_effect=lambda _hook, data: data).start()
        patch(f'{_CASES_MODULE}.cases_create', return_value=self.case).start()

    def _create(self):
        body = {
            'case_name': 'case name',
            'case_description': 'description',
            'case_customer_id': _CUSTOMER_IDENTIFIER,
            'case_soc_id': ''
        }
        with app.test_request_context('/api/v2/cases', method='POST', json=body):
            return cases_operations.create()

    def test_two_creates_build_two_schemas(self):
        self._create()
        self._create()
        self._assert_a_schema_per_request('create', uses_per_request=2)

    def test_the_second_create_does_not_load_through_the_first_schema(self):
        # The load is where `instance` gets parked, so this is the call that
        # must never be seen twice on the same object.
        self._create()
        self._create()
        self.schemas[0].load.assert_called_once()
        self.schemas[1].load.assert_called_once()


class TestUpdateBuildsItsOwnSchema(_SchemaIsolationTestCase):

    def setUp(self):
        super().setUp()
        patch(f'{_CASES_MODULE}.cases_get_by_identifier', return_value=self.case).start()
        patch(f'{_CASES_MODULE}.ac_fast_check_current_user_has_case_access',
              return_value=CaseAccessLevel.full_access.value).start()
        patch(f'{_CASES_MODULE}.cases_update', return_value=self.case).start()

    def _update(self):
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}', method='PUT',
                                      json={'case_name': 'new name'}):
            return cases_operations.update(_CASE_IDENTIFIER)

    def test_two_updates_build_two_schemas(self):
        self._update()
        self._update()
        self._assert_a_schema_per_request('update', uses_per_request=2)

    def test_the_second_update_does_not_load_through_the_first_schema(self):
        # The load is where `instance` gets parked, so this is the call that
        # must never be seen twice on the same object.
        self._update()
        self._update()
        self.schemas[0].load.assert_called_once()
        self.schemas[1].load.assert_called_once()

    def test_the_second_update_does_not_dump_through_the_first_schema(self):
        self._update()
        self._update()
        self.schemas[0].dump.assert_called_once()
        self.schemas[1].dump.assert_called_once()


class TestReadBuildsItsOwnSchema(_SchemaIsolationTestCase):

    def setUp(self):
        super().setUp()
        patch(f'{_CASES_MODULE}.cases_get_by_identifier', return_value=self.case).start()
        patch(f'{_CASES_MODULE}.ac_fast_check_current_user_has_case_access',
              return_value=CaseAccessLevel.full_access.value).start()

    def _read(self):
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}'):
            return cases_operations.read(_CASE_IDENTIFIER)

    def test_two_reads_build_two_schemas(self):
        self._read()
        self._read()
        self._assert_a_schema_per_request('read', uses_per_request=1)


class TestSearchBuildsItsOwnSchema(_SchemaIsolationTestCase):

    def setUp(self):
        super().setUp()
        patch(f'{_CASES_MODULE}.cases_filter', return_value=SimpleNamespace(
            items=[], total=0, pages=0, page=1, has_next=False
        )).start()

    def _search(self):
        with app.test_request_context('/api/v2/cases'):
            return cases_operations.search()

    def test_two_searches_build_two_schemas(self):
        self._search()
        self._search()
        self._assert_a_schema_per_request('search', uses_per_request=1)
