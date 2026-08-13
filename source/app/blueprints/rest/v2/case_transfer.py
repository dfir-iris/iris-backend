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

"""v2 REST routes for moving a case between IRIS instances.

Mounted under `/api/v2/cases`::

    POST   /<int:case_identifier>/export   download the case as an archive
    POST   /import/inspect                 upload an archive, get a staging token
    POST   /import                         apply a staged archive
    DELETE /import/<token>                 throw a staged archive away

Export is a POST rather than a GET because the request body may carry the
encryption passphrase, and a GET would put it in the URL — where it would be
kept by access logs, proxies and browser history.

The import passphrase gets the same treatment throughout: it is read, handed
straight to the business layer, and never written to a log, an activity entry
or an error response.
"""

from flask import Blueprint
from flask import after_this_request
from flask import request
from flask import send_file

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.case_transfer.crypto import ArchiveDecryptionError
from app.business.case_transfer.crypto import ArchiveEncryptedError
from app.business.case_transfer.exporter import build_case_archive
from app.business.case_transfer.importer import apply_import
from app.business.case_transfer.importer import discard_staged
from app.business.case_transfer.importer import inspect_upload
from app.business.cases import cases_exists
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import CaseDetailsSchema

case_transfer_blueprint = Blueprint('case_transfer_rest_v2', __name__)

_READ_LEVELS = [CaseAccessLevel.read_only, CaseAccessLevel.full_access]

_UPLOAD_FIELD = 'archive'


def _optional_string(value):
    if value is None:
        return None
    value = str(value)
    return value if value else None


def _requests_placeholder(principal_mapping):
    if not isinstance(principal_mapping, dict):
        return False
    return any(isinstance(decision, dict) and decision.get('action') == 'placeholder'
               for decision in principal_mapping.values())


@case_transfer_blueprint.post('/<int:case_identifier>/export')
@ac_api_requires()
@api_doc(tags=['CaseTransfer'],
         summary='Export a case as a transferable archive')
def export_case(case_identifier):
    """Stream the case out as a `.iris` bundle.

    Body (JSON, all optional):
        include_blobs  bool  ship the Datastore file contents (default true)
        passphrase     str   encrypt the archive with this passphrase
    """
    if not cases_exists(case_identifier):
        return response_api_not_found()

    if not ac_fast_check_current_user_has_case_access(case_identifier, _READ_LEVELS):
        return ac_api_return_access_denied(caseid=case_identifier)

    body = request.get_json(silent=True) or {}
    include_blobs = body.get('include_blobs', True)
    passphrase = _optional_string(body.get('passphrase'))

    try:
        archive_path, download_name, workspace = build_case_archive(
            case_identifier,
            exported_by=iris_current_user.user,
            include_blobs=bool(include_blobs),
            passphrase=passphrase,
        )
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), e.get_data())

    # The archive is a temporary file; it exists only for the length of this
    # response. Cleaning up afterwards keeps a large export from lingering on
    # disk whether or not the download completes.
    @after_this_request
    def _cleanup(response):
        workspace.discard()
        return response

    track_activity(f'exported case {case_identifier}'
                   f'{" (encrypted)" if passphrase else ""}',
                   caseid=case_identifier, ctx_less=False)

    return send_file(archive_path, as_attachment=True, download_name=download_name,
                     mimetype='application/octet-stream')


@case_transfer_blueprint.post('/import/inspect')
@ac_api_requires(Permissions.standard_user)
@api_doc(tags=['CaseTransfer'],
         summary='Upload a case archive and report what importing it would do')
def inspect_import():
    """Stage an uploaded archive without writing anything.

    Multipart form:
        archive     file  the `.iris` bundle
        passphrase  str   required only if the archive is encrypted
    """
    uploaded = request.files.get(_UPLOAD_FIELD)
    if uploaded is None:
        return response_api_error(f'No archive supplied — expected a `{_UPLOAD_FIELD}` file field')

    passphrase = _optional_string(request.form.get('passphrase'))

    try:
        summary = inspect_upload(uploaded.stream, owner_id=iris_current_user.id,
                                 passphrase=passphrase)
    except ArchiveEncryptedError as e:
        # Distinct from a decryption failure so the UI can prompt for a
        # passphrase rather than telling the operator theirs was wrong.
        return response_api_error(e.get_message(), data={'encrypted': True})
    except ArchiveDecryptionError as e:
        return response_api_error(e.get_message())
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), e.get_data())

    return response_api_success(summary)


@case_transfer_blueprint.post('/import')
@ac_api_requires(Permissions.standard_user)
@api_doc(response=CaseDetailsSchema, response_shape='created', tags=['CaseTransfer'],
         summary='Import a previously staged case archive')
def perform_import():
    """Create the case from a staged archive.

    Body (JSON):
        staging_token      str   from `/import/inspect`
        principal_mapping  dict  {"user:12": {"action": "map"|"placeholder"|"importer",
                                              "target_user_id": 5}}
        lookup_decisions   dict  {"ioc_type:3": {"action": "map", "target_id": 7}}
        customer_id        int   override the customer for the imported case
        acl_grants         list  [{"group_id": 2, "access_level": 4}]
    """
    body = request.get_json(silent=True) or {}

    staging_token = body.get('staging_token')
    if not staging_token:
        return response_api_error('No staging token supplied')

    # Minting user accounts is an administrative act; an upload must not be a
    # way around that. The business layer refuses the action outright when this
    # is false, rather than trusting the caller to have hidden the option — the
    # check here only exists so the refusal reads as an authorisation failure
    # instead of a validation error.
    may_create_placeholders = ac_current_user_has_permission(Permissions.server_administrator)
    if not may_create_placeholders and _requests_placeholder(body.get('principal_mapping')):
        return ac_api_return_access_denied()

    try:
        case, report = apply_import(
            staging_token,
            owner_id=iris_current_user.id,
            principal_decisions=body.get('principal_mapping'),
            lookup_decisions=body.get('lookup_decisions'),
            customer_identifier=body.get('customer_id'),
            group_grants=body.get('acl_grants'),
            may_create_placeholders=may_create_placeholders,
        )
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), e.get_data())

    payload = CaseDetailsSchema().dump(case)
    payload['import_report'] = report

    return response_api_created(payload)


@case_transfer_blueprint.delete('/import/<token>')
@ac_api_requires(Permissions.standard_user)
@api_doc(response_shape='deleted', tags=['CaseTransfer'],
         summary='Discard a staged case archive')
def discard_import(token):
    try:
        discard_staged(token, owner_id=iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), e.get_data())

    return response_api_deleted()
