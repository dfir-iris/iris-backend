from marshmallow import Schema, fields as ma_fields, validate


class DashboardWidgetFieldSchema(Schema):
    table = ma_fields.String(required=True)
    column = ma_fields.String(required=True)
    aggregation = ma_fields.String(required=False, allow_none=True, validate=validate.OneOf(['count', 'sum', 'avg', 'min', 'max']))
    alias = ma_fields.String(required=False, allow_none=True)


class DashboardFilterSchema(Schema):
    table = ma_fields.String(required=True)
    column = ma_fields.String(required=True)
    operator = ma_fields.String(required=True, validate=validate.OneOf(['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'in', 'nin', 'between', 'contains']))
    value = ma_fields.Raw(required=True)


class DashboardWidgetSchema(Schema):
    name = ma_fields.String(required=True)
    chart_type = ma_fields.String(
        required=True,
        validate=validate.OneOf(['line', 'bar', 'pie', 'number', 'percentage', 'table'])
    )
    fields = ma_fields.List(ma_fields.Nested(DashboardWidgetFieldSchema), required=True, validate=validate.Length(min=1))
    filters = ma_fields.List(ma_fields.Nested(DashboardFilterSchema), required=False)
    group_by = ma_fields.List(ma_fields.String(), required=False)
    time_bucket = ma_fields.String(required=False, allow_none=True)
    options = ma_fields.Dict(required=False)


class CustomDashboardSchema(Schema):
    name = ma_fields.String(required=True)
    description = ma_fields.String(allow_none=True)
    is_shared = ma_fields.Boolean(required=False)
    widgets = ma_fields.List(ma_fields.Nested(DashboardWidgetSchema), required=True, validate=validate.Length(min=1))
