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

"""Native webhooks.

`Webhook` describes one outbound HTTP integration: which `on_postload_*`
events it fires on and the full shape of the request (method, URL,
query, headers, auth, body, TLS behaviour). URL, query values, header
values and the body template are sandboxed Jinja2 templates.

Secrets — header/query values flagged `secret`, the auth secret and the
signing secret — are stored Fernet-encrypted (see
`app.iris_engine.mail.secrets`) and never returned by the API.

`WebhookDelivery` is one attempt chain for one event and one webhook.
It keeps the event payload so it can be redelivered, and a (truncated,
secret-masked) copy of the last request and response for the log UI.
"""

import uuid

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import db


WEBHOOK_METHODS = ('POST', 'PUT', 'PATCH', 'GET', 'DELETE')

AUTH_NONE = 'none'
AUTH_BASIC = 'basic'
AUTH_BEARER = 'bearer'
AUTH_TYPES = (AUTH_NONE, AUTH_BASIC, AUTH_BEARER)

# `default` sends the IRIS JSON envelope, `template` renders
# `body_template`, `none` sends no body (GET / DELETE style endpoints).
BODY_DEFAULT = 'default'
BODY_TEMPLATE = 'template'
BODY_NONE = 'none'
BODY_MODES = (BODY_DEFAULT, BODY_TEMPLATE, BODY_NONE)

# Subscribing to every event
ALL_EVENTS = '*'

TRIGGER_EVENT = 'event'
TRIGGER_MANUAL = 'manual'
TRIGGER_TEST = 'test'
TRIGGER_REDELIVER = 'redeliver'

STATUS_PENDING = 'pending'
STATUS_RETRYING = 'retrying'
STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'
DELIVERY_STATUSES = (STATUS_PENDING, STATUS_RETRYING, STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED)


class Webhook(db.Model):
    __tablename__ = 'webhook'

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)

    # Hook names. '*' stands for every postload event; manual triggers
    # (`on_manual_trigger_*`) are always listed one by one.
    events = Column(JSON, nullable=False, default=list)
    # Entry shown in the object menus for the manual triggers; the name
    # when empty
    manual_label = Column(String(255), nullable=True)
    # Optional Jinja expression; the event is only delivered when it
    # renders truthy. Same context as the templates.
    condition = Column(Text, nullable=True)

    method = Column(String(10), nullable=False, default='POST')
    url = Column(Text, nullable=False)
    # [{name, value, secret}] — secret values are ciphertext
    query_params = Column(JSON, nullable=False, default=list)
    headers = Column(JSON, nullable=False, default=list)

    auth_type = Column(String(16), nullable=False, default=AUTH_NONE)
    auth_username = Column(String(255), nullable=True)
    # Basic password or bearer token, ciphertext
    auth_secret = Column(Text, nullable=True)

    body_mode = Column(String(16), nullable=False, default=BODY_DEFAULT)
    body_template = Column(Text, nullable=True)
    content_type = Column(String(255), nullable=False, default='application/json')

    # HMAC-SHA256 signing key, ciphertext
    signing_secret = Column(Text, nullable=True)

    verify_tls = Column(Boolean, nullable=False, default=True)
    timeout_seconds = Column(Integer, nullable=False, default=10)
    max_retries = Column(Integer, nullable=False, default=3)
    follow_redirects = Column(Boolean, nullable=False, default=False)
    # Off: connect directly, ignoring the server settings and environment proxies
    use_proxy = Column(Boolean, nullable=False, default=True)

    created_by_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    created_by = relationship('User', foreign_keys=[created_by_id])


class WebhookDelivery(db.Model):
    __tablename__ = 'webhook_delivery'

    id = Column(BigInteger, primary_key=True)
    # Sent to the receiver as `X-IRIS-Delivery`, lets it dedupe retries
    uuid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    webhook_id = Column(ForeignKey('webhook.id', ondelete='CASCADE'), nullable=False)

    event = Column(String(128), nullable=False)
    trigger = Column(String(16), nullable=False, default=TRIGGER_EVENT)
    status = Column(String(16), nullable=False, default=STATUS_PENDING)
    attempts = Column(Integer, nullable=False, default=0)

    # The event (IRIS envelope without the per-delivery fields), kept for
    # redelivery and for the log UI
    payload = Column(JSON, nullable=True)

    request_method = Column(String(10), nullable=True)
    request_url = Column(Text, nullable=True)
    request_headers = Column(JSON, nullable=True)
    request_body = Column(Text, nullable=True)

    response_status = Column(Integer, nullable=True)
    response_headers = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)

    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    webhook = relationship('Webhook', foreign_keys=[webhook_id])

    __table_args__ = (
        Index('ix_webhook_delivery_webhook_created', 'webhook_id', 'created_at'),
        Index('ix_webhook_delivery_created_at', 'created_at'),
    )
