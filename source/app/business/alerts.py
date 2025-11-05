#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

import json
from datetime import datetime
from typing import Optional

from app import db
from app import socket_io
from app.blueprints.access_controls import ac_current_user_has_customer_access
from app.models.alerts import Alert
from app.models.iocs import Ioc
from app.models.models import CaseAssets
from app.datamgmt.alerts.alerts_db import cache_similar_alert
from app.datamgmt.alerts.alerts_db import delete_similar_alert_cache
from app.datamgmt.alerts.alerts_db import delete_related_alerts_cache
from app.datamgmt.alerts.alerts_db import get_alert_by_id
from app.datamgmt.alerts.alerts_db import delete_alert
from app.datamgmt.alerts.alerts_db import get_filtered_alerts
from app.datamgmt.alerts.alerts_db import get_related_alerts_details
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.util import add_obj_history_entry
from app.models.errors import ObjectNotFoundError


def alerts_search(start_date, end_date, source_start_date, source_end_date, title, description,
                  status, severity, owner, source, tags, case_identifier, customer_identifier, classification, alert_identifiers,
                  assets, iocs, resolution_status, source_reference, custom_conditions, user_identifier_filter, page, per_page, sort):

    return get_filtered_alerts(
        start_date,
        end_date,
        source_start_date,
        source_end_date,
        title,
        description,
        status,
        severity,
        owner,
        source,
        tags,
        case_identifier,
        customer_identifier,
        classification,
        alert_identifiers,
        assets,
        iocs,
        resolution_status,
        page,
        per_page,
        sort,
        user_identifier_filter,
        source_reference,
        custom_conditions
    )


def alerts_create(alert: Alert, iocs: list[Ioc], assets: list[CaseAssets]) -> Alert:

    alert.alert_creation_time = datetime.utcnow()

    alert.iocs = iocs
    alert.assets = assets

    db.session.add(alert)
    db.session.commit()

    add_obj_history_entry(alert, 'Alert created')

    cache_similar_alert(alert.alert_customer_id, assets, iocs, alert.alert_id, alert.alert_source_event_time)

    alert = call_modules_hook('on_postload_alert_create', data=alert)

    track_activity(f"created alert #{alert.alert_id} - {alert.alert_title}", ctx_less=True)

    socket_io.emit('new_alert', json.dumps({
        'alert_id': alert.alert_id
    }), namespace='/alerts')

    return alert


def _get(user, identifier) -> Optional[Alert]:
    alert = get_alert_by_id(identifier)
    if not alert:
        return None
    if not ac_current_user_has_customer_access(alert.alert_customer_id):
        return None
    return alert


def alerts_get(user, identifier) -> Alert:
    alert = _get(user, identifier)
    if not alert:
        raise ObjectNotFoundError()
    return alert


def related_alerts_get(alert, open_alerts, closed_alerts, open_cases, closed_cases,
                        days_back, number_of_results):
    return get_related_alerts_details(alert.alert_customer_id, alert.assets, alert.iocs,
                                     open_alerts, closed_alerts, open_cases, closed_cases,
                                     days_back, number_of_results)


def alerts_exists(user, identifier) -> bool:
    alert = _get(user, identifier)

    return alert is not None


def alerts_update(alert: Alert, updated_alert: Alert, activity_data) -> Alert:

    do_resolution_hook = False
    do_status_hook = False

    if alert.alert_resolution_status_id != updated_alert.alert_resolution_status_id:
        do_resolution_hook = True
    if alert.alert_status_id != updated_alert.alert_status_id:
        do_status_hook = True

    updated_alert = call_modules_hook('on_postload_alert_update', data=updated_alert)

    if do_resolution_hook:
        updated_alert = call_modules_hook('on_postload_alert_resolution_update', data=updated_alert)

    if do_status_hook:
        updated_alert = call_modules_hook('on_postload_alert_status_update', data=updated_alert)

    if activity_data:
        activity_data_as_string = ','.join(activity_data)
        track_activity(f'updated alert #{alert.alert_id}: {activity_data_as_string}', ctx_less=True)
        add_obj_history_entry(updated_alert, f'updated alert: {activity_data_as_string}')
    else:
        track_activity(f'updated alert #{alert.alert_id}', ctx_less=True)
        add_obj_history_entry(updated_alert, 'updated alert')

    db.session.commit()
    return updated_alert


def alerts_delete(alert: Alert):

    delete_similar_alert_cache(alert.alert_id)
    delete_related_alerts_cache([alert.alert_id])
    delete_alert(alert)

    call_modules_hook('on_postload_alert_delete', data=alert.alert_id)
    track_activity(f'delete alert #{alert.alert_id}', ctx_less=True)
