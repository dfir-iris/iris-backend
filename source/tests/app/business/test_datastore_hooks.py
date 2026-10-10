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

"""The module hooks the case datastore fires: what webhooks and AI
workflows subscribe to when a file is uploaded, updated or deleted."""

from unittest import TestCase
from unittest.mock import patch

from app.business.datastore import datastore_add_file_as_evidence
from app.business.datastore import datastore_delete_file
from app.business.datastore import datastore_hook_file_saved

_MODULE = 'app.business.datastore'


class TestsDatastoreHooks(TestCase):

    def setUp(self):
        patcher = patch(f'{_MODULE}.call_modules_hook')
        self.hook = patcher.start()
        self.addCleanup(patcher.stop)

    def test_upload_should_fire_the_create_hook(self):
        datastore_hook_file_saved('dsf', 4, created=True)
        self.hook.assert_called_once_with('on_postload_datastore_file_create', data='dsf', caseid=4)

    def test_update_should_fire_the_update_hook(self):
        datastore_hook_file_saved('dsf', 4, created=False)
        self.hook.assert_called_once_with('on_postload_datastore_file_update', data='dsf', caseid=4)

    def test_new_evidence_should_fire_the_evidence_hook(self):
        with patch(f'{_MODULE}._add_file_as_evidence', return_value='crf'):
            self.assertEqual('crf', datastore_add_file_as_evidence(1, 'dsf', 4))
        self.hook.assert_called_once_with('on_postload_evidence_create', data='crf', caseid=4)

    def test_evidence_already_in_the_case_should_fire_nothing(self):
        with patch(f'{_MODULE}._add_file_as_evidence', return_value=None):
            self.assertIsNone(datastore_add_file_as_evidence(1, 'dsf', 4))
        self.hook.assert_not_called()

    def test_delete_should_fire_the_delete_hook_only_on_success(self):
        with patch(f'{_MODULE}._delete_file', return_value=(True, 'Invalid DS file ID for this case')):
            datastore_delete_file(12, 4)
        self.hook.assert_not_called()
        with patch(f'{_MODULE}._delete_file', return_value=(False, 'File deleted')):
            self.assertEqual((False, 'File deleted'), datastore_delete_file(12, 4))
        self.hook.assert_called_once_with('on_postload_datastore_file_delete',
                                          data={'id': 12, 'file_id': 12, 'case_id': 4}, caseid=4)
