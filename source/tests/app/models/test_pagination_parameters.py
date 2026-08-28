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

from app.models.pagination_parameters import PaginationParameters


class TestPaginationParameters(TestCase):

    def test_get_page_returns_page(self):
        params = PaginationParameters(1, 10, 'name', 'asc')
        self.assertEqual(1, params.get_page())

    def test_get_per_page_returns_per_page(self):
        params = PaginationParameters(1, 25, 'name', 'asc')
        self.assertEqual(25, params.get_per_page())

    def test_get_order_by_returns_order_by(self):
        params = PaginationParameters(1, 10, 'created_at', 'asc')
        self.assertEqual('created_at', params.get_order_by())

    def test_get_direction_returns_direction(self):
        params = PaginationParameters(1, 10, 'name', 'desc')
        self.assertEqual('desc', params.get_direction())

    def test_all_getters_with_typical_values(self):
        params = PaginationParameters(3, 50, 'id', 'asc')
        self.assertEqual(3, params.get_page())
        self.assertEqual(50, params.get_per_page())
        self.assertEqual('id', params.get_order_by())
        self.assertEqual('asc', params.get_direction())

    def test_page_of_zero(self):
        params = PaginationParameters(0, 10, 'name', 'asc')
        self.assertEqual(0, params.get_page())

    def test_per_page_of_one(self):
        params = PaginationParameters(1, 1, 'name', 'asc')
        self.assertEqual(1, params.get_per_page())

    def test_none_values_are_preserved(self):
        params = PaginationParameters(None, None, None, None)
        self.assertIsNone(params.get_page())
        self.assertIsNone(params.get_per_page())
        self.assertIsNone(params.get_order_by())
        self.assertIsNone(params.get_direction())

    def test_large_page_number(self):
        params = PaginationParameters(9999, 100, 'updated_at', 'desc')
        self.assertEqual(9999, params.get_page())
        self.assertEqual(100, params.get_per_page())

    def test_direction_asc_string(self):
        params = PaginationParameters(1, 10, 'name', 'asc')
        self.assertEqual('asc', params.get_direction())
