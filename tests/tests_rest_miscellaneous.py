#  IRIS Source Code
#  Copyright (C) 2023 - DFIR-IRIS
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

from unittest import TestCase
from iris import Iris

from time import sleep


class TestsRestMiscellaneous(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_get_api_version_should_not_fail(self):
        response = self._subject.get('api/versions').json()
        self.assertEqual('success', response['status'])

    def test_get_case_graph_should_not_fail(self):
        response = self._subject.get('/case/graph/getdata').json()
        self.assertEqual('success', response['status'])

    def test_create_case_template_should_not_be_forbidden_to_administrator(self):
        query_parameters = {
            'cid': 1
        }
        body = {
            'case_template_json': '{"name": "Template name"}',
        }
        response = self._subject.create('/manage/case-templates/add', body, query_parameters=query_parameters)
        # TODO should really be 201 here
        self.assertEqual(200, response.status_code)

    def test_update_settings_should_return_200(self):
        body = {}
        response = self._subject.create('/manage/settings/update', body)
        self.assertEqual(200, response.status_code)

    def test_get_timeline_state_should_return_200(self):
        response = self._subject.get('/case/timeline/state', query_parameters={'cid': 1})
        self.assertEqual(200, response.status_code)

    # TODO should probably move this in a test suite related to modules?
    # TODO skipping this tests, because it randomly triggers exceptions in the iriswebappp_worker
    #      (psycopg2.errors.NotNullViolation) null value in column "client_id" violates not-null constraint
    #      DETAIL:  Failing row contains (3, null, null, null, null, null, null, 2025-03-14 09:59:04.454669, null, null, null, 0, null, null, 8030efd5-04ae-4337-b55b-3fb0226e2736, null, null, null, null, null).
    #      File "/iriswebapp/app/iris_engine/module_handler/module_handler.py", line 465, in task_hook_wrapper, db.session.commit()
    #
    #      After investigation, this is what seems to happen:
    #      - the app creates the case and publishes a task_hook_wrapper('on_postload_case_create') in Celery
    #      - the app deletes the case
    #      - the worker takes some time to startup
    #      - then it gets the Celery 'on_postload_case_create' task and merges the incoming case data with the state of database
    #        since, by then, the case has already been removed from database, on the identifier and the fields with a server_default are filled
    #        in particulier, client_id is None, and the code fails during the commit
    def test_delete_case_should_set_module_state_to_success(self):
        module_identifier = self._subject.get_module_identifier_by_name('IrisCheck')
        self._subject.create(f'/manage/modules/enable/{module_identifier}', {})
        case_identifier = self._subject.create_dummy_case()
        self._subject.delete(f'/api/v2/cases/{case_identifier}')
        self._subject.create(f'/manage/modules/disable/{module_identifier}', {})

        response = self._subject.get('/dim/tasks/list/1').json()
        attempts = 0
        while len(response['data']) == 0:
            sleep(1)
            response = self._subject.get('/dim/tasks/list/1').json()
            attempts += 1
            if attempts > 20:
                logs = self._subject.extract_logs('worker')
                self.fail(f'Timed out with logs: {logs}')
        module = response['data'][0]

        self.assertEqual('success', module['state'])
