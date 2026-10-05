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

"""Celery side of webhooks.

`iris.webhooks.dispatch` receives an event and the ids of the webhooks
subscribed to it (or the one a user picked in an object's menu), evaluates each webhook's condition and creates one
delivery row per webhook, then queues `iris.webhooks.deliver` for each
— one task per delivery so a slow receiver doesn't hold up the others
and each retries on its own schedule.

Retries: connection errors, timeouts, 408/425/429 and 5xx, up to the
webhook's `max_retries`, backing off 30s, 2min, 8min, 32min, then
hourly. A delivery left `retrying` survives a worker restart only as
long as the broker keeps the message; it can always be redelivered by
hand from the log.
"""

import datetime
import logging

from sqlalchemy.exc import OperationalError

from app import app
from app import celery
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_commit
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_create_delivery
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_get
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_get_delivery
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_proxies
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_prune_deliveries
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_rollback
from app.datamgmt.manage.manage_webhooks_db import webhooks_db_utcnow
from app.iris_engine.webhooks.delivery import webhooks_attempt
from app.iris_engine.webhooks.delivery import webhooks_config_from_model
from app.iris_engine.webhooks.delivery import webhooks_record_attempt
from app.iris_engine.webhooks.events import webhooks_event_matches
from app.iris_engine.webhooks.render import WebhookRenderError
from app.iris_engine.webhooks.render import webhooks_eval_condition
from app.iris_engine.webhooks.render import webhooks_template_context
from app.models.webhooks import STATUS_FAILED
from app.models.webhooks import STATUS_PENDING
from app.models.webhooks import STATUS_RETRYING
from app.models.webhooks import STATUS_SKIPPED
from app.models.webhooks import STATUS_SUCCESS
from app.models.webhooks import TRIGGER_EVENT
from app.models.webhooks import TRIGGER_MANUAL


logger = logging.getLogger(__name__)

_PRUNE_BEAT_ENTRY = 'iris-webhooks-prune-deliveries'


def webhooks_retry_countdown(attempts) -> int:
    return min(30 * 4 ** max(attempts - 1, 0), 3600)


@celery.task(
    bind=True,
    name='iris.webhooks.dispatch',
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def webhooks_dispatch_task(self, event, webhook_ids, trigger=TRIGGER_EVENT):
    queued = []
    try:
        for webhook_id in webhook_ids:
            webhook = webhooks_db_get(webhook_id)
            if webhook is None or not webhook.enabled or not webhooks_event_matches(webhook.events, event['event']):
                continue

            status = STATUS_PENDING
            error = None
            try:
                context = webhooks_template_context(event, None, {'id': webhook.id, 'name': webhook.name})
                if not webhooks_eval_condition(webhook.condition, context):
                    continue
            except WebhookRenderError as e:
                status = STATUS_FAILED
                error = f'Condition error — {e.message}'

            delivery = webhooks_db_create_delivery(webhook.id, event['event'], trigger, event, status)
            if error is not None:
                delivery.error = error
                delivery.completed_at = webhooks_db_utcnow()
                webhooks_db_commit()
                continue
            queued.append(delivery.id)

    except OperationalError:
        webhooks_db_rollback()
        raise

    for delivery_id in queued:
        webhooks_deliver_task.delay(delivery_id)
    return queued


@celery.task(
    bind=True,
    name='iris.webhooks.deliver',
    max_retries=None,
)
def webhooks_deliver_task(self, delivery_id):
    delivery = webhooks_db_get_delivery(delivery_id)
    if delivery is None or delivery.status in (STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED):
        return None

    webhook = webhooks_db_get(delivery.webhook_id)
    if webhook is None:
        return None

    if not webhook.enabled and delivery.trigger in (TRIGGER_EVENT, TRIGGER_MANUAL):
        delivery.status = STATUS_SKIPPED
        delivery.error = 'Webhook disabled before the delivery was sent'
        delivery.completed_at = webhooks_db_utcnow()
        webhooks_db_commit()
        return delivery.status

    try:
        rendered, result = webhooks_attempt(webhooks_config_from_model(webhook), delivery.payload or {},
                                            delivery_id=delivery.uuid, proxies=webhooks_db_proxies())
        webhooks_record_attempt(delivery, rendered, result)
    except Exception as e:
        webhooks_db_rollback()
        logger.exception(f'Webhook delivery {delivery_id} crashed')
        delivery = webhooks_db_get_delivery(delivery_id)
        if delivery is None:
            return None
        delivery.attempts = (delivery.attempts or 0) + 1
        delivery.status = STATUS_FAILED
        delivery.error = f'Internal error: {e}'
        delivery.completed_at = webhooks_db_utcnow()
        webhooks_db_commit()
        return delivery.status

    if result['success']:
        delivery.status = STATUS_SUCCESS
    elif result['retryable'] and delivery.attempts <= (webhook.max_retries or 0):
        delivery.status = STATUS_RETRYING
        webhooks_db_commit()
        raise self.retry(countdown=webhooks_retry_countdown(delivery.attempts))
    else:
        delivery.status = STATUS_FAILED

    delivery.completed_at = webhooks_db_utcnow()
    webhooks_db_commit()
    return delivery.status


@celery.task(name='iris.webhooks.prune_deliveries')
def webhooks_prune_task():
    days = int(app.config.get('WEBHOOKS_DELIVERY_RETENTION_DAYS') or 30)
    count = webhooks_db_prune_deliveries(webhooks_db_utcnow() - datetime.timedelta(days=days))
    logger.info(f'Pruned {count} webhook deliveries older than {days} days')
    return count


@celery.on_after_finalize.connect
def _register_webhooks_beat_schedule(sender, **_kwargs):
    from celery.schedules import crontab

    sender.conf.beat_schedule = dict(sender.conf.beat_schedule or {})
    sender.conf.beat_schedule[_PRUNE_BEAT_ENTRY] = {
        'task': 'iris.webhooks.prune_deliveries',
        'schedule': crontab(hour=3, minute=17),
    }
