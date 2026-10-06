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

"""Unit tests for the CVSS parsing and scoring helpers (no database)."""

from unittest import TestCase

from app.business.vulnerabilities_cvss import vulnerabilities_cvss_base_score
from app.business.vulnerabilities_cvss import vulnerabilities_cvss_parse
from app.business.vulnerabilities_cvss import vulnerabilities_cvss_severity
from app.models.errors import BusinessProcessingError

# Reference vectors with the base score of the official FIRST / NVD calculators.
_V31_REFERENCES = (
    ('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H', 9.8),
    ('CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N', 6.1),
    ('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H', 10.0),
    ('CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H', 7.8),
    ('CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N', 5.9),
    ('CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N', 6.4),
    ('CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N', 1.6),
    ('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N', 0.0),
)
_V30_REFERENCES = (
    ('CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H', 9.8),
    ('CVSS:3.0/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H', 7.8),
)
_V2_REFERENCES = (
    ('AV:N/AC:L/Au:N/C:C/I:C/A:C', 10.0),
    ('AV:N/AC:L/Au:N/C:P/I:P/A:P', 7.5),
    ('AV:N/AC:M/Au:N/C:N/I:P/A:N', 4.3),
    ('AV:L/AC:L/Au:N/C:C/I:C/A:C', 7.2),
    ('AV:N/AC:L/Au:N/C:N/I:N/A:N', 0.0),
)
_V4_VECTOR = 'CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N'


class TestCvssParse(TestCase):

    def test_v31_vector_should_parse_as_31(self):
        version, vector = vulnerabilities_cvss_parse(f'  {_V31_REFERENCES[0][0]} ')
        self.assertEqual(version, '3.1')
        self.assertEqual(vector, _V31_REFERENCES[0][0])

    def test_v30_vector_should_parse_as_30(self):
        self.assertEqual(vulnerabilities_cvss_parse(_V30_REFERENCES[0][0])[0], '3.0')

    def test_v4_vector_should_parse_as_40(self):
        self.assertEqual(vulnerabilities_cvss_parse(_V4_VECTOR), ('4.0', _V4_VECTOR))

    def test_v2_vector_should_parse_and_drop_parentheses(self):
        self.assertEqual(vulnerabilities_cvss_parse('(AV:N/AC:L/Au:N/C:C/I:C/A:C)'),
                         ('2.0', 'AV:N/AC:L/Au:N/C:C/I:C/A:C'))

    def test_temporal_metrics_should_be_accepted(self):
        self.assertEqual(vulnerabilities_cvss_parse(f'{_V31_REFERENCES[0][0]}/E:P/RL:O/RC:C')[0], '3.1')
        self.assertEqual(vulnerabilities_cvss_parse('AV:N/AC:L/Au:N/C:P/I:P/A:P/E:F/RL:OF/RC:C')[0], '2.0')

    def test_invalid_vectors_should_be_rejected(self):
        for vector in ('', '   ', 'garbage', 'CVSS:3.2/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H',
                       'CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H',
                       'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H',
                       'cvss:3.1/av:n/ac:l/pr:n/ui:n/s:u/c:h/i:h/a:h',
                       'CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N',
                       'AV:N/AC:L/Au:N/C:C/I:C', 'AV:N/AC:X/Au:N/C:C/I:C/A:C'):
            with self.assertRaises(BusinessProcessingError, msg=vector):
                vulnerabilities_cvss_parse(vector)

    def test_non_string_should_be_rejected(self):
        for value in (None, 9.8, ['CVSS:3.1'], {'vector': 'x'}):
            with self.assertRaises(BusinessProcessingError):
                vulnerabilities_cvss_parse(value)


class TestCvssBaseScore(TestCase):

    def _score(self, vector):
        return vulnerabilities_cvss_base_score(*vulnerabilities_cvss_parse(vector))

    def test_v31_reference_scores(self):
        for vector, expected in _V31_REFERENCES:
            self.assertEqual(self._score(vector), expected, vector)

    def test_v30_reference_scores(self):
        for vector, expected in _V30_REFERENCES:
            self.assertEqual(self._score(vector), expected, vector)

    def test_v2_reference_scores(self):
        for vector, expected in _V2_REFERENCES:
            self.assertEqual(self._score(vector), expected, vector)

    def test_temporal_metrics_should_not_change_the_base_score(self):
        self.assertEqual(self._score(f'{_V31_REFERENCES[0][0]}/E:U/RL:O/RC:U'), 9.8)

    def test_v4_score_should_not_be_computed(self):
        self.assertIsNone(self._score(_V4_VECTOR))


class TestCvssSeverity(TestCase):

    def test_v3_bands(self):
        for score, expected in ((None, 'unknown'), (0.0, 'none'), (0.1, 'low'), (3.9, 'low'),
                                (4.0, 'medium'), (6.9, 'medium'), (7.0, 'high'), (8.9, 'high'),
                                (9.0, 'critical'), (10.0, 'critical')):
            self.assertEqual(vulnerabilities_cvss_severity(score, '3.1'), expected, score)

    def test_default_version_uses_v3_bands(self):
        self.assertEqual(vulnerabilities_cvss_severity(9.8), 'critical')

    def test_v2_bands_have_no_critical_nor_none(self):
        for score, expected in ((0.0, 'low'), (3.9, 'low'), (4.0, 'medium'), (6.9, 'medium'),
                                (7.0, 'high'), (10.0, 'high')):
            self.assertEqual(vulnerabilities_cvss_severity(score, '2.0'), expected, score)
        self.assertEqual(vulnerabilities_cvss_severity(None, '2.0'), 'unknown')
