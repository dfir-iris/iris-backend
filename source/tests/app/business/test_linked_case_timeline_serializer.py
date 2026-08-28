#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _serialize_case_event in war_room_linked_case_timelines.py.

The function does three DB sub-queries for asset IDs, IOC IDs and children
count. Those are patched out so the field-mapping logic is testable purely.
"""

import datetime
import uuid
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.business.war_room_linked_case_timelines import _serialize_case_event


def _make_event(**kwargs):
    defaults = {
        'case_id': 10,
        'event_id': 99,
        'event_uuid': uuid.UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
        'event_title': 'Initial compromise',
        'event_content': 'Attacker logged in via VPN',
        'event_raw': 'raw log line',
        'event_source': 'SIEM',
        'event_tags': 'lateral,movement',
        'event_is_flagged': True,
        'event_date': datetime.datetime(2026, 3, 1, 12, 0, 0),
        'event_tz': 'UTC',
        'event_color': '#ff0000',
        'modification_history': {},
        'event_added': datetime.datetime(2026, 3, 1, 10, 0, 0),
        'user_id': 5,
        'parent_event_id': None,
    }
    defaults.update(kwargs)
    event = MagicMock()
    for key, value in defaults.items():
        setattr(event, key, value)
    return event


def _serialize(event, source_timeline_id=1, asset_ids=None, ioc_ids=None, children_count=0):
    """Helper: call _serialize_case_event with patched DB lookups."""
    if asset_ids is None:
        asset_ids = []
    if ioc_ids is None:
        ioc_ids = []

    with patch('app.business.war_room_linked_case_timelines.CasesEvent') as mock_ce, \
         patch('app.business.war_room_linked_case_timelines.CaseEventsAssets', create=True) as _mca, \
         patch('app.business.war_room_linked_case_timelines.CaseEventsIoc', create=True) as _mci:

        # Patch inside the lazy import block — _serialize_case_event does
        # `from app.models.models import CaseEventsAssets` locally.
        with patch.dict('sys.modules', {}):
            import sys

            # Build tiny stubs the function can `.query.filter_by(...).with_entities(...).all()` on
            assets_stub = MagicMock()
            assets_stub.query.filter_by.return_value.with_entities.return_value.all.return_value = [
                MagicMock(asset_id=aid) for aid in asset_ids
            ]
            iocs_stub = MagicMock()
            iocs_stub.query.filter_by.return_value.with_entities.return_value.all.return_value = [
                MagicMock(ioc_id=iid) for iid in ioc_ids
            ]
            mock_ce.query.filter.return_value.count.return_value = children_count

            sys.modules['app.models.models'] = MagicMock(
                CaseEventsAssets=assets_stub,
                CaseEventsIoc=iocs_stub,
            )
            try:
                return _serialize_case_event(event, source_timeline_id)
            finally:
                sys.modules.pop('app.models.models', None)


class TestSerializeCaseEvent(TestCase):

    def setUp(self):
        self.event = _make_event()

    def _call(self, event=None, timeline_id=1, asset_ids=None, ioc_ids=None, children_count=0):
        return _serialize(event or self.event, timeline_id, asset_ids, ioc_ids, children_count)

    def test_id_is_synthetic_string(self):
        result = self._call()
        self.assertEqual('case:10:99', result['id'])

    def test_war_room_source_is_case(self):
        result = self._call()
        self.assertEqual('case', result['war_room_source'])

    def test_uuid_is_string(self):
        result = self._call()
        self.assertIsInstance(result['uuid'], str)
        self.assertEqual('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', result['uuid'])

    def test_uuid_is_empty_string_when_none(self):
        event = _make_event(event_uuid=None)
        result = self._call(event=event)
        self.assertEqual('', result['uuid'])

    def test_timeline_id_set(self):
        result = self._call(timeline_id=7)
        self.assertEqual(7, result['timeline_id'])

    def test_parent_id_is_none(self):
        result = self._call()
        self.assertIsNone(result['parent_id'])

    def test_category_is_none(self):
        result = self._call()
        self.assertIsNone(result['category'])

    def test_is_flagged_coerced_to_bool(self):
        event = _make_event(event_is_flagged=1)
        result = self._call(event=event)
        self.assertIs(True, result['is_flagged'])

    def test_is_flagged_false(self):
        event = _make_event(event_is_flagged=0)
        result = self._call(event=event)
        self.assertIs(False, result['is_flagged'])

    def test_event_date_iso_format(self):
        result = self._call()
        self.assertIn('2026-03-01', result['event_date'])

    def test_event_date_none_when_not_set(self):
        event = _make_event(event_date=None)
        result = self._call(event=event)
        self.assertIsNone(result['event_date'])

    def test_created_at_iso_format(self):
        result = self._call()
        self.assertIn('2026-03-01', result['created_at'])

    def test_created_at_none_when_not_set(self):
        event = _make_event(event_added=None)
        result = self._call(event=event)
        self.assertIsNone(result['created_at'])

    def test_assets_list_populated(self):
        result = self._call(asset_ids=[1, 2, 3])
        self.assertEqual([1, 2, 3], result['assets'])

    def test_iocs_list_populated(self):
        result = self._call(ioc_ids=[10, 20])
        self.assertEqual([10, 20], result['iocs'])

    def test_children_count_set(self):
        result = self._call(children_count=5)
        self.assertEqual(5, result['children_count'])

    def test_case_id_and_event_id_fields(self):
        result = self._call()
        self.assertEqual(10, result['case_id'])
        self.assertEqual(99, result['event_id'])

    def test_all_expected_keys_present(self):
        result = self._call()
        for key in ('id', 'war_room_source', 'uuid', 'timeline_id', 'parent_id', 'case_id',
                    'event_id', 'title', 'content', 'raw', 'source', 'tags', 'is_flagged',
                    'event_date', 'event_tz', 'color', 'category', 'modification_history',
                    'assets', 'iocs', 'children_count', 'created_at', 'created_by_id'):
            with self.subTest(key=key):
                self.assertIn(key, result)
