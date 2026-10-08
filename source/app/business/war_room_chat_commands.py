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

"""Argument parsing and name resolution for the war-room scope chat
commands (`/asset`, `/ioc`, `/flag`, `/unflag`, `/push`,
`/share-note`).

Everything here is authorization-free: the blueprint computes the
readable / writable attached cases and passes them in, exactly like the
scope REST routes do. The helpers only parse the command text, match
names against lists the caller is already allowed to see, and phrase
the system chat row.

Argument grammar (shared by every command):

  * the first token is the subject (asset name, IOC value, note title);
    quote it when it contains spaces (`"WS 042"`), or use the composer
    markup `[Asset "WS-042"](/case/12/assets)`, which also restricts the
    match to case 12;
  * case targets: `#12`, `#case-12`, `#case12`, a markdown link to
    `/case/12...`, comma lists (`#12,#13`), or `all` (every attached case
    the caller has full access on). `customer` is recognised so that
    `/ioc` can reject it with a clear message;
  * options: `type:"Windows - Computer"`, `reason:"..."`, `note:<id>`;
  * every other token is a plain word, interpreted by the command.
"""

import re

from app.business.asset_flags import asset_flags_list
from app.business.war_room_decisions import war_room_decisions_list
from app.business.war_room_notes import war_room_note_list
from app.business.war_rooms import war_room_members_list
from app.datamgmt.case.assets_type import get_assets_types
from app.datamgmt.case.case_iocs_db import get_ioc_types_list
from app.logger import logger
from app.models.errors import BusinessProcessingError


_TOKEN_RE = re.compile(r'\[[^\]\n]*\]\([^)\s]*\)|[A-Za-z_]+:"[^"]*"|"[^"]*"|\S+')
_MARKUP_RE = re.compile(r'^\[(?:(?P<kind>[A-Za-z]+) )?"(?P<label>[^"]*)"\]\((?P<url>/[^)\s]*)\)$')
_LINK_RE = re.compile(r'^\[(?P<label>[^\]]*)\]\((?P<url>/[^)\s]*)\)$')
_CASE_URL_RE = re.compile(r'^/case/(?P<case_id>\d+)(?:[/?#].*)?$')
_CASE_HASH_RE = re.compile(r'^#(?:case-?)?(?P<case_id>\d+)$', re.IGNORECASE)
_DECISION_REF_RE = re.compile(r'^D-?(?P<number>\d+)$', re.IGNORECASE)

_OPTION_KEYS = ('type', 'reason', 'note')
_DEFAULT_ASSET_TYPES = ('unspecified', 'other', 'windows - computer')
_ACCOUNT_ASSET_TYPE = 'account'
_MARKUP_KINDS = {'asset': 'asset', 'ioc': 'ioc'}
_MAX_LISTED_NAMES = 5


# ---------------------------------------------------------------------------
# Tokenizing / argument parsing
# ---------------------------------------------------------------------------

def war_room_chat_commands_tokenize(text):
    """Split a command tail into tokens, keeping composer markup and
    `"quoted strings"` whole."""
    if not isinstance(text, str):
        return []
    return _TOKEN_RE.findall(text)


def _unquote(token):
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return token[1:-1].strip()
    return token


def _case_id_from_url(url):
    match = _CASE_URL_RE.match(url or '')
    return int(match.group('case_id')) if match else None


def war_room_chat_commands_parse_target(token):
    """Parse one target token.

    Returns `('case', <id>)`, `('all', None)`, `('customer', None)`, or
    None when the token is not a target.
    """
    if not isinstance(token, str):
        return None
    lowered = token.lower()
    if lowered == 'all':
        return 'all', None
    if lowered == 'customer':
        return 'customer', None
    match = _CASE_HASH_RE.match(token)
    if match:
        return 'case', int(match.group('case_id'))
    match = _LINK_RE.match(token)
    if match:
        case_id = _case_id_from_url(match.group('url'))
        if case_id is not None:
            return 'case', case_id
    return None


def _parse_target_list(token):
    """Targets of a token, comma lists included; None if any part is not
    a target (so `a,b` stays a plain word)."""
    if _LINK_RE.match(token):
        single = war_room_chat_commands_parse_target(token)
        return [single] if single else None
    parts = [part for part in token.split(',') if part]
    if not parts:
        return None
    targets = []
    for part in parts:
        target = war_room_chat_commands_parse_target(part)
        if target is None:
            return None
        targets.append(target)
    return targets


def _parse_option(token):
    key, sep, value = token.partition(':')
    if not sep or key.lower() not in _OPTION_KEYS or not value:
        return None
    return key.lower(), _unquote(value)


def _parse_subject(token):
    """(label, case_id, kind) of the subject token."""
    match = _MARKUP_RE.match(token)
    if match:
        kind = _MARKUP_KINDS.get((match.group('kind') or '').lower())
        return match.group('label').strip(), _case_id_from_url(match.group('url')), kind
    return _unquote(token), None, None


def war_room_chat_commands_parse_args(text):
    """Parse a command tail into subject, targets, options and words.

    The subject is the first token that is neither an option nor a case
    target. Everything after it is sorted into targets, options and
    plain words (in order).
    """
    parsed = {
        'subject': None,
        'subject_case_id': None,
        'subject_kind': None,
        'case_ids': [],
        'all': False,
        'customer': False,
        'options': {},
        'words': [],
    }
    for token in war_room_chat_commands_tokenize(text):
        option = _parse_option(token)
        if option is not None:
            parsed['options'][option[0]] = option[1]
            continue
        targets = _parse_target_list(token)
        if targets is not None and (parsed['subject'] is not None or not _MARKUP_RE.match(token)):
            for kind, case_id in targets:
                if kind == 'all':
                    parsed['all'] = True
                elif kind == 'customer':
                    parsed['customer'] = True
                elif case_id not in parsed['case_ids']:
                    parsed['case_ids'].append(case_id)
            continue
        if parsed['subject'] is None:
            label, case_id, kind = _parse_subject(token)
            parsed['subject'] = label or None
            parsed['subject_case_id'] = case_id
            parsed['subject_kind'] = kind
            continue
        parsed['words'].append(_unquote(token))
    return parsed


def war_room_chat_commands_has_targets(parsed):
    return bool(parsed['all'] or parsed['case_ids'] or parsed['customer'])


def war_room_chat_commands_resolve_targets(parsed, attached_case_ids, writable_case_ids):
    """Target case ids of a parsed command.

    `all` expands to the attached cases the caller has full access on.
    Listed cases must be attached to the war room; a listed case the
    caller cannot write is kept so the scope business layer reports it
    as `denied`, like the REST route does.
    """
    attached = list(attached_case_ids)
    writable = set(writable_case_ids)
    targets = []
    if parsed['all']:
        targets = [case_id for case_id in attached if case_id in writable]
        if not targets:
            raise BusinessProcessingError('You have full access on none of the attached cases')
    for case_id in parsed['case_ids']:
        if case_id not in attached:
            raise BusinessProcessingError(f'Case #{case_id} is not attached to this war room')
        if case_id not in targets:
            targets.append(case_id)
    return targets


def war_room_chat_commands_reject_words(words, usage):
    if words:
        raise BusinessProcessingError(
            f'Unexpected text "{" ".join(words)}". Quote names that contain spaces. Usage: {usage}'
        )


def war_room_chat_commands_take_decision_number(words):
    """Pull a single `D-<n>` reference out of `words`.

    Returns (number or None, remaining words)."""
    number = None
    remaining = []
    for word in words:
        match = _DECISION_REF_RE.match(word)
        if match and number is None:
            number = int(match.group('number'))
            continue
        remaining.append(word)
    return number, remaining


# ---------------------------------------------------------------------------
# Taxonomy lookups
# ---------------------------------------------------------------------------

def war_room_chat_commands_asset_types():
    """(asset_type_id, name) pairs, ordered by name."""
    return list(get_assets_types())


def war_room_chat_commands_ioc_types():
    return list(get_ioc_types_list())


def war_room_chat_commands_flags():
    return asset_flags_list()


def war_room_chat_commands_pick_asset_type(name, asset_types, requested=None):
    """(type_id, type_name) for a new asset.

    `type:"..."` wins (exact, case-insensitive). Otherwise a name that
    looks like an account (`DOMAIN\\user`, `user@domain`) gets `Account`,
    and anything else the first of Unspecified / Other /
    Windows - Computer the instance has, else the first type by name.
    """
    by_lower = {}
    for type_id, type_name in asset_types:
        if isinstance(type_name, str):
            by_lower.setdefault(type_name.lower(), (type_id, type_name))
    if not by_lower:
        raise BusinessProcessingError('No asset type is defined on this instance')
    if requested:
        found = by_lower.get(requested.strip().lower())
        if found is None:
            raise BusinessProcessingError(f'Unknown asset type "{requested}"')
        return found
    if ('\\' in name or '@' in name) and _ACCOUNT_ASSET_TYPE in by_lower:
        return by_lower[_ACCOUNT_ASSET_TYPE]
    for preferred in _DEFAULT_ASSET_TYPES:
        if preferred in by_lower:
            return by_lower[preferred]
    first_id, first_name = asset_types[0]
    return first_id, first_name


def war_room_chat_commands_pick_ioc_type(value, ioc_types, requested=None):
    """The ioc_types item for `value`: `type:"..."` when given, else the
    auto-detected type. Raises when neither yields a known type."""
    from app.business.ioc_type_detect import ioc_type_detect_pick
    if requested:
        wanted = requested.strip().lower()
        for ioc_type in ioc_types:
            if str(ioc_type.get('type_name') or '').lower() == wanted:
                return ioc_type
        raise BusinessProcessingError(f'Unknown IOC type "{requested}"')
    picked = ioc_type_detect_pick(value, ioc_types)
    if picked is None:
        raise BusinessProcessingError(
            f'Could not detect the IOC type of "{value}"; add type:"<IOC type>"'
        )
    return picked


def war_room_chat_commands_match_flag(words, flags):
    """Match the leading words against the flag names.

    Returns `(flag, remaining words)`. The longest flag name that is a
    case-insensitive word prefix wins, so `Can't be patched` beats a
    `Can't` flag; a quoted name (`"Can't be patched"`) matches too.
    """
    names = ', '.join(flag['name'] for flag in flags) or 'none defined'
    words = ' '.join(words).split()
    if not words:
        raise BusinessProcessingError(f'Missing flag. Flags: {names}')
    lowered = [word.lower() for word in words]
    best = None
    best_len = 0
    for flag in flags:
        flag_words = str(flag.get('name') or '').lower().split()
        size = len(flag_words)
        if size and size > best_len and lowered[:size] == flag_words:
            best = flag
            best_len = size
    if best is None:
        raise BusinessProcessingError(f'Unknown flag "{words[0]}". Flags: {names}')
    return best, words[best_len:]


def war_room_chat_commands_decision_id(war_room_id, number):
    for decision in war_room_decisions_list(war_room_id):
        if decision.number == number:
            return decision.decision_id
    raise BusinessProcessingError(f'Decision D-{number} not found in this war room')


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _same(left, right):
    return (left or '').strip().lower() == (right or '').strip().lower()


def war_room_chat_commands_match_assets(assets, name, case_id=None):
    """Scope assets (serialized rows) whose name equals `name`
    (case-insensitive), optionally restricted to one case."""
    return [
        asset for asset in assets
        if _same(asset.get('asset_name'), name) and (case_id is None or asset.get('case_id') == case_id)
    ]


def war_room_chat_commands_match_iocs(iocs, value, case_id=None):
    return [
        ioc for ioc in iocs
        if _same(ioc.get('ioc_value'), value) and (case_id is None or ioc.get('case_id') == case_id)
    ]


def war_room_chat_commands_match_staged(staged, name, kind=None):
    """Staged objects whose asset name / IOC value equals `name`."""
    out = []
    for item in staged:
        object_type = item.get('object_type')
        if kind is not None and object_type != kind:
            continue
        payload = item.get('payload') or {}
        label = payload.get('asset_name') if object_type == 'asset' else payload.get('ioc_value')
        if _same(label, name):
            out.append(item)
    return out


def war_room_chat_commands_one_source_per_type(assets):
    """One source asset per asset type: pushing the same name+type from
    several cases would only produce `exists` rows."""
    seen = set()
    out = []
    for asset in assets:
        type_id = asset.get('asset_type_id')
        if type_id in seen:
            continue
        seen.add(type_id)
        out.append(asset)
    return out


def war_room_chat_commands_match_note(war_room_id, query, note_id=None):
    """The war-room note designated by `note:<id>` or by its title.

    Title matching: an exact (case-insensitive) title wins; otherwise a
    unique substring match. Several candidates is an error that asks for
    a more specific title or `note:<id>`.
    """
    notes = war_room_note_list(war_room_id)
    if note_id is not None:
        for note in notes:
            if str(note.note_id) == str(note_id):
                return note
        raise BusinessProcessingError(f'Note #{note_id} not found in this war room')
    if not query:
        raise BusinessProcessingError('Missing note title')
    exact = [note for note in notes if _same(note.title, query)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        ids = ', '.join(f'note:{note.note_id}' for note in exact[:_MAX_LISTED_NAMES])
        raise BusinessProcessingError(f'Several notes are titled "{query}"; use one of {ids}')
    needle = query.strip().lower()
    partial = [note for note in notes if needle in (note.title or '').lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise BusinessProcessingError(f'No note matches "{query}"')
    titles = ', '.join(f'"{note.title}" (note:{note.note_id})' for note in partial[:_MAX_LISTED_NAMES])
    raise BusinessProcessingError(f'"{query}" matches {len(partial)} notes: {titles}')


# ---------------------------------------------------------------------------
# Result phrasing
# ---------------------------------------------------------------------------

def war_room_chat_commands_case_count(case_ids):
    """`1 case` / `N cases`. Result rows are posted to the whole war room,
    whose members may not read every attached case: never name them."""
    count = len(case_ids)
    return f'{count} case' if count == 1 else f'{count} cases'


def war_room_chat_commands_describe_results(results, done_label):
    """Phrase scope business results for the system chat row.

    Counts only, no case ids (see `war_room_chat_commands_case_count`).
    Returns `(text, succeeded)` where `succeeded` counts the targets
    that ended in `created`, `exists`, `updated` or `unchanged`.
    """
    buckets = {}
    error_cases = []
    error_messages = []
    for row in results:
        status = row.get('status')
        case_id = row.get('case_id')
        if status == 'error':
            error_cases.append(case_id)
            message = row.get('message') or 'error'
            if message not in error_messages:
                error_messages.append(message)
            continue
        bucket = buckets.setdefault(status, [])
        if case_id is None or case_id not in bucket:
            bucket.append(case_id)

    def _cases(status):
        ids = [case_id for case_id in buckets.get(status, []) if case_id is not None]
        unknown = len(buckets.get(status, [])) - len(ids)
        text = war_room_chat_commands_case_count(ids) if ids else ''
        if unknown:
            text = f'{text}, {unknown} not found' if text else f'{unknown} not found'
        return text

    parts = []
    done = [c for c in buckets.get('created', []) + buckets.get('updated', []) if c is not None]
    if done:
        parts.append(f'{done_label} {war_room_chat_commands_case_count(sorted(set(done)))}')
    if buckets.get('exists'):
        parts.append(f'already in {_cases("exists")}')
    if buckets.get('unchanged'):
        parts.append(f'unchanged in {_cases("unchanged")}')
    if buckets.get('denied'):
        parts.append(f'denied on {_cases("denied")}')
    if error_cases:
        parts.append(f'failed on {war_room_chat_commands_case_count(error_cases)} ({", ".join(error_messages)})')
    succeeded = sum(len(buckets.get(status, [])) for status in ('created', 'exists', 'updated', 'unchanged'))
    return '; '.join(parts), succeeded


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def war_room_chat_commands_lead_ids(war_room_id):
    """User ids of the room's leads (Incident Commanders)."""
    return [row.user_id for row in war_room_members_list(war_room_id) if row.role == 'lead']


def war_room_chat_commands_notify_approvers(war_room_id, decision, approver_ids, actor_id):
    """Best-effort bell notification to the approvers of a new decision.

    The decision is already stored: a notification failure is logged,
    never raised."""
    if not approver_ids:
        return
    try:
        from app.iris_engine.notifications.service import notify_many
        notify_many(
            user_ids=list(approver_ids),
            event_type='mention',
            title=f'Approval requested: D-{decision.number}',
            body=(decision.title or '')[:255],
            link=f'/war-rooms/{war_room_id}/decisions?d={decision.decision_id}',
            source_type='war_room_decision',
            source_id=decision.decision_id,
            exclude_user_ids=[actor_id] if actor_id else [],
        )
    except Exception:
        logger.exception('War-room decision: approver notification failed')
