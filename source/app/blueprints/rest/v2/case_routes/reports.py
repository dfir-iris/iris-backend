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

"""Case reports: the templates a report can be generated from, and the
generation itself, streamed back as a file. Readers of the case may
generate one, like the legacy `/case/report/generate-*` routes."""

import shutil
import tempfile

from flask import Blueprint
from flask import request
from flask import send_file

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.cases import cases_exists
from app.business.reports.reports import reports_generate
from app.business.reports.reports import reports_list_templates
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.util import FileRemover


case_reports_blueprint = Blueprint('case_reports_rest_v2', __name__, url_prefix='/<int:case_identifier>/reports')

# Removes the generated file once the response has streamed
_FILE_REMOVER = FileRemover()

_READ_LEVELS = [CaseAccessLevel.read_only, CaseAccessLevel.full_access]


def _access_error(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, _READ_LEVELS):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


@case_reports_blueprint.get('/templates')
@ac_api_requires()
@api_doc(tags=['CaseReports'], summary='List the templates a case report can be generated from')
def case_reports_list_templates(case_identifier):
    access_error = _access_error(case_identifier)
    if access_error:
        return access_error
    return response_api_success(reports_list_templates())


@case_reports_blueprint.post('')
@ac_api_requires()
@api_doc(tags=['CaseReports'], summary='Generate a case report from a template and download it')
def case_reports_generate(case_identifier):
    access_error = _access_error(case_identifier)
    if access_error:
        return access_error

    body = request.get_json(silent=True) or {}
    template_id = body.get('template_id')
    if not isinstance(template_id, int) or isinstance(template_id, bool):
        return response_api_error('Missing or invalid template_id')
    safe_mode = body.get('safe_mode') is True

    tmp_dir = tempfile.mkdtemp()
    try:
        fpath = reports_generate(case_identifier, template_id, safe_mode, tmp_dir)
    except ObjectNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return response_api_not_found()
    except BusinessProcessingError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return response_api_error(e.get_message(), data=e.get_data())

    response = send_file(fpath, as_attachment=True)
    _FILE_REMOVER.cleanup_once_done(response, tmp_dir)
    return response
