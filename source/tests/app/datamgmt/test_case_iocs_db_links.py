#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for get_ioc_links in datamgmt/case/case_iocs_db.py.

The model is patched out; what is under test is which queries the function
issues, not the SQL they compile to.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.datamgmt.case.case_iocs_db import get_ioc_links


def _fake_ioc(ioc_id=97627):
    ioc = MagicMock()
    ioc.ioc_id = ioc_id
    ioc.ioc_value = '8.8.8.8'
    ioc.ioc_type_id = 1
    return ioc


def _patched_model(related=()):
    """Stub Ioc whose related-IOCs query resolves to `related`."""
    model = MagicMock()
    query = model.query.with_entities.return_value.filter.return_value
    query.join.return_value.join.return_value.all.return_value = list(related)
    return model


class TestGetIocLinksDoesNotReReadTheIoc(TestCase):
    """Regression for GlitchTip #209.

    `get_ioc_links` used to take an `ioc_id` and re-read the IOC itself
    before comparing values, then went straight to `ioc.ioc_value`. Callers
    pass an IOC they already loaded, so the row could disappear between the
    two reads — the caller's own access-control lookup sits in that window —
    and `.first()` returned None, raising
    `AttributeError: 'NoneType' object has no attribute 'ioc_value'` and
    turning a valid `GET /api/v2/cases/<id>/iocs/<id>` into a 500. It was
    also a SELECT per serialized IOC on the list endpoints.
    """

    def test_takes_the_ioc_and_never_looks_it_up_by_id(self):
        model = _patched_model()
        with patch('app.datamgmt.case.case_iocs_db.Ioc', model), \
                patch('app.datamgmt.case.case_iocs_db.and_', lambda *_: MagicMock()):
            get_ioc_links(_fake_ioc(), [3532])
        # `Ioc.query.filter(...)` was the re-read; the related-IOCs query
        # goes through `with_entities` instead.
        model.query.filter.assert_not_called()

    def test_returns_the_related_rows(self):
        related = [MagicMock()]
        model = _patched_model(related)
        with patch('app.datamgmt.case.case_iocs_db.Ioc', model), \
                patch('app.datamgmt.case.case_iocs_db.and_', lambda *_: MagicMock()):
            self.assertEqual(related, get_ioc_links(_fake_ioc(), [3532]))

    def test_returns_empty_list_for_a_missing_ioc(self):
        # Nothing to compare against, and no reason to hit the database.
        model = _patched_model()
        with patch('app.datamgmt.case.case_iocs_db.Ioc', model):
            self.assertEqual([], get_ioc_links(None, [3532]))
        model.query.with_entities.assert_not_called()

    def test_runs_for_a_row_that_is_not_a_model_instance(self):
        # The legacy /case/ioc/list route passes a with_entities row, which
        # carries ioc_id / ioc_value / ioc_type_id but no mapper.
        row = MagicMock(spec=['ioc_id', 'ioc_value', 'ioc_type_id'])
        row.ioc_id, row.ioc_value, row.ioc_type_id = 42, '8.8.8.8', 1
        related = [MagicMock()]
        model = _patched_model(related)
        with patch('app.datamgmt.case.case_iocs_db.Ioc', model), \
                patch('app.datamgmt.case.case_iocs_db.and_', lambda *_: MagicMock()):
            self.assertEqual(related, get_ioc_links(row, [3532]))

    def test_runs_without_case_access_limitations(self):
        # The empty-limitations branch builds a different search condition.
        related = [MagicMock()]
        model = _patched_model(related)
        with patch('app.datamgmt.case.case_iocs_db.Ioc', model), \
                patch('app.datamgmt.case.case_iocs_db.and_', lambda *_: MagicMock()):
            self.assertEqual(related, get_ioc_links(_fake_ioc(), []))
