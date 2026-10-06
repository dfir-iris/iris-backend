#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""SitRep REST routes + export endpoints."""

from flask import Blueprint, Response, request, send_file
import io

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.war_room_chat import create_message
from app.business.war_room_sitreps import (
    sitrep_as_html,
    sitrep_as_markdown,
    sitrep_attached_case_ids,
    sitrep_auto_draft,
    sitrep_cadence_get,
    sitrep_cadence_set,
    sitrep_delete,
    sitrep_draft,
    sitrep_get,
    sitrep_list,
    sitrep_publish,
    sitrep_update,
)
from app.logger import logger
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_sitreps_blueprint = Blueprint(
    'war_rooms_sitreps_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/sitreps'
)


def _serialize(s, include_body=True):
    d = {
        'sitrep_id': s.sitrep_id,
        'war_room_id': s.war_room_id,
        'version': s.version,
        'title': s.title,
        'authored_by_id': s.authored_by_id,
        'authored_at': s.authored_at.isoformat() if s.authored_at else None,
        'published': bool(s.published),
        'snapshot_json': s.snapshot_json,
    }
    if include_body:
        d['body_md'] = s.body_md
    return d


def _safe_filename(name, ext):
    out = ''.join(c if c.isalnum() or c in ('-', '_') else '-' for c in (name or 'sitrep'))
    out = out.strip('-_') or 'sitrep'
    return f'{out[:100]}.{ext}'


def _is_int_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


_SHARE_MAX_CASES = 200


def _parse_share_targets(raw):
    """`None` (no share), `'all'`, or a de-duplicated list of case ids."""
    if raw is None:
        return None
    if raw == 'all':
        return 'all'
    if not isinstance(raw, list) or not all(_is_int_id(case_id) for case_id in raw):
        raise BusinessProcessingError("share_to_case_ids must be a list of case ids or 'all'")
    if len(raw) > _SHARE_MAX_CASES:
        raise BusinessProcessingError(f'At most {_SHARE_MAX_CASES} cases can be shared to')
    return list(dict.fromkeys(raw))


def _authorize_share_targets(war_room_id, targets):
    """Split targets into denied result rows and allowed case ids.

    Every target must be attached to the room and the caller needs full
    access on it.
    """
    if targets is None:
        return [], []
    attached = sitrep_attached_case_ids(war_room_id)
    case_ids = attached if targets == 'all' else targets
    attached_set = set(attached)
    denied_rows = []
    allowed = []
    for case_id in case_ids:
        if case_id not in attached_set or ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.full_access]
        ) is None:
            denied_rows.append({'case_id': case_id, 'status': 'denied'})
        else:
            allowed.append(case_id)
    return denied_rows, allowed


def _share_to_cases(war_room_id, sit, case_ids):
    if not case_ids:
        return []
    # Late import: the note-sharing module is optional at import time.
    from app.business.war_room_note_shares import war_room_note_shares_copy_to_cases
    try:
        return war_room_note_shares_copy_to_cases(
            war_room_id, f'SitRep v{sit.version} — {sit.title}',
            sitrep_as_markdown(sit), case_ids, iris_current_user.id,
        )
    except Exception:
        logger.exception(f'Sharing SitRep #{sit.sitrep_id} to cases failed')
        return [{'case_id': case_id, 'status': 'error',
                 'message': 'Unable to copy the SitRep into the case'}
                for case_id in case_ids]


def _readable_attached_case_ids(war_room_id):
    return [
        case_id for case_id in sitrep_attached_case_ids(war_room_id)
        if ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
        ) is not None
    ]


@war_rooms_sitreps_blueprint.get('/auto-draft')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Generate a SitRep draft from the war room state')
def get_auto_draft(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        draft = sitrep_auto_draft(war_room_id, _readable_attached_case_ids(war_room_id))
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(draft)


@war_rooms_sitreps_blueprint.get('/cadence')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Get the SitRep cadence')
def get_cadence(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        return response_api_success(sitrep_cadence_get(war_room_id))
    except ObjectNotFoundError:
        return response_api_not_found()


@war_rooms_sitreps_blueprint.put('/cadence')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Set the SitRep cadence')
def set_cadence(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict) or 'cadence_minutes' not in raw:
        return response_api_error('Invalid request')
    kwargs = {}
    if 'reminder_minutes' in raw:
        kwargs['reminder_minutes'] = raw['reminder_minutes']
    try:
        cadence = sitrep_cadence_set(war_room_id, raw['cadence_minutes'], **kwargs)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(cadence)


@war_rooms_sitreps_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='List SitReps')
def list_sitreps(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    rows = sitrep_list(war_room_id)
    return response_api_success(data=[_serialize(s, include_body=False) for s in rows])


@war_rooms_sitreps_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomSitreps'], summary='Draft a SitRep')
def create_sitrep(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json()
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        sit = sitrep_draft(
            war_room_id,
            title=raw.get('title'),
            body_md=raw.get('body_md') or '',
            authored_by_id=iris_current_user.id,
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(_serialize(sit))


@war_rooms_sitreps_blueprint.get('/<int:sitrep_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Get a SitRep')
def get_sitrep(war_room_id, sitrep_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        sit = sitrep_get(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(_serialize(sit))


@war_rooms_sitreps_blueprint.patch('/<int:sitrep_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Update a SitRep')
def update_sitrep(war_room_id, sitrep_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json()
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        sit = sitrep_update(war_room_id, sitrep_id,
                            title=raw.get('title'),
                            body_md=raw.get('body_md'))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_serialize(sit))


@war_rooms_sitreps_blueprint.post('/<int:sitrep_id>/publish')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Publish a SitRep')
def publish_sitrep(war_room_id, sitrep_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    # Optional body; an empty/absent body keeps the historical behaviour.
    raw = request.get_json(silent=True)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        share_targets = _parse_share_targets(raw.get('share_to_case_ids'))
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    try:
        sitrep_get(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    share_rows, allowed_case_ids = _authorize_share_targets(war_room_id, share_targets)
    try:
        sit = sitrep_publish(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    # Mirror the publish event into the chat so the team sees it inline.
    try:
        create_message(
            war_room_id, iris_current_user.id,
            body=f'Published SitRep v{sit.version}: {sit.title}',
            kind='sitrep_published', ref_type='sitrep', ref_id=sit.sitrep_id,
        )
    except Exception:
        pass
    data = _serialize(sit)
    data['shared'] = share_rows + _share_to_cases(war_room_id, sit, allowed_case_ids)
    return response_api_success(data)


@war_rooms_sitreps_blueprint.delete('/<int:sitrep_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomSitreps'], summary='Delete a SitRep')
def delete_sitrep(war_room_id, sitrep_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        sitrep_delete(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_deleted()


@war_rooms_sitreps_blueprint.get('/<int:sitrep_id>/export.md')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Export a SitRep as Markdown')
def export_markdown(war_room_id, sitrep_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        sit = sitrep_get(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    md = sitrep_as_markdown(sit)
    buf = io.BytesIO(md.encode('utf-8'))
    return send_file(
        buf,
        mimetype='text/markdown; charset=utf-8',
        as_attachment=True,
        download_name=_safe_filename(sit.title, 'md'),
    )


@war_rooms_sitreps_blueprint.get('/<int:sitrep_id>/export.html')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Export a SitRep as HTML')
def export_html(war_room_id, sitrep_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        sit = sitrep_get(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return Response(
        sitrep_as_html(sit),
        mimetype='text/html; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="{_safe_filename(sit.title, "html")}"'},
    )


@war_rooms_sitreps_blueprint.get('/<int:sitrep_id>/export.pdf')
@ac_api_requires()
@api_doc(tags=['WarRoomSitreps'], summary='Export a SitRep as PDF')
def export_pdf(war_room_id, sitrep_id):
    """PDF export.

    Best-effort: if `weasyprint` or `xhtml2pdf` is installed in the
    server image we render server-side. If not, we still return the
    rich HTML with a `Content-Disposition: attachment` header that
    most browsers happily print-to-PDF — the operator still gets a
    self-contained, styled document without us shelling out to a
    binary that may not be available.
    """
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        sit = sitrep_get(war_room_id, sitrep_id)
    except ObjectNotFoundError:
        return response_api_not_found()

    html = sitrep_as_html(sit)
    filename = _safe_filename(sit.title, 'pdf')

    try:
        from weasyprint import HTML  # type: ignore
        pdf_bytes = HTML(string=html).write_pdf()
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception:
        pass

    try:
        from xhtml2pdf import pisa  # type: ignore
        out = io.BytesIO()
        result = pisa.CreatePDF(html, dest=out)
        if not result.err:
            out.seek(0)
            return Response(
                out.getvalue(),
                mimetype='application/pdf',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )
    except Exception:
        pass

    # PDF backend unavailable. Return the rich HTML so the browser can
    # print-to-PDF — but flip the extension back to .html so users know
    # what they got.
    fallback_name = _safe_filename(sit.title, 'html')
    return Response(
        html,
        mimetype='text/html; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="{fallback_name}"',
                 'X-Pdf-Backend': 'unavailable'},
    )
