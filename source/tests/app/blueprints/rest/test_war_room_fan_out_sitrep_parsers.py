#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Request-body parsers of the task fan-out and SitRep publish-share routes."""

from unittest import TestCase

from app.blueprints.rest.v2.war_rooms.sitreps import _parse_share_targets
from app.blueprints.rest.v2.war_rooms.tasks import _parse_fan_out_body
from app.models.errors import BusinessProcessingError


class TestParseFanOutBody(TestCase):

    def test_defaults_and_dedup(self):
        self.assertEqual(([3, 1], True, None), _parse_fan_out_body({'case_ids': [3, 1, 3]}))

    def test_explicit_values(self):
        body = {'case_ids': [1], 'assign_to_case_owner': False, 'status_id': 2}
        self.assertEqual(([1], False, 2), _parse_fan_out_body(body))

    def test_invalid(self):
        for body in ({}, {'case_ids': []}, {'case_ids': 'all'}, {'case_ids': [True]},
                     {'case_ids': ['1']}, {'case_ids': [0]}, {'case_ids': list(range(1, 202))},
                     {'case_ids': [1], 'assign_to_case_owner': 'yes'},
                     {'case_ids': [1], 'status_id': '2'}):
            with self.assertRaises(BusinessProcessingError, msg=repr(body)):
                _parse_fan_out_body(body)


class TestParseShareTargets(TestCase):

    def test_none_and_all(self):
        self.assertIsNone(_parse_share_targets(None))
        self.assertEqual('all', _parse_share_targets('all'))

    def test_list(self):
        self.assertEqual([2, 1], _parse_share_targets([2, 1, 2]))

    def test_invalid(self):
        for raw in ('ALL', 1, [1, 'x'], [False], {'a': 1}, list(range(1, 202))):
            with self.assertRaises(BusinessProcessingError, msg=repr(raw)):
                _parse_share_targets(raw)
