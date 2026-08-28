#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the no-conditions fast-path in _flow_matches.

_flow_matches requires DB query for the actual matching logic, but the
empty-conditions guard (returns False immediately) is pure.
"""

from unittest import TestCase
from unittest.mock import MagicMock

from app.business.investigation_flows import _flow_matches


def _flow(flow_conditions):
    flow = MagicMock()
    flow.flow_id = 1
    flow.flow_conditions = flow_conditions
    return flow


class TestFlowMatchesEmptyConditions(TestCase):

    def test_none_conditions_payload_returns_false(self):
        flow = _flow(flow_conditions=None)
        model = MagicMock()
        self.assertFalse(_flow_matches(flow, model, entity_id=1))

    def test_empty_dict_conditions_payload_returns_false(self):
        flow = _flow(flow_conditions={})
        model = MagicMock()
        self.assertFalse(_flow_matches(flow, model, entity_id=1))

    def test_empty_conditions_list_returns_false(self):
        flow = _flow(flow_conditions={'conditions': [], 'logic': 'and'})
        model = MagicMock()
        self.assertFalse(_flow_matches(flow, model, entity_id=1))

    def test_none_conditions_list_returns_false(self):
        flow = _flow(flow_conditions={'conditions': None, 'logic': 'and'})
        model = MagicMock()
        self.assertFalse(_flow_matches(flow, model, entity_id=1))
