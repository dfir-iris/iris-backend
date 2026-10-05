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

"""Convert an `iris_webhooks_module` configuration into native webhooks.

The module's `wh_configuration` JSON holds a list of hooks with a
`request_body` that is either rendered (`use_rendering`, with `%TITLE%`
/ `%DESCRIPTION%` placeholders) or a field mapping (`"key":
"alerts.alert_title"`, `"key": "Text ${{alerts.alert_title}}"`). Both
become body templates over the native event: placeholders map to
`title` / `summary` / `url`, mapped paths to the `pluck` filter, which
reproduces the module's list-or-scalar behaviour.
"""

import json
import re


LEGACY_MODULE_NAME = 'iris_webhooks_module'

_LINK_STYLES = {
    'markdown': 'markdown',
    'markdown_slack': 'slack',
    'html': 'html',
}
_SECRET_HEADER_HINTS = ('authorization', 'token', 'key', 'secret', 'password', 'auth')
_INLINE_PATH = re.compile(r'\$\{\{(.*?)\}\}')


def _path_expression(path):
    """A Jinja expression for a module field path (`alerts.alert_title`)."""
    keys = [k for k in path.strip().split('.') if k]
    if not keys:
        return "''"
    if keys[0] == 'object_url':
        return 'url'
    rest = '.'.join(keys[1:])
    if not rest:
        return 'items'
    return f"items | pluck('{rest}')"


def _mapped_body(request_body):
    raw = {}

    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, str):
            if '${{' in value:
                # Placeholders inside a literal: keep it a JSON string
                return _INLINE_PATH.sub(lambda m: f'{{{{ ({_path_expression(m.group(1))}) | json_escape }}}}', value)
            token = f'__IRIS_LEGACY_{len(raw)}__'
            raw[token] = f'{{{{ ({_path_expression(value)}) | tojson }}}}'
            return token
        return value

    text = json.dumps(walk(request_body), indent=2, ensure_ascii=False)
    for token, expression in raw.items():
        text = text.replace(f'"{token}"', expression)
    return text


def _rendered_body(request_body, style, warnings):
    text = json.dumps(request_body, indent=2, ensure_ascii=False)
    link = f"(url | link(url, '{style}'))"
    text = text.replace('%TITLE%', '{{ title | json_escape }}')
    text = text.replace('%DESCRIPTION%', f"{{{{ (summary ~ ((' ' ~ {link}) if url else '')) | json_escape }}}}")
    if '%FILE%' in text:
        text = text.replace('%FILE%', '')
        warnings.append('%FILE% (report file attachment) has no native equivalent and was removed.')
    return text


def _events(trigger_on, event_names, warnings):
    events = []
    for trigger in trigger_on or []:
        if trigger == 'all':
            events.append('*')
        if trigger in ('all_create', 'all_update'):
            suffix = trigger.split('_', 1)[1]
            events.extend(name for name in sorted(event_names)
                          if name.startswith('on_postload_') and name.endswith(f'_{suffix}'))
        elif trigger.startswith('on_postload_'):
            if trigger in event_names:
                events.append(trigger)
            else:
                warnings.append(f'Unknown event {trigger} was dropped.')
        elif trigger.startswith('on_preload_'):
            postload = trigger.replace('on_preload_', 'on_postload_', 1)
            if postload in event_names:
                events.append(postload)
                warnings.append(f'{trigger} replaced by {postload}: webhooks only fire once a change is committed.')
            else:
                warnings.append(f'{trigger} has no post-commit equivalent and was dropped.')
        elif trigger.startswith('on_manual_trigger'):
            if trigger in event_names:
                events.append(trigger)
            else:
                warnings.append(f'Unknown manual trigger {trigger} was dropped.')
        else:
            warnings.append(f'Unknown trigger {trigger} was dropped.')
    events = list(dict.fromkeys(events))
    if '*' in events:
        events = ['*'] + [e for e in events if e.startswith('on_manual_trigger')]
    return events


def _headers(request_headers):
    headers = []
    for name, value in (request_headers or {}).items():
        secret = any(hint in name.lower() for hint in _SECRET_HEADER_HINTS)
        headers.append({'name': str(name), 'value': '' if value is None else str(value), 'secret': secret})
    return headers


def webhooks_parse_legacy_config(raw):
    """The parsed `wh_configuration`, or raise ValueError."""
    if isinstance(raw, dict):
        return raw
    try:
        config = json.loads(raw or '{}')
    except (TypeError, ValueError) as e:
        raise ValueError(f'The module configuration is not valid JSON ({e})')
    if not isinstance(config, dict):
        raise ValueError('The module configuration is not a JSON object')
    return config


def webhooks_convert_legacy(config, event_names) -> list:
    """`[{'webhook': <API body>, 'warnings': [...]}]`, one per module hook.

    Imported webhooks are disabled: the module may still be active, and
    both firing would double every delivery.
    """
    converted = []
    for index, hook in enumerate(config.get('webhooks') or []):
        if not isinstance(hook, dict):
            continue
        warnings = []
        name = str(hook.get('name') or f'Imported webhook {index + 1}')
        body = hook.get('request_body')
        if not isinstance(body, dict):
            body = {}
            warnings.append('The hook had no request body; the default IRIS payload is sent instead.')

        if hook.get('use_rendering'):
            style = _LINK_STYLES.get(hook.get('request_rendering'), 'plain')
            template = _rendered_body(body, style, warnings)
        else:
            template = _mapped_body(body)

        events = _events(hook.get('trigger_on'), event_names, warnings)
        if not events:
            warnings.append('No supported event left — pick the events before enabling it.')

        description = 'Imported from the IrisWebHooks module.'
        if hook.get('active') is not False:
            description = f'{description} It was active there.'

        converted.append({
            'webhook': {
                'name': name[:255],
                'description': description,
                'enabled': False,
                'events': events,
                'manual_label': str(hook.get('manual_trigger_name') or '').strip()[:255] or None,
                'method': 'POST',
                'url': str(hook.get('request_url') or ''),
                'query_params': [],
                'headers': _headers(hook.get('request_headers')),
                'auth_type': 'none',
                'body_mode': 'template' if body else 'default',
                'body_template': template if body else None,
                'content_type': 'application/json',
                'verify_tls': hook.get('verify_ssl') is not False,
            },
            'warnings': warnings,
        })
    return converted
