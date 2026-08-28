#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure rendering helpers in business/war_room_sitreps.py."""

import datetime
from unittest import TestCase
from unittest.mock import MagicMock

from app.business.war_room_sitreps import sitrep_as_html, sitrep_as_markdown


def _sitrep(title='Incident Alpha', war_room_id=1, version=1,
            authored_at=None, published=True, body_md='Body text.',
            snapshot_json=None):
    sit = MagicMock()
    sit.title = title
    sit.war_room_id = war_room_id
    sit.version = version
    sit.authored_at = authored_at
    sit.published = published
    sit.body_md = body_md
    sit.snapshot_json = snapshot_json
    return sit


class TestSitrepAsMarkdown(TestCase):

    def test_title_in_output(self):
        result = sitrep_as_markdown(_sitrep(title='My SitRep'))
        self.assertIn('# My SitRep', result)

    def test_war_room_id_in_output(self):
        result = sitrep_as_markdown(_sitrep(war_room_id=42))
        self.assertIn('#42', result)

    def test_version_in_output(self):
        result = sitrep_as_markdown(_sitrep(version=3))
        self.assertIn('version 3', result)

    def test_published_status(self):
        result = sitrep_as_markdown(_sitrep(published=True))
        self.assertIn('Published', result)

    def test_draft_status(self):
        result = sitrep_as_markdown(_sitrep(published=False))
        self.assertIn('Draft', result)

    def test_body_md_in_output(self):
        result = sitrep_as_markdown(_sitrep(body_md='## Analysis\nSome text.'))
        self.assertIn('## Analysis', result)

    def test_none_body_md_uses_placeholder(self):
        result = sitrep_as_markdown(_sitrep(body_md=None))
        self.assertIn('_(no content)_', result)

    def test_authored_at_included_when_set(self):
        ts = datetime.datetime(2026, 1, 15, 10, 30, 0)
        result = sitrep_as_markdown(_sitrep(authored_at=ts))
        self.assertIn('2026-01-15', result)

    def test_authored_at_absent_when_none(self):
        result = sitrep_as_markdown(_sitrep(authored_at=None))
        self.assertNotIn('Authored at', result)

    def test_snapshot_included_when_present(self):
        snap = {'attached_case_ids': [1, 2], 'tasks_open': 3, 'tasks_closed': 5}
        result = sitrep_as_markdown(_sitrep(snapshot_json=snap))
        self.assertIn('Snapshot at publish', result)
        self.assertIn('1, 2', result)
        self.assertIn('Open tasks: 3', result)
        self.assertIn('Closed tasks: 5', result)

    def test_snapshot_empty_case_ids_shows_dash(self):
        snap = {'attached_case_ids': []}
        result = sitrep_as_markdown(_sitrep(snapshot_json=snap))
        self.assertIn('—', result)

    def test_no_snapshot_section_when_absent(self):
        result = sitrep_as_markdown(_sitrep(snapshot_json=None))
        self.assertNotIn('Snapshot at publish', result)

    def test_returns_string(self):
        self.assertIsInstance(sitrep_as_markdown(_sitrep()), str)


class TestSitrepAsHtml(TestCase):

    def test_returns_html_string(self):
        result = sitrep_as_html(_sitrep())
        self.assertIn('<!doctype html>', result)

    def test_title_in_html_head(self):
        result = sitrep_as_html(_sitrep(title='My Report'))
        self.assertIn('<title>My Report</title>', result)

    def test_body_tag_present(self):
        result = sitrep_as_html(_sitrep())
        self.assertIn('<body>', result)
        self.assertIn('</body>', result)

    def test_content_included(self):
        result = sitrep_as_html(_sitrep(body_md='Unique-content-here'))
        self.assertIn('Unique-content-here', result)
