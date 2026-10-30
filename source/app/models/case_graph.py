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

"""SQLAlchemy models of the investigation graph of a case.

The graph read by `GET /cases/<id>/graph` is the union of edges derived
on read from the case data (timeline events, IOC/asset links, parent
events) and of the stored rows below: analyst-made edges, free nodes and
saved views. Edge endpoints are polymorphic (`<kind>:<id>`), so they
cannot be real foreign keys: the business layer deletes the edges of an
object in the same transaction as the object itself.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID

from app.db import db


# Kinds a stored edge may reference (`case` nodes are read-only pivots).
CASE_GRAPH_EDGE_KINDS = ('asset', 'ioc', 'event', 'evidence', 'task', 'note', 'alert', 'node')

CASE_GRAPH_NODE_TYPES = ('actor', 'account', 'infrastructure', 'malware', 'note', 'group', 'other')

CASE_GRAPH_LINE_STYLES = ('solid', 'dashed', 'dotted')

CASE_GRAPH_EDGE_ORIGINS = ('manual', 'ai', 'module', 'import', 'suggestion')

CASE_GRAPH_REVIEW_STATES = ('accepted', 'pending', 'rejected')

CASE_GRAPH_EVENT_ROLES = ('source', 'target', 'involved')

# The system relation types are seeded by the alembic revision
# a3f6c1d8e2b4 (`_RELATION_TYPES`).


class CaseGraphRelationType(db.Model):
    """Managed catalogue of edge relations. System rows cannot be deleted."""
    __tablename__ = 'case_graph_relation_type'
    __table_args__ = (
        CheckConstraint("line_style IN ('solid', 'dashed', 'dotted')",
                        name='ck_case_graph_relation_type_line_style'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    label = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    directed = Column(Boolean, nullable=False, default=True, server_default=text('true'))
    color = Column(String(16), nullable=False, server_default=text("'slate'"))
    line_style = Column(String(16), nullable=False, server_default=text("'solid'"))
    stix_relationship = Column(String(64), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    sort_order = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseGraphNode(db.Model):
    """Free node: an entity of the case graph that is not an asset or IOC."""
    __tablename__ = 'case_graph_node'

    node_id = Column(BigInteger, primary_key=True)
    node_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    label = Column(Text, nullable=False)
    node_type = Column(String(32), nullable=False, server_default=text("'other'"))
    description = Column(Text, nullable=True)
    icon = Column(String(64), nullable=True)
    color = Column(String(16), nullable=True)
    tlp_id = Column(Integer, ForeignKey('tlp.tlp_id', ondelete='SET NULL'), nullable=True)
    custom_attributes = Column(JSONB, nullable=True)
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseGraphEdge(db.Model):
    """Stored edge between two nodes of the case graph."""
    __tablename__ = 'case_graph_edge'
    __table_args__ = (
        CheckConstraint('NOT (source_kind = target_kind AND source_id = target_id)',
                        name='ck_case_graph_edge_no_loop'),
        CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 100)',
                        name='ck_case_graph_edge_confidence'),
        Index('idx_case_graph_edge_source', 'case_id', 'source_kind', 'source_id'),
        Index('idx_case_graph_edge_target', 'case_id', 'target_kind', 'target_id'),
    )

    edge_id = Column(BigInteger, primary_key=True)
    edge_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    source_kind = Column(String(16), nullable=False)
    source_id = Column(BigInteger, nullable=False)
    target_kind = Column(String(16), nullable=False)
    target_id = Column(BigInteger, nullable=False)
    relation_type_id = Column(Integer, ForeignKey('case_graph_relation_type.id'), nullable=False)
    directed = Column(Boolean, nullable=False)
    label = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    confidence = Column(SmallInteger, nullable=True)
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    tlp_id = Column(Integer, ForeignKey('tlp.tlp_id', ondelete='SET NULL'), nullable=True)
    origin = Column(String(16), nullable=False, server_default=text("'manual'"))
    review_state = Column(String(16), nullable=False, server_default=text("'accepted'"))
    custom_attributes = Column(JSONB, nullable=True)
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseGraphEdgeEvent(db.Model):
    """Timeline event supporting a stored edge."""
    __tablename__ = 'case_graph_edge_event'

    edge_id = Column(BigInteger, ForeignKey('case_graph_edge.edge_id', ondelete='CASCADE'),
                     primary_key=True, nullable=False)
    event_id = Column(BigInteger, ForeignKey('cases_events.event_id', ondelete='CASCADE'),
                      primary_key=True, nullable=False, index=True)


class CaseGraphDismissedSuggestion(db.Model):
    """Graph suggestion dismissed in a case (its key is deterministic)."""
    __tablename__ = 'case_graph_dismissed_suggestion'

    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'),
                     primary_key=True, nullable=False)
    suggestion_key = Column(String(255), primary_key=True, nullable=False)
    dismissed_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    dismissed_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseGraphView(db.Model):
    """Saved presentation of the case graph. `state` is opaque to the
    backend; `snapshot` is a PNG data URL."""
    __tablename__ = 'case_graph_view'

    view_id = Column(BigInteger, primary_key=True)
    view_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    is_report_default = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    state = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))
