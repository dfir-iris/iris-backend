from __future__ import annotations

from typing import Iterable, List

from sqlalchemy import or_

from app import db
from app.models.models import CustomDashboard, CustomDashboardWidget


class DashboardAccessError(Exception):
    pass



class DashboardNotFoundError(Exception):
    pass


def list_dashboards_for_user(user_id: int) -> List[CustomDashboard]:
    query = CustomDashboard.query.filter(
        or_(
            CustomDashboard.owner_id == user_id,
            CustomDashboard.is_shared.is_(True)
        )
    ).order_by(CustomDashboard.created_at.desc())
    return query.all()


def get_dashboard_for_user(dashboard_id: int, user_id: int) -> CustomDashboard:
    dashboard = CustomDashboard.query.filter(CustomDashboard.id == dashboard_id).first()
    if dashboard is None:
        raise DashboardNotFoundError()

    if dashboard.owner_id != user_id and not dashboard.is_shared:
        raise DashboardAccessError()

    return dashboard


def _resolve_shared_flag(data: dict) -> bool:
    value = data.get("is_shared")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    if isinstance(value, int):
        return value != 0
    return False


def create_dashboard_for_user(user_id: int, payload: dict, allow_share: bool) -> CustomDashboard:
    data = dict(payload or {})
    data.pop('csrf_token', None)
    is_shared = _resolve_shared_flag(data) if allow_share else False
    data['is_shared'] = is_shared

    dashboard = CustomDashboard(
        name=data["name"],
        description=data.get("description"),
        owner_id=user_id,
        is_shared=is_shared,
        definition=data
    )
    _apply_widgets(dashboard, data.get("widgets", []))
    db.session.add(dashboard)
    db.session.commit()
    return dashboard


def update_dashboard_for_user(dashboard_id: int, user_id: int, payload: dict, allow_share: bool) -> CustomDashboard:
    dashboard = get_dashboard_for_user(dashboard_id, user_id)
    if dashboard.owner_id != user_id:
        raise DashboardAccessError()

    data = dict(payload or {})
    data.pop('csrf_token', None)
    requested_shared = _resolve_shared_flag(data) if allow_share else dashboard.is_shared
    data['is_shared'] = requested_shared

    dashboard.name = data["name"]
    dashboard.description = data.get("description")
    dashboard.is_shared = requested_shared
    dashboard.definition = data
    dashboard.widgets.clear()
    _apply_widgets(dashboard, data.get("widgets", []))
    db.session.commit()
    return dashboard


def delete_dashboard_for_user(dashboard_id: int, user_id: int) -> None:
    dashboard = get_dashboard_for_user(dashboard_id, user_id)
    if dashboard.owner_id != user_id:
        raise DashboardAccessError()

    db.session.delete(dashboard)
    db.session.commit()


def _apply_widgets(dashboard: CustomDashboard, widgets_payload: Iterable[dict]) -> None:
    for position, widget in enumerate(widgets_payload):
        dashboard.widgets.append(CustomDashboardWidget(
            name=widget["name"],
            chart_type=widget["chart_type"],
            definition=widget,
            position=position
        ))
