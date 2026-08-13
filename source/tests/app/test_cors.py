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

from flask import Flask
from flask import Response

from app.cors import apply_cors_headers
from app.cors import is_origin_allowed
from app.cors import normalise_origin
from app.cors import parse_allowed_origins
from app.cors import preflight_response
from app.cors import primary_public_url

_PRIMARY = 'https://instance.example.com'
_CUSTOM = 'https://iris.acme.corp'
_ALIASES = [_PRIMARY, _CUSTOM]

_app = Flask(__name__)


def _request(origin=None, method='GET', request_method=None, request_headers=None):
    headers = {}
    if origin is not None:
        headers['Origin'] = origin
    if request_method is not None:
        headers['Access-Control-Request-Method'] = request_method
    if request_headers is not None:
        headers['Access-Control-Request-Headers'] = request_headers
    return _app.test_request_context('/api/v2/cases', method=method, headers=headers)


class TestParseAllowedOrigins(TestCase):

    def test_parse_allowed_origins_should_split_on_commas(self):
        result = parse_allowed_origins(f'{_PRIMARY},{_CUSTOM}')
        self.assertEqual(_ALIASES, result)

    def test_parse_allowed_origins_should_split_on_whitespace(self):
        result = parse_allowed_origins(f'{_PRIMARY} {_CUSTOM}')
        self.assertEqual(_ALIASES, result)

    def test_parse_allowed_origins_should_tolerate_padding_and_trailing_slashes(self):
        result = parse_allowed_origins(f' {_PRIMARY}/ ,  {_CUSTOM}/ ')
        self.assertEqual(_ALIASES, result)

    def test_parse_allowed_origins_should_drop_duplicates_keeping_order(self):
        result = parse_allowed_origins(f'{_CUSTOM},{_PRIMARY},{_CUSTOM}')
        self.assertEqual([_CUSTOM, _PRIMARY], result)

    def test_parse_allowed_origins_should_default_to_wildcard_when_unset(self):
        self.assertEqual(['*'], parse_allowed_origins(None))
        self.assertEqual(['*'], parse_allowed_origins(''))

    def test_parse_allowed_origins_should_drop_entries_without_a_scheme(self):
        # A bare hostname has no safe reading: authorising both schemes
        # would silently allow the plaintext origin too.
        result = parse_allowed_origins(f'instance.example.com,{_CUSTOM}')
        self.assertEqual([_CUSTOM], result)

    def test_normalise_origin_should_strip_path_and_case(self):
        self.assertEqual(_PRIMARY,
                         normalise_origin('HTTPS://Instance.Example.com/some/path'))

    def test_normalise_origin_should_keep_the_port(self):
        self.assertEqual('https://localhost:5173',
                         normalise_origin('https://localhost:5173'))


class TestPrimaryPublicUrl(TestCase):

    def test_primary_public_url_should_be_the_first_configured_origin(self):
        self.assertEqual(_PRIMARY, primary_public_url(_ALIASES))

    def test_primary_public_url_should_be_empty_for_a_wildcard(self):
        # A wildcard says nothing about where the instance lives, and a
        # link starting with `*` is worse than no link.
        self.assertEqual('', primary_public_url(['*']))


class TestIsOriginAllowed(TestCase):

    def test_is_origin_allowed_should_accept_every_configured_hostname(self):
        # The regression this whole module exists for: the second
        # hostname is as valid as the first.
        self.assertTrue(is_origin_allowed(_PRIMARY, _ALIASES))
        self.assertTrue(is_origin_allowed(_CUSTOM, _ALIASES))

    def test_is_origin_allowed_should_reject_an_unlisted_hostname(self):
        self.assertFalse(is_origin_allowed('https://evil.example', _ALIASES))

    def test_is_origin_allowed_should_reject_a_scheme_downgrade(self):
        self.assertFalse(is_origin_allowed('http://iris.acme.corp', _ALIASES))

    def test_is_origin_allowed_should_accept_anything_under_a_wildcard(self):
        self.assertTrue(is_origin_allowed('https://anywhere.example', ['*']))

    def test_is_origin_allowed_should_reject_a_missing_origin(self):
        self.assertFalse(is_origin_allowed(None, _ALIASES))


class TestApplyCorsHeaders(TestCase):

    def test_apply_cors_headers_should_echo_an_allowed_custom_domain(self):
        with _request(origin=_CUSTOM):
            from flask import request
            response = apply_cors_headers(Response(), request, _ALIASES)
        self.assertEqual(_CUSTOM, response.headers['Access-Control-Allow-Origin'])
        self.assertEqual('true', response.headers['Access-Control-Allow-Credentials'])

    def test_apply_cors_headers_should_emit_a_single_allow_origin(self):
        # Two `Access-Control-Allow-Origin` headers on one response make
        # browsers reject it outright.
        with _request(origin=_CUSTOM):
            from flask import request
            response = apply_cors_headers(Response(), request, _ALIASES)
        self.assertEqual(1, len(response.headers.getlist('Access-Control-Allow-Origin')))

    def test_apply_cors_headers_should_vary_on_origin(self):
        with _request(origin=_CUSTOM):
            from flask import request
            response = apply_cors_headers(Response(), request, _ALIASES)
        self.assertIn('Origin', response.headers.get('Vary', ''))

    def test_apply_cors_headers_should_stay_silent_for_an_unlisted_origin(self):
        with _request(origin='https://evil.example'):
            from flask import request
            response = apply_cors_headers(Response(), request, _ALIASES)
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)

    def test_apply_cors_headers_should_not_pair_wildcard_with_credentials(self):
        # `*` plus credentials is a spec violation browsers reject, so
        # claiming it would break the very request it means to allow.
        with _request(origin=_CUSTOM):
            from flask import request
            response = apply_cors_headers(Response(), request, ['*'])
        self.assertEqual('*', response.headers['Access-Control-Allow-Origin'])
        self.assertNotIn('Access-Control-Allow-Credentials', response.headers)

    def test_apply_cors_headers_should_expose_the_request_id(self):
        with _request(origin=_CUSTOM):
            from flask import request
            response = apply_cors_headers(Response(), request, _ALIASES)
        self.assertIn('X-Request-Id', response.headers['Access-Control-Expose-Headers'])


class TestPreflightResponse(TestCase):

    def test_preflight_response_should_answer_a_preflight_from_a_custom_domain(self):
        with _request(origin=_CUSTOM, method='OPTIONS', request_method='PATCH'):
            from flask import request
            response = preflight_response(request, _ALIASES)
        self.assertIsNotNone(response)
        self.assertEqual(204, response.status_code)
        self.assertEqual(_CUSTOM, response.headers['Access-Control-Allow-Origin'])
        self.assertIn('PATCH', response.headers['Access-Control-Allow-Methods'])

    def test_preflight_response_should_echo_the_requested_headers(self):
        with _request(origin=_CUSTOM, method='OPTIONS', request_method='POST',
                      request_headers='authorization, x-request-id'):
            from flask import request
            response = preflight_response(request, _ALIASES)
        self.assertEqual('authorization, x-request-id',
                         response.headers['Access-Control-Allow-Headers'])

    def test_preflight_response_should_ignore_a_plain_options_request(self):
        with _request(origin=_CUSTOM, method='OPTIONS'):
            from flask import request
            self.assertIsNone(preflight_response(request, _ALIASES))

    def test_preflight_response_should_ignore_an_unlisted_origin(self):
        with _request(origin='https://evil.example', method='OPTIONS',
                      request_method='POST'):
            from flask import request
            self.assertIsNone(preflight_response(request, _ALIASES))
