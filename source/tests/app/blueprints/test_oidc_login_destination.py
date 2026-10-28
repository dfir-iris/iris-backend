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

"""Unit tests for the OIDC post-login destination plumbing.

A user who opens a notification link while unauthenticated used to be sent
to the OIDC login page without the intended destination, so the SPA always
landed on `/` after authenticating. The destination is now read from the
`redirect` (or historic `next`) argument and sanitised before it is stored
in the session and echoed back to the SPA as `/login?oidc=1&redirect=...`.

What is under test here is the two pure helpers: which argument wins, what
gets rejected, and how the final login page URL is built. The request-time
wiring (session storage across the provider round trip) is exercised by the
route code, but the security-relevant decisions are concentrated here.
"""

from unittest import TestCase

from app.blueprints.pages.login.login_routes import _oidc_destination_from_args
from app.blueprints.pages.login.login_routes import _oidc_login_page_url


class TestOidcDestinationFromArgs(TestCase):

    def test_redirect_is_preferred_over_next(self):
        destination = _oidc_destination_from_args(
            {'redirect': '/case/1/tasks/2', 'next': '/dashboard?cid=1'}
        )
        self.assertEqual('/case/1/tasks/2', destination)

    def test_next_is_used_when_redirect_is_absent(self):
        destination = _oidc_destination_from_args({'next': '/case/1/tasks/2'})
        self.assertEqual('/case/1/tasks/2', destination)

    def test_absolute_url_is_rejected(self):
        self.assertIsNone(
            _oidc_destination_from_args({'redirect': 'https://evil.com/case/1'})
        )

    def test_protocol_relative_url_is_rejected(self):
        self.assertIsNone(_oidc_destination_from_args({'redirect': '//evil.com'}))

    def test_login_page_itself_is_rejected(self):
        self.assertIsNone(_oidc_destination_from_args({'redirect': '/login'}))

    def test_login_page_subpath_is_rejected(self):
        self.assertIsNone(_oidc_destination_from_args({'redirect': '/login/mfa-verify'}))

    def test_missing_arguments_return_none(self):
        self.assertIsNone(_oidc_destination_from_args({}))

    def test_empty_arguments_return_none(self):
        self.assertIsNone(_oidc_destination_from_args({'redirect': '', 'next': ''}))


class TestOidcLoginPageUrl(TestCase):

    def test_login_page_url_without_destination(self):
        self.assertEqual('/login?oidc=1', _oidc_login_page_url(None))

    def test_login_page_url_carries_encoded_destination(self):
        self.assertEqual(
            '/login?oidc=1&redirect=%2Fcase%2F1%2Ftasks%2F2',
            _oidc_login_page_url('/case/1/tasks/2'),
        )

    def test_login_page_url_encodes_query_in_destination(self):
        self.assertEqual(
            '/login?oidc=1&redirect=%2Fdashboard%3Fcid%3D1',
            _oidc_login_page_url('/dashboard?cid=1'),
        )
