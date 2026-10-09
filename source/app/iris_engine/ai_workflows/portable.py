#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Portable JSON documents: workflows and saved blocks as files.

A document carries the definition and what it needs on the importing
instance (keystore entry names, tools) — never a secret. Secrets live in
the keystore and a definition only references them with `key("NAME")`;
the export replaces a literal credential a definition still holds with
a reference to a keystore entry to create, and says so in `warnings`:
in an HTTP request, an `auth_secret`, a secret or credential-named header
or query parameter, URL credentials; in any string of any node, a
recognisable token (`AKIA…`, `ghp_…`, a JWT, a private key…), a quoted
`"password": "…"` style assignment or a `?apikey=…` query. String
constants inside template expressions are scanned too. Neither the owner,
the customer scope, the inbound token nor the signing secret of a
workflow are exported, and the trigger configuration keeps only the keys
the trigger types read: they belong to the instance.

Pure functions: the business layer reads the database and validates.
"""

import copy
import re

FORMAT_WORKFLOW = 'iris-ai-workflow'
FORMAT_BLOCK = 'iris-ai-workflow-block'
FORMAT_VERSION = 1

# Workflow fields a document carries
WORKFLOW_FIELDS = ('name', 'description', 'trigger_type', 'trigger_config', 'graph', 'write_tool_allowlist',
                   'max_runs_per_hour', 'token_budget_per_run', 'suggestion_audience')
BLOCK_FIELDS = ('name', 'description', 'category', 'nodes', 'edges')

# Keys the trigger types read (`graph._*_config_errors`, `triggers`)
TRIGGER_CONFIG_FIELDS = ('hooks', 'condition', 'dedup_minutes', 'skip_if_active', 'cron', 'target', 'max_targets',
                         'entity_types', 'require_signature', 'entity_type', 'entity_id_path')

_KEY_CALL = re.compile(r'''\bkey\(\s*(['"])([^'"]{1,128})\1\s*\)''')
_TEMPLATE_CLOSERS = {'{{': '}}', '{%': '%}', '{#': '#}'}
_TEMPLATE_OPENER = re.compile(r'\{[{%#]')
_STRING_CONSTANT = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_CREDENTIAL_NAME = re.compile(r'auth|key|token|secret|pass|pwd|cookie|session|signature|\bsig\b|credential|bearer|'
                              r'private', re.IGNORECASE)
_TOKEN_LIKE = re.compile(r'[A-Za-z0-9_\-+/=.:~]{12,}')
_AUTH_SCHEMES = re.compile(r'^(bearer|basic|token|apikey|api-key|key|sso-key)$', re.IGNORECASE)
_KEY_NAME_CHARS = re.compile(r'[^A-Z0-9_]+')
_URL_USERINFO = re.compile(r'://([^/?#@\s{}"\'<>]+)@')
# Starts only where a token can start, so each run is scanned once
_TOKEN_START = r'(?<![A-Za-z0-9_\-])'
_KNOWN_TOKEN = re.compile(
    _TOKEN_START + r'(?:A[KS]IA[0-9A-Z]{16}'
    r'|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
    r'|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_\-]{20,}'
    r'|xox[abprse]-[A-Za-z0-9\-]{10,}|glpat-[A-Za-z0-9_\-]{20,}'
    r'|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})'
    r'|-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----[A-Za-z0-9+/=\\\s]{0,20000}'
    r'(?:-----END [A-Z ]{0,40}PRIVATE KEY-----)?')
# `"password": "…"`, `api_key = '…'`: a quoted literal value
_QUOTED_ASSIGNMENT = re.compile(
    r'''(?<![A-Za-z0-9])(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?secret|'''
    r'''secret|password|passwd|pwd|token|bearer)["']?[ \t]{0,8}[=:][ \t]{0,8}(["'])([^"'\s{}]{6,})\1''',
    re.IGNORECASE)
# Prose: `API key x9Y8…`, `token: Zm9v…` (letters and digits, not code)
_PROSE_CREDENTIAL = re.compile(
    r'''(?<![A-Za-z0-9])(?:api[ _-]?key|apikey|token|password|passwd|pwd|secret|bearer)[\s:=]{1,3}'''
    r'''(?:is[ \t]{1,4})?([A-Za-z0-9_\-+/=.~]{8,})''',
    re.IGNORECASE)
_LETTER = re.compile(r'[A-Za-z]')
_DIGIT = re.compile(r'[0-9]')
# `?apikey=…`, `&token=…`
_QUERY_CREDENTIAL = re.compile(
    r'''[?&](?:api[_-]?key|apikey|key|access[_-]?token|token|secret|client[_-]?secret|password|passwd|pwd|sig|'''
    r'''signature|auth)=([^&#\s{}"'<>]{4,})''',
    re.IGNORECASE)


class PortableError(ValueError):
    pass


def ai_workflows_portable_key_references(value) -> list:
    """Keystore names referenced with `key("NAME")` anywhere in `value`."""
    names = set()

    def walk(item):
        if isinstance(item, str):
            names.update(match.group(2) for match in _KEY_CALL.finditer(item))
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return sorted(names)


def ai_workflows_portable_tools(nodes, allowlist=()) -> list:
    """Tool names the nodes call or propose, plus the allowlist."""
    tools = {t for t in allowlist or [] if isinstance(t, str)}
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        config = node.get('config') if isinstance(node.get('config'), dict) else {}
        node_type = node.get('type')
        if node_type == 'action' and isinstance(config.get('tool'), str):
            tools.add(config['tool'])
        elif node_type == 'ai_agent' and isinstance(config.get('tools'), list):
            tools.update(t for t in config['tools'] if isinstance(t, str))
        elif node_type == 'suggest' and isinstance(config.get('proposed_action'), dict):
            tool = config['proposed_action'].get('tool')
            if isinstance(tool, str):
                tools.add(tool)
    return sorted(t for t in tools if t)


def _template_spans(value) -> list:
    """`[(start, end)]` of the template tags of `value` (an unclosed tag
    runs to the end). Linear: one `find` per tag."""
    spans = []
    position = 0
    while True:
        opener = _TEMPLATE_OPENER.search(value, position)
        if opener is None:
            return spans
        end = value.find(_TEMPLATE_CLOSERS[opener.group()], opener.end())
        end = len(value) if end < 0 else end + 2
        spans.append((opener.start(), end))
        position = end


def _literal(value) -> str:
    """`value` without its template tags, but with the string constants
    of its expressions (`{{ "abc" }}` holds `abc`), but `key()` names."""
    value = value or ''
    parts = []
    position = 0
    for start, end in _template_spans(value):
        parts.append(value[position:start])
        tag = value[start:end]
        if not tag.startswith('{#'):
            tag = _KEY_CALL.sub(' ', tag)
            parts.extend(m.group(1) if m.group(1) is not None else m.group(2)
                         for m in _STRING_CONSTANT.finditer(tag))
        position = end
    parts.append(value[position:])
    return ' '.join(parts)


def _holds_secret(value, always) -> bool:
    """Whether a field value holds a literal credential. `always`: the
    field is a secret (anything literal but an auth scheme word is one);
    otherwise a long token-like literal is."""
    if not isinstance(value, str) or not value.strip():
        return False
    literal = _literal(value)
    if always:
        words = [w for w in re.split(r'[\s:]+', literal) if w]
        return any(not _AUTH_SCHEMES.match(w) for w in words)
    return bool(_TOKEN_LIKE.search(literal))


def _placeholder_name(node_id, field) -> str:
    name = _KEY_NAME_CHARS.sub('_', f'{node_id}_{field}'.upper()).strip('_')
    return (name or 'SECRET')[:64]


def _warn(warnings, node, field, name):
    warnings.append({'node_id': node.get('id'), 'field': field,
                     'message': f'A literal secret was replaced by key("{name}"): create that keystore '
                                'entry on the importing instance'})


def _secret_matches(value, prose) -> list:
    """`[(start, end)]` of the recognisable secrets in `value`; `prose`:
    also a credential word followed by a letters-and-digits token."""
    spans = [m.span() for m in _KNOWN_TOKEN.finditer(value)]
    if prose:
        spans.extend(m.span(1) for m in _PROSE_CREDENTIAL.finditer(value)
                     if _LETTER.search(m.group(1)) and _DIGIT.search(m.group(1)))
    spans.extend(m.span(2) for m in _QUOTED_ASSIGNMENT.finditer(value))
    spans.extend(m.span(1) for m in _QUERY_CREDENTIAL.finditer(value))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _scrub_string(value, node, field, warnings, templated, prose=True):
    """`value` without URL credentials and recognisable secrets. A secret
    outside template tags of a templated field becomes `{{ key() }}`;
    elsewhere (inside an expression, in code, a label) the
    `[secret:NAME]` a non-HTTP node renders `key()` as."""
    if _URL_USERINFO.search(value):
        warnings.append({'node_id': node.get('id'), 'field': field,
                         'message': 'Credentials in a URL were removed'})
        value = _URL_USERINFO.sub('://', value)
    matches = _secret_matches(value, prose)
    if not matches:
        return value
    tags = _template_spans(value) if templated else []
    node_id = str(node.get('id') or 'node')
    parts = []
    position = 0
    for index, (start, end) in enumerate(matches):
        name = _placeholder_name(node_id, field if len(matches) == 1 else f'{field}_{index}')
        _warn(warnings, node, field, name)
        inside = any(s <= start < e for s, e in tags)
        parts.append(value[position:start])
        parts.append(f'{{{{ key("{name}") }}}}' if templated and not inside else f'[secret:{name}]')
        position = end
    parts.append(value[position:])
    return ''.join(parts)


def _credential_entry(name, value) -> bool:
    """A credential-named key holding a literal string value."""
    return isinstance(name, str) and isinstance(value, str) and bool(_CREDENTIAL_NAME.search(name)) \
        and _holds_secret(value, always=True)


def _scrub_value(value, node, field, warnings, templated, depth=0):
    """Every string of a config value, recursively; a credential-named
    entry (`{"password": "…"}`, `{"name": "token", "value": "…"}`)
    holding a literal becomes a reference."""
    if isinstance(value, str):
        return _scrub_string(value, node, field, warnings, templated, prose=templated)
    if depth > 64:
        return value
    if isinstance(value, list):
        return [_scrub_value(item, node, f'{field}.{index}', warnings, templated, depth + 1)
                for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    named = value.get('name') if 'value' in value else None
    scrubbed = {}
    for key, item in value.items():
        path = f'{field}.{key}'
        credential = _credential_entry(named, item) if key == 'value' else \
            depth > 0 and _credential_entry(key, item)
        if credential:
            name = _placeholder_name(str(node.get('id') or 'node'), path)
            _warn(warnings, node, path, name)
            scrubbed[key] = f'{{{{ key("{name}") }}}}' if templated else f'[secret:{name}]'
        else:
            scrubbed[key] = _scrub_value(item, node, path, warnings, templated, depth + 1)
    return scrubbed


# Config keys that are not Jinja templates
_UNTEMPLATED = ('code',)


def _scrub_any_node(node, warnings):
    """Recognisable secrets in every string of the node."""
    if isinstance(node.get('label'), str):
        node['label'] = _scrub_string(node['label'], node, 'label', warnings, templated=False, prose=True)
    config = node.get('config')
    if not isinstance(config, dict):
        return
    for key in list(config):
        if node.get('type') == 'http_request' and key in ('auth_secret', 'auth_username', 'headers',
                                                          'query_params'):
            continue
        config[key] = _scrub_value(config[key], node, key, warnings, templated=key not in _UNTEMPLATED,
                                   depth=1 if key in ('arguments', 'variables', 'inputs') else 0)


def _scrub_node(node, warnings):
    if not isinstance(node, dict):
        return node
    if node.get('type') == 'http_request' and isinstance(node.get('config'), dict):
        _scrub_http(node, warnings)
    _scrub_any_node(node, warnings)
    return node


def _scrub_http(node, warnings):
    config = node['config']
    node_id = str(node.get('id') or 'node')

    def replace(field, current):
        name = _placeholder_name(node_id, field)
        _warn(warnings, node, field, name)
        prefix = ''
        words = [w for w in re.split(r'\s+', _literal(current).strip()) if w]
        if words and _AUTH_SCHEMES.match(words[0]) and len(words) > 1:
            prefix = f'{words[0]} '
        return f'{prefix}{{{{ key("{name}") }}}}'

    auth_type = config.get('auth_type') or 'none'
    if _holds_secret(config.get('auth_secret'), always=True):
        config['auth_secret'] = replace('auth_secret', config['auth_secret'])
    elif auth_type == 'none' and config.get('auth_secret'):
        config['auth_secret'] = ''
    if _holds_secret(config.get('auth_username'), always=False):
        config['auth_username'] = replace('auth_username', config['auth_username'])
    for key in ('headers', 'query_params'):
        entries = config.get(key)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            credential = bool(entry.get('secret')) or bool(_CREDENTIAL_NAME.search(str(entry.get('name') or '')))
            if credential and _holds_secret(entry.get('value'), always=True):
                entry['value'] = replace(f'{key}.{index}', entry.get('value'))
            elif isinstance(entry.get('value'), str):
                entry['value'] = _scrub_string(entry['value'], node, f'{key}.{index}', warnings, templated=True)


def ai_workflows_portable_scrub_nodes(nodes) -> tuple:
    """`(nodes, warnings)`: a copy of the nodes without literal secrets."""
    nodes = copy.deepcopy(nodes if isinstance(nodes, list) else [])
    warnings = []
    for node in nodes:
        _scrub_node(node, warnings)
    return nodes, warnings


def ai_workflows_portable_literal_secrets(nodes) -> list:
    """The `[{node_id, field, message}]` a scrub would replace."""
    return ai_workflows_portable_scrub_nodes(nodes)[1]


def _requirements(nodes, allowlist=()) -> dict:
    return {
        'keystore': ai_workflows_portable_key_references(nodes),
        'tools': ai_workflows_portable_tools(nodes, allowlist),
    }


def ai_workflows_portable_export_workflow(definition, exported_at=None, instance_version=None) -> dict:
    """The document of a workflow definition (`_definition` of the
    business layer); keeps only `WORKFLOW_FIELDS`."""
    workflow = {field: copy.deepcopy(definition.get(field)) for field in WORKFLOW_FIELDS}
    graph = workflow.get('graph') if isinstance(workflow.get('graph'), dict) else {'nodes': [], 'edges': []}
    nodes, warnings = ai_workflows_portable_scrub_nodes(graph.get('nodes'))
    workflow['graph'] = {'nodes': nodes, 'edges': copy.deepcopy(graph.get('edges') or [])}
    trigger_config = workflow.get('trigger_config')
    if isinstance(trigger_config, dict):
        workflow['trigger_config'] = {k: v for k, v in trigger_config.items() if k in TRIGGER_CONFIG_FIELDS}
    return {
        'format': FORMAT_WORKFLOW,
        'format_version': FORMAT_VERSION,
        'exported_at': exported_at,
        'iris_version': instance_version,
        'workflow': workflow,
        'requirements': _requirements(nodes, workflow.get('write_tool_allowlist')),
        'warnings': warnings,
    }


def ai_workflows_portable_export_block(block, exported_at=None, instance_version=None) -> dict:
    """The document of a saved block: `{name, description, category,
    nodes, edges}`."""
    nodes, warnings = ai_workflows_portable_scrub_nodes(block.get('nodes'))
    data = {
        'name': block.get('name'),
        'description': block.get('description'),
        'category': block.get('category'),
        'nodes': nodes,
        'edges': copy.deepcopy(block.get('edges') or []),
    }
    return {
        'format': FORMAT_BLOCK,
        'format_version': FORMAT_VERSION,
        'exported_at': exported_at,
        'iris_version': instance_version,
        'block': data,
        'requirements': _requirements(nodes),
        'warnings': warnings,
    }


def _read(document, expected_format, key, fields):
    if not isinstance(document, dict):
        raise PortableError('The document must be a JSON object')
    if 'format' not in document:
        # A bare definition (what an LLM is most likely to write)
        body = document
    else:
        if document.get('format') != expected_format:
            raise PortableError(f'Expected a document of format "{expected_format}", got "{document.get("format")}"')
        version = document.get('format_version')
        if not isinstance(version, int) or isinstance(version, bool) or version < 1 or version > FORMAT_VERSION:
            raise PortableError(f'Unsupported format_version {version!r} (this instance reads up to '
                                f'{FORMAT_VERSION})')
        body = document.get(key)
        if not isinstance(body, dict):
            raise PortableError(f'"{key}" must be an object')
    try:
        return {field: copy.deepcopy(body[field]) for field in fields if field in body}
    except RecursionError:
        raise PortableError('The document is nested too deeply')


def ai_workflows_portable_read_workflow(document) -> dict:
    """The workflow fields of an imported document (an export, or a bare
    `{name, trigger_type, graph, …}`); raises `PortableError`."""
    return _read(document, FORMAT_WORKFLOW, 'workflow', WORKFLOW_FIELDS)


def ai_workflows_portable_read_block(document) -> dict:
    """The block fields of an imported document; raises `PortableError`."""
    return _read(document, FORMAT_BLOCK, 'block', BLOCK_FIELDS)
