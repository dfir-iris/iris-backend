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

"""The IRIS envelope built from hook data. Pure, no app context."""

import datetime
from unittest import TestCase

from app.iris_engine.webhooks.payload import webhooks_case_id
from app.iris_engine.webhooks.payload import webhooks_make_event
from app.iris_engine.webhooks.payload import webhooks_serialize

_BASE = 'https://iris.example.org'
_ACTOR = {'id': 2, 'login': 'jdoe', 'name': 'Jane Doe'}
_CASE = {'id': 4, 'name': '#4 - Phishing'}


def _make(hook, data, case=None, actor=_ACTOR):
    return webhooks_make_event(hook, data, case=case, actor=actor, instance_url=f'{_BASE}/', version='3.0.0',
                               timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))


class TestWebhooksSerialize(TestCase):

    def test_serialize_should_make_values_json_safe(self):
        data = {'when': datetime.datetime(2026, 1, 1, 8, 0), 'items': ({1, 2} and [1, 2]), 'raw': b'\x00'}
        self.assertEqual({'when': '2026-01-01T08:00:00', 'items': [1, 2], 'raw': None}, webhooks_serialize(data))

    def test_serialize_should_pass_scalars_through(self):
        self.assertEqual(5, webhooks_serialize(5))
        self.assertIsNone(webhooks_serialize(None))


class TestWebhooksMakeEvent(TestCase):

    def test_alert_event_should_link_to_the_alert(self):
        event = _make('on_postload_alert_create', {'alert_id': 9, 'alert_title': 'Beacon'})
        self.assertEqual('alert', event['object_type'])
        self.assertEqual('create', event['action'])
        self.assertEqual(9, event['object_id'])
        self.assertEqual('Alert created: Beacon', event['title'])
        self.assertEqual('Jane Doe created alert Beacon', event['summary'])
        self.assertEqual(f'{_BASE}/alerts/9', event['url'])
        self.assertEqual({'url': _BASE, 'version': '3.0.0'}, event['iris'])
        self.assertEqual('2026-01-01T00:00:00+00:00', event['timestamp'])

    def test_case_object_event_should_link_inside_the_case_and_name_it(self):
        event = _make('on_postload_ioc_create', {'ioc_id': 3, 'ioc_value': '1.2.3.4'}, case=_CASE)
        self.assertEqual(f'{_BASE}/case/4/iocs/3', event['url'])
        self.assertEqual('Jane Doe created ioc 1.2.3.4 in case #4 #4 - Phishing', event['summary'])

    def test_datastore_file_event_should_find_its_file_and_case(self):
        data = {'file_id': 12, 'file_case_id': 4, 'file_original_name': 'dump.raw'}
        event = _make('on_postload_datastore_file_create', data)
        self.assertEqual(12, event['object_id'])
        self.assertEqual('Datastore file created: dump.raw', event['title'])
        self.assertEqual(4, webhooks_case_id(data, 'datastore_file'))

    def test_list_data_should_use_the_first_item(self):
        event = _make('on_postload_asset_update', [{'asset_id': 1, 'asset_name': 'PC'}, {'asset_id': 2}], case=_CASE)
        self.assertEqual(1, event['object_id'])
        self.assertEqual('Asset updated: PC', event['title'])

    def test_delete_event_should_take_the_bare_id_and_link_to_the_case(self):
        event = _make('on_postload_note_delete', 12, case=_CASE)
        self.assertEqual(12, event['object_id'])
        self.assertEqual('Note deleted: #12', event['title'])
        self.assertEqual(f'{_BASE}/case/4', event['url'])

    def test_comment_event_should_append_the_comment(self):
        data = {'comment': {'comment_text': 'Looks malicious'}, 'ioc': {'ioc_id': 3, 'ioc_value': 'evil.com'}}
        event = _make('on_postload_ioc_commented', data, case=_CASE)
        self.assertIn('added a comment', event['summary'].replace('comment added', 'added a comment'))
        self.assertIn(': Looks malicious', event['summary'])
        self.assertEqual(f'{_BASE}/case/4', event['url'])

    def test_notification_event_should_use_its_title_body_and_link(self):
        data = {'id': 1, 'title': 'You were mentioned', 'body': 'in note X', 'link': '/case/4/notes/2'}
        event = _make('on_postload_notification_create', data)
        self.assertEqual('You were mentioned', event['title'])
        self.assertEqual('in note X', event['summary'])
        self.assertEqual(f'{_BASE}/case/4/notes/2', event['url'])

    def test_notification_with_an_external_link_should_get_no_url(self):
        event = _make('on_postload_notification_create', {'title': 't', 'link': 'https://evil.example/'})
        self.assertIsNone(event['url'])

    def test_event_without_actor_should_say_iris(self):
        event = _make('on_postload_alert_update', {'alert_id': 1, 'alert_title': 'A'}, actor=None)
        self.assertTrue(event['summary'].startswith('IRIS updated alert A'))

    def test_no_instance_url_should_give_no_link(self):
        event = webhooks_make_event('on_postload_alert_create', {'alert_id': 1})
        self.assertIsNone(event['url'])
        self.assertIsNone(event['iris']['url'])

    def test_alert_cluster_event_should_link_to_the_cluster(self):
        event = _make('on_postload_alert_cluster_create', {'cluster_id': 5, 'cluster_title': 'Wave'})
        self.assertEqual(f'{_BASE}/alert-clusters/5', event['url'])


class TestWebhooksCaseId(TestCase):

    def test_explicit_caseid_should_win(self):
        self.assertEqual(7, webhooks_case_id({'case_id': 1}, 'ioc', caseid=7))

    def test_case_event_should_use_its_own_id(self):
        self.assertEqual(3, webhooks_case_id({'case_id': 3}, 'case'))

    def test_object_event_should_use_its_case_reference(self):
        self.assertEqual(4, webhooks_case_id({'task_case_id': 4}, 'task'))
        self.assertEqual(5, webhooks_case_id({'case': {'case_id': 5}}, 'note'))

    def test_unknown_case_should_be_none(self):
        self.assertIsNone(webhooks_case_id(12, 'note'))
