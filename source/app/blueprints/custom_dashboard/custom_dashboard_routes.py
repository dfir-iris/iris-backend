from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, render_template
from flask_login import current_user
from flask_wtf import FlaskForm
from app.models.authorization import Permissions

from app.datamgmt.custom_dashboard.custom_dashboard_db import (
    list_dashboards_for_user,
    get_dashboard_for_user,
    create_dashboard_for_user,
    update_dashboard_for_user,
    delete_dashboard_for_user,
    DashboardAccessError,
    DashboardNotFoundError
)
from app.datamgmt.custom_dashboard.schema import CustomDashboardSchema
from app.datamgmt.custom_dashboard.query_engine import execute_widget, format_widget_payload, QueryExecutionError
from app.util import ac_api_requires, ac_requires

from app import app, ac_current_user_has_permission

custom_dashboard_blueprint = Blueprint(
    'custom_dashboard',
    __name__,
    template_folder='templates'
)

_dashboard_schema = CustomDashboardSchema()

_TIMEFRAME_DEFAULT_DAYS = 30


def _parse_datetime_param(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)

    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    candidates = (raw, raw.replace('Z', '+00:00'))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed
        except ValueError:
            continue

    return None


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


@custom_dashboard_blueprint.route('/custom-dashboards')
@ac_requires(Permissions.custom_dashboards_read, no_cid_required=True)
def dashboard_index(caseid, url_redir):
    form = FlaskForm()
    can_share = ac_current_user_has_permission(Permissions.custom_dashboards_share, Permissions.server_administrator)
    if url_redir:
        return render_template('custom_dashboards/index.html', case_id=caseid, form=form, can_share=can_share)

    dashboards = list_dashboards_for_user(current_user.id)
    return render_template('custom_dashboards/index.html', dashboards=dashboards, case_id=caseid, form=form, can_share=can_share)


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards', methods=['GET'])
@ac_api_requires(Permissions.custom_dashboards_read)
def api_list_dashboards():
    dashboards = list_dashboards_for_user(current_user.id)
    payload = [
        {
            'id': dashboard.id,
            'uuid': str(dashboard.dashboard_uuid),
            'name': dashboard.name,
            'description': dashboard.description,
            'is_shared': dashboard.is_shared,
            'definition': dashboard.definition,
            'widgets': [widget.definition for widget in dashboard.widgets]
        }
        for dashboard in dashboards
    ]
    return jsonify({'status': 'success', 'data': payload})


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards/<int:dashboard_id>', methods=['GET'])
@ac_api_requires(Permissions.custom_dashboards_read)
def api_get_dashboard(dashboard_id):
    try:
        dashboard = get_dashboard_for_user(dashboard_id, current_user.id)
    except DashboardNotFoundError:
        return jsonify({'status': 'failure', 'message': 'Dashboard not found'}), 404
    except DashboardAccessError:
        return jsonify({'status': 'failure', 'message': 'Access denied'}), 403

    payload = {
        'id': dashboard.id,
        'uuid': str(dashboard.dashboard_uuid),
        'name': dashboard.name,
        'description': dashboard.description,
        'is_shared': dashboard.is_shared,
        'definition': dashboard.definition,
        'widgets': [widget.definition for widget in dashboard.widgets]
    }

    return jsonify({'status': 'success', 'data': payload})


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards/<int:dashboard_id>/data', methods=['GET'])
@ac_api_requires(Permissions.custom_dashboards_read)
def api_get_dashboard_data(dashboard_id):
    try:
        dashboard = get_dashboard_for_user(dashboard_id, current_user.id)
    except DashboardNotFoundError:
        return jsonify({'status': 'failure', 'message': 'Dashboard not found'}), 404
    except DashboardAccessError:
        return jsonify({'status': 'failure', 'message': 'Access denied'}), 403

    try:
        start_dt, end_dt = _resolve_timeframe(request.args)
    except ValueError as exc:
        return jsonify({'status': 'failure', 'message': str(exc)}), 400

    timeframe_tuple = (start_dt, end_dt)
    widgets_payload = []

    ordered_widgets = sorted(dashboard.widgets, key=lambda item: ((item.position or 0), item.id))

    for widget in ordered_widgets:
        definition = widget.definition or {}
        widget_payload = {
            'widget_id': str(widget.widget_uuid),
            'name': definition.get('name') or widget.name,
            'chart_type': widget.chart_type,
            'definition': definition
        }

        try:
            result = execute_widget(definition, timeframe_tuple)
            widget_payload['data'] = format_widget_payload(result, definition)
        except QueryExecutionError as exc:
            widget_payload['error'] = str(exc)
        except Exception as exc:  # noqa: BLE001
            widget_payload['error'] = 'Unexpected error while executing widget.'
            app.logger.exception('Failed to execute custom dashboard widget %s', widget.widget_uuid)

        widgets_payload.append(widget_payload)

    response_payload = {
        'dashboard': {
            'id': dashboard.id,
            'uuid': str(dashboard.dashboard_uuid),
            'name': dashboard.name,
            'description': dashboard.description,
            'is_shared': dashboard.is_shared
        },
        'timeframe': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        },
        'widgets': widgets_payload
    }

    return jsonify({'status': 'success', 'data': response_payload})


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards', methods=['POST'])
@ac_api_requires(Permissions.custom_dashboards_write)
def api_create_dashboard():
    payload = request.get_json(force=True) or {}
    payload.pop('csrf_token', None)
    allow_share = ac_current_user_has_permission(Permissions.custom_dashboards_share, Permissions.server_administrator)

    errors = _dashboard_schema.validate(payload)
    if errors:
        return jsonify({'status': 'failure', 'message': 'Invalid payload', 'errors': errors}), 400

    dashboard = create_dashboard_for_user(current_user.id, payload, allow_share)

    return jsonify({'status': 'success', 'data': {'id': dashboard.id}}), 201


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards/<int:dashboard_id>', methods=['PUT'])
@ac_api_requires(Permissions.custom_dashboards_write)
def api_update_dashboard(dashboard_id):
    payload = request.get_json(force=True) or {}
    payload.pop('csrf_token', None)
    allow_share = ac_current_user_has_permission(Permissions.custom_dashboards_share, Permissions.server_administrator)
    errors = _dashboard_schema.validate(payload)
    if errors:
        return jsonify({'status': 'failure', 'message': 'Invalid payload', 'errors': errors}), 400

    try:
        dashboard = update_dashboard_for_user(dashboard_id, current_user.id, payload, allow_share)
    except DashboardNotFoundError:
        return jsonify({'status': 'failure', 'message': 'Dashboard not found'}), 404
    except DashboardAccessError:
        return jsonify({'status': 'failure', 'message': 'Access denied'}), 403

    return jsonify({'status': 'success', 'data': {'id': dashboard.id}})


@custom_dashboard_blueprint.route('/custom-dashboards/api/dashboards/<int:dashboard_id>', methods=['DELETE'])
@ac_api_requires(Permissions.custom_dashboards_write)
def api_delete_dashboard(dashboard_id):
    try:
        delete_dashboard_for_user(dashboard_id, current_user.id)
    except DashboardNotFoundError:
        return jsonify({'status': 'failure', 'message': 'Dashboard not found'}), 404
    except DashboardAccessError:
        return jsonify({'status': 'failure', 'message': 'Access denied'}), 403

    return jsonify({'status': 'success'})
