#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure validation helpers in business/war_room_chat.py.

These helpers validate and normalise client-supplied inputs — no DB,
no socket, no Flask context needed.
"""

import datetime
from unittest import TestCase

from app.business.war_room_chat import (
    _parse_closes_at,
    _validate_body,
    _validate_kind,
    _validate_poll_options,
    _validate_thread_title,
    _validate_topic_name,
)
from app.models.errors import BusinessProcessingError


class TestValidateKind(TestCase):

    def test_none_defaults_to_message(self):
        self.assertEqual('message', _validate_kind(None))

    def test_valid_message_kind(self):
        self.assertEqual('message', _validate_kind('message'))

    def test_valid_system_kind(self):
        self.assertEqual('system', _validate_kind('system'))

    def test_invalid_kind_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind('invalid_kind')

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind(123)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind('')


class TestValidateBody(TestCase):

    def test_none_body_for_system_message_allowed(self):
        result = _validate_body(None, 'system')
        self.assertIsNone(result)

    def test_none_body_for_user_message_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body(None, 'message')

    def test_non_string_body_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body(123, 'message')

    def test_valid_string_body_returned(self):
        result = _validate_body('Hello world', 'message')
        self.assertEqual('Hello world', result)

    def test_empty_string_body_for_message_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body('', 'message')

    def test_empty_string_body_for_system_allowed(self):
        result = _validate_body('', 'system')
        self.assertEqual('', result)


class TestValidateThreadTitle(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_thread_title(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_thread_title(''))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_validate_thread_title('   '))

    def test_valid_title_returned_stripped(self):
        result = _validate_thread_title('  My Thread  ')
        self.assertEqual('My Thread', result)

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_thread_title(42)

    def test_title_too_long_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_thread_title('x' * 161)

    def test_title_at_limit_accepted(self):
        result = _validate_thread_title('a' * 160)
        self.assertEqual('a' * 160, result)


class TestValidatePollOptions(TestCase):

    def test_non_list_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options('not a list')

    def test_too_few_options_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options(['Only one'])

    def test_two_options_accepted(self):
        result = _validate_poll_options(['A', 'B'])
        self.assertEqual(['A', 'B'], result)

    def test_options_stripped(self):
        result = _validate_poll_options(['  Yes  ', '  No  '])
        self.assertEqual(['Yes', 'No'], result)

    def test_too_many_options_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options([f'opt{i}' for i in range(21)])

    def test_twenty_options_accepted(self):
        opts = [f'option_{i}' for i in range(20)]
        result = _validate_poll_options(opts)
        self.assertEqual(20, len(result))

    def test_option_too_long_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options(['A', 'x' * 257])

    def test_non_string_option_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options(['Valid', 123])

    def test_empty_option_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options(['Valid', ''])

    def test_whitespace_only_option_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_poll_options(['Valid', '   '])


class TestValidateTopicName(TestCase):

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_topic_name(42)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_topic_name('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_topic_name('   ')

    def test_valid_name_returned_stripped(self):
        result = _validate_topic_name('  My Topic  ')
        self.assertEqual('My Topic', result)

    def test_name_too_long_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_topic_name('x' * 81)

    def test_name_at_limit_accepted(self):
        result = _validate_topic_name('a' * 80)
        self.assertEqual('a' * 80, result)


class TestParseClosesAt(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_parse_closes_at(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_closes_at(''))

    def test_datetime_object_passthrough(self):
        dt = datetime.datetime(2026, 6, 1, 12, 0)
        result = _parse_closes_at(dt)
        self.assertIsInstance(result, datetime.datetime)

    def test_iso8601_string_parsed(self):
        result = _parse_closes_at('2026-06-01T12:00:00')
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(2026, result.year)
        self.assertEqual(6, result.month)

    def test_invalid_format_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _parse_closes_at('not-a-date')
