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

"""Keystore resolution for workflow runs.

A run resolves `key('NAME')` through a `KeystoreResolver` bound to the
run-as user: the user's personal entry wins, then a shared entry whose
`allowed_group_ids` is empty or intersects the user's groups. The
resolver remembers every entry it handed out so the caller can mask the
secret values out of anything it persists (`mask`) and refuse to send
them to a host the entry is not allowed on (`check_url`).
"""

import base64
import json
from urllib.parse import quote
from urllib.parse import quote_plus
from urllib.parse import urlsplit

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_keystore_list
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_keystore_touch
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_group_ids
from app.iris_engine.mail.secrets import decrypt_secret
from app.iris_engine.webhooks.render import MASK
from app.models.ai_workflows import KEYSTORE_PERSONAL
from app.models.ai_workflows import KEYSTORE_SHARED

# Shorter secrets are not masked: replacing every 'a' or 'id' in a log
# would destroy it without protecting anything
_MIN_MASK_LENGTH = 4


class KeystoreError(Exception):
    pass


def _shared_entry_allowed(entry, group_ids) -> bool:
    allowed = entry.allowed_group_ids or []
    if not allowed:
        return True
    return bool(set(allowed) & set(group_ids))


def _entry_visible(entry, user_id, group_ids) -> bool:
    if entry.scope == KEYSTORE_PERSONAL:
        return entry.owner_id == user_id
    if entry.scope == KEYSTORE_SHARED:
        return _shared_entry_allowed(entry, group_ids)
    return False


def ai_workflows_keystore_visible_entries(user_id) -> list:
    """Personal entries of the user plus the shared entries its groups
    may use, ordered by name."""
    group_ids = ai_workflows_db_user_group_ids(user_id)
    entries = ai_workflows_db_keystore_list(owner_id=user_id, include_shared=True)
    return [entry for entry in entries if _entry_visible(entry, user_id, group_ids)]


def _normalize_host(host) -> str:
    return (host or '').strip().lower().rstrip('.')


def ai_workflows_keystore_host_matches(host, pattern) -> bool:
    """Exact host, or `*.example.com` matching any subdomain (at any
    depth) of example.com but not example.com itself."""
    host = _normalize_host(host)
    pattern = _normalize_host(pattern)
    if not host or not pattern:
        return False
    if pattern.startswith('*.'):
        suffix = pattern[1:]
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == pattern


class KeystoreResolver:
    """Resolves keystore names for one user. Not thread-safe; build one
    per run."""

    def __init__(self, user_id):
        self._user_id = user_id
        self._entries = None
        self._values = {}
        self._used = set()
        self._lost = set()
        self._placeholders = {}

    def _load(self) -> dict:
        if self._entries is None:
            entries = {}
            for entry in ai_workflows_keystore_visible_entries(self._user_id):
                current = entries.get(entry.name)
                if current is None or (current.scope != KEYSTORE_PERSONAL and entry.scope == KEYSTORE_PERSONAL):
                    entries[entry.name] = entry
            self._entries = entries
        return self._entries

    def _entry(self, name):
        if not isinstance(name, str) or not name:
            raise KeystoreError('Keystore entry name must be a non-empty string')
        entry = self._load().get(name)
        if entry is None:
            # Same message for unknown and forbidden: don't reveal which
            # entries exist for other users / groups
            raise KeystoreError(f'Keystore entry {name} does not exist or is not available to this user')
        return entry

    def get(self, name) -> str:
        """Plain value of `name`; marks it used and stamps `last_used_at`
        (flush only, the caller commits)."""
        if name in self._values:
            return self._values[name]
        entry = self._entry(name)
        if entry.is_secret:
            value = decrypt_secret(entry.value)
            if value is None:
                raise KeystoreError(f'Keystore entry {name} could not be decrypted')
        else:
            value = entry.value or ''
        self._values[name] = value
        self._used.add(name)
        ai_workflows_db_keystore_touch(entry)
        return value

    def restore(self, names):
        """Re-resolve the entries an earlier step of the run used, so their
        values are masked and their host restrictions keep applying. Does
        not stamp `last_used_at`. An entry that can no longer be resolved
        is remembered: `check_url` then refuses every host, since its
        restrictions are unknown."""
        for name in names or []:
            if not isinstance(name, str) or name in self._values:
                continue
            try:
                entry = self._entry(name)
                value = decrypt_secret(entry.value) if entry.is_secret else (entry.value or '')
            except Exception:
                self._lost.add(name)
                continue
            if value is None:
                self._lost.add(name)
                continue
            self._values[name] = value
            self._used.add(name)

    def reveal_for_llm(self, name) -> str:
        """What the model may see: the value of a non-secret entry, a
        `[secret:NAME]` placeholder for a secret one."""
        entry = self._entry(name)
        if entry.is_secret:
            # Not sent anywhere (so not `used`), but masked from now on in
            # case the value reaches an output by another way
            if name not in self._values and name not in self._placeholders:
                value = decrypt_secret(entry.value)
                if isinstance(value, str):
                    self._placeholders[name] = value
            return f'[secret:{name}]'
        return self.get(name)

    @property
    def used(self) -> set:
        return set(self._used)

    def _secret_values(self) -> list:
        entries = self._load()
        values = {
            value for name, value in self._values.items()
            if entries[name].is_secret and isinstance(value, str) and len(value) >= _MIN_MASK_LENGTH
        }
        values |= {value for value in self._placeholders.values() if len(value) >= _MIN_MASK_LENGTH}
        variants = set()
        for value in values:
            variants |= _encoded_variants(value)
        # Longest first so a secret containing another is masked whole
        return sorted(variants, key=len, reverse=True)

    def mask(self, obj):
        """`obj` with every resolved secret value replaced by MASK, in
        strings, dict keys and values, lists and tuples, recursively."""
        secrets = self._secret_values()
        if not secrets:
            return obj
        return _mask(obj, secrets)

    def check_url(self, url):
        """Raise KeystoreError when a used entry restricts its hosts and
        the host of `url` is not one of them."""
        if self._lost:
            raise KeystoreError(f'Keystore entry {sorted(self._lost)[0]} used earlier in this run is no longer '
                                f'available; its host restrictions cannot be checked')
        entries = self._load()
        restricted = [
            (name, entries[name].allowed_hosts)
            for name in sorted(self._used)
            if name in entries and entries[name].allowed_hosts
        ]
        if not restricted:
            return
        try:
            host = urlsplit(url or '').hostname
        except ValueError:
            host = None
        for name, allowed_hosts in restricted:
            if not host or not any(ai_workflows_keystore_host_matches(host, pattern) for pattern in allowed_hosts):
                raise KeystoreError(f'Keystore entry {name} may not be sent to host {host or "(none)"}')


def _base64_cores(raw) -> set:
    """The base64 characters that depend only on `raw`, for each of the
    three alignments it can have inside a longer encoded string (a Basic
    `user:secret` header, a token embedded in a JSON blob...)."""
    cores = set()
    for offset in range(3):
        encoded = base64.b64encode(b'\0' * offset + raw).decode('ascii')
        # The first group mixes in the preceding bytes, the last partial
        # one the following bytes: keep only the groups in between
        start = 4 if offset else 0
        end = ((offset + len(raw)) // 3) * 4
        core = encoded[start:end]
        if len(core) >= 8:
            cores.add(core)
            cores.add(core.replace('+', '-').replace('/', '_'))
    return cores


def _encoded_variants(value) -> set:
    """`value` plus the encodings it commonly travels under: base64
    (standard, url-safe, with or without padding), percent-encoding, hex
    and JSON string escaping."""
    raw = value.encode('utf-8')
    standard = base64.b64encode(raw).decode('ascii')
    urlsafe = base64.urlsafe_b64encode(raw).decode('ascii')
    variants = {
        value,
        standard,
        standard.rstrip('='),
        urlsafe,
        urlsafe.rstrip('='),
        quote(value, safe=''),
        quote_plus(value, safe=''),
        raw.hex(),
        raw.hex().upper(),
        json.dumps(value, ensure_ascii=False)[1:-1],
        json.dumps(value)[1:-1],
    }
    variants |= _base64_cores(raw)
    return {variant for variant in variants if len(variant) >= _MIN_MASK_LENGTH}


def _mask_str(value, secrets) -> str:
    for secret in secrets:
        if secret in value:
            value = value.replace(secret, MASK)
    return value


def _mask(obj, secrets):
    if isinstance(obj, str):
        return _mask_str(obj, secrets)
    if isinstance(obj, dict):
        return {_mask(key, secrets): _mask(value, secrets) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_mask(value, secrets) for value in obj]
    if isinstance(obj, tuple):
        return tuple(_mask(value, secrets) for value in obj)
    return obj


def ai_workflows_keystore_resolver(user_id) -> KeystoreResolver:
    return KeystoreResolver(user_id)
