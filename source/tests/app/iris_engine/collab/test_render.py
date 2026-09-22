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

"""Unit tests for app.iris_engine.collab.render.

All functions under test are pure Python — no DB, no Flask context, no mocks.
"""

from unittest import TestCase

from app.iris_engine.collab.mentions import build_mention_span
from app.iris_engine.collab.render import (
    _escape_markdown,
    _rewrite_legacy_case_urls,
    _strip_fontawesome_tags,
    _unflatten_pipe_tables,
    append_markdown_to_ydoc_update,
    markdown_to_ydoc_update,
    ydoc_update_to_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(md: str) -> str:
    """Convenience: md → ydoc update → md."""
    return ydoc_update_to_markdown(markdown_to_ydoc_update(md))


def _append(base_md: str, md: str) -> str:
    """Convenience: seed a doc from `base_md`, append `md`, render back."""
    state = markdown_to_ydoc_update(base_md)
    return ydoc_update_to_markdown(append_markdown_to_ydoc_update(state, md))


# ---------------------------------------------------------------------------
# _escape_markdown
# ---------------------------------------------------------------------------

class TestEscapeMarkdown(TestCase):

    def test_plain_text_is_unchanged(self):
        self.assertEqual('hello world', _escape_markdown('hello world'))

    def test_backslash_is_escaped(self):
        self.assertEqual(r'\\path\\to', _escape_markdown(r'\path\to'))

    def test_backtick_is_escaped(self):
        self.assertEqual(r'\`code\`', _escape_markdown('`code`'))

    def test_asterisk_is_escaped(self):
        self.assertEqual(r'\*bold\*', _escape_markdown('*bold*'))

    def test_underscore_is_escaped(self):
        self.assertEqual(r'\_italic\_', _escape_markdown('_italic_'))

    def test_open_bracket_is_escaped(self):
        self.assertEqual(r'\[link\]', _escape_markdown('[link]'))

    def test_less_than_is_escaped(self):
        # Only `<` is in the escape set; `>` is not (only meaningful at line
        # start for blockquotes, left unescaped to avoid visual noise).
        self.assertEqual(r'\<tag>', _escape_markdown('<tag>'))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _escape_markdown(''))

    def test_multiple_special_chars_each_escaped(self):
        result = _escape_markdown('`*_')
        self.assertEqual(r'\`\*\_', result)

    def test_chars_not_in_escape_set_are_left_alone(self):
        # ! . - + > # are deliberately NOT escaped (only matter at line start)
        self.assertEqual('!.-+>#', _escape_markdown('!.-+>#'))

    def test_digits_and_letters_pass_through(self):
        self.assertEqual('abc123', _escape_markdown('abc123'))


# ---------------------------------------------------------------------------
# _rewrite_legacy_case_urls
# ---------------------------------------------------------------------------

class TestRewriteLegacyCaseUrls(TestCase):

    # --- fast-path guards ---

    def test_string_without_case_is_returned_unchanged(self):
        s = 'no links here'
        self.assertIs(s, _rewrite_legacy_case_urls(s))

    def test_string_with_case_but_no_cid_is_returned_unchanged(self):
        s = '/case/iocs?ioc_id=5'
        self.assertEqual(s, _rewrite_legacy_case_urls(s))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _rewrite_legacy_case_urls(''))

    # --- subpath rewrites ---

    def test_iocs_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/iocs?cid=3&ioc_id=7')
        self.assertEqual('/case/3/iocs/7', result)

    def test_assets_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/assets?cid=10&asset_id=42')
        self.assertEqual('/case/10/assets/42', result)

    def test_tasks_url_rewritten(self):
        # tasks used `id=` not `task_id=` in v2.4
        result = _rewrite_legacy_case_urls('/case/tasks?cid=2&id=99')
        self.assertEqual('/case/2/tasks/99', result)

    def test_notes_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/notes?cid=5&note_id=11')
        self.assertEqual('/case/5/notes/11', result)

    def test_evidences_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/evidences?cid=1&evidence_id=3')
        self.assertEqual('/case/1/evidences/3', result)

    def test_timeline_url_rewritten_without_object_id(self):
        result = _rewrite_legacy_case_urls('/case/timeline?cid=7')
        self.assertEqual('/case/7/timeline', result)

    def test_bare_case_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case?cid=4')
        self.assertEqual('/case/4', result)

    def test_subpath_without_item_id_falls_back_to_section_only(self):
        # cid present, item id absent → /case/{cid}/{section}
        result = _rewrite_legacy_case_urls('/case/iocs?cid=3')
        self.assertEqual('/case/3/iocs', result)

    def test_multiple_urls_in_one_string_all_rewritten(self):
        md = ('See [IOC](/case/iocs?cid=1&ioc_id=2) '
              'and [case](/case?cid=1).')
        result = _rewrite_legacy_case_urls(md)
        self.assertIn('/case/1/iocs/2', result)
        self.assertIn('/case/1', result)

    def test_cid_as_param_after_ampersand_works(self):
        # cid may appear after the resource id param
        result = _rewrite_legacy_case_urls('/case/iocs?ioc_id=9&cid=5')
        self.assertEqual('/case/5/iocs/9', result)

    def test_unrelated_text_after_url_is_preserved(self):
        result = _rewrite_legacy_case_urls('Go to /case?cid=2 now.')
        self.assertIn('/case/2', result)
        self.assertIn('now.', result)


# ---------------------------------------------------------------------------
# _unflatten_pipe_tables
# ---------------------------------------------------------------------------

class TestUnflattenPipeTables(TestCase):

    def test_string_without_pipe_returned_unchanged(self):
        s = 'no table here'
        self.assertIs(s, _unflatten_pipe_tables(s))

    def test_string_without_dashes_returned_unchanged(self):
        s = '| a | b |'
        self.assertIs(s, _unflatten_pipe_tables(s))

    def test_well_formed_multiline_table_passes_through_intact(self):
        table = '| A | B |\n| --- | --- |\n| 1 | 2 |'
        result = _unflatten_pipe_tables(table)
        # All original cells still present
        self.assertIn('| A | B |', result)
        self.assertIn('| --- | --- |', result)
        self.assertIn('| 1 | 2 |', result)

    def test_flattened_table_is_split_into_rows(self):
        flat = '| A | B || --- | --- || 1 | 2 |'
        result = _unflatten_pipe_tables(flat)
        lines = [line for line in result.split('\n') if line.strip()]
        # Should be at least 3 separate lines now
        self.assertGreaterEqual(len(lines), 3)

    def test_flattened_table_rows_each_contain_pipe(self):
        flat = '| H1 | H2 || --- | --- || R1 | R2 |'
        result = _unflatten_pipe_tables(flat)
        lines = [line for line in result.split('\n') if line.strip()]
        for line in lines:
            self.assertIn('|', line)

    def test_plain_prose_with_dashes_is_not_split(self):
        # A line with dashes but no separator pattern should not be mangled
        prose = 'The score is 10 -- 5 and | denotes or.'
        result = _unflatten_pipe_tables(prose)
        self.assertIn('The score', result)

    def test_empty_input_returns_empty(self):
        self.assertEqual('', _unflatten_pipe_tables(''))

    def test_output_can_be_reparsed_by_markdownit(self):
        """After unflattening, markdown-it should recognise the table."""
        from markdown_it import MarkdownIt
        flat = '| Col1 | Col2 || --- | --- || val1 | val2 |'
        result = _unflatten_pipe_tables(flat)
        tokens = MarkdownIt('commonmark').enable('table').parse(result)
        types = [t.type for t in tokens]
        self.assertIn('table_open', types)


# ---------------------------------------------------------------------------
# _strip_fontawesome_tags
# ---------------------------------------------------------------------------

class TestStripFontAwesomeTags(TestCase):
    """Mirror of `stripFontAwesomeTags` in the frontend's legacy-content.ts."""

    def test_string_without_i_tag_is_returned_unchanged(self):
        s = 'no icons here'
        self.assertIs(s, _strip_fontawesome_tags(s))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _strip_fontawesome_tags(''))

    def test_single_quoted_fa_solid_tag_is_stripped(self):
        result = _strip_fontawesome_tags("<i class='fa-solid fa-bell'></i> #106")
        self.assertEqual(' #106', result)

    def test_double_quoted_fa_tag_is_stripped(self):
        result = _strip_fontawesome_tags('<i class="fa-solid fa-virus-covid"></i> [DS] f.zip')
        self.assertEqual(' [DS] f.zip', result)

    def test_tag_inside_markdown_link_text_is_stripped(self):
        md = "[<i class='fa-solid fa-bell'></i> #106](/alerts?alert_ids=106)"
        self.assertEqual('[ #106](/alerts?alert_ids=106)', _strip_fontawesome_tags(md))

    def test_uppercase_tag_is_stripped(self):
        self.assertEqual('', _strip_fontawesome_tags('<I CLASS="FA-SOLID FA-BELL"></I>'))

    def test_whitespace_between_open_and_close_is_tolerated(self):
        self.assertEqual('', _strip_fontawesome_tags("<i class='fa-bell'>  </i>"))

    def test_multiple_tags_all_stripped(self):
        md = "<i class='fa-bell'></i>a<i class='fa-bug'></i>b"
        self.assertEqual('ab', _strip_fontawesome_tags(md))

    def test_non_fontawesome_i_tag_is_left_alone(self):
        md = '<i class="italic"></i>'
        self.assertEqual(md, _strip_fontawesome_tags(md))

    def test_i_tag_with_text_content_is_left_alone(self):
        # Only self-empty icon tags are stripped — a tag with real content
        # would lose that content.
        md = "<i class='fa-bell'>text</i>"
        self.assertEqual(md, _strip_fontawesome_tags(md))

    def test_extra_attributes_around_class_are_tolerated(self):
        md = '<i id="x" class="fa-solid fa-bell" title="t"></i>done'
        self.assertEqual('done', _strip_fontawesome_tags(md))


# ---------------------------------------------------------------------------
# markdown_to_ydoc_update / ydoc_update_to_markdown round-trips
# ---------------------------------------------------------------------------

class TestRoundTripEmpty(TestCase):

    def test_empty_string_produces_empty_string(self):
        update = markdown_to_ydoc_update('')
        result = ydoc_update_to_markdown(update)
        self.assertEqual('', result)

    def test_none_update_produces_empty_string(self):
        self.assertEqual('', ydoc_update_to_markdown(None))

    def test_empty_bytes_produces_empty_string(self):
        self.assertEqual('', ydoc_update_to_markdown(b''))

    def test_markdown_to_ydoc_returns_bytes(self):
        self.assertIsInstance(markdown_to_ydoc_update('hello'), bytes)

    def test_markdown_to_ydoc_with_none_does_not_crash(self):
        # None is coerced to '' inside the function
        result = markdown_to_ydoc_update(None)
        self.assertIsInstance(result, bytes)


class TestRoundTripHeadings(TestCase):

    def test_h1_round_trips(self):
        md = '# Hello\n'
        self.assertEqual(md, _roundtrip(md))

    def test_h2_round_trips(self):
        md = '## Section\n'
        self.assertEqual(md, _roundtrip(md))

    def test_h3_round_trips(self):
        md = '### Sub-section\n'
        self.assertEqual(md, _roundtrip(md))

    def test_h4_round_trips(self):
        md = '#### Deep\n'
        self.assertEqual(md, _roundtrip(md))

    def test_h5_round_trips(self):
        md = '##### Deeper\n'
        self.assertEqual(md, _roundtrip(md))

    def test_h6_round_trips(self):
        md = '###### Deepest\n'
        self.assertEqual(md, _roundtrip(md))

    def test_heading_followed_by_paragraph(self):
        md = '# Title\n\nSome text.\n'
        self.assertEqual(md, _roundtrip(md))


class TestRoundTripParagraphs(TestCase):

    def test_simple_paragraph_round_trips(self):
        md = 'Hello, world.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_multiple_paragraphs(self):
        md = 'First.\n\nSecond.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_paragraph_with_escaped_asterisk(self):
        # An asterisk in normal prose should survive unchanged
        result = _roundtrip('The * symbol.\n')
        self.assertIn('*', result)


class TestRoundTripBoldItalic(TestCase):

    def test_bold_round_trips(self):
        md = 'This is **bold** text.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_italic_round_trips(self):
        md = 'This is *italic* text.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_bold_and_italic_combined(self):
        md = '**bold** and *italic*.\n'
        result = _roundtrip(md)
        self.assertIn('**bold**', result)
        self.assertIn('*italic*', result)


class TestRoundTripInlineCode(TestCase):

    def test_inline_code_round_trips(self):
        md = 'Use `printf()` here.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_inline_code_with_special_chars_preserved(self):
        md = 'Run `ls -la | grep foo`.\n'
        result = _roundtrip(md)
        self.assertIn('`ls -la | grep foo`', result)


class TestRoundTripFencedCodeBlock(TestCase):

    def test_fenced_code_block_no_lang_round_trips(self):
        md = '```\nsome code\n```\n'
        self.assertEqual(md, _roundtrip(md))

    def test_fenced_code_block_with_language_round_trips(self):
        md = '```python\nprint("hi")\n```\n'
        self.assertEqual(md, _roundtrip(md))

    def test_fenced_code_block_multiline_round_trips(self):
        md = '```bash\necho hello\necho world\n```\n'
        self.assertEqual(md, _roundtrip(md))

    def test_empty_code_block_round_trips(self):
        md = '```\n```\n'
        self.assertEqual(md, _roundtrip(md))


class TestRoundTripUnorderedList(TestCase):

    def test_single_item_unordered_list(self):
        md = '- item\n'
        self.assertEqual(md, _roundtrip(md))

    def test_multi_item_unordered_list(self):
        md = '- alpha\n- beta\n- gamma\n'
        self.assertEqual(md, _roundtrip(md))


class TestRoundTripOrderedList(TestCase):

    def test_single_item_ordered_list(self):
        md = '1. first\n'
        self.assertEqual(md, _roundtrip(md))

    def test_multi_item_ordered_list(self):
        md = '1. one\n2. two\n3. three\n'
        self.assertEqual(md, _roundtrip(md))


class TestRoundTripBlockquote(TestCase):

    def test_simple_blockquote_round_trips(self):
        md = '> quoted text\n'
        self.assertEqual(md, _roundtrip(md))

    def test_multi_line_blockquote_content_preserved(self):
        # Two `> …` lines are parsed as a single paragraph with a softbreak,
        # which the renderer maps to a hardBreak (`  \n`). The exact whitespace
        # differs from the original input, but both lines of text survive.
        md = '> line one\n> line two\n'
        result = _roundtrip(md)
        self.assertIn('line one', result)
        self.assertIn('line two', result)
        self.assertTrue(result.startswith('>'))

    def test_blockquote_with_paragraph_inside(self):
        md = '> A paragraph inside a quote.\n'
        self.assertEqual(md, _roundtrip(md))


class TestRoundTripMixed(TestCase):

    def test_heading_then_list_then_paragraph(self):
        md = '# Heading\n\n- item one\n- item two\n\nSome text.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_code_block_after_paragraph(self):
        md = 'Here is some code:\n\n```python\nx = 1\n```\n'
        self.assertEqual(md, _roundtrip(md))

    def test_update_is_content_stable(self):
        """Same markdown round-trips to the same markdown content.

        Y.Doc update bytes embed random client IDs so raw bytes differ
        per call. What must be stable is the decoded content — that
        the round-trip reproduces the same markdown string.
        """
        md = '# Title\n\nParagraph.\n'
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
        )


# ---------------------------------------------------------------------------
# Mention chips
# ---------------------------------------------------------------------------

class TestRoundTripMentions(TestCase):
    """Mention spans must survive the markdown → Y.Doc → markdown trip.

    Before mention support existed, the span was parsed as literal text
    on the way in (`html: False`) and the `caseMention` / `userMention`
    XmlElement the TipTap binding writes was dropped on the way out —
    silent data loss on every flush.
    """

    def test_alert_chip_alone_round_trips(self):
        chip = build_mention_span('alert', 106, 'Alert #106')
        md = f'{chip}\n'
        self.assertEqual(md, _roundtrip(md))

    def test_alert_chip_in_a_paragraph_round_trips(self):
        chip = build_mention_span('alert', 106, 'Alert #106')
        md = f'Escalated from {chip} this morning.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_note_chip_round_trips(self):
        """The latent data-loss regression: existing kinds were destroyed too."""
        chip = build_mention_span('note', 7, 'Runbook')
        md = f'See {chip} for details.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_user_chip_round_trips_with_at_sign(self):
        chip = build_mention_span('user', 3, 'alice')
        self.assertIn('>@alice<', chip)
        md = f'Assigned to {chip}.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_asset_chip_round_trips(self):
        chip = build_mention_span('asset', 12, 'WKS-042')
        md = f'{chip} was compromised.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_two_chips_in_one_paragraph_round_trip(self):
        first = build_mention_span('alert', 1, 'Alert #1')
        second = build_mention_span('alert', 2, 'Alert #2')
        md = f'Merged {first} {second} into this case.\n'
        self.assertEqual(md, _roundtrip(md))

    def test_chip_mid_sentence_keeps_surrounding_text(self):
        chip = build_mention_span('alert', 42, 'Alert #42')
        result = _roundtrip(f'before {chip} after\n')
        self.assertTrue(result.startswith('before '))
        self.assertTrue(result.rstrip('\n').endswith(' after'))
        self.assertIn(chip, result)

    def test_chip_in_heading_round_trips(self):
        chip = build_mention_span('alert', 9, 'Alert #9')
        md = f'## Escalated {chip}\n'
        self.assertEqual(md, _roundtrip(md))

    def test_chip_in_list_item_round_trips(self):
        chip = build_mention_span('ioc', 5, 'evil.com')
        md = f'- seen in {chip}\n'
        self.assertEqual(md, _roundtrip(md))

    def test_chip_inside_bold_survives(self):
        """The chip is a node, not text, so the bold mark around it is not
        re-emitted — but the chip itself and the surrounding words must
        survive rather than being dropped."""
        chip = build_mention_span('alert', 106, 'Alert #106')
        result = _roundtrip(f'**urgent {chip}** now\n')
        self.assertIn(chip, result)
        self.assertIn('urgent', result)
        self.assertIn('now', result)

    def test_chip_inside_link_survives(self):
        chip = build_mention_span('note', 7, 'Runbook')
        result = _roundtrip(f'[see {chip} here](/case/1/notes/7)\n')
        self.assertIn(chip, result)
        self.assertIn('see', result)
        self.assertIn('here', result)

    def test_chip_label_with_html_special_chars_round_trips(self):
        # The label is HTML-escaped inside the attribute; parsing must
        # unescape it so re-emitting doesn't double-escape.
        chip = build_mention_span('asset', 4, 'R&D "box" <1>')
        self.assertIn('&amp;', chip)
        md = f'{chip}\n'
        self.assertEqual(md, _roundtrip(md))

    def test_chip_id_cannot_break_out_of_its_attribute(self):
        # Escalation passes an int, but the flush path re-emits whatever a
        # connected client put in the Y.Doc's `id` attribute, so the id is
        # escaped exactly like the label — and unescaped on the way back
        # in, or the round trip would drift on every flush.
        chip = build_mention_span('asset', '4" onload="x', 'WKS')
        self.assertNotIn('onload="x', chip)
        self.assertIn('&quot;', chip)
        md = f'{chip}\n'
        self.assertEqual(md, _roundtrip(md))

    def test_chip_is_stable_across_two_round_trips(self):
        chip = build_mention_span('alert', 106, 'Alert #106')
        once = _roundtrip(f'Alert: {chip}\n')
        self.assertEqual(once, _roundtrip(once))

    def test_chip_survives_a_legacy_case_url_rewrite_in_the_same_text(self):
        chip = build_mention_span('alert', 3, 'Alert #3')
        md = f'{chip} and [case](/case?cid=8)\n'
        result = _roundtrip(md)
        self.assertIn(chip, result)
        self.assertIn('/case/8', result)

    # --- degradation ---

    def test_span_without_data_id_degrades_to_text(self):
        # No `data-id` → not a mention. It must not raise and must not
        # silently swallow the body.
        md = '<span data-mention data-kind="alert">no id</span>\n'
        result = _roundtrip(md)
        self.assertIn('no id', result)

    def test_unknown_kind_does_not_raise_and_is_not_a_user_mention(self):
        md = '<span data-mention data-kind="wibble" data-id="3" data-label="X">#X</span>\n'
        result = _roundtrip(md)
        self.assertIn('data-id="3"', result)
        self.assertIn('X', result)
        self.assertNotIn('data-kind="user"', result)

    def test_unclosed_span_degrades_to_text(self):
        md = '<span data-mention data-kind="alert" data-id="1" data-label="A">#A\n'
        result = _roundtrip(md)
        self.assertIn('#A', result)

    def test_lone_less_than_is_untouched(self):
        result = _roundtrip('a < b\n')
        self.assertIn('<', result)

    # --- legacy FontAwesome cleanup in the full pipeline ---

    def test_legacy_fontawesome_alert_link_is_stripped_not_shown_as_text(self):
        md = "### IRIS alert link\n\n[<i class='fa-solid fa-bell'></i> #106](/alerts?alert_ids=106)\n"
        result = _roundtrip(md)
        self.assertNotIn('fa-solid', result)
        self.assertNotIn('<i ', result)
        self.assertNotIn(r'\<i', result)
        self.assertIn('106', result)
        self.assertIn('/alerts?alert_ids=106', result)


# ---------------------------------------------------------------------------
# append_markdown_to_ydoc_update
# ---------------------------------------------------------------------------

class TestAppendMarkdownToYdocUpdate(TestCase):
    """Server-side append into an already-seeded Y.Doc.

    Once a `collab_doc` row exists the Y.Doc is authoritative and the
    source column is ignored on re-open, so a backend feature that only
    appends to the column (alert merge writing its mention chip into
    `cases.description`) loses the append on the next flush. This
    primitive is how that append reaches the CRDT instead.
    """

    # --- empty / absent starting state ---

    def test_append_to_none_state_matches_a_fresh_build(self):
        md = '# Heading\n\nA paragraph.\n'
        # Bytes can't be compared — Yjs stamps a random client id into
        # every update — so the contract is content equivalence.
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(None, md)),
        )

    def test_append_to_empty_bytes_matches_a_fresh_build(self):
        md = '- one\n- two\n'
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(b'', md)),
        )

    def test_append_to_the_update_of_an_empty_doc_matches_a_fresh_build(self):
        # `ensure_snapshot` stores the update of an EMPTY doc when the
        # source column is blank; appending to that must not differ from
        # seeding the same markdown from scratch.
        md = 'Just text.\n'
        empty_state = markdown_to_ydoc_update('')
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(empty_state, md)),
        )

    def test_appending_empty_markdown_leaves_the_document_untouched(self):
        base = '# Title\n\nBody.\n'
        self.assertEqual(base, _append(base, ''))

    # --- ordering and preservation ---

    def test_original_blocks_are_preserved(self):
        base = '# Case summary\n\nInitial findings.\n'
        result = _append(base, 'Appended line.')
        self.assertIn('# Case summary', result)
        self.assertIn('Initial findings.', result)

    def test_appended_blocks_come_last(self):
        result = _append('# Case summary\n\nInitial findings.\n', 'Appended line.')
        self.assertTrue(result.startswith('# Case summary'))
        self.assertEqual('Appended line.', result.rstrip('\n').split('\n')[-1])

    def test_append_result_equals_the_concatenated_markdown(self):
        base = '# Title\n\nFirst.\n'
        added = 'Second.'
        self.assertEqual(_roundtrip(base + '\n' + added + '\n'),
                         _append(base, added))

    def test_multi_block_append_keeps_its_internal_order(self):
        result = _append('Intro.\n', '## Section\n\n- a\n- b\n')
        lines = [line for line in result.split('\n') if line]
        self.assertEqual(['Intro.', '## Section', '- a', '- b'], lines)

    def test_append_into_a_document_with_a_table_keeps_the_table(self):
        base = '| A | B |\n| --- | --- |\n| 1 | 2 |\n'
        result = _append(base, 'After the table.')
        self.assertIn('| A | B |', result)
        self.assertIn('| 1 | 2 |', result)
        self.assertEqual('After the table.', result.rstrip('\n').split('\n')[-1])

    # --- mention chips ---

    def test_appended_alert_chip_survives_the_append_and_the_render(self):
        """The exact shape `merge_alert_in_case` writes on escalation."""
        chip = build_mention_span('alert', 106, 'Alert #106')
        result = _append('# Case summary\n\nInitial findings.\n',
                         f'{chip} *escalated by alice*')
        self.assertIn(chip, result)
        self.assertIn('*escalated by alice*', result)
        self.assertIn('Initial findings.', result)

    def test_a_chip_already_in_the_document_is_not_disturbed_by_an_append(self):
        first = build_mention_span('alert', 1, 'Alert #1')
        second = build_mention_span('alert', 2, 'Alert #2')
        result = _append(f'{first}\n', second)
        self.assertIn(first, result)
        self.assertIn(second, result)
        self.assertLess(result.index(first), result.index(second))

    def test_appended_chip_survives_a_second_full_round_trip(self):
        # The flush path renders the Y.Doc to markdown and re-seeds from
        # it later; the chip must be stable across that.
        chip = build_mention_span('alert', 42, 'Alert #42')
        once = _append('Base.\n', f'{chip} *escalated by bob*')
        self.assertEqual(once, _roundtrip(once))

    # --- repeated appends (the batch-merge shape) ---

    def test_appending_twice_produces_both_fragments_in_order(self):
        first = build_mention_span('alert', 1, 'Alert #1')
        second = build_mention_span('alert', 2, 'Alert #2')
        state = markdown_to_ydoc_update('# Case summary\n')
        state = append_markdown_to_ydoc_update(state, f'{first} *escalated by alice*')
        state = append_markdown_to_ydoc_update(state, f'{second} *escalated by bob*')
        result = ydoc_update_to_markdown(state)
        self.assertIn('# Case summary', result)
        self.assertIn(first, result)
        self.assertIn(second, result)
        self.assertLess(result.index(first), result.index(second))

    def test_appending_twice_is_content_stable(self):
        state = markdown_to_ydoc_update('Base.\n')
        state = append_markdown_to_ydoc_update(state, 'One.')
        state = append_markdown_to_ydoc_update(state, 'Two.')
        result = ydoc_update_to_markdown(state)
        self.assertEqual('Base.\n\nOne.\n\nTwo.\n', result)
        # Re-seeding from the rendered markdown yields the same document.
        self.assertEqual(result, _roundtrip(result))

    def test_repeated_appends_of_the_same_markdown_accumulate(self):
        state = markdown_to_ydoc_update('')
        for _ in range(3):
            state = append_markdown_to_ydoc_update(state, 'line')
        self.assertEqual('line\n\nline\n\nline\n', ydoc_update_to_markdown(state))

    # --- returned value shape ---

    def test_the_return_value_is_a_full_state_not_a_delta(self):
        # `collab_doc.y_state` holds a full state, so the return value has
        # to render standalone — applying it to a fresh Doc must yield the
        # whole document, original blocks included.
        state = append_markdown_to_ydoc_update(
            markdown_to_ydoc_update('# Original\n'), 'Added.',
        )
        self.assertIsInstance(state, bytes)
        self.assertIn('# Original', ydoc_update_to_markdown(state))

    # --- the cleaning pre-passes must run on appended markdown too ---

    def test_legacy_case_urls_in_appended_markdown_are_rewritten(self):
        result = _append('Base.\n', 'see [the case](/case?cid=8)')
        self.assertIn('/case/8', result)
        self.assertNotIn('cid=8', result)

    def test_fontawesome_tags_in_appended_markdown_are_stripped(self):
        result = _append('Base.\n', "[<i class='fa-solid fa-bell'></i> #106](/alerts?alert_ids=106)")
        self.assertNotIn('fa-solid', result)
        self.assertIn('/alerts?alert_ids=106', result)

    def test_a_flattened_pipe_table_in_appended_markdown_is_unflattened(self):
        result = _append('Base.\n', '| A | B | |---|---| | 1 | 2 |')
        self.assertIn('| A | B |', result)
        self.assertIn('| 1 | 2 |', result)
