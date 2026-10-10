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

"""Case report generation: the template's report type picks the generator."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.reports.reports import reports_generate
from app.business.reports.reports import reports_list_templates
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_MODULE = 'app.business.reports.reports'


def _template(report_type, identifier=3, reference='abc.docx'):
    return SimpleNamespace(id=identifier, name='Weekly', description='d', internal_reference=reference,
                           report_type=SimpleNamespace(name=report_type) if report_type else None,
                           language=SimpleNamespace(name='english'))


class TestsReportsGenerate(TestCase):

    def test_investigation_template_should_use_the_investigation_generator(self):
        with patch(f'{_MODULE}.case_db_get_report_template', return_value=_template('Investigation')), \
                patch(f'{_MODULE}.generate_investigation_report', return_value='/tmp/r.docx') as generate:
            self.assertEqual('/tmp/r.docx', reports_generate(4, 3, True, '/tmp'))
        generate.assert_called_once_with(4, 3, True, '/tmp')

    def test_activities_template_should_use_the_activities_generator(self):
        with patch(f'{_MODULE}.case_db_get_report_template', return_value=_template('Activities')), \
                patch(f'{_MODULE}.generate_activities_report', return_value='/tmp/a.md') as generate:
            self.assertEqual('/tmp/a.md', reports_generate(4, 3, False, '/tmp'))
        generate.assert_called_once_with(4, 3, False, '/tmp')

    def test_unknown_template_should_raise_not_found(self):
        with patch(f'{_MODULE}.case_db_get_report_template', return_value=None):
            with self.assertRaises(ObjectNotFoundError):
                reports_generate(4, 3, False, '/tmp')

    def test_template_without_type_should_be_refused(self):
        with patch(f'{_MODULE}.case_db_get_report_template', return_value=_template(None)):
            with self.assertRaises(BusinessProcessingError):
                reports_generate(4, 3, False, '/tmp')

    def test_list_should_give_type_language_and_format(self):
        with patch(f'{_MODULE}.case_db_list_report_templates', return_value=[_template('Activities', reference='x.MD')]):
            self.assertEqual([{'id': 3, 'name': 'Weekly', 'description': 'd', 'report_type': 'Activities',
                               'language': 'english', 'format': 'md'}], reports_list_templates())
