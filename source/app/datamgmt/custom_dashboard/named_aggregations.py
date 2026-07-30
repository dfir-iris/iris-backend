"""Post-query named aggregations for custom dashboards.

The query engine in query_engine.py is declarative: table.column + whitelisted
SQL aggregations + whitelisted filter operators. Some statistics (MTTD, MTTR,
false-positive rate, escalation rate, sliding-window alert counts) cannot be
expressed as a single SQL aggregation because they walk JSON modification
history rows or compose two counts. They live here as Python helpers that the
render endpoint resolves when a widget references table=='computed'.

A widget that uses a named aggregation looks like:

    {
        "name": "Mean time to detect",
        "chart_type": "number",
        "fields": [{"table": "computed", "column": "mttd_seconds", "alias": "mttd_seconds"}]
    }

The registry is intentionally small and additive: future named aggregations
register a callable(timeframe, filters, scope) -> Optional[float]. The engine
never touches user-supplied identifiers; only registered names resolve.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select

from app import db
# NOTE: use `iris_current_user`, not `flask_login.current_user`. IRIS's
# v2 REST endpoints authenticate via JWT — the request-scoped user
# lives in `g.auth_user` (populated by `ac_api_requires`) and Flask-
# Login's `current_user` is `AnonymousUserMixin`. Using `current_user`
# here made every non-admin dashboard silently return zero rows
# (`Alert.alert_id == -1` fallback), because is_authenticated=False +
# id=None. `iris_current_user` transparently returns the TokenUser
# under JWT and falls back to flask-login for session auth.
from app.blueprints.iris_user import iris_current_user
from app.datamgmt.manage.manage_access_control_db import get_user_clients_id
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.access_control.utils import ac_get_fast_user_cases_access
from app.models.alerts import Alert, AlertCaseAssociation, AlertResolutionStatus, AlertStatus
from app.models.authorization import Permissions, ac_flag_match_mask


def _current_user_is_server_admin() -> bool:
    if not getattr(iris_current_user, 'is_authenticated', False):
        return False
    perms = ac_get_effective_permissions_of_user(iris_current_user)
    return ac_flag_match_mask(perms, Permissions.server_administrator.value)


class NamedAggregationError(Exception):
    pass


@dataclass
class ComputedFilters:
    customer_id: Optional[int] = None
    severity_id: Optional[int] = None
    case_status_id: Optional[int] = None
    window: Optional[str] = None


def _apply_access_filter_to_alert_query(stmt):
    if _current_user_is_server_admin():
        return stmt
    user_id = getattr(iris_current_user, 'id', None)
    if not user_id:
        return stmt.where(Alert.alert_id == -1)
    client_ids = get_user_clients_id(user_id) or []
    case_ids = ac_get_fast_user_cases_access(user_id) or []
    conditions = []
    if client_ids:
        conditions.append(Alert.alert_customer_id.in_(client_ids))
    if case_ids:
        sub = select(AlertCaseAssociation.alert_id).where(AlertCaseAssociation.case_id.in_(case_ids))
        conditions.append(Alert.alert_id.in_(sub))
    if not conditions:
        return stmt.where(Alert.alert_id == -1)
    if len(conditions) == 1:
        return stmt.where(conditions[0])
    return stmt.where(or_(*conditions))


def _apply_timeframe(stmt, column, timeframe: Tuple[Optional[datetime], Optional[datetime]]):
    start, end = timeframe
    if start is not None:
        stmt = stmt.where(column >= start)
    if end is not None:
        stmt = stmt.where(column <= end)
    return stmt


def _apply_alert_filters(stmt, filters: ComputedFilters):
    if filters.customer_id is not None:
        stmt = stmt.where(Alert.alert_customer_id == filters.customer_id)
    if filters.severity_id is not None:
        stmt = stmt.where(Alert.alert_severity_id == filters.severity_id)
    return stmt


def _mttd_seconds(timeframe, filters: ComputedFilters) -> Optional[float]:
    """Mean time to detect: alert_creation_time − alert_source_event_time.

    The interval between when the underlying event happened (as reported
    by the source) and when the alert reached IRIS. Sub-second precision
    is preserved so genuinely fast pipelines are measured accurately.

    Only exactly-equal timestamps are dropped, not "close to zero" ones:
    equality means both columns hit their `server_default=now()` on the
    same INSERT because the source didn't supply an event time. A real
    fast pipeline can produce a delta of tens of microseconds and that
    IS signal we want to reflect in the mean.
    """
    delta = func.extract(
        'epoch', Alert.alert_creation_time - Alert.alert_source_event_time,
    )
    stmt = select(func.avg(delta)).where(
        Alert.alert_creation_time != Alert.alert_source_event_time,
        delta > 0,
    )
    stmt = _apply_alert_filters(stmt, filters)
    stmt = _apply_timeframe(stmt, Alert.alert_creation_time, timeframe)
    stmt = _apply_access_filter_to_alert_query(stmt)
    value = db.session.execute(stmt).scalar()
    return float(value) if value is not None else None


def _mttr_seconds(timeframe, filters: ComputedFilters) -> Optional[float]:
    """Mean time to resolve for **alerts**: `resolved_at − alert_creation_time`.

    Alerts are the analyst's real-time queue; cases are the outcome of
    a resolved alert that turned out to be worth deeper investigation.
    The SOC metric "how long did it take us to get to a verdict" is
    naturally an alert-level measure.

    `resolved_at` is set by `alerts_update` the first time an analyst
    picks a resolution status, and cleared if they later revert it to
    "unresolved". Legacy alerts (pre-migration `d1a6b3c7e908`) get a
    best-effort backfill from `modification_history`; those with no
    history dict but a resolution set fall back to `alert_creation_time`
    (a zero-duration contribution rather than dragging the mean by
    exclusion).

    Scope:
      * `resolved_at IS NOT NULL` — unresolved alerts contribute nothing.
      * Timeframe filters on `resolved_at` (MTTR-over-last-30-days means
        "the mean over alerts we resolved in that window", not "alerts
        that were created in that window"). Slow-to-resolve alerts show
        up in the window where they were closed, matching the analyst's
        mental model.
    """
    delta = func.extract(
        'epoch', Alert.resolved_at - Alert.alert_creation_time,
    )
    stmt = select(func.avg(delta)).where(
        Alert.resolved_at.isnot(None),
        delta >= 0,
    )
    stmt = _apply_alert_filters(stmt, filters)
    stmt = _apply_timeframe(stmt, Alert.resolved_at, timeframe)
    stmt = _apply_access_filter_to_alert_query(stmt)
    value = db.session.execute(stmt).scalar()
    return float(value) if value is not None else None


def _false_positive_rate(timeframe, filters: ComputedFilters) -> Optional[float]:
    fp_status = db.session.execute(
        select(AlertResolutionStatus.resolution_status_id)
        .where(func.lower(AlertResolutionStatus.resolution_status_name) == 'false positive')
    ).scalar_one_or_none()

    base_stmt = select(func.count(Alert.alert_id))
    base_stmt = _apply_alert_filters(base_stmt, filters)
    base_stmt = _apply_timeframe(base_stmt, Alert.alert_creation_time, timeframe)
    base_stmt = _apply_access_filter_to_alert_query(base_stmt)
    total = db.session.execute(base_stmt).scalar() or 0
    if total == 0 or fp_status is None:
        return None

    fp_stmt = base_stmt.where(Alert.alert_resolution_status_id == fp_status)
    fp_count = db.session.execute(fp_stmt).scalar() or 0
    return (fp_count / total) * 100.0


def _escalation_rate(timeframe, filters: ComputedFilters) -> Optional[float]:
    escalated_status = db.session.execute(
        select(AlertStatus.status_id)
        .where(func.lower(AlertStatus.status_name) == 'escalated')
    ).scalar_one_or_none()

    base_stmt = select(func.count(Alert.alert_id))
    base_stmt = _apply_alert_filters(base_stmt, filters)
    base_stmt = _apply_timeframe(base_stmt, Alert.alert_creation_time, timeframe)
    base_stmt = _apply_access_filter_to_alert_query(base_stmt)
    total = db.session.execute(base_stmt).scalar() or 0
    if total == 0 or escalated_status is None:
        return None

    esc_stmt = base_stmt.where(Alert.alert_status_id == escalated_status)
    esc_count = db.session.execute(esc_stmt).scalar() or 0
    return (esc_count / total) * 100.0


def _alerts_window_count(timeframe, filters: ComputedFilters) -> Optional[float]:
    window = (filters.window or '24h').lower()
    if window == '2h':
        delta = timedelta(hours=2)
    elif window == '48h':
        delta = timedelta(hours=48)
    else:
        delta = timedelta(hours=24)
    now = datetime.utcnow()
    bounded = (now - delta, now)
    stmt = select(func.count(Alert.alert_id))
    stmt = _apply_alert_filters(stmt, filters)
    stmt = _apply_timeframe(stmt, Alert.alert_creation_time, bounded)
    stmt = _apply_access_filter_to_alert_query(stmt)
    return float(db.session.execute(stmt).scalar() or 0)


_NAMED_AGGREGATIONS: Dict[str, Callable[..., Optional[float]]] = {
    'mttd_seconds': _mttd_seconds,
    'mttr_seconds': _mttr_seconds,
    'false_positive_rate': _false_positive_rate,
    'escalation_rate': _escalation_rate,
    'alerts_window_count': _alerts_window_count,
}


def list_named_aggregations() -> List[Dict[str, str]]:
    return [
        {'name': 'mttd_seconds', 'label': 'Mean time to detect (seconds)', 'value_format': 'duration'},
        {'name': 'mttr_seconds', 'label': 'Mean time to resolve (seconds)', 'value_format': 'duration'},
        {'name': 'false_positive_rate', 'label': 'False positive rate', 'value_format': 'percentage'},
        {'name': 'escalation_rate', 'label': 'Escalation rate', 'value_format': 'percentage'},
        {'name': 'alerts_window_count', 'label': 'Alerts within window', 'value_format': 'number'},
    ]


def compute_named_aggregation(
    name: str,
    timeframe: Tuple[Optional[datetime], Optional[datetime]],
    filters: ComputedFilters,
) -> Optional[float]:
    fn = _NAMED_AGGREGATIONS.get(name)
    if fn is None:
        raise NamedAggregationError(f"Named aggregation '{name}' is not defined.")
    return fn(timeframe, filters)
