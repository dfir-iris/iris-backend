#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""The mention-chip markup contract, shared by every server-side writer.

The TipTap editor in iris-frontend stores an inline reference to another
IRIS object as a structured HTML span inside the markdown source column:

    <span data-mention data-kind="note" data-id="7" data-label="Runbook">#Runbook</span>

Anything the backend writes into a markdown column that should render as
a chip MUST go through `build_mention_span` here, and `collab/render.py`
MUST be able to read it back — the collaborative editor re-seeds its
Y.Doc from that column, so a span it doesn't understand is either shown
as literal text or silently dropped on the next flush.

The `kind` → node-name mapping mirrors the TipTap nodes registered in
`iris-frontend/src/lib/components/common/MarkDown/MarkDownEditor.svelte`.
y-prosemirror names each XmlElement after its ProseMirror node type, so
these strings are a wire contract with the frontend: renaming a node
there without renaming it here empties chips out of every collab doc.

Note this module is deliberately free of user-notification concerns.
`iris_engine/notifications/mentions.py` parses the same spans, but only
ever for `data-kind="user"` / `"team"`, and stays independent.
"""

from __future__ import annotations

import html
import re


# Every chip kind the frontend knows how to render. Kept in sync with
# `KIND_STYLE` in `MarkDown/mention-kinds.ts`.
MENTION_KINDS = frozenset({
    'user',
    'team',
    'asset',
    'ioc',
    'note',
    'task',
    'datastore',
    'alert',
})

# `@` addresses people, `#` addresses objects. Only used for the span's
# text body — the rendered chip draws an icon instead, and the char is
# what a reader sees if the chip ever degrades to plain text.
_MENTION_CHAR_BY_KIND = {
    'user': '@',
    'team': '@',
}
_DEFAULT_MENTION_CHAR = '#'

# ProseMirror node type behind each kind. Two nodes cover all of them:
# `userMention` (the `@` trigger) and `caseMention` (the `#` trigger).
MENTION_NODE_BY_KIND = {
    'user': 'userMention',
    'team': 'userMention',
    'asset': 'caseMention',
    'ioc': 'caseMention',
    'note': 'caseMention',
    'task': 'caseMention',
    'datastore': 'caseMention',
    'alert': 'caseMention',
}

MENTION_NODE_NAMES = frozenset(MENTION_NODE_BY_KIND.values())

# Default kind per node, for spans whose `data-kind` is missing or bogus.
# Matches the `defaultKind` argument of `createMentionNode` on the front.
MENTION_DEFAULT_KIND_BY_NODE = {
    'userMention': 'user',
    'caseMention': 'asset',
}

# Matches one mention span and captures its attributes. Attribute order
# is fixed by the TipTap render function, but we don't rely on it: the
# lookaheads let `data-mention` / `data-kind` / `data-id` / `data-label`
# appear in any order. Same defensive approach as the notification
# parser — a regex is enough for the strictly-shaped span the editor
# emits, and we never interpret the captured values as markup.
MENTION_SPAN_RE = re.compile(
    r'<span\b(?=[^>]*\bdata-mention\b)'
    r'(?=[^>]*\bdata-id=["\'](?P<id>[^"\']*)["\'])'
    r'(?:(?=[^>]*\bdata-kind=["\'](?P<kind>[^"\']*)["\']))?'
    r'(?:(?=[^>]*\bdata-label=["\'](?P<label>[^"\']*)["\']))?'
    r'[^>]*>(?P<body>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)


def build_mention_span(kind: str, object_id, label: str) -> str:
    """Render the chip markup for `label` pointing at `object_id`.

    Both `label` and `object_id` are HTML-escaped. The escalation call
    sites pass a plain int id and a column value for the label, but this
    is also the flush path's renderer (`render.py`'s
    `_render_inline_children`), where both values come straight off a
    Y.Doc attribute — i.e. off the wire from a connected browser. They
    land inside attribute values that the frontend reads back with
    `getAttribute`, so neither may be able to close the quote.
    """
    safe_kind = kind if kind in MENTION_KINDS else 'user'
    safe_label = html.escape(str(label), quote=True)
    safe_id = html.escape(str(object_id), quote=True)
    char = _MENTION_CHAR_BY_KIND.get(safe_kind, _DEFAULT_MENTION_CHAR)
    return (f'<span data-mention data-kind="{safe_kind}" data-id="{safe_id}" '
            f'data-label="{safe_label}">{char}{safe_label}</span>')


def mention_node_name(kind: str) -> str:
    """ProseMirror node type for `kind`, defaulting to the `#` node."""
    return MENTION_NODE_BY_KIND.get(kind, 'caseMention')
