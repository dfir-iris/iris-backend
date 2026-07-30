"""Enrich the built-in Statistics dashboard with more DFIR-relevant widgets

Revision ID: c9e4a12b7f83
Revises: b8f4c2d7e91a
Create Date: 2026-07-29 20:00:00.000000

The initial seed in c7f1e2a4d810 gave analysts four KPIs (total alerts,
total cases, MTTD, MTTR) and three distribution charts. Feedback from
production: "there's way more we care about — false-positive rate,
escalation rate, open cases, cases by state, alerts over time". This
migration rewrites the definition in place (keyed on the stable
system-dashboard UUID) so existing installs pick up the richer layout
without breaking any user clones (system rows have no owner_id and are
looked up by UUID; user clones live under separate UUIDs).

Idempotent: guarded on the system uuid + is_system flag. Downgrade
restores the original c7f1e2a4d810 payload so a rollback is clean.
"""

import json

from alembic import op
import sqlalchemy as sa


revision = 'c9e4a12b7f83'
down_revision = 'b8f4c2d7e91a'
branch_labels = None
depends_on = None


STATISTICS_DASHBOARD_UUID = '00000000-0000-4000-8000-000000000001'


def _enriched_definition():
    return {
        'name': 'Statistics',
        'description': 'Built-in IRIS statistics dashboard. Clone to customise.',
        'is_shared': True,
        'is_system': True,
        'filters_schema': [
            {'key': 'start', 'label': 'Start date', 'type': 'date'},
            {'key': 'end', 'label': 'End date', 'type': 'date'},
            {'key': 'customer_id', 'label': 'Customer', 'type': 'reference',
             'table': 'client', 'column': 'client_id'},
            {'key': 'severity_id', 'label': 'Severity', 'type': 'reference',
             'table': 'severities', 'column': 'severity_id'},
            {'key': 'case_status_id', 'label': 'Case status', 'type': 'reference',
             'table': 'case_state', 'column': 'state_id'},
        ],
        'sections': [
            {
                'id': 'section-kpis',
                'title': 'Key indicators',
                'description': 'Headline volumes and response times over the selected window.',
                'show_divider': False,
                'widgets': [
                    {
                        'name': 'Total alerts',
                        'chart_type': 'number',
                        'fields': [{'table': 'alerts', 'column': 'alert_id',
                                    'aggregation': 'count', 'alias': 'total_alerts'}],
                        'options': {
                            'help': 'Count of alerts whose creation time falls in the selected window.',
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                    {
                        'name': 'Total cases',
                        'chart_type': 'number',
                        'fields': [{'table': 'cases', 'column': 'case_id',
                                    'aggregation': 'count', 'alias': 'total_cases'}],
                        'options': {
                            'help': 'Count of cases opened in the selected window.',
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                    {
                        'name': 'Mean time to detect',
                        'chart_type': 'number',
                        'fields': [{'table': 'computed', 'column': 'mttd_seconds',
                                    'alias': 'mttd_seconds'}],
                        'options': {
                            'value_format': 'duration',
                            'help': (
                                'Average delay between when an event actually happened at the source '
                                '(alert_source_event_time) and when the alert reached IRIS '
                                '(alert_creation_time). Sub-second deltas are counted, so genuinely '
                                'fast pipelines show up honestly. Only alerts where the two '
                                'timestamps are exactly identical are excluded — that means the '
                                'source did not supply an event time and both columns defaulted to '
                                'the same now() on insert, which is noise not signal.'
                            ),
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                    {
                        'name': 'Mean time to resolve',
                        'chart_type': 'number',
                        'fields': [{'table': 'computed', 'column': 'mttr_seconds',
                                    'alias': 'mttr_seconds'}],
                        'options': {
                            'value_format': 'duration',
                            'help': (
                                'Average time between when an alert was created (alert_creation_time) '
                                'and when an analyst first set a resolution status on it (resolved_at). '
                                'Unresolved alerts are excluded. The window filter applies to '
                                'resolved_at — an alert closed today counts toward today’s MTTR '
                                'even if it was opened weeks ago. Alerts resolved before the '
                                'resolved_at column existed (installed 2026-07-30) don’t have a '
                                'reliable timestamp and are excluded rather than guessed.'
                            ),
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                ],
            },
            {
                'id': 'section-triage',
                'title': 'Triage quality',
                'description': 'How the analyst pipeline is handling incoming signal.',
                'show_divider': True,
                'widgets': [
                    {
                        'name': 'False positive rate',
                        'chart_type': 'percentage',
                        'fields': [{'table': 'computed', 'column': 'false_positive_rate',
                                    'alias': 'false_positive_rate'}],
                        'options': {
                            'value_format': 'percentage',
                            'help': (
                                'Share of alerts in the window whose resolution status is "False positive". '
                                'Denominator is all alerts created in the window (including still-unresolved). '
                                'A high rate suggests overly noisy detection rules.'
                            ),
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                    {
                        'name': 'Escalation rate',
                        'chart_type': 'percentage',
                        'fields': [{'table': 'computed', 'column': 'escalation_rate',
                                    'alias': 'escalation_rate'}],
                        'options': {
                            'value_format': 'percentage',
                            'help': (
                                'Share of alerts in the window whose status is "Escalated" (i.e. promoted '
                                'to a case). Denominator is all alerts created in the window. A high rate '
                                'means most alerts warranted investigation.'
                            ),
                        },
                        'layout': {'widget_size': 'kpi'},
                    },
                    {
                        'name': 'Alerts by status',
                        'chart_type': 'pie',
                        'fields': [
                            {'table': 'alert_status', 'column': 'status_name', 'alias': 'status'},
                            {'table': 'alerts', 'column': 'alert_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['alert_status.status_name'],
                        'layout': {'widget_size': 'half'},
                    },
                ],
            },
            {
                'id': 'section-distributions',
                'title': 'Distributions',
                'description': 'How alerts, cases and evidence break down across the selected window.',
                'show_divider': True,
                'widgets': [
                    {
                        'name': 'Alerts by severity',
                        'chart_type': 'pie',
                        'fields': [
                            {'table': 'severities', 'column': 'severity_name', 'alias': 'severity'},
                            {'table': 'alerts', 'column': 'alert_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['severities.severity_name'],
                        'layout': {'widget_size': 'half'},
                    },
                    {
                        'name': 'Cases by classification',
                        'chart_type': 'bar',
                        'fields': [
                            {'table': 'case_classification', 'column': 'name_expanded',
                             'alias': 'classification'},
                            {'table': 'cases', 'column': 'case_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['case_classification.name_expanded'],
                        'layout': {'widget_size': 'half'},
                    },
                    {
                        'name': 'Cases by state',
                        'chart_type': 'bar',
                        'fields': [
                            {'table': 'case_state', 'column': 'state_name', 'alias': 'state'},
                            {'table': 'cases', 'column': 'case_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['case_state.state_name'],
                        'layout': {'widget_size': 'half'},
                    },
                    {
                        'name': 'Evidence by type',
                        'chart_type': 'bar',
                        'fields': [
                            {'table': 'case_asset_types', 'column': 'asset_name',
                             'alias': 'asset_type'},
                            {'table': 'case_assets', 'column': 'asset_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['case_asset_types.asset_name'],
                        'layout': {'widget_size': 'half'},
                    },
                ],
            },
            {
                'id': 'section-timeseries',
                'title': 'Trends',
                'description': 'Volume of alerts over the selected window.',
                'show_divider': True,
                'widgets': [
                    {
                        'name': 'Alerts over time',
                        'chart_type': 'timechart',
                        'fields': [
                            {'table': 'alerts', 'column': 'alert_id',
                             'aggregation': 'count', 'alias': 'total'},
                        ],
                        'group_by': ['alerts.alert_creation_time'],
                        'time_bucket': 'day',
                        'options': {
                            'time_column': 'alerts.alert_creation_time',
                            'sort': 'asc',
                        },
                        'layout': {'widget_size': 'full'},
                    },
                ],
            },
        ],
    }


def _original_definition():
    return {
        'name': 'Statistics',
        'description': 'Built-in IRIS statistics dashboard. Clone to customise.',
        'is_shared': True,
        'is_system': True,
        'filters_schema': [
            {'key': 'start', 'label': 'Start date', 'type': 'date'},
            {'key': 'end', 'label': 'End date', 'type': 'date'},
            {'key': 'customer_id', 'label': 'Customer', 'type': 'reference',
             'table': 'client', 'column': 'client_id'},
            {'key': 'severity_id', 'label': 'Severity', 'type': 'reference',
             'table': 'severities', 'column': 'severity_id'},
            {'key': 'case_status_id', 'label': 'Case status', 'type': 'reference',
             'table': 'case_state', 'column': 'state_id'},
        ],
        'sections': [
            {
                'id': 'section-kpis',
                'title': 'Key indicators',
                'description': 'Headline volumes and response times.',
                'show_divider': False,
                'widgets': [
                    {'name': 'Total alerts', 'chart_type': 'number',
                     'fields': [{'table': 'alerts', 'column': 'alert_id',
                                 'aggregation': 'count', 'alias': 'total_alerts'}],
                     'layout': {'widget_size': 'kpi'}},
                    {'name': 'Total cases', 'chart_type': 'number',
                     'fields': [{'table': 'cases', 'column': 'case_id',
                                 'aggregation': 'count', 'alias': 'total_cases'}],
                     'layout': {'widget_size': 'kpi'}},
                    {'name': 'Mean time to detect', 'chart_type': 'number',
                     'fields': [{'table': 'computed', 'column': 'mttd_seconds',
                                 'alias': 'mttd_seconds'}],
                     'options': {'value_format': 'duration'},
                     'layout': {'widget_size': 'kpi'}},
                    {'name': 'Mean time to resolve', 'chart_type': 'number',
                     'fields': [{'table': 'computed', 'column': 'mttr_seconds',
                                 'alias': 'mttr_seconds'}],
                     'options': {'value_format': 'duration'},
                     'layout': {'widget_size': 'kpi'}},
                ],
            },
            {
                'id': 'section-charts',
                'title': 'Distributions',
                'description': 'How alerts, cases and evidence break down across the selected window.',
                'show_divider': True,
                'widgets': [
                    {'name': 'Alerts by severity', 'chart_type': 'pie',
                     'fields': [{'table': 'alerts', 'column': 'alert_id',
                                 'aggregation': 'count', 'alias': 'total'}],
                     'group_by': ['severities.severity_name'],
                     'layout': {'widget_size': 'half'}},
                    {'name': 'Cases by classification', 'chart_type': 'bar',
                     'fields': [{'table': 'cases', 'column': 'case_id',
                                 'aggregation': 'count', 'alias': 'total'}],
                     'group_by': ['case_classification.name_expanded'],
                     'layout': {'widget_size': 'half'}},
                    {'name': 'Evidence by type', 'chart_type': 'bar',
                     'fields': [{'table': 'case_assets', 'column': 'asset_id',
                                 'aggregation': 'count', 'alias': 'total'}],
                     'group_by': ['case_asset_types.asset_name'],
                     'layout': {'widget_size': 'full'}},
                ],
            },
        ],
    }


def _apply_definition(bind, definition):
    """Overwrite the system dashboard's definition + regenerate widgets.

    The widgets table is a materialised projection of definition.sections
    used by the render endpoint for indexing/ordering. Keep it in sync
    by wiping and re-inserting from the fresh definition.
    """
    row = bind.execute(
        sa.text('SELECT id FROM custom_dashboard WHERE dashboard_uuid = :u AND is_system'),
        {'u': STATISTICS_DASHBOARD_UUID},
    ).fetchone()
    if row is None:
        return
    dashboard_id = row[0]

    bind.execute(
        sa.text(
            'UPDATE custom_dashboard SET name = :name, description = :desc, '
            'definition = CAST(:def AS jsonb) WHERE id = :id'
        ),
        {
            'name': definition['name'],
            'desc': definition['description'],
            'def': json.dumps(definition),
            'id': dashboard_id,
        },
    )

    bind.execute(
        sa.text('DELETE FROM custom_dashboard_widget WHERE dashboard_id = :id'),
        {'id': dashboard_id},
    )

    position = 0
    for section in definition.get('sections', []):
        for widget in section.get('widgets', []):
            payload = dict(widget)
            layout = dict(payload.get('layout') or {})
            layout['section_id'] = section.get('id')
            layout['section_title'] = section.get('title')
            payload['layout'] = layout
            bind.execute(
                sa.text(
                    'INSERT INTO custom_dashboard_widget '
                    '(dashboard_id, name, chart_type, definition, position) '
                    'VALUES (:did, :name, :ctype, CAST(:def AS jsonb), :pos)'
                ),
                {
                    'did': dashboard_id,
                    'name': widget['name'],
                    'ctype': widget['chart_type'],
                    'def': json.dumps(payload),
                    'pos': position,
                },
            )
            position += 1


def upgrade():
    _apply_definition(op.get_bind(), _enriched_definition())


def downgrade():
    _apply_definition(op.get_bind(), _original_definition())
