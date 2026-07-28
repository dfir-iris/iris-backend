#!/usr/bin/env python3
#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  Generate an OpenAPI 3.1 spec by statically parsing the v2 blueprint
#  files with ast — no Flask boot, no DB, no runtime imports beyond
#  PyYAML. This is the Phase 1 shape: every route annotated with
#  @api_doc(...) becomes an OpenAPI operation; schemas are referenced
#  by name only (`$ref: '#/components/schemas/Case'`). The property
#  list for each schema is expected to live in a hand-written fragment
#  under iris-doc-src/ and to be bundled at doc-build time.
#
#  Regenerate:
#      cd source && python -m scripts.generate_openapi \
#          --output app/blueprints/rest/openapi.generated.yaml
#
#  The generator walks source/app/blueprints/rest/v2/ recursively,
#  reads each blueprint's url_prefix, and combines it with parent
#  prefixes (via the register_blueprint chain rooted at rest_v2_blueprint
#  in api_v2_routes.py) to compute absolute paths.

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent  # source/
V2_ROOT = REPO_ROOT / 'app' / 'blueprints' / 'rest' / 'v2'
API_V2_ROUTES = REPO_ROOT / 'app' / 'blueprints' / 'rest' / 'api_v2_routes.py'
MARSHABLES = REPO_ROOT / 'app' / 'schema' / 'marshables.py'
MODELS_ROOT = REPO_ROOT / 'app' / 'models'

_PATH_PARAM_RE = re.compile(r'<(?:([^:>]+):)?([^>]+)>')
_HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete'}

# Flask URL converters → OpenAPI (type, format).
# `path` is a catch-all matching slashes; documented as string.
# `uuid` gets `format: uuid`. `any` and `default` fall through to string.
_FLASK_CONVERTER_TO_OPENAPI: dict[str, tuple[str, str | None]] = {
    'int': ('integer', None),
    'float': ('number', 'float'),
    'string': ('string', None),
    'path': ('string', None),
    'uuid': ('string', 'uuid'),
    'default': ('string', None),
    'any': ('string', None),
}


def _flask_path_to_openapi(rule: str) -> str:
    return _PATH_PARAM_RE.sub(r'{\2}', rule)


def _split_docstring(doc: str | None) -> tuple[str, str]:
    if not doc:
        return '', ''
    lines = [line.strip() for line in doc.strip().splitlines()]
    return lines[0], '\n'.join(lines[1:]).strip()


def _literal(node: ast.AST) -> Any:
    """Best-effort literal eval; returns the raw ast.Name.id for
    schema class references (so we can turn them into $refs by name)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return _SchemaRef(node.id)
    if isinstance(node, ast.List):
        return [_literal(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal(e) for e in node.elts)
    return None


class _SchemaRef:
    """Marker for an unresolved schema class reference."""
    def __init__(self, name: str):
        self.name = name


def _schema_component_name(cls_name: str) -> str:
    return cls_name[:-len('Schema')] if cls_name.endswith('Schema') else cls_name


# --------------------------------------------------------------------
# Marshmallow schema resolution (AST-only)
# --------------------------------------------------------------------
#
# Given source/app/schema/marshables.py we produce, per schema class,
# an OpenAPI object schema listing every explicitly-declared field —
# `fields.X`, `auto_field(...)`, `ma.Nested(Y)`, `ma.Method(...)`.
#
# We don't try to resolve the SQLAlchemy `Meta.model` columns (that
# would require importing the ORM), so `SQLAlchemyAutoSchema` classes
# with no explicit field declarations get an open object schema. This
# is honest — we document what we can see; hand-written fragments in
# iris-doc-src can layer on richer types where needed.

_MA_FIELD_TO_OPENAPI: dict[str, dict[str, Any]] = {
    'String': {'type': 'string'},
    'Str': {'type': 'string'},
    'Integer': {'type': 'integer'},
    'Int': {'type': 'integer'},
    'Float': {'type': 'number', 'format': 'float'},
    'Number': {'type': 'number'},
    'Decimal': {'type': 'string', 'format': 'decimal'},
    'Boolean': {'type': 'boolean'},
    'Bool': {'type': 'boolean'},
    'DateTime': {'type': 'string', 'format': 'date-time'},
    'Date': {'type': 'string', 'format': 'date'},
    'Time': {'type': 'string', 'format': 'time'},
    'UUID': {'type': 'string', 'format': 'uuid'},
    'Email': {'type': 'string', 'format': 'email'},
    'URL': {'type': 'string', 'format': 'uri'},
    'Url': {'type': 'string', 'format': 'uri'},
    'Raw': {},
    'Dict': {'type': 'object'},
    'Function': {},
    'Method': {},
    'Constant': {},
}


def _field_call_name(call: ast.Call) -> str | None:
    """Return the leaf attribute name of a marshmallow field call.

    fields.String(...)   → 'String'
    ma.fields.Integer()  → 'Integer'
    ma.Nested(...)       → 'Nested'
    fields.List(...)     → 'List'
    auto_field(...)      → 'auto_field'
    Nested(...)          → 'Nested'
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _extract_field_kwarg_constant(call: ast.Call, name: str) -> Any:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _nested_target_name(call: ast.Call) -> str | None:
    """First positional arg to ma.Nested — the schema class name."""
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _resolve_field_schema(
    call: ast.Call,
    schemas_seen: set[tuple[str, str, str]],
    mode: str = 'response',
) -> dict[str, Any]:
    """Turn a marshmallow field-construction call into an OpenAPI schema.

    Transitively nested schemas are always registered in RESPONSE mode.
    The request/response split matters at the top-level operation
    boundary (POST body vs. GET response); nested resources like
    `owner`/`severity` inside a top-level Alert are documented once
    with the full read shape and referenced from both directions.
    """
    name = _field_call_name(call)
    if name is None:
        return {}

    if name == 'Nested':
        target = _nested_target_name(call)
        if target is None:
            return {'type': 'object'}
        component = _schema_component_name(target)
        schemas_seen.add((component, target, 'response'))
        many = _extract_field_kwarg_constant(call, 'many') is True
        ref = {'$ref': f'#/components/schemas/{component}'}
        return {'type': 'array', 'items': ref} if many else ref

    if name == 'List':
        inner_schema: dict[str, Any] = {}
        if call.args and isinstance(call.args[0], ast.Call):
            inner_schema = _resolve_field_schema(call.args[0], schemas_seen, mode=mode)
        elif call.args and isinstance(call.args[0], ast.Attribute):
            # fields.List(fields.String) — bare class ref, no ()
            attr_name = call.args[0].attr
            inner_schema = dict(_MA_FIELD_TO_OPENAPI.get(attr_name, {}))
        elif call.args and isinstance(call.args[0], ast.Name):
            # fields.List(SomeSchema) — treat as nested
            target = call.args[0].id
            component = _schema_component_name(target)
            schemas_seen.add((component, target, 'response'))
            inner_schema = {'$ref': f'#/components/schemas/{component}'}
        return {'type': 'array', 'items': inner_schema or {}}

    if name == 'auto_field':
        # We can't recover the SQLAlchemy column type without importing
        # the model, so leave the type open. The field NAME still lands
        # in `properties`, which is the main value of auto_field for
        # documentation purposes.
        return {}

    if name in _MA_FIELD_TO_OPENAPI:
        return dict(_MA_FIELD_TO_OPENAPI[name])

    # Unknown field type — emit an empty schema (still lists the property).
    return {}


def _is_schema_base(base: ast.expr) -> bool:
    """Recognise marshmallow schema base classes we care about."""
    if isinstance(base, ast.Name):
        return base.id in {'Schema', 'SQLAlchemyAutoSchema'}
    if isinstance(base, ast.Attribute):
        return base.attr in {'Schema', 'SQLAlchemyAutoSchema'}
    return False


def _class_is_schema(cls: ast.ClassDef, known_schemas: set[str]) -> bool:
    for base in cls.bases:
        if _is_schema_base(base):
            return True
        # Inheritance from another schema class we've already discovered
        # (e.g. `class SearchCaseNoteDirectorySchema(CaseNoteDirectorySchema)`).
        if isinstance(base, ast.Name) and base.id in known_schemas:
            return True
    return cls.name.endswith('Schema')


def _extract_meta_exclude(cls: ast.ClassDef) -> set[str]:
    return _extract_meta_string_iterable(cls, 'exclude')


def _extract_meta_string_iterable(cls: ast.ClassDef, name: str) -> set[str]:
    for node in cls.body:
        if not (isinstance(node, ast.ClassDef) and node.name == 'Meta'):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(stmt.value, (ast.List, ast.Tuple, ast.Set)):
                        return {
                            elt.value for elt in stmt.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    return set()


def _extract_meta_flag(cls: ast.ClassDef, name: str) -> bool:
    """Return the boolean value of `Meta.<name>` if present, else False."""
    for node in cls.body:
        if not (isinstance(node, ast.ClassDef) and node.name == 'Meta'):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, bool)
                ):
                    return stmt.value.value
    return False


def _extract_meta_model(cls: ast.ClassDef) -> str | None:
    """Return the ORM class name from `Meta.model = <Name>`."""
    for node in cls.body:
        if not (isinstance(node, ast.ClassDef) and node.name == 'Meta'):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == 'model':
                    if isinstance(stmt.value, ast.Name):
                        return stmt.value.id
    return None


# --------------------------------------------------------------------
# SQLAlchemy ORM model resolution (AST-only)
# --------------------------------------------------------------------
#
# `SQLAlchemyAutoSchema` inspects its `Meta.model` at runtime and
# emits a field per column — including foreign-key columns when
# `include_fk=True` (which is the default in most iris schemas).
# Without walking the model, our schema components are missing the
# scalar payload fields entirely: e.g. AlertSchema declared only
# nested `owner`, `severity`, ... but the API actually accepts (and
# emits) `alert_owner_id`, `alert_severity_id`, `alert_title` …
#
# We parse `source/app/models/*.py` once and build a per-class map of
# {attribute_name: openapi_schema}. Column type mapping is best-effort
# — the surface used by iris models is small enough that a static
# table catches ~all real columns.

_SA_TYPE_TO_OPENAPI: dict[str, dict[str, Any]] = {
    'Integer': {'type': 'integer'},
    'BigInteger': {'type': 'integer', 'format': 'int64'},
    'SmallInteger': {'type': 'integer'},
    'Numeric': {'type': 'number'},
    'Float': {'type': 'number', 'format': 'float'},
    'Boolean': {'type': 'boolean'},
    'String': {'type': 'string'},
    'Unicode': {'type': 'string'},
    'Text': {'type': 'string'},
    'UnicodeText': {'type': 'string'},
    'LargeBinary': {'type': 'string', 'format': 'binary'},
    'Date': {'type': 'string', 'format': 'date'},
    'DateTime': {'type': 'string', 'format': 'date-time'},
    'Time': {'type': 'string', 'format': 'time'},
    'JSON': {'type': 'object'},
    'JSONB': {'type': 'object'},
    'UUID': {'type': 'string', 'format': 'uuid'},
    'Enum': {'type': 'string'},
    'ARRAY': {'type': 'array', 'items': {}},
}


def _column_type_to_openapi(call: ast.Call) -> dict[str, Any]:
    """Given a `Column(...)` call, extract the OpenAPI schema for its
    SQLAlchemy type argument.

    We look at the first positional arg that isn't a `ForeignKey(...)`
    call — that's the type. If the type is passed as an instance
    (`String(64)`), we use the class name. If it's passed as a class
    reference (`Integer`), we use the identifier. `ForeignKey(...)`
    columns default to `integer` unless the ForeignKey target's PK
    type is knowable — good-enough default since iris uses integer
    PKs everywhere except `alert_uuid`-style UUID columns, which
    declare their type alongside the FK.
    """
    for arg in call.args:
        # Skip ForeignKey wrapper — inspect the sibling type arg.
        if isinstance(arg, ast.Call) and _call_name(arg.func) == 'ForeignKey':
            continue
        if isinstance(arg, ast.Call):
            type_name = _call_name(arg.func)
            if type_name and type_name in _SA_TYPE_TO_OPENAPI:
                return dict(_SA_TYPE_TO_OPENAPI[type_name])
        if isinstance(arg, ast.Name) and arg.id in _SA_TYPE_TO_OPENAPI:
            return dict(_SA_TYPE_TO_OPENAPI[arg.id])
        if isinstance(arg, ast.Attribute) and arg.attr in _SA_TYPE_TO_OPENAPI:
            return dict(_SA_TYPE_TO_OPENAPI[arg.attr])
    # ForeignKey-only column with no explicit type — iris uses integer
    # PKs by convention, so default to integer.
    if any(
        isinstance(arg, ast.Call) and _call_name(arg.func) == 'ForeignKey'
        for arg in call.args
    ):
        return {'type': 'integer'}
    return {}


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _column_is_fk(call: ast.Call) -> bool:
    return any(
        isinstance(arg, ast.Call) and _call_name(arg.func) == 'ForeignKey'
        for arg in call.args
    )


def _column_nullable(call: ast.Call) -> bool:
    """Columns are nullable unless `nullable=False` or `primary_key=True`."""
    for kw in call.keywords:
        if kw.arg == 'nullable' and isinstance(kw.value, ast.Constant):
            return bool(kw.value.value)
        if kw.arg == 'primary_key' and isinstance(kw.value, ast.Constant) and kw.value.value:
            return False
    return True


def _parse_orm_class(cls: ast.ClassDef) -> dict[str, dict[str, Any]] | None:
    """Return {column_name: {'schema': …, 'fk': bool, 'nullable': bool}}
    for a class inheriting from `db.Model` (or similar), or None if the
    class isn't ORM-shaped.

    We accept the class if either its base is `Model` / `Base` (SQLAlchemy
    declarative) or it defines a `__tablename__` attribute (covers
    projects that alias `Base = declarative_base()` under a different
    name).
    """
    is_model = False
    for base in cls.bases:
        base_name = base.attr if isinstance(base, ast.Attribute) else getattr(base, 'id', None)
        if base_name in {'Model', 'Base'}:
            is_model = True
            break
    if not is_model and not _class_has_tablename(cls):
        return None

    columns: dict[str, dict[str, Any]] = {}
    for node in cls.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_name(node.value.func) != 'Column':
            continue
        openapi = _column_type_to_openapi(node.value)
        fk = _column_is_fk(node.value)
        nullable = _column_nullable(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                columns[target.id] = {
                    'schema': openapi,
                    'fk': fk,
                    'nullable': nullable,
                }
    return columns if columns else None


def _class_has_tablename(cls: ast.ClassDef) -> bool:
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == '__tablename__':
                    return True
    return False


def _build_model_index() -> dict[str, dict[str, dict[str, Any]]]:
    """Parse every `app/models/*.py` file once and return
    {ModelClassName: {column_name: {'schema': …, 'fk': bool,
    'nullable': bool}}}."""
    index: dict[str, dict[str, dict[str, Any]]] = {}
    if not MODELS_ROOT.exists():
        return index
    for f in sorted(MODELS_ROOT.rglob('*.py')):
        try:
            tree = ast.parse(f.read_text(), filename=str(f))
        except SyntaxError as exc:
            print(f'openapi: cannot parse {f.name} ({exc})', file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            columns = _parse_orm_class(node)
            if columns:
                index[node.name] = columns
    return index


# Populated lazily; kept module-global so `_parse_schema_class` can
# see it without threading the map through every helper.
_MODEL_INDEX: dict[str, dict[str, dict[str, Any]]] | None = None


def _get_model_index() -> dict[str, dict[str, dict[str, Any]]]:
    global _MODEL_INDEX
    if _MODEL_INDEX is None:
        _MODEL_INDEX = _build_model_index()
    return _MODEL_INDEX


def _column_has_default(call: ast.Call) -> bool:
    """A SQLAlchemy Column has an implicit value when `default=` or
    `server_default=` is set, or when it's a primary key (usually
    autoincrement). Columns with a default can be omitted from a POST
    body, so they should NOT land in the OpenAPI `required` list.
    """
    for kw in call.keywords:
        if kw.arg in ('default', 'server_default'):
            return True
        if kw.arg == 'primary_key' and isinstance(kw.value, ast.Constant) and kw.value.value:
            return True
    return False


# Re-parse the raw Column ast we stored so we can determine defaults
# lazily (kept out of `_parse_orm_class`'s output to keep the model
# index a plain data structure; `_column_has_default` is called from
# a second walk).
def _model_column_has_default(model_name: str, column_name: str) -> bool:
    for f in sorted(MODELS_ROOT.rglob('*.py')):
        try:
            tree = ast.parse(f.read_text(), filename=str(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != model_name:
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                    continue
                if _call_name(stmt.value.func) != 'Column':
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == column_name:
                        return _column_has_default(stmt.value)
    return False


def _parse_schema_class(
    cls: ast.ClassDef,
    schemas_seen: set[tuple[str, str, str]],
    mode: str = 'response',
) -> dict[str, Any]:
    """Convert a marshmallow schema AST node into an OpenAPI object schema.

    `mode` is 'response' or 'request':
      * response — the shape the API returns. Drops `load_only`
        fields. `nullable=False` on the model does NOT make fields
        required (the response always includes them, marshmallow's
        `required` doesn't apply to dump).
      * request — the shape the API accepts. Drops `dump_only`
        fields, drops `fields.Method(...)` (dump-only by nature).
        Nested-schema overlays (`ma.Nested(X)`) are dropped when the
        parent model has a corresponding FK column, since callers
        send `<name>_id` rather than the nested object (marshmallow
        load_instance accepts both, but the FK is the ergonomic
        idiom). `required` = explicit `required=True` ∪ every model
        column with `nullable=False` and no default.

    `schemas_seen` accumulates transitively-referenced schemas as
    (component_name, source_cls, mode) triples so the generator can
    emit a components entry per mode.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    autofield_targets: dict[str, str] = {}   # class-attr → ORM column name
    nested_overlays: set[str] = set()        # class-attr names that are ma.Nested(X)

    exclude = _extract_meta_exclude(cls)

    for node in cls.body:
        # Marshmallow schemas mix plain assignments (`foo = fields.Str()`)
        # and PEP-526 annotated assignments (`foo: str = auto_field(...)`).
        # Handle both.
        if isinstance(node, ast.Assign):
            if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            target = node.target
            value = node.value
        else:
            continue

        if not isinstance(value, ast.Call):
            continue

        field_name = target.id
        if field_name in exclude:
            continue

        # Skip fields not applicable to this mode.
        dump_only = _extract_field_kwarg_constant(value, 'dump_only') is True
        load_only = _extract_field_kwarg_constant(value, 'load_only') is True
        field_name_call = _field_call_name(value)
        if mode == 'request' and (dump_only or field_name_call == 'Method'):
            continue
        if mode == 'response' and load_only:
            continue

        schema = _resolve_field_schema(value, schemas_seen, mode=mode)

        # For `auto_field('column_name', ...)` remember the ORM
        # attribute so we can later fill in its type from the model.
        if field_name_call == 'auto_field':
            if value.args and isinstance(value.args[0], ast.Constant) \
                    and isinstance(value.args[0].value, str):
                autofield_targets[field_name] = value.args[0].value
            else:
                autofield_targets[field_name] = field_name

        if field_name_call == 'Nested':
            nested_overlays.add(field_name)

        if _extract_field_kwarg_constant(value, 'required') is True:
            required.append(field_name)

        if _extract_field_kwarg_constant(value, 'allow_none') is True:
            # OpenAPI 3.1: express nullability with a type union.
            if 'type' in schema and 'nullable' not in schema:
                schema = {**schema, 'type': [schema['type'], 'null']}

        properties[field_name] = schema or {}

    # If the schema binds to a SQLAlchemy model via `Meta.model = X`,
    # merge in the model's columns so auto_field / SQLAlchemyAutoSchema
    # fields land in the spec instead of being reduced to `{}`. This is
    # what marshmallow-sqlalchemy does at runtime.
    model_name = _extract_meta_model(cls)
    if model_name:
        columns = _get_model_index().get(model_name, {})
        include_fk = _extract_meta_flag(cls, 'include_fk')
        include_rels = _extract_meta_flag(cls, 'include_relationships')

        # 1. Backfill types on `auto_field` declarations that were
        #    emitted as `{}` above.
        for attr, orm_column in autofield_targets.items():
            if properties.get(attr):
                continue
            column = columns.get(orm_column)
            if column and column['schema']:
                properties[attr] = dict(column['schema'])
                if column['nullable'] and 'type' in properties[attr]:
                    properties[attr] = {
                        **properties[attr],
                        'type': [properties[attr]['type'], 'null'],
                    }

        # 2. Add every ORM column NOT already declared, subject to
        #    Meta.exclude and (for FKs) include_fk. Relationships live
        #    on the model but as Python-only attrs — they're not
        #    `Column(...)` so they never made it into the model index,
        #    which matches marshmallow's behaviour when
        #    include_relationships is False.
        _ = include_rels  # currently only used to gate a warning path
        for orm_column, meta in columns.items():
            if orm_column in properties or orm_column in exclude:
                continue
            if meta['fk'] and not include_fk:
                continue
            column_schema = dict(meta['schema']) if meta['schema'] else {}
            if meta['nullable'] and 'type' in column_schema:
                column_schema = {
                    **column_schema,
                    'type': [column_schema['type'], 'null'],
                }
            properties[orm_column] = column_schema

        if mode == 'request':
            # Drop nested-schema overlays when the parent's model has a
            # matching FK column — the API idiom is to send `<name>_id`,
            # not the nested object. If no FK, keep the nested field so
            # non-model-bound relationships still document.
            for overlay in list(nested_overlays):
                fk_candidates = {f'{overlay}_id', f'alert_{overlay}_id',
                                 f'case_{overlay}_id', f'ioc_{overlay}_id'}
                if any(c in properties for c in fk_candidates):
                    properties.pop(overlay, None)
                    if overlay in required:
                        required.remove(overlay)

            # Every non-nullable model column without a default is a
            # required POST field. Skip primary keys (server-assigned)
            # and Meta.exclude / Meta.dump_only entries.
            dump_only_meta = _extract_meta_string_iterable(cls, 'dump_only')
            for orm_column, meta in columns.items():
                if meta['nullable'] or meta['fk'] and not include_fk:
                    continue
                if orm_column in exclude or orm_column in dump_only_meta:
                    continue
                if orm_column not in properties:
                    continue
                if _model_column_has_default(model_name, orm_column):
                    continue
                if orm_column not in required:
                    required.append(orm_column)

    if mode == 'request':
        # Response-only Method fields are already dropped above; also
        # remove them from `required` (they'd never make sense on a POST).
        # No-op if they never got added.
        pass

    result: dict[str, Any] = {'type': 'object', 'properties': properties}
    if required:
        result['required'] = sorted(set(required))
    doc = ast.get_docstring(cls)
    if doc:
        result['description'] = doc.strip().splitlines()[0]
    return result


def _iter_refs(node: Any) -> Any:
    """Yield every '#/components/schemas/X' $ref value inside a nested
    dict/list structure (as strings)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == '$ref' and isinstance(value, str):
                yield value
            else:
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _build_schema_ast_index() -> dict[str, ast.ClassDef]:
    """Parse marshables.py once and return {ClassName: ClassDef ast}.

    The actual OpenAPI schema is derived per-mode by
    `_parse_schema_class` — we can't pre-compute at index time
    because response and request modes need different outputs.
    """
    if not MARSHABLES.exists():
        return {}
    try:
        tree = ast.parse(MARSHABLES.read_text(), filename=str(MARSHABLES))
    except SyntaxError as exc:
        print(f'openapi: cannot parse {MARSHABLES.name} ({exc})', file=sys.stderr)
        return {}

    known_schemas: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith('Schema'):
            known_schemas.add(node.name)

    index: dict[str, ast.ClassDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _class_is_schema(node, known_schemas):
            continue
        index[node.name] = node
    return index


# --------------------------------------------------------------------
# Blueprint prefix resolution
# --------------------------------------------------------------------

def _extract_prefix_from_call(call: ast.Call) -> str:
    """Return the url_prefix passed to a `Blueprint(...)` call, or ''.

    Handles both `Blueprint(..., url_prefix='/foo')` and the factory
    idiom `Blueprint(..., url_prefix=f'/{url_prefix}')` where the
    f-string interpolates a factory parameter that we later resolve
    from each call site.
    """
    for kw in call.keywords:
        if kw.arg != 'url_prefix':
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value or ''
        # Factory-style f'/{name}' — leave as-is; the caller resolves.
        if isinstance(kw.value, ast.JoinedStr):
            return _dump_fstring_placeholder(kw.value)
    return ''


def _dump_fstring_placeholder(node: ast.JoinedStr) -> str:
    """Render an f-string as a placeholder-form template string.

    `f'/{url_prefix}'` → `'/{$url_prefix}'`. Callers pattern-match on
    `{$name}` markers to substitute per-call-site values.
    """
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            expr = value.value
            if isinstance(expr, ast.Name):
                parts.append('{$' + expr.id + '}')
            elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
                # e.g. `config.url_prefix` → `{$config.url_prefix}`.
                parts.append('{$' + expr.value.id + '.' + expr.attr + '}')
            else:
                parts.append('{$?}')
    return ''.join(parts)


def _parse_blueprints(path: Path) -> dict[str, dict[str, Any]]:
    """Extract Blueprint(...) assignments + register_blueprint() edges.

    Returns {variable_name: {'prefix': str, 'children': [child_var]}}
    limited to the current module. Also handles the factory idiom
    `X = _build_thing(url_prefix='foo', ...)` (or with a config
    object) by resolving `_build_thing`'s inner `Blueprint(...,
    url_prefix=f'/{url_prefix}')` template against the call's kwargs.

    Callers combine per-module data into a full prefix map.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    bps: dict[str, dict[str, Any]] = {}

    # First pass: identify factory functions (returning a Blueprint with
    # a template url_prefix like f'/{param}').
    factories: dict[str, dict[str, Any]] = _find_factory_blueprints(tree)

    # Second pass: collect config-object literals so we can resolve
    # `_build_blueprint(config)` calls where config is a dataclass-like
    # object with a url_prefix attribute.
    config_prefixes: dict[str, str] = _find_config_url_prefixes(tree)

    # Only look at module-level statements, not nested ones. Nested
    # `bp = Blueprint(...)` lives inside a factory and is discovered
    # separately via `_find_factory_blueprints`; picking it up here
    # would double-register it as a top-level blueprint whose prefix
    # is the raw f-string template.
    for node in tree.body:
        # `foo = Blueprint('name', __name__, url_prefix='/x')`
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)

            if fname == 'Blueprint':
                prefix = _extract_prefix_from_call(call)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bps.setdefault(target.id, {'prefix': prefix, 'children': []})

            # `foo = _build_readonly_blueprint(url_prefix='x', ...)` or
            # `foo = _build_blueprint(some_config)` — direct assignment
            # of a factory's return value. Route emission needs the
            # factory metadata (which local var inside the factory
            # holds the blueprint, so we can bind routes to it).
            elif fname in factories:
                factory = factories[fname]
                prefix = _resolve_factory_prefix(factory, call, config_prefixes)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bps.setdefault(target.id, {
                            'prefix': prefix,
                            'children': [],
                            'factory': fname,
                            'local_bp': factory['local_bp'],
                        })

        # `foo.register_blueprint(bar)` — where bar may be a Name or a
        # factory call like `_build_blueprint(some_config)`.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == 'register_blueprint'
                and isinstance(call.func.value, ast.Name)
                and call.args
            ):
                parent = call.func.value.id
                arg = call.args[0]
                child: str | None = None
                if isinstance(arg, ast.Name):
                    child = arg.id
                elif isinstance(arg, ast.Call):
                    inner_fname = (
                        arg.func.id if isinstance(arg.func, ast.Name)
                        else getattr(arg.func, 'attr', None)
                    )
                    if inner_fname in factories:
                        # Synthesise a virtual blueprint per call site so
                        # each factory registration lives at its own
                        # resolved prefix. The name is stable — same
                        # (parent, factory, config) always maps to the
                        # same virtual key — so re-runs remain idempotent.
                        prefix = _resolve_factory_prefix(
                            factories[inner_fname], arg, config_prefixes
                        )
                        child = _virtual_blueprint_name(
                            path.stem, inner_fname, arg, config_prefixes
                        )
                        info = bps.setdefault(child, {'prefix': prefix, 'children': []})
                        if prefix and not info['prefix']:
                            info['prefix'] = prefix
                        # Tag the virtual blueprint with the factory
                        # it wraps so route emission can recognise it.
                        info['factory'] = inner_fname
                        info['local_bp'] = factories[inner_fname]['local_bp']
                if child is not None:
                    bps.setdefault(parent, {'prefix': '', 'children': []})
                    bps[parent]['children'].append(child)

        # `for _c in _CONFIGS: parent.register_blueprint(_build_blueprint(_c))`
        # — the loop generates one registration per list entry. Unroll
        # each entry so every registered blueprint gets its own
        # prefix-resolved virtual entry.
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name):
            configs_name = node.iter.id
            loop_var = node.target.id if isinstance(node.target, ast.Name) else None
            if loop_var:
                for stmt in node.body:
                    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
                        continue
                    call = stmt.value
                    if not (
                        isinstance(call.func, ast.Attribute)
                        and call.func.attr == 'register_blueprint'
                        and isinstance(call.func.value, ast.Name)
                        and call.args
                        and isinstance(call.args[0], ast.Call)
                    ):
                        continue
                    parent = call.func.value.id
                    factory_call = call.args[0]
                    inner_fname = (
                        factory_call.func.id if isinstance(factory_call.func, ast.Name)
                        else getattr(factory_call.func, 'attr', None)
                    )
                    if inner_fname not in factories:
                        continue
                    # Enumerate every `_CONFIGS[i]` entry and register
                    # one virtual blueprint per iteration.
                    for key, prefix in config_prefixes.items():
                        if not key.startswith(f'{configs_name}['):
                            continue
                        virtual_name = f'__vloop__{path.stem}__{inner_fname}__{key}'
                        info = bps.setdefault(virtual_name, {
                            'prefix': '/' + prefix if not prefix.startswith('/') else prefix,
                            'children': [],
                        })
                        info['factory'] = inner_fname
                        info['local_bp'] = factories[inner_fname]['local_bp']
                        bps.setdefault(parent, {'prefix': '', 'children': []})
                        bps[parent]['children'].append(virtual_name)

    return bps


def _find_factory_blueprints(tree: ast.Module) -> dict[str, dict[str, Any]]:
    """Discover functions that build and return a Blueprint.

    A factory looks like:

        def _build(url_prefix, ...):
            bp = Blueprint(name, __name__, url_prefix=f'/{url_prefix}')
            @bp.get('')
            def x(): ...
            return bp

    We record:
        {factory_name: {
            'local_bp': 'bp',                # local var holding the Blueprint
            'prefix_template': '/{$url_prefix}',  # template string
            'params': ['url_prefix', ...],   # positional param names
        }}
    """
    factories: dict[str, dict[str, Any]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in fn.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            call_func = call.func
            fname = (
                call_func.attr if isinstance(call_func, ast.Attribute)
                else getattr(call_func, 'id', None)
            )
            if fname != 'Blueprint':
                continue
            template = _extract_prefix_from_call(call)
            if not template:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    factories[fn.name] = {
                        'local_bp': target.id,
                        'prefix_template': template,
                        'params': [arg.arg for arg in fn.args.args],
                    }
                    break
            break
    return factories


def _find_config_url_prefixes(tree: ast.Module) -> dict[str, str]:
    """Static registry of `TaxonomyConfig(url_prefix='asset-types', ...)`
    literals so we can resolve `_build_blueprint(config_var)` calls
    against the same file's config assignments.

    Two shapes captured:
      * `NAME = TaxonomyConfig(url_prefix='x', ...)` at module scope.
      * `TaxonomyConfig(url_prefix='x', ...)` inside a list literal
        assigned to `_CONFIGS = [TaxonomyConfig(...), ...]` — used by
        the `for c in _CONFIGS: parent.register_blueprint(_build(c))`
        idiom. We don't try to match individual list entries back to
        specific register_blueprint calls (the loop makes that a
        1-to-N mapping) but instead treat every list entry as its own
        virtual registration.
    """
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # `NAME = SomeConfig(url_prefix='x')`
        if isinstance(node.value, ast.Call):
            prefix = _extract_kwarg_string(node.value, 'url_prefix')
            if prefix:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
        # `_CONFIGS = [SomeConfig(url_prefix='x'), SomeConfig(url_prefix='y')]`
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for i, elt in enumerate(node.value.elts):
                if isinstance(elt, ast.Call):
                    prefix = _extract_kwarg_string(elt, 'url_prefix')
                    if not prefix:
                        continue
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            prefixes[f'{target.id}[{i}]'] = prefix
    return prefixes


def _extract_kwarg_string(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _resolve_factory_prefix(
    factory: dict[str, Any],
    call: ast.Call,
    config_prefixes: dict[str, str],
) -> str:
    """Substitute the factory's `prefix_template` placeholders against
    the call's arguments and available config-object url_prefixes.

    Substitution rules:
      * `{$name}` where `name` is a factory param — look up the value
        from `call.keywords` (`name=...`) or the matching positional
        arg. String constants substitute directly.
      * `{$name.url_prefix}` — look up `name` as a config variable in
        the file's `config_prefixes` map (built by
        `_find_config_url_prefixes`). This handles
        `_build_blueprint(config)` where config's url_prefix is
        determined by the caller — we can't statically bind to a
        single instance, so we resolve nothing and return the
        template's static prefix (usually just '/').
    """
    template = factory['prefix_template']
    if not template:
        return ''

    # Fast path: template has no placeholders.
    if '{$' not in template:
        return template

    # Build kwarg map from the call. Positional args map by index to
    # factory params.
    resolved: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            resolved[kw.arg] = kw.value.value
    for i, arg in enumerate(call.args):
        if i >= len(factory['params']):
            break
        param_name = factory['params'][i]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            resolved[param_name] = arg.value
        elif isinstance(arg, ast.Name):
            # `_build_blueprint(config)` — resolve config.url_prefix
            # via the file-scope config map.
            for key, value in config_prefixes.items():
                # `key` may be `config_name` or `_CONFIGS[i]` — accept
                # the first that matches the passed variable name.
                if key == arg.id:
                    resolved[param_name] = value
                    resolved[param_name + '.url_prefix'] = value
                    break

    # Substitute placeholders. `{$name}` and `{$name.url_prefix}`.
    def sub(match: re.Match) -> str:
        key = match.group(1)
        return resolved.get(key, '')

    return re.sub(r'\{\$([^}]+)\}', sub, template)


def _virtual_blueprint_name(
    file_stem: str,
    factory_name: str,
    call: ast.Call,
    config_prefixes: dict[str, str],
) -> str:
    """Synthesise a stable name for a factory-registered virtual
    blueprint.

    The name is only used internally to key the `bps`/`prefix_map`
    dicts — it never lands in the emitted spec. Determinism matters
    because the CI drift check compares byte-for-byte.
    """
    parts = [file_stem, factory_name]
    for kw in call.keywords:
        if kw.arg == 'url_prefix' and isinstance(kw.value, ast.Constant):
            parts.append(str(kw.value.value))
    for arg in call.args:
        if isinstance(arg, ast.Constant):
            parts.append(str(arg.value))
        elif isinstance(arg, ast.Name):
            parts.append(config_prefixes.get(arg.id, arg.id))
    return '__vfact__' + '__'.join(parts)


def _build_prefix_map() -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    """Walk the api_v2_routes registration tree and every child module
    to compute the absolute URL prefix per blueprint variable.

    Returns two dicts:
      * `absolute`: {variable_name: absolute_prefix} — the classic map.
      * `factory_bindings`: {(file_stem, factory_name): [
             {'prefix': absolute_prefix, 'local_bp': 'bp'}, ...
         ]} — every registration site of a factory function, so
         `_discover_routes` can emit each factory route once per site.

    We use the variable name as the identity key. Duplicate names
    across modules would collide — the v2 codebase already avoids
    that (each blueprint is a globally-unique variable). Virtual
    blueprint keys (`__vfact__…` / `__vloop__…`) are guaranteed unique
    by construction.
    """
    all_bps: dict[str, dict[str, Any]] = {}
    files: list[Path] = [API_V2_ROUTES]
    files.extend(V2_ROOT.rglob('*.py'))

    for f in files:
        if not f.exists():
            continue
        try:
            for name, info in _parse_blueprints(f).items():
                existing = all_bps.setdefault(name, {'prefix': '', 'children': []})
                if info.get('prefix') and not existing['prefix']:
                    existing['prefix'] = info['prefix']
                if info.get('factory'):
                    existing['factory'] = info['factory']
                    existing['local_bp'] = info['local_bp']
                    existing['source_file'] = f.stem
                existing['children'].extend(info['children'])
        except SyntaxError as exc:
            print(f'openapi: skipping {f} ({exc})', file=sys.stderr)

    # Traverse from the root, accumulating prefixes.
    absolute: dict[str, str] = {}
    factory_bindings: dict[str, list[dict[str, Any]]] = {}

    def walk(var: str, parent_prefix: str) -> None:
        if var in absolute:
            return
        info = all_bps.get(var)
        if info is None:
            return
        absolute[var] = parent_prefix + info['prefix']
        if info.get('factory'):
            key = (info['source_file'], info['factory'])
            factory_bindings.setdefault(key, []).append({
                'prefix': absolute[var],
                'local_bp': info['local_bp'],
            })
        for child in info['children']:
            walk(child, absolute[var])

    walk('rest_v2_blueprint', '')
    return absolute, factory_bindings


# --------------------------------------------------------------------
# Route discovery
# --------------------------------------------------------------------

def _find_module_blueprint(tree: ast.Module) -> str | None:
    """Return the last Blueprint(...) assignment in a module — v2
    modules define one primary blueprint per file, and routes hang
    off that variable."""
    last = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
            if fname == 'Blueprint':
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        last = target.id
    return last


def _extract_route_decorator(dec: ast.expr, module_bp: str | None) -> tuple[str, str] | None:
    """If `dec` is @bp.get('/x'), return ('get', '/x'). Otherwise None."""
    if not isinstance(dec, ast.Call):
        return None
    if not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    if module_bp and isinstance(dec.func.value, ast.Name) and dec.func.value.id != module_bp:
        return None
    if not dec.args or not isinstance(dec.args[0], ast.Constant):
        return None
    return method, dec.args[0].value


def _extract_api_doc(dec: ast.expr) -> dict[str, Any] | None:
    """If `dec` is @api_doc(...), return its kwargs dict."""
    if not isinstance(dec, ast.Call):
        return None
    fname = getattr(dec.func, 'id', None) or getattr(dec.func, 'attr', None)
    if fname != 'api_doc':
        return None
    kwargs: dict[str, Any] = {}
    for kw in dec.keywords:
        if kw.arg is None:
            continue
        kwargs[kw.arg] = _literal(kw.value)
    return kwargs


def _enclosing_factory(node: ast.AST, tree: ast.Module) -> str | None:
    """Return the name of the FunctionDef that lexically encloses
    `node`, or None if `node` is at module scope. Walks the tree
    building parent links on the fly; slow-but-simple.
    """
    for parent in ast.walk(tree):
        if not isinstance(parent, ast.FunctionDef):
            continue
        for child in ast.walk(parent):
            if child is node:
                return parent.name
    return None


def _discover_routes() -> list[dict[str, Any]]:
    prefix_map, factory_bindings = _build_prefix_map()
    routes: list[dict[str, Any]] = []

    for f in V2_ROOT.rglob('*.py'):
        try:
            tree = ast.parse(f.read_text(), filename=str(f))
        except SyntaxError as exc:
            print(f'openapi: skipping {f} ({exc})', file=sys.stderr)
            continue

        module_bp = _find_module_blueprint(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            route_info: tuple[str, str] | None = None
            apidoc: dict[str, Any] | None = None
            bp_var: str | None = None

            for dec in node.decorator_list:
                r = _extract_route_decorator(dec, module_bp=None)
                if r is not None:
                    route_info = r
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                            and isinstance(dec.func.value, ast.Name):
                        bp_var = dec.func.value.id
                d = _extract_api_doc(dec)
                if d is not None:
                    apidoc = d

            if route_info is None or apidoc is None:
                continue

            method, path = route_info

            # Factory-based route? If this function is lexically nested
            # inside a Blueprint factory that's been registered N times,
            # emit one route per registration with each site's resolved
            # prefix. The bp_var must match the factory's local
            # blueprint variable — a nested function that mutates some
            # other blueprint isn't a factory template.
            enclosing = _enclosing_factory(node, tree)
            bindings = factory_bindings.get((f.stem, enclosing)) if enclosing else None
            if bindings and any(b.get('local_bp') == bp_var for b in bindings):
                for binding in bindings:
                    routes.append({
                        'method': method,
                        'path': binding['prefix'] + path,
                        'view_name': node.name,
                        'docstring': ast.get_docstring(node),
                        'apidoc': apidoc,
                        'source': f'{f.relative_to(REPO_ROOT)}:{node.lineno}',
                    })
                continue

            prefix = prefix_map.get(bp_var or module_bp or '', '')
            if not prefix and (bp_var or module_bp):
                print(f'openapi: no prefix resolved for {bp_var or module_bp} in {f.name} '
                      f'(route {method.upper()} {path} will be emitted at root)', file=sys.stderr)

            routes.append({
                'method': method,
                'path': prefix + path,
                'view_name': node.name,
                'docstring': ast.get_docstring(node),
                'apidoc': apidoc,
                'source': f'{f.relative_to(REPO_ROOT)}:{node.lineno}',
            })

    return routes


# --------------------------------------------------------------------
# OpenAPI emission
# --------------------------------------------------------------------

def _envelope_paginated(item_schema_name: str) -> dict[str, Any]:
    return {
        'type': 'object',
        'properties': {
            'total': {'type': 'integer'},
            'data': {
                'type': 'array',
                'items': {'$ref': f'#/components/schemas/{item_schema_name}'},
            },
            'last_page': {'type': 'integer'},
            'current_page': {'type': 'integer'},
            'next_page': {'type': ['integer', 'null']},
        },
    }


def _shared_components() -> dict[str, Any]:
    return {
        'securitySchemes': {
            'ApiKey': {
                'type': 'apiKey',
                'in': 'header',
                'name': 'Authorization',
                'description': 'Bearer <api-key> — the key from your profile.',
            },
        },
        'responses': {
            'ApiError': {
                'description': 'Error',
                'content': {
                    'application/json': {
                        'schema': {
                            'type': 'object',
                            'properties': {
                                'message': {'type': 'string'},
                                'data': {},
                            },
                            'required': ['message'],
                        }
                    }
                },
            },
            'Forbidden': {'description': 'Insufficient permissions to access resource'},
            'NotFound': {'description': 'Resource not found'},
            'Deleted': {'description': 'Resource successfully deleted'},
            # Referenced by the MCP entrypoint operation; the JSON-RPC
            # endpoint rejects session-cookie-only callers with 401.
            'Unauthorized': {
                'description': 'Missing or invalid API key / Bearer token',
            },
        },
        'schemas': {},
    }


def _build_operation(
    route: dict[str, Any],
    schemas_seen: set[tuple[str, str, str]],
) -> dict[str, Any]:
    method: str = route['method']
    apidoc = route['apidoc']

    summary_doc, description = _split_docstring(route['docstring'])
    summary = apidoc.get('summary') or summary_doc or route['view_name']

    op: dict[str, Any] = {
        'operationId': f"{route['view_name']}_{method}",
        'summary': summary,
        'tags': apidoc.get('tags') or ['Uncategorized'],
        'security': [{'ApiKey': []}],
    }
    if description:
        op['description'] = description

    params = _path_params_from_flask(route['path'])
    params.extend(_query_params_from_apidoc(apidoc.get('query_params') or []))
    if params:
        op['parameters'] = params

    request_schema = apidoc.get('request')
    if isinstance(request_schema, _SchemaRef) and method in {'post', 'put', 'patch'}:
        base = _schema_component_name(request_schema.name)
        # Request bodies get their own component (dump_only fields
        # dropped, nullable=False non-default cols become required,
        # nested overlays dropped in favour of the FK).
        request_component = f'{base}Request'
        schemas_seen.add((request_component, request_schema.name, 'request'))
        op['requestBody'] = {
            'required': True,
            'content': {
                'application/json': {'schema': {'$ref': f'#/components/schemas/{request_component}'}}
            },
        }

    responses: dict[str, Any] = {}
    response_schema = apidoc.get('response')
    response_shape = apidoc.get('response_shape') or 'raw'
    response_status = str(apidoc.get('response_status') or 200)

    if response_shape == 'deleted':
        responses['204'] = {'$ref': '#/components/responses/Deleted'}
    elif isinstance(response_schema, _SchemaRef):
        name = _schema_component_name(response_schema.name)
        schemas_seen.add((name, response_schema.name, 'response'))
        if response_shape == 'paginated':
            schema_obj: dict[str, Any] = _envelope_paginated(name)
        else:
            schema_obj = {'$ref': f'#/components/schemas/{name}'}
        status = '201' if response_shape == 'created' else response_status
        responses[status] = {
            'description': 'Success',
            'content': {'application/json': {'schema': schema_obj}},
        }
    else:
        responses[response_status] = {'description': 'Success'}

    responses.setdefault('400', {'$ref': '#/components/responses/ApiError'})
    responses.setdefault('403', {'$ref': '#/components/responses/Forbidden'})
    if '<' in route['path']:
        responses.setdefault('404', {'$ref': '#/components/responses/NotFound'})

    op['responses'] = responses
    return op


_QUERY_TYPE_ALIASES = {
    'string': ('string', None),
    'str': ('string', None),
    'integer': ('integer', None),
    'int': ('integer', None),
    'number': ('number', None),
    'float': ('number', 'float'),
    'boolean': ('boolean', None),
    'bool': ('boolean', None),
    'date': ('string', 'date'),
    'date-time': ('string', 'date-time'),
    'datetime': ('string', 'date-time'),
    'uuid': ('string', 'uuid'),
}


def _query_type_to_schema(raw_type: str) -> dict[str, Any]:
    """Turn 'string' / 'integer[]' / 'date' into an OpenAPI schema dict."""
    is_array = raw_type.endswith('[]')
    base = raw_type[:-2] if is_array else raw_type
    base = base.lower().strip()
    kind, fmt = _QUERY_TYPE_ALIASES.get(base, (base or 'string', None))
    inner: dict[str, Any] = {'type': kind}
    if fmt is not None:
        inner['format'] = fmt
    return {'type': 'array', 'items': inner} if is_array else inner


def _query_params_from_apidoc(entries: list[Any]) -> list[dict[str, Any]]:
    """Convert @api_doc(query_params=[...]) tuples to OpenAPI parameters.

    Accepted tuple shapes (positional, no keyword args because ast can't
    reconstruct dict literals as terse):
        (name, type)
        (name, type, description)
        (name, type, description, required)

    Repeatable keys (e.g. `?tag=a&tag=b`) use 'string[]' etc. and get
    `style: form, explode: true`.
    """
    params: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        name = entry[0]
        raw_type = entry[1]
        description = entry[2] if len(entry) >= 3 else None
        required = bool(entry[3]) if len(entry) >= 4 else False

        if not isinstance(name, str) or not isinstance(raw_type, str):
            continue

        schema = _query_type_to_schema(raw_type)
        param: dict[str, Any] = {
            'name': name,
            'in': 'query',
            'required': required,
            'schema': schema,
        }
        if description:
            param['description'] = description
        if schema.get('type') == 'array':
            # Flask's request.args.getlist(...) matches OpenAPI's default
            # `form / explode=true`, but we emit it explicitly so clients
            # don't have to guess.
            param['style'] = 'form'
            param['explode'] = True
        params.append(param)
    return params


def _path_params_from_flask(rule: str) -> list[dict[str, Any]]:
    """Flask URL rule → OpenAPI parameter list.

    Reads the converter (`<int:foo>`, `<uuid:bar>`, `<path:baz>`) so the
    emitted `schema.type` matches what Flask actually validates against.
    Falls back to the same name-based heuristic used before for the
    converter-less `<name>` shape (bare names default to `string` in
    Flask; we upgrade to `integer` when the name is a well-known numeric
    id like `<foo_id>`, matching the codebase convention).
    """
    params: list[dict[str, Any]] = []
    for match in _PATH_PARAM_RE.finditer(rule):
        converter, name = match.group(1), match.group(2)
        if converter:
            kind, fmt = _FLASK_CONVERTER_TO_OPENAPI.get(
                converter, ('string', None)
            )
        else:
            # `<foo>` — no converter. Flask defaults to string, but the
            # codebase uses bare-name integer IDs in a handful of routes;
            # keep the pre-existing heuristic so those still document as
            # integers rather than regressing to string.
            kind = 'integer' if name.endswith('_id') or name == 'identifier' else 'string'
            fmt = None
        schema: dict[str, Any] = {'type': kind}
        if fmt is not None:
            schema['format'] = fmt
        params.append({
            'name': name,
            'in': 'path',
            'required': True,
            'schema': schema,
        })
    return params


# --------------------------------------------------------------------
# MCP surface discovery
# --------------------------------------------------------------------
#
# The MCP endpoint (`/api/v2/mcp`, defined in
# `app/blueprints/rest/v2/mcp/transport.py`) is a single JSON-RPC POST
# that multiplexes many "methods" (`tools/call`, `resources/read`, ...)
# internally. `_discover_routes` picks it up if it carries `@api_doc`
# but only as one anonymous POST — no per-tool visibility.
#
# We add a synthesised path per registered `@mcp_tool` / `@mcp_resource`
# decoration so Redoc can render every MCP tool alongside the REST
# routes. These synthesised entries are marked with `x-mcp: true` and
# grouped under the `MCP` tag; they are documentation only — actual
# dispatch happens through the single JSON-RPC POST.

MCP_TOOLS_DIR = REPO_ROOT / 'app' / 'blueprints' / 'rest' / 'v2' / 'mcp' / 'tools'
MCP_RESOURCES_FILE = REPO_ROOT / 'app' / 'blueprints' / 'rest' / 'v2' / 'mcp' / 'resources.py'
MCP_ENDPOINT_PATH = '/api/v2/mcp'


def _literal_dict(node: ast.AST) -> Any:
    """Safely evaluate a JSON-Schema-like AST literal to a Python value.

    Supports the subset used in MCP `input_schema=` declarations:
    dicts, lists, strings, ints, bools, None. Any node outside this
    subset returns None so the caller can decide whether that's a
    degraded-but-valid result or a hard error.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        out: dict[Any, Any] = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                # `{**x}` splat — skip; nothing in MCP schemas uses this.
                continue
            key = _literal_dict(k)
            out[key] = _literal_dict(v)
        return out
    if isinstance(node, ast.List):
        return [_literal_dict(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal_dict(e) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_literal_dict(e) for e in node.elts}
    # Common at-schema-declaration idiom is `{**PAGINATION_SCHEMA_FRAGMENT,
    # 'foo': {...}}` — the dict merge is handled by the caller (below)
    # using a manual walk that stitches in the fragment before this
    # function runs. Anything else that isn't a bare literal we don't
    # try to eval — leave it as `None` so the operation still emits.
    return None


def _resolve_dict_with_fragments(
    node: ast.Dict,
    fragments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate a dict-literal node, expanding `{**FRAGMENT_NAME, ...}`
    splats using a lookup table of module-level fragment constants.

    Used for MCP input schemas which commonly splat
    `PAGINATION_SCHEMA_FRAGMENT` (from tools/_common.py) into their
    `properties`. The generator's static AST evaluator can't see the
    fragment values, so callers pass in a pre-computed lookup.
    """
    out: dict[str, Any] = {}
    for k, v in zip(node.keys, node.values):
        if k is None and isinstance(v, ast.Name) and v.id in fragments:
            # `{**FRAGMENT, ...}` — merge.
            out.update(fragments[v.id])
            continue
        if k is None:
            # Splat of a non-literal expression — skip.
            continue
        key = _literal_dict(k)
        if isinstance(v, ast.Dict):
            out[key] = _resolve_dict_with_fragments(v, fragments)
        else:
            out[key] = _literal_dict(v)
    return out


def _collect_module_fragments(tree: ast.Module) -> dict[str, dict[str, Any]]:
    """Harvest module-level `NAME = {...}` dict assignments as fragments.

    We use this to resolve `{**PAGINATION_SCHEMA_FRAGMENT, ...}` splats
    inside tool `input_schema=` declarations. Only bare dict literals
    are captured; anything else is silently ignored (the fragment lookup
    just misses and the property is dropped from the emitted schema).
    """
    out: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = _resolve_dict_with_fragments(node.value, {})
    return out


def _mcp_decorator_kwargs(dec: ast.expr) -> dict[str, Any] | None:
    """Return kwargs dict for a `@mcp_tool(...)` / `@mcp_resource(...)`
    decorator, or None if the decorator doesn't match."""
    if not isinstance(dec, ast.Call):
        return None
    fname = getattr(dec.func, 'id', None) or getattr(dec.func, 'attr', None)
    if fname not in ('mcp_tool', 'mcp_resource'):
        return None
    kwargs: dict[str, Any] = {'__decorator__': fname}
    for kw in dec.keywords:
        if kw.arg is None:
            continue
        kwargs[kw.arg] = kw.value  # raw ast — resolved by the caller.
    return kwargs


def _load_mcp_common_fragments() -> dict[str, dict[str, Any]]:
    """Load the fragment table from tools/_common.py.

    `PAGINATION_SCHEMA_FRAGMENT` is the only fragment used by MVP tools
    today; keeping this generic in case more get added.
    """
    common_file = MCP_TOOLS_DIR / '_common.py'
    if not common_file.exists():
        return {}
    tree = ast.parse(common_file.read_text(), filename=str(common_file))
    return _collect_module_fragments(tree)


def _discover_mcp_tools() -> list[dict[str, Any]]:
    """Parse tools/*.py and return one dict per registered @mcp_tool."""
    if not MCP_TOOLS_DIR.exists():
        return []
    fragments = _load_mcp_common_fragments()

    tools: list[dict[str, Any]] = []
    for f in sorted(MCP_TOOLS_DIR.glob('*.py')):
        if f.name.startswith('_'):
            continue
        try:
            tree = ast.parse(f.read_text(), filename=str(f))
        except SyntaxError as exc:
            print(f'openapi/mcp: skipping {f} ({exc})', file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                kw = _mcp_decorator_kwargs(dec)
                if kw is None or kw['__decorator__'] != 'mcp_tool':
                    continue
                # Resolve the raw ast nodes into Python values.
                name_node = kw.get('name')
                if not isinstance(name_node, ast.Constant):
                    continue  # tool name must be a string literal
                name = name_node.value
                desc_node = kw.get('description')
                description = (
                    desc_node.value if isinstance(desc_node, ast.Constant) else ''
                )
                schema_node = kw.get('input_schema')
                if isinstance(schema_node, ast.Dict):
                    schema = _resolve_dict_with_fragments(schema_node, fragments)
                elif schema_node is None:
                    # `input_schema` omitted — decorator supplies the default
                    # empty-object schema at runtime.
                    schema = {'type': 'object', 'properties': {}}
                else:
                    # Non-literal expression (e.g. reference to a module
                    # constant not in the fragment table). Fall back to
                    # an empty shape so the operation still emits.
                    schema = {'type': 'object', 'properties': {}}
                if not isinstance(schema, dict):
                    schema = {'type': 'object', 'properties': {}}
                # Augment with the scoping fields dispatch injects at
                # runtime so the OpenAPI shape matches what a caller
                # actually sees on `tools/list`.
                case_scoped = _kw_flag(kw, 'case_scoped')
                war_room_scoped = _kw_flag(kw, 'war_room_scoped')
                admin_only = _kw_flag(kw, 'admin_only')
                mvp = _kw_flag(kw, 'mvp')
                raw_props = schema.get('properties') or {}
                props = dict(raw_props) if isinstance(raw_props, dict) else {}
                raw_required = schema.get('required') or []
                required = list(raw_required) if isinstance(raw_required, (list, tuple)) else []
                if case_scoped and 'case_identifier' not in props:
                    props['case_identifier'] = {
                        'type': 'integer',
                        'description': 'Numeric ID of the IRIS case this call targets.',
                    }
                    if 'case_identifier' not in required:
                        required.append('case_identifier')
                if war_room_scoped and 'war_room_id' not in props:
                    props['war_room_id'] = {
                        'type': 'integer',
                        'description': 'Numeric ID of the war room this call targets.',
                    }
                    if 'war_room_id' not in required:
                        required.append('war_room_id')
                schema['properties'] = props
                if required:
                    schema['required'] = required

                tools.append({
                    'name': name,
                    'description': description,
                    'input_schema': schema,
                    'admin_only': admin_only,
                    'case_scoped': case_scoped,
                    'war_room_scoped': war_room_scoped,
                    'mvp': mvp,
                    'source_file': str(f.relative_to(REPO_ROOT.parent)),
                    'view_name': node.name,
                })
    tools.sort(key=lambda t: t['name'])
    return tools


def _kw_flag(kw: dict[str, Any], name: str) -> bool:
    node = kw.get(name)
    return isinstance(node, ast.Constant) and bool(node.value)


def _discover_mcp_resources() -> list[dict[str, Any]]:
    """Parse resources.py and return one dict per registered @mcp_resource."""
    if not MCP_RESOURCES_FILE.exists():
        return []
    tree = ast.parse(MCP_RESOURCES_FILE.read_text(),
                     filename=str(MCP_RESOURCES_FILE))
    out: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            kw = _mcp_decorator_kwargs(dec)
            if kw is None or kw['__decorator__'] != 'mcp_resource':
                continue
            uri_node = kw.get('uri_template')
            name_node = kw.get('name')
            desc_node = kw.get('description')
            if not isinstance(uri_node, ast.Constant):
                continue
            out.append({
                'uri_template': uri_node.value,
                'name': name_node.value if isinstance(name_node, ast.Constant) else '',
                'description': (
                    desc_node.value if isinstance(desc_node, ast.Constant) else ''
                ),
            })
    out.sort(key=lambda r: r['uri_template'])
    return out


def _build_mcp_operations(
    tools: list[dict[str, Any]],
    resources: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Turn discovered tools + resources into (path, method, op) triples.

    Emits three families of paths under `/api/v2/mcp/…`:
      * The raw JSON-RPC POST /api/v2/mcp — one operation with a
        JSON-RPC-shaped requestBody.
      * POST /api/v2/mcp/tools/{name} — one per tool, with its declared
        `input_schema` inline as the requestBody schema.
      * GET  /api/v2/mcp/resources — a single operation listing every
        registered resource template as an example.

    All entries carry `x-mcp: true` and tag `MCP` so the doc UI can
    group them.
    """
    out: list[tuple[str, str, dict[str, Any]]] = []

    # Raw JSON-RPC POST — canonical entrypoint.
    out.append((MCP_ENDPOINT_PATH, 'post', {
        'operationId': 'mcp_jsonrpc_post',
        'summary': 'MCP JSON-RPC endpoint (single POST, multiplexes every MCP method)',
        'description': (
            'Streamable-HTTP MCP transport. Send a JSON-RPC 2.0 request '
            'or batch; methods include `initialize`, `tools/list`, '
            '`tools/call`, `resources/list`, `resources/templates/list`, '
            '`resources/read`, `ping`. Authenticate with X-IRIS-AUTH '
            'or Authorization: Bearer. See the individual tool operations '
            'under the `MCP` tag for a per-tool schema view.'
        ),
        'tags': ['MCP'],
        'security': [{'ApiKey': []}],
        'x-mcp': True,
        'requestBody': {
            'required': True,
            'content': {
                'application/json': {
                    'schema': {'$ref': '#/components/schemas/McpJsonRpcRequest'},
                },
            },
        },
        'responses': {
            '200': {
                'description': 'JSON-RPC response (result or error).',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/McpJsonRpcResponse'},
                    },
                },
            },
            '202': {
                'description': 'Accepted (notification — no body).',
            },
            '401': {'$ref': '#/components/responses/Unauthorized'},
            '503': {'$ref': '#/components/responses/ApiError'},
        },
    }))

    # Per-tool synthesised operations.
    for tool in tools:
        path = f'{MCP_ENDPOINT_PATH}/tools/{tool["name"]}'
        op: dict[str, Any] = {
            'operationId': f'mcp_tool_{tool["name"]}',
            'summary': tool['description'] or f'MCP tool {tool["name"]}',
            'tags': ['MCP', 'MCP:admin' if tool['admin_only'] else 'MCP:tools'],
            'security': [{'ApiKey': []}],
            'x-mcp': True,
            'x-mcp-tool': tool['name'],
            'x-mcp-mvp': tool['mvp'],
            'x-mcp-admin-only': tool['admin_only'],
            'description': (
                'Synthesised documentation entry — MCP tools are actually '
                f'dispatched via a single POST {MCP_ENDPOINT_PATH} '
                'with `method: "tools/call"` and `params.name = '
                f'"{tool["name"]}"`. The request body schema below is the '
                'tool\'s `input_schema` (identical to what `tools/list` '
                'returns for this tool).'
            ),
            'requestBody': {
                'required': True,
                'content': {
                    'application/json': {'schema': tool['input_schema']},
                },
            },
            'responses': {
                '200': {
                    'description': 'Tool result — JSON-RPC content wrapper.',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/McpToolResult'},
                        },
                    },
                },
                '403': {'$ref': '#/components/responses/Forbidden'},
            },
        }
        out.append((path, 'post', op))

    # Resources — a single GET operation whose response enumerates every
    # template. Redoc renders the `examples` block inline.
    resource_examples: dict[str, dict[str, Any]] = {}
    for res in resources:
        resource_examples[res['name']] = {
            'summary': res['description'] or res['name'],
            'value': {'uri': res['uri_template']},
        }
    out.append((f'{MCP_ENDPOINT_PATH}/resources', 'get', {
        'operationId': 'mcp_resources_list',
        'summary': 'List MCP resource templates',
        'description': (
            'Synthesised documentation entry — the runtime call is '
            f'POST {MCP_ENDPOINT_PATH} with '
            '`method: "resources/templates/list"`. Each template can be '
            f'read via `method: "resources/read"` + `params.uri = <template>` '
            'with the placeholder(s) filled in.'
        ),
        'tags': ['MCP', 'MCP:resources'],
        'security': [{'ApiKey': []}],
        'x-mcp': True,
        'responses': {
            '200': {
                'description': 'Registered resource templates.',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/McpResourceTemplateList'},
                        'examples': resource_examples or None,
                    },
                },
            },
        },
    }))

    return out


def _mcp_component_schemas() -> dict[str, dict[str, Any]]:
    """Shared components referenced by the synthesised MCP paths."""
    return {
        'McpJsonRpcRequest': {
            'type': 'object',
            'description': (
                'JSON-RPC 2.0 request or batch. `method` selects the MCP '
                'operation; `params` carries method-specific arguments.'
            ),
            'properties': {
                'jsonrpc': {'type': 'string', 'enum': ['2.0']},
                'id': {
                    'oneOf': [
                        {'type': 'integer'},
                        {'type': 'string'},
                        {'type': 'null'},
                    ],
                    'description': 'Request id — omit for notifications.',
                },
                'method': {'type': 'string'},
                'params': {'type': 'object'},
            },
            'required': ['jsonrpc', 'method'],
        },
        'McpJsonRpcResponse': {
            'type': 'object',
            'properties': {
                'jsonrpc': {'type': 'string', 'enum': ['2.0']},
                'id': {
                    'oneOf': [{'type': 'integer'}, {'type': 'string'}, {'type': 'null'}],
                },
                'result': {'type': 'object'},
                'error': {
                    'type': 'object',
                    'properties': {
                        'code': {'type': 'integer'},
                        'message': {'type': 'string'},
                        'data': {},
                    },
                    'required': ['code', 'message'],
                },
            },
            'required': ['jsonrpc'],
        },
        'McpToolResult': {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'type': {'type': 'string', 'enum': ['text']},
                            'text': {'type': 'string'},
                        },
                        'required': ['type', 'text'],
                    },
                },
            },
            'required': ['content'],
        },
        'McpResourceTemplateList': {
            'type': 'object',
            'properties': {
                'resourceTemplates': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'uriTemplate': {'type': 'string'},
                            'name': {'type': 'string'},
                            'description': {'type': 'string'},
                            'mimeType': {'type': 'string'},
                        },
                        'required': ['uriTemplate', 'name'],
                    },
                },
            },
            'required': ['resourceTemplates'],
        },
    }


def generate() -> dict[str, Any]:
    routes = _discover_routes()
    routes.sort(key=lambda r: (r['path'], r['method']))

    paths: dict[str, dict[str, Any]] = {}
    schemas_seen: set[tuple[str, str, str]] = set()

    for route in routes:
        openapi_path = _flask_path_to_openapi(route['path'])
        paths.setdefault(openapi_path, {})
        paths[openapi_path][route['method']] = _build_operation(route, schemas_seen)

    # ------------------------------------------------------------------
    # MCP surface — synthesised paths, one per registered @mcp_tool /
    # @mcp_resource. The runtime dispatch is still the single JSON-RPC
    # POST /api/v2/mcp entry (also emitted below); these synthesised
    # entries exist so Redoc renders every MCP tool inline alongside
    # the REST endpoints.
    # ------------------------------------------------------------------
    mcp_tools = _discover_mcp_tools()
    mcp_resources = _discover_mcp_resources()
    for path, method, op in _build_mcp_operations(mcp_tools, mcp_resources):
        paths.setdefault(path, {})
        paths[path][method] = op

    components = _shared_components()
    ast_index = _build_schema_ast_index()

    # Transitively resolve nested-schema references. Only the schemas
    # directly named by routes appear in `schemas_seen`; follow every
    # $ref inside them until the closure is stable so consumers see a
    # self-contained components.schemas map.
    #
    # We parse per (component, source_cls, mode) lazily and cache the
    # result. Emission order is deterministic (alphabetical by
    # component) so the CI drift check stays byte-stable.
    resolved: dict[tuple[str, str, str], dict[str, Any]] = {}
    pending = list(schemas_seen)
    while pending:
        component, source_cls, mode = pending.pop()
        key = (component, source_cls, mode)
        if key in resolved:
            continue
        cls_node = ast_index.get(source_cls)
        if cls_node is None:
            resolved[key] = {
                'type': 'object',
                'description': f'Auto-generated placeholder — see marshables.py::{source_cls}.',
            }
            continue
        cls_schema = _parse_schema_class(cls_node, schemas_seen, mode=mode)
        resolved[key] = cls_schema
        for ref in _iter_refs(cls_schema):
            target = ref.rsplit('/', 1)[-1]
            source = target if target in ast_index else f'{target}Schema'
            # Nested refs are always response-mode (see
            # _resolve_field_schema's mode='response' hard-coding).
            pending.append((target, source, 'response'))

    for (component, _source_cls, _mode) in sorted(resolved):
        components['schemas'][component] = resolved[(component, _source_cls, _mode)]

    # MCP-specific components (JSON-RPC envelopes, tool result wrapper,
    # resource template list). Added after resolution so alphabetical
    # sort places them among the Mc… slice deterministically.
    for name, schema in sorted(_mcp_component_schemas().items()):
        components['schemas'][name] = schema

    spec: dict[str, Any] = {
        'openapi': '3.1.0',
        'info': {
            'title': 'IRIS',
            'version': '2.1.0',
            'description': 'IRIS REST API — automatically generated from the Flask backend.',
            'contact': {'name': 'DFIR-IRIS', 'email': 'contact@dfir-iris.org'},
            'license': {'name': 'LGPLv3', 'url': 'https://www.gnu.org/licenses/lgpl-3.0.txt'},
        },
        'security': [{'ApiKey': []}],
        'paths': paths,
        'components': components,
    }

    print(f'openapi: documented {len(routes)} routes', file=sys.stderr)
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Path to write the YAML spec to.')
    args = parser.parse_args()

    spec = generate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(spec, sort_keys=False, width=100))
    print(f'openapi: wrote {args.output}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
