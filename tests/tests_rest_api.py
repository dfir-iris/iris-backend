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

from unittest import TestCase

from iris import Iris


class TestsRestApi(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def test_v2_ping_should_return_200_and_pong(self):
        response = self._subject.get('/api/v2/ping')
        self.assertEqual(200, response.status_code)
        self.assertEqual('pong', response.json())

    def test_v2_versions_should_return_iris_and_api_versions(self):
        response = self._subject.get('/api/v2/versions')
        self.assertEqual(200, response.status_code)
        payload = response.json()
        for key in ('iris_current', 'api_min', 'api_current'):
            self.assertIn(key, payload)

    def test_v1_ping_should_still_work_with_deprecation_header(self):
        response = self._subject.get('/api/ping')
        self.assertEqual(200, response.status_code)
        self.assertEqual('true', response.headers.get('Deprecation'))
        link = response.headers.get('Link', '')
        self.assertIn('/api/v2/ping', link)

    def test_v1_versions_should_still_work_with_deprecation_header(self):
        response = self._subject.get('/api/versions')
        self.assertEqual(200, response.status_code)
        self.assertEqual('true', response.headers.get('Deprecation'))
        link = response.headers.get('Link', '')
        self.assertIn('/api/v2/versions', link)

    def test_v2_openapi_yaml_should_return_the_generated_spec(self):
        # The static spec file shipped alongside api.py — same one the
        # CI drift check regenerates and compares.
        response = self._subject.get('/api/v2/openapi.yaml')
        self.assertEqual(200, response.status_code)
        body = response.text
        self.assertTrue(body.startswith('openapi: 3.1.0'))
        # Sanity-check that a well-known operation shows up so a
        # regression in the send_file wiring doesn't ship an empty spec.
        self.assertIn('/api/v2/cases', body)

    def test_v2_docs_should_serve_the_redoc_html_shell(self):
        response = self._subject.get('/api/v2/docs')
        self.assertEqual(200, response.status_code)
        self.assertIn('text/html', response.headers.get('Content-Type', ''))
        body = response.text
        self.assertIn('spec-url="/api/v2/openapi.yaml"', body)
        self.assertIn('redoc.standalone.js', body)
