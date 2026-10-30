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

"""Unit tests for the investigation graph parts of the case transfer."""

from types import SimpleNamespace
from unittest import TestCase

from app.datamgmt.case_transfer.transfer_db import _remap_graph_keys
from app.datamgmt.case_transfer.transfer_db import _write_poly_refs
from app.datamgmt.case_transfer.transfer_spec import ENTITIES

_REF_MAP = {'asset:1': 101, 'ioc:2': 202, 'graph_node:3': 303, 'note:4': 404}


def _edge_spec():
    return next(spec for spec in ENTITIES if spec.key == 'graph_edge')


class TestRemapGraphKeys(TestCase):

    def test_state_is_remapped_and_missing_objects_dropped(self):
        state = {'positions': {'asset:1': {'x': 1, 'y': 2}, 'ioc:9': {'x': 0, 'y': 0}, 'node:3': {'x': 5, 'y': 5}},
                 'hidden': ['ioc:2', 'asset:9', 'case:7'], 'selected': 'note:4', 'zoom': 1.5, 'layout': 'cose'}
        self.assertEqual({'positions': {'asset:101': {'x': 1, 'y': 2}, 'node:303': {'x': 5, 'y': 5}},
                          'hidden': ['ioc:202', 'case:7'], 'selected': 'note:404', 'zoom': 1.5, 'layout': 'cose'},
                         _remap_graph_keys(state, _REF_MAP))


class TestWritePolyRefs(TestCase):

    def test_endpoints_are_resolved(self):
        row = SimpleNamespace()
        payload = {'source_kind': 'asset', 'source_id': 'asset:1', 'target_kind': 'node', 'target_id': 'graph_node:3'}
        self.assertTrue(_write_poly_refs(row, _edge_spec(), payload, _REF_MAP))
        self.assertEqual(('asset', 101, 'node', 303), (row.source_kind, row.source_id, row.target_kind, row.target_id))

    def test_ref_of_another_entity_is_refused(self):
        payload = {'source_kind': 'asset', 'source_id': 'note:4', 'target_kind': 'ioc', 'target_id': 'ioc:2'}
        self.assertFalse(_write_poly_refs(SimpleNamespace(), _edge_spec(), payload, _REF_MAP))

    def test_unresolved_or_alert_endpoint_is_refused(self):
        for payload in ({'source_kind': 'asset', 'source_id': 'asset:9', 'target_kind': 'ioc', 'target_id': 'ioc:2'},
                        {'source_kind': 'alert', 'source_id': 'alert:1', 'target_kind': 'ioc', 'target_id': 'ioc:2'}):
            with self.subTest(payload=payload):
                self.assertFalse(_write_poly_refs(SimpleNamespace(), _edge_spec(), payload, _REF_MAP))
