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
from unittest import skip
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
    # TODO skipping this tests, before it randomly triggers exceptions during the iriswebappp_worker initialization
    #      (depending on the order into which things get executed). Should investigate.
    #      Hint: I think the problem happens when starting from an empty database.
    #      It may be the case that celery tasks (hence api requests?) are executed before the database is fully created
    #      Initialization should be done in the following order?
    #      - in the worker: ensure database is created, load database schemas, register celery tasks
    #      - in the app: create database, ensure worker is ready, register API endpoints
    #      (maybe it shouldn't be the app responsibility to create and fill the database, but another independent task
    #      which should be ended before everything else?)
    @skip
    def test_create_case_should_not_raise_exception_when_module_is_enabled(self):
        response = self._subject.get('/manage/modules/list').json()
        module_identifier = None
        for module in response['data']:
            if module['module_human_name'] == 'IrisCheck':
                module_identifier = module['id']
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
