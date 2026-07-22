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

_PATH_PARAM_RE = re.compile(r'<(?:[^:>]+:)?([^>]+)>')
_HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def _flask_path_to_openapi(rule: str) -> str:
    return _PATH_PARAM_RE.sub(r'{\1}', rule)


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
# Blueprint prefix resolution
# --------------------------------------------------------------------

def _parse_blueprints(path: Path) -> dict[str, dict[str, Any]]:
    """Extract Blueprint(...) assignments + register_blueprint() edges.

    Returns {variable_name: {'prefix': str, 'children': [child_var]}}
    limited to the current module. Callers combine per-module data
    into a full prefix map.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    bps: dict[str, dict[str, Any]] = {}

    for node in ast.walk(tree):
        # `foo = Blueprint('name', __name__, url_prefix='/x')`
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
            if fname == 'Blueprint':
                prefix = ''
                for kw in call.keywords:
                    if kw.arg == 'url_prefix' and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value or ''
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bps.setdefault(target.id, {'prefix': prefix, 'children': []})

        # `foo.register_blueprint(bar)`
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == 'register_blueprint'
                and isinstance(call.func.value, ast.Name)
                and call.args
                and isinstance(call.args[0], ast.Name)
            ):
                parent = call.func.value.id
                child = call.args[0].id
                bps.setdefault(parent, {'prefix': '', 'children': []})
                bps[parent]['children'].append(child)

    return bps


def _build_prefix_map() -> dict[str, str]:
    """Walk the api_v2_routes registration tree and every child module
    to compute the absolute URL prefix per blueprint variable.

    We use the variable name as the identity key. Duplicate names
    across modules would collide — the v2 codebase already avoids
    that (each blueprint is a globally-unique variable).
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
                if info['prefix']:
                    existing['prefix'] = info['prefix']
                existing['children'].extend(info['children'])
        except SyntaxError as exc:
            print(f'openapi: skipping {f} ({exc})', file=sys.stderr)

    # Traverse from the root, accumulating prefixes.
    absolute: dict[str, str] = {}

    def walk(var: str, parent_prefix: str) -> None:
        if var in absolute:
            return
        info = all_bps.get(var)
        if info is None:
            return
        absolute[var] = parent_prefix + info['prefix']
        for child in info['children']:
            walk(child, absolute[var])

    walk('rest_v2_blueprint', '')
    return absolute


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


def _discover_routes() -> list[dict[str, Any]]:
    prefix_map = _build_prefix_map()
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
        },
        'schemas': {},
    }


def _build_operation(route: dict[str, Any], schemas_seen: set[tuple[str, str]]) -> dict[str, Any]:
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

    params = _path_params_from_openapi(_flask_path_to_openapi(route['path']))
    if params:
        op['parameters'] = params

    request_schema = apidoc.get('request')
    if isinstance(request_schema, _SchemaRef) and method in {'post', 'put', 'patch'}:
        name = _schema_component_name(request_schema.name)
        schemas_seen.add((name, request_schema.name))
        op['requestBody'] = {
            'required': True,
            'content': {
                'application/json': {'schema': {'$ref': f'#/components/schemas/{name}'}}
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
        schemas_seen.add((name, response_schema.name))
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


def _path_params_from_openapi(path: str) -> list[dict[str, Any]]:
    """OpenAPI-style `/x/{name}` → parameter list. We can't recover the
    original Flask converter, so we default to string; annotate as
    integer when the name is a well-known numeric id."""
    params: list[dict[str, Any]] = []
    for match in re.finditer(r'\{([^}]+)\}', path):
        name = match.group(1)
        openapi_type = 'integer' if name.endswith('_id') or name == 'identifier' else 'string'
        params.append({
            'name': name,
            'in': 'path',
            'required': True,
            'schema': {'type': openapi_type},
        })
    return params


def generate() -> dict[str, Any]:
    routes = _discover_routes()
    routes.sort(key=lambda r: (r['path'], r['method']))

    paths: dict[str, dict[str, Any]] = {}
    schemas_seen: set[tuple[str, str]] = set()

    for route in routes:
        openapi_path = _flask_path_to_openapi(route['path'])
        paths.setdefault(openapi_path, {})
        paths[openapi_path][route['method']] = _build_operation(route, schemas_seen)

    components = _shared_components()
    # Emit placeholder schema components so the spec validates even if
    # no hand-written schema fragment is bundled in yet.
    for name, source_cls in sorted(schemas_seen):
        components['schemas'][name] = {
            'type': 'object',
            'description': f'Auto-generated placeholder — see marshables.py::{source_cls}.',
        }

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
