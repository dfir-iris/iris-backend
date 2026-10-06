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

"""Server-side IOC type detection (port of the SPA's ioc-type-detect.ts)."""

from unittest import TestCase

from app.business.ioc_type_detect import ioc_type_detect_candidates
from app.business.ioc_type_detect import ioc_type_detect_is_ip
from app.business.ioc_type_detect import ioc_type_detect_pick


class TestIocTypeDetectCandidates(TestCase):

    def test_same_cases_as_the_spa_helper(self):
        cases = {
            '10.0.0.1': ['ip-any', 'ip-dst', 'ip-src'],
            '2001:db8::1': ['ip-any', 'ip-dst', 'ip-src'],
            '10.0.0.1:443': ['ip-dst|port', 'ip-src|port'],
            'https://evil.example/x?a=1': ['url', 'uri', 'link'],
            'bob@evil.example': ['email', 'email-src', 'email-dst'],
            '00:1a:2B:3c:4d:5e': ['mac-address'],
            'CVE-2024-3094': ['vulnerability'],
            'HKLM\\Software\\Run': ['regkey'],
            'd41d8cd98f00b204e9800998ecf8427e': ['md5'],
            'da39a3ee5e6b4b0d3255bfef95601890afd80709': ['sha1'],
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855': ['sha256'],
            'a' * 56: ['sha224'],
            'b' * 96: ['sha384'],
            'c' * 128: ['sha512'],
            'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq': ['btc'],
            'AS13335': ['AS'],
            'evil.example.com': ['domain', 'hostname'],
        }
        for value, expected in cases.items():
            self.assertEqual(expected, ioc_type_detect_candidates(value), value)

    def test_unknown_or_invalid(self):
        for value in ('', '   ', 'two words', 'nodot', '999.1.1.1:99', None, 42):
            self.assertEqual([], ioc_type_detect_candidates(value), repr(value))

    def test_trims_the_value(self):
        self.assertEqual(['ip-any', 'ip-dst', 'ip-src'], ioc_type_detect_candidates('  10.0.0.1 '))

    def test_invalid_ipv4_is_a_domain_lookalike_not_an_ip(self):
        self.assertFalse(ioc_type_detect_is_ip('256.1.1.1'))
        self.assertTrue(ioc_type_detect_is_ip('::1'))
        self.assertFalse(ioc_type_detect_is_ip('dead:beef'))
        self.assertFalse(ioc_type_detect_is_ip(None))


class TestIocTypeDetectPick(TestCase):

    _TYPES = [
        {'type_id': 1, 'type_name': 'IP-DST'},
        {'type_id': 2, 'type_name': 'domain'},
        {'type_id': 3, 'type_name': 'sha256'},
    ]

    def test_first_known_candidate_case_insensitive(self):
        self.assertEqual(1, ioc_type_detect_pick('10.0.0.1', self._TYPES)['type_id'])
        self.assertEqual(2, ioc_type_detect_pick('evil.example', self._TYPES)['type_id'])

    def test_no_known_candidate(self):
        self.assertIsNone(ioc_type_detect_pick('bob@evil.example', self._TYPES))
        self.assertIsNone(ioc_type_detect_pick('10.0.0.1', []))
        self.assertIsNone(ioc_type_detect_pick('10.0.0.1', None))
