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

"""Query helpers for the `webhook` and `webhook_delivery` tables.

Request-free — used from the REST layer and from the Celery delivery
tasks alike.
"""

import datetime

from sqlalchemy import func
from sqlalchemy import or_

from app.db import db
from app.models.cases import Cases
from app.models.models import IrisHook
from app.models.models import IrisModule
from app.models.models import ServerSettings
from app.models.webhooks import Webhook
from app.models.webhooks import WebhookDelivery


def webhooks_db_list():
    return Webhook.query.order_by(Webhook.name.asc(), Webhook.id.asc()).all()


def webhooks_db_list_enabled():
    return Webhook.query.filter(Webhook.enabled.is_(True)).order_by(Webhook.id.asc()).all()


def webhooks_db_get(webhook_id):
    return Webhook.query.filter(Webhook.id == webhook_id).first()


def webhooks_db_last_deliveries(webhook_ids) -> dict:
    """Latest delivery per webhook, keyed by webhook id."""
    if not webhook_ids:
        return {}
    rows = (
        WebhookDelivery.query
        .with_entities(
            WebhookDelivery.webhook_id,
            WebhookDelivery.status,
            WebhookDelivery.response_status,
            WebhookDelivery.event,
            WebhookDelivery.created_at,
            WebhookDelivery.error,
        )
        .filter(WebhookDelivery.webhook_id.in_(webhook_ids))
        .distinct(WebhookDelivery.webhook_id)
        .order_by(WebhookDelivery.webhook_id, WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .all()
    )
    return {row.webhook_id: row for row in rows}


def webhooks_db_delivery_counts(webhook_ids, since) -> dict:
    """{webhook_id: {status: count}} for deliveries created after `since`."""
    if not webhook_ids:
        return {}
    rows = (
        db.session.query(WebhookDelivery.webhook_id, WebhookDelivery.status, func.count(WebhookDelivery.id))
        .filter(WebhookDelivery.webhook_id.in_(webhook_ids), WebhookDelivery.created_at >= since)
        .group_by(WebhookDelivery.webhook_id, WebhookDelivery.status)
        .all()
    )
    counts = {}
    for webhook_id, status, count in rows:
        counts.setdefault(webhook_id, {})[status] = count
    return counts


def webhooks_db_list_deliveries(webhook_id, status=None, event=None, page=1, per_page=25):
    query = WebhookDelivery.query.filter(WebhookDelivery.webhook_id == webhook_id)
    if status:
        query = query.filter(WebhookDelivery.status == status)
    if event:
        query = query.filter(WebhookDelivery.event == event)
    return (
        query
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )


def webhooks_db_get_delivery(delivery_id):
    return WebhookDelivery.query.filter(WebhookDelivery.id == delivery_id).first()


def webhooks_db_create_delivery(webhook_id, event, trigger, payload, status):
    delivery = WebhookDelivery(webhook_id=webhook_id, event=event, trigger=trigger, payload=payload, status=status)
    db.session.add(delivery)
    db.session.commit()
    return delivery


def webhooks_db_commit():
    db.session.commit()


def webhooks_db_rollback():
    db.session.rollback()


def webhooks_db_latest_payload(event, webhook_id=None):
    """Most recent real payload for `event`, for previews."""
    query = WebhookDelivery.query.with_entities(WebhookDelivery.payload).filter(
        WebhookDelivery.event == event,
        WebhookDelivery.payload.isnot(None),
    )
    if webhook_id is not None:
        query = query.order_by((WebhookDelivery.webhook_id == webhook_id).desc())
    row = query.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()).first()
    return row.payload if row else None


def webhooks_db_prune_deliveries(before) -> int:
    count = WebhookDelivery.query.filter(WebhookDelivery.created_at < before).delete(synchronize_session=False)
    db.session.commit()
    return count


def webhooks_db_case_summary(case_id):
    row = Cases.query.with_entities(Cases.case_id, Cases.name).filter(Cases.case_id == case_id).first()
    if row is None:
        return None
    return {'id': row.case_id, 'name': row.name}


def webhooks_db_subscribable_hooks():
    """(hook_name, hook_description) of the postload and manual hooks."""
    return (
        IrisHook.query
        .with_entities(IrisHook.hook_name, IrisHook.hook_description)
        .filter(or_(IrisHook.hook_name.like('on\\_postload\\_%'),
                    IrisHook.hook_name.like('on\\_manual\\_trigger\\_%')))
        .all()
    )


def webhooks_db_hook_names() -> set:
    return {row.hook_name for row in webhooks_db_subscribable_hooks()}


def webhooks_db_proxies() -> dict:
    settings = ServerSettings.query.with_entities(ServerSettings.http_proxy, ServerSettings.https_proxy).first()
    proxies = {}
    if settings is not None:
        if settings.http_proxy:
            proxies['http'] = settings.http_proxy
        if settings.https_proxy:
            proxies['https'] = settings.https_proxy
    return proxies


def webhooks_db_get_module(module_name):
    return IrisModule.query.filter(IrisModule.module_name == module_name).first()


def webhooks_db_utcnow():
    """Naive UTC, matching the `DateTime` columns."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
