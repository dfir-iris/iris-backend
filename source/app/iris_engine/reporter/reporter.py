#  IRIS Source Code
#  Copyright (C) 2022 - DFIR IRIS Team
#  contact@dfir-iris.org
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

import logging as log
import os
from datetime import datetime

from app.iris_engine.access_control.iris_user import iris_current_user
from docx_generator.docx_generator import DocxGenerator
from docx_generator.exceptions import rendering_error

from app import app
from app.business.cases import cases_export_to_json

from app.datamgmt.activities.activities_db import get_auto_activities
from app.datamgmt.activities.activities_db import get_manual_activities

from app.models.models import CaseTemplateReport

from app.iris_engine.reporter.ImageHandler import ImageHandler
from app.iris_engine.utils.common import IrisJinjaEnv

LOG_FORMAT = '%(asctime)s :: %(levelname)s :: %(module)s :: %(funcName)s :: %(message)s'
log.basicConfig(level=log.INFO, format=LOG_FORMAT)


def _get_docid():
    return '{}'.format(datetime.utcnow().strftime('%y%m%d_%H%M'))


def _get_case_info(case_identifier):
    case_info = cases_export_to_json(case_identifier)

    # Get customer, user and case title
    case_info['doc_id'] = _get_docid()
    case_info['user'] = iris_current_user.name

    # Set date
    case_info['date'] = datetime.utcnow().strftime("%Y-%m-%d")
    customer_name = case_info['case'].get('client').get('customer_name')
    case_info['case']['for_customer'] = f'{customer_name} (legacy::use client.customer_name)'

    return case_info


def _get_activity_info(case_identifier):
    auto_activities = get_auto_activities(case_identifier)
    manual_activities = get_manual_activities(case_identifier)
    case_info_in = _get_case_info(case_identifier)

    doc_id = _get_docid()

    case_info = {
        'auto_activities': auto_activities,
        'manual_activities': manual_activities,
        'date': datetime.utcnow(),
        'gen_user': iris_current_user.name,
        'case': {'name': case_info_in['case'].get('name'),
                 'open_date': case_info_in['case'].get('open_date'),
                 'for_customer': case_info_in['case'].get('for_customer'),
                 'client': case_info_in['case'].get('client')
                 },
        'doc_id': doc_id
    }

    return case_info


class IrisMakeDocReport:
    """
    Generates a DOCX report for the case
    """

    def __init__(self, tmp_dir, report_id, caseid, safe_mode=False):
        self._tmp = tmp_dir
        self._report_id = report_id
        self._case_info = {}
        self._caseid = caseid
        self._safe_mode = safe_mode

    def generate_doc_report(self, doc_type):
        """
        Actually generates the report
        :return:
        """
        if doc_type == 'Investigation':
            case_info = _get_case_info(self._caseid)
        elif doc_type == 'Activities':
            case_info = _get_activity_info(self._caseid)
        else:
            log.error("Unknown report type")
            return None

        report = CaseTemplateReport.query.filter(CaseTemplateReport.id == self._report_id).first()

        name = f'{report.naming_format}.docx'
        name = name.replace("%code_name%", case_info['doc_id'])
        name = name.replace('%customer%', case_info['case']['client']['customer_name'])
        name = name.replace('%case_name%', case_info['case'].get('name'))
        name = name.replace('%date%', datetime.utcnow().strftime("%Y-%m-%d"))
        output_file_path = os.path.join(self._tmp, name)

        try:

            if not self._safe_mode:
                image_handler = ImageHandler(template=None, base_path='/')
            else:
                image_handler = None

            generator = DocxGenerator(image_handler=image_handler)
            generator.generate_docx("/",
                                    os.path.join(app.config['TEMPLATES_PATH'], report.internal_reference),
                                    case_info,
                                    output_file_path
                                    )

            return output_file_path, ""

        except rendering_error.RenderingError as e:

            return None, e.__str__()


class IrisMakeMdReport:
    """
    Generates a MD report for the case
    """

    def __init__(self, tmp_dir, report_id, caseid, safe_mode=False):
        self._tmp = tmp_dir
        self._report_id = report_id
        self._case_info = {}
        self._caseid = caseid
        self.safe_mode = safe_mode

    def get_case_info(self, doc_type):
        """Returns case information

        Args:
            doc_type (_type_): Investigation or Activities report

        Returns:
            _type_: case info
        """
        if doc_type == 'Investigation':
            case_info = _get_case_info(self._caseid)
        elif doc_type == 'Activities':
            case_info = _get_activity_info(self._caseid)
        else:
            log.error("Unknown report type")
            return None
        return case_info

    def generate_md_report(self, doc_type):
        """
        Generate report file
        """
        case_info = self.get_case_info(doc_type)
        if case_info is None:
            return None

        # Get file extension
        report = CaseTemplateReport.query.filter(
            CaseTemplateReport.id == self._report_id).first()

        _, report_format = os.path.splitext(report.internal_reference)

        # Prepare report name
        name = "{}".format(("{}" + str(report_format)).format(report.naming_format))
        name = name.replace("%code_name%", case_info['doc_id'])
        name = name.replace(
            '%customer%', case_info['case'].get('client').get('customer_name'))
        name = name.replace('%case_name%', case_info['case'].get('name'))
        name = name.replace('%date%', datetime.utcnow().strftime("%Y-%m-%d"))

        # Build output file
        output_file_path = os.path.join(self._tmp, name)

        try:
            env = IrisJinjaEnv()
            env.filters = app.jinja_env.filters

            template_path = os.path.join(app.config['TEMPLATES_PATH'], report.internal_reference)
            with open(template_path, 'r', encoding="utf-8") as template_file:
                template = env.from_string(template_file.read())

            output_text = template.render(case_info)

            with open(output_file_path, 'w', encoding="utf-8") as html_file:
                html_file.write(output_text)

        except Exception as e:
            log.exception(f'Error while generating report: {e}')
            return None, e.__str__()

        return output_file_path, 'Report generated'
