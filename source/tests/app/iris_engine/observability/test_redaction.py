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

"""Unit tests for app.iris_engine.observability.redaction.

All functions under test are pure Python — no Flask context or database
connection is required to exercise them. The env vars below prevent
app/__init__.py from crashing on the missing DB connection string
(the connection itself is never established during these tests).
"""

import os
import sys

os.environ.setdefault('POSTGRES_SERVER', 'localhost')
os.environ.setdefault('POSTGRES_PORT', '5432')
os.environ.setdefault('POSTGRES_USER', 'iris')
os.environ.setdefault('POSTGRES_PASSWORD', 'iris')
os.environ.setdefault('POSTGRES_DB', 'iris')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))

import unittest

from app.iris_engine.observability.redaction import (
    REDACTED,
    _frame_file_is_sensitive,
    _redact_frame_vars,
    _redact_headers,
    _redact_mapping,
    _redact_query_string,
    _should_redact_key,
    _walk_exception_frames,
    before_breadcrumb,
    make_before_send,
)


# ---------------------------------------------------------------------------
# _should_redact_key
# ---------------------------------------------------------------------------

class TestShouldRedactKey(unittest.TestCase):

    def test_password_true(self):
        self.assertTrue(_should_redact_key('password'))

    def test_token_true(self):
        self.assertTrue(_should_redact_key('token'))

    def test_api_key_true(self):
        self.assertTrue(_should_redact_key('api_key'))

    def test_secret_true(self):
        self.assertTrue(_should_redact_key('secret'))

    def test_ioc_true(self):
        self.assertTrue(_should_redact_key('ioc'))

    def test_evidence_true(self):
        self.assertTrue(_should_redact_key('evidence'))

    def test_malware_true(self):
        self.assertTrue(_should_redact_key('malware'))

    def test_username_false(self):
        self.assertFalse(_should_redact_key('username'))

    def test_email_false(self):
        self.assertFalse(_should_redact_key('email'))

    def test_case_insensitive_password(self):
        self.assertTrue(_should_redact_key('PASSWORD'))

    def test_case_insensitive_token(self):
        self.assertTrue(_should_redact_key('TOKEN'))

    def test_substring_oauth_token(self):
        self.assertTrue(_should_redact_key('oauth_token'))

    def test_substring_db_password(self):
        self.assertTrue(_should_redact_key('db_password'))

    def test_substring_api_key_variant(self):
        self.assertTrue(_should_redact_key('my_api_key'))

    def test_dsn_true(self):
        self.assertTrue(_should_redact_key('dsn'))

    def test_sentry_dsn_true(self):
        self.assertTrue(_should_redact_key('sentry_dsn'))

    def test_credential_true(self):
        self.assertTrue(_should_redact_key('credential'))

    def test_indicator_true(self):
        self.assertTrue(_should_redact_key('indicator'))

    def test_payload_true(self):
        self.assertTrue(_should_redact_key('payload'))

    def test_hash_true(self):
        self.assertTrue(_should_redact_key('hash'))

    def test_artifact_true(self):
        self.assertTrue(_should_redact_key('artifact'))

    def test_safe_name_false(self):
        self.assertFalse(_should_redact_key('case_title'))

    def test_session_true(self):
        self.assertTrue(_should_redact_key('session'))

    def test_cookie_true(self):
        self.assertTrue(_should_redact_key('cookie'))


# ---------------------------------------------------------------------------
# _redact_mapping
# ---------------------------------------------------------------------------

class TestRedactMapping(unittest.TestCase):

    def test_empty_dict(self):
        self.assertEqual({}, _redact_mapping({}))

    def test_non_sensitive_key_preserved(self):
        result = _redact_mapping({'case_id': 42})
        self.assertEqual({'case_id': 42}, result)

    def test_sensitive_key_value_redacted(self):
        result = _redact_mapping({'password': 'hunter2'})
        self.assertEqual({'password': REDACTED}, result)

    def test_token_key_value_redacted(self):
        result = _redact_mapping({'token': 'abc123'})
        self.assertEqual({'token': REDACTED}, result)

    def test_api_key_redacted(self):
        result = _redact_mapping({'api_key': 'secret'})
        self.assertEqual({'api_key': REDACTED}, result)

    def test_nested_dict_sensitive_inner_key_redacted(self):
        result = _redact_mapping({'config': {'password': 's3cr3t', 'host': 'localhost'}})
        self.assertEqual({'config': {'password': REDACTED, 'host': 'localhost'}}, result)

    def test_nested_dict_safe_keys_preserved(self):
        result = _redact_mapping({'meta': {'count': 5, 'label': 'foo'}})
        self.assertEqual({'meta': {'count': 5, 'label': 'foo'}}, result)

    def test_list_value_with_dict_items_redacted(self):
        result = _redact_mapping({'items': [{'password': 'x'}, {'name': 'y'}]})
        self.assertEqual({'items': [{'password': REDACTED}, {'name': 'y'}]}, result)

    def test_list_value_non_dict_items_preserved(self):
        result = _redact_mapping({'tags': ['ioc_list', 'malware_hash']})
        # List values that are plain strings are left as-is (only dict items within a list are recursed)
        self.assertEqual({'tags': ['ioc_list', 'malware_hash']}, result)

    def test_integer_non_string_key_preserved(self):
        result = _redact_mapping({1: 'value'})
        self.assertEqual({1: 'value'}, result)

    def test_original_dict_not_mutated(self):
        original = {'password': 'hunter2', 'name': 'alice'}
        _redact_mapping(original)
        self.assertEqual('hunter2', original['password'])

    def test_ioc_key_redacted(self):
        result = _redact_mapping({'ioc_value': '1.2.3.4'})
        self.assertEqual({'ioc_value': REDACTED}, result)

    def test_evidence_key_redacted(self):
        result = _redact_mapping({'evidence_bytes': b'data'})
        self.assertEqual({'evidence_bytes': REDACTED}, result)


# ---------------------------------------------------------------------------
# _redact_headers
# ---------------------------------------------------------------------------

class TestRedactHeaders(unittest.TestCase):

    def test_dict_authorization_redacted(self):
        result = _redact_headers({'Authorization': 'Bearer token123', 'Content-Type': 'application/json'})
        self.assertEqual(REDACTED, result['Authorization'])
        self.assertEqual('application/json', result['Content-Type'])

    def test_dict_x_api_key_redacted(self):
        result = _redact_headers({'X-API-Key': 'mykey'})
        self.assertEqual(REDACTED, result['X-API-Key'])

    def test_dict_x_custom_token_redacted(self):
        result = _redact_headers({'X-IRIS-Token': 'tok'})
        self.assertEqual(REDACTED, result['X-IRIS-Token'])

    def test_dict_content_type_preserved(self):
        result = _redact_headers({'Content-Type': 'text/plain'})
        self.assertEqual('text/plain', result['Content-Type'])

    def test_dict_proxy_authorization_redacted(self):
        result = _redact_headers({'Proxy-Authorization': 'Basic abc'})
        self.assertEqual(REDACTED, result['Proxy-Authorization'])

    def test_dict_cookie_redacted(self):
        result = _redact_headers({'Cookie': 'session=abc'})
        self.assertEqual(REDACTED, result['Cookie'])

    def test_list_of_pairs_authorization_redacted(self):
        result = _redact_headers([['Authorization', 'Bearer tok'], ['Accept', '*/*']])
        self.assertEqual(['Authorization', REDACTED], result[0])
        self.assertEqual(['Accept', '*/*'], result[1])

    def test_list_of_pairs_x_api_key_redacted(self):
        result = _redact_headers([['X-API-Key', 'k']])
        self.assertEqual(REDACTED, result[0][1])

    def test_list_of_pairs_safe_header_preserved(self):
        result = _redact_headers([['Content-Length', '42']])
        self.assertEqual('42', result[0][1])

    def test_non_dict_non_list_returned_as_is(self):
        self.assertIsNone(_redact_headers(None))
        self.assertEqual('raw', _redact_headers('raw'))
        self.assertEqual(42, _redact_headers(42))

    def test_case_insensitive_authorization(self):
        result = _redact_headers({'authorization': 'Basic xyz'})
        self.assertEqual(REDACTED, result['authorization'])


# ---------------------------------------------------------------------------
# _redact_query_string
# ---------------------------------------------------------------------------

class TestRedactQueryString(unittest.TestCase):

    def test_empty_string_passthrough(self):
        self.assertEqual('', _redact_query_string(''))

    def test_non_string_passthrough(self):
        self.assertIsNone(_redact_query_string(None))
        self.assertEqual(42, _redact_query_string(42))

    def test_sensitive_param_redacted(self):
        result = _redact_query_string('password=hunter2')
        self.assertIn('password=' + REDACTED.replace('[', '%5B').replace(']', '%5D'), result)

    def test_safe_param_preserved(self):
        result = _redact_query_string('page=1')
        self.assertEqual('page=1', result)

    def test_multiple_params_mixed(self):
        result = _redact_query_string('page=1&token=abc&sort=asc')
        self.assertIn('page=1', result)
        self.assertIn('sort=asc', result)
        self.assertNotIn('abc', result)

    def test_api_key_param_redacted(self):
        result = _redact_query_string('api_key=mykey&q=search')
        self.assertNotIn('mykey', result)
        self.assertIn('q=search', result)


# ---------------------------------------------------------------------------
# _frame_file_is_sensitive
# ---------------------------------------------------------------------------

class TestFrameFileIsSensitive(unittest.TestCase):

    def test_mail_secrets_is_sensitive(self):
        self.assertTrue(_frame_file_is_sensitive('iris_engine/mail/secrets.py'))

    def test_access_control_is_sensitive(self):
        self.assertTrue(_frame_file_is_sensitive('iris_engine/access_control/utils.py'))

    def test_mail_config_is_sensitive(self):
        self.assertTrue(_frame_file_is_sensitive('iris_engine/mail/config.py'))

    def test_business_cases_not_sensitive(self):
        self.assertFalse(_frame_file_is_sensitive('app/business/cases.py'))

    def test_non_string_not_sensitive(self):
        self.assertFalse(_frame_file_is_sensitive(None))
        self.assertFalse(_frame_file_is_sensitive(42))
        self.assertFalse(_frame_file_is_sensitive(['iris_engine/mail/secrets.py']))

    def test_absolute_path_mail_secrets_sensitive(self):
        self.assertTrue(_frame_file_is_sensitive('/opt/iris/app/iris_engine/mail/secrets.py'))

    def test_random_path_not_sensitive(self):
        self.assertFalse(_frame_file_is_sensitive('app/utils/helpers.py'))


# ---------------------------------------------------------------------------
# _redact_frame_vars
# ---------------------------------------------------------------------------

class TestRedactFrameVars(unittest.TestCase):

    def test_no_vars_key_is_noop(self):
        frame = {'filename': 'app/foo.py'}
        _redact_frame_vars(frame, strict=False)
        self.assertNotIn('vars', frame)

    def test_non_dict_vars_is_noop(self):
        frame = {'vars': 'not-a-dict'}
        _redact_frame_vars(frame, strict=False)
        self.assertEqual('not-a-dict', frame['vars'])

    def test_strict_true_all_vars_redacted(self):
        frame = {'vars': {'password': 'x', 'name': 'alice', 'count': 5}}
        _redact_frame_vars(frame, strict=True)
        for val in frame['vars'].values():
            self.assertEqual(REDACTED, val)

    def test_strict_false_sensitive_file_all_vars_redacted(self):
        frame = {
            'abs_path': '/opt/iris/iris_engine/mail/secrets.py',
            'vars': {'smtp_pass': 'pw', 'name': 'alice'},
        }
        _redact_frame_vars(frame, strict=False)
        for val in frame['vars'].values():
            self.assertEqual(REDACTED, val)

    def test_strict_false_normal_file_sensitive_key_redacted(self):
        frame = {
            'abs_path': 'app/business/cases.py',
            'vars': {'password': 'pw', 'case_id': 7},
        }
        _redact_frame_vars(frame, strict=False)
        self.assertEqual(REDACTED, frame['vars']['password'])
        self.assertEqual(7, frame['vars']['case_id'])

    def test_strict_false_normal_file_safe_vars_preserved(self):
        frame = {
            'filename': 'app/business/tasks.py',
            'vars': {'task_id': 1, 'title': 'Test'},
        }
        _redact_frame_vars(frame, strict=False)
        self.assertEqual(1, frame['vars']['task_id'])
        self.assertEqual('Test', frame['vars']['title'])

    def test_filename_fallback_used_when_no_abs_path(self):
        frame = {
            'filename': 'iris_engine/access_control/utils.py',
            'vars': {'user': 'alice', 'perm': 'admin'},
        }
        _redact_frame_vars(frame, strict=False)
        for val in frame['vars'].values():
            self.assertEqual(REDACTED, val)


# ---------------------------------------------------------------------------
# _walk_exception_frames
# ---------------------------------------------------------------------------

class TestWalkExceptionFrames(unittest.TestCase):

    def test_missing_exception_key_no_crash(self):
        event = {'message': 'oops'}
        _walk_exception_frames(event, strict=False)  # must not raise

    def test_exception_not_dict_no_crash(self):
        event = {'exception': 'not-a-dict'}
        _walk_exception_frames(event, strict=False)

    def test_values_not_list_no_crash(self):
        event = {'exception': {'values': 'bad'}}
        _walk_exception_frames(event, strict=False)

    def test_malformed_value_entry_no_crash(self):
        event = {'exception': {'values': [None, 'bad', 42]}}
        _walk_exception_frames(event, strict=False)

    def test_missing_stacktrace_no_crash(self):
        event = {'exception': {'values': [{'type': 'ValueError'}]}}
        _walk_exception_frames(event, strict=False)

    def test_valid_structure_frame_vars_redacted(self):
        frame = {'vars': {'password': 'pw', 'case_id': 1}}
        event = {
            'exception': {
                'values': [{
                    'stacktrace': {
                        'frames': [frame],
                    },
                }],
            },
        }
        _walk_exception_frames(event, strict=False)
        self.assertEqual(REDACTED, frame['vars']['password'])
        self.assertEqual(1, frame['vars']['case_id'])

    def test_strict_mode_all_frame_vars_dropped(self):
        frame = {'vars': {'safe_name': 'val', 'token': 'tok'}}
        event = {
            'exception': {
                'values': [{
                    'stacktrace': {
                        'frames': [frame],
                    },
                }],
            },
        }
        _walk_exception_frames(event, strict=True)
        for val in frame['vars'].values():
            self.assertEqual(REDACTED, val)


# ---------------------------------------------------------------------------
# make_before_send
# ---------------------------------------------------------------------------

class TestMakeBeforeSend(unittest.TestCase):

    def _make(self, strict=False):
        return make_before_send(strict_frame_vars=strict)

    def test_returns_callable(self):
        fn = self._make()
        self.assertTrue(callable(fn))

    def test_event_without_request_returned_unchanged(self):
        fn = self._make()
        event = {'level': 'error', 'message': 'oops'}
        result = fn(event, None)
        self.assertEqual(event, result)

    def test_authorization_header_redacted(self):
        fn = self._make()
        event = {'request': {'headers': {'Authorization': 'Bearer tok', 'Content-Type': 'application/json'}}}
        result = fn(event, None)
        self.assertEqual(REDACTED, result['request']['headers']['Authorization'])
        self.assertEqual('application/json', result['request']['headers']['Content-Type'])

    def test_cookies_removed(self):
        fn = self._make()
        event = {'request': {'cookies': {'session': 'abc'}, 'url': '/api'}}
        result = fn(event, None)
        self.assertNotIn('cookies', result['request'])

    def test_query_string_sensitive_param_redacted(self):
        fn = self._make()
        event = {'request': {'query_string': 'token=abc&page=1'}}
        result = fn(event, None)
        qs = result['request']['query_string']
        self.assertNotIn('abc', qs)
        self.assertIn('page=1', qs)

    def test_request_data_dict_redacted(self):
        fn = self._make()
        event = {'request': {'data': {'password': 'secret', 'username': 'alice'}}}
        result = fn(event, None)
        self.assertEqual(REDACTED, result['request']['data']['password'])
        self.assertEqual('alice', result['request']['data']['username'])

    def test_request_data_string_replaced_with_placeholder(self):
        fn = self._make()
        event = {'request': {'data': '{"raw": "json body"}'}}
        result = fn(event, None)
        self.assertEqual('[redacted-body]', result['request']['data'])

    def test_request_data_empty_string_not_redacted(self):
        fn = self._make()
        event = {'request': {'data': ''}}
        result = fn(event, None)
        self.assertEqual('', result['request']['data'])

    def test_exception_frames_redacted(self):
        fn = self._make()
        frame = {'vars': {'password': 'pw', 'case_id': 5}}
        event = {
            'request': {},
            'exception': {
                'values': [{'stacktrace': {'frames': [frame]}}],
            },
        }
        fn(event, None)
        # Frames are mutated in-place
        self.assertEqual(REDACTED, frame['vars']['password'])
        self.assertEqual(5, frame['vars']['case_id'])

    def test_strict_mode_all_frame_vars_blanked(self):
        fn = self._make(strict=True)
        frame = {'vars': {'safe_var': 'value', 'token': 'tok'}}
        event = {
            'exception': {
                'values': [{'stacktrace': {'frames': [frame]}}],
            },
        }
        fn(event, None)
        for val in frame['vars'].values():
            self.assertEqual(REDACTED, val)

    def test_full_event_passthrough_structure_intact(self):
        fn = self._make()
        event = {
            'level': 'error',
            'request': {
                'url': '/api/cases',
                'method': 'POST',
                'headers': {'Content-Type': 'application/json'},
                'data': {'case_id': 1},
            },
        }
        result = fn(event, None)
        self.assertEqual('error', result['level'])
        self.assertEqual('/api/cases', result['request']['url'])
        self.assertEqual(1, result['request']['data']['case_id'])

    def test_list_form_headers_redacted(self):
        fn = self._make()
        event = {'request': {'headers': [['Authorization', 'Bearer x'], ['Accept', '*/*']]}}
        result = fn(event, None)
        headers = result['request']['headers']
        self.assertEqual(REDACTED, headers[0][1])
        self.assertEqual('*/*', headers[1][1])


# ---------------------------------------------------------------------------
# before_breadcrumb
# ---------------------------------------------------------------------------

class TestBeforeBreadcrumb(unittest.TestCase):

    def test_sql_category_returns_none(self):
        crumb = {'category': 'sql', 'message': 'SELECT * FROM cases'}
        self.assertIsNone(before_breadcrumb(crumb, None))

    def test_query_category_returns_none(self):
        crumb = {'category': 'query', 'message': 'SELECT 1'}
        self.assertIsNone(before_breadcrumb(crumb, None))

    def test_other_category_returns_crumb(self):
        crumb = {'category': 'navigation', 'message': 'page changed'}
        result = before_breadcrumb(crumb, None)
        self.assertIs(crumb, result)

    def test_no_category_returns_crumb(self):
        crumb = {'message': 'something happened'}
        result = before_breadcrumb(crumb, None)
        self.assertIs(crumb, result)

    def test_data_dict_with_sensitive_key_redacted(self):
        crumb = {'category': 'http', 'data': {'token': 'abc', 'url': '/api'}}
        result = before_breadcrumb(crumb, None)
        self.assertEqual(REDACTED, result['data']['token'])
        self.assertEqual('/api', result['data']['url'])

    def test_data_dict_safe_keys_preserved(self):
        crumb = {'category': 'ui', 'data': {'component': 'Button', 'action': 'click'}}
        result = before_breadcrumb(crumb, None)
        self.assertEqual('Button', result['data']['component'])
        self.assertEqual('click', result['data']['action'])

    def test_data_non_dict_not_mutated(self):
        crumb = {'category': 'http', 'data': 'raw-string'}
        result = before_breadcrumb(crumb, None)
        self.assertEqual('raw-string', result['data'])

    def test_sql_category_with_case_content_still_dropped(self):
        crumb = {'category': 'sql', 'data': {'query': 'SELECT ioc FROM ...'}}
        self.assertIsNone(before_breadcrumb(crumb, None))


if __name__ == '__main__':
    unittest.main()
