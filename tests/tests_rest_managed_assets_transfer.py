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

"""Export and import of the asset registry.

Export is server-side on purpose: the browser only ever holds one
access-filtered page, and a client-side writer cannot be trusted to
neutralise spreadsheet formulas. Both properties are asserted here.

Import is two-phase. `/import/inspect` stages the upload and reports
what applying it *would* do without writing a row; `/import` spends the
resulting token. The token is a capability — it pins the customer chosen
at inspect, only its uploader can spend it, and it is single-use. Those
are the properties that make the phase split safe rather than decorative,
so each has a test.

The importer writes to `managed_asset` and nowhere else. An import that
could reach into `case_assets` would let anyone holding
`asset_manager_write` fabricate evidence in an investigation they cannot
even open, which is why there is an explicit test that it does not.
"""

import io
import json
from unittest import TestCase
from uuid import uuid4

from iris import Iris
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER
from iris import IRIS_PERMISSION_ASSET_MANAGER_READ
from iris import IRIS_PERMISSION_ASSET_MANAGER_WRITE

_MANAGED_ASSETS_URL = '/api/v2/manage/managed-assets'
_TAGS_URL = '/api/v2/tags'
_INSPECT_URL = f'{_MANAGED_ASSETS_URL}/import/inspect'
_IMPORT_URL = f'{_MANAGED_ASSETS_URL}/import'
_EXPORT_URL = f'{_MANAGED_ASSETS_URL}/export'

_DEFAULT_ASSET_TYPE_IDENTIFIER = 1
_DEFAULT_ASSET_TYPE_NAME = 'Account'
_OTHER_ASSET_TYPE_NAME = 'Firewall'

_CSV_HEADER = 'name,asset_type,criticality,owner'
_CSV_HEADER_WITH_TAGS = 'name,asset_type,criticality,tags'

# A zip local file header followed by a byte that is not valid UTF-8.
# Archives are refused outright rather than unpacked — the whole
# archive-bomb class simply does not apply to a format we never read.
_ZIP_BYTES = b'PK\x03\x04\x14\x00\x00\x00\x08\x00\xff\xfe\xfd\xfc'


class TestsRestManagedAssetsTransfer(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        self._asset_name = f'SRV-{uuid4().hex[:12]}'

    def tearDown(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 100}).json()
        for asset in response.get('data', []):
            self._subject.delete(f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}')
        self._subject.clear_database()

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def _create(self, name=None, client_identifier=IRIS_INITIAL_CUSTOMER_IDENTIFIER, **overrides):
        body = {
            'client_id': client_identifier,
            'asset_type_id': _DEFAULT_ASSET_TYPE_IDENTIFIER,
            'name': name or self._asset_name,
        }
        body.update(overrides)
        return self._subject.create(_MANAGED_ASSETS_URL, body).json()

    def _user(self, permissions, customers=(IRIS_INITIAL_CUSTOMER_IDENTIFIER,)):
        user = self._subject.create_dummy_user(permissions=permissions)
        self._subject.create(
            f'/manage/users/{user.get_identifier()}/customers/update',
            {'customers_membership': list(customers)},
        )
        return user

    def _writer(self, customers=(IRIS_INITIAL_CUSTOMER_IDENTIFIER,)):
        return self._user(
            IRIS_PERMISSION_ASSET_MANAGER_READ | IRIS_PERMISSION_ASSET_MANAGER_WRITE,
            customers,
        )

    def _registry_names(self):
        response = self._subject.get(_MANAGED_ASSETS_URL, {'per_page': 100}).json()
        return [asset['name'] for asset in response['data']]

    # ------------------------------------------------------------------
    # transport helpers
    # ------------------------------------------------------------------
    def _export(self, export_format='csv', actor=None, **body):
        body['format'] = export_format
        return (actor or self._subject).create(_EXPORT_URL, body)

    def _export_csv(self, **body):
        response = self._export(**body)
        self.assertEqual(200, response.status_code)
        return response.text

    @staticmethod
    def _csv(*rows):
        return '\r\n'.join([_CSV_HEADER, *rows]) + '\r\n'

    def _row(self, name=None, asset_type=_DEFAULT_ASSET_TYPE_NAME, criticality='high', owner='IT Ops'):
        return f'{name or self._asset_name},{asset_type},{criticality},{owner}'

    @staticmethod
    def _tagged_csv(*rows):
        return '\r\n'.join([_CSV_HEADER_WITH_TAGS, *rows]) + '\r\n'

    def _tagged_row(self, name=None, asset_type=_DEFAULT_ASSET_TYPE_NAME, criticality='high',
                    tags=''):
        # Quoted so a multi-tag string keeps its commas instead of
        # splitting into extra CSV columns.
        return f'{name or self._asset_name},{asset_type},{criticality},"{tags}"'

    def _registered_tags(self, search):
        response = self._subject.get(_TAGS_URL, {'tag_title': search, 'per_page': 100}).json()
        return [tag['tag_title'] for tag in response['data']]

    def _inspect(self, payload, actor=None, client_id=IRIS_INITIAL_CUSTOMER_IDENTIFIER,
                 import_format='csv'):
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        filename = f'assets.{import_format}'
        content_type = 'application/json' if import_format == 'json' else 'text/csv'
        return (actor or self._subject).post_multipart_encoded_files(
            _INSPECT_URL,
            {'client_id': client_id, 'format': import_format},
            {'file': (filename, io.BytesIO(payload), content_type)},
        )

    def _stage(self, payload, **kwargs):
        response = self._inspect(payload, **kwargs)
        self.assertEqual(200, response.status_code)
        return response.json()

    def _apply(self, token, actor=None, **body):
        body['staging_token'] = token
        return (actor or self._subject).create(_IMPORT_URL, body)

    def _import(self, payload, on_conflict='skip', **kwargs):
        token = self._stage(payload, **kwargs)['staging_token']
        response = self._apply(token, on_conflict=on_conflict)
        self.assertEqual(200, response.status_code)
        return response.json()

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    def test_export_should_return_a_csv_attachment(self):
        self._create()

        response = self._export()

        self.assertIn('text/csv', response.headers['Content-Type'])

    def test_export_should_refuse_content_type_sniffing(self):
        # Without `nosniff` a browser may decide a CSV of attacker-chosen
        # hostnames is HTML and render it inside the IRIS origin.
        self._create()

        response = self._export()

        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])

    def test_export_should_return_an_attachment_disposition(self):
        self._create()

        response = self._export()

        self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_export_should_include_the_asset_name(self):
        self._create()

        self.assertIn(self._asset_name, self._export_csv())

    def test_export_should_quote_every_cell(self):
        # QUOTE_ALL: a value holding a delimiter, a quote or a newline
        # cannot break out of its cell, and a reader never has to guess
        # whether an empty cell meant empty string or NULL.
        self._create()

        header_line = self._export_csv().splitlines()[0].lstrip('﻿')

        self.assertEqual(
            '"client_name","name","asset_type","description","criticality","environment",'
            '"owner","location","tags","ip","domain","is_active","source","custom_attributes"',
            header_line,
        )

    def test_export_should_prefix_a_cell_that_starts_with_an_equals_sign(self):
        name = f'=SRV-{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"\'{name}"', self._export_csv())

    def test_export_should_prefix_a_cell_that_starts_with_a_plus_sign(self):
        name = f'+SRV-{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"\'{name}"', self._export_csv())

    def test_export_should_prefix_a_cell_that_starts_with_a_minus_sign(self):
        name = f'-SRV-{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"\'{name}"', self._export_csv())

    def test_export_should_prefix_a_cell_that_starts_with_an_at_sign(self):
        name = f'@SRV-{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"\'{name}"', self._export_csv())

    def test_export_should_prefix_a_cell_that_starts_with_a_tab(self):
        # Excel strips a leading tab and then evaluates what followed, so
        # it belongs in the same set as `=`.
        name = f'\t=SRV-{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"\'{name}"', self._export_csv())

    def test_export_should_not_break_a_cell_containing_a_comma(self):
        name = f'SRV,{uuid4().hex[:12]}'
        self._create(name=name)

        self.assertIn(f'"{name}"', self._export_csv())

    def test_export_should_only_return_assets_of_accessible_customers(self):
        other_customer = self._subject.create_dummy_customer()
        self._create(client_identifier=other_customer)

        reader = self._user(IRIS_PERMISSION_ASSET_MANAGER_READ)
        response = self._export(actor=reader)

        self.assertNotIn(self._asset_name, response.text)

    def test_export_should_apply_the_supplied_filters(self):
        self._create(criticality='low')
        self._create(name=f'OTHER-{uuid4().hex[:12]}', criticality='critical')

        payload = self._export_csv(filters={'criticality': ['critical']})

        self.assertNotIn(self._asset_name, payload)

    def test_export_should_return_400_for_a_malformed_filter(self):
        response = self._export(filters={'criticality': 'critical'})

        self.assertEqual(400, response.status_code)

    def test_export_should_return_400_for_an_unsupported_format(self):
        response = self._export(export_format='xlsx')

        self.assertEqual(400, response.status_code)

    def test_export_should_return_403_without_the_read_permission(self):
        user = self._subject.create_dummy_user()

        response = self._export(actor=user)

        self.assertEqual(403, response.status_code)

    def test_export_should_return_a_json_document_when_asked(self):
        self._create()

        document = json.loads(self._export(export_format='json').text)

        self.assertEqual([self._asset_name], [asset['name'] for asset in document['assets']])

    def test_export_should_produce_a_file_the_importer_accepts(self):
        # The round trip is the point of the column order: a file exported
        # from one instance has to be importable into another.
        self._create()
        payload = self._export_csv()

        report = self._stage(payload)

        self.assertEqual(1, report['counts']['update'])

    # ------------------------------------------------------------------
    # import — inspect
    # ------------------------------------------------------------------
    def test_import_inspect_should_report_a_create_for_an_unknown_asset(self):
        report = self._stage(self._csv(self._row()))

        self.assertEqual({'create': 1, 'update': 0, 'error': 0}, report['counts'])

    def test_import_inspect_should_report_an_update_for_a_known_asset(self):
        self._create()

        report = self._stage(self._csv(self._row()))

        self.assertEqual(1, report['counts']['update'])

    def test_import_inspect_should_not_write_the_asset(self):
        # A dry run that writes is not a dry run.
        self._stage(self._csv(self._row()))

        self.assertEqual([], self._registry_names())

    def test_import_inspect_should_return_a_staging_token(self):
        report = self._stage(self._csv(self._row()))

        self.assertRegex(report['staging_token'], '^[0-9a-f]{32}$')

    def test_import_inspect_should_report_an_unknown_asset_type(self):
        report = self._stage(self._csv(self._row(asset_type='Flying Saucer')))

        self.assertEqual(['unknown asset type "Flying Saucer"'], report['rows'][0]['errors'])

    def test_import_inspect_should_report_an_unknown_criticality(self):
        report = self._stage(self._csv(self._row(criticality='apocalyptic')))

        self.assertEqual(['unknown criticality "apocalyptic"'], report['rows'][0]['errors'])

    def test_import_inspect_should_report_a_missing_name(self):
        report = self._stage(self._csv(self._row(name='')))

        self.assertEqual(['name is required'], report['rows'][0]['errors'])

    def test_import_inspect_should_report_a_duplicate_row(self):
        # Two rows naming the same asset may disagree on every other
        # field, so the file is ambiguous rather than mergeable.
        report = self._stage(self._csv(self._row(), self._row(owner='Someone else')))

        self.assertEqual(['duplicate of row 1 in the same file'], report['rows'][1]['errors'])

    def test_import_inspect_should_allow_the_same_name_under_a_different_type(self):
        report = self._stage(self._csv(
            self._row(),
            self._row(name=self._asset_name.lower(), asset_type=_OTHER_ASSET_TYPE_NAME),
        ))

        self.assertEqual(2, report['counts']['create'])

    def test_import_inspect_should_report_a_case_insensitive_duplicate(self):
        report = self._stage(self._csv(self._row(), self._row(name=self._asset_name.lower())))

        self.assertEqual('error', report['rows'][1]['action'])

    def test_import_inspect_should_return_400_for_a_file_without_a_name_column(self):
        response = self._inspect('asset_type,owner\r\nAccount,IT Ops\r\n')

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_for_an_empty_file(self):
        response = self._inspect(b'')

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_for_an_archive(self):
        response = self._inspect(_ZIP_BYTES)

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_for_an_unsupported_format(self):
        response = self._inspect(self._csv(self._row()), import_format='zip')

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_for_deeply_nested_json(self):
        # Guarded by scanning the raw text: `json.loads` recurses, so a
        # deeply nested document is a one-request worker crash.
        payload = '{"assets": [{"a": {"b": {"c": {"d": 1}}}}]}'

        response = self._inspect(payload, import_format='json')

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_for_invalid_json(self):
        response = self._inspect('{"assets": [', import_format='json')

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_400_when_no_file_is_supplied(self):
        response = self._subject.create(_INSPECT_URL, {'client_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER})

        self.assertEqual(400, response.status_code)

    def test_import_inspect_should_return_403_for_an_inaccessible_customer(self):
        # 403 rather than 404 here: the caller named the customer, so
        # there is no existence to hide that they did not already state.
        other_customer = self._subject.create_dummy_customer()

        writer = self._writer()
        response = self._inspect(self._csv(self._row()), actor=writer, client_id=other_customer)

        self.assertEqual(403, response.status_code)

    def test_import_inspect_should_return_403_without_the_write_permission(self):
        reader = self._user(IRIS_PERMISSION_ASSET_MANAGER_READ)

        response = self._inspect(self._csv(self._row()), actor=reader)

        self.assertEqual(403, response.status_code)

    # ------------------------------------------------------------------
    # import — apply
    # ------------------------------------------------------------------
    def test_import_should_create_the_reported_assets(self):
        other_name = f'OTHER-{uuid4().hex[:12]}'

        report = self._import(self._csv(self._row(), self._row(name=other_name)))

        self.assertEqual(2, report['created'])

    def test_import_should_register_the_asset_in_the_registry(self):
        self._import(self._csv(self._row()))

        self.assertEqual([self._asset_name], self._registry_names())

    def test_import_should_record_the_import_source(self):
        self._import(self._csv(self._row()))

        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': self._asset_name}).json()
        self.assertEqual('import', response['data'][0]['source'])

    def test_import_should_write_an_import_audit_entry(self):
        self._import(self._csv(self._row()))

        asset = self._subject.get(_MANAGED_ASSETS_URL, {'search': self._asset_name}).json()['data'][0]
        entries = self._subject.get(
            f'{_MANAGED_ASSETS_URL}/{asset["managed_asset_id"]}/audit').json()['data']
        self.assertEqual(['import'], [entry['action'] for entry in entries])

    def test_import_should_register_the_tags_it_writes(self):
        # A CSV is the highest-volume source of tags in the product; an
        # import that skipped the suggestion index would leave the bulk
        # of an inventory's vocabulary uncompletable.
        tag = f'tag-{uuid4().hex[:12]}'

        self._import(self._tagged_csv(self._tagged_row(tags=tag)))

        self.assertIn(tag, self._registered_tags(tag))

    def test_import_should_register_a_tag_shared_by_several_rows_once(self):
        tag = f'tag-{uuid4().hex[:12]}'

        self._import(self._tagged_csv(
            self._tagged_row(tags=tag),
            self._tagged_row(name=f'OTHER-{uuid4().hex[:12]}', tags=tag),
        ))

        self.assertEqual([tag], self._registered_tags(tag))

    def test_import_should_not_register_tags_of_a_skipped_row(self):
        # Skipped means nothing was written, so there is no new
        # vocabulary to index.
        tag = f'tag-{uuid4().hex[:12]}'
        self._create()

        self._import(self._tagged_csv(self._tagged_row(tags=tag)), on_conflict='skip')

        self.assertEqual([], self._registered_tags(tag))

    def test_import_should_register_tags_of_an_updated_row(self):
        tag = f'tag-{uuid4().hex[:12]}'
        self._create()

        self._import(self._tagged_csv(self._tagged_row(tags=tag)), on_conflict='update')

        self.assertIn(tag, self._registered_tags(tag))

    def test_import_should_not_create_a_case_asset(self):
        # The registry is fed *from* cases, never the other way round.
        # A write in this direction would let anyone with
        # `asset_manager_write` plant an asset in an investigation they
        # cannot open.
        case_identifier = self._subject.create_dummy_case()

        self._import(self._csv(self._row()))

        assets = self._subject.get(f'/api/v2/cases/{case_identifier}/assets').json()
        self.assertEqual(0, assets['total'])

    def test_import_should_skip_an_existing_asset_by_default(self):
        self._create(owner='Original owner')

        report = self._import(self._csv(self._row(owner='Overwritten')))

        self.assertEqual(1, report['skipped'])

    def test_import_should_not_overwrite_an_existing_asset_when_skipping(self):
        self._create(owner='Original owner')

        self._import(self._csv(self._row(owner='Overwritten')))

        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': self._asset_name}).json()
        self.assertEqual('Original owner', response['data'][0]['owner'])

    def test_import_should_overwrite_an_existing_asset_when_asked(self):
        self._create(owner='Original owner')

        self._import(self._csv(self._row(owner='Overwritten')), on_conflict='update')

        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': self._asset_name}).json()
        self.assertEqual('Overwritten', response['data'][0]['owner'])

    def test_import_should_report_an_unchanged_row_separately_from_an_update(self):
        self._create(criticality='high', owner='IT Ops')

        report = self._import(self._csv(self._row()), on_conflict='update')

        self.assertEqual(1, report['unchanged'])

    def test_import_should_not_create_the_rows_it_reported_as_errors(self):
        report = self._import(self._csv(self._row(asset_type='Flying Saucer')))

        self.assertEqual((0, 1), (report['created'], report['errors']))

    def test_import_should_accept_the_json_format(self):
        payload = json.dumps({'assets': [{
            'name': self._asset_name,
            'asset_type': _DEFAULT_ASSET_TYPE_NAME,
            'criticality': 'high',
        }]})

        report = self._import(payload, import_format='json')

        self.assertEqual(1, report['created'])

    def test_import_should_ignore_the_customer_named_in_the_file(self):
        # `client_name` round-trips for readability only. Honouring it
        # would let a file retarget itself at a customer the uploader
        # cannot see, making the check at inspect meaningless.
        other_customer = self._subject.create_dummy_customer()
        payload = json.dumps({'assets': [{
            'name': self._asset_name,
            'asset_type': _DEFAULT_ASSET_TYPE_NAME,
            'client_id': other_customer,
            'client_name': 'somebody else',
        }]})

        self._import(payload, import_format='json')

        response = self._subject.get(_MANAGED_ASSETS_URL, {'search': self._asset_name}).json()
        self.assertEqual(IRIS_INITIAL_CUSTOMER_IDENTIFIER, response['data'][0]['client_id'])

    # ------------------------------------------------------------------
    # import — the staging token is a capability
    # ------------------------------------------------------------------
    def test_import_should_return_404_for_a_traversal_token(self):
        response = self._apply('../../etc/passwd')

        self.assertEqual(404, response.status_code)

    def test_import_should_return_404_for_an_encoded_traversal_token(self):
        response = self._apply('%2e%2e%2f%2e%2e')

        self.assertEqual(404, response.status_code)

    def test_import_should_return_404_for_a_non_hexadecimal_token(self):
        response = self._apply('z' * 32)

        self.assertEqual(404, response.status_code)

    def test_import_should_return_404_for_an_unknown_token(self):
        response = self._apply('0' * 32)

        self.assertEqual(404, response.status_code)

    def test_import_should_return_400_when_no_token_is_supplied(self):
        response = self._subject.create(_IMPORT_URL, {})

        self.assertEqual(400, response.status_code)

    def test_import_should_return_400_for_an_unknown_conflict_policy(self):
        token = self._stage(self._csv(self._row()))['staging_token']

        response = self._apply(token, on_conflict='merge')

        self.assertEqual(400, response.status_code)

    def test_import_should_return_404_for_another_users_token(self):
        # Reported as "not found" rather than "forbidden" so a token
        # cannot be probed for existence by someone who does not hold it.
        writer = self._writer()
        token = self._stage(self._csv(self._row()), actor=writer)['staging_token']

        response = self._apply(token, actor=self._writer())

        self.assertEqual(404, response.status_code)

    def test_import_should_return_404_when_the_token_was_already_spent(self):
        token = self._stage(self._csv(self._row()))['staging_token']
        self._apply(token)

        response = self._apply(token)

        self.assertEqual(404, response.status_code)

    def test_import_should_return_404_when_the_token_was_discarded(self):
        token = self._stage(self._csv(self._row()))['staging_token']
        self._subject.delete(f'{_IMPORT_URL}/{token}')

        response = self._apply(token)

        self.assertEqual(404, response.status_code)

    def test_import_discard_should_return_404_for_another_users_token(self):
        writer = self._writer()
        token = self._stage(self._csv(self._row()), actor=writer)['staging_token']

        response = self._writer().delete(f'{_IMPORT_URL}/{token}')

        self.assertEqual(404, response.status_code)

    def test_import_should_return_403_when_customer_access_is_revoked_after_the_inspect(self):
        # Group membership can change between the two calls. The apply
        # re-authorises against the customer pinned at inspect rather
        # than trusting either the body or the earlier decision.
        writer = self._writer()
        token = self._stage(self._csv(self._row()), actor=writer)['staging_token']
        self._subject.create(
            f'/manage/users/{writer.get_identifier()}/customers/update',
            {'customers_membership': []},
        )

        response = self._apply(token, actor=writer)

        self.assertEqual(403, response.status_code)

    def test_import_should_not_write_anything_when_access_is_revoked_after_the_inspect(self):
        writer = self._writer()
        token = self._stage(self._csv(self._row()), actor=writer)['staging_token']
        self._subject.create(
            f'/manage/users/{writer.get_identifier()}/customers/update',
            {'customers_membership': []},
        )

        self._apply(token, actor=writer)

        self.assertEqual([], self._registry_names())

    def test_import_should_return_403_without_the_write_permission(self):
        token = self._stage(self._csv(self._row()))['staging_token']

        response = self._apply(token, actor=self._user(IRIS_PERMISSION_ASSET_MANAGER_READ))

        self.assertEqual(403, response.status_code)
