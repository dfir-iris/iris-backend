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

"""Business layer for the AI workflow keystore.

Rules:
- personal entries belong to their creator, who alone sees and edits
  them; writing one needs `ai_workflows_write`;
- shared entries are written by server administrators only; users see
  those whose `allowed_group_ids` is empty or intersects their groups;
- administrators list every entry, but secret values never leave the
  server: `value` is null and `has_value` tells whether one is stored;
- on update, an omitted or null `value` keeps the stored one;
- `allowed_hosts` lists the hosts an `http_request` node may send the
  value to. A shared entry must list at least one; an empty list on a
  personal entry means any host (the owner's own choice);
- secret values are encrypted with a key derived from `IRIS_SECRET_KEY`:
  rotating it makes every stored secret unreadable (they must be
  entered again).

The caller passes the permission flags (`is_admin`, `can_write`): the
blueprint resolves them, this module enforces the rules.
"""

import re

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_delete
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_existing_group_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_keystore_by_name
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_keystore_get
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_keystore_list_all
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_visible_entries
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.utils.tracker import track_activity
from app.models.ai_workflows import AiKeystoreEntry
from app.models.ai_workflows import KEYSTORE_PERSONAL
from app.models.ai_workflows import KEYSTORE_SCOPES
from app.models.ai_workflows import KEYSTORE_SHARED
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_NAME_RE = re.compile(r'^[A-Z0-9_]{1,64}$')
_HOST_LABEL_RE = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')
_MAX_VALUE_BYTES = 8 * 1024
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_HOSTS = 50
_MAX_GROUPS = 200


class AiKeystoreForbiddenError(BusinessProcessingError):
    """The user is authenticated but may not perform this operation."""

    def __init__(self, message='Permission denied'):
        super().__init__(message)


# ---- Serialization ---------------------------------------------------------

def _iso(value):
    return value.isoformat() if value else None


def _owner_summary(entry):
    owner = getattr(entry, 'owner', None)
    if owner is None:
        if entry.owner_id is None:
            return None
        return {'id': entry.owner_id, 'login': None, 'name': None}
    return {'id': owner.id, 'login': owner.user, 'name': owner.name}


def ai_keystore_serialize(entry) -> dict:
    """Public shape of an entry. Never carries a secret value."""
    return {
        'id': entry.id,
        'name': entry.name,
        'is_secret': bool(entry.is_secret),
        'value': None if entry.is_secret else entry.value,
        'has_value': bool(entry.value),
        'description': entry.description,
        'scope': entry.scope,
        'owner': _owner_summary(entry),
        'allowed_group_ids': list(entry.allowed_group_ids or []),
        'allowed_hosts': list(entry.allowed_hosts or []),
        'created_at': _iso(entry.created_at),
        'updated_at': _iso(entry.updated_at),
        'last_used_at': _iso(entry.last_used_at),
    }


# ---- Validation ------------------------------------------------------------

def ai_keystore_is_valid_name(name) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def ai_keystore_is_valid_host(host) -> bool:
    """A hostname, optionally with a leading `*.` wildcard."""
    if not isinstance(host, str):
        return False
    host = host.strip().lower().rstrip('.')
    if host.startswith('*.'):
        host = host[2:]
    if not host or len(host) > 253:
        return False
    return all(_HOST_LABEL_RE.match(label) for label in host.split('.'))


def _invalid(errors):
    raise BusinessProcessingError('Invalid keystore entry', data=errors)


def _validate_name(body, existing, errors):
    if existing is not None and 'name' not in body:
        return existing.name
    name = body.get('name')
    if not ai_keystore_is_valid_name(name):
        errors['name'] = ['Name must be 1 to 64 characters among A-Z, 0-9 and _']
        return None
    return name


def _validate_scope(body, existing, errors):
    if existing is not None:
        scope = body.get('scope') or existing.scope
        if scope != existing.scope:
            errors['scope'] = ['The scope of an entry cannot be changed']
        return existing.scope
    scope = body.get('scope') or KEYSTORE_PERSONAL
    if scope not in KEYSTORE_SCOPES:
        errors['scope'] = [f'Scope must be one of {", ".join(KEYSTORE_SCOPES)}']
        return None
    return scope


def _validate_is_secret(body, existing, errors):
    if 'is_secret' not in body or body.get('is_secret') is None:
        return existing.is_secret if existing is not None else True
    value = body.get('is_secret')
    if not isinstance(value, bool):
        errors['is_secret'] = ['Must be a boolean']
        return True
    return value


def _validate_value(body, existing, is_secret, errors):
    """The new plain value, or None to keep the stored one."""
    value = body.get('value')
    if value == '' and existing is not None and existing.is_secret:
        # Forms round-trip a secret as an empty field: keep it
        value = None
    if value is None:
        if existing is None:
            errors['value'] = ['A value is required']
        elif existing.is_secret and not is_secret:
            # Turning a secret into a plain entry would disclose it
            errors['value'] = ['A new value is required to make a secret entry non-secret']
        return None
    if not isinstance(value, str):
        errors['value'] = ['Must be a string']
        return None
    if value == '':
        errors['value'] = ['Must not be empty']
        return None
    if len(value.encode('utf-8')) > _MAX_VALUE_BYTES:
        errors['value'] = [f'Must be at most {_MAX_VALUE_BYTES} bytes']
        return None
    return value


def _validate_description(body, existing, errors):
    if 'description' not in body:
        return existing.description if existing is not None else None
    description = body.get('description')
    if description is None:
        return None
    if not isinstance(description, str):
        errors['description'] = ['Must be a string']
        return None
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        errors['description'] = [f'Must be at most {_MAX_DESCRIPTION_LENGTH} characters']
        return None
    return description.strip() or None


def _validate_group_ids(body, existing, scope, errors):
    if 'allowed_group_ids' not in body:
        return list(existing.allowed_group_ids or []) if existing is not None else []
    group_ids = body.get('allowed_group_ids') or []
    if not isinstance(group_ids, list) or len(group_ids) > _MAX_GROUPS \
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in group_ids):
        errors['allowed_group_ids'] = ['Must be a list of group ids']
        return []
    group_ids = sorted(set(group_ids))
    if group_ids and scope == KEYSTORE_PERSONAL:
        errors['allowed_group_ids'] = ['Only shared entries can be limited to groups']
        return []
    if group_ids:
        missing = sorted(set(group_ids) - set(ai_workflows_db_existing_group_ids(group_ids)))
        if missing:
            errors['allowed_group_ids'] = [f'Unknown group ids: {", ".join(str(i) for i in missing)}']
            return []
    return group_ids


def _validate_hosts(body, existing, scope, errors):
    if 'allowed_hosts' not in body:
        hosts = list(existing.allowed_hosts or []) if existing is not None else []
        if not hosts and scope == KEYSTORE_SHARED:
            errors['allowed_hosts'] = ['A shared entry must list at least one allowed host']
        return hosts
    hosts = body.get('allowed_hosts') or []
    if not isinstance(hosts, list) or len(hosts) > _MAX_HOSTS:
        errors['allowed_hosts'] = [f'Must be a list of at most {_MAX_HOSTS} hostnames']
        return []
    invalid = [str(h) for h in hosts if not ai_keystore_is_valid_host(h)]
    if invalid:
        errors['allowed_hosts'] = [f'Invalid hostnames: {", ".join(invalid)}']
        return []
    if not hosts and scope == KEYSTORE_SHARED:
        errors['allowed_hosts'] = ['A shared entry must list at least one allowed host']
        return []
    normalized = []
    for host in hosts:
        host = host.strip().lower().rstrip('.')
        if host not in normalized:
            normalized.append(host)
    return normalized


def _check_unique(name, scope, owner_id, entry_id, errors):
    if not name or not scope:
        return
    for other in ai_workflows_db_keystore_by_name(name):
        if other.id == entry_id or other.scope != scope:
            continue
        if scope == KEYSTORE_SHARED or other.owner_id == owner_id:
            errors['name'] = [f'An entry named {name} already exists']
            return


def _ai_keystore_validate(body, user_id, existing=None) -> dict:
    if not isinstance(body, dict):
        _invalid({'_schema': ['Expected a JSON object']})
    errors = {}
    name = _validate_name(body, existing, errors)
    scope = _validate_scope(body, existing, errors)
    is_secret = _validate_is_secret(body, existing, errors)
    value = _validate_value(body, existing, is_secret, errors)
    description = _validate_description(body, existing, errors)
    group_ids = _validate_group_ids(body, existing, scope, errors)
    hosts = _validate_hosts(body, existing, scope, errors)
    owner_id = existing.owner_id if existing is not None else (user_id if scope == KEYSTORE_PERSONAL else None)
    _check_unique(name, scope, owner_id, existing.id if existing is not None else None, errors)
    if errors:
        _invalid(errors)
    return {
        'name': name,
        'scope': scope,
        'is_secret': is_secret,
        'value': value,
        'description': description,
        'allowed_group_ids': group_ids,
        'allowed_hosts': hosts,
        'owner_id': owner_id,
    }


def _stored_value(plain, is_secret):
    return encrypt_secret(plain) if is_secret else plain


# ---- Permissions -----------------------------------------------------------

def _check_can_write_scope(scope, is_admin, can_write):
    if scope == KEYSTORE_SHARED and not is_admin:
        raise AiKeystoreForbiddenError('Only server administrators can manage shared entries')
    if scope == KEYSTORE_PERSONAL and not (can_write or is_admin):
        raise AiKeystoreForbiddenError('Permission denied')


def _get_writable(entry_id, user_id, is_admin, can_write, deleting=False):
    entry = ai_workflows_db_keystore_get(entry_id)
    if entry is None:
        raise ObjectNotFoundError()
    if entry.scope == KEYSTORE_PERSONAL and entry.owner_id != user_id:
        if not is_admin:
            # Other users' personal entries don't exist as far as you know
            raise ObjectNotFoundError()
        if not deleting:
            raise AiKeystoreForbiddenError('Personal entries can only be edited by their owner')
        return entry
    if entry.scope == KEYSTORE_SHARED and not is_admin:
        visible = {e.id for e in ai_workflows_keystore_visible_entries(user_id)}
        if entry.id not in visible:
            raise ObjectNotFoundError()
    _check_can_write_scope(entry.scope, is_admin, can_write)
    return entry


# ---- Operations ------------------------------------------------------------

def ai_keystore_list(user_id, is_admin=False) -> list:
    """Every entry for administrators, else the user's personal entries
    and the shared entries its groups may use."""
    entries = ai_workflows_db_keystore_list_all() if is_admin else ai_workflows_keystore_visible_entries(user_id)
    return [ai_keystore_serialize(entry) for entry in entries]


def ai_keystore_create(body, user_id, is_admin=False, can_write=False) -> dict:
    scope = body.get('scope') if isinstance(body, dict) else None
    _check_can_write_scope(scope or KEYSTORE_PERSONAL, is_admin, can_write)
    fields = _ai_keystore_validate(body, user_id)
    entry = AiKeystoreEntry(
        name=fields['name'],
        scope=fields['scope'],
        is_secret=fields['is_secret'],
        value=_stored_value(fields['value'], fields['is_secret']),
        description=fields['description'],
        owner_id=fields['owner_id'],
        allowed_group_ids=fields['allowed_group_ids'],
        allowed_hosts=fields['allowed_hosts'],
        created_by_id=user_id,
    )
    ai_workflows_db_add(entry)
    ai_workflows_db_commit()
    track_activity(f'AI keystore {entry.scope} entry #{entry.id} "{entry.name}" created', ctx_less=True)
    return ai_keystore_serialize(entry)


def ai_keystore_update(entry_id, body, user_id, is_admin=False, can_write=False) -> dict:
    entry = _get_writable(entry_id, user_id, is_admin, can_write)
    fields = _ai_keystore_validate(body, user_id, existing=entry)
    if fields['value'] is not None:
        entry.value = _stored_value(fields['value'], fields['is_secret'])
    elif fields['is_secret'] and not entry.is_secret:
        # Plain entry becoming secret: encrypt what is stored
        entry.value = encrypt_secret(entry.value)
    entry.name = fields['name']
    entry.is_secret = fields['is_secret']
    entry.description = fields['description']
    entry.allowed_group_ids = fields['allowed_group_ids']
    entry.allowed_hosts = fields['allowed_hosts']
    ai_workflows_db_commit()
    track_activity(f'AI keystore {entry.scope} entry #{entry.id} "{entry.name}" updated', ctx_less=True)
    return ai_keystore_serialize(entry)


def ai_keystore_delete(entry_id, user_id, is_admin=False, can_write=False) -> None:
    entry = _get_writable(entry_id, user_id, is_admin, can_write, deleting=True)
    name = entry.name
    scope = entry.scope
    ai_workflows_db_delete(entry)
    track_activity(f'AI keystore {scope} entry #{entry_id} "{name}" deleted', ctx_less=True)
