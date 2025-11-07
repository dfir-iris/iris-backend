from app import db
from app.models.alerts import Alert
from app.models.alerts import AlertCaseAssociation
from app.models.alerts import AlertResolutionStatus
from app.models.alerts import AlertStatus
from app.models.alerts import Severity
from app.models.cases import Cases
from app.models.models import CaseClassification
from app.models.models import CaseReceivedFile
from app.models.models import CaseStatus
from app.models.models import EvidenceTypes


from sqlalchemy import and_
from sqlalchemy import distinct
from sqlalchemy import func
import re
from datetime import datetime, timedelta
from datetime import timezone
import json
from collections import defaultdict


_OPEN_ALERT_STATUS_NAMES = (
    'New',
    'Assigned',
    'In progress',
    'Pending',
    'Unspecified'
)

_STATUS_CHANGE_REGEX = re.compile(r'"alert_status_id"\s+from\s+"([^\"]*)"\s+to\s+"([^\"]*)"', re.IGNORECASE)

_CASE_STATUS_LABELS = {
    CaseStatus.unknown.value: 'Unknown',
    CaseStatus.false_positive.value: 'False Positive',
    CaseStatus.true_positive_with_impact.value: 'True Positive (Impact)',
    CaseStatus.not_applicable.value: 'Not Applicable',
    CaseStatus.true_positive_without_impact.value: 'True Positive (No Impact)',
    CaseStatus.legitimate.value: 'Legitimate'
}


def _case_status_label(status_id):
    if status_id is None:
        return 'Unspecified'
    return _CASE_STATUS_LABELS.get(status_id, 'Unknown')


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

    candidates = (
        raw,
        raw.replace('Z', '+00:00'),
    )

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



def _safe_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)

    if isinstance(value, str):
        return _parse_datetime_param(value)

    if hasattr(value, 'isoformat'):
        try:
            return _parse_datetime_param(value.isoformat())
        except Exception:
            return None

    return None


def _latest_history_timestamp(history):
    if not history:
        return None

    data = history
    if isinstance(history, str):
        try:
            data = json.loads(history)
        except ValueError:
            return None

    timestamps = []

    if isinstance(data, dict):
        for ts_raw in data.keys():
            try:
                ts_float = float(ts_raw)
            except (TypeError, ValueError):
                continue
            timestamps.append(datetime.fromtimestamp(ts_float, tz=timezone.utc).replace(tzinfo=None))
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            candidate = item.get('timestamp') or item.get('time') or item.get('date')
            ts = _safe_datetime(candidate)
            if ts:
                timestamps.append(ts)

    if not timestamps:
        return None

    return max(timestamps)


def _normalize_history(history):
    if not history:
        return {}

    if isinstance(history, dict):
        return history

    if isinstance(history, str):
        try:
            parsed = json.loads(history)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return {}

    return {}


def _floor_to_bucket(dt, bucket):
    if not dt:
        return None

    if bucket == 'minute':
        return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute)

    if bucket == '5minute':
        minute = (dt.minute // 5) * 5
        return datetime(dt.year, dt.month, dt.day, dt.hour, minute)

    if bucket == '15minute':
        minute = (dt.minute // 15) * 15
        return datetime(dt.year, dt.month, dt.day, dt.hour, minute)

    if bucket == 'hour':
        return datetime(dt.year, dt.month, dt.day, dt.hour)

    if bucket == 'week':
        start_of_day = datetime(dt.year, dt.month, dt.day)
        return start_of_day - timedelta(days=start_of_day.weekday())

    return datetime(dt.year, dt.month, dt.day)


def _determine_time_series_bucket(timeframe_delta):
    total_seconds = timeframe_delta.total_seconds()

    if total_seconds <= 3600:  # <= 1 hour
        return 'minute', timedelta(minutes=1), 'minute'
    if total_seconds <= 6 * 3600:  # <= 6 hours
        return '5minute', timedelta(minutes=5), 'minute'
    if total_seconds <= 24 * 3600:  # <= 1 day
        return '15minute', timedelta(minutes=15), 'minute'
    if total_seconds <= 7 * 24 * 3600:  # <= 1 week
        return 'hour', timedelta(hours=1), 'hour'
    if total_seconds <= 30 * 24 * 3600:  # <= 1 month
        return 'day', timedelta(days=1), 'day'

    return 'week', timedelta(days=7), 'week'


def _alert_handled_timestamp(modification_history, open_status_ids, open_status_names):
    history = _normalize_history(modification_history)
    if not history:
        return None

    entries = []
    for key, details in history.items():
        try:
            timestamp = float(key)
        except (TypeError, ValueError):
            continue
        entries.append((timestamp, details))

    if not entries:
        return None

    entries.sort(key=lambda item: item[0])

    for ts_float, details in entries:
        action = ''
        if isinstance(details, dict):
            action = details.get('action', '') or ''
        else:
            action = str(details)

        if not action:
            continue

        for match in _STATUS_CHANGE_REGEX.finditer(action):
            new_value = match.group(2)
            try:
                new_status_id = int(new_value)
            except (TypeError, ValueError):
                normalized = (new_value or '').strip().lower()
                if normalized and (not open_status_names or normalized not in open_status_names):
                    return datetime.fromtimestamp(ts_float, tz=timezone.utc).replace(tzinfo=None)
                continue

            if not open_status_ids or new_status_id not in open_status_ids:
                return datetime.fromtimestamp(ts_float, tz=timezone.utc).replace(tzinfo=None)

    return None


def _alert_second_history_timestamp(modification_history):
    history = _normalize_history(modification_history)
    if not history:
        return None

    entries = []
    for key, value in history.items():
        try:
            timestamp = float(key)
        except (TypeError, ValueError):
            continue
        entries.append((timestamp, value))

    if len(entries) < 2:
        return None

    entries.sort(key=lambda item: item[0])
    second_entry_ts = entries[1][0]
    return datetime.fromtimestamp(second_entry_ts, tz=timezone.utc).replace(tzinfo=None)


def _get_alert_status_ids_by_names(names):
    if not names:
        return []

    lowered = [name.lower() for name in names]
    rows = AlertStatus.query.with_entities(
        AlertStatus.status_id,
        func.lower(AlertStatus.status_name)
    ).filter(func.lower(AlertStatus.status_name).in_(lowered)).all()

    mapping = {status_name: status_id for status_id, status_name in rows}
    return [mapping[name] for name in lowered if name in mapping]


def _extract_custom_timestamp(custom_attributes, keys):
    if not custom_attributes:
        return None

    data = custom_attributes
    if isinstance(custom_attributes, str):
        try:
            data = json.loads(custom_attributes)
        except ValueError:
            return None

    if not isinstance(data, dict):
        return None

    for key in keys:
        if key in data:
            dt = _safe_datetime(data.get(key))
            if dt:
                return dt

    return None


def _extract_history_timestamp(history, keywords):
    if not history:
        return None

    data = history
    if isinstance(history, str):
        try:
            data = json.loads(history)
        except ValueError:
            return None

    if not isinstance(data, dict):
        return None

    matches = []

    for ts_raw, details in data.items():
        action = ''
        if isinstance(details, dict):
            action = details.get('action', '') or ''
        else:
            action = str(details)

        action_lower = action.lower()
        if not any(keyword in action_lower for keyword in keywords):
            continue

        try:
            ts_float = float(ts_raw)
        except (TypeError, ValueError):
            continue

        matches.append(datetime.fromtimestamp(ts_float, tz=timezone.utc).replace(tzinfo=None))

    if not matches:
        return None

    return min(matches)


def _get_case_phase_timestamp(case_obj, keywords, custom_keys):
    ts = _extract_custom_timestamp(case_obj.custom_attributes, custom_keys)
    if ts:
        return ts

    return _extract_history_timestamp(case_obj.modification_history, keywords)


def _average_seconds(durations):
    values = [delta.total_seconds() for delta in durations if delta is not None]
    if not values:
        return None

    return sum(values) / len(values)


def _duration_payload(avg_seconds):
    if avg_seconds is None:
        return {
            'seconds': None,
            'hours': None
        }

    hours = avg_seconds / 3600
    return {
        'seconds': avg_seconds,
        'hours': hours
    }


def _percentage(numerator, denominator):
    if denominator in (None, 0):
        return None

    return (numerator / denominator) * 100

def _build_case_filters(client_id, severity_filter, case_status_filter):
    filters = []
    if client_id:
        filters.append(Cases.client_id == client_id)
    if severity_filter:
        filters.append(Cases.severity_id == severity_filter)
    if case_status_filter is not None:
        filters.append(Cases.status_id == case_status_filter)
    return filters


def _build_kpi_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter):
    alert_filters = []
    if client_id:
        alert_filters.append(Alert.alert_customer_id == client_id)
    if severity_filter:
        alert_filters.append(Alert.alert_severity_id == severity_filter)

    alert_query = Alert.query.filter(and_(Alert.alert_creation_time >= start_dt, Alert.alert_creation_time <= end_dt))
    if alert_filters:
        alert_query = alert_query.filter(*alert_filters)

    alert_rows = alert_query.with_entities(
        Alert.alert_id,
        Alert.alert_source_event_time,
        Alert.alert_creation_time,
        Alert.alert_resolution_status_id,
        Alert.alert_status_id,
        Alert.alert_owner_id,
        Alert.modification_history,
        Alert.alert_severity_id
    ).all()

    total_alerts = len(alert_rows)

    open_alert_status_ids = set(_get_alert_status_ids_by_names(_OPEN_ALERT_STATUS_NAMES))
    alert_windows = {
        '2h': end_dt - timedelta(hours=2),
        '24h': end_dt - timedelta(hours=24),
        '48h': end_dt - timedelta(hours=48)
    }
    alert_window_keys = list(alert_windows.keys())
    new_alerts_since = {key: 0 for key in alert_windows}
    new_unassigned_alerts_since = {key: 0 for key in alert_windows}
    high_priority_alerts = {
        '2h': 0,
        '24h': 0
    }

    high_critical_severity_rows = Severity.query.with_entities(Severity.severity_id).filter(
        func.lower(Severity.severity_name).in_(('high', 'critical'))
    ).all()
    high_critical_severity_ids = {row[0] for row in high_critical_severity_rows if row and row[0] is not None}

    false_positive_resolution = AlertResolutionStatus.query.with_entities(
        AlertResolutionStatus.resolution_status_id
    ).filter(func.lower(AlertResolutionStatus.resolution_status_name) == 'false positive').first()
    false_positive_resolution_id = false_positive_resolution.resolution_status_id if false_positive_resolution else None

    unknown_resolution_rows = AlertResolutionStatus.query.with_entities(
        AlertResolutionStatus.resolution_status_id
    ).filter(func.lower(AlertResolutionStatus.resolution_status_name).in_(('unknown', 'unspecified'))).all()
    unknown_resolution_status_ids = {row[0] for row in unknown_resolution_rows if row and row[0] is not None}

    escalated_status = AlertStatus.query.with_entities(AlertStatus.status_id).filter(
        func.lower(AlertStatus.status_name) == 'escalated'
    ).first()
    escalated_status_id = escalated_status.status_id if escalated_status else None

    new_status_ids = _get_alert_status_ids_by_names(['New'])
    new_status_id = new_status_ids[0] if new_status_ids else None

    alert_mttd_deltas = []
    alert_mttr_deltas = []
    false_positive_alerts = 0
    escalated_alerts = 0
    resolution_unknown_alerts = 0
    new_status_alerts = 0

    timeframe_delta = end_dt - start_dt
    bucket_key, bucket_step, chart_time_unit = _determine_time_series_bucket(timeframe_delta)

    created_counts = defaultdict(int)

    for row in alert_rows:
        source_time = _safe_datetime(row.alert_source_event_time)
        creation_time = _safe_datetime(row.alert_creation_time)

        if source_time and creation_time and creation_time >= source_time:
            alert_mttd_deltas.append(creation_time - source_time)

        if false_positive_resolution_id and row.alert_resolution_status_id == false_positive_resolution_id:
            false_positive_alerts += 1

        if escalated_status_id and row.alert_status_id == escalated_status_id:
            escalated_alerts += 1

        if row.alert_resolution_status_id is None or row.alert_resolution_status_id in unknown_resolution_status_ids:
            resolution_unknown_alerts += 1

        if new_status_id and row.alert_status_id == new_status_id:
            new_status_alerts += 1

        if creation_time:
            creation_bucket = _floor_to_bucket(creation_time, bucket_key)
            if creation_bucket:
                created_counts[creation_bucket] += 1
            for window_key, threshold in alert_windows.items():
                if creation_time >= threshold:
                    new_alerts_since[window_key] += 1
                    if row.alert_owner_id is None and (not open_alert_status_ids or row.alert_status_id in open_alert_status_ids):
                        new_unassigned_alerts_since[window_key] += 1

            if high_critical_severity_ids and row.alert_severity_id in high_critical_severity_ids:
                condition_matched = row.alert_owner_id is None
                if not condition_matched and new_status_id and row.alert_status_id == new_status_id:
                    condition_matched = True

                if condition_matched:
                    for window_key in ('2h', '24h'):
                        threshold = alert_windows.get(window_key)
                        if threshold and creation_time >= threshold:
                            high_priority_alerts[window_key] += 1

        second_history_timestamp = _alert_second_history_timestamp(row.modification_history)
        if creation_time and second_history_timestamp and second_history_timestamp >= creation_time:
            alert_mttr_deltas.append(second_history_timestamp - creation_time)

    unassigned_alerts_query = Alert.query.filter(Alert.alert_owner_id.is_(None))
    if open_alert_status_ids:
        unassigned_alerts_query = unassigned_alerts_query.filter(Alert.alert_status_id.in_(open_alert_status_ids))
    if client_id:
        unassigned_alerts_query = unassigned_alerts_query.filter(Alert.alert_customer_id == client_id)
    if severity_filter:
        unassigned_alerts_query = unassigned_alerts_query.filter(Alert.alert_severity_id == severity_filter)
    unassigned_alerts_query = unassigned_alerts_query.filter(Alert.alert_creation_time <= end_dt)
    unassigned_alerts_total = unassigned_alerts_query.count()

    alerts_with_case_query = db.session.query(func.count(distinct(AlertCaseAssociation.alert_id))).join(
        Alert, Alert.alert_id == AlertCaseAssociation.alert_id
    ).filter(and_(Alert.alert_creation_time >= start_dt, Alert.alert_creation_time <= end_dt))

    if client_id:
        alerts_with_case_query = alerts_with_case_query.filter(Alert.alert_customer_id == client_id)
    if severity_filter:
        alerts_with_case_query = alerts_with_case_query.filter(Alert.alert_severity_id == severity_filter)

    alerts_with_cases = alerts_with_case_query.scalar() or 0

    mean_time_to_detect_seconds = _average_seconds(alert_mttd_deltas)
    mean_time_to_remediate_alerts_seconds = _average_seconds(alert_mttr_deltas)
    detection_coverage_percent = _percentage(alerts_with_cases, total_alerts)
    incident_escalation_rate_percent = _percentage(escalated_alerts, total_alerts)
    alert_false_positive_rate_percent = _percentage(false_positive_alerts, total_alerts)
    resolution_unknown_ratio_percent = _percentage(resolution_unknown_alerts, total_alerts)
    new_status_ratio_percent = _percentage(new_status_alerts, total_alerts)

    case_filters = _build_case_filters(client_id, severity_filter, case_status_filter)

    cases_detected_query = Cases.query.filter(and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt))
    if case_filters:
        cases_detected_query = cases_detected_query.filter(*case_filters)
    incidents_detected = cases_detected_query.count()

    cases_resolved_query = Cases.query.filter(Cases.close_date.isnot(None))
    if case_filters:
        cases_resolved_query = cases_resolved_query.filter(*case_filters)
    cases_resolved_query = cases_resolved_query.filter(
        Cases.close_date >= start_dt.date(),
        Cases.close_date <= end_dt.date()
    )
    incidents_resolved = cases_resolved_query.count()

    false_positive_cases_query = Cases.query.filter(
        Cases.status_id == CaseStatus.false_positive.value,
        Cases.initial_date >= start_dt,
        Cases.initial_date <= end_dt
    )
    if case_filters:
        false_positive_cases_query = false_positive_cases_query.filter(*case_filters)
    false_positive_cases = false_positive_cases_query.count()

    cases_for_mttr_query = Cases.query.with_entities(Cases.initial_date, Cases.close_date)
    if case_filters:
        cases_for_mttr_query = cases_for_mttr_query.filter(*case_filters)
    cases_for_mttr_query = cases_for_mttr_query.filter(
        Cases.close_date.isnot(None),
        Cases.close_date >= start_dt.date(),
        Cases.close_date <= end_dt.date()
    )

    mttr_deltas = []
    for initial_date, close_date in cases_for_mttr_query:
        start_time = _safe_datetime(initial_date)
        if not start_time or not close_date:
            continue
        close_dt = datetime.combine(close_date, datetime.min.time())
        close_time = _safe_datetime(close_dt)
        if close_time and close_time >= start_time:
            mttr_deltas.append(close_time - start_time)

    cases_for_phase_query = Cases.query
    if case_filters:
        cases_for_phase_query = cases_for_phase_query.filter(*case_filters)
    cases_for_phase_query = cases_for_phase_query.filter(
        and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt)
    )
    cases_for_phase = cases_for_phase_query.all()

    mttc_deltas = []
    mttrv_deltas = []

    for case_obj in cases_for_phase:
        start_time = _safe_datetime(case_obj.initial_date)
        if not start_time:
            continue

        contain_time = _get_case_phase_timestamp(case_obj, ['contain'], ['containment_time', 'contained_at'])
        if contain_time and contain_time >= start_time:
            mttc_deltas.append(contain_time - start_time)

        recover_time = _get_case_phase_timestamp(case_obj, ['recover'], ['recovery_time', 'recovered_at'])
        if recover_time and recover_time >= start_time:
            mttrv_deltas.append(recover_time - start_time)

    mean_time_to_respond_seconds = _average_seconds(mttr_deltas)
    mean_time_to_contain_seconds = _average_seconds(mttc_deltas)
    mean_time_to_recover_seconds = _average_seconds(mttrv_deltas)

    false_positive_rate_percent = _percentage(false_positive_cases, incidents_detected)

    severity_query = db.session.query(Severity.severity_name, func.count(Cases.case_id)).join(
        Cases, Cases.severity_id == Severity.severity_id
    )
    severity_query = severity_query.filter(and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt))
    if case_filters:
        severity_query = severity_query.filter(*case_filters)
    severity_query = severity_query.group_by(Severity.severity_name)
    severity_counts = severity_query.all()

    unspecified_severity_count_query = Cases.query.filter(
        Cases.severity_id.is_(None),
        Cases.initial_date >= start_dt,
        Cases.initial_date <= end_dt
    )
    if case_filters:
        unspecified_severity_count_query = unspecified_severity_count_query.filter(*case_filters)
    unspecified_severity_count = unspecified_severity_count_query.count()

    alert_severity_rows = alert_query.outerjoin(
        Severity, Alert.alert_severity_id == Severity.severity_id
    ).with_entities(
        Severity.severity_name,
        func.count(Alert.alert_id)
    ).group_by(Severity.severity_name).all()

    alert_status_rows = alert_query.outerjoin(
        AlertStatus, Alert.alert_status_id == AlertStatus.status_id
    ).with_entities(
        AlertStatus.status_name,
        func.count(Alert.alert_id)
    ).group_by(AlertStatus.status_name).all()

    alert_resolution_rows = alert_query.outerjoin(
        AlertResolutionStatus, Alert.alert_resolution_status_id == AlertResolutionStatus.resolution_status_id
    ).with_entities(
        AlertResolutionStatus.resolution_status_name,
        func.count(Alert.alert_id)
    ).group_by(AlertResolutionStatus.resolution_status_name).all()

    queue_time_series_points = []
    current_bucket = _floor_to_bucket(start_dt, bucket_key)
    end_bucket = _floor_to_bucket(end_dt, bucket_key)

    if current_bucket and end_bucket:
        while current_bucket <= end_bucket:
            queue_time_series_points.append({
                'date': current_bucket.isoformat(),
                'created': int(created_counts.get(current_bucket, 0))
            })
            current_bucket += bucket_step

    alert_severity_distribution = []
    for severity_name, count in alert_severity_rows:
        alert_severity_distribution.append({
            'severity': severity_name or 'Unspecified',
            'count': count,
            'percentage': _percentage(count, total_alerts)
        })

    alert_status_distribution = []
    for status_name, count in alert_status_rows:
        alert_status_distribution.append({
            'status': status_name or 'Unspecified',
            'count': count,
            'percentage': _percentage(count, total_alerts)
        })

    alert_resolution_distribution = []
    for resolution_name, count in alert_resolution_rows:
        alert_resolution_distribution.append({
            'resolution': resolution_name or 'Unspecified',
            'count': count,
            'percentage': _percentage(count, total_alerts)
        })

    case_severity_distribution = []
    denominator = incidents_detected if incidents_detected else None

    for severity_name, count in severity_counts:
        percent = _percentage(count, denominator) if denominator else None
        case_severity_distribution.append({
            'severity': severity_name or 'Unspecified',
            'count': count,
            'percentage': percent
        })

    if unspecified_severity_count:
        percent = _percentage(unspecified_severity_count, denominator) if denominator else None
        case_severity_distribution.append({
            'severity': 'Unspecified',
            'count': unspecified_severity_count,
            'percentage': percent
        })

    window_thresholds = {key: alert_windows[key].isoformat() for key in alert_window_keys}

    queue_payload = {
        'summary': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        },
        'window_order': alert_window_keys,
        'unassigned_alerts_total': unassigned_alerts_total,
        'new_alerts_total': total_alerts,
        'new_alerts_windows': {key: new_alerts_since.get(key, 0) for key in alert_window_keys},
        'new_unassigned_alerts_windows': {key: new_unassigned_alerts_since.get(key, 0) for key in alert_window_keys},
        'resolution_unknown': {
            'count': resolution_unknown_alerts,
            'ratio_percent': resolution_unknown_ratio_percent
        },
        'status_new': {
            'count': new_status_alerts,
            'ratio_percent': new_status_ratio_percent
        },
        'high_priority_alerts': high_priority_alerts,
        'time_series': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat(),
            'time_unit': chart_time_unit,
            'bucket': bucket_key,
            'points': queue_time_series_points
        },
        'window_thresholds': window_thresholds
    }

    alert_filter_metadata = {
        'open_status_ids': sorted(open_alert_status_ids),
        'new_status_id': new_status_id,
        'unknown_resolution_status_ids': sorted(list(unknown_resolution_status_ids)),
        'escalated_status_id': escalated_status_id,
        'false_positive_resolution_id': false_positive_resolution_id,
        'high_critical_severity_ids': sorted(list(high_critical_severity_ids))
    }

    return {
        'timeframe': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        },
        'filters': {
            'client_id': client_id,
            'severity_id': severity_filter,
            'case_status_id': case_status_filter
        },
        'alerts': {
            'total': total_alerts,
            'false_positive_count': false_positive_alerts,
            'false_positive_rate_percent': alert_false_positive_rate_percent,
            'associated_with_cases': alerts_with_cases,
            'escalated_count': escalated_alerts,
            'severity_distribution': alert_severity_distribution,
            'status_distribution': alert_status_distribution,
            'resolution_distribution': alert_resolution_distribution
        },
        'metrics': {
            'mean_time_to_detect': _duration_payload(mean_time_to_detect_seconds),
            'mean_time_to_remediate_alerts': _duration_payload(mean_time_to_remediate_alerts_seconds),
            'mean_time_to_respond': _duration_payload(mean_time_to_respond_seconds),
            'mean_time_to_contain': _duration_payload(mean_time_to_contain_seconds),
            'mean_time_to_recover': _duration_payload(mean_time_to_recover_seconds),
            'incidents_detected': incidents_detected,
            'incidents_resolved': incidents_resolved,
            'false_positive_incidents': false_positive_cases,
            'false_positive_rate_percent': false_positive_rate_percent,
            'detection_coverage_percent': detection_coverage_percent,
            'incident_escalation_rate_percent': incident_escalation_rate_percent,
            'case_severity_distribution': case_severity_distribution
        },
        'queue': queue_payload,
        'alert_filter_metadata': alert_filter_metadata
    }


def _build_classification_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter):
    case_filters = _build_case_filters(client_id, severity_filter, case_status_filter)

    base_case_query = Cases.query.filter(and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt))
    if case_filters:
        base_case_query = base_case_query.filter(*case_filters)
    total_cases = base_case_query.count()

    classification_query = db.session.query(
        CaseClassification.name,
        func.count(Cases.case_id)
    ).select_from(Cases).outerjoin(
        CaseClassification, Cases.classification_id == CaseClassification.id
    ).filter(and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt))

    if case_filters:
        classification_query = classification_query.filter(*case_filters)

    classification_query = classification_query.group_by(CaseClassification.name)
    rows = classification_query.all()

    items = []
    for name, count in rows:
        items.append({
            'classification': name or 'Unspecified',
            'count': count,
            'percentage': _percentage(count, total_cases)
        })

    items.sort(key=lambda item: item['count'], reverse=True)

    status_query = db.session.query(
        Cases.status_id,
        func.count(Cases.case_id)
    ).filter(and_(Cases.initial_date >= start_dt, Cases.initial_date <= end_dt))

    if case_filters:
        status_query = status_query.filter(*case_filters)

    status_query = status_query.group_by(Cases.status_id)
    status_rows = status_query.all()

    status_items = []
    for status_id, count in status_rows:
        status_items.append({
            'status_id': status_id,
            'status': _case_status_label(status_id),
            'count': count,
            'percentage': _percentage(count, total_cases)
        })

    status_items.sort(key=lambda item: item['count'], reverse=True)

    return {
        'timeframe': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        },
        'filters': {
            'client_id': client_id,
            'severity_id': severity_filter,
            'case_status_id': case_status_filter
        },
        'total_cases': total_cases,
        'classifications': items,
        'status_distribution': status_items
    }


def _build_evidence_payload(start_dt, end_dt, client_id, severity_filter, case_status_filter):
    case_filters = _build_case_filters(client_id, severity_filter, case_status_filter)

    conditions = [
        CaseReceivedFile.date_added >= start_dt,
        CaseReceivedFile.date_added <= end_dt
    ]

    base_evidence_query = db.session.query(CaseReceivedFile.id).join(
        Cases, Cases.case_id == CaseReceivedFile.case_id
    ).filter(*conditions)

    if case_filters:
        base_evidence_query = base_evidence_query.filter(*case_filters)

    total_evidences = base_evidence_query.count()

    total_size_query = db.session.query(func.coalesce(func.sum(CaseReceivedFile.file_size), 0)).join(
        Cases, Cases.case_id == CaseReceivedFile.case_id
    ).filter(*conditions)

    if case_filters:
        total_size_query = total_size_query.filter(*case_filters)

    total_size = total_size_query.scalar() or 0

    distribution_query = db.session.query(
        EvidenceTypes.name,
        func.count(CaseReceivedFile.id),
        func.coalesce(func.sum(CaseReceivedFile.file_size), 0)
    ).select_from(CaseReceivedFile).outerjoin(
        EvidenceTypes, CaseReceivedFile.type_id == EvidenceTypes.id
    ).join(
        Cases, Cases.case_id == CaseReceivedFile.case_id
    ).filter(*conditions)

    if case_filters:
        distribution_query = distribution_query.filter(*case_filters)

    distribution_query = distribution_query.group_by(EvidenceTypes.name)
    rows = distribution_query.all()

    items = []
    for name, count, size_bytes in rows:
        items.append({
            'type': name or 'Unspecified',
            'count': count,
            'percentage': _percentage(count, total_evidences),
            'size_bytes': int(size_bytes or 0)
        })

    items.sort(key=lambda item: item['count'], reverse=True)

    return {
        'timeframe': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        },
        'filters': {
            'client_id': client_id,
            'severity_id': severity_filter,
            'case_status_id': case_status_filter
        },
        'total_evidences': total_evidences,
        'total_size_bytes': int(total_size or 0),
        'evidence_types': items
    }