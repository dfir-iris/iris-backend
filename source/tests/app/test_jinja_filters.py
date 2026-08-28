#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for Jinja filter functions.

All helpers tested here are pure Python — no DB, no request context.
The XSS sanitisation test is the most security-critical: it exercises
the bleach-based _sanitize_attribute_html filter which replaced a raw
`| safe` sink (SBA-ADV-20260126-03 / CWE-79).
"""

from unittest import TestCase

from app.jinja_filters import (
    _escape_dots,
    _sanitize_attribute_html,
    _to_json_indent,
    _to_json_safe,
    _unquote,
)


class TestUnquote(TestCase):

    def test_percent_encoded_slash(self):
        self.assertEqual('/test/path', _unquote('%2Ftest%2Fpath'))

    def test_percent_encoded_space(self):
        self.assertEqual('hello world', _unquote('hello%20world'))

    def test_plus_sign_is_not_decoded_as_space(self):
        # urllib.parse.unquote (not unquote_plus) — '+' stays literal
        self.assertEqual('a+b', _unquote('a+b'))

    def test_plain_string_unchanged(self):
        self.assertEqual('plain', _unquote('plain'))

    def test_double_encoded_slash(self):
        # One level of decoding only
        self.assertEqual('%2F', _unquote('%252F'))

    def test_empty_string(self):
        self.assertEqual('', _unquote(''))


class TestEscapeDots(TestCase):

    def test_ipv4_address(self):
        self.assertEqual('1[.]2[.]3[.]4', _escape_dots('1.2.3.4'))

    def test_domain_name(self):
        self.assertEqual('example[.]com', _escape_dots('example.com'))

    def test_no_dots(self):
        self.assertEqual('hello', _escape_dots('hello'))

    def test_multiple_consecutive_dots(self):
        self.assertEqual('a[.][.]b', _escape_dots('a..b'))

    def test_empty_string(self):
        self.assertEqual('', _escape_dots(''))


class TestToJsonSafe(TestCase):

    def test_dict_serialised(self):
        result = _to_json_safe({'key': 'value'})
        self.assertIn('"key"', result)
        self.assertIn('"value"', result)

    def test_non_ascii_not_escaped(self):
        # ensure_ascii=False — unicode stays as unicode
        result = _to_json_safe({'msg': 'héllo'})
        self.assertIn('héllo', result)

    def test_indented_4_spaces(self):
        result = _to_json_safe({'a': 1})
        self.assertIn('    ', result)

    def test_list_serialised(self):
        result = _to_json_safe([1, 2, 3])
        self.assertIn('[', result)


class TestToJsonIndent(TestCase):

    def test_dict_serialised(self):
        result = _to_json_indent({'k': 'v'})
        self.assertIn('"k"', result)

    def test_non_ascii_is_escaped(self):
        # Default ensure_ascii=True for _to_json_indent
        result = _to_json_indent({'msg': 'héllo'})
        self.assertNotIn('héllo', result)
        self.assertIn('\\u', result)

    def test_indented_4_spaces(self):
        result = _to_json_indent({'a': 1})
        self.assertIn('    ', result)


class TestSanitizeAttributeHtml(TestCase):
    """Covers the XSS sanitisation filter (CWE-79 fix)."""

    def test_none_returns_empty_string(self):
        self.assertEqual('', _sanitize_attribute_html(None))

    def test_plain_text_unchanged(self):
        result = _sanitize_attribute_html('hello world')
        self.assertEqual('hello world', result)

    def test_script_tag_stripped(self):
        result = _sanitize_attribute_html('<script>alert(1)</script>after')
        self.assertNotIn('<script>', str(result))
        self.assertIn('after', str(result))

    def test_script_tag_stripped_but_text_content_exposed(self):
        # bleach strip=True removes the <script> wrapper but bleach does NOT
        # suppress the text content between stripped tags — "alert(1)" is
        # inert text, not executable JS, because there is no surrounding tag.
        result = _sanitize_attribute_html('<script>alert(1)</script>')
        self.assertNotIn('<script>', str(result))
        # The text "alert(1)" is kept as inert text; this is bleach's documented
        # behaviour with strip=True (strip=False would HTML-escape the tag instead).
        self.assertIn('alert(1)', str(result))

    def test_inline_event_handler_stripped(self):
        result = _sanitize_attribute_html('<img onerror="alert(1)" src="x">')
        self.assertNotIn('onerror', str(result))

    def test_javascript_href_stripped(self):
        result = _sanitize_attribute_html('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn('javascript:', str(result))
        # The link text is preserved, but the href is removed or sanitised
        self.assertIn('click', str(result))

    def test_safe_anchor_preserved(self):
        result = _sanitize_attribute_html('<a href="https://example.com" rel="noopener">link</a>')
        self.assertIn('href', str(result))
        self.assertIn('example.com', str(result))

    def test_safe_img_src_preserved(self):
        result = _sanitize_attribute_html('<img src="https://cdn.example.com/img.png" alt="logo">')
        self.assertIn('src', str(result))

    def test_h1_through_h6_preserved(self):
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            with self.subTest(tag=tag):
                result = _sanitize_attribute_html(f'<{tag}>Title</{tag}>')
                self.assertIn(tag, str(result))

    def test_iframe_stripped(self):
        result = _sanitize_attribute_html('<iframe src="https://evil.com"></iframe>')
        self.assertNotIn('iframe', str(result))

    def test_code_block_preserved(self):
        result = _sanitize_attribute_html('<code>print("hello")</code>')
        self.assertIn('<code>', str(result))

    def test_data_uri_stripped(self):
        result = _sanitize_attribute_html('<img src="data:image/png;base64,abc">')
        # data: is not in the allowed protocols list
        self.assertNotIn('data:', str(result))

    def test_numeric_input_coerced_to_string(self):
        result = _sanitize_attribute_html(42)
        self.assertEqual('42', str(result))

    def test_returns_markup_type(self):
        from markupsafe import Markup
        result = _sanitize_attribute_html('<b>bold</b>')
        self.assertIsInstance(result, Markup)
