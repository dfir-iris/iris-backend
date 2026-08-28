#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from app.datamgmt.persistence_error import PersistenceError


class TestPersistenceError(TestCase):

    def test_inherits_from_exception(self):
        underlying = ValueError('db error')
        error = PersistenceError(underlying)
        self.assertIsInstance(error, Exception)

    def test_get_underlying_exception_returns_wrapped_exception(self):
        underlying = ValueError('db error')
        error = PersistenceError(underlying)
        self.assertIs(underlying, error.get_underlying_exception())

    def test_underlying_exception_preserves_type(self):
        underlying = RuntimeError('timeout')
        error = PersistenceError(underlying)
        self.assertIsInstance(error.get_underlying_exception(), RuntimeError)

    def test_underlying_exception_preserves_message(self):
        underlying = IOError('disk full')
        error = PersistenceError(underlying)
        self.assertEqual('disk full', str(error.get_underlying_exception()))

    def test_can_wrap_any_exception_type(self):
        underlying = KeyError('missing key')
        error = PersistenceError(underlying)
        self.assertIs(underlying, error.get_underlying_exception())

    def test_can_be_raised_and_caught(self):
        underlying = ValueError('original')
        with self.assertRaises(PersistenceError):
            raise PersistenceError(underlying)

    def test_can_be_caught_as_exception(self):
        underlying = ValueError('original')
        with self.assertRaises(Exception):
            raise PersistenceError(underlying)

    def test_underlying_exception_accessible_after_catch(self):
        underlying = TypeError('bad type')
        try:
            raise PersistenceError(underlying)
        except PersistenceError as e:
            self.assertIs(underlying, e.get_underlying_exception())
