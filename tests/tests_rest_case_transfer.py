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

"""
Integration tests for `/api/v2/cases/<id>/export` and `/api/v2/cases/import*`.

Exercised against the docker-compose stack through the `Iris` helper. There is
only one instance available here, so a "transfer" is an export followed by an
import back into the same deployment. That still covers what matters: the
importer never sees the source rows, only the bundle, so the only way the
imported case can come out right is if every foreign key really was rewritten
through the ref map.

Where a scenario needs a target that genuinely lacks the source's users, the
bundle's `principals.json` is rewritten in place so the identities miss on every
matching axis — the same thing that happens naturally between two deployments.

The archive size cap is not exercised here: reaching it would mean generating a
multi-gigabyte upload. `source/tests/app/business/case_transfer/test_manifest.py`
covers it directly against `BundleReader`, which is where the cap is enforced.
"""

import hashlib
import io
import json
import uuid
import zipfile
from unittest import TestCase

from iris import Iris
from iris import ADMINISTRATOR_USER_IDENTIFIER

# `Permissions.standard_user` — enough to reach the import endpoints, not enough
# to mint placeholder accounts.
_IRIS_PERMISSION_STANDARD_USER = 0x1

_PASSPHRASE = 'correct horse battery staple'
_BLOB_CONTENT = b'\x00\x01forensic artefact bytes\xff' * 512
_BLOB_NAME = 'artefact.bin'

_PRINCIPALS_ENTRY = 'principals.json'
_LOOKUPS_ENTRY = 'lookups.json'


class TestsRestCaseTransfer(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    # ------------------------------------------------------------------
    # building a case worth transferring
    # ------------------------------------------------------------------
    def _datastore_root(self, case_identifier):
        tree = self._subject.get(f'/api/v2/cases/{case_identifier}/datastore/tree').json()
        return int(next(iter(tree)).split('-')[1])

    def _build_rich_case(self, assignee):
        """A case touching every relationship the bundle has to preserve.

        Returned as a dict of source ids so the assertions can look the same
        objects up again after the round trip.
        """
        case_identifier = self._subject.create_dummy_case()
        self._subject.update(f'/api/v2/cases/{case_identifier}',
                             {'case_tags': 'ransomware,exfiltration'})

        timeline = self._subject.create(
            f'/api/v2/cases/{case_identifier}/timelines',
            {'name': 'Attacker activity', 'color': '#ff0000'}
        ).json()

        asset = self._subject.create(
            f'/api/v2/cases/{case_identifier}/assets',
            {'asset_type_id': 1, 'asset_name': 'DC01', 'asset_description': 'domain controller'}
        ).json()
        second_asset = self._subject.create(
            f'/api/v2/cases/{case_identifier}/assets',
            {'asset_type_id': 1, 'asset_name': 'WKS42'}
        ).json()

        ioc = self._subject.create(
            f'/api/v2/cases/{case_identifier}/iocs',
            {'ioc_type_id': 1, 'ioc_tlp_id': 2, 'ioc_value': '8.8.8.8',
             'ioc_description': 'c2', 'ioc_tags': 'c2'}
        ).json()
        second_ioc = self._subject.create(
            f'/api/v2/cases/{case_identifier}/iocs',
            {'ioc_type_id': 2, 'ioc_tlp_id': 2, 'ioc_value': 'evil.example',
             'ioc_description': 'staging', 'ioc_tags': ''}
        ).json()

        # `event_sync_iocs_assets` is what creates the ioc↔asset link rows, which
        # are the relationship most likely to be silently lost in a transfer.
        linked_event = self._subject.create(
            f'/api/v2/cases/{case_identifier}/events',
            {'event_title': 'Initial access', 'event_content': 'phishing',
             'event_category_id': 1, 'event_date': '2025-03-26T00:00:00.000',
             'event_tz': '+00:00', 'event_assets': [asset['asset_id']],
             'event_iocs': [ioc['ioc_id']], 'event_sync_iocs_assets': True,
             'event_in_summary': True, 'event_in_graph': True,
             'timeline_ids': [timeline['timeline_id']]}
        ).json()
        default_event = self._subject.create(
            f'/api/v2/cases/{case_identifier}/events',
            {'event_title': 'Lateral movement', 'event_content': '',
             'event_category_id': 1, 'event_date': '2025-03-27T00:00:00.000',
             'event_tz': '+00:00', 'event_assets': [second_asset['asset_id']],
             'event_iocs': [], 'event_in_summary': False, 'event_in_graph': False}
        ).json()

        task = self._subject.create(
            f'/api/v2/cases/{case_identifier}/tasks',
            {'task_assignees_id': [assignee.get_identifier()], 'task_status_id': 1,
             'task_title': 'Collect triage package', 'task_description': 'on DC01'}
        ).json()

        directory = self._subject.create(
            f'/api/v2/cases/{case_identifier}/notes-directories',
            {'name': 'Analysis'}
        ).json()
        note = self._subject.create(
            f'/api/v2/cases/{case_identifier}/notes',
            {'directory_id': directory['id'], 'note_title': 'Timeline draft',
             'note_content': '# findings\n\nthe attacker used DC01'}
        ).json()

        evidence = self._subject.create(
            f'/api/v2/cases/{case_identifier}/evidences',
            {'filename': 'memory.raw', 'file_size': 4096,
             'file_hash': hashlib.sha256(b'memory').hexdigest()}
        ).json()

        self._subject.create(f'/api/v2/iocs/{ioc["ioc_id"]}/comments',
                             {'comment_text': 'seen in three other cases'})
        self._subject.create(f'/api/v2/tasks/{task["id"]}/comments',
                             {'comment_text': 'blocked on legal'})

        folder = self._subject.create(
            f'/api/v2/cases/{case_identifier}/datastore/folders',
            {'parent_node': self._datastore_root(case_identifier), 'folder_name': 'Collected'}
        ).json()
        datastore_file = self._subject.post_multipart_encoded_files(
            f'/api/v2/cases/{case_identifier}/datastore/folders/{folder["path_id"]}/files',
            {'file_original_name': _BLOB_NAME, 'file_description': 'triage output'},
            {'file_content': (_BLOB_NAME, io.BytesIO(_BLOB_CONTENT), 'application/octet-stream')}
        ).json()

        return {
            'case_id': case_identifier,
            'timeline_id': timeline['timeline_id'],
            'asset_id': asset['asset_id'],
            'ioc_id': ioc['ioc_id'],
            'second_ioc_id': second_ioc['ioc_id'],
            'linked_event_id': linked_event['event_id'],
            'default_event_id': default_event['event_id'],
            'task_id': task['id'],
            'directory_id': directory['id'],
            'note_id': note['note_id'],
            'evidence_id': evidence['id'],
            'folder_id': folder['path_id'],
            'file_id': datastore_file['file_id'],
        }

    # ------------------------------------------------------------------
    # transport helpers
    # ------------------------------------------------------------------
    def _export(self, case_identifier, **body):
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/export', body)
        self.assertEqual(200, response.status_code)
        return response.content

    def _inspect(self, archive, passphrase=None, actor=None):
        actor = actor or self._subject
        data = {'passphrase': passphrase} if passphrase else {}
        return actor.post_multipart_encoded_files(
            '/api/v2/cases/import/inspect',
            data,
            {'archive': ('bundle.iris', io.BytesIO(archive), 'application/octet-stream')}
        )

    def _stage(self, archive, passphrase=None):
        response = self._inspect(archive, passphrase)
        self.assertEqual(200, response.status_code)
        return response.json()

    def _apply(self, token, actor=None, **body):
        actor = actor or self._subject
        body['staging_token'] = token
        return actor.create('/api/v2/cases/import', body)

    def _import(self, archive, passphrase=None, **body):
        summary = self._stage(archive, passphrase)
        response = self._apply(summary['staging_token'], **body)
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    # ------------------------------------------------------------------
    # bundle surgery — standing in for a target that never knew these users
    # ------------------------------------------------------------------
    @staticmethod
    def _read_entry(archive, name):
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            return json.loads(bundle.read(name))

    @staticmethod
    def _rewrite(archive, mutate):
        """Rebuild the zip, passing every entry through `mutate(name, data)`.

        Returning `None` drops the entry; returning bytes replaces it.
        """
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
                for name in source.namelist():
                    replacement = mutate(name, source.read(name))
                    if replacement is not None:
                        target.writestr(name, replacement)
        return output.getvalue()

    def _orphan_principal(self, archive, login):
        """Make the principal with this login miss on uuid, external_id, email and login."""
        principals = self._read_entry(archive, _PRINCIPALS_ENTRY)
        orphaned_ref = None

        for principal in principals:
            if principal.get('login') != login:
                continue
            orphaned_ref = principal['ref']
            marker = uuid.uuid4().hex
            principal['uuid'] = str(uuid.uuid4())
            principal['external_id'] = None
            principal['email'] = f'{marker}@absent.invalid'
            principal['login'] = f'absent-{marker}'

        self.assertIsNotNone(orphaned_ref, f'no principal with login {login} in the bundle')

        def mutate(name, data):
            return json.dumps(principals).encode('utf8') if name == _PRINCIPALS_ENTRY else data

        return self._rewrite(archive, mutate), orphaned_ref

    # ------------------------------------------------------------------
    # reading a case back
    # ------------------------------------------------------------------
    @staticmethod
    def _total(payload):
        if isinstance(payload, list):
            return len(payload)
        if 'total' in payload:
            return payload['total']
        return len(payload.get('data') or [])

    def _events(self, case_identifier):
        # The events endpoint answers with the timeline payload the SPA renders
        # (`tim` plus its filter caches), not a paginated envelope.
        return self._subject.get(f'/api/v2/cases/{case_identifier}/events').json()['tim']

    def _event(self, case_identifier, title):
        for event in self._events(case_identifier):
            if event['event_title'] == title:
                return event
        self.fail(f'no event titled {title} in case {case_identifier}')

    def _shape(self, case_identifier):
        get = self._subject.get
        return {
            'assets': self._total(get(f'/api/v2/cases/{case_identifier}/assets').json()),
            'iocs': self._total(get(f'/api/v2/cases/{case_identifier}/iocs').json()),
            'events': len(self._events(case_identifier)),
            'tasks': self._total(get(f'/api/v2/cases/{case_identifier}/tasks').json()),
            'notes': self._total(get(f'/api/v2/cases/{case_identifier}/notes').json()),
            'evidences': self._total(get(f'/api/v2/cases/{case_identifier}/evidences').json()),
            'timelines': self._total(get(f'/api/v2/cases/{case_identifier}/timelines').json()),
            'datastore': self._total(get(f'/api/v2/cases/{case_identifier}/datastore/files').json()),
        }

    def _first(self, case_identifier, collection, field, value):
        payload = self._subject.get(f'/api/v2/cases/{case_identifier}/{collection}').json()
        rows = payload if isinstance(payload, list) else (payload.get('data') or [])
        for row in rows:
            if row.get(field) == value:
                return row
        self.fail(f'no {collection} with {field}={value} in case {case_identifier}')

    def _datastore_file_id(self, case_identifier):
        files = self._subject.get(f'/api/v2/cases/{case_identifier}/datastore/files').json()
        return files['data'][0]['file_id']

    def _case_count(self):
        cases = self._subject.get('/api/v2/cases', query_parameters={'per_page': 1000000000}).json()
        return len(cases['data'])

    def _user_count(self):
        return len(self._subject.get('/manage/users/list').json()['data'])

    # ==================================================================
    # 1. round trip
    # ==================================================================
    def test_export_should_return_an_archive(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/export', {})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.content.startswith(b'PK'))

    def test_export_should_return_404_when_case_does_not_exist(self):
        response = self._subject.create('/api/v2/cases/123456789/export', {})
        self.assertEqual(404, response.status_code)

    def test_export_should_return_403_when_user_has_no_case_access(self):
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        response = user.create(f'/api/v2/cases/{case_identifier}/export', {})
        self.assertEqual(403, response.status_code)

    def test_round_trip_should_preserve_every_entity_count(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        expected = self._shape(source['case_id'])

        imported = self._import(self._export(source['case_id']))

        self.assertEqual(expected, self._shape(imported['case_id']))

    def test_round_trip_should_create_a_distinct_case(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))
        self.assertNotEqual(source['case_id'], imported['case_id'])

    def test_round_trip_should_rewrite_the_case_name_prefix(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))
        self.assertTrue(imported['name'].startswith(f'#{imported["case_id"]} - '))

    def test_round_trip_should_preserve_the_case_tags(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))
        tags = {tag['tag_title'] for tag in imported['tags']}
        self.assertEqual({'ransomware', 'exfiltration'}, tags)

    def test_round_trip_should_preserve_the_ioc_asset_link(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        ioc = self._first(imported['case_id'], 'iocs', 'ioc_value', '8.8.8.8')
        detail = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/iocs/{ioc["ioc_id"]}').json()
        self.assertEqual(['DC01'], [asset['asset_name'] for asset in detail['link']])

    def test_round_trip_should_keep_the_event_on_its_own_timeline(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        timelines = self._subject.get(f'/api/v2/cases/{imported["case_id"]}/timelines').json()
        attacker = next(t for t in timelines if t['name'] == 'Attacker activity')

        event = self._event(imported['case_id'], 'Initial access')
        self.assertEqual([attacker['timeline_id']], event['timeline_ids'])

    def test_round_trip_should_keep_the_event_linked_to_its_asset_and_ioc(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        event = self._event(imported['case_id'], 'Initial access')

        self.assertEqual(['DC01'], [asset['asset_name'] for asset in event['assets']])
        self.assertEqual(['8.8.8.8'], [ioc['ioc_value'] for ioc in event['iocs']])

    def test_round_trip_should_keep_the_note_in_its_directory(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        directories = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/notes-directories').json()
        rows = directories if isinstance(directories, list) else directories.get('data') or []
        analysis = next(row for row in rows if row['name'] == 'Analysis')

        note = self._first(imported['case_id'], 'notes', 'note_title', 'Timeline draft')
        detail = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/notes/{note["note_id"]}').json()
        self.assertEqual(analysis['id'], detail['directory_id'])

    def test_round_trip_should_preserve_the_note_content(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        note = self._first(imported['case_id'], 'notes', 'note_title', 'Timeline draft')
        detail = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/notes/{note["note_id"]}').json()
        self.assertIn('the attacker used DC01', detail['note_content'])

    def test_round_trip_should_preserve_comments(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        ioc = self._first(imported['case_id'], 'iocs', 'ioc_value', '8.8.8.8')
        comments = self._subject.get(f'/api/v2/iocs/{ioc["ioc_id"]}/comments').json()
        texts = [row['comment_text'] for row in (comments.get('data') or comments)]
        self.assertEqual(['seen in three other cases'], texts)

    def test_round_trip_should_restore_the_datastore_blob_byte_for_byte(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        file_identifier = self._datastore_file_id(imported['case_id'])
        response = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/datastore/files/{file_identifier}')
        self.assertEqual(_BLOB_CONTENT, response.content)

    def test_round_trip_should_give_the_imported_file_its_own_storage(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id']))

        original = self._subject.get(
            f'/api/v2/cases/{source["case_id"]}/datastore/files/{source["file_id"]}/info').json()
        copy = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/datastore/files/'
            f'{self._datastore_file_id(imported["case_id"])}/info').json()
        self.assertNotEqual(original['file_uuid'], copy['file_uuid'])

    def test_round_trip_should_record_the_source_case_uuid(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        original = self._subject.get(f'/api/v2/cases/{source["case_id"]}').json()

        imported = self._import(self._export(source['case_id']))

        entry = Iris.get_most_recent_object_history_entry(imported)
        self.assertEqual(original['case_uuid'], entry['source_case_uuid'])

    def test_round_trip_should_regenerate_the_case_uuid(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        original = self._subject.get(f'/api/v2/cases/{source["case_id"]}').json()

        imported = self._import(self._export(source['case_id']))

        self.assertNotEqual(original['case_uuid'], imported['case_uuid'])

    def test_import_without_blobs_should_keep_the_datastore_metadata(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id'], include_blobs=False))

        files = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/datastore/files').json()
        self.assertEqual(1, files['total'])

    def test_import_without_blobs_should_leave_the_file_downloadable_as_a_virtual_entry(self):
        # The row has to carry a real path on this instance even with no bytes
        # behind it — `file_local_name` is NOT NULL, and a placeholder would
        # escape the datastore root the download endpoint confines files to.
        source = self._build_rich_case(self._subject.create_dummy_user())
        imported = self._import(self._export(source['case_id'], include_blobs=False))

        response = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/datastore/files/'
            f'{self._datastore_file_id(imported["case_id"])}')

        self.assertEqual(400, response.status_code)
        self.assertIn(f'case-{imported["case_id"]}', response.json()['message'])

    def test_inspect_without_blobs_should_warn_that_content_is_missing(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id'], include_blobs=False))
        self.assertTrue(any('metadata only' in warning for warning in summary['warnings']))

    def test_inspect_should_report_the_entity_counts(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id']))
        self.assertEqual(2, summary['counts']['ioc'])

    def test_inspect_should_report_the_datastore_blob_count(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id']))
        self.assertEqual(1, summary['datastore_blobs'])

    def test_inspect_should_not_create_a_case(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        before = self._case_count()
        self._stage(self._export(source['case_id']))
        self.assertEqual(before, self._case_count())

    def test_discarding_a_staged_archive_should_return_204(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id']))
        response = self._subject.delete(f'/api/v2/cases/import/{summary["staging_token"]}')
        self.assertEqual(204, response.status_code)

    def test_applying_a_discarded_token_should_return_404(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id']))
        self._subject.delete(f'/api/v2/cases/import/{summary["staging_token"]}')

        response = self._apply(summary['staging_token'])
        self.assertEqual(404, response.status_code)

    def test_applying_an_unknown_token_should_return_404(self):
        response = self._apply('0' * 32)
        self.assertEqual(404, response.status_code)

    def test_applying_someone_elses_token_should_return_404(self):
        # Reported as not-found rather than forbidden: a token is a capability,
        # and confirming one exists is itself a leak.
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._export(source['case_id']))

        other = self._subject.create_dummy_user([_IRIS_PERMISSION_STANDARD_USER])
        response = self._apply(summary['staging_token'], actor=other)
        self.assertEqual(404, response.status_code)

    def test_import_should_return_400_without_a_staging_token(self):
        response = self._subject.create('/api/v2/cases/import', {})
        self.assertEqual(400, response.status_code)

    # ==================================================================
    # 2. missing users
    # ==================================================================
    def test_inspect_should_match_a_user_that_exists_on_the_target(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)

        summary = self._stage(self._export(source['case_id']))

        matched = [entry for entry in summary['principals'] if entry['matched_user_id']]
        self.assertEqual(len(summary['principals']), len(matched))

    def test_inspect_should_report_an_unknown_user_as_unresolved(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        summary = self._stage(archive)

        entry = next(item for item in summary['principals'] if item['ref'] == orphaned_ref)
        self.assertIsNone(entry['matched_user_id'])

    def test_inspect_should_report_how_often_a_principal_is_referenced(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        summary = self._stage(archive)

        entry = next(item for item in summary['principals'] if item['ref'] == orphaned_ref)
        self.assertGreaterEqual(entry['reference_count'], 1)

    def test_mapping_an_unknown_user_should_reassign_their_rows(self):
        assignee = self._subject.create_dummy_user()
        substitute = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'map', 'target_user_id': substitute.get_identifier()}
        })

        task = self._first(imported['case_id'], 'tasks', 'task_title', 'Collect triage package')
        self.assertEqual([substitute.get_identifier()], task['task_assignees_id'])

    def test_mapping_to_an_unknown_target_user_should_return_400(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())
        summary = self._stage(archive)

        response = self._apply(summary['staging_token'], principal_mapping={
            orphaned_ref: {'action': 'map', 'target_user_id': 123456789}
        })
        self.assertEqual(400, response.status_code)

    def test_attributing_an_unknown_user_to_the_importer_should_reassign_their_rows(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'importer'}
        })

        task = self._first(imported['case_id'], 'tasks', 'task_title', 'Collect triage package')
        self.assertEqual([ADMINISTRATOR_USER_IDENTIFIER], task['task_assignees_id'])

    def test_attributing_to_the_importer_should_record_the_original_identity(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())
        principals = self._read_entry(archive, _PRINCIPALS_ENTRY)
        orphaned_login = next(entry['login'] for entry in principals
                              if entry['ref'] == orphaned_ref)

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'importer'}
        })

        entry = Iris.get_most_recent_object_history_entry(imported)
        self.assertIn(orphaned_login, entry['attributed_to_importer'])

    def test_an_unresolved_principal_with_no_decision_should_fall_back_to_the_importer(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive)

        report = next(entry for entry in imported['import_report']['principals']
                      if entry['ref'] == orphaned_ref)
        self.assertEqual('importer', report['action'])

    def test_creating_a_placeholder_should_report_it_as_created(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'placeholder'}
        })

        report = next(entry for entry in imported['import_report']['principals']
                      if entry['ref'] == orphaned_ref)
        self.assertTrue(report['created'])

    def test_a_placeholder_should_own_the_rows_of_the_user_it_stands_in_for(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'placeholder'}
        })

        report = next(entry for entry in imported['import_report']['principals']
                      if entry['ref'] == orphaned_ref)
        task = self._first(imported['case_id'], 'tasks', 'task_title', 'Collect triage package')
        self.assertEqual([report['target_user_id']], task['task_assignees_id'])

    def test_a_placeholder_should_be_created_inactive(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        imported = self._import(archive, principal_mapping={
            orphaned_ref: {'action': 'placeholder'}
        })

        report = next(entry for entry in imported['import_report']['principals']
                      if entry['ref'] == orphaned_ref)
        user = self._subject.get(f'/api/v2/manage/users/{report["target_user_id"]}').json()
        self.assertFalse(user['user_active'])

    def test_a_second_import_should_reuse_the_same_placeholder(self):
        # The whole point of stamping `external_id` with the source uuid: a
        # second case from the same instance must not mint a twin.
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        first = self._import(archive, principal_mapping={orphaned_ref: {'action': 'placeholder'}})
        second = self._import(archive, principal_mapping={orphaned_ref: {'action': 'placeholder'}})

        first_report = next(entry for entry in first['import_report']['principals']
                            if entry['ref'] == orphaned_ref)
        second_report = next(entry for entry in second['import_report']['principals']
                             if entry['ref'] == orphaned_ref)
        self.assertEqual(first_report['target_user_id'], second_report['target_user_id'])

    def test_a_second_import_should_not_report_the_placeholder_as_created_again(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        self._import(archive, principal_mapping={orphaned_ref: {'action': 'placeholder'}})
        second = self._import(archive, principal_mapping={orphaned_ref: {'action': 'placeholder'}})

        report = next(entry for entry in second['import_report']['principals']
                      if entry['ref'] == orphaned_ref)
        self.assertFalse(report['created'])

    def test_an_unknown_principal_action_should_return_400(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())
        summary = self._stage(archive)

        response = self._apply(summary['staging_token'],
                               principal_mapping={orphaned_ref: {'action': 'invent'}})
        self.assertEqual(400, response.status_code)

    # ==================================================================
    # 3. placeholder authorisation
    # ==================================================================
    def test_a_non_administrator_should_not_be_allowed_to_create_placeholders(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        analyst = self._subject.create_dummy_user([_IRIS_PERMISSION_STANDARD_USER])
        staged = self._inspect(archive, actor=analyst).json()

        response = self._apply(staged['staging_token'], actor=analyst,
                               principal_mapping={orphaned_ref: {'action': 'placeholder'}})
        self.assertEqual(403, response.status_code)

    def test_a_refused_placeholder_should_not_create_a_user(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._export(source['case_id']), assignee.get_login())

        analyst = self._subject.create_dummy_user([_IRIS_PERMISSION_STANDARD_USER])
        staged = self._inspect(archive, actor=analyst).json()
        before = self._user_count()

        self._apply(staged['staging_token'], actor=analyst,
                    principal_mapping={orphaned_ref: {'action': 'placeholder'}})
        self.assertEqual(before, self._user_count())

    # ==================================================================
    # 4. reference data
    # ==================================================================
    def test_import_should_create_reference_data_that_is_missing(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        renamed = f'ioc-type-{uuid.uuid4().hex[:8]}'
        lookups['ioc_type'][0]['name'] = renamed

        archive = self._rewrite(
            archive,
            lambda name, data: json.dumps(lookups).encode('utf8')
            if name == _LOOKUPS_ENTRY else data)

        imported = self._import(archive)

        created = [entry['name'] for entry in imported['import_report']['created_lookups']]
        self.assertIn(renamed, created)

    def test_import_should_create_a_tag_that_is_missing(self):
        # Tags get their own case: `Tags` is the one reference-data model that
        # declares a constructor of its own, so it is the one that breaks if the
        # importer builds rows with a bare `Model()`. A same-instance round trip
        # never reaches that path — the tag always matches by name — so the
        # bundle has to name one this deployment does not have.
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        renamed = f'tag-{uuid.uuid4().hex[:8]}'
        lookups['tag'][0]['name'] = renamed

        archive = self._rewrite(
            archive,
            lambda name, data: json.dumps(lookups).encode('utf8')
            if name == _LOOKUPS_ENTRY else data)

        imported = self._import(archive)

        created = [entry['name'] for entry in imported['import_report']['created_lookups']]
        self.assertIn(renamed, created)
        self.assertIn(renamed, {tag['tag_title'] for tag in imported['tags']})

    def test_inspect_should_flag_reference_data_it_would_create(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        lookups['ioc_type'][0]['name'] = f'ioc-type-{uuid.uuid4().hex[:8]}'
        archive = self._rewrite(
            archive,
            lambda name, data: json.dumps(lookups).encode('utf8')
            if name == _LOOKUPS_ENTRY else data)

        summary = self._stage(archive)

        self.assertTrue(any(entry['will_create'] for entry in summary['lookups']['ioc_type']))

    def test_a_lookup_decision_should_map_instead_of_creating(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        entry = next(item for item in lookups['ioc_type'] if item['name'])
        entry['name'] = f'ioc-type-{uuid.uuid4().hex[:8]}'
        archive = self._rewrite(
            archive,
            lambda name, data: json.dumps(lookups).encode('utf8')
            if name == _LOOKUPS_ENTRY else data)

        imported = self._import(archive, lookup_decisions={
            entry['ref']: {'action': 'map', 'target_id': 1}
        })

        created = [item['name'] for item in imported['import_report']['created_lookups']]
        self.assertNotIn(entry['name'], created)

    def test_a_lookup_decision_pointing_nowhere_should_return_400(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])
        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        summary = self._stage(archive)

        response = self._apply(summary['staging_token'], lookup_decisions={
            lookups['ioc_type'][0]['ref']: {'action': 'map', 'target_id': 123456789}
        })
        self.assertEqual(400, response.status_code)

    def test_import_should_honour_a_customer_override(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        customer_identifier = self._subject.create_dummy_customer()

        imported = self._import(self._export(source['case_id']),
                                customer_id=customer_identifier)

        self.assertEqual(customer_identifier, imported['client']['customer_id'])

    # ==================================================================
    # 5. security
    # ==================================================================
    def test_inspect_should_reject_an_archive_with_a_traversal_entry(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive)) as original:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as tampered:
                for name in original.namelist():
                    tampered.writestr(name, original.read(name))
                tampered.writestr('../../etc/passwd', 'root:x:0:0')

        response = self._inspect(output.getvalue())
        self.assertEqual(400, response.status_code)

    def test_inspect_should_reject_a_blob_entry_that_is_not_a_uuid(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive)) as original:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as tampered:
                for name in original.namelist():
                    tampered.writestr(name, original.read(name))
                tampered.writestr('datastore/../../escape', 'nope')

        response = self._inspect(output.getvalue())
        self.assertEqual(400, response.status_code)

    def test_inspect_should_reject_a_file_that_is_not_an_archive(self):
        response = self._inspect(b'this is not a zip file')
        self.assertEqual(400, response.status_code)

    def test_inspect_should_reject_an_empty_upload(self):
        response = self._inspect(b'')
        self.assertEqual(400, response.status_code)

    def test_inspect_should_return_400_without_an_archive_field(self):
        response = self._subject.create('/api/v2/cases/import/inspect', {})
        self.assertEqual(400, response.status_code)

    def _archive_with_corrupted_blob(self, case_identifier):
        archive = self._export(case_identifier)

        def mutate(name, data):
            return b'not the bytes the manifest describes' if name.startswith('datastore/') else data

        return self._rewrite(archive, mutate)

    def test_a_blob_that_does_not_match_its_digest_should_abort_the_import(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._archive_with_corrupted_blob(source['case_id']))

        response = self._apply(summary['staging_token'])
        self.assertEqual(400, response.status_code)

    # ==================================================================
    # 6. atomicity
    # ==================================================================
    def test_a_failed_import_should_not_leave_a_case_behind(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        summary = self._stage(self._archive_with_corrupted_blob(source['case_id']))
        before = self._case_count()

        self._apply(summary['staging_token'])

        self.assertEqual(before, self._case_count())

    def test_a_failed_import_should_not_leave_placeholder_users_behind(self):
        assignee = self._subject.create_dummy_user()
        source = self._build_rich_case(assignee)
        archive, orphaned_ref = self._orphan_principal(
            self._archive_with_corrupted_blob(source['case_id']), assignee.get_login())
        summary = self._stage(archive)
        before = self._user_count()

        self._apply(summary['staging_token'],
                    principal_mapping={orphaned_ref: {'action': 'placeholder'}})

        self.assertEqual(before, self._user_count())

    def test_a_failed_import_should_not_leave_reference_data_behind(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._archive_with_corrupted_blob(source['case_id'])

        lookups = self._read_entry(archive, _LOOKUPS_ENTRY)
        renamed = f'ioc-type-{uuid.uuid4().hex[:8]}'
        lookups['ioc_type'][0]['name'] = renamed
        archive = self._rewrite(
            archive,
            lambda name, data: json.dumps(lookups).encode('utf8')
            if name == _LOOKUPS_ENTRY else data)

        summary = self._stage(archive)
        self._apply(summary['staging_token'])

        types = self._subject.get('/manage/ioc-types/list').json()
        self.assertNotIn(renamed, [row['type_name'] for row in types['data']])

    # ==================================================================
    # 7. encryption
    # ==================================================================
    def test_an_encrypted_export_should_not_be_a_zip(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)
        self.assertTrue(archive.startswith(b'IRISENC1'))

    def test_an_encrypted_round_trip_should_produce_the_same_case(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        expected = self._shape(source['case_id'])

        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)
        imported = self._import(archive, passphrase=_PASSPHRASE)

        self.assertEqual(expected, self._shape(imported['case_id']))

    def test_an_encrypted_round_trip_should_restore_the_blob(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)
        imported = self._import(archive, passphrase=_PASSPHRASE)

        file_identifier = self._datastore_file_id(imported['case_id'])
        response = self._subject.get(
            f'/api/v2/cases/{imported["case_id"]}/datastore/files/{file_identifier}')
        self.assertEqual(_BLOB_CONTENT, response.content)

    def test_inspecting_an_encrypted_archive_without_a_passphrase_should_say_so(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)

        response = self._inspect(archive)

        self.assertEqual(400, response.status_code)
        self.assertTrue(response.json()['data']['encrypted'])

    def test_inspecting_with_the_wrong_passphrase_should_fail(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)

        response = self._inspect(archive, passphrase='not the passphrase')

        self.assertEqual(400, response.status_code)
        self.assertNotIn('encrypted', response.json().get('data', {}))

    def test_inspecting_with_the_wrong_passphrase_should_not_echo_it(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)

        response = self._inspect(archive, passphrase='hunter2')

        self.assertNotIn('hunter2', response.text)

    def test_a_flipped_ciphertext_byte_should_fail_authentication(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = bytearray(self._export(source['case_id'], passphrase=_PASSPHRASE))
        archive[-1] ^= 0xFF

        response = self._inspect(bytes(archive), passphrase=_PASSPHRASE)
        self.assertEqual(400, response.status_code)

    def test_a_tampered_header_should_fail_authentication(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = bytearray(self._export(source['case_id'], passphrase=_PASSPHRASE))
        # Byte 10 sits in the scrypt cost parameters. The header is bound in as
        # AAD precisely so it cannot be downgraded.
        archive[10] ^= 0x01

        response = self._inspect(bytes(archive), passphrase=_PASSPHRASE)
        self.assertEqual(400, response.status_code)

    def test_a_truncated_encrypted_archive_should_fail_authentication(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'], passphrase=_PASSPHRASE)

        response = self._inspect(archive[:len(archive) // 2], passphrase=_PASSPHRASE)
        self.assertEqual(400, response.status_code)

    def test_two_encrypted_exports_of_the_same_case_should_differ(self):
        source = self._build_rich_case(self._subject.create_dummy_user())
        first = self._export(source['case_id'], passphrase=_PASSPHRASE)
        second = self._export(source['case_id'], passphrase=_PASSPHRASE)
        self.assertNotEqual(first, second)

    def test_a_passphrase_on_an_unencrypted_archive_should_be_ignored(self):
        # Detection is by magic bytes, not by whether the operator typed
        # something into the passphrase box.
        source = self._build_rich_case(self._subject.create_dummy_user())
        archive = self._export(source['case_id'])

        response = self._inspect(archive, passphrase=_PASSPHRASE)
        self.assertEqual(200, response.status_code)
