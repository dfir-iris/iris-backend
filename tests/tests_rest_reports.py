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
from io import BytesIO
from docx import Document

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY


class TestsRestReports(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()
        response = self._subject.get('/manage/templates/list').json()
        for report_template in response['data']:
            identifier = report_template['id']
            self._subject.create(f'/manage/templates/delete/{identifier}', {})

    def test_generate_docx_report__in_safe_mode_should_return_200(self):
        data = {'report_name': 'name', 'report_type': 1, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'empty.docx')
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_identifier, 'safe': True})
        self.assertEqual(200, response.status_code)

    def test_generate_docx_report_should_render_variable_case_for_customer(self):
        data = {'report_name': 'name', 'report_type': 1, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'variable_case_for_customer.docx')
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_identifier, 'safe': True})
        with BytesIO(response.content) as content:
            document = Document(content)
            self.assertEqual('IrisInitialClient (legacy::use client.customer_name)', document.paragraphs[0].text)

    def test_generate_md_report_should_render_variable_case_name(self):
        data = {'report_name': 'name', 'report_type': 1, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'variable_case_name.md')
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_identifier, 'safe': True})
        self.assertEqual(f'#{case_identifier} - case name', response.text)

    def test_generate_md_report_should_render_variable_case_for_customer(self):
        data = {'report_name': 'name', 'report_type': 1, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'variable_case_for_customer.md')
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_identifier, 'safe': True})
        self.assertEqual('IrisInitialClient (legacy::use client.customer_name)', response.text)

    def test_generate_md_activities_report_should_render_variable_case_for_customer_when(self):
        data = {'report_name': 'name', 'report_type': 2, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'variable_case_for_customer.md')
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/case/report/generate-activities/{report_identifier}',
                                     {'cid': case_identifier, 'safe': True})
        self.assertEqual('IrisInitialClient (legacy::use client.customer_name)', response.text)

    def _create_investigation_template(self, filename='variable_case_name.md'):
        data = {'report_name': 'name', 'report_type': 1, 'report_language': 1, 'report_description': 'description',
                'report_name_format': 'report_name_format'}
        return self._subject.create_report(data, filename)

    def test_list_case_report_templates_should_return_the_template_with_its_type(self):
        report_identifier = self._create_investigation_template()
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/reports/templates').json()
        template = next(t for t in response if t['id'] == report_identifier)
        self.assertEqual('Investigation', template['report_type'])
        self.assertEqual('md', template['format'])

    def test_generate_case_report_should_return_the_rendered_file(self):
        report_identifier = self._create_investigation_template()
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/reports',
                                        {'template_id': report_identifier, 'safe_mode': True})
        self.assertEqual(200, response.status_code)
        self.assertEqual(f'#{case_identifier} - case name', response.text)

    def test_generate_case_report_should_return_400_without_template(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/reports', {})
        self.assertEqual(400, response.status_code)

    def test_generate_case_report_should_return_404_for_an_unknown_template(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/reports', {'template_id': 999999})
        self.assertEqual(404, response.status_code)

    def test_generate_case_report_should_be_allowed_to_a_read_only_user(self):
        report_identifier = self._create_investigation_template()
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_identifier, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        response = user.create(f'/api/v2/cases/{case_identifier}/reports', {'template_id': report_identifier})
        self.assertEqual(200, response.status_code)

    def test_generate_case_report_should_return_403_to_a_user_without_access(self):
        report_identifier = self._create_investigation_template()
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        response = user.create(f'/api/v2/cases/{case_identifier}/reports', {'template_id': report_identifier})
        self.assertEqual(403, response.status_code)
