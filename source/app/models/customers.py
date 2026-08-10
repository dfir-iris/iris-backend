#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import UUID
from sqlalchemy import text
from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy import func
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship

from app.db import db


class Client(db.Model):
    __tablename__ = 'client'

    client_id = Column(BigInteger, primary_key=True)
    client_uuid = Column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"), nullable=False)
    name = Column(Text, unique=True)
    description = Column(Text)
    sla = Column(Text)
    creation_date = Column(DateTime, server_default=func.now(), nullable=True)
    created_by = Column(ForeignKey('user.id'), nullable=True)
    last_update_date = Column(DateTime, server_default=func.now(), nullable=True)

    custom_attributes = Column(JSON)

    # Optional per-customer chatbot policy override. NULL = fall back to
    # the global `ServerSettings.chatbot_*` block. See models/chatbot_policy.py.
    # ON DELETE SET NULL so deleting a policy doesn't cascade into
    # customer rows — the customer just reverts to the global default.
    chatbot_policy_id = Column(
        BigInteger,
        ForeignKey('chatbot_policy.id', ondelete='SET NULL'),
        nullable=True,
    )
    chatbot_policy = relationship(
        'ChatbotPolicy', back_populates='clients', lazy='joined')
