#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Regression tests for _coerce_dashboard_uuid.

GET /api/v2/custom-dashboards/undefined used to 500: the raw route
string went straight into a query against a postgres UUID column and
psycopg2 raised DataError ("invalid input syntax for type uuid").
Malformed ids must instead resolve to None so the caller returns 404.

Pure function — no DB or app context needed.
"""

import uuid
from unittest import TestCase

from app.datamgmt.custom_dashboard.custom_dashboard_db import _coerce_dashboard_uuid


class TestCoerceDashboardUuid(TestCase):

    # ── the reported bug ─────────────────────────────────────────────────────

    def test_literal_undefined_returns_none(self):
        """The exact value the frontend sent in GlitchTip issue 165."""
        self.assertIsNone(_coerce_dashboard_uuid('undefined'))

    def test_literal_null_returns_none(self):
        self.assertIsNone(_coerce_dashboard_uuid('null'))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_coerce_dashboard_uuid(''))

    def test_arbitrary_junk_returns_none(self):
        self.assertIsNone(_coerce_dashboard_uuid('not-a-uuid'))

    # ── non-string inputs must not raise ─────────────────────────────────────

    def test_none_returns_none_and_does_not_raise(self):
        """uuid.UUID(None) raises TypeError, which must stay contained."""
        self.assertIsNone(_coerce_dashboard_uuid(None))

    def test_int_returns_none(self):
        """uuid.UUID(210) raises AttributeError, which must stay contained."""
        self.assertIsNone(_coerce_dashboard_uuid(210))

    def test_list_returns_none(self):
        self.assertIsNone(_coerce_dashboard_uuid(['a']))

    # ── valid ids still resolve ──────────────────────────────────────────────

    def test_valid_uuid_string_is_parsed(self):
        value = uuid.uuid4()
        self.assertEqual(_coerce_dashboard_uuid(str(value)), value)

    def test_uuid_object_passes_through(self):
        """The model column is UUID(as_uuid=True), so attribute reads give
        back UUID objects; feeding one back in must not read as 404."""
        value = uuid.uuid4()
        self.assertEqual(_coerce_dashboard_uuid(value), value)

    def test_uppercase_uuid_string_is_parsed(self):
        value = uuid.uuid4()
        self.assertEqual(_coerce_dashboard_uuid(str(value).upper()), value)

    def test_braced_uuid_string_is_parsed(self):
        value = uuid.uuid4()
        self.assertEqual(_coerce_dashboard_uuid('{%s}' % value), value)
