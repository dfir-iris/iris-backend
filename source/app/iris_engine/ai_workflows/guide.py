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

"""The authoring guide: `authoring_guide.md` (how to write workflows and
blocks as JSON) followed by a live catalogue of this instance — node
types and their defaults, events, tools with their arguments, keystore
names — and the example documents shipped in `examples/` (the
VirusTotal pair in full, the rest of the workflow library by name).
Meant to be handed to an LLM as is."""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_GUIDE = os.path.join(_HERE, 'authoring_guide.md')
_EXAMPLES = os.path.join(_HERE, 'examples')
_MAX_DESCRIPTION = 300
# Reproduced in full in the guide; the others are only listed
_EMBEDDED = ('virustotal_ioc_enrichment.workflow.json', 'virustotal_lookup.block.json')


def ai_workflows_guide_examples() -> list:
    """`[{file, kind, name, document}]` of the shipped example documents."""
    examples = []
    for filename in sorted(os.listdir(_EXAMPLES)):
        if not filename.endswith('.json'):
            continue
        with open(os.path.join(_EXAMPLES, filename), encoding='utf-8') as handle:
            document = json.load(handle)
        kind = 'block' if document.get('format') == 'iris-ai-workflow-block' else 'workflow'
        examples.append({'file': filename, 'kind': kind, 'name': (document.get(kind) or {}).get('name'),
                         'document': document})
    return examples


def _short(text) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= _MAX_DESCRIPTION else f'{text[:_MAX_DESCRIPTION - 1]}…'


def _sentence(text) -> str:
    text = _short(text)
    return text if not text or text[-1] in '.!?…' else f'{text}.'


def _arguments(schema) -> str:
    properties = (schema or {}).get('properties') or {}
    if not properties:
        return 'none'
    required = set((schema or {}).get('required') or [])
    parts = []
    for name, spec in properties.items():
        kind = spec.get('type') if isinstance(spec, dict) else None
        kind = '|'.join(kind) if isinstance(kind, list) else kind
        parts.append(f'`{name}`{"*" if name in required else ""}{f" ({kind})" if kind else ""}')
    return ', '.join(parts)


def _catalogue_markdown(catalogue) -> list:
    lines = ['## Live catalogue of this instance', '',
             'Generated from this IRIS instance. Use only these names.', '',
             '### Node types', '']
    for spec in catalogue.get('node_types') or []:
        lines.append(f'- **`{spec["type"]}`** — {_sentence(spec.get("description"))} '
                     f'Ports: {", ".join(f"`{p}`" for p in spec.get("ports") or [])}. '
                     f'Config defaults: `{json.dumps(spec.get("config_defaults") or {}, sort_keys=True)}`')
    lines += ['', '### Trigger types', '', ', '.join(f'`{t}`' for t in catalogue.get('trigger_types') or []),
              '', '### Events (`trigger_config.hooks` of an event trigger)', '']
    for hook in catalogue.get('hooks') or []:
        description = _short(hook.get('description'))
        lines.append(f'- `{hook["name"]}` — {description}' if description else f'- `{hook["name"]}`')
    lines += ['', '### Entity types', '', ', '.join(f'`{t}`' for t in catalogue.get('entity_types') or []),
              '', '### Suggestion kinds and audiences', '',
              f'Kinds: {", ".join(f"`{k}`" for k in catalogue.get("suggestion_kinds") or [])}. '
              f'Audiences: {", ".join(f"`{a}`" for a in catalogue.get("suggestion_audiences") or [])}.',
              '', '### Tools', '',
              'Arguments marked * are required. `case_identifier` and the other scope ids are pinned to the run '
              'entity. A `write` tool runs only when it is in `write_tool_allowlist`.', '']
    for tool in catalogue.get('tools') or []:
        disabled = '' if tool.get('enabled', True) else ' **(disabled on this instance)**'
        lines.append(f'- **`{tool["name"]}`** ({tool.get("classification")}){disabled} — '
                     f'{_sentence(tool.get("description"))} Arguments: {_arguments(tool.get("input_schema"))}')
    keystore = catalogue.get('keystore') or []
    lines += ['', '### Keystore entries you can reference with `key("NAME")`', '']
    if keystore:
        lines += [f'- `{entry["name"]}`{" (secret)" if entry.get("is_secret") else ""}' for entry in keystore]
    else:
        lines.append('None yet: name the entries the workflow needs; they are listed as requirements on import.')
    return lines


def ai_workflows_guide_render(catalogue, examples=None) -> str:
    """The guide markdown for `catalogue` (`ai_workflows_catalogue`)."""
    with open(_GUIDE, encoding='utf-8') as handle:
        lines = [handle.read().rstrip(), '']
    lines += _catalogue_markdown(catalogue)
    examples = ai_workflows_guide_examples() if examples is None else examples
    embedded = [e for e in examples if e['file'] in _EMBEDDED]
    listed = [e for e in examples if e['file'] not in _EMBEDDED]
    if embedded:
        lines += ['', '## Shipped examples', '']
        for example in embedded:
            lines += [f'### {example["name"]} (`{example["file"]}`, {example["kind"]})', '', '```json',
                      json.dumps(example['document'], indent=2, ensure_ascii=False), '```', '']
    if listed:
        # In full they would make the guide too long to hand to an LLM
        lines += ['', '## Workflow library', '',
                  'Also shipped, in the workflow library (`GET /api/v2/ai-workflows/library`, or the Library button '
                  'of the AI workflows page), not reproduced here:', '']
        for example in listed:
            body = example['document'].get(example['kind']) or {}
            lines.append(f'- **{example["name"]}** (`{example["file"]}`, {example["kind"]}) — '
                         f'{_sentence(body.get("description"))}')
    return '\n'.join(lines).rstrip() + '\n'
