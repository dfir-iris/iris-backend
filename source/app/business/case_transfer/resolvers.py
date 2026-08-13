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

"""Turning source-instance identities and reference data into target-instance ids.

Two instances share no user directory and no reference tables, so every
`user:<id>` and every `tag:<id>` in a bundle is a dangling pointer until this
module resolves it. Resolution happens twice: once read-only at inspect time so
the operator can see what will happen, and once for real at apply time using the
decisions they made.

The output of both halves is the same shape — entries in the ref map the
importer feeds to `write_case_bundle`. Nothing here trusts a number that came
out of the bundle: an operator-supplied `target_user_id` is checked against the
database before it is used, and a source pk is only ever a dictionary key.
"""

import secrets

from app import bc
from app.business.case_transfer.manifest import BundleFormatError
from app.datamgmt.case_transfer.transfer_db import create_lookup_row
from app.datamgmt.case_transfer.transfer_db import create_placeholder_user
from app.datamgmt.case_transfer.transfer_db import email_exists
from app.datamgmt.case_transfer.transfer_db import existing_user_ids
from app.datamgmt.case_transfer.transfer_db import find_lookup_ids_by_name
from app.datamgmt.case_transfer.transfer_db import find_user_id_by_external_id
from app.datamgmt.case_transfer.transfer_db import find_users_by_identity
from app.datamgmt.case_transfer.transfer_db import login_exists
from app.datamgmt.case_transfer.transfer_db import lookup_row_exists
from app.datamgmt.case_transfer.transfer_spec import CASE_SPEC
from app.datamgmt.case_transfer.transfer_spec import ENTITIES_BY_KEY
from app.datamgmt.case_transfer.transfer_spec import LOOKUPS
from app.models.errors import BusinessProcessingError

PRINCIPAL_ACTION_MAP = 'map'
PRINCIPAL_ACTION_PLACEHOLDER = 'placeholder'
PRINCIPAL_ACTION_IMPORTER = 'importer'
PRINCIPAL_ACTIONS = (PRINCIPAL_ACTION_MAP, PRINCIPAL_ACTION_PLACEHOLDER, PRINCIPAL_ACTION_IMPORTER)

LOOKUP_ACTION_MAP = 'map'
LOOKUP_ACTION_CREATE = 'create'
LOOKUP_ACTIONS = (LOOKUP_ACTION_MAP, LOOKUP_ACTION_CREATE)

# Marks a user row this instance minted to stand in for someone on another
# deployment. The source user's own uuid is already globally unique, so it is
# enough on its own to re-match the same person across several bundles from the
# same origin.
PLACEHOLDER_EXTERNAL_ID_PREFIX = 'iris-import:'

_LOGIN_MAX_LENGTH = 64
_LOGIN_MAX_ATTEMPTS = 200

# Ordered strictly: a uuid match is the same person, a login match is a guess
# that happens to be right most of the time. The operator sees which axis hit.
_MATCH_AXES = (
    ('uuid', 'uuid'),
    ('external_id', 'external_id'),
    ('email', 'email'),
    ('login', 'login'),
)


def count_principal_references(case_row, entities):
    """How many rows in the bundle point at each principal.

    Surfaced on inspect so the operator can tell the case owner apart from
    someone who left a single comment three years ago, and spend their attention
    accordingly.
    """
    counts = {}

    def tally(payload, user_columns):
        for column in user_columns:
            ref = payload.get(column)
            if ref:
                counts[ref] = counts.get(ref, 0) + 1

    if case_row:
        tally(case_row, CASE_SPEC.user_refs)

    for key, rows in (entities or {}).items():
        spec = ENTITIES_BY_KEY.get(key)
        if spec is None or not spec.user_refs:
            continue
        for payload in rows:
            tally(payload, spec.user_refs)

    return counts


def _principal_identity(principal):
    if not isinstance(principal, dict):
        raise BundleFormatError('principals.json holds a malformed entry')

    ref = principal.get('ref')
    if not isinstance(ref, str) or not ref:
        raise BundleFormatError('A principal in the bundle has no ref')

    return {
        'ref': ref,
        'uuid': principal.get('uuid'),
        'external_id': principal.get('external_id'),
        'email': principal.get('email'),
        'login': principal.get('login'),
        'name': principal.get('name'),
    }


def match_principals(principals):
    """Match every bundle principal against local users, without writing anything.

    Returns `{ref: {source, matched_user_id, matched_by}}`.
    """
    identities = [_principal_identity(principal) for principal in principals or []]

    matches = find_users_by_identity(
        uuids={identity['uuid'] for identity in identities if identity['uuid']},
        external_ids={identity['external_id'] for identity in identities if identity['external_id']},
        emails={identity['email'] for identity in identities if identity['email']},
        logins={identity['login'] for identity in identities if identity['login']},
    )

    # A placeholder minted by an earlier import carries the source uuid in its
    # external_id, so the second bundle from the same instance lands on the same
    # row instead of minting a twin.
    placeholder_ids = {}
    for identity in identities:
        if identity['uuid']:
            placeholder_ids[identity['uuid']] = find_user_id_by_external_id(
                f'{PLACEHOLDER_EXTERNAL_ID_PREFIX}{identity["uuid"]}')

    resolved = {}
    for identity in identities:
        matched_user_id = None
        matched_by = None

        for axis, field in _MATCH_AXES:
            value = identity[field]
            if value and value in matches[axis]:
                matched_user_id = matches[axis][value]
                matched_by = axis
                break

        if matched_user_id is None and identity['uuid'] and placeholder_ids.get(identity['uuid']):
            matched_user_id = placeholder_ids[identity['uuid']]
            matched_by = 'placeholder'

        resolved[identity['ref']] = {
            'source': identity,
            'matched_user_id': matched_user_id,
            'matched_by': matched_by,
        }

    return resolved


def inspect_principals(principals, case_row, entities):
    """The principal half of the inspect response."""
    matched = match_principals(principals)
    counts = count_principal_references(case_row, entities)

    report = []
    for ref, entry in matched.items():
        source = entry['source']
        report.append({
            'ref': ref,
            'source': {
                'uuid': source['uuid'],
                'login': source['login'],
                'name': source['name'],
                'email': source['email'],
                'external_id': source['external_id'],
            },
            'matched_user_id': entry['matched_user_id'],
            'matched_by': entry['matched_by'],
            'reference_count': counts.get(ref, 0),
        })

    report.sort(key=lambda item: (-item['reference_count'], item['ref']))
    return report


def _allocate_login(preferred_login, fallback):
    """A free login derived from the source one.

    Collisions are real: `admin` exists on nearly every instance. We suffix
    rather than overwrite, because silently reusing an existing login would hand
    someone else's account the imported history.
    """
    base = (preferred_login or fallback or 'imported-user').strip() or 'imported-user'
    base = base[:_LOGIN_MAX_LENGTH]

    if not login_exists(base):
        return base

    for suffix in range(2, _LOGIN_MAX_ATTEMPTS):
        marker = f'-{suffix}'
        candidate = f'{base[:_LOGIN_MAX_LENGTH - len(marker)]}{marker}'
        if not login_exists(candidate):
            return candidate

    raise BusinessProcessingError(f'Could not allocate a free login for {base}')


def _allocate_email(preferred_email, login):
    """Keep the source address when it is free, otherwise synthesise one.

    `user.email` is unique. A collision here means the address belongs to a real
    local account the operator chose not to map onto, so the placeholder gets an
    unroutable address instead — `.invalid` is reserved by RFC 2606 and can
    never be delivered to.
    """
    if preferred_email and not email_exists(preferred_email):
        return preferred_email

    candidate = f'{login}@iris-import.invalid'
    if not email_exists(candidate):
        return candidate

    return f'{login}-{secrets.token_hex(4)}@iris-import.invalid'


def _create_placeholder(identity):
    external_id = None
    if identity['uuid']:
        external_id = f'{PLACEHOLDER_EXTERNAL_ID_PREFIX}{identity["uuid"]}'
        existing = find_user_id_by_external_id(external_id)
        if existing is not None:
            return existing, False

    login = _allocate_login(identity['login'], identity['name'])
    email = _allocate_email(identity['email'], login)

    # No one holds this password and no one ever will — the account is inactive
    # and exists only to own history. Hashing a random secret rather than
    # storing an empty string keeps it out of reach even if `active` is later
    # flipped by mistake.
    password_hash = bc.generate_password_hash(secrets.token_urlsafe(48).encode('utf8')).decode('utf8')

    user_id = create_placeholder_user(
        login=login,
        name=identity['name'] or login,
        email=email,
        external_id=external_id,
        password_hash=password_hash,
    )
    return user_id, True


def resolve_principals(principals, decisions, importer_user_id, may_create_placeholders):
    """Apply the operator's decisions and return `(ref_map, report)`.

    `ref_map` maps `user:<source_id>` to a live local user id. Every principal
    ends up in it — a principal with no decision and no match collapses to the
    importing user, so an import can never leave a null owner behind.

    Placeholder rows are created here, inside the caller's transaction, so a
    failure further down the import rolls them back with everything else.
    """
    decisions = decisions or {}
    matched = match_principals(principals)

    requested_ids = {
        decision.get('target_user_id')
        for decision in decisions.values()
        if isinstance(decision, dict) and decision.get('action') == PRINCIPAL_ACTION_MAP
    }
    requested_ids.discard(None)
    valid_target_ids = existing_user_ids(requested_ids)

    ref_map = {}
    report = []

    for ref, entry in matched.items():
        identity = entry['source']
        decision = decisions.get(ref)
        action = None
        if isinstance(decision, dict):
            action = decision.get('action')

        if action is not None and action not in PRINCIPAL_ACTIONS:
            raise BusinessProcessingError(f'Unknown principal action {action} for {ref}')

        if action is None:
            # No explicit decision: honour the automatic match if there was one,
            # otherwise fall back to the importer rather than guessing.
            action = PRINCIPAL_ACTION_MAP if entry['matched_user_id'] else PRINCIPAL_ACTION_IMPORTER
            target_user_id = entry['matched_user_id']
        elif action == PRINCIPAL_ACTION_MAP:
            target_user_id = decision.get('target_user_id') or entry['matched_user_id']
            if target_user_id is None:
                raise BusinessProcessingError(f'No target user supplied for principal {ref}')
            if target_user_id not in valid_target_ids and target_user_id != entry['matched_user_id']:
                raise BusinessProcessingError(f'Unknown target user for principal {ref}')
        else:
            target_user_id = None

        created = False
        if action == PRINCIPAL_ACTION_PLACEHOLDER:
            if not may_create_placeholders:
                # Minting accounts is an administrative act. Letting an upload
                # do it for a standard user would be a privilege escalation
                # dressed up as an import.
                raise BusinessProcessingError(
                    'Creating placeholder users requires server administrator permissions')
            target_user_id, created = _create_placeholder(identity)
        elif action == PRINCIPAL_ACTION_IMPORTER:
            target_user_id = importer_user_id

        ref_map[ref] = target_user_id
        report.append({
            'ref': ref,
            'action': action,
            'target_user_id': target_user_id,
            'created': created,
            'source_login': identity['login'],
            'source_uuid': identity['uuid'],
        })

    return ref_map, report


def _lookup_entries(lookups, lookup_key):
    entries = (lookups or {}).get(lookup_key) or []
    if not isinstance(entries, list):
        raise BundleFormatError(f'lookups.json entry {lookup_key} is not a list')
    return entries


def inspect_lookups(lookups):
    """What each piece of reference data will resolve to, without writing anything."""
    report = {}

    for lookup_key, spec in LOOKUPS.items():
        entries = _lookup_entries(lookups, lookup_key)
        if not entries:
            continue

        names = {entry.get('name') for entry in entries if entry.get('name') is not None}
        by_name = find_lookup_ids_by_name(lookup_key, names)

        report[lookup_key] = []
        for entry in entries:
            matched_id = by_name.get(entry.get('name'))
            report[lookup_key].append({
                'ref': entry.get('ref'),
                'name': entry.get('name'),
                'matched_id': matched_id,
                'will_create': matched_id is None and spec.creatable,
                # Some reference tables drive behaviour rather than labelling it;
                # inventing a row there would produce a case the target's own
                # workflows cannot act on, so the operator has to choose.
                'requires_decision': matched_id is None and not spec.creatable,
            })

    return report


def resolve_lookups(lookups, decisions, customer_identifier=None):
    """Apply lookup decisions and return `(ref_map, created)`.

    Default is match-by-name, create-if-missing. `decisions` overrides any
    single ref; `customer_identifier` overrides the customer wholesale, since
    the operator picks that from the target's own customer list.
    """
    decisions = decisions or {}
    ref_map = {}
    created = []

    for lookup_key, spec in LOOKUPS.items():
        entries = _lookup_entries(lookups, lookup_key)
        if not entries:
            continue

        names = {entry.get('name') for entry in entries if entry.get('name') is not None}
        by_name = find_lookup_ids_by_name(lookup_key, names)

        for entry in entries:
            ref = entry.get('ref')
            if not isinstance(ref, str) or not ref:
                raise BundleFormatError(f'A {lookup_key} entry in the bundle has no ref')

            if lookup_key == 'customer' and customer_identifier is not None:
                ref_map[ref] = customer_identifier
                continue

            decision = decisions.get(ref)
            if isinstance(decision, dict) and decision.get('action'):
                action = decision.get('action')
                if action not in LOOKUP_ACTIONS:
                    raise BusinessProcessingError(f'Unknown reference data action {action} for {ref}')

                if action == LOOKUP_ACTION_MAP:
                    target_id = decision.get('target_id')
                    if target_id is None or not lookup_row_exists(lookup_key, target_id):
                        raise BusinessProcessingError(f'Unknown target for reference data {ref}')
                    ref_map[ref] = target_id
                    continue

                if not spec.creatable:
                    raise BusinessProcessingError(
                        f'Reference data {lookup_key} cannot be created on import — map {ref} instead')

            matched_id = by_name.get(entry.get('name'))
            if matched_id is not None:
                ref_map[ref] = matched_id
                continue

            if not spec.creatable:
                raise BusinessProcessingError(
                    f'No {lookup_key} named "{entry.get("name")}" on this instance — '
                    f'choose an existing one for {ref}')

            new_id = create_lookup_row(lookup_key, entry)
            # Later entries in the same bundle may carry the same name; keep the
            # local cache in step so we create it once.
            by_name[entry.get('name')] = new_id
            ref_map[ref] = new_id
            created.append({'lookup': lookup_key, 'name': entry.get('name'), 'id': new_id})

    return ref_map, created
