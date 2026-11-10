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

"""Integration tests for `/api/v2/cases/<id>/tracker-steps` and
`/api/v2/cases/<id>/trackers`."""

from unittest import TestCase

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY


class TestsRestCaseTrackers(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        templates = self._subject.get('/api/v2/manage/tracker-templates').json()
        if isinstance(templates, list):
            for template in templates:
                self._subject.delete(f"/api/v2/manage/tracker-templates/{template['id']}")
        self._subject.clear_database()

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _task(self, case_id, title):
        body = {'task_assignees_id': [], 'task_description': '', 'task_status_id': 1, 'task_tags': '',
                'task_title': title, 'custom_attributes': {}}
        return self._subject.create(f'/api/v2/cases/{case_id}/tasks', body).json()['id']

    def _evidence(self, case_id, filename):
        return self._subject.create(f'/api/v2/cases/{case_id}/evidences', {'filename': filename}).json()['id']

    def _step(self, case_id, name, **body):
        return self._subject.create(f'/api/v2/cases/{case_id}/tracker-steps', {'name': name, **body})

    def _tracker(self, case_id, source='manual', **body):
        body.setdefault('name', f'{source} tracker')
        return self._subject.create(f'/api/v2/cases/{case_id}/trackers', {'source': source, **body})

    def _state(self, case_id, tracker_id, user=None, **body):
        return (user or self._subject).create(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows/state', body)

    def _case_events(self, case_id):
        return self._subject.get(f'/api/v2/cases/{case_id}/events').json()['tim']

    # ------------------------------------------------------------- Steps --

    def test_create_step_should_return_201(self):
        case_id = self._subject.create_dummy_case()
        response = self._step(case_id, 'THOR received', color='green', icon='check')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('THOR received', 'green', 'check'), (body['name'], body['color'], body['icon']))

    def test_create_step_should_reject_a_duplicated_name(self):
        case_id = self._subject.create_dummy_case()
        self._step(case_id, 'Waiting')
        self.assertEqual(400, self._step(case_id, 'waiting').status_code)

    def test_steps_should_be_per_case(self):
        case_id = self._subject.create_dummy_case()
        other_id = self._subject.create_dummy_case()
        step_id = self._step(case_id, 'Waiting').json()['id']
        self.assertEqual(201, self._step(other_id, 'Waiting').status_code)
        self.assertEqual(404, self._subject.patch(f'/api/v2/cases/{other_id}/tracker-steps/{step_id}',
                                                  {'name': 'x'}).status_code)

    def test_reorder_steps_should_return_them_in_order(self):
        case_id = self._subject.create_dummy_case()
        first = self._step(case_id, 'A').json()['id']
        second = self._step(case_id, 'B').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/tracker-steps/reorder', {'ids': [second]})
        self.assertEqual(200, response.status_code)
        self.assertEqual([second, first], [step['id'] for step in response.json()])

    def test_delete_step_should_prune_it_from_the_trackers(self):
        case_id = self._subject.create_dummy_case()
        step_id = self._step(case_id, 'A').json()['id']
        tracker_id = self._tracker(case_id, step_ids=[step_id]).json()['id']
        self.assertEqual(204, self._subject.delete(f'/api/v2/cases/{case_id}/tracker-steps/{step_id}').status_code)
        tracker = self._subject.get(f'/api/v2/cases/{case_id}/trackers/{tracker_id}').json()
        self.assertEqual([], tracker['step_ids'])

    # ---------------------------------------------------------- Trackers --

    def test_create_tracker_should_return_201(self):
        case_id = self._subject.create_dummy_case()
        response = self._tracker(case_id, 'assets', columns=[{'label': 'Ticket', 'type': 'url'}],
                                 show_asset_flags=True)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('assets', case_id), (body['source'], body['case_id']))
        self.assertEqual('c_ticket', body['columns'][0]['key'])
        self.assertTrue(body['show_asset_flags'])
        self.assertEqual(0, body['row_count'])

    def test_create_tracker_should_reject_a_war_room_source(self):
        case_id = self._subject.create_dummy_case()
        self.assertEqual(400, self._tracker(case_id, 'cases').status_code)

    def test_update_tracker_should_refuse_to_change_the_source(self):
        case_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(case_id).json()['id']
        url = f'/api/v2/cases/{case_id}/trackers/{tracker_id}'
        self.assertEqual(400, self._subject.patch(url, {'source': 'assets'}).status_code)
        self.assertEqual('Renamed', self._subject.patch(url, {'name': 'Renamed'}).json()['name'])

    def test_get_tracker_of_another_case_should_return_404(self):
        case_id = self._subject.create_dummy_case()
        other_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(case_id).json()['id']
        self.assertEqual(404, self._subject.get(f'/api/v2/cases/{other_id}/trackers/{tracker_id}').status_code)

    def test_reorder_and_delete_trackers(self):
        case_id = self._subject.create_dummy_case()
        first = self._tracker(case_id, name='A').json()['id']
        second = self._tracker(case_id, name='B').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/trackers/reorder', {'ids': [second]})
        self.assertEqual([second, first], [tracker['id'] for tracker in response.json()])
        self.assertEqual(204, self._subject.delete(f'/api/v2/cases/{case_id}/trackers/{first}').status_code)
        self.assertEqual([second], [t['id'] for t in self._subject.get(f'/api/v2/cases/{case_id}/trackers').json()])

    def test_unknown_case_should_return_404(self):
        self.assertEqual(404, self._subject.get('/api/v2/cases/999999/trackers').status_code)

    # -------------------------------------------------------------- Rows --

    def test_mirror_should_list_the_assets_of_the_case(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(case_id, 'assets').json()['id']
        response = self._subject.get(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows')
        self.assertEqual(200, response.status_code)
        rows = response.json()['data']
        self.assertEqual([f'asset:{asset_id}'], [row['key'] for row in rows])
        self.assertIsNone(rows[0]['row_id'])
        self.assertEqual('dc01', rows[0]['label'])
        self.assertEqual(1, self._subject.get(f'/api/v2/cases/{case_id}/trackers').json()[0]['row_count'])

    def test_mirror_should_list_the_tasks_and_evidences_of_the_case(self):
        case_id = self._subject.create_dummy_case()
        task_id = self._task(case_id, 'Collect THOR')
        evidence_id = self._evidence(case_id, 'thor.zip')
        tasks = self._tracker(case_id, 'tasks').json()['id']
        evidences = self._tracker(case_id, 'evidences').json()['id']
        task_rows = self._subject.get(f'/api/v2/cases/{case_id}/trackers/{tasks}/rows').json()['data']
        self.assertIn((f'task:{task_id}', 'Collect THOR'), [(row['key'], row['label']) for row in task_rows])
        evidence_rows = self._subject.get(f'/api/v2/cases/{case_id}/trackers/{evidences}/rows').json()['data']
        self.assertEqual([(f'evidence:{evidence_id}', 'thor.zip')],
                         [(row['key'], row['label']) for row in evidence_rows])

    def test_state_should_store_steps_and_values_of_a_mirrored_item(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(case_id, 'THOR received').json()['id']
        tracker = self._tracker(case_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        key = tracker['columns'][0]['key']
        response = self._state(case_id, tracker['id'], rows=[{'item_type': 'asset', 'item_id': asset_id}],
                               add_step_ids=[step_id], note='Waiting for the logs', values={key: '2026-10-21'})
        self.assertEqual(200, response.status_code)
        row = response.json()['data'][0]
        self.assertIsNotNone(row['row_id'])
        self.assertEqual([step_id], [step['step_id'] for step in row['steps']])
        self.assertEqual({key: '2026-10-21'}, row['values'])
        self.assertNotIn('timeline', response.json())
        history = self._subject.get(f'/api/v2/cases/{case_id}/trackers/{tracker["id"]}/rows/history',
                                    {'item_type': 'asset', 'item_id': asset_id}).json()
        self.assertEqual({'step_added', 'note_changed', 'value_changed'}, {event['action'] for event in history})

    def test_state_should_reject_an_item_of_another_case(self):
        case_id = self._subject.create_dummy_case()
        other_id = self._subject.create_dummy_case()
        asset_id = self._asset(other_id, 'elsewhere')
        tracker_id = self._tracker(case_id, 'assets').json()['id']
        response = self._state(case_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], note='x')
        self.assertEqual(400, response.status_code)

    def test_state_should_reject_an_invalid_value(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        tracker = self._tracker(case_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        response = self._state(case_id, tracker['id'], rows=[{'item_type': 'asset', 'item_id': asset_id}],
                               values={tracker['columns'][0]['key']: 'tomorrow'})
        self.assertEqual(400, response.status_code)

    def test_manual_rows_should_be_added_reordered_and_deleted(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(case_id).json()['id']
        url = f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows'
        response = self._subject.create(url, {'rows': [{'label': 'Call the CERT'},
                                                       {'item_type': 'asset', 'item_id': asset_id}]})
        self.assertEqual(201, response.status_code)
        created = response.json()['data']
        self.assertEqual(2, len(created))
        again = self._subject.create(url, {'rows': [{'item_type': 'asset', 'item_id': asset_id}]}).json()['data']
        self.assertEqual([], again)
        ids = [row['row_id'] for row in created]
        self.assertEqual(204, self._subject.create(f'{url}/reorder', {'ids': [ids[1]]}).status_code)
        self.assertEqual([ids[1], ids[0]], [row['row_id'] for row in self._subject.get(url).json()['data']])
        self.assertEqual(204, self._subject.delete(f'{url}/{ids[0]}').status_code)
        self.assertEqual(1, len(self._subject.get(url).json()['data']))

    def test_manual_rows_should_repeat_an_item_and_be_renamed(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(case_id).json()['id']
        url = f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows'
        row = {'item_type': 'asset', 'item_id': asset_id}
        response = self._subject.create(url, {'rows': [row, {**row, 'label': 'Second pass'}],
                                              'allow_duplicates': True})
        self.assertEqual(201, response.status_code)
        created = response.json()['data']
        self.assertEqual([None, 'Second pass'], [entry['custom_label'] for entry in created])
        self.assertEqual(2, len({entry['key'] for entry in created}))
        response = self._subject.create(f'{url}/state', {'rows': [{'row_id': created[0]['row_id']}],
                                                         'label': 'First pass'})
        self.assertEqual(200, response.status_code)
        self.assertEqual([('dc01', 'First pass'), ('dc01', 'Second pass')],
                         [(entry['label'], entry['custom_label']) for entry in self._subject.get(url).json()['data']])

    def test_manual_rows_should_reject_an_item_of_another_case(self):
        case_id = self._subject.create_dummy_case()
        other_id = self._subject.create_dummy_case()
        asset_id = self._asset(other_id, 'elsewhere')
        tracker_id = self._tracker(case_id).json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'item_type': 'asset', 'item_id': asset_id}]})
        self.assertEqual(400, response.status_code)

    def test_rows_cannot_be_added_to_a_mirror(self):
        case_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(case_id, 'tasks').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'label': 'x'}]})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------ Access --

    def test_a_user_without_case_access_should_be_denied(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, user.get(f'/api/v2/cases/{case_id}/trackers').status_code)
        self.assertEqual(403, user.get(f'/api/v2/cases/{case_id}/tracker-steps').status_code)

    def test_a_reader_should_read_but_not_write(self):
        case_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(case_id).json()['id']
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        self.assertEqual(200, user.get(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows').status_code)
        self.assertEqual(403, user.create(f'/api/v2/cases/{case_id}/tracker-steps', {'name': 'x'}).status_code)
        self.assertEqual(403, user.create(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows',
                                          {'rows': [{'label': 'x'}]}).status_code)

    # --------------------------------------------------------- Timelines --

    def test_changes_should_be_written_to_a_trackers_timeline(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(case_id, 'Isolated').json()['id']
        tracker = self._tracker(case_id, 'assets', name='Hosts', timeline_sync={'mode': 'changes'}).json()
        self.assertEqual({'mode': 'changes', 'date': 'changed_at', 'case_timeline_id': None},
                         tracker['timeline_sync'])
        rows = [{'item_type': 'asset', 'item_id': asset_id}]
        response = self._state(case_id, tracker['id'], rows=rows, add_step_ids=[step_id])
        self.assertEqual({'events': 1}, response.json()['timeline'])
        self._state(case_id, tracker['id'], rows=rows, note='Contained')
        titles = [event['event_title'] for event in self._case_events(case_id)]
        self.assertEqual(['Hosts · dc01: Isolated set', 'Hosts · dc01: note updated'], titles)
        timelines = self._subject.get(f'/api/v2/cases/{case_id}/timelines').json()
        self.assertIn('Trackers', [timeline['name'] for timeline in timelines])

    def test_sync_should_use_the_chosen_timeline(self):
        case_id = self._subject.create_dummy_case()
        timeline_id = self._subject.create(f'/api/v2/cases/{case_id}/timelines',
                                           {'name': 'Remediation'}).json()['timeline_id']
        other_id = self._subject.create_dummy_case()
        other_timeline = self._subject.create(f'/api/v2/cases/{other_id}/timelines',
                                              {'name': 'Elsewhere'}).json()['timeline_id']
        self.assertEqual(400, self._tracker(case_id, timeline_sync={
            'mode': 'changes', 'case_timeline_id': other_timeline}).status_code)
        tracker_id = self._tracker(case_id, name='Actions', timeline_sync={
            'mode': 'changes', 'case_timeline_id': timeline_id}).json()['id']
        self._subject.create(f'/api/v2/cases/{case_id}/trackers/{tracker_id}/rows',
                             {'rows': [{'label': 'Call the CERT'}]})
        timelines = self._subject.get(f'/api/v2/cases/{case_id}/timelines').json()
        self.assertNotIn('Trackers', [timeline['name'] for timeline in timelines])
        self.assertEqual(['Actions · Call the CERT: added to the tracker'],
                         [event['event_title'] for event in self._case_events(case_id)])

    def test_state_should_be_one_event_per_row_dated_by_the_column(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        isolated = self._step(case_id, 'Isolated').json()['id']
        restored = self._step(case_id, 'Restored').json()['id']
        tracker = self._tracker(case_id, 'assets', name='Hosts', columns=[{'label': 'Done on', 'type': 'date'}],
                                timeline_sync={'mode': 'state', 'date': 'c_done_on'}).json()
        rows = [{'item_type': 'asset', 'item_id': asset_id}]
        self._state(case_id, tracker['id'], rows=rows, add_step_ids=[isolated], values={'c_done_on': '2026-10-21'})
        self._state(case_id, tracker['id'], rows=rows, add_step_ids=[restored])
        events = self._case_events(case_id)
        self.assertEqual(['Hosts · dc01: Isolated, Restored'], [event['event_title'] for event in events])
        self.assertTrue(events[0]['event_date'].startswith('2026-10-21'))

    def test_the_dating_column_should_not_be_removed(self):
        case_id = self._subject.create_dummy_case()
        tracker = self._tracker(case_id, columns=[{'label': 'Done on', 'type': 'date'}],
                                timeline_sync={'mode': 'state', 'date': 'c_done_on'}).json()
        url = f'/api/v2/cases/{case_id}/trackers/{tracker["id"]}'
        self.assertEqual(400, self._subject.patch(url, {'columns': []}).status_code)

    def test_sync_should_write_the_state_once(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(case_id, 'Isolated').json()['id']
        tracker_id = self._tracker(case_id, 'assets', name='Hosts').json()['id']
        self._state(case_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], add_step_ids=[step_id])
        url = f'/api/v2/cases/{case_id}/trackers/{tracker_id}'
        self.assertEqual(400, self._subject.create(f'{url}/timeline/sync', {}).status_code)
        self._subject.patch(url, {'timeline_sync': {'mode': 'state'}})
        for _ in range(2):
            response = self._subject.create(f'{url}/timeline/sync', {})
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, response.json()['events'])
        self.assertEqual(['Hosts · dc01: Isolated'], [event['event_title'] for event in self._case_events(case_id)])

    def test_sync_should_write_the_past_changes_once(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(case_id, 'Isolated').json()['id']
        tracker_id = self._tracker(case_id, 'assets', name='Hosts').json()['id']
        rows = [{'item_type': 'asset', 'item_id': asset_id}]
        self._state(case_id, tracker_id, rows=rows, add_step_ids=[step_id])
        self._state(case_id, tracker_id, rows=rows, note='done')
        url = f'/api/v2/cases/{case_id}/trackers/{tracker_id}'
        self._subject.patch(url, {'timeline_sync': {'mode': 'changes'}})
        listed = self._subject.get(f'/api/v2/cases/{case_id}/trackers').json()
        self.assertEqual(2, next(t for t in listed if t['id'] == tracker_id)['timeline_backfill'])
        response = self._subject.create(f'{url}/timeline/sync', {})
        self.assertEqual(200, response.status_code)
        self.assertEqual((2, 0), (response.json()['events'], response.json()['remaining']))
        self.assertEqual(0, self._subject.create(f'{url}/timeline/sync', {}).json()['events'])
        self.assertEqual(['Hosts · dc01: Isolated set', 'Hosts · dc01: note updated'],
                         sorted(event['event_title'] for event in self._case_events(case_id)))

    # --------------------------------------------------------- Templates --

    def test_template_should_be_applied_to_a_case_skipping_war_room_sources(self):
        response = self._subject.create('/api/v2/manage/tracker-templates', {
            'name': 'Collection',
            'steps': [{'name': 'THOR received', 'color': 'green'}, {'name': 'Waiting'}],
            'trackers': [{'name': 'Hosts', 'source': 'assets', 'step_names': ['THOR received', 'Waiting']},
                         {'name': 'Evidence', 'source': 'evidences'},
                         {'name': 'Customers', 'source': 'customers'}],
        })
        self.assertEqual(201, response.status_code)
        template_id = response.json()['id']
        case_id = self._subject.create_dummy_case()
        self._step(case_id, 'waiting')
        applied = self._subject.create(f'/api/v2/cases/{case_id}/trackers/apply-template',
                                       {'template_id': template_id})
        self.assertEqual(200, applied.status_code)
        body = applied.json()
        self.assertEqual(2, len(body['steps']))
        self.assertEqual(['Hosts', 'Evidence'], [tracker['name'] for tracker in body['trackers']])
        self.assertEqual(['Customers'], body['skipped'])
        self.assertEqual(400, self._subject.create(f'/api/v2/cases/{case_id}/trackers/apply-template',
                                                   {'template_id': 'x'}).status_code)

    def test_save_as_template_should_return_201(self):
        case_id = self._subject.create_dummy_case()
        step_id = self._step(case_id, 'Isolated').json()['id']
        self._tracker(case_id, 'tasks', step_ids=[step_id], timeline_sync={'mode': 'changes'})
        response = self._subject.create(f'/api/v2/cases/{case_id}/trackers/save-as-template',
                                        {'name': 'From case'})
        self.assertEqual(201, response.status_code)
        tracker = response.json()['trackers'][0]
        self.assertEqual(('tasks', ['Isolated']), (tracker['source'], tracker['step_names']))

    def test_save_as_template_should_need_server_administrator(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id)
        response = user.create(f'/api/v2/cases/{case_id}/trackers/save-as-template', {'name': 'x'})
        self.assertEqual(403, response.status_code)
