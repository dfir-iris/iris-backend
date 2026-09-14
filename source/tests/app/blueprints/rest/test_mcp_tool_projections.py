#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the MCP list-tool response projections.

Marshmallow validates top-level `only=` names when a schema is built,
but NOT dotted paths into nested schemas — `severity.bogus` constructs
happily and silently dumps nothing. These tests dump a stand-in record
through each summary schema so a typo in a nested path fails here
rather than silently stripping a field from Yuki's view of the data.

Dumping needs no DB: Marshmallow reads plain attributes.
"""

import json
from unittest import TestCase
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.result_budget import (
    MAX_RESULT_BYTES,
    apply_result_budget,
)
from app.blueprints.rest.v2.mcp.tools._common import (
    DEFAULT_PER_PAGE,
    VIEW_FULL,
    VIEW_SUMMARY,
    clip_text_field,
    requested_view,
    schema_for_view,
)
from app.blueprints.rest.v2.mcp.tools.alerts import (
    _ALERT_LIST_PREVIEWS,
    _alert_schema,
    _alert_summary_schema,
)
from app.blueprints.rest.v2.mcp.tools.assets import _asset_summary_schema
from app.blueprints.rest.v2.mcp.tools.cases import _case_summary_schema
from app.blueprints.rest.v2.mcp.tools.iocs import _ioc_summary_schema
from app.blueprints.rest.v2.mcp.tools.notes import _note_summary_schema
from app.blueprints.rest.v2.mcp.tools.tasks import _task_summary_schema


class _Row:
    """Stand-in for an ORM row: unsupplied attributes read as None.

    Note the *model* attribute names, which differ from the serialised
    names for several fields — `user_name` dumps from `.name`,
    `customer_name` from `.name`, `customer_id` from `.client_id`.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, item):
        return None


def _alert(description='d' * 50):
    return _Row(
        alert_id=7,
        alert_title='Brute force',
        alert_description=description,
        alert_source='crowdstrike',
        alert_severity_id=3,
        alert_status_id=1,
        alert_customer_id=1,
        alert_owner_id=4,
        severity=_Row(severity_id=3, severity_name='High'),
        status=_Row(status_id=1, status_name='New'),
        resolution_status=_Row(resolution_status_id=2,
                               resolution_status_name='True positive'),
        classification=_Row(id=2, name='intrusion'),
        customer=_Row(client_id=1, name='ACME'),
        owner=_Row(id=4, name='alice', user='al', email='a@b.c'),
        iocs=[_Row(ioc_id=1, ioc_value='1.2.3.4', ioc_type_id=2)],
        assets=[_Row(asset_id=9, asset_name='host-1', asset_type_id=5)],
        cases=[_Row(case_id=11)],
        clusters=[_Row(cluster_id=3)],
        # The blobs the projection exists to keep out.
        alert_context={'rule': 'r' * 4_000},
        alert_source_content={'raw': 's' * 4_000},
        modification_history={'1': 'h' * 4_000},
        comments=[],
    )


class TestAlertSummaryProjection(TestCase):

    def setUp(self):
        self.dumped = _alert_summary_schema.dump(_alert())

    def test_resolved_names_are_present(self):
        # The point of the nested paths: the caller can reason about
        # "High" without a second call to resolve severity id 3.
        self.assertEqual({'severity_name': 'High'}, self.dumped['severity'])
        self.assertEqual({'status_name': 'New'}, self.dumped['status'])
        self.assertEqual({'name': 'intrusion'}, self.dumped['classification'])

    def test_nested_owner_and_customer_names_resolve(self):
        self.assertEqual('alice', self.dumped['owner']['user_name'])
        self.assertEqual('ACME', self.dumped['customer']['customer_name'])

    def test_resolution_status_name_resolves(self):
        self.assertEqual(
            'True positive',
            self.dumped['resolution_status']['resolution_status_name'],
        )

    def test_ioc_and_asset_values_survive_but_not_their_full_records(self):
        self.assertEqual('1.2.3.4', self.dumped['iocs'][0]['ioc_value'])
        self.assertEqual('host-1', self.dumped['assets'][0]['asset_name'])
        self.assertNotIn('ioc_enrichment', self.dumped['iocs'][0])
        self.assertNotIn('alerts', self.dumped['assets'][0])

    def test_cross_references_survive(self):
        self.assertEqual([11], self.dumped['cases'])
        self.assertEqual([3], self.dumped['clusters'])

    def test_raw_payloads_are_excluded(self):
        for field in ('alert_context', 'alert_source_content',
                      'modification_history', 'comments'):
            self.assertNotIn(field, self.dumped)

    def test_summary_is_dramatically_smaller_than_full(self):
        full = _alert_schema.dump(_alert())
        self.assertLess(
            len(json.dumps(self.dumped, default=str)),
            len(json.dumps(full, default=str)) // 4,
        )


class TestOtherSummaryProjections(TestCase):
    """Nested paths in the remaining summary schemas resolve to a value.

    A dotted path that doesn't exist dumps an empty dict instead of
    raising, so asserting on the resolved value is what catches a typo.
    """

    def test_case_summary_resolves_nested_names(self):
        dumped = _case_summary_schema.dump(_Row(
            case_id=1, name='Case', description='x', status_id=0,
            state=_Row(state_name='Open'),
            severity=_Row(severity_name='High'),
            classification=_Row(name='intrusion'),
            client=_Row(client_id=1, name='ACME'),
            owner=_Row(id=4, name='alice'),
            tags=[_Row(tag_title='phishing')],
        ))
        self.assertEqual('Open', dumped['state']['state_name'])
        self.assertEqual('High', dumped['severity']['severity_name'])
        self.assertEqual('intrusion', dumped['classification']['name'])
        self.assertEqual('ACME', dumped['client']['customer_name'])
        self.assertEqual('alice', dumped['owner']['user_name'])
        self.assertEqual([{'tag_title': 'phishing'}], dumped['tags'])

    def test_case_summary_excludes_unbounded_collections(self):
        dumped = _case_summary_schema.dump(
            _Row(case_id=1, name='Case', status_id=0))
        for field in ('alerts', 'note_directories', 'protagonists',
                      'modification_history', 'custom_attributes'):
            self.assertNotIn(field, dumped)

    def test_note_summary_keeps_content_but_drops_comments(self):
        dumped = _note_summary_schema.dump(_Row(
            note_id=1, note_title='T', note_content='body',
            directory_id=2, directory=_Row(id=2, name='Folder'),
        ))
        self.assertEqual('body', dumped['note_content'])
        self.assertEqual('Folder', dumped['directory']['name'])
        self.assertNotIn('comments', dumped)
        self.assertNotIn('modification_history', dumped)

    def test_asset_summary_resolves_nested_names(self):
        dumped = _asset_summary_schema.dump(_Row(
            asset_id=1, asset_name='host-1',
            asset_type=_Row(asset_name='Windows'),
            analysis_status=_Row(id=1, name='Done'),
        ))
        self.assertEqual('Windows', dumped['asset_type']['asset_name'])
        self.assertEqual('Done', dumped['analysis_status']['name'])
        for field in ('alerts', 'iocs', 'ioc_links', 'asset_enrichment'):
            self.assertNotIn(field, dumped)

    def test_ioc_summary_resolves_nested_names(self):
        dumped = _ioc_summary_schema.dump(_Row(
            ioc_id=1, ioc_value='1.2.3.4',
            ioc_type=_Row(type_name='ip-dst'),
            tlp=_Row(tlp_name='amber'),
        ))
        self.assertEqual('ip-dst', dumped['ioc_type']['type_name'])
        self.assertEqual('amber', dumped['tlp']['tlp_name'])
        for field in ('ioc_enrichment', 'ioc_misp', 'modification_history'):
            self.assertNotIn(field, dumped)

    def test_task_summary_resolves_status_name(self):
        # `populate_assignees` is a post_dump hook that queries the DB
        # for every task dumped — it runs after `only=` is applied, so it
        # has to be stubbed rather than projected away.
        with patch('app.schema.marshables.get_task_assignees', return_value=[]):
            dumped = _task_summary_schema.dump(_Row(
                id=1, task_title='T', task_status_id=2,
                status=_Row(id=2, status_name='Done'),
            ))
        self.assertEqual('Done', dumped['status']['status_name'])
        for field in ('case', 'modification_history', 'custom_attributes'):
            self.assertNotIn(field, dumped)


def _busy_alert():
    """A busy but unremarkable alert: wordy description, 5 IOCs, 3 assets.

    The budget check is only worth anything if its fixture is at least as
    heavy as a real page. The single-IOC `_alert()` above is too light —
    a page of those fits whatever the preview limit is, so it would pin
    nothing.
    """
    alert = _alert(description='Detected anomalous authentication. ' * 30)
    alert.iocs = [_Row(ioc_id=j, ioc_value=f'10.0.0.{j}', ioc_type_id=2)
                  for j in range(5)]
    alert.assets = [_Row(asset_id=j, asset_name=f'host-{j}', asset_type_id=5)
                    for j in range(3)]
    alert.alert_source_ref = 'CS-00000001'
    alert.alert_tags = 'auth,bruteforce,windows'
    return alert


class TestDefaultPageFitsTheResponseBudget(TestCase):
    """A default-sized page of summaries must not trip the budget guard.

    The projections and `result_budget` are sized against each other:
    the guard is a backstop for unprojected or `view=full` calls, so if
    it fires on an *ordinary* list call then the default is too fat.
    Widening a projection or raising a preview limit far enough to break
    this is the signal to re-check both numbers, not to bump the ceiling.
    """

    def test_full_page_of_projected_alerts_is_not_truncated(self):
        rows = _alert_summary_schema.dump(
            [_busy_alert() for _ in range(DEFAULT_PER_PAGE)], many=True,
        )
        clip_text_field(rows, 'alert_description',
                        _ALERT_LIST_PREVIEWS['alert_description'])
        page = {'total': 500, 'data': rows, 'last_page': 20,
                'current_page': 1, 'next_page': 2}

        _, report = apply_result_budget(page)

        self.assertIsNone(
            report,
            f'a default page of {DEFAULT_PER_PAGE} projected alerts '
            f'serialises to {len(json.dumps(page, default=str))} bytes, over '
            f'the {MAX_RESULT_BYTES}-byte budget',
        )


class TestViewSelection(TestCase):

    def test_view_defaults_to_summary(self):
        self.assertEqual(VIEW_SUMMARY, requested_view({}))

    def test_full_view_is_honoured(self):
        self.assertEqual(VIEW_FULL, requested_view({'view': 'full'}))

    def test_unknown_view_degrades_to_summary(self):
        # A hallucinated value must fall back to the cheap projection,
        # never the expensive one.
        self.assertEqual(VIEW_SUMMARY, requested_view({'view': 'verbose'}))
        self.assertEqual(VIEW_SUMMARY, requested_view({'view': None}))

    def test_schema_for_view_picks_the_matching_projection(self):
        self.assertIs(
            _alert_summary_schema,
            schema_for_view({}, _alert_summary_schema, _alert_schema),
        )
        self.assertIs(
            _alert_schema,
            schema_for_view({'view': 'full'}, _alert_summary_schema,
                            _alert_schema),
        )


class TestClipTextField(TestCase):

    def test_long_value_is_clipped_with_a_visible_marker(self):
        rows = [{'note_content': 'x' * 900}]
        clip_text_field(rows, 'note_content', 400)
        self.assertTrue(rows[0]['note_content'].startswith('x' * 400))
        self.assertIn('900 chars total', rows[0]['note_content'])

    def test_short_value_is_left_alone(self):
        rows = [{'note_content': 'short'}]
        clip_text_field(rows, 'note_content', 400)
        self.assertEqual('short', rows[0]['note_content'])

    def test_absent_or_non_string_value_is_ignored(self):
        rows = [{}, {'note_content': None}, {'note_content': 42}]
        clip_text_field(rows, 'note_content', 400)
        self.assertEqual([{}, {'note_content': None}, {'note_content': 42}], rows)
