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
from sqlalchemy import Column, Integer, Text, DateTime, func, ForeignKey
from sqlalchemy.orm import relationship

from app import db


class EvidenceTypes(db.Model):
    __tablename__ = 'evidence_type'

    id = Column(Integer, primary_key=True)
    name = Column(Text)
    description = Column(Text)
    creation_date = Column(DateTime, server_default=func.now(), nullable=True)
    created_by_id = Column(ForeignKey('user.id'), nullable=True)

    created_by = relationship('User')
