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

from sqlalchemy import asc
from sqlalchemy import desc

from app.datamgmt.conversions import convert_sort_direction


class TestConvertSortDirection(TestCase):

    def test_desc_string_returns_desc(self):
        result = convert_sort_direction('desc')
        self.assertIs(desc, result)

    def test_asc_string_returns_asc(self):
        result = convert_sort_direction('asc')
        self.assertIs(asc, result)

    def test_none_returns_asc(self):
        result = convert_sort_direction(None)
        self.assertIs(asc, result)

    def test_unknown_string_returns_asc(self):
        result = convert_sort_direction('random')
        self.assertIs(asc, result)

    def test_empty_string_returns_asc(self):
        result = convert_sort_direction('')
        self.assertIs(asc, result)

    def test_uppercase_desc_returns_asc(self):
        # only lowercase 'desc' is recognized
        result = convert_sort_direction('DESC')
        self.assertIs(asc, result)

    def test_uppercase_asc_returns_asc_as_default(self):
        result = convert_sort_direction('ASC')
        self.assertIs(asc, result)

    def test_whitespace_returns_asc(self):
        result = convert_sort_direction(' ')
        self.assertIs(asc, result)
