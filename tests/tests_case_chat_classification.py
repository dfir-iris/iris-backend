#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Case-chat tool classification exhaustiveness — pure unit test.

Runs inside the docker-compose IRIS backend container (imports live
Flask modules) so the `TOOL_REGISTRY` and classification sets are the
real ones the chatbot dispatches against. Adding a new MCP tool without
classifying it as read or write must fail this test — that's the
whole safety hinge for the confirm-writes UX.
"""
import os
import sys
from unittest import TestCase


class TestsCaseChatClassification(TestCase):

    def setUp(self) -> None:
        # These tests import the backend package directly rather than
        # driving the app via HTTP, so we must extend sys.path to the
        # deployed source root. Same trick the migration tests use.
        source_root = '/iris_source'  # docker-compose test container mount
        if os.path.isdir(source_root) and source_root not in sys.path:
            sys.path.insert(0, source_root)

    def test_read_and_write_sets_are_disjoint(self):
        from app.blueprints.rest.v2.case_chat.tool_classification import (
            READ_ONLY_TOOLS,
            WRITE_TOOLS,
        )
        overlap = READ_ONLY_TOOLS & WRITE_TOOLS
        self.assertEqual(set(), overlap,
                         f'A tool cannot be both read and write: {overlap}')

    def test_every_mvp_tool_is_classified(self):
        """Every MVP-enabled MCP tool must appear in either
        READ_ONLY_TOOLS or WRITE_TOOLS. An unclassified new tool would
        be refused at dispatch — the CI failure here catches it at
        merge time instead of at analyst-facing runtime."""
        from app.blueprints.rest.v2.case_chat.tool_classification import (
            READ_ONLY_TOOLS,
            WRITE_TOOLS,
        )
        from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY

        classified = READ_ONLY_TOOLS | WRITE_TOOLS
        mvp_tools = {name for name, spec in TOOL_REGISTRY.items()
                     if getattr(spec, 'mvp', False)}
        unclassified = mvp_tools - classified
        self.assertEqual(
            set(), unclassified,
            f'MVP tools missing classification: {sorted(unclassified)}. '
            f'Add each name to either READ_ONLY_TOOLS or WRITE_TOOLS in '
            f'app/blueprints/rest/v2/case_chat/tool_classification.py.',
        )

    def test_classification_covers_registered_writes(self):
        """A tool present in WRITE_TOOLS that isn't registered in
        TOOL_REGISTRY is dead code (or a typo). Reverse-check catches
        both."""
        from app.blueprints.rest.v2.case_chat.tool_classification import (
            READ_ONLY_TOOLS,
            WRITE_TOOLS,
        )
        from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY

        registered = set(TOOL_REGISTRY.keys())
        stale = (READ_ONLY_TOOLS | WRITE_TOOLS) - registered
        self.assertEqual(
            set(), stale,
            f'Classification lists reference unregistered tool(s): {sorted(stale)}. '
            f'Remove them from tool_classification.py.',
        )
