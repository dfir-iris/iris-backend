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

"""Business layer of the org-wide asset flag taxonomy and of the flags
set on a case asset.

A flag is a fact about an asset (Isolated, Patched, Can't be patched…);
an asset carries any combination of them, in no order. Every change
(set, updated, cleared) writes a history row and an event on the case
"Asset status" timeline, linked to the asset.

Authorization is not checked here: the taxonomy routes require server
administrator for changes, and every caller of `asset_flags_set_for_asset`
/ `asset_flags_clear_for_asset` checks case (and war-room) access first.
"""

import datetime
import re

import dateutil.parser

from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_add
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_commit
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_count_assets
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_decision_war_room_id
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_delete
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_find_by_name
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_get
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_get_asset_flag
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_get_case_event
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_get_timeline
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_history
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_in_use_counts
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_link_event
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_list
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_max_sort_order
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_remove
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_replace_all
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_rollback
from app.datamgmt.manage.manage_asset_flags_db import asset_flags_db_save
from app.datamgmt.manage.manage_asset_flags_db import decision_belongs_to_case_war_room
from app.datamgmt.states import update_timeline_state
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.assets import ASSET_FLAG_COLORS
from app.models.assets import ASSET_FLAG_KINDS
from app.models.assets import AssetFlag
from app.models.assets import CaseAssetFlag
from app.models.assets import CaseAssetFlagHistory
from app.models.assets import CaseAssets
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.util import add_obj_history_entry

_NAME_MAX_LENGTH = 64
_DESCRIPTION_MAX_LENGTH = 2000
_REASON_MAX_LENGTH = 4000
_ICON_RE = re.compile(r'^[a-z0-9-]{1,64}$')

ASSET_FLAGS_TIMELINE_NAME = 'Asset status'
_TIMELINE_COLOR = '#6366f1'
_TIMELINE_DESCRIPTION = 'Status flags set on and removed from the assets of the case'
_EVENT_SOURCE = 'IRIS asset status'
_EVENT_TAGS = 'asset-status'

# Events only accept the palette of the timeline UI (see
# `EventSchema.verify_data`): map each flag color onto its closest entry.
_EVENT_COLORS = {
    'red': '#F2596199', 'rose': '#F2596199', 'pink': '#F2596199', 'fuchsia': '#F2596199',
    'orange': '#FFAD4699', 'amber': '#FFAD4699', 'yellow': '#FFAD4699',
    'lime': '#31CE3699', 'green': '#31CE3699', 'emerald': '#31CE3699',
    'teal': '#48ABF799', 'cyan': '#48ABF799', 'sky': '#48ABF799',
    'blue': '#1572E899',
    'indigo': '#6861CE99', 'violet': '#6861CE99', 'purple': '#6861CE99',
}

# name, color, icon, kind, requires_reason, description
_PRESETS = {
    'incident': (
        ('Isolated', 'blue', 'unplug', 'status', False,
         'Asset is contained (network isolation, account disabled, …)'),
        ('Credentials reset', 'teal', 'key-round', 'status', False,
         'Credentials used on or by the asset were reset'),
        ('Patched', 'violet', 'wrench', 'status', False,
         'Root cause is fixed (patch, configuration change)'),
        ('Reimaged', 'indigo', 'hard-drive', 'status', False,
         'Asset was reinstalled from a trusted image'),
        ('Monitored', 'sky', 'eye', 'status', False,
         'Asset is under reinforced monitoring'),
        ('Restored', 'emerald', 'circle-check', 'done', False,
         'Asset is back in normal operation'),
        ("Can't be patched", 'amber', 'shield-off', 'exception', True,
         'Accepted exception: the asset cannot be fixed for now'),
        ('Blocked', 'red', 'octagon-x', 'exception', True,
         'Work on the asset is blocked'),
    ),
    'vulnerability': (
        ('Mitigated', 'sky', 'shield', 'status', False,
         'A workaround reduces the exposure until the fix is applied'),
        ('Patched', 'emerald', 'wrench', 'done', False,
         'The vulnerability is fixed on the asset'),
        ('Not affected', 'slate', 'shield-check', 'done', False,
         'The asset is not affected (version, configuration, feature not used)'),
        ("Can't be patched", 'amber', 'shield-off', 'exception', True,
         'Accepted exception: the asset cannot be fixed for now'),
        ('Accepted risk', 'orange', 'triangle-alert', 'exception', True,
         'Residual risk accepted for this asset'),
    ),
}

ASSET_FLAG_PRESETS = tuple(_PRESETS)


# ---- Serialisation ---------------------------------------------------------

def asset_flags_serialize(flag: AssetFlag, in_use_count=None) -> dict:
    data = {
        'id': flag.id,
        'name': flag.name,
        'description': flag.description,
        'color': flag.color,
        'icon': flag.icon,
        'kind': flag.kind,
        'sort_order': flag.sort_order,
        'requires_reason': bool(flag.requires_reason),
        'requires_decision': bool(flag.requires_decision),
    }
    if in_use_count is not None:
        data['in_use_count'] = in_use_count
    return data


def _asset_flags_serialize_all() -> list:
    counts = asset_flags_db_in_use_counts()
    return [asset_flags_serialize(flag, counts.get(flag.id, 0)) for flag in asset_flags_db_list()]


# ---- Validation ------------------------------------------------------------

def _asset_flags_validate_name(value, exclude_id=None) -> str:
    if not isinstance(value, str):
        raise BusinessProcessingError('Flag name must be a string')
    name = value.strip()
    if not 1 <= len(name) <= _NAME_MAX_LENGTH:
        raise BusinessProcessingError(f'Flag name must be between 1 and {_NAME_MAX_LENGTH} characters')
    if asset_flags_db_find_by_name(name, exclude_id=exclude_id) is not None:
        raise BusinessProcessingError(f'A flag named "{name}" already exists')
    return name


def _asset_flags_validate_description(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('Flag description must be a string')
    value = value.strip()
    if len(value) > _DESCRIPTION_MAX_LENGTH:
        raise BusinessProcessingError(f'Flag description must be at most {_DESCRIPTION_MAX_LENGTH} characters')
    return value or None


def _asset_flags_validate_color(value) -> str:
    if not isinstance(value, str) or value not in ASSET_FLAG_COLORS:
        raise BusinessProcessingError(f'Flag color must be one of: {", ".join(ASSET_FLAG_COLORS)}')
    return value


def _asset_flags_validate_icon(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _ICON_RE.fullmatch(value):
        raise BusinessProcessingError('Flag icon must be lowercase letters, digits and dashes (max 64)')
    return value


def _asset_flags_validate_kind(value) -> str:
    if not isinstance(value, str) or value not in ASSET_FLAG_KINDS:
        raise BusinessProcessingError(f'Flag kind must be one of: {", ".join(ASSET_FLAG_KINDS)}')
    return value


def _asset_flags_validate_bool(value, field) -> bool:
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def _asset_flags_validate(body, existing=None) -> dict:
    """Validated attributes from a request body. Only the writable fields
    are read (no mass assignment); anything else is ignored.

    On create (`existing` is None) missing optional fields take their
    defaults; on update only the fields present are returned.
    """
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid request')

    is_create = existing is None
    exclude_id = None if is_create else existing.id
    attributes = {}

    if 'name' in body or is_create:
        attributes['name'] = _asset_flags_validate_name(body.get('name'), exclude_id=exclude_id)
    if 'description' in body:
        attributes['description'] = _asset_flags_validate_description(body.get('description'))
    if 'color' in body or is_create:
        attributes['color'] = _asset_flags_validate_color(body.get('color', 'slate'))
    if 'icon' in body:
        attributes['icon'] = _asset_flags_validate_icon(body.get('icon'))
    if 'kind' in body or is_create:
        attributes['kind'] = _asset_flags_validate_kind(body.get('kind', 'status'))
    for field in ('requires_reason', 'requires_decision'):
        if field in body or is_create:
            attributes[field] = _asset_flags_validate_bool(body.get(field, False), field)

    return attributes


def _asset_flags_validate_id(value, field, required=False):
    if value is None:
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be an integer{"" if required else " or null"}')
    return value


def asset_flags_validate_reason(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('reason must be a string')
    value = value.strip()
    if len(value) > _REASON_MAX_LENGTH:
        raise BusinessProcessingError(f'reason must be at most {_REASON_MAX_LENGTH} characters')
    return value or None


def _asset_flags_validate_date(value):
    """Naive UTC datetime of the timeline event; now when not given."""
    if value is None or value == '':
        return datetime.datetime.utcnow()
    if not isinstance(value, str):
        raise BusinessProcessingError('date must be an ISO 8601 string')
    try:
        parsed = dateutil.parser.isoparse(value)
    except ValueError:
        raise BusinessProcessingError('date must be an ISO 8601 string')
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


# ---- Taxonomy CRUD ---------------------------------------------------------

def asset_flags_list() -> list:
    return _asset_flags_serialize_all()


def asset_flags_get(flag_id) -> AssetFlag:
    flag = asset_flags_db_get(flag_id)
    if flag is None:
        raise ObjectNotFoundError()
    return flag


def _asset_flags_public(flag: AssetFlag) -> dict:
    return asset_flags_serialize(flag, asset_flags_db_count_assets(flag.id))


def asset_flags_create(body) -> dict:
    attributes = _asset_flags_validate(body)
    flag = AssetFlag(**attributes)
    flag.sort_order = asset_flags_db_max_sort_order() + 1
    if not asset_flags_db_save(flag):
        raise BusinessProcessingError(f'A flag named "{attributes["name"]}" already exists')
    track_activity(f'asset flag #{flag.id} "{flag.name}" created', ctx_less=True)
    return _asset_flags_public(flag)


def asset_flags_update(flag_id, body) -> dict:
    flag = asset_flags_get(flag_id)
    attributes = _asset_flags_validate(body, existing=flag)
    for key, value in attributes.items():
        setattr(flag, key, value)
    if not asset_flags_db_save(flag):
        raise BusinessProcessingError('A flag with this name already exists')
    track_activity(f'asset flag #{flag.id} "{flag.name}" updated', ctx_less=True)
    return _asset_flags_public(flag)


def _asset_flags_in_use_message(count) -> str:
    return f'Flag is set on {count} asset{"" if count == 1 else "s"}'


def asset_flags_delete(flag_id) -> None:
    flag = asset_flags_get(flag_id)
    count = asset_flags_db_count_assets(flag.id)
    if count:
        raise BusinessProcessingError(_asset_flags_in_use_message(count))
    name = flag.name
    if not asset_flags_db_delete(flag):
        raise BusinessProcessingError('Flag is set on assets')
    track_activity(f'asset flag #{flag_id} "{name}" deleted', ctx_less=True)


def asset_flags_reorder(ids) -> list:
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
        raise BusinessProcessingError('ids must be a list of integers')
    flags = asset_flags_db_list()
    if len(ids) != len(set(ids)) or set(ids) != {flag.id for flag in flags}:
        raise BusinessProcessingError('ids must list every flag exactly once')
    by_id = {flag.id: flag for flag in flags}
    for position, flag_id in enumerate(ids):
        by_id[flag_id].sort_order = position
    if not asset_flags_db_commit():
        raise BusinessProcessingError('Unable to reorder the flags')
    track_activity('asset flags reordered', ctx_less=True)
    return _asset_flags_serialize_all()


def asset_flags_apply_preset(preset) -> list:
    definition = _PRESETS.get(preset) if isinstance(preset, str) else None
    if definition is None:
        raise BusinessProcessingError(f'Unknown preset. Expected one of: {", ".join(ASSET_FLAG_PRESETS)}')
    in_use = sum(asset_flags_db_in_use_counts().values())
    if in_use:
        raise BusinessProcessingError(
            f'Cannot apply a preset while flags are in use ({_asset_flags_in_use_message(in_use).lower()})')

    flags = [
        AssetFlag(name=name, color=color, icon=icon, kind=kind, requires_reason=requires_reason,
                  requires_decision=False, description=description, sort_order=position)
        for position, (name, color, icon, kind, requires_reason, description) in enumerate(definition)
    ]
    if not asset_flags_db_replace_all(flags):
        raise BusinessProcessingError('Cannot apply a preset while flags are in use')
    track_activity(f'asset flags replaced by the "{preset}" preset', ctx_less=True)
    return _asset_flags_serialize_all()


# ---- Flags of a case asset -------------------------------------------------

def asset_flags_decision_war_room_id(decision_id):
    """War room of a decision (None if unknown). Lets the caller check war-room
    read access before the decision is linked to an asset."""
    if not isinstance(decision_id, int) or isinstance(decision_id, bool):
        return None
    return asset_flags_db_decision_war_room_id(decision_id)


def asset_flags_is_unchanged(asset: CaseAssets, flag_id, action, reason=None, decision_id=None) -> bool:
    """Whether applying `action` ('set' or 'clear') would be a no-op."""
    current = next((entry for entry in asset.flags if entry.flag_id == flag_id), None)
    if action == 'clear':
        return current is None
    return (current is not None and (current.reason or None) == asset_flags_validate_reason(reason)
            and current.decision_id == decision_id)


def _asset_flags_status_timeline(case_id, user_id):
    """The case "Asset status" timeline, created (flushed) when missing.
    Returns `(timeline, created)`."""
    timeline = asset_flags_db_get_timeline(case_id, ASSET_FLAGS_TIMELINE_NAME)
    if timeline is not None:
        return timeline, False
    timeline = CaseTimeline(case_id=case_id, name=ASSET_FLAGS_TIMELINE_NAME, description=_TIMELINE_DESCRIPTION,
                            color=_TIMELINE_COLOR, is_default=False, created_by_id=user_id)
    asset_flags_db_add(timeline)
    return timeline, True


def _asset_flags_new_event(asset, flag, title, reason, event_date, user_id, timeline):
    now = datetime.datetime.utcnow()
    event = CasesEvent(
        case_id=asset.case_id,
        event_title=title,
        event_source=_EVENT_SOURCE,
        event_content=reason or '',
        event_raw='',
        event_date=event_date,
        event_date_wtz=event_date,
        event_tz='+00:00',
        event_added=now,
        event_in_graph=True,
        event_in_summary=False,
        event_is_flagged=False,
        event_color=_EVENT_COLORS.get(flag.color, ''),
        event_tags=_EVENT_TAGS,
        user_id=user_id,
    )
    add_obj_history_entry(event, 'created')
    asset_flags_db_add(event)
    asset_flags_db_link_event(event.event_id, asset.case_id, asset.asset_id, timeline.timeline_id)
    return event


def _asset_flags_validate_change(asset, flag_id, reason, decision_id, event_date):
    flag_id = _asset_flags_validate_id(flag_id, 'flag_id', required=True)
    decision_id = _asset_flags_validate_id(decision_id, 'decision_id')
    reason = asset_flags_validate_reason(reason)
    event_date = _asset_flags_validate_date(event_date)

    flag = asset_flags_db_get(flag_id)
    if flag is None:
        raise BusinessProcessingError('Unknown flag')

    decision = None
    if decision_id is not None:
        decision = decision_belongs_to_case_war_room(decision_id, asset.case_id)
        if decision is None:
            raise BusinessProcessingError('Decision not found in a war room this case is attached to')
    return flag, reason, decision, event_date


def _asset_flags_commit(asset, user_id, action_label, timeline_created, log_war_room_id) -> CaseAssets:
    update_timeline_state(asset.case_id, user_id)
    add_obj_history_entry(asset, action_label)
    if not asset_flags_db_commit():
        asset_flags_db_rollback()
        raise BusinessProcessingError('Unable to update the asset flags')

    hooked = call_modules_hook('on_postload_asset_update', asset, caseid=asset.case_id)
    if timeline_created:
        track_activity(f'created timeline "{ASSET_FLAGS_TIMELINE_NAME}"', caseid=asset.case_id)
    # Case details stay in the case feed: war-room members may not be able
    # to read the case, so the room only gets a generic entry.
    track_activity(f'{action_label} on asset "{asset.asset_name}"', caseid=asset.case_id)
    if log_war_room_id is not None:
        track_activity('changed the flags of 1 asset', war_room_id=log_war_room_id)
    # A module hook returning nothing must not lose the committed change.
    return hooked or asset


def asset_flags_set_for_asset(asset: CaseAssets, flag_id, reason, decision_id, user_id, war_room_id=None,
                              event_date=None) -> CaseAssets:
    """Set (or update the reason / decision of) a flag on `asset`, write
    the "Asset status" timeline event, commit and record it.

    `event_date` (ISO 8601, defaults to now) is the date of the timeline
    event. No authorization here: the caller must have checked full
    access on the asset's case, and war-room read access on the
    decision's war room. A no-op (flag already set with the same reason
    and decision, no date given) returns the asset untouched.
    """
    flag, reason, decision, date = _asset_flags_validate_change(asset, flag_id, reason, decision_id, event_date)
    if flag.requires_reason and not reason:
        raise BusinessProcessingError(f'Flag "{flag.name}" requires a reason')
    if flag.requires_decision and decision is None:
        raise BusinessProcessingError(f'Flag "{flag.name}" requires a war-room decision')
    decision_id = decision.decision_id if decision is not None else None

    current = asset_flags_db_get_asset_flag(asset.asset_id, flag.id)
    if current is not None and not event_date and (current.reason or None) == reason \
            and current.decision_id == decision_id:
        return asset

    # The scope endpoints pass war_room_id and log their own (aggregate)
    # war-room activity; only a decision-derived room is logged here.
    log_war_room_id = None
    if war_room_id is None and decision is not None:
        war_room_id = decision.war_room_id
        log_war_room_id = war_room_id

    timeline, timeline_created = _asset_flags_status_timeline(asset.case_id, user_id)
    title = f'{asset.asset_name}: {flag.name}'

    if current is None:
        action = 'set'
        event = _asset_flags_new_event(asset, flag, title, reason, date, user_id, timeline)
        current = CaseAssetFlag(asset_id=asset.asset_id, flag_id=flag.id, case_id=asset.case_id, reason=reason,
                                decision_id=decision_id, event_id=event.event_id,
                                set_at=datetime.datetime.utcnow(), set_by_id=user_id)
        asset.flags.append(current)
    else:
        action = 'updated'
        event = asset_flags_db_get_case_event(current.event_id, asset.case_id)
        if event is None:
            # The event was deleted from the timeline: write a new one.
            event = _asset_flags_new_event(asset, flag, title, reason, date, user_id, timeline)
        else:
            event.event_title = title
            event.event_content = reason or ''
            if event_date:
                event.event_date = date
                event.event_date_wtz = date
                event.event_tz = '+00:00'
            add_obj_history_entry(event, 'updated')
        current.reason = reason
        current.decision_id = decision_id
        current.event_id = event.event_id
        current.set_by_id = user_id

    asset_flags_db_add(CaseAssetFlagHistory(
        asset_id=asset.asset_id, case_id=asset.case_id, flag_id=flag.id, flag_name=flag.name, action=action,
        reason=reason, decision_id=decision_id, war_room_id=war_room_id, event_id=event.event_id,
        changed_by_id=user_id,
    ))

    label = f'flag "{flag.name}" {"set" if action == "set" else "updated"}'
    return _asset_flags_commit(asset, user_id, label, timeline_created, log_war_room_id)


def asset_flags_clear_for_asset(asset: CaseAssets, flag_id, reason, user_id, war_room_id=None,
                                event_date=None) -> CaseAssets:
    """Remove a flag from `asset` and write a "removed" event on the
    "Asset status" timeline. Same authorization contract as
    `asset_flags_set_for_asset`; a flag that is not set is a no-op."""
    flag, reason, _, date = _asset_flags_validate_change(asset, flag_id, reason, None, event_date)

    current = asset_flags_db_get_asset_flag(asset.asset_id, flag.id)
    if current is None:
        return asset

    timeline, timeline_created = _asset_flags_status_timeline(asset.case_id, user_id)
    event = _asset_flags_new_event(asset, flag, f'{asset.asset_name}: {flag.name} removed', reason, date, user_id,
                                   timeline)
    asset_flags_db_add(CaseAssetFlagHistory(
        asset_id=asset.asset_id, case_id=asset.case_id, flag_id=flag.id, flag_name=flag.name, action='cleared',
        reason=reason, war_room_id=war_room_id, event_id=event.event_id, changed_by_id=user_id,
    ))
    if current in asset.flags:
        asset.flags.remove(current)
    else:
        asset_flags_db_remove(current)

    return _asset_flags_commit(asset, user_id, f'flag "{flag.name}" removed', timeline_created, None)


def asset_flags_history(asset: CaseAssets) -> list:
    return [{
        'id': entry.id,
        'asset_id': entry.asset_id,
        'case_id': entry.case_id,
        'flag_id': entry.flag_id,
        'flag_name': entry.flag_name,
        'action': entry.action,
        'reason': entry.reason,
        'decision_id': entry.decision_id,
        'decision_number': decision_number,
        'war_room_id': entry.war_room_id,
        'war_room_name': war_room_name,
        'event_id': entry.event_id,
        'changed_by_id': entry.changed_by_id,
        'changed_by_name': changed_by_name,
        'changed_at': entry.changed_at.isoformat() if entry.changed_at else None,
    } for entry, changed_by_name, war_room_name, decision_number in asset_flags_db_history(asset.asset_id)]
