#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _read_row and make_ref in datamgmt/case_transfer/transfer_db.py.

Both functions are pure — no DB, no Flask context needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock

from app.datamgmt.case_transfer.transfer_db import BundleCollector, make_ref, _read_row
from app.datamgmt.case_transfer.transfer_spec import EntitySpec, USER_REF_PREFIX


def _spec(**kwargs):
    defaults = dict(
        key='note', model=object, pk='note_id', case_column='case_id',
        fields=(), user_refs=(), entity_refs=None, lookup_refs=None,
        self_refs=(), blob_column=None,
    )
    defaults.update(kwargs)
    return EntitySpec(**defaults)


def _row(**attrs):
    row = MagicMock()
    for k, v in attrs.items():
        setattr(row, k, v)
    return row


class TestMakeRef(TestCase):

    def test_format_is_kind_colon_id(self):
        self.assertEqual('note:42', make_ref('note', 42))

    def test_string_id(self):
        self.assertEqual('user:abc', make_ref('user', 'abc'))

    def test_zero_id(self):
        self.assertEqual('asset:0', make_ref('asset', 0))


class TestReadRow(TestCase):

    def test_ref_uses_pk(self):
        spec = _spec(pk='note_id')
        row = _row(note_id=7)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('note:7', result['_ref'])

    def test_no_pk_no_ref_key(self):
        spec = _spec(pk=None)
        row = _row()
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertNotIn('_ref', result)

    def test_scalar_fields_copied(self):
        spec = _spec(fields=('title', 'body'), pk=None)
        row = _row(title='Hello', body='World')
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('Hello', result['title'])
        self.assertEqual('World', result['body'])

    def test_user_ref_rewritten(self):
        spec = _spec(user_refs=('created_by',), pk=None)
        row = _row(created_by=5)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual(f'{USER_REF_PREFIX}:5', result['created_by'])
        self.assertIn(5, collector.principal_ids)

    def test_user_ref_none_gives_none(self):
        spec = _spec(user_refs=('owner_id',), pk=None)
        row = _row(owner_id=None)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['owner_id'])
        self.assertEqual(set(), collector.principal_ids)

    def test_entity_ref_rewritten(self):
        spec = _spec(entity_refs={'task_id': 'task'}, pk=None)
        row = _row(task_id=3)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('task:3', result['task_id'])

    def test_entity_ref_none_gives_none(self):
        spec = _spec(entity_refs={'task_id': 'task'}, pk=None)
        row = _row(task_id=None)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['task_id'])

    def test_lookup_ref_rewritten(self):
        spec = _spec(lookup_refs={'severity_id': 'severity'}, pk=None)
        row = _row(severity_id=2)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('severity:2', result['severity_id'])
        self.assertIn(2, collector.lookup_ids['severity'])

    def test_lookup_ref_none_gives_none(self):
        spec = _spec(lookup_refs={'severity_id': 'severity'}, pk=None)
        row = _row(severity_id=None)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['severity_id'])

    def test_self_ref_rewritten(self):
        spec = _spec(key='folder', self_refs=('parent_id',), pk=None)
        row = _row(parent_id=10)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('folder:10', result['parent_id'])

    def test_self_ref_none_gives_none(self):
        spec = _spec(key='folder', self_refs=('parent_id',), pk=None)
        row = _row(parent_id=None)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['parent_id'])

    def test_self_ref_zero_sentinel_gives_none(self):
        # 0 is the datastore root sentinel for "no parent"
        spec = _spec(key='path', self_refs=('parent_id',), pk=None)
        row = _row(parent_id=0)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['parent_id'])

    def test_blob_column_adds_blob_key(self):
        spec = _spec(blob_column='file_uuid', pk=None)
        row = _row(file_uuid='abc-123')
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertEqual('abc-123', result['_blob'])

    def test_blob_column_none_gives_none(self):
        spec = _spec(blob_column='file_uuid', pk=None)
        row = _row(file_uuid=None)
        collector = BundleCollector()
        result = _read_row(row, spec, collector)
        self.assertIsNone(result['_blob'])
