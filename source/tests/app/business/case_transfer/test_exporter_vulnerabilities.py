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

"""Unit tests for leaving vulnerabilities out of an export."""

from unittest import TestCase

from app.business.case_transfer.exporter import _strip_vulnerabilities


class TestStripVulnerabilities(TestCase):

    def test_findings_links_and_catalogue_entries_are_dropped(self):
        entities = {'asset': [{'_ref': 'asset:1'}],
                    'asset_vulnerability': [{'_ref': 'asset_vulnerability:1'}],
                    'asset_vulnerability_event': [{}],
                    'asset_vulnerability_ioc': [{}]}
        lookup_ids = {'tag': {1}, 'vulnerability': {3, 4}}
        _strip_vulnerabilities(entities, lookup_ids)
        self.assertEqual(['asset'], list(entities))
        self.assertEqual({'tag': {1}, 'vulnerability': set()}, lookup_ids)
