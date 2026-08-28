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

from app.iris_engine.collab.render import (
    _escape_markdown,
    _rewrite_legacy_case_urls,
    _unflatten_pipe_tables,
    markdown_to_ydoc_update,
    ydoc_update_to_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(md: str) -> str:
    """Convenience: md → ydoc update → md."""
    return ydoc_update_to_markdown(markdown_to_ydoc_update(md))


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
