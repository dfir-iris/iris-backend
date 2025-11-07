
from flask import Blueprint
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_wtf import FlaskForm
from app.util import ac_api_requires
from app.util import ac_requires
from app.util import response_error
from app.util import response_success
from datetime import datetime, timedelta
from app.datamgmt.statistics.statistics_db import _build_kpi_payload, _parse_datetime_param
from app.datamgmt.statistics.statistics_db import _build_classification_payload, _build_evidence_payload

stats_blueprint = Blueprint(
    'stats',
    __name__,
    template_folder='templates'
)

_TIMEFRAME_DEFAULT_DAYS = 30


def _resolve_timeframe(args):
    start_raw = args.get('start') or args.get('from')
    end_raw = args.get('end') or args.get('to')

    end_dt = _parse_datetime_param(end_raw)
    if end_raw and end_dt is None:
        raise ValueError('Invalid end timeframe value')

    start_dt = _parse_datetime_param(start_raw)
    if start_raw and start_dt is None:
        raise ValueError('Invalid start timeframe value')

    if end_dt is None:
        end_dt = datetime.utcnow()

    if start_dt is None:
        start_dt = end_dt - timedelta(days=_TIMEFRAME_DEFAULT_DAYS)

    if start_dt > end_dt:
        raise ValueError('Start timeframe must be earlier than end timeframe')

    return start_dt, end_dt


@stats_blueprint.route('/statistics')
@ac_requires()
def statistics(caseid, url_redir):
    if url_redir:
        return redirect(url_for('index.index', cid=caseid if caseid is not None else 1, redirect=True))

    form = FlaskForm()
    return render_template('statistics.html', form=form)


@stats_blueprint.route('/statistics/api/kpis', methods=['GET'])
@ac_api_requires()
def get_statistics_kpis():
    try:
        start_dt, end_dt = _resolve_timeframe(request.args)
    except ValueError as exc:
        return response_error(str(exc))

    client_id = request.args.get('client_id', type=int)
    severity_filter = request.args.get('severity_id', type=int)
    case_status_filter = request.args.get('case_status_id', type=int)

    payload = _build_kpi_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter)
    return response_success('', data=payload)


@stats_blueprint.route('/statistics/api/classifications', methods=['GET'])
@ac_api_requires()
def get_statistics_classifications():
    try:
        start_dt, end_dt = _resolve_timeframe(request.args)
    except ValueError as exc:
        return response_error(str(exc))

    client_id = request.args.get('client_id', type=int)
    severity_filter = request.args.get('severity_id', type=int)
    case_status_filter = request.args.get('case_status_id', type=int)

    payload = _build_classification_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter)
    return response_success('', data=payload)


@stats_blueprint.route('/statistics/api/evidence', methods=['GET'])
@ac_api_requires()
def get_statistics_evidence():
    try:
        start_dt, end_dt = _resolve_timeframe(request.args)
    except ValueError as exc:
        return response_error(str(exc))

    client_id = request.args.get('client_id', type=int)
    severity_filter = request.args.get('severity_id', type=int)
    case_status_filter = request.args.get('case_status_id', type=int)

    payload = _build_evidence_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter)
    return response_success('', data=payload)

