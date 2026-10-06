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

"""Business layer of the org-wide asset stage taxonomy and of the stage
of a case asset.

Authorization is not checked here: the taxonomy routes require server
administrator for changes, and every caller of
`asset_stages_set_for_asset` checks case (and war-room) access first.
"""

import datetime
import re

from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_add_history
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_commit
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_count_assets
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_decision_war_room_id
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_delete
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_find_by_name
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_get
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_history
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_in_use_counts
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_list
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_max_sort_order
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_replace_all
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_rollback
from app.datamgmt.manage.manage_asset_stages_db import asset_stages_db_save
from app.datamgmt.manage.manage_asset_stages_db import decision_belongs_to_case_war_room
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.assets import ASSET_STAGE_COLORS
from app.models.assets import ASSET_STAGE_KINDS
from app.models.assets import AssetStage
from app.models.assets import CaseAssetStageHistory
from app.models.assets import CaseAssets
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.util import add_obj_history_entry

_NAME_MAX_LENGTH = 64
_DESCRIPTION_MAX_LENGTH = 2000
_REASON_MAX_LENGTH = 4000
_ICON_RE = re.compile(r'^[a-z0-9-]{1,64}$')

# name, color, icon, kind, requires_reason, is_optional, description
_PRESETS = {
    'incident': (
        ('Identified', 'slate', 'scan-search', 'progress', False, False,
         'Asset is known to be in scope of the incident'),
        ('Isolated', 'orange', 'unplug', 'progress', False, True,
         'Asset is contained (network isolation, account disabled, …)'),
        ('Patched', 'violet', 'wrench', 'progress', False, False,
         'Root cause is fixed (patch, configuration, credentials reset)'),
        ('Restored', 'emerald', 'circle-check', 'done', False, False,
         'Asset is back in normal operation'),
        ('Unpatched', 'amber', 'shield-off', 'exception', True, False,
         'Accepted exception: the asset cannot be fixed for now'),
        ('Blocked', 'red', 'octagon-x', 'exception', True, False,
         'Work on the asset is blocked'),
    ),
    'compromise-simple': (
        ('Compromised', 'red', 'bug', 'progress', False, False,
         'Asset is known to be compromised'),
        ('Contained', 'orange', 'unplug', 'progress', False, True,
         'The attacker no longer has access to the asset'),
        ('Eradicated', 'violet', 'shield-check', 'progress', False, False,
         'Persistence and attacker artefacts are removed'),
        ('Recovered', 'emerald', 'rotate-ccw', 'done', False, False,
         'Asset is back in normal operation'),
        ('Accepted risk', 'amber', 'triangle-alert', 'exception', True, False,
         'Residual risk accepted for this asset'),
    ),
}

ASSET_STAGE_PRESETS = tuple(_PRESETS)


# ---- Serialisation ---------------------------------------------------------

def asset_stages_serialize(stage: AssetStage, in_use_count=None) -> dict:
    data = {
        'id': stage.id,
        'name': stage.name,
        'description': stage.description,
        'color': stage.color,
        'icon': stage.icon,
        'kind': stage.kind,
        'sort_order': stage.sort_order,
        'requires_reason': bool(stage.requires_reason),
        'requires_decision': bool(stage.requires_decision),
        'is_optional': bool(stage.is_optional),
    }
    if in_use_count is not None:
        data['in_use_count'] = in_use_count
    return data


def _asset_stages_serialize_all() -> list:
    counts = asset_stages_db_in_use_counts()
    return [asset_stages_serialize(stage, counts.get(stage.id, 0)) for stage in asset_stages_db_list()]


# ---- Validation ------------------------------------------------------------

def _asset_stages_validate_name(value, exclude_id=None) -> str:
    if not isinstance(value, str):
        raise BusinessProcessingError('Stage name must be a string')
    name = value.strip()
    if not 1 <= len(name) <= _NAME_MAX_LENGTH:
        raise BusinessProcessingError(f'Stage name must be between 1 and {_NAME_MAX_LENGTH} characters')
    if asset_stages_db_find_by_name(name, exclude_id=exclude_id) is not None:
        raise BusinessProcessingError(f'A stage named "{name}" already exists')
    return name


def _asset_stages_validate_description(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('Stage description must be a string')
    value = value.strip()
    if len(value) > _DESCRIPTION_MAX_LENGTH:
        raise BusinessProcessingError(f'Stage description must be at most {_DESCRIPTION_MAX_LENGTH} characters')
    return value or None


def _asset_stages_validate_color(value) -> str:
    if not isinstance(value, str) or value not in ASSET_STAGE_COLORS:
        raise BusinessProcessingError(f'Stage color must be one of: {", ".join(ASSET_STAGE_COLORS)}')
    return value


def _asset_stages_validate_icon(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _ICON_RE.fullmatch(value):
        raise BusinessProcessingError('Stage icon must be lowercase letters, digits and dashes (max 64)')
    return value


def _asset_stages_validate_kind(value) -> str:
    if not isinstance(value, str) or value not in ASSET_STAGE_KINDS:
        raise BusinessProcessingError(f'Stage kind must be one of: {", ".join(ASSET_STAGE_KINDS)}')
    return value


def _asset_stages_validate_bool(value, field) -> bool:
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def _asset_stages_validate(body, existing=None) -> dict:
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
        attributes['name'] = _asset_stages_validate_name(body.get('name'), exclude_id=exclude_id)
    if 'description' in body:
        attributes['description'] = _asset_stages_validate_description(body.get('description'))
    if 'color' in body or is_create:
        attributes['color'] = _asset_stages_validate_color(body.get('color', 'slate'))
    if 'icon' in body:
        attributes['icon'] = _asset_stages_validate_icon(body.get('icon'))
    if 'kind' in body or is_create:
        attributes['kind'] = _asset_stages_validate_kind(body.get('kind', 'progress'))
    for field in ('requires_reason', 'requires_decision', 'is_optional'):
        if field in body or is_create:
            attributes[field] = _asset_stages_validate_bool(body.get(field, False), field)

    # Only a progress stage can be skipped on the way to done.
    kind = attributes.get('kind', None if is_create else existing.kind)
    if kind != 'progress' and (attributes.get('is_optional') or (not is_create and existing.is_optional)):
        attributes['is_optional'] = False

    return attributes


def _asset_stages_validate_id(value, field):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be an integer or null')
    return value


def _asset_stages_validate_reason(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('reason must be a string')
    value = value.strip()
    if len(value) > _REASON_MAX_LENGTH:
        raise BusinessProcessingError(f'reason must be at most {_REASON_MAX_LENGTH} characters')
    return value or None


# ---- Taxonomy CRUD ---------------------------------------------------------

def asset_stages_list() -> list:
    return _asset_stages_serialize_all()


def asset_stages_get(stage_id) -> AssetStage:
    stage = asset_stages_db_get(stage_id)
    if stage is None:
        raise ObjectNotFoundError()
    return stage


def _asset_stages_public(stage: AssetStage) -> dict:
    return asset_stages_serialize(stage, asset_stages_db_count_assets(stage.id))


def asset_stages_create(body) -> dict:
    attributes = _asset_stages_validate(body)
    stage = AssetStage(**attributes)
    stage.sort_order = asset_stages_db_max_sort_order() + 1
    if not asset_stages_db_save(stage):
        raise BusinessProcessingError(f'A stage named "{attributes["name"]}" already exists')
    track_activity(f'asset stage #{stage.id} "{stage.name}" created', ctx_less=True)
    return _asset_stages_public(stage)


def asset_stages_update(stage_id, body) -> dict:
    stage = asset_stages_get(stage_id)
    attributes = _asset_stages_validate(body, existing=stage)
    for key, value in attributes.items():
        setattr(stage, key, value)
    if not asset_stages_db_save(stage):
        raise BusinessProcessingError('A stage with this name already exists')
    track_activity(f'asset stage #{stage.id} "{stage.name}" updated', ctx_less=True)
    return _asset_stages_public(stage)


def _asset_stages_in_use_message(count) -> str:
    return f'Stage is in use by {count} asset{"" if count == 1 else "s"}'


def asset_stages_delete(stage_id) -> None:
    stage = asset_stages_get(stage_id)
    count = asset_stages_db_count_assets(stage.id)
    if count:
        raise BusinessProcessingError(_asset_stages_in_use_message(count))
    name = stage.name
    if not asset_stages_db_delete(stage):
        raise BusinessProcessingError('Stage is in use by assets')
    track_activity(f'asset stage #{stage_id} "{name}" deleted', ctx_less=True)


def asset_stages_reorder(ids) -> list:
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
        raise BusinessProcessingError('ids must be a list of integers')
    stages = asset_stages_db_list()
    if len(ids) != len(set(ids)) or set(ids) != {stage.id for stage in stages}:
        raise BusinessProcessingError('ids must list every stage exactly once')
    by_id = {stage.id: stage for stage in stages}
    for position, stage_id in enumerate(ids):
        by_id[stage_id].sort_order = position
    if not asset_stages_db_commit():
        raise BusinessProcessingError('Unable to reorder the stages')
    track_activity('asset stages reordered', ctx_less=True)
    return _asset_stages_serialize_all()


def asset_stages_apply_preset(preset) -> list:
    definition = _PRESETS.get(preset) if isinstance(preset, str) else None
    if definition is None:
        raise BusinessProcessingError(f'Unknown preset. Expected one of: {", ".join(ASSET_STAGE_PRESETS)}')
    in_use = sum(asset_stages_db_in_use_counts().values())
    if in_use:
        raise BusinessProcessingError(
            f'Cannot apply a preset while stages are in use ({_asset_stages_in_use_message(in_use).lower()})')

    stages = [
        AssetStage(name=name, color=color, icon=icon, kind=kind, requires_reason=requires_reason,
                   requires_decision=False, is_optional=is_optional, description=description,
                   sort_order=position)
        for position, (name, color, icon, kind, requires_reason, is_optional, description) in enumerate(definition)
    ]
    if not asset_stages_db_replace_all(stages):
        raise BusinessProcessingError('Cannot apply a preset while stages are in use')
    track_activity(f'asset stages replaced by the "{preset}" preset', ctx_less=True)
    return _asset_stages_serialize_all()


# ---- Stage of a case asset -------------------------------------------------

def asset_stages_decision_war_room_id(decision_id):
    """War room of a decision (None if unknown). Lets the caller check war-room
    read access before the decision is linked to an asset."""
    if not isinstance(decision_id, int) or isinstance(decision_id, bool):
        return None
    return asset_stages_db_decision_war_room_id(decision_id)


def asset_stages_set_for_asset(asset: CaseAssets, stage_id, reason, decision_id, user_id,
                               war_room_id=None) -> CaseAssets:
    """Validate and apply a stage change on `asset`, commit, and record it.

    No authorization here: the caller must have checked full access on
    the asset's case, and war-room read access on the decision's war room.
    A no-op (same stage, reason and decision) returns the asset untouched.
    """
    stage_id = _asset_stages_validate_id(stage_id, 'stage_id')
    decision_id = _asset_stages_validate_id(decision_id, 'decision_id')
    reason = _asset_stages_validate_reason(reason)

    stage = None
    if stage_id is not None:
        stage = asset_stages_db_get(stage_id)
        if stage is None:
            raise BusinessProcessingError('Unknown stage')
        if stage.requires_reason and not reason:
            raise BusinessProcessingError(f'Stage "{stage.name}" requires a reason')
        if stage.requires_decision and decision_id is None:
            raise BusinessProcessingError(f'Stage "{stage.name}" requires a war-room decision')

    decision = None
    if decision_id is not None:
        decision = decision_belongs_to_case_war_room(decision_id, asset.case_id)
        if decision is None:
            raise BusinessProcessingError('Decision not found in a war room this case is attached to')

    # Clearing the stage clears the justification too; the reason is
    # still kept on the history row.
    new_reason = reason if stage is not None else None
    new_decision_id = decision_id if stage is not None else None

    if asset.stage_id == stage_id and (asset.stage_reason or None) == new_reason \
            and asset.stage_decision_id == new_decision_id:
        return asset

    # The scope endpoints pass war_room_id and log their own (aggregate)
    # war-room activity; only a decision-derived room is logged here.
    log_war_room_id = None
    if war_room_id is None and decision is not None:
        war_room_id = decision.war_room_id
        log_war_room_id = war_room_id

    previous = asset_stages_db_get(asset.stage_id) if asset.stage_id is not None else None
    to_name = stage.name if stage is not None else None

    asset.stage = stage
    asset.stage_id = stage_id
    asset.stage_reason = new_reason
    asset.stage_decision_id = new_decision_id
    asset.stage_updated_at = datetime.datetime.utcnow()
    asset.stage_updated_by_id = user_id

    asset_stages_db_add_history(CaseAssetStageHistory(
        asset_id=asset.asset_id,
        case_id=asset.case_id,
        from_stage_id=previous.id if previous is not None else None,
        from_stage_name=previous.name if previous is not None else None,
        to_stage_id=stage_id,
        to_stage_name=to_name,
        reason=reason,
        decision_id=decision_id,
        war_room_id=war_room_id,
        changed_by_id=user_id,
    ))

    label = f'"{to_name}"' if to_name is not None else 'none'
    add_obj_history_entry(asset, f'stage changed to {label}')
    if not asset_stages_db_commit():
        asset_stages_db_rollback()
        raise BusinessProcessingError('Unable to update the asset stage')

    hooked = call_modules_hook('on_postload_asset_update', asset, caseid=asset.case_id)
    # Case details stay in the case feed: war-room members may not be able
    # to read the case, so the room only gets a generic entry.
    track_activity(f'changed stage of asset "{asset.asset_name}" to {label}', caseid=asset.case_id)
    if log_war_room_id is not None:
        track_activity('changed the stage of 1 asset', war_room_id=log_war_room_id)
    # A module hook returning nothing must not lose the committed change.
    return hooked or asset


def asset_stages_history(asset: CaseAssets) -> list:
    return [{
        'id': entry.id,
        'asset_id': entry.asset_id,
        'case_id': entry.case_id,
        'from_stage_id': entry.from_stage_id,
        'from_stage_name': entry.from_stage_name,
        'to_stage_id': entry.to_stage_id,
        'to_stage_name': entry.to_stage_name,
        'reason': entry.reason,
        'decision_id': entry.decision_id,
        'decision_number': decision_number,
        'war_room_id': entry.war_room_id,
        'war_room_name': war_room_name,
        'changed_by_id': entry.changed_by_id,
        'changed_by_name': changed_by_name,
        'changed_at': entry.changed_at.isoformat() if entry.changed_at else None,
    } for entry, changed_by_name, war_room_name, decision_number in asset_stages_db_history(asset.asset_id)]
