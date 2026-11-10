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

"""Integration tests for `/api/v2/war-rooms/<id>/tracker-steps`,
`/api/v2/war-rooms/<id>/trackers` and `/api/v2/manage/tracker-templates`."""

from unittest import TestCase

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER

_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000
_WAR_ROOM_READ_ONLY = 0x2


class TestsRestWarRoomTrackers(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        templates = self._subject.get('/api/v2/manage/tracker-templates').json()
        if isinstance(templates, list):
            for template in templates:
                self._subject.delete(f"/api/v2/manage/tracker-templates/{template['id']}")
        self._subject.clear_database()

    def _room(self, name='Trackers Room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _attach_case(self, room_id):
        case_id = self._subject.create_dummy_case()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})
        return case_id

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _ioc(self, case_id, value):
        return self._subject.create(f'/api/v2/cases/{case_id}/iocs',
                                    {'ioc_type_id': 1, 'ioc_tlp_id': 2, 'ioc_value': value}).json()['ioc_id']

    def _step(self, room_id, name, **body):
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/tracker-steps', {'name': name, **body})

    def _tracker(self, room_id, source='manual', **body):
        body.setdefault('name', f'{source} tracker')
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers', {'source': source, **body})

    def _member(self, room_id, access_level=None):
        member = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        body = {'user_id': member.get_identifier()}
        if access_level is not None:
            body['access_level'] = access_level
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members', body)
        return member

    # ------------------------------------------------------------- Steps --

    def test_create_step_should_return_201(self):
        room_id = self._room()
        response = self._step(room_id, 'Isolated', color='red', icon='shield-off')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('Isolated', 'red', 'shield-off', 0), (body['name'], body['color'], body['icon'],
                                                                body['sort_order']))

    def test_create_step_should_reject_a_duplicated_name(self):
        room_id = self._room()
        self._step(room_id, 'Isolated')
        self.assertEqual(400, self._step(room_id, 'isolated').status_code)

    def test_create_step_should_reject_an_unknown_color(self):
        room_id = self._room()
        self.assertEqual(400, self._step(room_id, 'Isolated', color='#ff0000').status_code)

    def test_reorder_steps_should_return_them_in_order(self):
        room_id = self._room()
        first = self._step(room_id, 'A').json()['id']
        second = self._step(room_id, 'B').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/tracker-steps/reorder', {'ids': [second]})
        self.assertEqual(200, response.status_code)
        self.assertEqual([second, first], [step['id'] for step in response.json()])

    def test_delete_step_should_prune_it_from_the_trackers(self):
        room_id = self._room()
        step_id = self._step(room_id, 'A').json()['id']
        tracker_id = self._tracker(room_id, step_ids=[step_id]).json()['id']
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/tracker-steps/{step_id}')
        self.assertEqual(204, response.status_code)
        tracker = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}').json()
        self.assertEqual([], tracker['step_ids'])

    # ---------------------------------------------------------- Trackers --

    def test_create_tracker_should_return_201(self):
        room_id = self._room()
        response = self._tracker(room_id, 'assets', columns=[{'label': 'Ticket', 'type': 'url'}],
                                 show_asset_flags=True)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('assets', body['source'])
        self.assertEqual('c_ticket', body['columns'][0]['key'])
        self.assertTrue(body['show_asset_flags'])
        self.assertIsNone(body['step_ids'])
        self.assertEqual(0, body['row_count'])

    def test_create_tracker_should_reject_an_unknown_source(self):
        room_id = self._room()
        self.assertEqual(400, self._tracker(room_id, 'vulnerabilities').status_code)

    def test_update_tracker_should_refuse_to_change_the_source(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}', {'source': 'assets'})
        self.assertEqual(400, response.status_code)
        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}', {'name': 'Renamed'})
        self.assertEqual('Renamed', response.json()['name'])

    def test_get_tracker_of_another_room_should_return_404(self):
        room_id = self._room()
        other_id = self._room('Other')
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.get(f'/api/v2/war-rooms/{other_id}/trackers/{tracker_id}')
        self.assertEqual(404, response.status_code)

    def test_delete_tracker_should_return_204(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id).json()['id']
        self.assertEqual(204, self._subject.delete(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}').status_code)
        self.assertEqual([], self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers').json())

    # -------------------------------------------------------------- Rows --

    def test_mirror_should_list_the_assets_of_the_attached_cases(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(room_id, 'assets').json()['id']
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows')
        self.assertEqual(200, response.status_code)
        rows = response.json()['data']
        self.assertEqual([f'asset:{asset_id}'], [row['key'] for row in rows])
        self.assertIsNone(rows[0]['row_id'])
        self.assertEqual('dc01', rows[0]['label'])
        trackers = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers').json()
        self.assertEqual(1, trackers[0]['row_count'])

    def test_state_should_store_steps_and_values_of_a_mirrored_item(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker = self._tracker(room_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        key = tracker['columns'][0]['key']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'add_step_ids': [step_id], 'note': 'Done',
            'values': {key: '2026-10-21'},
        })
        self.assertEqual(200, response.status_code)
        row = response.json()['data'][0]
        self.assertIsNotNone(row['row_id'])
        self.assertEqual([step_id], [step['step_id'] for step in row['steps']])
        self.assertEqual({key: '2026-10-21'}, row['values'])
        history = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/history',
                                    {'item_type': 'asset', 'item_id': asset_id}).json()
        self.assertEqual({'step_added', 'note_changed', 'value_changed'}, {event['action'] for event in history})

    def test_state_should_post_a_line_in_the_war_room_stream(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker = self._tracker(room_id, 'assets', name='Hosts').json()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'add_step_ids': [step_id]})
        listed = self._subject.get(f'/api/v2/war-rooms/{room_id}/chat').json()
        lines = [(m['body'], m['ref_type'], m['ref_id']) for m in listed if m['kind'] == 'tracker']
        self.assertIn(('Hosts: set Isolated on 1 asset', 'war_room_tracker', tracker['id']), lines)
        self.assertIn(('Created tracker: Hosts', 'war_room_tracker', tracker['id']), lines)

    def test_state_should_reject_an_item_out_of_the_room(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'elsewhere')
        tracker_id = self._tracker(room_id, 'assets').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'note': 'x'})
        self.assertEqual(400, response.status_code)

    def test_state_should_reject_an_invalid_value(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker = self._tracker(room_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}],
            'values': {tracker['columns'][0]['key']: 'tomorrow'}})
        self.assertEqual(400, response.status_code)

    def test_iocs_with_the_same_value_should_be_one_row(self):
        room_id = self._room()
        first_case = self._attach_case(room_id)
        second_case = self._attach_case(room_id)
        self._ioc(first_case, 'evil.example')
        self._ioc(second_case, 'evil.example')
        tracker_id = self._tracker(room_id, 'iocs').json()['id']
        rows = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows').json()['data']
        self.assertEqual(1, len(rows))
        self.assertEqual(2, len(rows[0]['item']['case_ids']))

    def test_manual_rows_should_be_added_reordered_and_deleted(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows'
        response = self._subject.create(url, {'rows': [{'label': 'Call the CERT'},
                                                       {'item_type': 'case', 'item_id': case_id}]})
        self.assertEqual(201, response.status_code)
        created = response.json()['data']
        self.assertEqual(2, len(created))
        again = self._subject.create(url, {'rows': [{'item_type': 'case', 'item_id': case_id}]}).json()['data']
        self.assertEqual([], again)
        ids = [row['row_id'] for row in created]
        self.assertEqual(204, self._subject.create(f'{url}/reorder', {'ids': [ids[1]]}).status_code)
        rows = self._subject.get(url).json()['data']
        self.assertEqual([ids[1], ids[0]], [row['row_id'] for row in rows])
        self.assertEqual(204, self._subject.delete(f'{url}/{ids[0]}').status_code)
        self.assertEqual(1, len(self._subject.get(url).json()['data']))

    def test_manual_rows_should_repeat_an_item_and_be_renamed(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows'
        row = {'item_type': 'case', 'item_id': case_id}
        response = self._subject.create(url, {'rows': [row, {**row, 'label': 'Second pass'}],
                                              'allow_duplicates': True})
        self.assertEqual(201, response.status_code)
        created = response.json()['data']
        self.assertEqual([None, 'Second pass'], [entry['custom_label'] for entry in created])
        self.assertEqual(2, len({entry['key'] for entry in created}))
        response = self._subject.create(f'{url}/state', {'rows': [{'row_id': created[0]['row_id']}],
                                                         'label': 'First pass'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(['First pass', 'Second pass'],
                         [entry['custom_label'] for entry in self._subject.get(url).json()['data']])

    def test_manual_rows_should_reject_an_item_out_of_the_room(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'item_type': 'case', 'item_id': case_id}]})
        self.assertEqual(400, response.status_code)

    def test_rows_cannot_be_added_to_a_mirror(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id, 'cases').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'label': 'x'}]})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------ Access --

    def test_a_member_without_case_access_should_see_restricted_rows(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                             {'rows': [{'item_type': 'case', 'item_id': case_id}]})
        member = self._member(room_id)
        rows = member.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows').json()['data']
        self.assertTrue(rows[0]['restricted'])
        self.assertIsNone(rows[0]['item'])

    def test_a_non_member_should_be_denied(self):
        room_id = self._room()
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self.assertEqual(403, user.get(f'/api/v2/war-rooms/{room_id}/trackers').status_code)
        self.assertEqual(403, user.create(f'/api/v2/war-rooms/{room_id}/tracker-steps', {'name': 'x'}).status_code)

    # ------------------------------------------------------------- Board --

    def _set_board(self, room_id, widgets, user=None):
        return (user or self._subject).update(f'/api/v2/war-rooms/{room_id}/board/widgets', {'widgets': widgets})

    def _widget(self, board, widget_id):
        return next(widget for widget in board['widgets'] if widget['id'] == widget_id)

    def test_a_room_should_start_with_the_default_board(self):
        room_id = self._room()
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/board/widgets').json()
        self.assertFalse(response['board_customized'])
        self.assertIn('cases', [widget['id'] for widget in response['widgets']])

    def test_board_widgets_should_show_the_tracker_rows(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        contacted = self._step(room_id, 'Contacted').json()['id']
        action = self._step(room_id, 'Action required').json()['id']
        tracker_id = self._tracker(room_id, 'cases', step_ids=[contacted, action]).json()['id']
        self._state(room_id, tracker_id, rows=[{'item_type': 'case', 'item_id': case_id}],
                    add_step_ids=[contacted, action])
        response = self._set_board(room_id, [
            {'type': 'cases', 'config': {'highlight_step_ids': [action]}},
            {'type': 'tracker_count', 'id': 'contacted', 'config': {'tracker_id': tracker_id, 'step_ids': [contacted]}},
            {'type': 'tracker_progress', 'id': 'progress', 'config': {'tracker_id': tracker_id}},
            {'type': 'tracker_list', 'id': 'actions', 'column': 'side',
             'config': {'tracker_id': tracker_id, 'step_ids': [action]}}])
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()['board_customized'])
        board = self._subject.get(f'/api/v2/war-rooms/{room_id}/board').json()
        self.assertEqual({'count': 1, 'total': 1},
                         {k: v for k, v in self._widget(board, 'contacted')['data'].items() if k in ('count', 'total')})
        progress = self._widget(board, 'progress')['data']
        self.assertEqual((1, 0), (progress['rows_total'], progress['rows_without_step']))
        self.assertEqual([{'step_id': contacted, 'count': 1}, {'step_id': action, 'count': 1}], progress['steps'])
        self.assertEqual([[case_id]], [row['case_ids'] for row in self._widget(board, 'actions')['data']['rows']])
        card = next(card for card in board['cases'] if card['case_id'] == case_id)
        self.assertEqual([{'step_id': action, 'count': 1}], card['highlights'])

    def test_board_widgets_should_be_validated_and_pruned(self):
        room_id = self._room()
        step_id = self._step(room_id, 'Action required').json()['id']
        tracker_id = self._tracker(room_id).json()['id']
        self.assertEqual(400, self._set_board(room_id, [{'type': 'tracker_count', 'config': {
            'tracker_id': tracker_id, 'step_ids': [step_id + 1000]}}]).status_code)
        self.assertEqual(400, self._set_board(room_id, [{'type': 'nope'}]).status_code)
        self._set_board(room_id, [{'type': 'cases', 'config': {'highlight_step_ids': [step_id]}},
                                  {'type': 'tracker_list', 'id': 'list', 'config': {'tracker_id': tracker_id}}])
        self._subject.delete(f'/api/v2/war-rooms/{room_id}/tracker-steps/{step_id}')
        self._subject.delete(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}')
        widgets = self._subject.get(f'/api/v2/war-rooms/{room_id}/board/widgets').json()['widgets']
        self.assertEqual(['cases'], [widget['id'] for widget in widgets])
        self.assertEqual([], widgets[0]['config']['highlight_step_ids'])
        response = self._set_board(room_id, None).json()
        self.assertFalse(response['board_customized'])

    def test_a_reader_should_not_change_the_board(self):
        room_id = self._room()
        member = self._member(room_id, access_level=_WAR_ROOM_READ_ONLY)
        self.assertEqual(200, member.get(f'/api/v2/war-rooms/{room_id}/board/widgets').status_code)
        self.assertEqual(403, self._set_board(room_id, None, member).status_code)

    def test_a_member_without_case_access_should_not_see_the_board_rows(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                             {'rows': [{'item_type': 'case', 'item_id': case_id}]})
        self._set_board(room_id, [{'type': 'tracker_list', 'id': 'list', 'config': {'tracker_id': tracker_id}}])
        member = self._member(room_id)
        board = member.get(f'/api/v2/war-rooms/{room_id}/board').json()
        row, = self._widget(board, 'list')['data']['rows']
        self.assertTrue(row['restricted'])
        self.assertIsNone(row['label'])
        self.assertEqual([], row['case_ids'])

    # --------------------------------------------------------- Timelines --

    def _state(self, room_id, tracker_id, user=None, **body):
        return (user or self._subject).create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows/state', body)

    def _case_events(self, case_id):
        return self._subject.get(f'/api/v2/cases/{case_id}/events').json()['tim']

    def _room_events(self, room_id, user=None):
        return (user or self._subject).get(f'/api/v2/war-rooms/{room_id}/timelines/events').json()

    def test_changes_should_be_written_to_the_timeline_of_the_case(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker = self._tracker(room_id, 'assets', name='Hosts',
                                timeline_sync={'mode': 'changes', 'target': 'cases'}).json()
        self.assertEqual({'mode': 'changes', 'target': 'cases', 'date': 'changed_at', 'war_room_timeline_id': None},
                         tracker['timeline_sync'])
        rows = [{'item_type': 'asset', 'item_id': asset_id}]
        response = self._state(room_id, tracker['id'], rows=rows, add_step_ids=[step_id])
        self.assertEqual({'events': 1, 'skipped_case_ids': [], 'restricted_rows': 0,
                          'war_room_rows': 0}, response.json()['timeline'])
        self._state(room_id, tracker['id'], rows=rows, note='Contained')
        titles = [event['event_title'] for event in self._case_events(case_id)]
        self.assertEqual(['Hosts · dc01: Isolated set', 'Hosts · dc01: note updated'], titles)
        timelines = self._subject.get(f'/api/v2/cases/{case_id}/timelines').json()
        self.assertIn('War-room trackers', [timeline['name'] for timeline in timelines])
        self.assertEqual([], self._room_events(room_id))

    def test_state_should_be_one_event_per_row_dated_by_the_column(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        isolated = self._step(room_id, 'Isolated').json()['id']
        restored = self._step(room_id, 'Restored').json()['id']
        tracker = self._tracker(room_id, 'assets', name='Hosts', columns=[{'label': 'Done on', 'type': 'date'}],
                                timeline_sync={'mode': 'state', 'target': 'cases', 'date': 'c_done_on'}).json()
        rows = [{'item_type': 'asset', 'item_id': asset_id}]
        self._state(room_id, tracker['id'], rows=rows, add_step_ids=[isolated], values={'c_done_on': '2026-10-21'})
        self._state(room_id, tracker['id'], rows=rows, add_step_ids=[restored])
        events = self._case_events(case_id)
        self.assertEqual(['Hosts · dc01: Isolated, Restored'], [event['event_title'] for event in events])
        self.assertTrue(events[0]['event_date'].startswith('2026-10-21'))

    def test_the_dating_column_should_not_be_removed(self):
        room_id = self._room()
        tracker = self._tracker(room_id, columns=[{'label': 'Done on', 'type': 'date'}],
                                timeline_sync={'mode': 'state', 'target': 'war_room', 'date': 'c_done_on'}).json()
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}'
        self.assertEqual(400, self._subject.patch(url, {'columns': []}).status_code)
        self.assertEqual(400, self._tracker(room_id, timeline_sync={'mode': 'state', 'target': 'war_room',
                                                                    'date': 'c_nope'}).status_code)

    def test_the_war_room_target_should_gate_events_by_their_case(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(room_id, 'assets', name='Hosts',
                                   timeline_sync={'mode': 'changes', 'target': 'war_room'}).json()['id']
        self._state(room_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], note='Contained')
        events = self._room_events(room_id)
        self.assertEqual([('Hosts · dc01: note updated', tracker_id)], [(e['title'], e['tracker_id']) for e in events])
        self.assertEqual([], self._case_events(case_id))
        member = self._member(room_id)
        self.assertEqual([], self._room_events(room_id, member))
        url = f'/api/v2/war-rooms/{room_id}/timelines/events/{events[0]["id"]}'
        self.assertEqual(404, member.patch(url, {'title': 'x'}).status_code)
        self.assertEqual(404, member.delete(url).status_code)
        self._subject.grant_case_access(member, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        self.assertEqual([events[0]['id']], [event['id'] for event in self._room_events(room_id, member)])

    def test_rows_without_case_should_go_to_the_war_room_timeline(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id, name='Actions',
                                   timeline_sync={'mode': 'changes', 'target': 'cases'}).json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'label': 'Call the CERT'}]})
        self.assertEqual((1, 1), (response.json()['timeline']['events'], response.json()['timeline']['war_room_rows']))
        member = self._member(room_id)
        self.assertEqual(['Actions · Call the CERT: added to the tracker'],
                         [event['title'] for event in self._room_events(room_id, member)])

    def test_a_customer_should_be_written_to_each_of_its_cases(self):
        room_id = self._room()
        first_case_id = self._attach_case(room_id)
        second_case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id, 'customers', name='Customers',
                                   timeline_sync={'mode': 'changes', 'target': 'cases'}).json()['id']
        response = self._state(room_id, tracker_id, rows=[{'item_type': 'customer',
                                                           'item_id': IRIS_INITIAL_CUSTOMER_IDENTIFIER}],
                               note='Notified')
        self.assertEqual({'events': 2, 'skipped_case_ids': [], 'restricted_rows': 0, 'war_room_rows': 0},
                         response.json()['timeline'])
        for case_id in (first_case_id, second_case_id):
            self.assertEqual(1, len(self._case_events(case_id)))
        self.assertEqual([], self._room_events(room_id))

    def test_a_case_without_full_access_should_be_skipped(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(room_id, 'assets',
                                   timeline_sync={'mode': 'changes', 'target': 'cases'}).json()['id']
        member = self._member(room_id)
        self._subject.grant_case_access(member, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        response = self._state(room_id, tracker_id, member, rows=[{'item_type': 'asset', 'item_id': asset_id}],
                               note='x')
        self.assertEqual(200, response.status_code)
        self.assertEqual({'events': 0, 'skipped_case_ids': [case_id], 'restricted_rows': 0, 'war_room_rows': 0},
                         response.json()['timeline'])
        self.assertEqual([], self._case_events(case_id))

    def test_sync_should_write_the_state_once(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker_id = self._tracker(room_id, 'assets', name='Hosts').json()['id']
        self._state(room_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], add_step_ids=[step_id])
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}'
        self.assertEqual(400, self._subject.create(f'{url}/timeline/sync', {}).status_code)
        self._subject.patch(url, {'timeline_sync': {'mode': 'state', 'target': 'cases'}})
        for _ in range(2):
            response = self._subject.create(f'{url}/timeline/sync', {})
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, response.json()['events'])
        self.assertEqual(['Hosts · dc01: Isolated'], [event['event_title'] for event in self._case_events(case_id)])

    def test_sync_should_write_the_past_changes_once(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker_id = self._tracker(room_id, 'assets', name='Hosts').json()['id']
        self._state(room_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], add_step_ids=[step_id])
        self._state(room_id, tracker_id, rows=[{'item_type': 'asset', 'item_id': asset_id}], note='done')
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}'
        self._subject.patch(url, {'timeline_sync': {'mode': 'changes', 'target': 'cases'}})
        listed = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers').json()
        self.assertEqual(2, next(t for t in listed if t['id'] == tracker_id)['timeline_backfill'])
        response = self._subject.create(f'{url}/timeline/sync', {})
        self.assertEqual(200, response.status_code)
        self.assertEqual((2, 0), (response.json()['events'], response.json()['remaining']))
        self.assertEqual(0, self._subject.create(f'{url}/timeline/sync', {}).json()['events'])
        self.assertEqual(['Hosts · dc01: Isolated set', 'Hosts · dc01: note updated'],
                         sorted(event['event_title'] for event in self._case_events(case_id)))

    def test_sync_should_need_war_room_write(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id, timeline_sync={'mode': 'state', 'target': 'war_room'}).json()['id']
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        response = user.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/timeline/sync', {})
        self.assertEqual(403, response.status_code)

    # --------------------------------------------------------- Templates --

    def test_template_should_be_created_applied_and_exported(self):
        response = self._subject.create('/api/v2/manage/tracker-templates', {
            'name': 'Remediation',
            'steps': [{'name': 'Isolated', 'color': 'red'}, {'name': 'Restored'}],
            'trackers': [{'name': 'Assets', 'source': 'assets', 'step_names': ['Isolated']}],
        })
        self.assertEqual(201, response.status_code)
        template_id = response.json()['id']
        room_id = self._room()
        self._step(room_id, 'isolated')
        applied = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/apply-template',
                                       {'template_id': template_id})
        self.assertEqual(200, applied.status_code)
        body = applied.json()
        self.assertEqual(2, len(body['steps']))
        self.assertEqual(1, len(body['trackers']))
        exported = self._subject.get(f'/api/v2/manage/tracker-templates/{template_id}/export').json()
        self.assertEqual('iris-tracker-template', exported['format'])
        self.assertEqual(400, self._subject.create('/api/v2/manage/tracker-templates/import', exported).status_code)
        exported['name'] = 'Remediation copy'
        self.assertEqual(201, self._subject.create('/api/v2/manage/tracker-templates/import', exported).status_code)

    def test_save_as_template_should_return_201(self):
        room_id = self._room()
        step_id = self._step(room_id, 'Isolated').json()['id']
        self._tracker(room_id, 'assets', step_ids=[step_id])
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/save-as-template',
                                        {'name': 'From room'})
        self.assertEqual(201, response.status_code)
        self.assertEqual(['Isolated'], response.json()['trackers'][0]['step_names'])

    def test_preset_should_create_a_template(self):
        presets = self._subject.get('/api/v2/manage/tracker-templates/presets').json()
        self.assertIn('customer-notification', [preset['key'] for preset in presets])
        response = self._subject.create('/api/v2/manage/tracker-templates/presets/customer-notification', {})
        self.assertEqual(201, response.status_code)
        self.assertEqual(5, len(response.json()['steps']))

    def test_template_changes_should_need_server_administrator(self):
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self.assertEqual(200, user.get('/api/v2/manage/tracker-templates').status_code)
        self.assertEqual(403, user.create('/api/v2/manage/tracker-templates', {'name': 'x'}).status_code)
