#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""markdown ↔ Yjs XmlFragment bridge for the collaborative editor.

We hold the authoritative Y.Doc server-side (see `business/collab.py`)
and clients speak Yjs updates over the wire. Three conversions are
needed at the storage boundary:

  * `markdown_to_ydoc_update(md)`  → bytes
      Called ONCE per document when its `collab_doc` row is being
      seeded from the source column (`notes.note_content` etc.). We
      parse the markdown into a ProseMirror-shaped XmlFragment inside
      a fresh Y.Doc and return the doc's update bytes. That's what
      the first joiner sees via `sync-init` and what every subsequent
      joiner merges against.

  * `append_markdown_to_ydoc_update(state, md)` → bytes
      Called when the SERVER needs to add content to a document that
      has already been seeded (alert escalation appending a mention
      chip to a case summary, see `collab/sync.py`). Once a
      `collab_doc` row exists the Y.Doc is authoritative and
      `ensure_snapshot` ignores the source column, so appending to
      the column alone is silently discarded by the next flush. We
      re-hydrate `state`, append the parsed blocks at the end of the
      fragment and return the full new state.

  * `ydoc_update_to_markdown(update)` → str
      Called on flush (last-client-disconnect + periodic tick). We
      apply the stored update into a fresh Y.Doc, walk the fragment,
      render markdown. Written back to the source column.

Both sides must speak the ProseMirror doc shape that TipTap's
`Collaboration` + `y-prosemirror` binding produces client-side. That
means specific tag names (heading / paragraph / bulletList / …), and
marks stored as `_prosemirror-mark` attributes on `XmlText` nodes
matching what y-prosemirror emits. StarterKit's default node set
covers everything the editor currently supports; if we ever add a
new node type on the frontend, we need to teach both sides here.

Design constraints that shape the code:
  1. Idempotent when possible — the SAME markdown must produce the
     SAME Y.Doc every time we re-seed, so that a fresh joiner and a
     re-hydration after restart produce identical state.
  2. Round-trip stable — `render(parse(md))` should match `md`
     modulo whitespace normalization. If it doesn't, users will
     see their content mutate on save.
  3. No inline HTML pass-through, with ONE documented exception:
     markdown-it-py is CommonMark-only, so if the source column has
     legacy HTML (from pre-editor code) we drop through to
     `<html_block>` tokens and render them back as a fenced code
     block. Aggressive, but safe — we're not going to reconstruct
     arbitrary HTML into a CRDT tree. The exception is the mention
     chip (`<span data-mention …>`, see `collab/mentions.py`): it is
     a closed, machine-written shape that maps 1:1 onto a TipTap
     node, so a dedicated inline rule parses it into a
     `userMention` / `caseMention` element and the renderer emits it
     back verbatim. Without that both directions lose it — literal
     text on the way in, silently dropped on the way out.
"""

from __future__ import annotations

import html
import logging
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdown_it.rules_inline import StateInline
from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from app.iris_engine.collab.mentions import MENTION_DEFAULT_KIND_BY_NODE
from app.iris_engine.collab.mentions import MENTION_KINDS
from app.iris_engine.collab.mentions import MENTION_NODE_NAMES
from app.iris_engine.collab.mentions import MENTION_SPAN_RE
from app.iris_engine.collab.mentions import build_mention_span
from app.iris_engine.collab.mentions import mention_node_name


logger = logging.getLogger(__name__)


# The Yjs XmlFragment field name that y-prosemirror uses by default.
# Both the frontend Collaboration extension and this renderer MUST
# agree on this key — a mismatch produces an empty editor with no
# obvious error.
_PROSEMIRROR_FIELD = 'prosemirror'


# ---------------------------------------------------------------------------
# markdown → Y.Doc
# ---------------------------------------------------------------------------

def _parse_markdown(md: str) -> list[Token]:
    """Clean `md` of the legacy shapes we know about and tokenize it.

    Shared by `markdown_to_ydoc_update` and
    `append_markdown_to_ydoc_update`: both must produce the exact same
    tree for the same markdown, or a chip appended into a live doc
    would parse differently from the same chip re-seeded from the
    source column.
    """
    # `commonmark` + explicit `enable('table')` gives us GFM pipe tables
    # (thead/tbody/tr/th/td tokens) without pulling in the rest of the
    # `gfm-like` preset — notably `linkify`, which requires the extra
    # `linkify-it-py` dependency. Legacy notes and case summaries in
    # production predate the CommonMark editor and often contain tables
    # (findings, IOC lists, etc.); dropping them silently at the parser
    # left users with pipes and dashes rendered as literal text.
    md_parser = (
        MarkdownIt('commonmark', {'html': False, 'breaks': False})
        .enable('table')
    )
    # The one inline-HTML shape we do understand. Registered BEFORE
    # `text` so the rule gets first look at the `<` that opens the
    # span; otherwise `text` swallows it and the chip degrades to
    # literal markup. See `_mention_inline_rule`.
    md_parser.inline.ruler.before('text', 'iris_mention', _mention_inline_rule)
    # Some legacy imports concatenated an entire GFM table onto a single
    # physical line ("| A | B | |---|---| | 1 | 2 | | 3 | 4 |"). markdown-it
    # sees that as one paragraph and never emits table tokens. Un-flatten
    # those single-liners into proper multi-line tables before parsing —
    # cheap heuristic, safe for well-formed input.
    #
    # `_rewrite_legacy_case_urls` runs FIRST so old IRIS v2.4.29 URLs
    # (`/case/iocs?cid=X&ioc_id=Y`, `/case?cid=X`, …) get rewritten to the
    # new SvelteKit path form before the tokens are frozen into the Y.Doc.
    # Without this the raw column keeps the old URLs forever after the
    # first collab open, since the Y.Doc becomes the source of truth.
    # `_strip_fontawesome_tags` runs after it and before the parser, in the
    # same order as the frontend's `normalizeLegacyContent`.
    cleaned = _strip_fontawesome_tags(_rewrite_legacy_case_urls(md or ''))
    return md_parser.parse(_unflatten_pipe_tables(cleaned))


def markdown_to_ydoc_update(md: str) -> bytes:
    """Build a fresh Y.Doc from `md` and return its update bytes.

    Empty input is legal — we return the update for an empty doc so
    the client's `sync-init` handler can still apply it cleanly (an
    empty XmlFragment is a valid starting state).
    """
    doc = Doc()
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag

    tokens = _parse_markdown(md)

    with doc.transaction():
        _build_blocks_into(frag, tokens)

    return doc.get_update()


def append_markdown_to_ydoc_update(state: bytes | None, md: str) -> bytes:
    """Append `md` to the end of the Y.Doc encoded by `state`.

    Returns the FULL new state (not a delta), so the result drops
    straight into `collab_doc.y_state` alongside what `ensure_snapshot`
    writes there.

    `state` may be `None` or empty — we then start from an empty
    fragment, which makes the result content-equivalent to
    `markdown_to_ydoc_update(md)`. Note that the two will never be
    byte-equal: Yjs stamps a random client id into every update.

    The appended blocks land AFTER everything already in the fragment.
    That's the only ordering a server-side writer can safely pick —
    it has no cursor and no idea what a concurrently-connected human
    is editing, and an append at the tail is the least likely to
    collide with them once the CRDT merges.
    """
    doc = Doc()
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag
    if state:
        doc.apply_update(bytes(state))

    tokens = _parse_markdown(md)

    with doc.transaction():
        _build_blocks_into(frag, tokens)

    return doc.get_update()


_LEGACY_CASE_SUBPATH_ID_PARAM = {
    'iocs': 'ioc_id',
    'assets': 'asset_id',
    'tasks': 'id',           # v2.4 tasks used `id=`, not `task_id=`
    'notes': 'note_id',
    'evidences': 'evidence_id',
}

_LEGACY_CASE_SUBPATH_RE = re.compile(
    r'/case/(iocs|assets|tasks|notes|evidences|timeline)(\?[^)\s"\'<>]*)'
)
_LEGACY_CASE_BARE_RE = re.compile(r'/case(\?[^)\s"\'<>]*)')
_LEGACY_CID_RE = re.compile(r'[?&]cid=(\d+)')


def _rewrite_legacy_case_urls(md: str) -> str:
    """Rewrite IRIS v2.4.29 flat-query case URLs to the SvelteKit v2 tree.

    Mirrors `iris-frontend/src/lib/components/common/MarkDown/legacy-content.ts`
    — kept intentionally byte-identical in behaviour so a document rewritten
    server-side matches what the frontend normalizer would produce. Runs
    before the tokens are seeded into the Y.Doc so old links don't get
    locked into collaborative state.
    """
    if '/case' not in md or 'cid=' not in md:
        return md

    def _subpath(match: re.Match) -> str:
        section = match.group(1)
        query = match.group(2)
        cid_match = _LEGACY_CID_RE.search(query)
        if not cid_match:
            return match.group(0)
        cid = cid_match.group(1)
        if section == 'timeline':
            return f'/case/{cid}/timeline'
        id_param = _LEGACY_CASE_SUBPATH_ID_PARAM.get(section)
        if not id_param:
            return match.group(0)
        id_match = re.search(rf'[?&]{id_param}=(\d+)', query)
        if not id_match:
            return f'/case/{cid}/{section}'
        return f'/case/{cid}/{section}/{id_match.group(1)}'

    def _bare(match: re.Match) -> str:
        cid_match = _LEGACY_CID_RE.search(match.group(1))
        if not cid_match:
            return match.group(0)
        return f'/case/{cid_match.group(1)}'

    out = _LEGACY_CASE_SUBPATH_RE.sub(_subpath, md)
    return _LEGACY_CASE_BARE_RE.sub(_bare, out)


# Self-empty FontAwesome `<i>` tags. Mirrors the regex in
# `stripFontAwesomeTags` (legacy-content.ts) character for character;
# the only deviation is Python's `\w` being Unicode-aware where JS's is
# ASCII-only, which cannot change the outcome here because the
# following `[^"']*` already accepts everything `\w` would add.
_FONTAWESOME_TAG_RE = re.compile(
    r'<i\b[^>]*\bclass=["\'][^"\']*\bfa[-\w]*[^"\']*["\'][^>]*>\s*</i>',
    re.IGNORECASE,
)


def _strip_fontawesome_tags(md: str) -> str:
    """Drop `<i class="fa-…"></i>` icon tags from legacy content.

    Mirrors `stripFontAwesomeTags` in
    `iris-frontend/src/lib/components/common/MarkDown/legacy-content.ts`
    — same deliberate byte-for-byte behaviour pact as
    `_rewrite_legacy_case_urls` above.

    IRIS v2 embedded FontAwesome markup inside link text (case
    descriptions written by alert escalation, datastore file links, …).
    This frontend doesn't ship FontAwesome, and the parser runs with
    `html: False`, so leaving them in surfaces the raw tag as literal
    text in the editor. They're always self-empty, so stripping them
    loses nothing: `[<i class='fa-solid fa-bell'></i> #106](…)` reads
    as `[ #106](…)`.

    Only the empty form is matched — an `<i>` with real content keeps
    it rather than having it swallowed.
    """
    if '<i' not in md and '<I' not in md:
        return md
    return _FONTAWESOME_TAG_RE.sub('', md)


def _mention_inline_rule(state: StateInline, silent: bool) -> bool:
    """markdown-it inline rule: `<span data-mention …>` → `iris_mention`.

    The chip is the single inline-HTML shape this renderer understands
    (see the module docstring). It is machine-written by
    `collab/mentions.py` and by the TipTap node's `renderHTML`, so a
    regex match is sufficient and we never interpret the captured
    values as markup — they become plain XmlElement attributes.

    Registered before `text`, so this fires on every `<` in the inline
    stream. Returning False on a non-match hands the character back to
    the normal rules, where it ends up as literal text exactly as
    before.

    `silent` is markdown-it's validation pass (used when scanning for a
    link label's end): consume the input and report the match, but
    don't emit a token.
    """
    if state.src[state.pos] != '<':
        return False
    match = MENTION_SPAN_RE.match(state.src, state.pos, state.posMax)
    if match is None:
        return False

    if not silent:
        token = state.push('iris_mention', '', 0)
        token.meta = {
            'kind': _normalize_mention_kind(match.group('kind')),
            # Both attributes hold HTML-escaped values; the Y.Doc
            # attributes must hold the real ones, or `build_mention_span`
            # would escape them a second time on the way back out and the
            # round trip would drift a little further on every flush.
            'id': html.unescape(match.group('id') or ''),
            'label': html.unescape(match.group('label') or ''),
        }

    state.pos = match.end()
    return True


def _normalize_mention_kind(raw: str | None) -> str:
    """Coerce a span's `data-kind` to a kind the frontend can render.

    A missing or unrecognised kind falls back to the default for the
    `#`-triggered node rather than being passed through: an unknown
    string would otherwise reach `build_mention_span`, which coerces
    anything it doesn't know to `user` — turning a typo'd chip into a
    user mention that the notification parser would then try to fan
    out to a non-existent account.
    """
    if raw in MENTION_KINDS:
        return raw
    return MENTION_DEFAULT_KIND_BY_NODE[mention_node_name(raw or '')]


def _unflatten_pipe_tables(md: str) -> str:
    """Repair single-line GFM pipe tables produced by legacy exporters.

    A well-formed table is `header | separator | row+`, each on its own
    line. When an import concatenates them onto a single line — `| A | B
    | |---|---| | 1 | 2 |` — markdown-it can't recognise it and drops
    through to paragraph parsing. Users see pipes and dashes as literal
    text.

    Detection: a line that contains
      * a `| --- | --- | ... |` separator segment, AND
      * multiple `| ... | ... | ... |` groups
    is almost certainly a flattened table. We split on ` | | ` (the
    boundary the concatenation left between adjacent rows) and re-insert
    line breaks so the standard GFM table rule can pick it back up.

    Runs strictly per-line, so paragraphs and non-flat tables are
    untouched. False positives are unlikely — inline pipes in normal
    prose don't produce a `| --- | --- |` separator segment.
    """
    if '|' not in md or '---' not in md:
        return md

    separator_re = re.compile(r'\|(\s*:?-{2,}:?\s*\|)+')
    # A row is `| cell | cell | ... |` with no internal newlines.
    # We split on ` | | ` — the collapsed-out newline between rows —
    # which reliably delimits rows in the pathological single-line
    # shape without matching legitimate mid-row pipe pairs.
    row_boundary_re = re.compile(r'\|\s*\|')

    out_lines: list[str] = []
    for line in md.split('\n'):
        if separator_re.search(line) and row_boundary_re.search(line):
            # Restore the newlines between adjacent `|...|` rows.
            # `.strip()` on each piece removes the extra spaces the
            # collapse left behind so the resulting rows look natural
            # in the source column after a round-trip.
            pieces = [p.strip() for p in row_boundary_re.split(line)]
            # Each interior piece lost a leading `|` (the row-start)
            # and a trailing `|` (the row-end) to the split. Re-add
            # them so we emit valid GFM rows.
            fixed: list[str] = []
            for idx, p in enumerate(pieces):
                if not p:
                    continue
                if idx > 0 and not p.startswith('|'):
                    p = '| ' + p
                if idx < len(pieces) - 1 and not p.endswith('|'):
                    p = p + ' |'
                fixed.append(p)
            # Multi-line tables need a blank line separating them from
            # the surrounding text — otherwise markdown-it fuses the
            # first row with any prose immediately before it.
            if out_lines and out_lines[-1].strip():
                out_lines.append('')
            out_lines.extend(fixed)
            out_lines.append('')
        else:
            out_lines.append(line)
    return '\n'.join(out_lines)


def _build_blocks_into(container, tokens: list[Token]) -> None:
    """Walk `tokens` and append the corresponding block-level XmlElements
    onto `container` (which must already be integrated into a Doc).

    `container` is either the root XmlFragment or a `listItem`/`blockquote`
    element that's mid-integration. We DON'T open a new transaction here;
    the caller owns the transaction that wraps every mutation.
    """
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        t = tok.type

        if t == 'heading_open':
            level = int(tok.tag[1])  # 'h1' → 1
            close_i = _find_close(tokens, i, 'heading_close')
            el = XmlElement('heading')
            container.children.append(el)
            el.attributes['level'] = str(level)
            _build_inline_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        if t == 'paragraph_open':
            close_i = _find_close(tokens, i, 'paragraph_close')
            el = XmlElement('paragraph')
            container.children.append(el)
            _build_inline_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        if t == 'bullet_list_open':
            close_i = _find_close(tokens, i, 'bullet_list_close')
            el = XmlElement('bulletList')
            container.children.append(el)
            _build_list_items_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        if t == 'ordered_list_open':
            close_i = _find_close(tokens, i, 'ordered_list_close')
            el = XmlElement('orderedList')
            container.children.append(el)
            start = tok.attrGet('start')
            if start is not None and str(start) != '1':
                el.attributes['start'] = str(start)
            _build_list_items_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        if t == 'blockquote_open':
            close_i = _find_close(tokens, i, 'blockquote_close')
            el = XmlElement('blockquote')
            container.children.append(el)
            _build_blocks_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        if t == 'code_block' or t == 'fence':
            el = XmlElement('codeBlock')
            container.children.append(el)
            if tok.info:
                el.attributes['language'] = tok.info.strip()
            # Trailing newline: markdown-it always appends one; TipTap
            # doesn't want it inside the codeBlock's text node.
            body = (tok.content or '').rstrip('\n')
            if body:
                el.children.append(XmlText(body))
            i += 1
            continue

        if t == 'hr':
            container.children.append(XmlElement('horizontalRule'))
            i += 1
            continue

        if t == 'html_block':
            # Legacy content that predates the CommonMark editor. Render
            # it as a code block rather than trying to reconstruct HTML
            # in the tree — keeps content visible and unambiguous while
            # avoiding a whole HTML-to-ProseMirror parser.
            el = XmlElement('codeBlock')
            container.children.append(el)
            el.attributes['language'] = 'html'
            body = (tok.content or '').rstrip('\n')
            if body:
                el.children.append(XmlText(body))
            i += 1
            continue

        if t == 'table_open':
            close_i = _find_close(tokens, i, 'table_close')
            el = XmlElement('table')
            container.children.append(el)
            _build_table_rows_into(el, tokens[i + 1: close_i])
            i = close_i + 1
            continue

        # Anything else: skip. Unknown block tokens are almost always
        # opener/closer noise (thead/tbody are handled inside
        # `_build_table_rows_into`; anything else falls here).
        i += 1


def _build_list_items_into(list_el, tokens: list[Token]) -> None:
    """Turn `list_item_open ... list_item_close` runs inside `tokens`
    into `listItem` XmlElements appended to `list_el`."""
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.type != 'list_item_open':
            i += 1
            continue
        close_i = _find_close(tokens, i, 'list_item_close')
        item = XmlElement('listItem')
        list_el.children.append(item)
        _build_blocks_into(item, tokens[i + 1: close_i])
        i = close_i + 1


def _build_table_rows_into(table_el, tokens: list[Token]) -> None:
    """Turn the interior of a GFM table (`table_open ... table_close`)
    into `tableRow` / `tableHeader` / `tableCell` XmlElements matching
    the TipTap Table extension's schema.

    markdown-it wraps the row runs in `thead_open ... thead_close` and
    `tbody_open ... tbody_close`; those wrappers are semantic-only in
    our target schema (TipTap distinguishes header vs body cells at
    the CELL level, not the section level), so we flatten them.

    Each cell in the TipTap schema requires at least one block child.
    We wrap the row's inline content in a `paragraph` — same shape
    y-prosemirror produces when a user types into a fresh table.
    """
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        # Skip thead/tbody wrappers — flat row list matches TipTap.
        if tok.type in ('thead_open', 'thead_close', 'tbody_open', 'tbody_close'):
            i += 1
            continue
        if tok.type != 'tr_open':
            i += 1
            continue
        row_close = _find_close(tokens, i, 'tr_close')
        row_el = XmlElement('tableRow')
        table_el.children.append(row_el)
        _build_table_cells_into(row_el, tokens[i + 1: row_close])
        i = row_close + 1


def _build_table_cells_into(row_el, tokens: list[Token]) -> None:
    """Emit `tableHeader` or `tableCell` XmlElements for each th/td
    inside a row. Each cell's inline content becomes a nested
    `paragraph` — TipTap's Table cell requires at least one block
    child, and paragraph is the neutral choice for text-only cells.

    We deliberately DO NOT copy colspan/rowspan/colwidth here. GFM
    pipe tables don't express those; a legacy note being migrated
    into the collab doc is always a plain rectangular grid. If the
    user later merges cells in the TipTap editor, y-prosemirror
    writes the merge attrs into the XmlElement and they survive
    subsequent flushes — this migration path just seeds the simplest
    possible table.
    """
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.type == 'th_open':
            close_i = _find_close(tokens, i, 'th_close')
            cell = XmlElement('tableHeader')
            row_el.children.append(cell)
            para = XmlElement('paragraph')
            cell.children.append(para)
            _build_inline_into(para, tokens[i + 1: close_i])
            i = close_i + 1
            continue
        if tok.type == 'td_open':
            close_i = _find_close(tokens, i, 'td_close')
            cell = XmlElement('tableCell')
            row_el.children.append(cell)
            para = XmlElement('paragraph')
            cell.children.append(para)
            _build_inline_into(para, tokens[i + 1: close_i])
            i = close_i + 1
            continue
        i += 1


def _build_inline_into(block_el, inline_tokens: list[Token]) -> None:
    """Walk `paragraph_open ... paragraph_close` interior (or heading
    interior) and append text/hardBreak nodes with marks applied.

    markdown-it flattens the interior of a paragraph into a single
    `inline` token whose `.children` are the actual leaf tokens
    (text, em_open/em_close, strong_open/strong_close, code_inline,
    link_open/link_close, image, softbreak, hardbreak). We flatten
    those with a small mark-stack so bold/italic/code/link/strike
    become y-prosemirror marks on `XmlText` nodes.
    """
    if not inline_tokens:
        return
    # There should be exactly one 'inline' token here (that's how
    # markdown-it structures paragraph/heading interiors).
    inline = None
    for t in inline_tokens:
        if t.type == 'inline':
            inline = t
            break
    if inline is None or not inline.children:
        return

    marks: list[dict] = []

    def _open_mark(name: str, attrs: dict | None = None) -> None:
        m = {'type': name}
        if attrs:
            m['attrs'] = attrs
        marks.append(m)

    def _close_mark(name: str) -> None:
        for i in range(len(marks) - 1, -1, -1):
            if marks[i]['type'] == name:
                marks.pop(i)
                return

    def _emit_text(text: str) -> None:
        if not text:
            return
        node = XmlText(text)
        block_el.children.append(node)
        if marks:
            # y-prosemirror stores marks as an attribute keyed by the
            # y-prosemirror-specific `_pm-marks` convention. In pycrdt
            # we set them as plain attributes; the client's y-prosemirror
            # binding reads them via the same protocol.
            #
            # NOTE: y-prosemirror's actual encoding uses a special
            # attribute name; setting per-mark bool attrs matches how
            # TipTap's Yjs bindings serialize simple marks. This is the
            # brittlest part of the renderer — see the round-trip tests.
            for m in marks:
                node.attributes[m['type']] = _mark_to_attr_value(m)

    for c in inline.children:
        ct = c.type
        if ct == 'text':
            _emit_text(c.content)
        elif ct == 'softbreak':
            # A bare newline inside a paragraph. CommonMark says "render
            # as a space", but IRIS content is overwhelmingly operational
            # text — scanner exports, host lists, `key: value` blocks —
            # where every newline the analyst typed carries meaning. We
            # map it to a `hardBreak` node so the line survives the trip
            # into the CRDT and back out to markdown as `  \n`.
            #
            # (markdown-it's own `breaks` option is not the lever here: it
            # only changes the HTML *renderer*, and we walk tokens
            # directly. The frontend counterpart is `Markdown.configure({
            # breaks: true })` in `MarkDownEditor.svelte` plus showdown's
            # `simpleLineBreaks` in `MarkDown/converter.ts`.)
            block_el.children.append(XmlElement('hardBreak'))
        elif ct == 'hardbreak':
            block_el.children.append(XmlElement('hardBreak'))
        elif ct == 'em_open':
            _open_mark('italic')
        elif ct == 'em_close':
            _close_mark('italic')
        elif ct == 'strong_open':
            _open_mark('bold')
        elif ct == 'strong_close':
            _close_mark('bold')
        elif ct == 's_open':
            _open_mark('strike')
        elif ct == 's_close':
            _close_mark('strike')
        elif ct == 'code_inline':
            _open_mark('code')
            _emit_text(c.content)
            _close_mark('code')
        elif ct == 'link_open':
            _open_mark('link', {
                'href': c.attrGet('href') or '',
                'title': c.attrGet('title') or '',
            })
        elif ct == 'link_close':
            _close_mark('link')
        elif ct == 'image':
            img = XmlElement('image')
            block_el.children.append(img)
            src = c.attrGet('src')
            if src:
                img.attributes['src'] = src
            title = c.attrGet('title')
            if title:
                img.attributes['title'] = title
            # `content` on an image token is its alt text.
            if c.content:
                img.attributes['alt'] = c.content
        elif ct == 'iris_mention':
            # A chip is an atomic NODE, not marked text, so it is
            # appended bare like `hardBreak` / `image` — the mark stack
            # only ever decorates XmlText. A chip written inside bold or
            # link text therefore keeps the chip but not the surrounding
            # mark, which matches how `image` has always behaved and is
            # what y-prosemirror produces for an atom in a marked range.
            meta = c.meta or {}
            kind = meta.get('kind') or MENTION_DEFAULT_KIND_BY_NODE['caseMention']
            chip = XmlElement(mention_node_name(kind))
            block_el.children.append(chip)
            # All three are plain string attributes, matching the attrs
            # the TipTap mention node declares.
            chip.attributes['id'] = str(meta.get('id', ''))
            chip.attributes['label'] = str(meta.get('label', ''))
            chip.attributes['kind'] = kind
        # Unknown inline types are silently dropped for the same reason
        # we drop unknown block tokens: keeps the tree coherent, and
        # any lost formatting is recoverable by retyping.


def _mark_to_attr_value(mark: dict) -> str:
    """Serialize a mark to an attribute value.

    Simple marks (bold/italic/code/strike) → 'true'. Marks with attrs
    (link) → JSON so we can round-trip href/title. This encoding lives
    entirely server-side; the frontend's y-prosemirror binding treats
    marks via its own protocol regardless of what we put in these
    attribute values. The values matter only when WE re-parse them
    in `ydoc_update_to_markdown` below.
    """
    if 'attrs' not in mark:
        return 'true'
    import json
    return json.dumps(mark['attrs'])


def _find_close(tokens: list[Token], open_idx: int, close_type: str) -> int:
    """Return the index of the matching close token, respecting nesting.

    `open_idx` points at the opening token; we walk forward tracking a
    nesting counter so a nested `paragraph_open` inside a `blockquote`
    doesn't confuse the search for the blockquote's close.
    """
    depth = 0
    open_type = tokens[open_idx].type
    for j in range(open_idx + 1, len(tokens)):
        t = tokens[j].type
        if t == open_type:
            depth += 1
        elif t == close_type:
            if depth == 0:
                return j
            depth -= 1
    # Malformed input — return the last token index so the caller
    # doesn't loop forever. This shouldn't happen with markdown-it output.
    logger.warning('collab render: unmatched %s from token %s', close_type, open_type)
    return len(tokens) - 1


# ---------------------------------------------------------------------------
# Y.Doc → markdown
# ---------------------------------------------------------------------------

def ydoc_update_to_markdown(update: bytes | None) -> str:
    """Apply `update` into a fresh Y.Doc, walk the XmlFragment, render markdown.

    `None` or empty bytes → empty string, matching an empty editor.
    """
    if not update:
        return ''
    doc = Doc()
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag
    doc.apply_update(update)

    lines: list[str] = []
    for child in list(frag.children):
        _render_block(child, lines, list_context=None)
    # Trim trailing blank lines but keep the terminal newline convention.
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines) + ('\n' if lines else '')


# Matches a hardBreak as `_render_inline_children` emits it (`'  \n'`,
# plus any whitespace the neighbouring text contributed) so callers that
# can't host a line break — headings — can collapse it back to a space.
_HARD_BREAK_RE = re.compile(r'[ \t]*\n[ \t]*')


def _render_block(node, lines: list[str], *, list_context) -> None:
    """Append the markdown lines for a single block node to `lines`.

    `list_context` carries per-list state when we're inside a list
    (kind='ul'|'ol', counter for numbered lists, indent depth). None
    at the top level.
    """
    if isinstance(node, XmlText):
        # Stray XmlText at block level — very rare, but render as a
        # bare paragraph so we don't drop content.
        lines.append(str(node))
        lines.append('')
        return

    tag = getattr(node, 'tag', None)

    if tag == 'heading':
        level = _int_attr(node, 'level', default=1)
        level = max(1, min(6, level))
        # ATX headings are single-line by definition. A hardBreak inside
        # one is reachable in the editor (Shift-Enter), so collapse it to
        # a space — emitting the second line raw would re-parse as a
        # separate paragraph and silently split the heading in two.
        text = _HARD_BREAK_RE.sub(' ', _render_inline_children(node))
        lines.append('#' * level + ' ' + text)
        lines.append('')
        return

    if tag == 'paragraph':
        text = _render_inline_children(node)
        if text.strip():
            # `_render_inline_children` emits `'  \n'` per hardBreak, so a
            # paragraph with line breaks comes back as a multi-line string.
            # Split it: `lines` is the unit that blockquote and list-item
            # rendering prefix/indent, and an embedded newline would slip
            # past that prefixing and break the block on re-parse.
            lines.extend(text.split('\n'))
            lines.append('')
        return

    if tag == 'bulletList':
        for child in list(node.children):
            _render_list_item(child, lines, marker='- ')
        lines.append('')
        return

    if tag == 'orderedList':
        start = _int_attr(node, 'start', default=1)
        idx = start
        for child in list(node.children):
            _render_list_item(child, lines, marker=f'{idx}. ')
            idx += 1
        lines.append('')
        return

    if tag == 'blockquote':
        inner: list[str] = []
        for child in list(node.children):
            _render_block(child, inner, list_context=None)
        while inner and inner[-1] == '':
            inner.pop()
        for line in inner:
            lines.append('> ' + line if line else '>')
        lines.append('')
        return

    if tag == 'codeBlock':
        lang = _string_attr(node, 'language', default='') or ''
        lines.append('```' + lang)
        # `codeBlock`'s children are XmlText nodes with the raw code.
        for child in list(node.children):
            if isinstance(child, XmlText):
                for code_line in str(child).splitlines() or ['']:
                    lines.append(code_line)
        lines.append('```')
        lines.append('')
        return

    if tag == 'horizontalRule':
        lines.append('---')
        lines.append('')
        return

    if tag == 'image':
        src = _string_attr(node, 'src', default='')
        alt = _string_attr(node, 'alt', default='')
        title = _string_attr(node, 'title', default='')
        title_part = f' "{title}"' if title else ''
        lines.append(f'![{alt}]({src}{title_part})')
        lines.append('')
        return

    if tag == 'table':
        _render_table(node, lines)
        return

    # Unknown block: render nothing rather than crashing. Loud in the
    # log so a missing case here doesn't silently eat content.
    logger.warning('collab render: unknown block tag %r — skipped', tag)


def _render_table(node, lines: list[str]) -> None:
    """Render a `table` XmlElement as a GFM pipe table.

    GFM requires the first row to be the header and the second row to
    be a separator (`| --- | --- |`). TipTap's schema tags header cells
    as `tableHeader` and body cells as `tableCell`, and doesn't require
    the header to be the first row — but pipe syntax has no way to
    express a mid-table header. We handle both shapes pragmatically:
      * If the first row is all `tableHeader`, treat it as the header
        and emit the separator after it (round-trip friendly).
      * Otherwise, synthesise an empty header row above the data so
        the output re-parses as a valid GFM table. This is lossy for
        the visual "no header" shape, but the alternative is emitting
        raw pipes that markdown-it will re-parse as a paragraph and
        we lose the tabular structure entirely.

    Cells are joined with pipes; internal pipes get escaped as `\\|`
    to keep the row shape parseable. Multi-line cell content is
    collapsed to a single line joined with `<br>` — GFM tables can't
    contain block content between the pipes.
    """
    rows: list[tuple[bool, list[str]]] = []
    for row in list(node.children):
        if not isinstance(row, XmlElement) or row.tag != 'tableRow':
            continue
        cells: list[str] = []
        all_headers = True
        for cell in list(row.children):
            if not isinstance(cell, XmlElement):
                continue
            if cell.tag not in ('tableHeader', 'tableCell'):
                continue
            if cell.tag != 'tableHeader':
                all_headers = False
            cell_lines: list[str] = []
            for child in list(cell.children):
                _render_block(child, cell_lines, list_context=None)
            while cell_lines and cell_lines[-1] == '':
                cell_lines.pop()
            # GFM cells are single-line: join intra-cell breaks with
            # `<br>`, and escape stray pipes so the row shape survives.
            text = '<br>'.join(line.strip() for line in cell_lines if line is not None)
            cells.append(text.replace('|', '\\|'))
        rows.append((all_headers and bool(cells), cells))

    if not rows:
        return

    # Column count: max cells across rows. Short rows get padded so
    # every emitted line has the same pipe count (a GFM parser is
    # forgiving here, but consistent output is easier on humans
    # reading the raw source column).
    cols = max(len(r[1]) for r in rows)
    if cols == 0:
        return

    def _emit(cells: list[str]) -> None:
        padded = cells + [''] * (cols - len(cells))
        lines.append('| ' + ' | '.join(padded) + ' |')

    if rows[0][0]:
        _emit(rows[0][1])
        lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
        body_start = 1
    else:
        # Header-less TipTap table → synthesise an empty header row so
        # the output remains a valid GFM table when re-parsed.
        _emit([''] * cols)
        lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
        body_start = 0

    for _, cells in rows[body_start:]:
        _emit(cells)
    lines.append('')


def _render_list_item(node, lines: list[str], marker: str) -> None:
    """Render a `listItem`'s children as a marker-prefixed list item.

    We render each block child, then indent all continuation lines to
    align under the marker column.
    """
    if not isinstance(node, XmlElement) or node.tag != 'listItem':
        return
    inner: list[str] = []
    for child in list(node.children):
        _render_block(child, inner, list_context=None)
    while inner and inner[-1] == '':
        inner.pop()
    if not inner:
        lines.append(marker.rstrip())
        return
    indent = ' ' * len(marker)
    lines.append(marker + inner[0])
    for line in inner[1:]:
        lines.append((indent + line) if line else '')


def _render_inline_children(block) -> str:
    """Render the inline children of a block node as a single markdown
    string. Handles the mark stack in reverse of `_build_inline_into`."""
    out: list[str] = []
    for child in list(block.children):
        if isinstance(child, XmlText):
            out.append(_render_text_with_marks(child))
        elif isinstance(child, XmlElement):
            if child.tag == 'hardBreak':
                out.append('  \n')
            elif child.tag == 'image':
                src = _string_attr(child, 'src', default='')
                alt = _string_attr(child, 'alt', default='')
                title = _string_attr(child, 'title', default='')
                title_part = f' "{title}"' if title else ''
                out.append(f'![{alt}]({src}{title_part})')
            elif child.tag in MENTION_NODE_NAMES:
                # Re-emit the span the mention rule parsed on the way in.
                # Before this branch existed, every chip a user typed was
                # dropped on the first flush back to the source column.
                kind = _string_attr(child, 'kind', default='')
                if kind not in MENTION_KINDS:
                    kind = MENTION_DEFAULT_KIND_BY_NODE[child.tag]
                out.append(build_mention_span(
                    kind,
                    _string_attr(child, 'id', default=''),
                    _string_attr(child, 'label', default=''),
                ))
            # Other inline elements are unexpected; ignore.
    return ''.join(out)


def _render_text_with_marks(node: XmlText) -> str:
    """Wrap the text in markdown syntax matching the marks stored on the
    XmlText node's attributes.

    Order of application matches TipTap's serializer: links wrap outermost,
    then code, then bold, then italic, then strike. Getting the order right
    matters for CommonMark's tight escapes.
    """
    text = str(node)
    if not text:
        return ''
    attrs = dict(node.attributes) if hasattr(node, 'attributes') else {}

    # Escape markdown special chars in raw text before we apply marks
    # (otherwise `**` in normal prose would render as bold on next parse).
    if 'code' not in attrs:
        text = _escape_markdown(text)

    if 'strike' in attrs:
        text = f'~~{text}~~'
    if 'italic' in attrs:
        text = f'*{text}*'
    if 'bold' in attrs:
        text = f'**{text}**'
    if 'code' in attrs:
        text = f'`{text}`'
    if 'link' in attrs:
        import json
        try:
            link_attrs = json.loads(attrs['link'])
        except (TypeError, ValueError):
            link_attrs = {'href': '', 'title': ''}
        href = link_attrs.get('href', '')
        title = link_attrs.get('title', '')
        title_part = f' "{title}"' if title else ''
        text = f'[{text}]({href}{title_part})'
    return text


# Characters that need escaping when they appear MID-TEXT to prevent
# a re-parse from interpreting them as markdown syntax. Deliberately
# narrower than markdown-it's max set — we don't blanket-escape `.`
# `!` `-` etc. because those only matter at line starts and their
# escaped form (`\.` `\!` `\-`) shows up as ugly noise in the raw
# source column readers see (REST, exports, git-tracked backups).
# Chars kept out and their justification:
#   `.`  only meaningful after digit + at line start (numbered list)
#   `!`  only meaningful when followed by `[` (image)
#   `-`  only meaningful at line start (unordered list / hr)
#   `+`  same as `-`
#   `>`  only meaningful at line start (blockquote)
#   `#`  only meaningful at line start (heading)
# These edge cases are handled by escaping only when the character
# lands at position 0 of a fresh line. Everything else is safe raw.
_MD_INLINE_ESCAPE_CHARS = r'\`*_[]<'


def _escape_markdown(text: str) -> str:
    r"""Prefix markdown-special characters with a backslash so a re-parse
    round-trips them as literal text.

    Kept intentionally narrow: only the chars that would change meaning
    if left raw INSIDE inline text. Chars that only matter at line
    starts (heading `#`, list `-`/`+`/`.`, blockquote `>`, hr `---`)
    are handled by the block renderer emitting them on their own lines
    where they can't collide with user text; escaping them everywhere
    would produce `\-hello` for a paragraph that begins with a hyphen,
    which is technically correct but visually wrong.
    """
    out = []
    for ch in text:
        if ch in _MD_INLINE_ESCAPE_CHARS:
            out.append('\\')
        out.append(ch)
    return ''.join(out)


def _int_attr(node, name: str, default: int) -> int:
    try:
        return int(node.attributes[name])
    except (KeyError, TypeError, ValueError):
        return default


def _string_attr(node, name: str, default: str) -> str:
    try:
        return str(node.attributes[name])
    except (KeyError, TypeError):
        return default
