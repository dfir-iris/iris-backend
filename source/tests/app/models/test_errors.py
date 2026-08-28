#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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
from unittest.mock import patch

from app.models.errors import BusinessProcessingError
from app.models.errors import ElementInUseError
from app.models.errors import ObjectNotFoundError
from app.models.errors import UnhandledBusinessError


class TestBusinessProcessingError(TestCase):

    def test_get_message_returns_message(self):
        error = BusinessProcessingError('something went wrong')
        self.assertEqual('something went wrong', error.get_message())

    def test_get_data_returns_none_when_not_provided(self):
        error = BusinessProcessingError('msg')
        self.assertIsNone(error.get_data())

    def test_get_data_returns_provided_data(self):
        data = {'key': 'value'}
        error = BusinessProcessingError('msg', data)
        self.assertEqual({'key': 'value'}, error.get_data())

    def test_is_exception_subclass(self):
        error = BusinessProcessingError('msg')
        self.assertIsInstance(error, Exception)

    def test_constructor_with_explicit_none_data(self):
        error = BusinessProcessingError('msg', None)
        self.assertIsNone(error.get_data())

    def test_message_can_be_empty_string(self):
        error = BusinessProcessingError('')
        self.assertEqual('', error.get_message())

    def test_data_can_be_a_list(self):
        error = BusinessProcessingError('msg', [1, 2, 3])
        self.assertEqual([1, 2, 3], error.get_data())

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(BusinessProcessingError):
            raise BusinessProcessingError('raised')


class TestObjectNotFoundError(TestCase):

    def test_is_business_processing_error(self):
        error = ObjectNotFoundError()
        self.assertIsInstance(error, BusinessProcessingError)

    def test_get_message_returns_object_not_found(self):
        error = ObjectNotFoundError()
        self.assertEqual('Object not found', error.get_message())

    def test_get_data_returns_none(self):
        error = ObjectNotFoundError()
        self.assertIsNone(error.get_data())

    def test_constructor_takes_no_arguments(self):
        # should not raise
        ObjectNotFoundError()

    def test_can_be_raised_and_caught_as_business_processing_error(self):
        with self.assertRaises(BusinessProcessingError):
            raise ObjectNotFoundError()


class TestUnhandledBusinessError(TestCase):

    def test_get_message_returns_message(self):
        with patch('app.models.errors.logger'):
            error = UnhandledBusinessError('unhandled error')
        self.assertEqual('unhandled error', error.get_message())

    def test_get_data_returns_none_when_not_provided(self):
        with patch('app.models.errors.logger'):
            error = UnhandledBusinessError('msg')
        self.assertIsNone(error.get_data())

    def test_get_data_returns_provided_data(self):
        with patch('app.models.errors.logger'):
            error = UnhandledBusinessError('msg', {'extra': 'info'})
        self.assertEqual({'extra': 'info'}, error.get_data())

    def test_is_business_processing_error(self):
        with patch('app.models.errors.logger'):
            error = UnhandledBusinessError('msg')
        self.assertIsInstance(error, BusinessProcessingError)

    def test_logger_exception_called_on_construction(self):
        with patch('app.models.errors.logger') as mock_logger:
            UnhandledBusinessError('bang', 'extra')
        self.assertEqual(2, mock_logger.exception.call_count)

    def test_can_be_raised_and_caught(self):
        with patch('app.models.errors.logger'):
            with self.assertRaises(UnhandledBusinessError):
                raise UnhandledBusinessError('raised')


class TestElementInUseError(TestCase):

    def test_is_business_processing_error(self):
        error = ElementInUseError('in use')
        self.assertIsInstance(error, BusinessProcessingError)

    def test_get_message_returns_message(self):
        error = ElementInUseError('resource in use')
        self.assertEqual('resource in use', error.get_message())

    def test_get_data_returns_none_by_default(self):
        error = ElementInUseError('in use')
        self.assertIsNone(error.get_data())

    def test_get_data_returns_provided_data(self):
        error = ElementInUseError('in use', {'id': 42})
        self.assertEqual({'id': 42}, error.get_data())
