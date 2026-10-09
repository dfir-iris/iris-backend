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

"""The workflow library: the workflow documents shipped in `examples/`,
added to an instance by importing them as they are or after an edit.
The shipped files are never modified; an added workflow is a copy."""

from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples

_DEFAULT_CATEGORY = 'Other'


def ai_workflows_library_entries() -> list:
    """`[{id, file, name, description, category, trigger_type,
    trigger_config, uses_ai, requirements, document}]`, by category then
    name. `id` is the file name without its extensions."""
    entries = []
    for example in ai_workflows_guide_examples():
        if example['kind'] != 'workflow':
            continue
        document = example['document']
        workflow = document.get('workflow') or {}
        nodes = (workflow.get('graph') or {}).get('nodes') or []
        entries.append({
            'id': example['file'].split('.')[0],
            'file': example['file'],
            'name': workflow.get('name') or example['file'],
            'description': workflow.get('description') or '',
            'category': document.get('category') or _DEFAULT_CATEGORY,
            'trigger_type': workflow.get('trigger_type'),
            'trigger_config': workflow.get('trigger_config') or {},
            'uses_ai': any(isinstance(n, dict) and n.get('type') == 'ai_agent' for n in nodes),
            'requirements': document.get('requirements') or {'keystore': [], 'tools': []},
            'document': document,
        })
    return sorted(entries, key=lambda e: (e['category'].lower(), e['name'].lower()))
