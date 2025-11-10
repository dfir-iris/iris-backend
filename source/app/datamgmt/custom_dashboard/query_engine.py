from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Query

from app import db
from app.models.alerts import Alert, AlertResolutionStatus, AlertStatus, Severity
from app.models.alerts import AlertCaseAssociation
from app.models.cases import Cases
from app.models.models import CaseClassification, Client


class QueryExecutionError(Exception):
    """Raised when a widget cannot be executed due to an invalid configuration."""


@dataclass
class WidgetQueryResult:
    chart_type: str
    rows: List[Dict[str, Any]]
    group_labels: Sequence[str]
    value_labels: Sequence[str]


_AGGREGATIONS = {
    'count': lambda column: func.count(column),
    'sum': lambda column: func.sum(column),
    'avg': lambda column: func.avg(column),
    'min': lambda column: func.min(column),
    'max': lambda column: func.max(column)
}

_OPERATORS = {
    'eq': lambda column, value: column == value,
    'neq': lambda column, value: column != value,
    'gt': lambda column, value: column > value,
    'gte': lambda column, value: column >= value,
    'lt': lambda column, value: column < value,
    'lte': lambda column, value: column <= value,
    'in': lambda column, value: column.in_(value if isinstance(value, (list, tuple, set)) else [value]),
    'nin': lambda column, value: ~column.in_(value if isinstance(value, (list, tuple, set)) else [value]),
    'between': lambda column, value: column.between(value[0], value[1]) if isinstance(value, (list, tuple)) and len(value) == 2 else None,
    'contains': lambda column, value: column.ilike(f"%{value}%")
}


def _capitalize_label(label: str) -> str:
    if not label:
        return label
    return label.replace('_', ' ').strip().capitalize()


class _WidgetQueryBuilder:
    def __init__(self):
        self.selects: List[Any] = []
        self.select_labels: List[str] = []
        self.group_by_exprs: List[Any] = []
        self.group_labels: List[str] = []
        self.aggregated_labels: List[str] = []
        self.aggregated_exprs: Dict[str, Any] = {}
        self.filters: List[Any] = []
        self.joins: List[str] = []
        self.chart_type: str = ''

    def add_join(self, table_name: str):
        if table_name not in self.joins:
            self.joins.append(table_name)

    def add_group_by(self, expr: Any, label: str):
        if expr not in self.group_by_exprs:
            self.group_by_exprs.append(expr)
        if label not in self.group_labels:
            self.group_labels.append(label)

    def add_select(self, expr: Any, label: str, aggregated: bool):
        labeled_expr = expr.label(label)
        self.selects.append(labeled_expr)
        self.select_labels.append(label)
        if aggregated:
            self.aggregated_labels.append(label)
            self.aggregated_exprs[label] = labeled_expr
        else:
            self.add_group_by(expr, label)


def _between_dates(column, start: Optional[datetime], end: Optional[datetime]):
    expressions = []
    if start is not None:
        expressions.append(column >= start)
    if end is not None:
        expressions.append(column <= end)
    return expressions


class WidgetQueryExecutor:
    """Builds and executes SQLAlchemy queries for dashboard widgets."""

    _BASE_TABLE = 'alerts'

    _TABLES: Dict[str, Dict[str, Any]] = {
        'alerts': {
            'model': Alert,
            'columns': {
                'alert_id': Alert.alert_id,
                'alert_uuid': Alert.alert_uuid,
                'alert_title': Alert.alert_title,
                'alert_source': Alert.alert_source,
                'alert_source_ref': Alert.alert_source_ref,
                'alert_description': Alert.alert_description,
                'alert_creation_time': Alert.alert_creation_time,
                'alert_source_event_time': Alert.alert_source_event_time,
                'alert_customer_id': Alert.alert_customer_id,
                'alert_resolution_status_id': Alert.alert_resolution_status_id,
                'alert_status_id': Alert.alert_status_id,
                'alert_severity_id': Alert.alert_severity_id,
                'alert_classification_id': Alert.alert_classification_id
            },
            'default_time_column': Alert.alert_creation_time
        },
        'client': {
            'model': Client,
            'columns': {
                'client_id': Client.client_id,
                'name': Client.name
            },
            'join': lambda query: query.outerjoin(Client, Alert.customer)
        },
        'alert_resolution_status': {
            'model': AlertResolutionStatus,
            'columns': {
                'resolution_status_id': AlertResolutionStatus.resolution_status_id,
                'resolution_status_name': AlertResolutionStatus.resolution_status_name
            },
            'join': lambda query: query.outerjoin(AlertResolutionStatus, Alert.resolution_status)
        },
        'alert_status': {
            'model': AlertStatus,
            'columns': {
                'status_id': AlertStatus.status_id,
                'status_name': AlertStatus.status_name
            },
            'join': lambda query: query.outerjoin(AlertStatus, Alert.status)
        },
        'severities': {
            'model': Severity,
            'columns': {
                'severity_id': Severity.severity_id,
                'severity_name': Severity.severity_name
            },
            'join': lambda query: query.outerjoin(Severity, Alert.severity)
        },
        'case_classification': {
            'model': CaseClassification,
            'columns': {
                'id': CaseClassification.id,
                'name': CaseClassification.name,
                'name_expanded': CaseClassification.name_expanded
            },
            'join': lambda query: query.outerjoin(CaseClassification, Alert.classification)
        },
        'cases': {
            'model': Cases,
            'columns': {
                'case_id': Cases.case_id,
                'name': Cases.name
            },
            'join': lambda query: query.outerjoin(AlertCaseAssociation, AlertCaseAssociation.alert_id == Alert.alert_id)
                                   .outerjoin(Cases, AlertCaseAssociation.case_id == Cases.case_id)
        }
    }

    def __init__(self, definition: Dict[str, Any]):
        self.definition = definition or {}
        self.builder = _WidgetQueryBuilder()

    def execute(self, timeframe: Tuple[Optional[datetime], Optional[datetime]]) -> WidgetQueryResult:
        widgets_fields = self.definition.get('fields') or []
        if not widgets_fields:
            raise QueryExecutionError('The widget must define at least one field.')

        self.builder.chart_type = (self.definition.get('chart_type') or '').lower()
        if not self.builder.chart_type:
            raise QueryExecutionError('Missing chart type in widget definition.')

        for field in widgets_fields:
            self._add_field(field)

        for group_entry in self.definition.get('group_by', []) or []:
            self._apply_group_by(group_entry)

        for filter_entry in self.definition.get('filters', []) or []:
            self._apply_filter(filter_entry)

        start, end = timeframe
        self._apply_timeframe(start, end)

        query = self._build_query()
        query = self._apply_sorting(query)
        query = self._apply_limit(query)

        rows = query.all()
        mapped_rows = [dict(row._mapping) for row in rows]

        return WidgetQueryResult(
            chart_type=self.builder.chart_type,
            rows=mapped_rows,
            group_labels=self.builder.group_labels,
            value_labels=self.builder.aggregated_labels or [label for label in self.builder.select_labels if label in self.builder.group_labels]
        )

    def _get_table(self, table_name: str) -> Dict[str, Any]:
        table = self._TABLES.get(table_name)
        if not table:
            raise QueryExecutionError(f"Table '{table_name}' is not allowed in custom dashboards.")
        return table

    def _get_column(self, table_name: str, column_name: str):
        table = self._get_table(table_name)
        columns = table.get('columns', {})
        column = columns.get(column_name)
        if column is None:
            raise QueryExecutionError(f"Column '{column_name}' is not allowed for table '{table_name}'.")
        if table_name != self._BASE_TABLE:
            self.builder.add_join(table_name)
        return column

    def _add_field(self, field_definition: Dict[str, Any]):
        table_name = field_definition.get('table')
        column_name = field_definition.get('column')
        if not table_name or not column_name:
            raise QueryExecutionError('Each field must specify a table and column.')

        column = self._get_column(table_name, column_name)
        alias = field_definition.get('alias') or f"{table_name}_{column_name}"
        aggregation = (field_definition.get('aggregation') or '').lower() or None

        if aggregation:
            agg_fn = _AGGREGATIONS.get(aggregation)
            if agg_fn is None:
                raise QueryExecutionError(f"Aggregation '{aggregation}' is not supported.")
            expr = agg_fn(column)
            self.builder.add_select(expr, alias, aggregated=True)
        else:
            self.builder.add_select(column, alias, aggregated=False)

    def _apply_group_by(self, group_entry: str):
        table_name, column_name = self._parse_table_column(group_entry)
        column = self._get_column(table_name, column_name)
        alias = f"{table_name}_{column_name}"
        self.builder.add_select(column, alias, aggregated=False)

    def _apply_filter(self, filter_definition: Dict[str, Any]):
        table_name = filter_definition.get('table')
        column_name = filter_definition.get('column')
        operator = (filter_definition.get('operator') or '').lower()
        value = filter_definition.get('value')

        if not table_name or not column_name or not operator:
            raise QueryExecutionError('Each filter must provide table, column, and operator.')

        column = self._get_column(table_name, column_name)
        operator_fn = _OPERATORS.get(operator)
        if operator_fn is None:
            raise QueryExecutionError(f"Operator '{operator}' is not supported.")

        expression = operator_fn(column, value)
        if expression is None:
            raise QueryExecutionError(f"Operator '{operator}' expects a different value format.")

        self.builder.filters.append(expression)

    def _apply_timeframe(self, start: Optional[datetime], end: Optional[datetime]):
        options = self.definition.get('options') or {}
        time_column_spec = options.get('time_column') or 'alerts.alert_creation_time'
        table_name, column_name = self._parse_table_column(time_column_spec)
        column = self._get_column(table_name, column_name)

        for expression in _between_dates(column, start, end):
            self.builder.filters.append(expression)

    def _build_query(self) -> Query:
        if not any(label in self.builder.select_labels for label in self.builder.aggregated_labels):
            if not self.builder.group_labels:
                raise QueryExecutionError('Widgets must contain at least one aggregated field or grouping column.')

        query = db.session.query(*self.builder.selects)
        query = query.select_from(Alert)
        for join_table in self.builder.joins:
            table = self._get_table(join_table)
            join_callable = table.get('join')
            if join_callable is None:
                raise QueryExecutionError(f"No join strategy registered for table '{join_table}'.")
            query = join_callable(query)

        if self.builder.filters:
            query = query.filter(*self.builder.filters)

        if self.builder.group_by_exprs:
            query = query.group_by(*self.builder.group_by_exprs)

        return query

    def _apply_sorting(self, query: Query) -> Query:
        options = self.definition.get('options') or {}
        sort_direction = (options.get('sort') or '').lower()
        if not sort_direction:
            return query

        order_expression = None
        if self.builder.aggregated_labels:
            primary_label = self.builder.aggregated_labels[0]
            order_expression = self.builder.aggregated_exprs.get(primary_label)
        elif self.builder.group_by_exprs:
            order_expression = self.builder.group_by_exprs[0]

        if order_expression is None:
            return query

        if sort_direction == 'desc':
            return query.order_by(order_expression.desc())
        if sort_direction == 'asc':
            return query.order_by(order_expression.asc())
        return query

    def _apply_limit(self, query: Query) -> Query:
        options = self.definition.get('options') or {}
        limit_value = options.get('limit')
        if isinstance(limit_value, int) and limit_value > 0:
            return query.limit(limit_value)
        return query

    def _parse_table_column(self, value: str) -> Tuple[str, str]:
        if not value or '.' not in value:
            raise QueryExecutionError('Expected table.column format in widget definition.')
        table_name, column_name = value.split('.', 1)
        return table_name.strip(), column_name.strip()


def execute_widget(definition: Dict[str, Any], timeframe: Tuple[Optional[datetime], Optional[datetime]]) -> WidgetQueryResult:
    executor = WidgetQueryExecutor(definition)
    return executor.execute(timeframe)


def format_widget_payload(result: WidgetQueryResult) -> Dict[str, Any]:
    chart_type = result.chart_type

    if chart_type in {'bar', 'line', 'pie'}:
        labels: List[str] = []
        datasets: Dict[str, List[Any]] = {label: [] for label in result.value_labels}

        for row in result.rows:
            label_values = []
            seen_components = set()
            for group_label in result.group_labels:
                raw_value = row.get(group_label)
                if raw_value is None:
                    continue
                text_value = str(raw_value)
                if text_value in seen_components:
                    continue
                seen_components.add(text_value)
                label_values.append(text_value)
            labels.append(' - '.join(label_values) or 'N/A')
            for value_label in result.value_labels:
                datasets.setdefault(value_label, []).append(row.get(value_label))

        formatted_datasets = [
            {
                'label': _capitalize_label(value_label),
                'data': [value if value is not None else 0 for value in values]
            }
            for value_label, values in datasets.items()
        ]

        return {
            'labels': labels,
            'datasets': formatted_datasets
        }

    if chart_type in {'number', 'percentage'}:
        value_label = result.value_labels[0] if result.value_labels else None
        value = None
        if result.rows:
            value = result.rows[0].get(value_label) if value_label else None
        return {
            'value': value,
            'label': _capitalize_label(value_label) if value_label else '',
            'rows': result.rows
        }

    return {
        'rows': result.rows,
        'group_labels': result.group_labels,
        'value_labels': result.value_labels
    }
