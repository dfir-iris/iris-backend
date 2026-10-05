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

"""Webhook event names → object / action / labels. Pure functions."""

from unittest import TestCase

from app.iris_engine.webhooks.events import webhooks_build_catalogue
from app.iris_engine.webhooks.events import webhooks_event_label
from app.iris_engine.webhooks.events import webhooks_event_matches
from app.iris_engine.webhooks.events import webhooks_is_event
from app.iris_engine.webhooks.events import webhooks_is_manual
from app.iris_engine.webhooks.events import webhooks_split_event


class TestWebhooksEvents(TestCase):

    def test_split_should_keep_alert_cluster_apart_from_alert(self):
        self.assertEqual(('alert_cluster', 'merge'), webhooks_split_event('on_postload_alert_cluster_merge'))
        self.assertEqual(('alert', 'create'), webhooks_split_event('on_postload_alert_create'))

    def test_split_should_handle_multi_word_actions(self):
        self.assertEqual(('war_room', 'note_create'), webhooks_split_event('on_postload_war_room_note_create'))
        self.assertEqual(('asset', 'comment_update'), webhooks_split_event('on_postload_asset_comment_update'))

    def test_split_should_handle_global_task_before_task(self):
        self.assertEqual(('global_task', 'create'), webhooks_split_event('on_postload_global_task_create'))

    def test_label_should_be_human_readable(self):
        self.assertEqual('War room note created', webhooks_event_label('on_postload_war_room_note_create'))
        self.assertEqual('IOC comment added', webhooks_event_label('on_postload_ioc_commented'))
        self.assertEqual('Alert cluster merged', webhooks_event_label('on_postload_alert_cluster_merge'))
        self.assertEqual('Notification created', webhooks_event_label('on_postload_notification_create'))

    def test_is_event_should_accept_postload_and_manual_hooks(self):
        self.assertTrue(webhooks_is_event('on_postload_case_create'))
        self.assertTrue(webhooks_is_event('on_manual_trigger_case'))
        self.assertFalse(webhooks_is_event('on_preload_case_create'))
        self.assertFalse(webhooks_is_event(None))

    def test_manual_trigger_should_split_into_object_and_manual_action(self):
        self.assertTrue(webhooks_is_manual('on_manual_trigger_global_task'))
        self.assertFalse(webhooks_is_manual('on_postload_case_create'))
        self.assertEqual(('global_task', 'manual_trigger'), webhooks_split_event('on_manual_trigger_global_task'))
        self.assertEqual('IOC manual trigger', webhooks_event_label('on_manual_trigger_ioc'))

    def test_wildcard_should_not_cover_manual_triggers(self):
        self.assertFalse(webhooks_event_matches(['*'], 'on_manual_trigger_ioc'))
        self.assertTrue(webhooks_event_matches(['*', 'on_manual_trigger_ioc'], 'on_manual_trigger_ioc'))
        self.assertFalse(webhooks_event_matches(['on_manual_trigger_ioc'], 'on_manual_trigger_case'))

    def test_matches_should_honour_the_wildcard(self):
        self.assertTrue(webhooks_event_matches(['*'], 'on_postload_ioc_create'))

    def test_matches_should_honour_the_subscription(self):
        self.assertTrue(webhooks_event_matches(['on_postload_ioc_create'], 'on_postload_ioc_create'))
        self.assertFalse(webhooks_event_matches(['on_postload_ioc_create'], 'on_postload_ioc_update'))

    def test_matches_should_never_fire_on_preload_even_with_the_wildcard(self):
        self.assertFalse(webhooks_event_matches(['*'], 'on_preload_ioc_create'))

    def test_matches_should_not_fire_without_subscription(self):
        self.assertFalse(webhooks_event_matches([], 'on_postload_ioc_create'))
        self.assertFalse(webhooks_event_matches(None, 'on_postload_ioc_create'))

    def test_catalogue_should_skip_preload_hooks_and_sort(self):
        catalogue = webhooks_build_catalogue([
            ('on_postload_ioc_create', 'IOC created'),
            ('on_preload_ioc_create', 'Before IOC creation'),
            ('on_postload_alert_create', None),
            ('on_manual_trigger_ioc', 'Manual IOC'),
        ])
        self.assertEqual(['on_postload_alert_create', 'on_postload_ioc_create', 'on_manual_trigger_ioc'],
                         [e['name'] for e in catalogue])
        self.assertTrue(catalogue[2]['manual'])
        self.assertFalse(catalogue[0]['manual'])
        self.assertEqual('', catalogue[0]['description'])
        self.assertEqual('IOC', catalogue[1]['object_label'])
