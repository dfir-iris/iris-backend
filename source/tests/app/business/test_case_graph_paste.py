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

"""Unit tests for the indicator extraction of the case graph paste (§4.5)."""

from unittest import TestCase

from app.business.case_graph_paste import case_graph_paste_extract
from app.business.case_graph_paste import case_graph_paste_plan

_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
_MD5 = 'd41d8cd98f00b204e9800998ecf8427e'

_IOC_TYPES = [{'type_id': 1, 'type_name': 'ip-dst'}, {'type_id': 2, 'type_name': 'ip-any'},
              {'type_id': 3, 'type_name': 'domain'}, {'type_id': 4, 'type_name': 'url'},
              {'type_id': 5, 'type_name': 'sha256'}, {'type_id': 6, 'type_name': 'email-src'},
              {'type_id': 7, 'type_name': 'md5'}]


class TestExtract(TestCase):

    def test_kinds_in_order_of_appearance(self):
        text = (f'Beacon to 10.0.0.5 then https://evil.example/a?b=1. Mail from bob@corp.example, '
                f'dropper {_SHA256} talks to c2.evil.example and 2001:db8::1')
        self.assertEqual(['10.0.0.5', 'https://evil.example/a?b=1', 'bob@corp.example', _SHA256,
                          'c2.evil.example', '2001:db8::1'], case_graph_paste_extract(text))

    def test_refang(self):
        self.assertEqual(['http://evil.example/x', 'evil.example', '10.0.0.1', 'a@b.example'],
                         case_graph_paste_extract('hxxp://evil[.]example/x evil(.)example 10[.]0[.]0[.]1 a[@]b.example'))

    def test_file_names_are_not_domains(self):
        self.assertEqual(['evil.example'], case_graph_paste_extract('ran payload.exe and report.pdf from evil.example'))

    def test_url_trailing_punctuation(self):
        self.assertEqual(['https://evil.example/path'], case_graph_paste_extract('(see https://evil.example/path).'))

    def test_url_host_is_not_extracted_again(self):
        self.assertEqual(['https://evil.example/'], case_graph_paste_extract('https://evil.example/'))

    def test_email_domain_is_not_extracted_again(self):
        self.assertEqual(['bob@corp.example'], case_graph_paste_extract('bob@corp.example'))

    def test_dedupe_case_insensitive(self):
        self.assertEqual(['Evil.example'], case_graph_paste_extract('Evil.example evil.EXAMPLE evil.example'))

    def test_hashes(self):
        self.assertEqual([_MD5], case_graph_paste_extract(f'md5={_MD5}'))
        self.assertEqual([], case_graph_paste_extract(f'{_MD5}ab'))

    def test_invalid_ipv4_and_times(self):
        self.assertEqual([], case_graph_paste_extract('999.1.1.1 at 12:30:45'))

    def test_empty(self):
        self.assertEqual([], case_graph_paste_extract(''))
        self.assertEqual([], case_graph_paste_extract('   '))
        self.assertEqual([], case_graph_paste_extract(None))


class TestPlan(TestCase):

    def test_create_existing_ignored(self):
        values = ['10.0.0.5', 'Evil.example', _SHA256, 'bob@corp.example', 'unknown-thing']
        existing = [{'ioc_id': 42, 'ioc_value': 'evil.EXAMPLE'}]
        plan = case_graph_paste_plan(values, _IOC_TYPES, existing)
        self.assertEqual([42], plan['existing'])
        self.assertEqual([('10.0.0.5', {'type_id': 2, 'type_name': 'ip-any'}),
                          (_SHA256, {'type_id': 5, 'type_name': 'sha256'}),
                          ('bob@corp.example', {'type_id': 6, 'type_name': 'email-src'})], plan['create'])
        self.assertEqual(['unknown-thing'], plan['ignored'])

    def test_value_without_known_type_is_ignored(self):
        plan = case_graph_paste_plan(['https://x.example/'], [{'type_id': 3, 'type_name': 'domain'}], [])
        self.assertEqual({'create': [], 'existing': [], 'ignored': ['https://x.example/']}, plan)

    def test_existing_listed_once(self):
        plan = case_graph_paste_plan(['a.example', 'A.example'], _IOC_TYPES, [{'ioc_id': 1, 'ioc_value': 'a.example'}])
        self.assertEqual([1], plan['existing'])
