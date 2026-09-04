#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the report filename builder (VI-009).

Pure helpers — no Flask context, no DB. The traversal payloads mirror the
ones the finding used against `os.path.join(tmp_dir, naming_format)`.
"""

import os
import tempfile
from unittest import TestCase

from app.business.reports.naming import build_report_filename
from app.business.reports.naming import naming_format_error
from app.business.reports.naming import resolve_output_path
from app.models.errors import BusinessProcessingError


_TAGS = {
    '%code_name%': '260904_1200',
    '%customer%': 'ACME',
    '%case_name%': 'Case One',
    '%date%': '2026-09-04',
}


class TestNamingFormatError(TestCase):

    def test_plain_format_is_accepted(self):
        self.assertIsNone(naming_format_error('report_%case_name%_%date%'))

    def test_empty_format_is_accepted(self):
        self.assertIsNone(naming_format_error(''))
        self.assertIsNone(naming_format_error(None))

    def test_forward_slash_is_rejected(self):
        self.assertIsNotNone(naming_format_error('../iris-render-poc'))

    def test_backslash_is_rejected(self):
        self.assertIsNotNone(naming_format_error('..\\iris-render-poc'))

    def test_control_character_is_rejected(self):
        self.assertIsNotNone(naming_format_error('report\x00.md'))


class TestBuildReportFilename(TestCase):

    def test_tags_are_substituted(self):
        name = build_report_filename('%customer%_%case_name%', '.md', _TAGS)
        self.assertEqual('ACME_Case One.md', name)

    def test_traversal_in_format_is_flattened(self):
        name = build_report_filename('../iris-render-poc', '.md', _TAGS)
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)

    def test_traversal_in_substituted_case_name_is_flattened(self):
        tags = {**_TAGS, '%case_name%': '../../etc/cron.d/pwn'}
        name = build_report_filename('%case_name%', '.md', tags)
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)

    def test_absolute_path_in_format_is_flattened(self):
        name = build_report_filename('/etc/cron.d/pwn', '.docx', _TAGS)
        self.assertEqual('etc_cron.d_pwn.docx', name)

    def test_empty_format_falls_back_to_a_stem(self):
        self.assertEqual('report.docx', build_report_filename('', '.docx', _TAGS))

    def test_format_of_only_dots_falls_back_to_a_stem(self):
        self.assertEqual('report.md', build_report_filename('..', '.md', _TAGS))

    def test_missing_substitution_value_does_not_crash(self):
        tags = {**_TAGS, '%customer%': None}
        self.assertEqual('.md', build_report_filename('%customer%', '.md', tags)[-3:])


class TestResolveOutputPath(TestCase):

    def test_plain_filename_resolves_inside_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            resolved = resolve_output_path(tmp_dir, 'report.md')
            self.assertEqual(os.path.realpath(os.path.join(tmp_dir, 'report.md')), resolved)

    def test_escaping_filename_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(BusinessProcessingError):
                resolve_output_path(tmp_dir, '../iris-render-poc.md')
