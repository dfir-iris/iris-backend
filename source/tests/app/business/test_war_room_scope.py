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

"""Unit tests for the war-room scope business layer (no database)."""

import csv
import io
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_scope as scope
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_BIZ = 'app.business.war_room_scope'
_USER = SimpleNamespace(id=7)


def _case_row(case_id, customer_id=1, customer_name='ACME'):
    return SimpleNamespace(case_id=case_id, case_name=f'Case {case_id}', customer_id=customer_id,
                           customer_name=customer_name)


def _ioc_row(value, type_name='domain', tlp_name='amber', case_id=1, type_id=1):
    return SimpleNamespace(ioc_value=value, ioc_type_id=type_id, ioc_type_name=type_name, tlp_name=tlp_name,
                           case_id=case_id, case_name=f'Case {case_id}')


class _DbPatches(TestCase):
    """Patch the lookups used by payload validation with permissive defaults."""

    def setUp(self):
        self._patchers = {
            'asset_type': patch(f'{_BIZ}.war_room_scope_db_asset_type_exists', return_value=True),
            'analysis': patch(f'{_BIZ}.war_room_scope_db_analysis_status_exists', return_value=True),
            'tlp': patch(f'{_BIZ}.war_room_scope_db_tlp_exists', return_value=True),
            'flag': patch(f'{_BIZ}.war_room_scope_db_flag_exists', return_value=True),
            'asset_flags': patch(f'{_BIZ}.war_room_scope_db_asset_flags', return_value=[]),
            'ioc_type': patch(f'{_BIZ}.war_room_scope_db_ioc_type_get',
                              return_value=SimpleNamespace(type_validation_regex=None,
                                                           type_validation_expect=None)),
            'cases': patch(f'{_BIZ}.war_room_scope_db_cases',
                           side_effect=lambda ids: [_case_row(cid) for cid in sorted(ids)]),
            'track': patch(f'{_BIZ}.track_activity'),
            'rollback': patch(f'{_BIZ}.war_room_scope_db_rollback'),
            'vuln_tags': patch(f'{_BIZ}.findings_db_asset_tags', return_value=[]),
        }
        self.mocks = {name: patcher.start() for name, patcher in self._patchers.items()}

    def tearDown(self):
        for patcher in self._patchers.values():
            patcher.stop()


class TestAssetPayloadValidation(_DbPatches):

    def test_allow_list_drops_unknown_fields(self):
        clean = scope._validate_asset_payload({
            'asset_name': '  srv01 ', 'asset_type_id': 3, 'asset_id': 99, 'case_id': 5,
            'flags': [2], 'custom_attributes': {'x': 1}, 'asset_tags': ['a', ' b', 'a'],
        })
        self.assertEqual(clean, {'asset_name': 'srv01', 'asset_type_id': 3, 'asset_tags': 'a,b'})

    def test_name_required(self):
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload({'asset_name': '   ', 'asset_type_id': 3})

    def test_name_too_long(self):
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload({'asset_name': 'x' * 513, 'asset_type_id': 3})

    def test_type_id_must_be_int_not_bool(self):
        for bad in (True, '3', 3.0, None, 0, -1):
            with self.assertRaises(BusinessProcessingError):
                scope._validate_asset_payload({'asset_name': 'a', 'asset_type_id': bad})

    def test_unknown_type_rejected(self):
        self.mocks['asset_type'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload({'asset_name': 'a', 'asset_type_id': 3})

    def test_compromise_status_enum(self):
        clean = scope._validate_asset_payload({'asset_name': 'a', 'asset_type_id': 3,
                                               'asset_compromise_status_id': 1})
        self.assertEqual(clean['asset_compromise_status_id'], 1)
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload({'asset_name': 'a', 'asset_type_id': 3, 'asset_compromise_status_id': 9})

    def test_unknown_analysis_status_rejected(self):
        self.mocks['analysis'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload({'asset_name': 'a', 'asset_type_id': 3, 'analysis_status_id': 4})

    def test_payload_must_be_object(self):
        with self.assertRaises(BusinessProcessingError):
            scope._validate_asset_payload(['asset_name'])


class TestIocPayloadValidation(_DbPatches):

    def test_valid_payload(self):
        clean = scope._validate_ioc_payload({'ioc_value': ' evil.com ', 'ioc_type_id': 2, 'ioc_tlp_id': 1,
                                             'ioc_tags': 'a,b', 'ioc_id': 3, 'user_id': 1})
        self.assertEqual(clean, {'ioc_value': 'evil.com', 'ioc_type_id': 2, 'ioc_tlp_id': 1, 'ioc_tags': 'a,b'})

    def test_regex_validation(self):
        self.mocks['ioc_type'].return_value = SimpleNamespace(type_validation_regex=r'[0-9a-f]{32}',
                                                              type_validation_expect='md5')
        with self.assertRaises(BusinessProcessingError):
            scope._validate_ioc_payload({'ioc_value': 'nothex', 'ioc_type_id': 2})
        clean = scope._validate_ioc_payload({'ioc_value': 'a' * 32, 'ioc_type_id': 2})
        self.assertEqual(clean['ioc_value'], 'a' * 32)

    def test_unknown_type_and_tlp(self):
        self.mocks['tlp'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            scope._validate_ioc_payload({'ioc_value': 'x', 'ioc_type_id': 2, 'ioc_tlp_id': 9})
        self.mocks['ioc_type'].return_value = None
        with self.assertRaises(BusinessProcessingError):
            scope._validate_ioc_payload({'ioc_value': 'x', 'ioc_type_id': 2})


class TestIdListsAndCaps(_DbPatches):

    def test_id_list_rejects_non_ints_and_dedups(self):
        self.assertEqual(scope._validate_id_list([3, 1, 3], 'case_ids', 50), [3, 1])
        for bad in ('1', [True], ['1'], [0], [1.5], None):
            with self.assertRaises(BusinessProcessingError):
                scope._validate_id_list(bad, 'case_ids', 50)

    def test_empty_target_list_rejected(self):
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_create_asset(1, _USER, {'asset_name': 'a', 'asset_type_id': 1}, [], [1])

    def test_target_cap(self):
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_create_ioc(1, _USER, {'ioc_value': 'x', 'ioc_type_id': 1},
                                            list(range(1, 52)), [1])

    def test_push_source_cap(self):
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_push_assets(1, _USER, list(range(1, 202)), [1], [1], [1])

    def test_bulk_flag_cap(self):
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_bulk_flag(1, 7, list(range(1, 502)), 1, 'set', None, None, [1], [1])


class TestCreateAndPush(_DbPatches):

    def test_create_denies_non_writable_targets_and_hides_their_customers(self):
        asset = SimpleNamespace(asset_id=55)
        with patch(f'{_BIZ}.war_room_scope_db_find_asset', return_value=None), \
                patch(f'{_BIZ}._create_asset_in_case', return_value=asset) as create:
            result = scope.war_room_scope_create_asset(
                9, _USER, {'asset_name': 'srv', 'asset_type_id': 1}, [1, 2], [1])
        self.assertEqual(result['results'], [
            {'case_id': 1, 'status': 'created', 'asset_id': 55},
            {'case_id': 2, 'status': 'denied', 'message': scope._DENIED_CASE_MESSAGE},
        ])
        create.assert_called_once()
        self.assertEqual(self.mocks['cases'].call_args.args[0], [1])
        self.assertNotIn('case_name', result['results'][1])

    def test_create_reports_exists_without_creating(self):
        with patch(f'{_BIZ}.war_room_scope_db_find_asset', return_value=12), \
                patch(f'{_BIZ}._create_asset_in_case') as create:
            result = scope.war_room_scope_create_asset(
                9, _USER, {'asset_name': 'srv', 'asset_type_id': 1}, [1], [1])
        self.assertEqual(result['results'], [{'case_id': 1, 'status': 'exists', 'asset_id': 12}])
        create.assert_not_called()

    def test_create_error_row_does_not_abort_other_targets(self):
        with patch(f'{_BIZ}.war_room_scope_db_find_ioc', return_value=None), \
                patch(f'{_BIZ}._create_ioc_in_case',
                      side_effect=[BusinessProcessingError('boom'), SimpleNamespace(ioc_id=4)]):
            result = scope.war_room_scope_create_ioc(9, _USER, {'ioc_value': 'x', 'ioc_type_id': 1}, [1, 2], [1, 2])
        self.assertEqual([row['status'] for row in result['results']], ['error', 'created'])
        self.mocks['rollback'].assert_called()

    def test_create_applies_flags_on_created_assets(self):
        asset = SimpleNamespace(asset_id=55)
        with patch(f'{_BIZ}.war_room_scope_db_find_asset', return_value=None), \
                patch(f'{_BIZ}._create_asset_in_case', return_value=asset), \
                patch(f'{_BIZ}.asset_flags_set_for_asset', return_value=asset) as set_flag:
            result = scope.war_room_scope_create_asset(9, _USER, {'asset_name': 'srv', 'asset_type_id': 1}, [1],
                                                       [1], flag_ids=[3, 4, 3], flag_reason='why')
        self.assertEqual([((asset, 3, 'why', None, 7), {'war_room_id': 9}),
                          ((asset, 4, 'why', None, 7), {'war_room_id': 9})],
                         [(call.args, call.kwargs) for call in set_flag.call_args_list])
        self.assertNotIn('message', result['results'][0])

    def test_create_reports_a_flag_that_could_not_be_set(self):
        asset = SimpleNamespace(asset_id=55)
        with patch(f'{_BIZ}.war_room_scope_db_find_asset', return_value=None), \
                patch(f'{_BIZ}._create_asset_in_case', return_value=asset), \
                patch(f'{_BIZ}.asset_flags_set_for_asset',
                      side_effect=BusinessProcessingError('Flag "Blocked" requires a reason')):
            result = scope.war_room_scope_create_asset(9, _USER, {'asset_name': 'srv', 'asset_type_id': 1}, [1],
                                                       [1], flag_ids=[3])
        row = result['results'][0]
        self.assertEqual('created', row['status'])
        self.assertEqual('Created, but the flags were not all set: Flag "Blocked" requires a reason', row['message'])

    def test_create_refuses_unknown_flags_before_creating(self):
        self.mocks['flag'].return_value = False
        with patch(f'{_BIZ}._create_asset_in_case') as create:
            with self.assertRaisesRegex(BusinessProcessingError, 'Flag not found'):
                scope.war_room_scope_create_asset(9, _USER, {'asset_name': 'srv', 'asset_type_id': 1}, [1], [1],
                                                  flag_ids=[3])
        create.assert_not_called()

    def test_push_created_then_exists(self):
        source = SimpleNamespace(asset_id=10, case_id=1, asset_name='SRV', asset_type_id=2, asset_description='d',
                                 asset_ip=None, asset_domain='', asset_tags='t', asset_compromise_status_id=1,
                                 analysis_status_id=None, flags=[SimpleNamespace(flag_id=4)],
                                 custom_attributes={'a': 1})
        existing = {}

        def find(case_id, name, type_id):
            return existing.get((case_id, name.lower(), type_id))

        def create(user, case_id, clean):
            existing[(case_id, clean['asset_name'].lower(), clean['asset_type_id'])] = 77
            return SimpleNamespace(asset_id=77)

        with patch(f'{_BIZ}.war_room_scope_db_assets_by_ids', return_value=[source]), \
                patch(f'{_BIZ}.war_room_scope_db_find_asset', side_effect=find), \
                patch(f'{_BIZ}._create_asset_in_case', side_effect=create) as create_mock:
            first = scope.war_room_scope_push_assets(9, _USER, [10], [2], [1, 2], [1, 2])
            second = scope.war_room_scope_push_assets(9, _USER, [10], [2], [1, 2], [1, 2])
        self.assertEqual(first['results'], [{'asset_id': 10, 'case_id': 2, 'status': 'created', 'new_asset_id': 77}])
        self.assertEqual(second['results'][0]['status'], 'exists')
        self.assertEqual(second['results'][0]['existing_asset_id'], 77)
        copied = create_mock.call_args_list[0].args[2]
        self.assertEqual(copied, {'asset_name': 'SRV', 'asset_type_id': 2, 'asset_description': 'd',
                                  'asset_tags': 't', 'asset_compromise_status_id': 1})

    def test_push_source_in_unreadable_case_is_denied(self):
        source = SimpleNamespace(ioc_id=10, case_id=3, ioc_value='x', ioc_type_id=1)
        with patch(f'{_BIZ}.war_room_scope_db_iocs_by_ids', return_value=[source]), \
                patch(f'{_BIZ}._create_ioc_in_case') as create:
            result = scope.war_room_scope_push_iocs(9, _USER, [10, 11], [1], [1, 2], [1, 2])
        self.assertEqual([row['status'] for row in result['results']], ['denied', 'denied'])
        create.assert_not_called()


def _flagged(asset_id, case_id, *flag_ids):
    return SimpleNamespace(asset_id=asset_id, case_id=case_id,
                           flags=[SimpleNamespace(flag_id=flag_id, reason=None, decision_id=None)
                                  for flag_id in flag_ids])


class TestBulkFlag(_DbPatches):

    def test_statuses(self):
        assets = [_flagged(1, 1), _flagged(2, 1, 3), _flagged(3, 2), _flagged(4, 5)]
        with patch(f'{_BIZ}.war_room_scope_db_assets_by_ids', return_value=assets), \
                patch(f'{_BIZ}.asset_flags_set_for_asset') as set_flag, \
                patch(f'{_BIZ}.call_modules_hook') as hook:
            result = scope.war_room_scope_bulk_flag(9, 7, [1, 2, 3, 4, 99], 3, 'set', None, None, [1, 2], [1])
        statuses = {row['asset_id']: row['status'] for row in result['results']}
        self.assertEqual(statuses, {1: 'updated', 2: 'unchanged', 3: 'denied', 4: 'denied', 99: 'denied'})
        set_flag.assert_called_once_with(assets[0], 3, None, None, 7, war_room_id=9)
        unreadable = next(row for row in result['results'] if row['asset_id'] == 4)
        self.assertIsNone(unreadable['case_id'])
        self.mocks['track'].assert_called_once_with('changed the flags of 1 asset(s) from the war room scope',
                                                    war_room_id=9)
        self.assertEqual(('on_postload_war_room_scope_flag_update', {
            'war_room_id': 9, 'flag_id': 3, 'action': 'set', 'reason': None, 'decision_id': None, 'asset_ids': [1],
        }), hook.call_args.args)

    def test_clear(self):
        assets = [_flagged(1, 1, 3), _flagged(2, 1)]
        with patch(f'{_BIZ}.war_room_scope_db_assets_by_ids', return_value=assets), \
                patch(f'{_BIZ}.asset_flags_clear_for_asset') as clear_flag, \
                patch(f'{_BIZ}.asset_flags_set_for_asset') as set_flag, \
                patch(f'{_BIZ}.call_modules_hook'):
            result = scope.war_room_scope_bulk_flag(9, 7, [1, 2], 3, 'clear', ' back online ', None, [1], [1])
        self.assertEqual(['updated', 'unchanged'], [row['status'] for row in result['results']])
        clear_flag.assert_called_once_with(assets[0], 3, 'back online', 7, war_room_id=9)
        set_flag.assert_not_called()

    def test_clear_ignores_the_decision(self):
        with patch(f'{_BIZ}.war_room_scope_db_decision_in_room', return_value=False) as in_room, \
                patch(f'{_BIZ}.war_room_scope_db_assets_by_ids', return_value=[]):
            scope.war_room_scope_bulk_flag(9, 7, [1], 3, 'clear', None, 5, [1], [1])
        in_room.assert_not_called()

    def test_invalid_action_and_unknown_flag(self):
        with self.assertRaisesRegex(BusinessProcessingError, 'action must be one of'):
            scope.war_room_scope_bulk_flag(9, 7, [1], 3, 'toggle', None, None, [1], [1])
        self.mocks['flag'].return_value = False
        with self.assertRaisesRegex(BusinessProcessingError, 'Flag not found'):
            scope.war_room_scope_bulk_flag(9, 7, [1], 3, 'set', None, None, [1], [1])

    def test_business_error_is_reported(self):
        with patch(f'{_BIZ}.war_room_scope_db_assets_by_ids', return_value=[_flagged(1, 1)]), \
                patch(f'{_BIZ}.asset_flags_set_for_asset', side_effect=BusinessProcessingError('reason required')):
            result = scope.war_room_scope_bulk_flag(9, 7, [1], 3, 'set', None, None, [1], [1])
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertEqual(result['results'][0]['message'], 'reason required')
        self.mocks['rollback'].assert_called_once()

    def test_decision_must_belong_to_room(self):
        with patch(f'{_BIZ}.war_room_scope_db_decision_in_room', return_value=False):
            with self.assertRaises(BusinessProcessingError):
                scope.war_room_scope_bulk_flag(9, 7, [1], 3, 'set', None, 5, [1], [1])


class TestStaging(_DbPatches):

    def test_create_validates_and_caps(self):
        with patch(f'{_BIZ}.war_room_scope_db_staged_count', return_value=500):
            with self.assertRaises(BusinessProcessingError):
                scope.war_room_scope_staged_create(9, 7, {'object_type': 'asset',
                                                          'payload': {'asset_name': 'a', 'asset_type_id': 1}})
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_staged_create(9, 7, {'object_type': 'note', 'payload': {}})
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_staged_create(9, 7, {'object_type': 'ioc', 'payload': {'ioc_value': 'x',
                                                                                         'ioc_type_id': 1},
                                                      'proposed_case_ids': ['1']})

    def test_create_stores_clean_payload(self):
        with patch(f'{_BIZ}.war_room_scope_db_staged_count', return_value=0), \
                patch(f'{_BIZ}.war_room_scope_db_staged_add', side_effect=lambda staged: staged):
            staged = scope.war_room_scope_staged_create(9, 7, {
                'object_type': 'asset', 'payload': {'asset_name': 'a', 'asset_type_id': 1, 'case_id': 4},
                'proposed_case_ids': [2, 2, 3], 'note': 'from chat',
            })
        self.assertEqual(staged.payload, {'asset_name': 'a', 'asset_type_id': 1})
        self.assertEqual(staged.proposed_case_ids, [2, 3])
        self.assertEqual(staged.created_by_id, 7)

    def _staged(self):
        return SimpleNamespace(id=4, object_type='ioc', payload={'ioc_value': 'x', 'ioc_type_id': 1},
                               proposed_case_ids=[1, 2])

    def test_push_deletes_row_when_every_target_succeeds(self):
        staged = self._staged()
        with patch(f'{_BIZ}.war_room_scope_db_staged_get', return_value=staged), \
                patch(f'{_BIZ}.war_room_scope_db_find_ioc', side_effect=[3, None]), \
                patch(f'{_BIZ}._create_ioc_in_case', return_value=SimpleNamespace(ioc_id=8)), \
                patch(f'{_BIZ}.war_room_scope_db_staged_delete') as delete:
            result = scope.war_room_scope_staged_push(9, _USER, 4, None, [1, 2])
        self.assertEqual([row['status'] for row in result['results']], ['exists', 'created'])
        self.assertTrue(result['staged_deleted'])
        delete.assert_called_once_with(staged)

    def test_push_should_fire_the_push_hook(self):
        with patch(f'{_BIZ}.war_room_scope_db_staged_get', return_value=self._staged()), \
                patch(f'{_BIZ}.war_room_scope_db_find_ioc', return_value=None), \
                patch(f'{_BIZ}._create_ioc_in_case', return_value=SimpleNamespace(ioc_id=8)), \
                patch(f'{_BIZ}.war_room_scope_db_staged_delete'), \
                patch(f'{_BIZ}.call_modules_hook') as hook:
            scope.war_room_scope_staged_push(9, _USER, 4, None, [1, 2])
        hook.assert_called_once()
        hook_name, data = hook.call_args[0]
        self.assertEqual(hook_name, 'on_postload_war_room_staged_object_push')
        self.assertEqual(data['war_room_id'], 9)
        self.assertEqual(data['staged_id'], 4)
        self.assertTrue(data['staged_deleted'])

    def test_push_keeps_row_when_a_target_is_denied(self):
        with patch(f'{_BIZ}.war_room_scope_db_staged_get', return_value=self._staged()), \
                patch(f'{_BIZ}.war_room_scope_db_find_ioc', return_value=None), \
                patch(f'{_BIZ}._create_ioc_in_case', return_value=SimpleNamespace(ioc_id=8)), \
                patch(f'{_BIZ}.war_room_scope_db_staged_delete') as delete:
            result = scope.war_room_scope_staged_push(9, _USER, 4, [1, 2], [1])
        self.assertFalse(result['staged_deleted'])
        delete.assert_not_called()

    def test_push_unknown_row(self):
        with patch(f'{_BIZ}.war_room_scope_db_staged_get', return_value=None):
            with self.assertRaises(ObjectNotFoundError):
                scope.war_room_scope_staged_push(9, _USER, 4, [1], [1])

    def test_update_only_touches_allow_listed_fields(self):
        staged = SimpleNamespace(id=4, object_type='asset', payload={}, proposed_case_ids=None, note=None,
                                 war_room_id=9)
        with patch(f'{_BIZ}.war_room_scope_db_staged_get', return_value=staged), \
                patch(f'{_BIZ}.war_room_scope_db_staged_save'):
            scope.war_room_scope_staged_update(9, 4, {'note': 'n', 'war_room_id': 1, 'object_type': 'ioc'})
        self.assertEqual(staged.note, 'n')
        self.assertEqual(staged.war_room_id, 9)
        self.assertEqual(staged.object_type, 'asset')


class TestExport(TestCase):

    def _export(self, rows, export_format, include_red=False):
        with patch(f'{_BIZ}.war_room_scope_db_iocs', return_value=rows) as query:
            content, mimetype, extension = scope.war_room_scope_export_iocs([1, 2], export_format,
                                                                            include_red=include_red)
        self.assertEqual(query.call_args.args[0], [1, 2])
        return content, mimetype, extension

    def test_txt_dedups_and_excludes_red(self):
        rows = [_ioc_row('a.com'), _ioc_row('a.com', case_id=2), _ioc_row('red.com', tlp_name='red'),
                _ioc_row('mixed.com', tlp_name='green'), _ioc_row('mixed.com', tlp_name='red', case_id=2)]
        content, mimetype, extension = self._export(rows, 'txt')
        self.assertEqual(content.splitlines(), ['a.com'])
        self.assertEqual((mimetype, extension), ('text/plain', 'txt'))
        content, _, _ = self._export(rows, 'txt', include_red=True)
        self.assertEqual(sorted(content.splitlines()), ['a.com', 'mixed.com', 'red.com'])

    def test_csv_injection_is_neutralised(self):
        rows = [_ioc_row('=HYPERLINK("http://x")'), _ioc_row('+1'), _ioc_row('-2'), _ioc_row('@SUM(A1)'),
                _ioc_row('safe.com')]
        content, mimetype, _ = self._export(rows, 'csv')
        self.assertEqual(mimetype, 'text/csv')
        parsed = list(csv.reader(io.StringIO(content)))
        self.assertEqual(parsed[0], ['value', 'type', 'tlp', 'cases'])
        values = sorted(row[0] for row in parsed[1:])
        self.assertEqual(values, ["'+1", "'-2", '\'=HYPERLINK("http://x")', "'@SUM(A1)", 'safe.com'])
        self.assertTrue(all(not row[3].startswith(('=', '+', '-', '@')) for row in parsed[1:]))

    def test_csv_safe(self):
        self.assertEqual(scope._csv_safe('=1+1'), "'=1+1")
        self.assertEqual(scope._csv_safe('\tx'), "'\tx")
        self.assertEqual(scope._csv_safe(None), '')
        self.assertEqual(scope._csv_safe('ok'), 'ok')

    def test_stix_bundle_shape(self):
        rows = [_ioc_row('evil.com'), _ioc_row('1.2.3.4', type_name='ip-dst', type_id=2),
                _ioc_row("it's", type_name='sha256', type_id=3, tlp_name='green')]
        content, mimetype, extension = self._export(rows, 'stix')
        self.assertEqual((mimetype, extension), ('application/json', 'json'))
        bundle = json.loads(content)
        self.assertEqual(bundle['type'], 'bundle')
        self.assertTrue(bundle['id'].startswith('bundle--'))
        self.assertEqual(len(bundle['objects']), 3)
        for indicator in bundle['objects']:
            self.assertEqual(indicator['type'], 'indicator')
            self.assertEqual(indicator['spec_version'], '2.1')
            self.assertEqual(indicator['pattern_type'], 'stix')
            self.assertTrue(indicator['id'].startswith('indicator--'))
            for key in ('created', 'modified', 'valid_from'):
                self.assertTrue(indicator[key].endswith('Z'))
        patterns = {indicator['name']: indicator['pattern'] for indicator in bundle['objects']}
        self.assertEqual(patterns['evil.com'], "[domain-name:value = 'evil.com']")
        self.assertEqual(patterns['1.2.3.4'], "[ipv4-addr:value = '1.2.3.4']")
        self.assertEqual(patterns["it's"], "[file:hashes.'SHA-256' = 'it\\'s']")

    def test_unknown_format(self):
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_export_iocs([1], 'xml')


class TestListing(_DbPatches):

    def test_case_filter_cannot_widen_readable_cases(self):
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query:
            scope.war_room_scope_list_assets([1, 2], case_id='3')
        self.assertEqual(query.call_args.args[0], [])

    def test_truncation_flag(self):
        row = MagicMock(asset_name='A', asset_uuid=None, date_update=None, ioc_count=0,
                        asset_type_id=1)
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[row] * (scope.WAR_ROOM_SCOPE_LIST_LIMIT + 1)):
            result = scope.war_room_scope_list_assets([1])
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['data']), scope.WAR_ROOM_SCOPE_LIST_LIMIT)
        self.assertEqual(result['data'][0]['group_key'], '1:a')

    def test_flag_filter_parsing(self):
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query:
            scope.war_room_scope_list_assets([1], flag='None', compromised='1')
        self.assertTrue(query.call_args.kwargs['flag_none'])
        self.assertIsNone(query.call_args.kwargs['flag_id'])
        self.assertTrue(query.call_args.kwargs['compromised'])
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query:
            scope.war_room_scope_list_assets([1], flag='3', without_flag='4')
        self.assertEqual((3, False, 4), (query.call_args.kwargs['flag_id'], query.call_args.kwargs['flag_none'],
                                         query.call_args.kwargs['without_flag_id']))
        for kwargs in ({'flag': 'abc'}, {'without_flag': 'none'}, {'flag': '0'}):
            with self.assertRaises(BusinessProcessingError, msg=kwargs):
                scope.war_room_scope_list_assets([1], **kwargs)

    def test_flags_are_attached_per_asset(self):
        rows = [_asset_row(1, 'a', 1), _asset_row(2, 'b', 1)]
        self.mocks['asset_flags'].return_value = [
            SimpleNamespace(asset_id=1, flag_id=3, reason=None, decision_id=None, set_at=None),
            SimpleNamespace(asset_id=1, flag_id=5, reason='legacy', decision_id=9, set_at=None),
        ]
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=rows):
            data = scope.war_room_scope_list_assets([1])['data']
        self.mocks['asset_flags'].assert_called_once_with([1, 2])
        self.assertEqual([{'flag_id': 3, 'reason': None, 'decision_id': None, 'set_at': None},
                          {'flag_id': 5, 'reason': 'legacy', 'decision_id': 9, 'set_at': None}], data[0]['flags'])
        self.assertEqual([], data[1]['flags'])

    def test_ioc_group_key_ignores_type_case_and_whitespace(self):
        def _row(ioc_id, value, type_id):
            return SimpleNamespace(ioc_id=ioc_id, ioc_value=value, ioc_type_id=type_id, ioc_type_name=None,
                                   ioc_tlp_id=None, tlp_name=None, ioc_description=None, ioc_tags=None,
                                   case_id=ioc_id, case_name='C', customer_id=1, customer_name='ACME')
        rows = [_row(1, 'Evil.COM', 1), _row(2, ' evil.com ', 2), _row(3, 'other.com', 1)]
        with patch(f'{_BIZ}.war_room_scope_db_iocs', return_value=rows):
            result = scope.war_room_scope_list_iocs([1, 2, 3])
        keys = [r['group_key'] for r in result['data']]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[2])

    def test_push_ioc_dedup_is_by_value_only(self):
        with patch(f'{_BIZ}.war_room_scope_db_find_ioc', return_value=3) as find, \
                patch(f'{_BIZ}._create_ioc_in_case') as create:
            result = scope.war_room_scope_create_ioc(9, _USER, {'ioc_value': 'x', 'ioc_type_id': 1}, [1], [1])
        find.assert_called_once_with(1, 'x')
        self.assertEqual(result['results'][0]['status'], 'exists')
        create.assert_not_called()


class TestVulnerabilityPermission(_DbPatches):

    def _row(self):
        return MagicMock(asset_name='srv', asset_uuid=None, date_update=None, ioc_count=0,
                         asset_type_id=1, vuln_open_count=2, vuln_max_rank=4)

    def test_vulnerability_fields_are_dropped_without_permission(self):
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[self._row()]), \
                patch(f'{_BIZ}._vulnerability_tags') as tags:
            result = scope.war_room_scope_list_assets([1], include_vulnerabilities=False)
        tags.assert_not_called()
        item = result['data'][0]
        self.assertEqual(item['asset_name'], 'srv')
        for field in scope._ASSET_VULNERABILITY_FIELDS:
            self.assertNotIn(field, item)

    def test_vulnerability_filters_are_refused_without_permission(self):
        for kwargs in ({'vulnerable': 'open'}, {'vulnerability': 'CVE-2024-3400'}):
            with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query, \
                    self.assertRaises(BusinessProcessingError):
                scope.war_room_scope_list_assets([1], include_vulnerabilities=False, **kwargs)
            query.assert_not_called()

    def test_case_totals_drop_vulnerability_counts_without_permission(self):
        breakdown = [_breakdown(1, 2, vuln_open=1, exploited_open=1)]
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]), \
                patch(f'{_BIZ}.war_room_scope_db_assets_breakdown', return_value=breakdown), \
                patch(f'{_BIZ}.war_room_scope_db_flag_totals', return_value=[]), \
                patch(f'{_BIZ}._asset_sightings', return_value={}):
            result = scope.war_room_scope_list_assets([1], page='1', include_vulnerabilities=False)
        self.assertEqual(result['case_totals'], [{'case_id': 1, 'assets': 2, 'done': 0}])


class TestVulnerableFilter(_DbPatches):

    def test_vulnerable_filter_parsing(self):
        for raw, expected in (('1', 'open'), ('open', 'open'), ('Exploited', 'exploited'), ('none', 'none'),
                              ('false', 'none'), (None, None), ('', None)):
            with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query:
                scope.war_room_scope_list_assets([1], vulnerable=raw)
            self.assertEqual(query.call_args.kwargs['vulnerable'], expected, raw)
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_list_assets([1], vulnerable='maybe')

    def test_vulnerability_columns(self):
        row = SimpleNamespace(
            asset_id=1, asset_uuid=None, asset_name='srv', asset_type_id=1, asset_type_name='Windows',
            asset_ip=None, asset_domain=None, asset_description=None, asset_tags=None,
            asset_compromise_status_id=1, analysis_status_id=None, analysis_status_name=None, case_id=3,
            case_name='c',
            customer_id=1, customer_name='x', ioc_count=0, vuln_open_count=2, vuln_total_count=3,
            vuln_exploited_count=1, vuln_exploited_open_count=1, vuln_max_rank=5, date_update=None)
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[row]):
            data = scope.war_room_scope_list_assets([3])['data'][0]
        self.assertEqual(data['vuln_open_count'], 2)
        self.assertEqual(data['vuln_exploited_open_count'], 1)
        self.assertEqual(data['vuln_max_severity'], 'critical')
        row.vuln_max_rank = None
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[row]):
            data = scope.war_room_scope_list_assets([3])['data'][0]
        self.assertIsNone(data['vuln_max_severity'])
        self.assertEqual(data['vulnerabilities'], [])

    def test_vulnerability_filter_is_normalised(self):
        for raw, expected in (('cve-2024-3400', 'CVE-2024-3400'), (' CVE-2024-3400 ', 'CVE-2024-3400'),
                              ('iris-vuln-2026-0001', 'IRIS-VULN-2026-0001'), ('', None), (None, None)):
            with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=[]) as query:
                scope.war_room_scope_list_assets([1], vulnerability=raw)
            self.assertEqual(query.call_args.kwargs['vulnerability'], expected, raw)
        with self.assertRaises(BusinessProcessingError):
            scope.war_room_scope_list_assets([1], vulnerability='CVE-24-1')

    def test_vulnerability_tags_are_attached_per_asset(self):
        rows = [SimpleNamespace(asset_id=asset_id, asset_uuid=None, asset_name=f'a{asset_id}', asset_type_id=1,
                                date_update=None, ioc_count=0, vuln_max_rank=None)
                for asset_id in (1, 2)]
        for row in rows:
            for column in ('asset_type_name', 'asset_ip', 'asset_domain', 'asset_description', 'asset_tags',
                           'asset_compromise_status_id', 'analysis_status_id', 'analysis_status_name',
                           'case_id', 'case_name',
                           'customer_id', 'customer_name', 'vuln_open_count', 'vuln_total_count',
                           'vuln_exploited_count', 'vuln_exploited_open_count'):
                setattr(row, column, None)
        tag = SimpleNamespace(asset_id=1, finding_id=7, vulnerability_id=4, identifier='CVE-2024-3400',
                              severity='critical', kev=True, remediation_status='open',
                              exploitation_status='exploited')
        self.mocks['vuln_tags'].return_value = [tag]
        with patch(f'{_BIZ}.war_room_scope_db_assets', return_value=rows):
            data = scope.war_room_scope_list_assets([3])['data']
        self.mocks['vuln_tags'].assert_called_once_with([1, 2])
        self.assertEqual(data[0]['vulnerabilities'], [{
            'finding_id': 7, 'vulnerability_id': 4, 'identifier': 'CVE-2024-3400', 'severity': 'critical',
            'kev': True, 'remediation_status': 'open', 'exploitation_status': 'exploited'}])
        self.assertEqual(data[1]['vulnerabilities'], [])


def _asset_row(asset_id, name, case_id, type_id=1):
    return SimpleNamespace(
        asset_id=asset_id, asset_uuid=None, asset_name=name, asset_type_id=type_id, asset_type_name='Windows',
        asset_ip=None, asset_domain=None, asset_description=None, asset_tags=None,
        asset_compromise_status_id=None, analysis_status_id=None, analysis_status_name=None, case_id=case_id,
        case_name='c', customer_id=1, customer_name='x', ioc_count=0, vuln_open_count=0, vuln_total_count=0,
        vuln_exploited_count=0, vuln_exploited_open_count=0, vuln_max_rank=None, date_update=None)


def _breakdown(case_id, assets, done=0, unflagged=0, vuln_open=0, exploited_open=0):
    return SimpleNamespace(case_id=case_id, assets=assets, done=done, unflagged=unflagged, vuln_open=vuln_open,
                           vuln_exploited_open=exploited_open)


def _full_ioc_row(ioc_id, value, case_id):
    return SimpleNamespace(ioc_id=ioc_id, ioc_value=value, ioc_type_id=1, ioc_type_name=None, ioc_tlp_id=None,
                           tlp_name=None, ioc_description=None, ioc_tags=None, case_id=case_id, case_name='C',
                           customer_id=1, customer_name='ACME')


class TestPaginatedAssets(_DbPatches):

    def setUp(self):
        super().setUp()
        self.assets = patch(f'{_BIZ}.war_room_scope_db_assets').start()
        self.breakdown = patch(f'{_BIZ}.war_room_scope_db_assets_breakdown', return_value=[]).start()
        self.flag_totals = patch(f'{_BIZ}.war_room_scope_db_flag_totals', return_value=[]).start()
        self.sightings = patch(f'{_BIZ}.war_room_scope_db_asset_sightings', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_legacy_listing_is_unchanged_without_page(self):
        self.assets.return_value = []
        result = scope.war_room_scope_list_assets([1, 2])
        self.assertEqual(set(result), {'data', 'truncated', 'limit', 'cases'})
        self.assertEqual(self.assets.call_args.kwargs['limit'], scope.WAR_ROOM_SCOPE_LIST_LIMIT)
        self.breakdown.assert_not_called()
        self.sightings.assert_not_called()

    def test_page_offset_totals_and_sightings(self):
        self.assets.return_value = [_asset_row(10, 'WS-1', 1), _asset_row(11, 'srv', 2), _asset_row(12, 'x', 3)]
        self.breakdown.return_value = [
            _breakdown(2, 5, done=4, vuln_open=1),
            _breakdown(1, 5, done=2, unflagged=3, vuln_open=2, exploited_open=1),
        ]
        self.flag_totals.return_value = [SimpleNamespace(flag_id=6, assets=1), SimpleNamespace(flag_id=5, assets=6)]
        self.sightings.return_value = [
            SimpleNamespace(asset_type_id=1, name_key='ws-1', case_id=1),
            SimpleNamespace(asset_type_id=1, name_key='ws-1', case_id=4),
            SimpleNamespace(asset_type_id=1, name_key='ws-1', case_id=3),
            SimpleNamespace(asset_type_id=2, name_key='srv', case_id=1),
        ]
        result = scope.war_room_scope_list_assets([1, 2, 3, 4], page='3', per_page='2', sort='case')

        kwargs = self.assets.call_args.kwargs
        self.assertEqual((kwargs['limit'], kwargs['offset'], kwargs['sort']), (2, 4, 'case'))
        self.assertEqual(len(result['data']), 2)
        self.assertEqual((result['total'], result['page'], result['per_page']), (10, 3, 2))
        self.assertFalse(result['truncated'])
        self.assertEqual(result['case_totals'], [
            {'case_id': 1, 'assets': 5, 'done': 2, 'vuln_open': 2, 'vuln_exploited_open': 1},
            {'case_id': 2, 'assets': 5, 'done': 4, 'vuln_open': 1, 'vuln_exploited_open': 0},
        ])
        self.assertEqual(result['flag_totals'], [{'flag_id': None, 'assets': 3}, {'flag_id': 5, 'assets': 6},
                                                 {'flag_id': 6, 'assets': 1}])
        self.assertEqual(self.flag_totals.call_args.args, ([1, 2, 3, 4],))
        first, second = result['data']
        self.assertEqual((first['sighting_case_ids'], first['sighting_count']), ([3, 4], 2))
        self.assertEqual((second['sighting_case_ids'], second['sighting_count']), ([], 0))
        self.assertEqual(self.sightings.call_args.args, ([1, 2, 3, 4], {'ws-1', 'srv'}))

    def test_case_filter_restricts_rows_but_not_sightings(self):
        self.assets.return_value = []
        scope.war_room_scope_list_assets([1, 2], case_id='2', page='1')
        self.assertEqual(self.assets.call_args.args[0], [2])
        self.assertEqual(self.breakdown.call_args.args[0], [2])

    def test_pagination_validation(self):
        self.assets.return_value = []
        for kwargs in ({'page': '0'}, {'page': 'x'}, {'page': '1', 'per_page': '501'},
                       {'page': '1', 'sort': 'size'}):
            with self.assertRaises(BusinessProcessingError, msg=kwargs):
                scope.war_room_scope_list_assets([1], **kwargs)
        result = scope.war_room_scope_list_assets([1], page='1')
        self.assertEqual(result['per_page'], scope.WAR_ROOM_SCOPE_DEFAULT_PER_PAGE)
        self.assertEqual(result['sort'], 'name')


class TestPaginatedIocs(_DbPatches):

    def setUp(self):
        super().setUp()
        self.iocs = patch(f'{_BIZ}.war_room_scope_db_iocs').start()
        self.keys = patch(f'{_BIZ}.war_room_scope_db_ioc_keys_page').start()
        self.count = patch(f'{_BIZ}.war_room_scope_db_ioc_keys_count', return_value=42).start()
        self.case_counts = patch(f'{_BIZ}.war_room_scope_db_ioc_case_counts', return_value=[
            SimpleNamespace(case_id=2, iocs=3), SimpleNamespace(case_id=1, iocs=5)]).start()
        self.addCleanup(patch.stopall)

    def test_legacy_listing_is_unchanged_without_page(self):
        self.iocs.return_value = []
        result = scope.war_room_scope_list_iocs([1])
        self.assertEqual(set(result), {'data', 'truncated', 'limit', 'cases'})
        self.keys.assert_not_called()

    def test_pages_over_distinct_indicators_in_key_order(self):
        self.keys.return_value = [SimpleNamespace(key='b.com', case_count=2), SimpleNamespace(key='a.com',
                                                                                               case_count=1)]
        self.iocs.return_value = [_full_ioc_row(1, 'a.com', 1), _full_ioc_row(2, 'B.com', 1),
                                  _full_ioc_row(3, ' b.com', 2)]
        result = scope.war_room_scope_list_iocs([1, 2], page='2', per_page='2', sort='spread')
        self.assertEqual(self.keys.call_args.kwargs, {'search': None, 'offset': 2, 'limit': 2, 'sort': 'spread'})
        self.assertEqual(self.iocs.call_args.kwargs['keys'], ['b.com', 'a.com'])
        self.assertEqual([row['ioc_id'] for row in result['data']], [2, 3, 1])
        self.assertEqual(result['total'], 42)
        self.assertEqual(result['case_totals'], [{'case_id': 1, 'iocs': 5}, {'case_id': 2, 'iocs': 3}])

    def test_empty_page_skips_the_rows_query(self):
        self.keys.return_value = []
        result = scope.war_room_scope_list_iocs([1], page='9')
        self.assertEqual(result['data'], [])
        self.iocs.assert_not_called()
