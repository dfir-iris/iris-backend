#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Per-customer chatbot policy.

Each `Client` may reference at most one `ChatbotPolicy`. When a
conversation is created for a case / war room / alert, the resolver
picks the "most restrictive" applicable policy across every customer
touched by the scope (a war room can span multiple customers via
`WarRoomCase`, so the strictest wins). Clients with no policy fall back
to the global `ServerSettings` chatbot block.

`restriction_level` is the composition key — a small integer that lets
the resolver do `max(...)` without cross-provider heuristics. Higher =
stricter. Conventional values:
    0    global default (server_settings)
    10   cloud provider, no redaction
    50   cloud provider with redaction
    100  local-only (Ollama or equivalent) — the ceiling

Deliberately not an enum: an install may want intermediate rungs (a
customer that must use a specific cloud region, e.g. level 30) without
a code change. Callers only ever compare with `<` / `max` — the number
is opaque.
"""
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.orm import relationship

from app.db import db


class ChatbotPolicy(db.Model):
    """A named chatbot configuration a customer can be pinned to.

    Fields mirror the shape of `ServerSettings.chatbot_*` so switching
    from "global default" to "policy X" is a straight field-by-field
    override. `api_key` is Fernet-encrypted like `chatbot_api_key`.
    """
    __tablename__ = 'chatbot_policy'

    id = Column(BigInteger, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(Text, nullable=False, default='',
                         server_default=text("''"))
    restriction_level = Column(Integer, nullable=False, default=10,
                               server_default=text('10'))

    # Provider config — same shape as ServerSettings.chatbot_*.
    provider = Column(String(32), nullable=False, default='',
                     server_default=text("''"))
    model = Column(String(120), nullable=False, default='',
                   server_default=text("''"))
    api_key = Column(Text, nullable=True)
    base_url = Column(String(500), nullable=False, default='',
                      server_default=text("''"))

    # Behaviour toggles.
    auto_execute_read_tools = Column(Boolean, nullable=False, default=True,
                                     server_default=text('true'))
    auto_approve_write_tools = Column(Boolean, nullable=False, default=False,
                                      server_default=text('false'))
    max_turns_per_conversation = Column(Integer, nullable=False,
                                        default=25, server_default=text('25'))
    max_tool_calls_per_turn = Column(Integer, nullable=False,
                                     default=8, server_default=text('8'))
    daily_token_budget_per_user = Column(Integer, nullable=False,
                                         default=500_000,
                                         server_default=text('500000'))
    daily_token_budget_org = Column(Integer, nullable=False,
                                    default=10_000_000,
                                    server_default=text('10000000'))
    redact_ips = Column(Boolean, nullable=False, default=False,
                        server_default=text('false'))
    redact_emails = Column(Boolean, nullable=False, default=False,
                           server_default=text('false'))
    redact_hashes = Column(Boolean, nullable=False, default=False,
                           server_default=text('false'))

    # Retention window (Stage 3). Kept on the policy so the DPO knob is
    # per-customer — a strict-local customer typically wants shorter
    # retention than a standard-cloud one. Zero disables auto-purge.
    retention_days = Column(Integer, nullable=False, default=0,
                            server_default=text('0'))

    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))

    clients = relationship('Client', back_populates='chatbot_policy')
