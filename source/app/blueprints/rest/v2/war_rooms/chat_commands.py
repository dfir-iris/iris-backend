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

"""War-room scope chat commands: /asset, /ioc, /stage, /push, /share-note.

Called from `chat._resolve_slash` once `post_chat` has checked war-room
write access. Authorization stays in this layer and mirrors the REST
routes the commands stand in for:

  * scope commands compute the attached cases the caller can read and
    the subset it has full access on with the scope blueprint's own
    helpers, then hand those lists to the same business functions as
    `POST /scope/...`; targets outside the writable set come back as
    `denied` rows, exactly like the REST responses;
  * /share-note applies the note-shares rule: full access on every
    target case, and for `all` a count-only error when it is missing on
    any attached case (never silently narrowed).

Each handler returns the `(kind, body, ref_type, ref_id, ref_case_id)`
tuple of the system chat row `post_chat` records; the audit entries
(`track_activity`) are written by the business functions themselves.
A command that changed nothing raises `BusinessProcessingError`, so no
misleading chat row is stored.

Ambiguous names:
  * /stage acts on every asset with that exact name (case-insensitive)
    in every readable attached case - that is the point of staging from
    the war room. Composer markup `[Asset "x"](/case/12/assets)` or case
    targets narrow it down;
  * /push prefers the war-room staging area: when a staged object has
    that name / value, the staged object(s) are pushed and the case
    copies are ignored. Otherwise existing assets (one source per asset
    type) are pushed, then IOCs (first match).
"""

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.war_rooms.note_shares import _CaseAccess
from app.blueprints.rest.v2.war_rooms.scope import _readable_case_ids
from app.blueprints.rest.v2.war_rooms.scope import _writable_case_ids
from app.business.ioc_type_detect import ioc_type_detect_is_ip
from app.business.war_room_chat_commands import war_room_chat_commands_asset_types
from app.business.war_room_chat_commands import war_room_chat_commands_case_count
from app.business.war_room_chat_commands import war_room_chat_commands_decision_id
from app.business.war_room_chat_commands import war_room_chat_commands_describe_results
from app.business.war_room_chat_commands import war_room_chat_commands_has_targets
from app.business.war_room_chat_commands import war_room_chat_commands_ioc_types
from app.business.war_room_chat_commands import war_room_chat_commands_match_assets
from app.business.war_room_chat_commands import war_room_chat_commands_match_iocs
from app.business.war_room_chat_commands import war_room_chat_commands_match_note
from app.business.war_room_chat_commands import war_room_chat_commands_match_stage
from app.business.war_room_chat_commands import war_room_chat_commands_match_staged
from app.business.war_room_chat_commands import war_room_chat_commands_one_source_per_type
from app.business.war_room_chat_commands import war_room_chat_commands_parse_args
from app.business.war_room_chat_commands import war_room_chat_commands_pick_asset_type
from app.business.war_room_chat_commands import war_room_chat_commands_pick_ioc_type
from app.business.war_room_chat_commands import war_room_chat_commands_reject_words
from app.business.war_room_chat_commands import war_room_chat_commands_resolve_targets
from app.business.war_room_chat_commands import war_room_chat_commands_stages
from app.business.war_room_chat_commands import war_room_chat_commands_take_decision_number
from app.business.war_room_note_shares import war_room_note_shares_create
from app.business.war_room_note_shares import war_room_note_shares_parse_create
from app.business.war_room_note_shares import war_room_note_shares_resolve_targets
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.business.war_room_scope import war_room_scope_bulk_stage
from app.business.war_room_scope import war_room_scope_create_asset
from app.business.war_room_scope import war_room_scope_create_ioc
from app.business.war_room_scope import war_room_scope_list_assets
from app.business.war_room_scope import war_room_scope_list_iocs
from app.business.war_room_scope import war_room_scope_push_assets
from app.business.war_room_scope import war_room_scope_push_iocs
from app.business.war_room_scope import war_room_scope_staged_create
from app.business.war_room_scope import war_room_scope_staged_list
from app.business.war_room_scope import war_room_scope_staged_push
from app.models.errors import BusinessProcessingError


_USAGE_ASSET = '/asset <name> [type:"<asset type>"] [#case ...|all]'
_USAGE_IOC = '/ioc <value> [type:"<IOC type>"] [#case ...|all]'
_USAGE_STAGE = '/stage <asset> <stage|none> [reason] [D-n] [#case ...]'
_USAGE_PUSH = '/push <asset|ioc> <#case ...|all>'
_USAGE_SHARE_NOTE = '/share-note <note title|note:<id>> <#case ...|all> [mirror|copy]'
_CUSTOMER_IOC_MESSAGE = (
    'Customer-level IOCs are not supported: IOCs live in cases. '
    'Target cases instead (#<case_id> or all)'
)
_DELIVERIES = ('mirror', 'copy')


def _case_access_lists(war_room_id):
    """(attached, readable, writable) case ids for the current user."""
    attached = war_room_scope_attached_case_ids(war_room_id)
    readable = _readable_case_ids(war_room_id)
    return attached, readable, _writable_case_ids(readable)


def _row_refs(target_case_ids):
    """Single-case results point at that case so the stream's case
    filter and the case card pick them up."""
    if len(target_case_ids) == 1:
        return 'case', target_case_ids[0], target_case_ids[0]
    return None, None, None


def _result_row(body, succeeded, target_case_ids):
    if not succeeded:
        raise BusinessProcessingError(body)
    ref_type, ref_id, ref_case_id = _row_refs(target_case_ids)
    return 'system', body, ref_type, ref_id, ref_case_id


def _require_subject(parsed, usage):
    if not parsed['subject']:
        raise BusinessProcessingError(f'Usage: {usage}')
    return parsed['subject']


def _create_in_cases_or_stage(war_room_id, object_type, label, payload, parsed, type_name):
    if not war_room_chat_commands_has_targets(parsed):
        war_room_scope_staged_create(war_room_id, iris_current_user.id,
                                     {'object_type': object_type, 'payload': payload})
        noun = 'asset' if object_type == 'asset' else 'IOC'
        return 'system', f'Staged {noun} "{label}" ({type_name}) in the war room', None, None, None

    attached, _readable, writable = _case_access_lists(war_room_id)
    targets = war_room_chat_commands_resolve_targets(parsed, attached, writable)
    if object_type == 'asset':
        result = war_room_scope_create_asset(war_room_id, iris_current_user, payload, targets, writable)
        prefix = f'Asset "{label}" ({type_name})'
    else:
        result = war_room_scope_create_ioc(war_room_id, iris_current_user, payload, targets, writable)
        prefix = f'IOC "{label}" ({type_name})'
    text, succeeded = war_room_chat_commands_describe_results(result['results'], 'added to')
    return _result_row(f'{prefix}: {text}', succeeded, targets)


def chat_commands_asset(war_room_id, rest):
    """`/asset <name> [type:"..."] [#case ...|all]`.

    No target: the asset goes to the war-room staging area. Targets:
    created in each case (`all` = attached cases with full access)."""
    parsed = war_room_chat_commands_parse_args(rest)
    name = _require_subject(parsed, _USAGE_ASSET)
    war_room_chat_commands_reject_words(parsed['words'], _USAGE_ASSET)
    if parsed['customer']:
        raise BusinessProcessingError('Assets cannot target a customer: target cases (#<case_id> or all)')
    type_id, type_name = war_room_chat_commands_pick_asset_type(
        name, war_room_chat_commands_asset_types(), parsed['options'].get('type'))
    payload = {'asset_name': name, 'asset_type_id': type_id}
    if ioc_type_detect_is_ip(name):
        payload['asset_ip'] = name
    return _create_in_cases_or_stage(war_room_id, 'asset', name, payload, parsed, type_name)


def chat_commands_ioc(war_room_id, rest):
    """`/ioc <value> [type:"..."] [#case ...|all]` - the type is
    auto-detected from the value unless `type:` is given. `customer` is
    rejected: there is no customer-level IOC store."""
    parsed = war_room_chat_commands_parse_args(rest)
    value = _require_subject(parsed, _USAGE_IOC)
    war_room_chat_commands_reject_words(parsed['words'], _USAGE_IOC)
    if parsed['customer']:
        raise BusinessProcessingError(_CUSTOMER_IOC_MESSAGE)
    ioc_type = war_room_chat_commands_pick_ioc_type(
        value, war_room_chat_commands_ioc_types(), parsed['options'].get('type'))
    payload = {'ioc_value': value, 'ioc_type_id': ioc_type['type_id']}
    return _create_in_cases_or_stage(war_room_id, 'ioc', value, payload, parsed, ioc_type['type_name'])


def chat_commands_stage(war_room_id, rest):
    """`/stage <asset> <stage|none> [reason] [D-n] [#case ...]`.

    Sets the stage of every readable asset with that exact name, in
    every attached case (narrowed by composer markup or case targets).
    Words after the stage name are the reason; `D-n` links a decision."""
    parsed = war_room_chat_commands_parse_args(rest)
    name = _require_subject(parsed, _USAGE_STAGE)
    if parsed['all'] or parsed['customer']:
        raise BusinessProcessingError(f'/stage already applies to every attached case. Usage: {_USAGE_STAGE}')
    number, words = war_room_chat_commands_take_decision_number(parsed['words'])
    stage, cleared, reason_words = war_room_chat_commands_match_stage(words, war_room_chat_commands_stages())
    reason = parsed['options'].get('reason') or ' '.join(reason_words) or None
    decision_id = war_room_chat_commands_decision_id(war_room_id, number) if number is not None else None

    _attached, readable, writable = _case_access_lists(war_room_id)
    scope_cases = readable
    if parsed['subject_case_id'] is not None:
        scope_cases = [case_id for case_id in readable if case_id == parsed['subject_case_id']]
    if parsed['case_ids']:
        scope_cases = [case_id for case_id in scope_cases if case_id in parsed['case_ids']]
    assets = war_room_chat_commands_match_assets(
        war_room_scope_list_assets(scope_cases, search=name)['data'] if scope_cases else [], name)
    if not assets:
        raise BusinessProcessingError(f'No asset named "{name}" in the attached cases you can read')

    result = war_room_scope_bulk_stage(
        war_room_id, iris_current_user.id, [asset['asset_id'] for asset in assets],
        None if cleared else stage['id'], reason, decision_id, readable, writable,
    )
    label = 'cleared in' if cleared else f'set to {stage["name"]} in'
    text, succeeded = war_room_chat_commands_describe_results(result['results'], label)
    suffix = f' (D-{number})' if number is not None else ''
    case_ids = sorted({asset['case_id'] for asset in assets})
    return _result_row(f'Stage of asset "{name}" {text}{suffix}', succeeded, case_ids)


def _push_staged(war_room_id, staged, targets, writable):
    texts = []
    succeeded = 0
    for item in staged:
        payload = item.get('payload') or {}
        object_type = item.get('object_type')
        label = payload.get('asset_name') if object_type == 'asset' else payload.get('ioc_value')
        noun = 'asset' if object_type == 'asset' else 'IOC'
        result = war_room_scope_staged_push(war_room_id, iris_current_user, item['id'], targets, writable)
        text, ok = war_room_chat_commands_describe_results(result['results'], 'pushed to')
        succeeded += ok
        texts.append(f'Staged {noun} "{label}": {text}')
    return ' | '.join(texts), succeeded


def chat_commands_push(war_room_id, rest):
    """`/push <asset|ioc> <#case ...|all>`.

    Resolution order: war-room staging first (every staged object with
    that name / value), then existing assets of the readable attached
    cases (one source per asset type), then IOCs (first match)."""
    parsed = war_room_chat_commands_parse_args(rest)
    name = _require_subject(parsed, _USAGE_PUSH)
    war_room_chat_commands_reject_words(parsed['words'], _USAGE_PUSH)
    if parsed['customer']:
        raise BusinessProcessingError(_CUSTOMER_IOC_MESSAGE)
    if not war_room_chat_commands_has_targets(parsed):
        raise BusinessProcessingError(f'Missing target cases. Usage: {_USAGE_PUSH}')

    attached, readable, writable = _case_access_lists(war_room_id)
    targets = war_room_chat_commands_resolve_targets(parsed, attached, writable)
    kind = parsed['subject_kind']

    if parsed['subject_case_id'] is None:
        staged = war_room_chat_commands_match_staged(war_room_scope_staged_list(war_room_id), name, kind)
        if staged:
            text, succeeded = _push_staged(war_room_id, staged, targets, writable)
            return _result_row(text, succeeded, targets)

    source_cases = readable
    if parsed['subject_case_id'] is not None:
        source_cases = [case_id for case_id in readable if case_id == parsed['subject_case_id']]

    if kind in (None, 'asset') and source_cases:
        assets = war_room_chat_commands_match_assets(
            war_room_scope_list_assets(source_cases, search=name)['data'], name)
        if assets:
            sources = war_room_chat_commands_one_source_per_type(assets)
            result = war_room_scope_push_assets(war_room_id, iris_current_user,
                                                [asset['asset_id'] for asset in sources],
                                                targets, readable, writable)
            text, succeeded = war_room_chat_commands_describe_results(result['results'], 'pushed to')
            return _result_row(f'Asset "{name}": {text}', succeeded, targets)

    if kind in (None, 'ioc') and source_cases:
        iocs = war_room_chat_commands_match_iocs(
            war_room_scope_list_iocs(source_cases, search=name)['data'], name)
        if iocs:
            result = war_room_scope_push_iocs(war_room_id, iris_current_user, [iocs[0]['ioc_id']],
                                              targets, readable, writable)
            text, succeeded = war_room_chat_commands_describe_results(result['results'], 'pushed to')
            return _result_row(f'IOC "{name}": {text}', succeeded, targets)

    raise BusinessProcessingError(
        f'Nothing named "{name}" in the war-room staging or in the attached cases you can read'
    )


def chat_commands_share_note(war_room_id, rest):
    """`/share-note <note title|note:<id>> <#case ...|all> [mirror|copy]`.

    The title is every non-target word (quotes optional); a trailing
    `mirror` / `copy` picks the delivery (default mirror). `all` keeps
    the REST rule: if the caller lacks full access on any attached case
    the share is refused with a count, never silently narrowed to the
    writable cases (unlike the scope commands' `all`)."""
    parsed = war_room_chat_commands_parse_args(rest)
    words = ([parsed['subject']] if parsed['subject'] else []) + parsed['words']
    delivery = 'mirror'
    if words and words[-1].lower() in _DELIVERIES:
        delivery = words.pop().lower()
    if parsed['customer']:
        raise BusinessProcessingError('Notes are shared with cases: target #<case_id> or all')
    if not parsed['all'] and not parsed['case_ids']:
        raise BusinessProcessingError(f'Missing target cases. Usage: {_USAGE_SHARE_NOTE}')
    note_id = parsed['options'].get('note')
    if note_id is None and not words:
        raise BusinessProcessingError(f'Usage: {_USAGE_SHARE_NOTE}')
    note = war_room_chat_commands_match_note(war_room_id, ' '.join(words), note_id=note_id)

    scope = 'all' if parsed['all'] else 'cases'
    parsed_share = war_room_note_shares_parse_create(war_room_id, {
        'note_id': note.note_id,
        'scope': scope,
        'case_ids': parsed['case_ids'] if scope == 'cases' else None,
        'delivery': delivery,
    })
    targets = war_room_note_shares_resolve_targets(war_room_id, scope, parsed_share['case_ids'])
    access = _CaseAccess()
    denied = [case_id for case_id in targets if not access.can_write(case_id)]
    if denied:
        if scope == 'all':
            raise BusinessProcessingError(
                f'You lack full access on {len(denied)} attached case(s); share with selected cases instead'
            )
        raise BusinessProcessingError(f'You need full access on case #{denied[0]} to share a note into it')
    if delivery == 'copy' and not targets:
        raise BusinessProcessingError('No attached case to copy into')

    _share, copy_results = war_room_note_shares_create(war_room_id, parsed_share, iris_current_user.id)
    where = 'all attached cases' if scope == 'all' else war_room_chat_commands_case_count(targets)
    body = f'Shared note "{note.title}" with {where} ({delivery})'
    failed = [case_id for case_id, outcome in (copy_results or {}).items()
              if (outcome or {}).get('status') == 'error']
    if failed:
        body = f'{body}; copy failed on {war_room_chat_commands_case_count(failed)}'
    ref_case_id = targets[0] if scope == 'cases' and len(targets) == 1 else None
    return 'system', body, 'war_room_note', note.note_id, ref_case_id


_HANDLERS = {
    'asset': chat_commands_asset,
    'ioc': chat_commands_ioc,
    'stage': chat_commands_stage,
    'push': chat_commands_push,
    'share-note': chat_commands_share_note,
}


def chat_commands_resolve(war_room_id, cmd, rest):
    """Dispatch a scope command; None when `cmd` is not one of them."""
    handler = _HANDLERS.get(cmd)
    if handler is None:
        return None
    return handler(war_room_id, rest)
