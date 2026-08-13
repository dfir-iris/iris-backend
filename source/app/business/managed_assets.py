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

"""Business layer for the customer-bounded asset registry.

Deliberately free of `import sqlalchemy` — the import-linter contract
forbids it, which is why the sighting aggregation and the attribute
history diff live in `datamgmt/manage/manage_managed_assets_db.py`
rather than here.

Access control is expressed once, as a `ManagedAssetViewerScope`, and
then handed to datamgmt to apply inside the SQL. Nothing in this module
filters rows after the fact.
"""

from app.datamgmt.case.case_db import get_case_client_id
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_add
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_add_audit
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_audit
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_audit_log
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_commit
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_compromise_status
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_delete
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_diff
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_export_rows
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_filter
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_get
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_get_by_identity
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_is_integrity_error
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_observe
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_observe_alert
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_observe_case
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_reconcile
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_resolve_user_logins
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_rollback
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_savepoint
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_sighting_summary
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_sightings
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_timeline
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_timeline_count
from app.datamgmt.manage.manage_tags_db import register_db_tags_from_string
from app.iris_engine.utils.tracker import track_activity
from app.logger import logger
from app.models.authorization import ac_has_permission_server_administrator
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.managed_asset_scope import ManagedAssetViewerScope
from app.models.managed_assets import MANAGED_ASSET_NAME_MAX_LENGTH


def managed_assets_normalize_name(value):
    """Collapse a display name to its dedup identity.

    Strip, collapse internal whitespace runs to one space, lowercase.
    `str.split()` with no argument splits on any whitespace, so tabs and
    newlines fold too.

    Deliberately NOT unicode-normalised: hostnames and asset names in
    practice are ASCII, and NFKC folding would silently merge visually
    distinct names an analyst then cannot tell apart or debug.

    This is the single source of truth. Two copies exist in SQL — the
    expression index in the migration and `_normalized_case_asset_name`
    in datamgmt — and all three must agree exactly.
    """
    return ' '.join((value or '').strip().split()).lower()


def managed_assets_viewer_scope(user, permissions):
    """What this caller may see, resolved once per request.

    `None` for either dimension means unrestricted. For a non-admin, an
    *empty* set means "nothing", which the datamgmt layer turns into a
    deny — never into an absent filter.
    """
    # Late import: `iris_engine.access_control.utils` imports back into
    # the business package, so resolving it at module scope would cycle.
    from app.iris_engine.access_control.utils import ac_get_fast_user_cases_access
    from app.business.access_controls import access_controls_user_accessible_customers

    is_administrator = ac_has_permission_server_administrator(permissions)
    if is_administrator:
        return ManagedAssetViewerScope(client_ids=None, case_ids=None, is_administrator=True)

    user_id = getattr(user, 'id', None)
    if user_id is None:
        return ManagedAssetViewerScope(client_ids=set(), case_ids=[], is_administrator=False)

    return ManagedAssetViewerScope(
        client_ids=access_controls_user_accessible_customers(user, permissions) or set(),
        case_ids=ac_get_fast_user_cases_access(user_id) or [],
        is_administrator=False,
    )


def _assert_visible(asset, scope):
    """Raise `ObjectNotFoundError` for an asset outside the caller's scope.

    404 rather than 403 on purpose. The caller has already proven they
    hold `asset_manager_read`, so a 403 here would confirm that asset
    #472 exists under a customer they cannot see — turning the id space
    into an enumerable map of other tenants' inventories.
    """
    if asset is None:
        raise ObjectNotFoundError()
    client_ids = scope.get_client_ids()
    if client_ids is not None and asset.client_id not in client_ids:
        raise ObjectNotFoundError()
    return asset


def managed_assets_get(identifier, scope):
    return _assert_visible(managed_assets_db_get(identifier), scope)


def managed_assets_search(scope, filters, pagination_parameters):
    return managed_assets_db_filter(scope, filters, pagination_parameters)


def managed_assets_summary(assets, scope):
    """Visible-only derived facts for a list of registry rows.

    Every number here is computed over the sightings this caller can
    actually read. True totals are never reported: "seen in 4 cases"
    when the reader can open 2 of them discloses the existence of two
    investigations they are not cleared for.
    """
    raw = managed_assets_db_sighting_summary(assets, scope)
    summary = {}
    for asset in assets:
        entry = raw.get((asset.client_id, asset.normalized_name, asset.asset_type_id))
        if entry is None:
            entry = {
                'case_sighting_count': 0,
                'alert_sighting_count': 0,
                'first_seen_at': None,
                'last_seen_at': None,
                'compromised_at': None,
                'seen_compromised': False,
                'seen_not_compromised': False,
            }
        has_sightings = bool(entry['case_sighting_count'] or entry['alert_sighting_count'])
        summary[asset.managed_asset_id] = {
            'case_sighting_count': entry['case_sighting_count'],
            'alert_sighting_count': entry['alert_sighting_count'],
            'first_seen_at': entry['first_seen_at'],
            'last_seen_at': entry['last_seen_at'],
            # Non-null only when the status below is "compromised": it is
            # aggregated over the compromised sightings alone, so no
            # sighting means no date.
            'compromised_at': entry['compromised_at'],
            'compromise_status_id': managed_assets_db_compromise_status(
                entry['seen_compromised'], entry['seen_not_compromised'], has_sightings
            ),
        }
    return summary


def managed_assets_sightings(asset, scope, kind, pagination_parameters):
    return managed_assets_db_sightings(asset, scope, kind, pagination_parameters)


def managed_assets_timeline(asset, scope, pagination_parameters):
    return managed_assets_db_timeline(asset, scope, pagination_parameters)


def managed_assets_timeline_count(asset, scope):
    return managed_assets_db_timeline_count(asset, scope)


def managed_assets_audit(asset, pagination_parameters):
    """Paginated change log, with the acting user's login resolved.

    The stored `user_login_snapshot` is what the account was called when
    the change happened; a live lookup is what it is called now. The
    live name is preferred and the snapshot is the fallback, so an entry
    stays attributable after the account is renamed or deleted — one
    batched lookup for the page, never one query per row.
    """
    return _resolve_audit_page(
        managed_assets_db_audit(asset.managed_asset_id, pagination_parameters)
    )


def managed_assets_audit_log(scope, client_filter, pagination_parameters):
    """The change log across the whole registry the caller can read.

    The per-asset endpoint cannot show a deletion — the entry's asset id
    is nulled when the row goes, so there is no asset left to ask. This
    is where "who deleted SRV-DC01, and when" is answerable, which is the
    single entry a DFIR audit trail can least afford to lose.
    """
    return _resolve_audit_page(
        managed_assets_db_audit_log(scope, client_filter, pagination_parameters)
    )


def _resolve_audit_page(pagination):
    logins = managed_assets_db_resolve_user_logins(row.user_id for row in pagination.items)
    pagination.items = [
        {
            'audit_id': row.audit_id,
            'managed_asset_id': row.managed_asset_id,
            'asset_name_snapshot': row.asset_name_snapshot,
            'action': row.action,
            'changes': row.changes,
            'user_id': row.user_id,
            'user_login': logins.get(row.user_id) or row.user_login_snapshot,
            'source': row.source,
            'occurred_at': row.occurred_at,
        }
        for row in pagination.items
    ]
    return pagination


def managed_assets_export(scope, filters, limit):
    rows = managed_assets_db_export_rows(scope, filters, limit + 1)
    if len(rows) > limit:
        raise BusinessProcessingError(
            f'Export matches more than {limit} assets. Narrow the filters and try again.'
        )
    return rows


def managed_assets_create(asset, user, source='api'):
    """Persist a new registry row, or explain the identity collision."""
    asset.normalized_name = managed_assets_normalize_name(asset.name)
    if not asset.normalized_name:
        raise BusinessProcessingError('Asset name must not be empty')

    existing = managed_assets_db_get_by_identity(
        asset.client_id, asset.normalized_name, asset.asset_type_id
    )
    if existing is not None:
        raise BusinessProcessingError(
            'An asset with this name and type already exists for this customer'
        )

    asset.created_by = getattr(user, 'id', None)
    asset.updated_by = getattr(user, 'id', None)
    managed_assets_db_add(asset)
    managed_assets_db_add_audit(asset, 'create', None, user, source=source)
    _commit_or_conflict()
    register_db_tags_from_string(asset.tags)

    track_activity(
        f'created managed asset "{asset.name}" for customer #{asset.client_id}',
        ctx_less=True,
    )
    return asset


def managed_assets_update(asset, user, source='api'):
    """Commit pending changes on `asset` and record the field-level diff.

    The diff must be taken before the flush — SQLAlchemy resets each
    attribute's history once the change is written, so reading it after
    the commit reports no changes at all.
    """
    asset.normalized_name = managed_assets_normalize_name(asset.name)
    if not asset.normalized_name:
        raise BusinessProcessingError('Asset name must not be empty')

    collision = managed_assets_db_get_by_identity(
        asset.client_id, asset.normalized_name, asset.asset_type_id
    )
    if collision is not None and collision.managed_asset_id != asset.managed_asset_id:
        raise BusinessProcessingError(
            'An asset with this name and type already exists for this customer'
        )

    changes = managed_assets_db_diff(asset)
    if not changes:
        # Nothing actually moved — do not manufacture an audit entry that
        # says a change happened.
        return asset

    asset.updated_by = getattr(user, 'id', None)
    managed_assets_db_add_audit(asset, 'update', changes, user, source=source)
    _commit_or_conflict()
    register_db_tags_from_string(asset.tags)

    track_activity(f'updated managed asset "{asset.name}"', ctx_less=True)
    return asset


def managed_assets_delete(asset, user, source='api'):
    name = asset.name
    client_id = asset.client_id
    asset_type_id = asset.asset_type_id

    # Audit first: once the row is gone the FK nulls out, and the entry
    # has to carry the identity forward on its own.
    managed_assets_db_add_audit(
        asset, 'delete', None, user, source=source,
        name_snapshot=name, client_id=client_id, asset_type_id=asset_type_id,
    )
    managed_assets_db_delete(asset)
    _commit_or_conflict()

    track_activity(f'deleted managed asset "{name}" of customer #{client_id}', ctx_less=True)


def _commit_or_conflict():
    try:
        managed_assets_db_commit()
    except Exception as exception:
        managed_assets_db_rollback()
        if managed_assets_db_is_integrity_error(exception):
            # Most likely the identity UNIQUE losing a race against a
            # concurrent create. The pre-check above cannot close that
            # window, so the constraint is the real arbiter.
            raise BusinessProcessingError(
                'An asset with this name and type already exists for this customer'
            )
        raise


# ---------------------------------------------------------------------------
# Ingest hooks
# ---------------------------------------------------------------------------

def _observe(operation, describe):
    """Run an observe statement, swallowing any failure.

    A registry write must never be able to break an alert ingest or an
    asset create. The registry can only ever be stale in the
    missing-row direction, and `managed_assets_reconcile` exists
    precisely to repair that.

    The statement runs inside a savepoint so a failure rolls back the
    observe alone and leaves the caller's transaction intact.
    """
    try:
        with managed_assets_db_savepoint():
            created = operation()
    except Exception:
        logger.exception(f'Failed to register managed assets for {describe}')
        return 0

    if not created:
        return 0

    try:
        managed_assets_db_commit()
    except Exception:
        managed_assets_db_rollback()
        logger.exception(f'Failed to commit managed assets for {describe}')
        return 0
    return created


def managed_assets_observe_asset(asset):
    """Register one `CaseAssets` observation in the registry.

    Only case-attached assets can be attributed here — an alert-only
    asset (`case_id IS NULL`) carries no customer of its own and is
    picked up by `managed_assets_observe_alert` instead.
    """
    case_identifier = getattr(asset, 'case_id', None)
    asset_type_id = getattr(asset, 'asset_type_id', None)
    name = getattr(asset, 'asset_name', None)
    if not case_identifier or not asset_type_id or not name:
        return 0

    normalized_name = managed_assets_normalize_name(name)
    # `case_assets.asset_name` has no length limit; the registry does. A
    # display name over the limit only because of whitespace padding is cut
    # to fit, but an identity that genuinely does not fit is not registered
    # at all — a truncated `normalized_name` would collide with every other
    # name sharing its prefix and would match none of its own sightings.
    # The bulk observe statements apply the same two rules in SQL.
    if not normalized_name or len(normalized_name) > MANAGED_ASSET_NAME_MAX_LENGTH:
        return 0

    client_id = get_case_client_id(case_identifier)
    if not client_id:
        return 0

    return _observe(
        lambda: managed_assets_db_observe(client_id, asset_type_id,
                                          name[:MANAGED_ASSET_NAME_MAX_LENGTH], normalized_name),
        f'asset "{name}" of case #{case_identifier}',
    )


def managed_assets_observe_case(case_identifier):
    if not case_identifier:
        return 0
    return _observe(
        lambda: managed_assets_db_observe_case(case_identifier),
        f'case #{case_identifier}',
    )


def managed_assets_observe_alert(alert_identifier):
    if not alert_identifier:
        return 0
    return _observe(
        lambda: managed_assets_db_observe_alert(alert_identifier),
        f'alert #{alert_identifier}',
    )


def managed_assets_reconcile(client_identifier, user):
    """Operator-triggered backfill for one customer.

    Not invoked from any GET: it is a customer-wide write, and hanging a
    write off a read verb is both a CSRF surface and a caching hazard.
    Emits one activity row for the batch rather than an audit entry per
    asset — machine-created rows would otherwise bury the human trail.
    """
    created = managed_assets_db_reconcile(client_identifier)
    managed_assets_db_commit()
    track_activity(
        f'reconciled managed assets for customer #{client_identifier} '
        f'({created} registered)',
        ctx_less=True,
    )
    return created
