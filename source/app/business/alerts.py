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

from app import db
from app import socket_io
from app.models.alerts import Alert
from app.models.iocs import Ioc
from app.models.models import CaseAssets
from app.datamgmt.alerts.alerts_db import cache_similar_alert
from app.datamgmt.alerts.alerts_db import get_alert_by_id
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.util import add_obj_history_entry
from app.business.errors import ObjectNotFoundError
from app.datamgmt.manage.manage_access_control_db import user_has_client_access


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


def alerts_get(current_user, identifier) -> Alert:

    alert = get_alert_by_id(identifier)

    if not alert:
        raise ObjectNotFoundError()
    if not user_has_client_access(current_user.id, alert.alert_customer_id):
        raise ObjectNotFoundError()

    return alert


def alerts_update(updated_alert, alert, iris_current_user, data, identifier) -> Alert:

    if not alert:
        raise ObjectNotFoundError()    
    if not user_has_client_access(iris_current_user.id, alert.alert_customer_id):
        raise ObjectNotFoundError()
    
    do_resolution_hook = False
    do_status_hook = False

    activity_data = []
    for key, value in data.items():
        old_value = getattr(alert, key, None)

        if type(old_value) is int:
            old_value = str(old_value)

        if type(value) is int:
            value = str(value)

        if old_value != value:
            if key == "alert_resolution_status_id":
                do_resolution_hook = True
            if key == 'alert_status_id':
                do_status_hook = True

            if key not in ["alert_content", "alert_note"]:
                activity_data.append(f"\"{key}\" from \"{old_value}\" to \"{value}\"")
            else:
                activity_data.append(f"\"{key}\"")
    if data.get('alert_owner_id') is None and updated_alert.alert_owner_id is None:
        updated_alert.alert_owner_id = iris_current_user.id

    if data.get('alert_owner_id') == "-1" or data.get('alert_owner_id') == -1:
        updated_alert.alert_owner_id = None

    db.session.commit()

    updated_alert = call_modules_hook('on_postload_alert_update', data=updated_alert)

    if do_resolution_hook:
        updated_alert = call_modules_hook('on_postload_alert_resolution_update', data=updated_alert)

    if do_status_hook:
        updated_alert = call_modules_hook('on_postload_alert_status_update', data=updated_alert)

    if activity_data:
        activity_data_as_string = ','.join(activity_data)
        track_activity(f'updated alert #{identifier}: {activity_data_as_string}', ctx_less=True)
        add_obj_history_entry(updated_alert, f'updated alert: {activity_data_as_string}')
    else:
        track_activity(f'updated alert #{identifier}', ctx_less=True)
        add_obj_history_entry(updated_alert, 'updated alert')

    db.session.commit()
    return alert
