#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the declarative case-transfer spec structures.

LookupSpec, EntitySpec, LOOKUPS, ENTITIES, and ENTITIES_BY_KEY are all
pure Python — no DB, no Flask context required.
"""

from unittest import TestCase

from app.datamgmt.case_transfer.transfer_spec import (
    ENTITIES,
    ENTITIES_BY_KEY,
    LOOKUPS,
    EntitySpec,
    LookupSpec,
    USER_REF_PREFIX,
)


class TestLookupSpec(TestCase):

    def test_key_stored(self):
        spec = LookupSpec('mykey', object, 'id', 'name')
        self.assertEqual('mykey', spec.key)

    def test_model_stored(self):
        sentinel = object()
        spec = LookupSpec('k', sentinel, 'id', 'name')
        self.assertIs(sentinel, spec.model)

    def test_pk_stored(self):
        spec = LookupSpec('k', object, 'my_pk', 'name')
        self.assertEqual('my_pk', spec.pk)

    def test_name_column_stored(self):
        spec = LookupSpec('k', object, 'pk', 'label')
        self.assertEqual('label', spec.name_column)

    def test_extra_columns_empty_by_default(self):
        spec = LookupSpec('k', object, 'pk', 'name')
        self.assertEqual((), spec.extra_columns)

    def test_extra_columns_stored_as_tuple(self):
        spec = LookupSpec('k', object, 'pk', 'name', extra_columns=['a', 'b'])
        self.assertEqual(('a', 'b'), spec.extra_columns)

    def test_creatable_defaults_to_true(self):
        spec = LookupSpec('k', object, 'pk', 'name')
        self.assertTrue(spec.creatable)

    def test_creatable_false_is_stored(self):
        spec = LookupSpec('k', object, 'pk', 'name', creatable=False)
        self.assertFalse(spec.creatable)


class TestEntitySpec(TestCase):

    def test_key_stored(self):
        spec = EntitySpec('ioc', object, 'ioc_id')
        self.assertEqual('ioc', spec.key)

    def test_model_stored(self):
        sentinel = object()
        spec = EntitySpec('x', sentinel, 'pk')
        self.assertIs(sentinel, spec.model)

    def test_pk_stored(self):
        spec = EntitySpec('x', object, 'my_pk')
        self.assertEqual('my_pk', spec.pk)

    def test_fields_empty_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertEqual((), spec.fields)

    def test_fields_stored_as_tuple(self):
        spec = EntitySpec('x', object, 'pk', fields=['a', 'b'])
        self.assertEqual(('a', 'b'), spec.fields)

    def test_user_refs_empty_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertEqual((), spec.user_refs)

    def test_user_refs_stored_as_tuple(self):
        spec = EntitySpec('x', object, 'pk', user_refs=['user_id'])
        self.assertEqual(('user_id',), spec.user_refs)

    def test_entity_refs_empty_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertEqual({}, spec.entity_refs)

    def test_entity_refs_stored_as_dict(self):
        spec = EntitySpec('x', object, 'pk', entity_refs={'note_id': 'note'})
        self.assertEqual({'note_id': 'note'}, spec.entity_refs)

    def test_lookup_refs_empty_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertEqual({}, spec.lookup_refs)

    def test_lookup_refs_stored_as_dict(self):
        spec = EntitySpec('x', object, 'pk', lookup_refs={'status_id': 'task_status'})
        self.assertEqual({'status_id': 'task_status'}, spec.lookup_refs)

    def test_self_refs_empty_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertEqual((), spec.self_refs)

    def test_self_refs_stored_as_tuple(self):
        spec = EntitySpec('x', object, 'pk', self_refs=['parent_id'])
        self.assertEqual(('parent_id',), spec.self_refs)

    def test_blob_column_none_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertIsNone(spec.blob_column)

    def test_blob_column_stored(self):
        spec = EntitySpec('x', object, 'pk', blob_column='file_uuid')
        self.assertEqual('file_uuid', spec.blob_column)

    def test_case_column_none_by_default(self):
        spec = EntitySpec('x', object, 'pk')
        self.assertIsNone(spec.case_column)

    def test_case_column_stored(self):
        spec = EntitySpec('x', object, 'pk', case_column='case_id')
        self.assertEqual('case_id', spec.case_column)


class TestLookupRegistry(TestCase):

    def test_lookups_is_dict(self):
        self.assertIsInstance(LOOKUPS, dict)

    def test_customer_lookup_present(self):
        self.assertIn('customer', LOOKUPS)

    def test_tag_lookup_present(self):
        self.assertIn('tag', LOOKUPS)

    def test_ioc_type_lookup_present(self):
        self.assertIn('ioc_type', LOOKUPS)

    def test_all_values_are_lookup_spec_instances(self):
        for key, spec in LOOKUPS.items():
            with self.subTest(key=key):
                self.assertIsInstance(spec, LookupSpec)

    def test_keys_match_spec_keys(self):
        for key, spec in LOOKUPS.items():
            with self.subTest(key=key):
                self.assertEqual(key, spec.key)

    def test_non_creatable_specs_are_present(self):
        non_creatable = [k for k, v in LOOKUPS.items() if not v.creatable]
        self.assertGreater(len(non_creatable), 0)


class TestEntityRegistry(TestCase):

    def test_entities_is_list(self):
        self.assertIsInstance(ENTITIES, list)

    def test_entities_by_key_is_dict(self):
        self.assertIsInstance(ENTITIES_BY_KEY, dict)

    def test_all_entities_are_entity_spec_instances(self):
        for spec in ENTITIES:
            with self.subTest(key=spec.key):
                self.assertIsInstance(spec, EntitySpec)

    def test_entities_by_key_matches_entities(self):
        self.assertEqual(len(ENTITIES), len(ENTITIES_BY_KEY))

    def test_ioc_entity_present(self):
        self.assertIn('ioc', ENTITIES_BY_KEY)

    def test_asset_entity_present(self):
        self.assertIn('asset', ENTITIES_BY_KEY)

    def test_note_entity_present(self):
        self.assertIn('note', ENTITIES_BY_KEY)

    def test_event_entity_present(self):
        self.assertIn('event', ENTITIES_BY_KEY)

    def test_dsfile_entity_has_blob_column(self):
        self.assertIsNotNone(ENTITIES_BY_KEY['dsfile'].blob_column)

    def test_keys_in_by_key_match_spec_keys(self):
        for key, spec in ENTITIES_BY_KEY.items():
            with self.subTest(key=key):
                self.assertEqual(key, spec.key)

    def test_user_ref_prefix_constant(self):
        self.assertEqual('user', USER_REF_PREFIX)
