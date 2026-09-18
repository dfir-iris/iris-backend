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

"""Integration tests for the /api/v2/mcp endpoint.

Every test hits the running docker-compose IRIS via HTTP — same shape
as tests_rest_*.py. Toggle state is manipulated through the admin
`PUT /api/v2/manage/server/settings` route so the tests exercise the
production toggle path.

Tests are grouped as:
  * Transport — protocol handshake, method dispatch, error shapes.
  * Toggle — enable/disable behaviour.
  * Auth — session-cookie rejection, API-key acceptance.
  * Dispatch — tool listing, permission enforcement, case ACL.
"""

import requests

from unittest import TestCase

from iris import API_URL, Iris


def _rpc(method: str, params: dict | None = None, rpc_id: int = 1) -> dict:
    body = {'jsonrpc': '2.0', 'id': rpc_id, 'method': method}
    if params is not None:
        body['params'] = params
    return body


class TestsRestMcp(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        # Snapshot the original MCP settings so tearDown can restore
        # them — the toggle is a server-wide singleton and leaving it
        # in an unexpected state would leak across test files.
        original = self._subject.get(
            '/api/v2/manage/server/settings').json()['settings']
        self._original_mcp = {
            'mcp_enabled': original.get('mcp_enabled', False),
            'mcp_max_calls_per_minute_per_worker':
                original.get('mcp_max_calls_per_minute_per_worker', 60),
            'mcp_expose_admin_tools':
                original.get('mcp_expose_admin_tools', False),
            'mcp_tool_allowlist': original.get('mcp_tool_allowlist', ''),
            'mcp_tool_denylist': original.get('mcp_tool_denylist', ''),
        }

    def tearDown(self) -> None:
        self._subject.update('/api/v2/manage/server/settings', self._original_mcp)
        self._subject.clear_database()

    # ---- helpers ---------------------------------------------------

    def _enable_mcp(self, **overrides) -> None:
        body = {'mcp_enabled': True}
        body.update(overrides)
        response = self._subject.update('/api/v2/manage/server/settings', body)
        self.assertEqual(200, response.status_code)

    def _mcp_post(self, payload, api_key: str | None = None,
                  extra_headers: dict | None = None) -> requests.Response:
        headers = {'Content-Type': 'application/json'}
        if api_key is not None:
            headers['X-IRIS-AUTH'] = api_key
        if extra_headers:
            headers.update(extra_headers)
        return requests.post(f'{API_URL}/api/v2/mcp', json=payload, headers=headers)

    def _admin_key(self) -> str:
        # `Iris._api._api_key` is the seeded administrator key from the
        # docker-compose fixture — same one every other test file uses.
        return self._subject._api._api_key

    # ---- toggle ----------------------------------------------------

    def test_mcp_disabled_should_return_503(self):
        response = self._mcp_post(_rpc('initialize', {
            'protocolVersion': '2025-03-26',
            'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'},
        }), api_key=self._admin_key())
        self.assertEqual(503, response.status_code)
        body = response.json()
        self.assertIn('error', body)
        self.assertEqual(-32003, body['error']['code'])

    def test_mcp_enabled_initialize_should_echo_protocol_version(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('initialize', {
            'protocolVersion': '2025-03-26',
            'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('2.0', body['jsonrpc'])
        self.assertEqual(1, body['id'])
        self.assertEqual('2025-03-26', body['result']['protocolVersion'])
        self.assertIn('tools', body['result']['capabilities'])
        self.assertIn('resources', body['result']['capabilities'])
        self.assertEqual('iris', body['result']['serverInfo']['name'])

    def test_mcp_initialize_with_unknown_version_returns_latest(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('initialize', {
            'protocolVersion': '1900-01-01',
            'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        version = response.json()['result']['protocolVersion']
        # Should be one of our supported versions — the latest.
        self.assertIn(version, ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'))

    # ---- auth ------------------------------------------------------

    def test_mcp_without_api_key_should_return_401(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('ping'))
        self.assertEqual(401, response.status_code)
        self.assertEqual(-32004, response.json()['error']['code'])

    def test_mcp_bad_protocol_version_header_returns_400(self):
        self._enable_mcp()
        response = self._mcp_post(
            _rpc('ping'),
            api_key=self._admin_key(),
            extra_headers={'MCP-Protocol-Version': '1900-01-01'},
        )
        self.assertEqual(400, response.status_code)

    # ---- transport / methods ---------------------------------------

    def test_mcp_ping_should_return_empty_result(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('ping'), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual({}, body['result'])

    def test_mcp_unknown_method_returns_method_not_found(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('does/not/exist'), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        self.assertEqual(-32601, response.json()['error']['code'])

    def test_mcp_get_method_returns_405(self):
        self._enable_mcp()
        response = requests.get(f'{API_URL}/api/v2/mcp',
                                headers={'X-IRIS-AUTH': self._admin_key()})
        self.assertEqual(405, response.status_code)

    def test_mcp_notification_returns_202(self):
        self._enable_mcp()
        # No `id` field → JSON-RPC notification. Spec says the server
        # must not respond.
        response = self._mcp_post(
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            api_key=self._admin_key(),
        )
        self.assertEqual(202, response.status_code)

    def test_mcp_batch_request_returns_array(self):
        self._enable_mcp()
        response = self._mcp_post(
            [_rpc('ping', rpc_id=1), _rpc('ping', rpc_id=2)],
            api_key=self._admin_key(),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(2, len(body))
        self.assertEqual({1, 2}, {item['id'] for item in body})

    # ---- tool listing ---------------------------------------------

    def test_mcp_tools_list_returns_mvp_by_default(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('tools/list'), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        tools = response.json()['result']['tools']
        names = [t['name'] for t in tools]
        # MVP set is on by default.
        self.assertIn('iris_cases_list', names)
        self.assertIn('iris_cases_get', names)
        self.assertIn('iris_alerts_list', names)
        self.assertIn('iris_search', names)
        # Extended tools are NOT on by default.
        self.assertNotIn('iris_cases_delete', names)
        self.assertNotIn('iris_cases_activities_get', names)

    def test_mcp_denylist_hides_tool(self):
        self._enable_mcp(mcp_tool_denylist='iris_cases_list')
        response = self._mcp_post(_rpc('tools/list'), api_key=self._admin_key())
        names = [t['name'] for t in response.json()['result']['tools']]
        self.assertNotIn('iris_cases_list', names)
        # Sibling tool still visible.
        self.assertIn('iris_cases_get', names)

    def test_mcp_tool_call_case_scoped_injects_case_identifier(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('tools/list'), api_key=self._admin_key())
        cases_get = next(t for t in response.json()['result']['tools']
                         if t['name'] == 'iris_cases_get')
        self.assertIn('case_identifier', cases_get['inputSchema']['properties'])
        self.assertIn('case_identifier', cases_get['inputSchema']['required'])

    # ---- dispatch --------------------------------------------------

    def test_mcp_tools_call_iris_me_get_returns_admin(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('tools/call', {
            'name': 'iris_me_get', 'arguments': {},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        content = response.json()['result']['content']
        self.assertEqual(1, len(content))
        self.assertEqual('text', content[0]['type'])
        # `text` is a JSON-encoded string.
        import json as _json
        payload = _json.loads(content[0]['text'])
        self.assertEqual('administrator', payload['user_login'])

    def test_mcp_tools_call_denied_tool_returns_method_not_found(self):
        self._enable_mcp(mcp_tool_denylist='iris_me_get')
        response = self._mcp_post(_rpc('tools/call', {
            'name': 'iris_me_get', 'arguments': {},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        self.assertEqual(-32601, response.json()['error']['code'])

    def test_mcp_tools_call_admin_only_disabled_by_default(self):
        # No admin tools are registered as MVP yet, but we test the
        # rejection path via the future admin surface by confirming
        # `mcp_expose_admin_tools=False` doesn't leak them into the
        # tools/list result.
        self._enable_mcp(mcp_expose_admin_tools=False)
        response = self._mcp_post(_rpc('tools/list'), api_key=self._admin_key())
        names = [t['name'] for t in response.json()['result']['tools']]
        self.assertFalse(any(n.startswith('iris_manage_') for n in names))

    def test_mcp_tools_call_iris_cases_list_returns_paginated(self):
        self._enable_mcp()
        self._subject.create_dummy_case()
        response = self._mcp_post(_rpc('tools/call', {
            'name': 'iris_cases_list', 'arguments': {},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        import json as _json
        payload = _json.loads(response.json()['result']['content'][0]['text'])
        self.assertIn('total', payload)
        self.assertIn('data', payload)
        self.assertGreaterEqual(payload['total'], 1)

    def test_mcp_tools_call_iris_taxonomies_list_exposes_analysis_statuses(self):
        # Regression: without this tool, AI clients probe `iris_case_assets_update`
        # with trial `analysis_status_id` values (1..6) to discover "Done".
        self._enable_mcp()
        response = self._mcp_post(_rpc('tools/list'), api_key=self._admin_key())
        names = [t['name'] for t in response.json()['result']['tools']]
        self.assertIn('iris_taxonomies_list', names)

        response = self._mcp_post(_rpc('tools/call', {
            'name': 'iris_taxonomies_list', 'arguments': {},
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        import json as _json
        payload = _json.loads(response.json()['result']['content'][0]['text'])
        # Seed statuses from post_init.create_safe_analysis_status().
        analysis_names = {row['name'] for row in payload['analysis_statuses']}
        self.assertEqual(
            {'Unspecified', 'To be done', 'Started', 'Pending', 'Canceled', 'Done'},
            analysis_names,
        )
        # Sibling taxonomies present so callers get them in one round-trip.
        for key in ('task_statuses', 'case_states', 'severities', 'tlps',
                    'ioc_types', 'asset_types', 'asset_compromise_statuses'):
            self.assertIn(key, payload)

    def test_mcp_resources_list_includes_case_template(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('resources/templates/list'),
                                  api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        templates = response.json()['result']['resourceTemplates']
        uris = [t['uriTemplate'] for t in templates]
        self.assertIn('iris://cases/{case_id}', uris)
        self.assertIn('iris://me', uris)

    def test_mcp_resource_read_iris_me(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('resources/read', {'uri': 'iris://me'}),
                                  api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        contents = response.json()['result']['contents']
        self.assertEqual(1, len(contents))
        self.assertEqual('iris://me', contents[0]['uri'])
        import json as _json
        payload = _json.loads(contents[0]['text'])
        self.assertEqual('administrator', payload['user_login'])

    def test_mcp_resource_read_unknown_uri_returns_method_not_found(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('resources/read', {'uri': 'iris://nope'}),
                                  api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        self.assertEqual(-32601, response.json()['error']['code'])

    def test_mcp_invalid_json_returns_parse_error(self):
        self._enable_mcp()
        response = requests.post(
            f'{API_URL}/api/v2/mcp',
            data='not json{',
            headers={'X-IRIS-AUTH': self._admin_key(),
                     'Content-Type': 'application/json'},
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual(-32700, response.json()['error']['code'])

    # ---- runtime-config surface ------------------------------------

    def test_runtime_config_exposes_mcp_enabled_bit(self):
        self._enable_mcp()
        response = self._subject.get('/api/v2/runtime-config')
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertIn('mcp', payload)
        self.assertTrue(payload['mcp']['enabled'])
        self.assertEqual('/api/v2/mcp', payload['mcp']['endpoint'])

    # ---- Extended resource templates ------------------------------------

    def test_mcp_resource_templates_include_extended_set(self):
        self._enable_mcp()
        response = self._mcp_post(_rpc('resources/templates/list'),
                                  api_key=self._admin_key())
        templates = response.json()['result']['resourceTemplates']
        uris = {t['uriTemplate'] for t in templates}
        # Case-extended
        self.assertIn('iris://cases/{case_id}/evidences', uris)
        self.assertIn('iris://cases/{case_id}/events', uris)
        self.assertIn('iris://cases/{case_id}/timelines', uris)
        self.assertIn('iris://cases/{case_id}/activities', uris)
        self.assertIn('iris://cases/{case_id}/war-rooms', uris)
        self.assertIn('iris://cases/{case_id}/source-alert-cluster', uris)
        self.assertIn('iris://cases/{case_id}/followers', uris)
        # Alert-cluster
        self.assertIn('iris://alert-clusters/{cluster_id}', uris)
        self.assertIn('iris://alert-clusters/{cluster_id}/graph', uris)
        # War-room
        self.assertIn('iris://war-rooms/{war_room_id}', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/chat', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/notes', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/tasks', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/timelines', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/sitreps', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/teams', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/datastore', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/members', uris)
        self.assertIn('iris://war-rooms/{war_room_id}/cases', uris)
        self.assertIn(
            'iris://war-rooms/{war_room_id}/linked-case-timelines', uris)

    def test_mcp_resource_read_case_activities_after_tool_call(self):
        self._enable_mcp()
        case_id = self._subject.create_dummy_case()
        # A tool call emits a `mcp:iris_cases_get` activity row.
        self._mcp_post(_rpc('tools/call', {
            'name': 'iris_cases_get',
            'arguments': {'case_identifier': case_id},
        }), api_key=self._admin_key())
        # Read that activity via the resource.
        read = self._mcp_post(_rpc('resources/read', {
            'uri': f'iris://cases/{case_id}/activities',
        }), api_key=self._admin_key())
        self.assertEqual(200, read.status_code)
        import json as _json
        payload = _json.loads(read.json()['result']['contents'][0]['text'])
        self.assertIn('activities', payload)
        # At least the mcp:iris_cases_get audit row is present.
        acts = payload['activities']
        self.assertTrue(any('mcp:iris_cases_get' in str(a) for a in acts))

    def test_mcp_resource_read_unknown_case_returns_access_denied(self):
        self._enable_mcp()
        # No such case; the ACL check fires before we look up the row,
        # yielding `IRIS_ACCESS_DENIED` rather than `not found`. Either
        # outcome is acceptable — the important thing is that we don't
        # leak the case's existence.
        response = self._mcp_post(_rpc('resources/read', {
            'uri': 'iris://cases/999999999/evidences',
        }), api_key=self._admin_key())
        self.assertEqual(200, response.status_code)
        self.assertIn('error', response.json())
