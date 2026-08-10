#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Per-customer chatbot policy resolver.

Turns a conversation scope (case / war-room / alert / global) into the
`ChatbotPolicy` row the loop should use — falling back to the global
`ServerSettings.chatbot_*` block when no policy applies.

The composition rule for war rooms is **most-restrictive-wins**: if
customer A is on the "standard-cloud" policy and customer B is on
"strict-local", a war room containing cases from both is bound to
"strict-local". This matches how compliance constraints compose — the
strictest customer's restrictions bind for anyone who shares a chat
with them, so the analyst can't accidentally leak B's data to a
provider B doesn't authorise.

`restriction_level` is the composition key (higher = stricter). A NULL
policy resolves to level 0 (unrestricted default).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.db import db
from app.models.case_chat import CaseChatConversation
from app.models.cases import Cases
from app.models.chatbot_policy import ChatbotPolicy
from app.models.customers import Client
from app.models.war_rooms import WarRoomCase


@dataclass(frozen=True)
class ResolvedPolicy:
    """The outcome of a resolution pass.

    `policy` is the row that binds, or None when the global default
    should apply. `restriction_level` is the level the caller stamps
    on the conversation — 0 means "default"; matches ChatbotPolicy
    defaults so migrated pre-Stage-1 rows compare cleanly.
    """
    policy: Optional[ChatbotPolicy]
    restriction_level: int


def resolve_for_case(case_id: Optional[int]) -> ResolvedPolicy:
    """Resolve the policy for a case-scoped conversation.

    A case belongs to exactly one client (`Cases.client_id` NOT NULL),
    so this is a single lookup — no composition needed.
    """
    if case_id is None:
        return ResolvedPolicy(None, 0)
    row = (
        db.session.query(ChatbotPolicy)
        .join(Client, Client.chatbot_policy_id == ChatbotPolicy.id)
        .join(Cases, Cases.client_id == Client.client_id)
        .filter(Cases.case_id == case_id)
        .first()
    )
    if row is None:
        return ResolvedPolicy(None, 0)
    return ResolvedPolicy(row, int(row.restriction_level))


def resolve_for_war_room(war_room_id: Optional[int]) -> ResolvedPolicy:
    """Resolve the strictest policy across every customer touched by a
    war room's currently-attached cases.

    An empty war room (no cases attached yet) has no customer basis
    to reason from, so it resolves to the global default. As cases are
    attached, the resolver re-runs on send() and the war-room hook, and
    the ceiling can only rise — see docstring on
    `resolve_for_conversation_update`.
    """
    if war_room_id is None:
        return ResolvedPolicy(None, 0)
    row = (
        db.session.query(ChatbotPolicy)
        .join(Client, Client.chatbot_policy_id == ChatbotPolicy.id)
        .join(Cases, Cases.client_id == Client.client_id)
        .join(WarRoomCase, WarRoomCase.case_id == Cases.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(ChatbotPolicy.restriction_level.desc())
        .first()
    )
    if row is None:
        return ResolvedPolicy(None, 0)
    return ResolvedPolicy(row, int(row.restriction_level))


def resolve_for_conversation(conv: CaseChatConversation) -> ResolvedPolicy:
    """Route a conversation to its owning scope-specific resolver."""
    if conv.war_room_id is not None:
        return resolve_for_war_room(conv.war_room_id)
    if conv.case_id is not None:
        return resolve_for_case(conv.case_id)
    # Global (no case, no war-room) — no customer context, so the
    # global default applies. If a customer-scoped feature (alert
    # focus-hint) is layered on later, it stays global for policy
    # purposes; the loop's audit trail still records `user_id`.
    return ResolvedPolicy(None, 0)


def resolve_war_room_after_attach(
    war_room_id: int, new_case_id: int,
) -> ResolvedPolicy:
    """Resolve the policy the war room WILL have after `new_case_id` is
    attached — call this after `db.session.add(WarRoomCase(...))` but
    before comparing against the conversation's stamped level.

    Kept as a distinct function (rather than reusing `resolve_for_war_room`
    with a pre-attach query) so callers can't accidentally read stale
    state: the attach hook runs `resolve_for_war_room` after the commit,
    which returns exactly what the loop would see on the next send().
    Naming the wrapper makes the intent explicit at the call site.
    """
    return resolve_for_war_room(war_room_id)
