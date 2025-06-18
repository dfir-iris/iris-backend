#  IRIS Source Code
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

import os
import tempfile
import base64

from flask import Blueprint
from flask import request
from flask import send_file

from app.business.errors import ObjectNotFoundError
from app.business.errors import BusinessProcessingError
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.business.reports.reporter import IrisMakeDocReport
from app.business.reports.reporter import IrisMakeMdReport
from app.iris_engine.utils.tracker import track_activity

from app.models.models import CaseTemplateReport
from app.models.authorization import CaseAccessLevel

from app.util import FileRemover
from app.blueprints.access_controls import ac_requires_case_identifier
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.responses import response_error
from app.datamgmt.case.case_db import get_case

reports_rest_blueprint = Blueprint('reports_rest', __name__)

file_remover = FileRemover()


@reports_rest_blueprint.route('/case/report/generate-activities/<int:report_id>', methods=['GET'])
@ac_api_requires()
@ac_requires_case_identifier(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def download_case_activity(report_id, caseid):
    if not report_id:
        return response_error('Unknown report', status=404)
    safe_mode = False
    if request.args.get('safe-mode') == 'true':
        safe_mode = True

    tmp_dir = tempfile.mkdtemp()

    call_modules_hook('on_preload_activities_report_create', data=report_id, caseid=caseid)

    report = CaseTemplateReport.query.filter(CaseTemplateReport.id == report_id).first()
    if not report:
        return response_error('Unknown report', status=404)

    # Get file extension
    _, report_format = os.path.splitext(report.internal_reference)

    # Depending on the template format, the generation process is different
    if report_format == '.docx':
        mreport = IrisMakeDocReport(tmp_dir, report_id, caseid, safe_mode)
        fpath, logs = mreport.generate_doc_report('Activities')

    elif report_format in ('.md', '.html'):
        mreport = IrisMakeMdReport(tmp_dir, report_id, caseid, safe_mode)
        fpath, logs = mreport.generate_md_report('Activities')

    else:
        return response_error('Report error', 'Unknown report format.')

    if fpath is None:
        track_activity('failed to generate a report')
        return response_error(msg='Failed to generate the report', data=logs)

    call_modules_hook('on_postload_activities_report_create', data=report_id, caseid=caseid)
    track_activity('generated a report')

    resp = send_file(fpath, as_attachment=True)
    file_remover.cleanup_once_done(resp, tmp_dir)

    return resp


@reports_rest_blueprint.route('/case/report/generate-investigation/<int:report_id>', methods=['GET'])
@ac_api_requires()
@ac_requires_case_identifier(CaseAccessLevel.read_only, CaseAccessLevel.full_access)
def generate_report(report_id, caseid):
    if not report_id:
        return response_error('Unknown report', status=404)
    safe_mode = False
    if request.args.get('safe-mode') == 'true':
        safe_mode = True

    tmp_dir = tempfile.mkdtemp()

    call_modules_hook('on_preload_report_create', data=report_id, caseid=caseid)

    try:
        fpath = generate_investigation_report(caseid, report_id, safe_mode, tmp_dir)

    except ObjectNotFoundError:
        return response_error('Unknown report', status=404)

    except BusinessProcessingError as e:
        return response_error(e.get_message(), data=e.get_data())

    resp = send_file(fpath, as_attachment=True)
    file_remover.cleanup_once_done(resp, tmp_dir)

    return resp


def generate_investigation_report(caseid, report_id, safe_mode, tmp_dir):
    report = CaseTemplateReport.query.filter(CaseTemplateReport.id == report_id).first()
    if not report:
        raise ObjectNotFoundError()
    _, report_format = os.path.splitext(report.internal_reference)
    if report_format in ('.md', '.html'):
        mreport = IrisMakeMdReport(tmp_dir, report_id, caseid, safe_mode)
        fpath, logs = mreport.generate_md_report('Investigation')

    elif report_format == '.docx':
        mreport = IrisMakeDocReport(tmp_dir, report_id, caseid, safe_mode)
        fpath, logs = mreport.generate_doc_report('Investigation')

    else:
        raise BusinessProcessingError('Report error', data='Unknown report format.')
    if fpath is None:
        track_activity('failed to generate the report')
        raise BusinessProcessingError('Failed to generate the report', data=logs)
    with open(fpath, 'rb') as rfile:
        encoded_file = base64.b64encode(rfile.read()).decode('utf-8')
    res = get_case(caseid)
    _data = {
        'report_id': report_id,
        'file_path': fpath,
        'case_id': res.case_id,
        'user_name': res.user.name,
        'file': encoded_file
    }
    call_modules_hook('on_postload_report_create', data=_data, caseid=caseid)
    track_activity('generated a report')
    return fpath
