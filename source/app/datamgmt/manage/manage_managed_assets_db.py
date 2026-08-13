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

"""Every SQL statement behind the asset registry.

Two things live here that would more naturally read as business logic,
and both are here for the same reason — the import-linter contract
forbids `app.business` from importing sqlalchemy:

  * the sighting aggregation (a UNION over case and alert observations)
  * `managed_assets_db_diff`, which reads `inspect(obj).attrs.*.history`

The access scope arrives as a `ManagedAssetViewerScope` built by the
caller and is applied **inside** the SQL. Filtering in Python after the
fact would still leak through `total`, through pagination boundaries,
and through any aggregate computed before the filter ran.
"""

from flask_sqlalchemy.pagination import SelectPagination
from sqlalchemy import Integer
from sqlalchemy import and_
from sqlalchemy import cast
from sqlalchemy import distinct
from sqlalchemy import func
from sqlalchemy import inspect
from sqlalchemy import literal
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import tuple_
from sqlalchemy import union_all
from sqlalchemy.exc import IntegrityError

from app.datamgmt.conversions import convert_sort_direction
from app.db import db
from app.models.alerts import Alert
from app.models.assets import CaseAssets
from app.models.assets import CompromiseStatus
from app.models.assets import alert_assets_association
from app.models.authorization import User
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.customers import Client
from app.models.managed_assets import MANAGED_ASSET_NAME_MAX_LENGTH
from app.models.managed_assets import MANAGED_ASSET_SORTABLE_FIELDS
from app.models.managed_assets import ManagedAsset
from app.models.managed_assets import ManagedAssetAudit
from app.models.models import CaseEventsAssets
from app.models.pagination_parameters import PaginationParameters


_COMPROMISED = CompromiseStatus.compromised.value
_NOT_COMPROMISED = CompromiseStatus.not_compromised.value

# Postgres caps a statement at 65535 bind parameters; a two-column tuple
# IN list burns two each. Chunked well below that so an import of any
# permitted size cannot generate a statement the driver refuses.
_IDENTITY_LOOKUP_CHUNK = 1000


class _RowPagination(SelectPagination):
    """`db.paginate` for a select of individual columns.

    Flask-SQLAlchemy ends its item query with `.scalars()`, which keeps
    only the *first* column of every row. That is right for
    `select(Entity)` and quietly destructive for a projection: the
    sightings UNION came back as a list of bare `'case'` / `'alert'`
    discriminators with every other column dropped, and the schema then
    serialised each one as `{}` — a page of rows the UI rendered as
    "#undefined". `total` is computed from a separate count, so the row
    count stayed right while the rows themselves were empty.

    Keeping the rows means every labelled column stays addressable, by
    attribute for the audit resolver and by name for marshmallow.
    """

    def _query_items(self):
        statement = self._query_args['select'].limit(self.per_page).offset(self._query_offset)
        return list(self._query_args['session'].execute(statement))


def _paginate_rows(stmt, page, per_page):
    """Paginate a column projection.

    `error_out=False` makes a page past the end an empty page rather
    than a 404, and `max_per_page=None` matches `db.paginate`'s own
    default — the routes clamp `per_page` before we ever get here.
    """
    return _RowPagination(
        select=stmt,
        session=db.session(),
        page=page,
        per_page=per_page,
        max_per_page=None,
        error_out=False,
    )


def _normalized_case_asset_name():
    """The `case_assets.asset_name` -> registry identity expression.

    Must stay character-for-character equivalent to
    `managed_assets_normalize_name` in the business layer and to the
    `_NORMALIZE_SQL` constant in the migration that indexes it. If the
    three drift, sightings silently stop resolving — no error, just an
    inventory that reports zero everywhere.
    """
    return func.lower(func.btrim(func.regexp_replace(CaseAssets.asset_name, r'\s+', ' ', 'g')))


def _escape_like(value):
    """Neutralise LIKE metacharacters in caller-supplied search text.

    Without this a search for `%` matches every row and `_` matches any
    single character — the user gets silently wrong results, and a long
    string of `%` is a cheap way to force repeated full scans.
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _case_sighting_conditions(scope):
    """Correlated join + scope predicates for case observations.

    Correlates against the mapped `ManagedAsset` entity, so these belong
    in an EXISTS subquery under a query that already selects from
    `managed_asset`. The detail endpoints pass concrete values instead.
    """
    conditions = [
        _normalized_case_asset_name() == ManagedAsset.normalized_name,
        CaseAssets.asset_type_id == ManagedAsset.asset_type_id,
        Cases.client_id == ManagedAsset.client_id,
    ]
    case_ids = scope.get_case_ids()
    if case_ids is not None:
        # `in_([])` renders as a false predicate, which is precisely the
        # behaviour we want for "this user can read no case at all".
        conditions.append(CaseAssets.case_id.in_(case_ids))
    return conditions


def _alert_sighting_conditions(scope):
    conditions = [
        _normalized_case_asset_name() == ManagedAsset.normalized_name,
        CaseAssets.asset_type_id == ManagedAsset.asset_type_id,
        Alert.alert_customer_id == ManagedAsset.client_id,
    ]
    client_ids = scope.get_client_ids()
    if client_ids is not None:
        # IRIS has no per-alert ACL; customer membership is the boundary.
        # (`UserClient.allow_alerts` is written but never read — relying
        # on it would be relying on a field nothing enforces.)
        conditions.append(Alert.alert_customer_id.in_(list(client_ids)))
    return conditions


def _visible_case_sighting_ids(asset, scope):
    """`case_assets.asset_id`s of `asset` that `scope` may actually see."""
    conditions = [
        _normalized_case_asset_name() == asset.normalized_name,
        CaseAssets.asset_type_id == asset.asset_type_id,
        Cases.client_id == asset.client_id,
    ]
    case_ids = scope.get_case_ids()
    if case_ids is not None:
        conditions.append(CaseAssets.case_id.in_(case_ids))
    return select(CaseAssets.asset_id).select_from(CaseAssets).join(
        Cases, Cases.case_id == CaseAssets.case_id
    ).where(and_(*conditions))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def managed_assets_db_get(identifier):
    return ManagedAsset.query.filter(
        ManagedAsset.managed_asset_id == identifier
    ).first()


def managed_assets_db_get_by_identity(client_id, normalized_name, asset_type_id):
    return ManagedAsset.query.filter(
        ManagedAsset.client_id == client_id,
        ManagedAsset.normalized_name == normalized_name,
        ManagedAsset.asset_type_id == asset_type_id,
    ).first()


def _apply_registry_filters(query, scope, filters):
    client_ids = scope.get_client_ids()
    if client_ids is not None:
        query = query.filter(ManagedAsset.client_id.in_(list(client_ids)))

    requested_clients = filters.get('client_id')
    if requested_clients:
        # Intersection, never replacement — a caller asking for a
        # customer they cannot read gets an empty page rather than that
        # customer's inventory.
        query = query.filter(ManagedAsset.client_id.in_(requested_clients))

    for field, values in (
        ('asset_type_id', filters.get('asset_type_id')),
        ('criticality', filters.get('criticality')),
        ('environment', filters.get('environment')),
    ):
        if values:
            query = query.filter(getattr(ManagedAsset, field).in_(values))

    owner = filters.get('owner')
    if owner:
        query = query.filter(ManagedAsset.owner.ilike(f'%{_escape_like(owner)}%', escape='\\'))

    for tag in filters.get('tag') or []:
        query = query.filter(ManagedAsset.tags.ilike(f'%{_escape_like(tag)}%', escape='\\'))

    search = filters.get('search')
    if search:
        pattern = f'%{_escape_like(search)}%'
        query = query.filter(or_(
            ManagedAsset.name.ilike(pattern, escape='\\'),
            ManagedAsset.description.ilike(pattern, escape='\\'),
            ManagedAsset.ip.ilike(pattern, escape='\\'),
            ManagedAsset.domain.ilike(pattern, escape='\\'),
        ))

    is_active = filters.get('is_active')
    if is_active is not None:
        query = query.filter(ManagedAsset.is_active.is_(is_active))

    return query


def _sighting_exists_clause(scope, compromised_only=False):
    """EXISTS over the *visible* sightings of the correlated registry row.

    Used by `has_sightings` and `compromised`. Both filters run against
    the scoped subquery rather than the raw observation tables — an
    unscoped `?compromised=true` would answer "is this host compromised
    in some case you cannot see?", which is the exact disclosure the
    counts policy exists to prevent.
    """
    case_stmt = select(literal(1)).select_from(CaseAssets).join(
        Cases, Cases.case_id == CaseAssets.case_id
    ).where(and_(*_case_sighting_conditions(scope)))

    alert_stmt = select(literal(1)).select_from(CaseAssets).join(
        alert_assets_association, alert_assets_association.c.asset_id == CaseAssets.asset_id
    ).join(Alert, Alert.alert_id == alert_assets_association.c.alert_id).where(
        and_(*_alert_sighting_conditions(scope))
    )

    if compromised_only:
        case_stmt = case_stmt.where(CaseAssets.asset_compromise_status_id == _COMPROMISED)
        alert_stmt = alert_stmt.where(CaseAssets.asset_compromise_status_id == _COMPROMISED)

    return or_(case_stmt.exists(), alert_stmt.exists())


def managed_assets_db_filter(scope, filters, pagination_parameters: PaginationParameters):
    """Paginated registry listing, access-scoped inside the query."""
    if not scope.has_customer_access():
        # No customer membership at all: deny outright rather than fall
        # through to an unfiltered query.
        query = ManagedAsset.query.filter(ManagedAsset.managed_asset_id == -1)
        return query.paginate(page=1, per_page=1, error_out=False)

    query = _apply_registry_filters(ManagedAsset.query, scope, filters)

    has_sightings = filters.get('has_sightings')
    if has_sightings is not None:
        clause = _sighting_exists_clause(scope)
        query = query.filter(clause if has_sightings else ~clause)

    compromised = filters.get('compromised')
    if compromised is not None:
        clause = _sighting_exists_clause(scope, compromised_only=True)
        query = query.filter(clause if compromised else ~clause)

    order_by = pagination_parameters.get_order_by()
    if order_by in MANAGED_ASSET_SORTABLE_FIELDS:
        order_func = convert_sort_direction(pagination_parameters.get_direction())
        query = query.order_by(order_func(getattr(ManagedAsset, order_by)))
    else:
        query = query.order_by(ManagedAsset.name.asc())
    # Stable tiebreak so page N+1 never repeats or skips a row that ties
    # with another on the sort column.
    query = query.order_by(ManagedAsset.managed_asset_id.asc())

    return query.paginate(
        page=pagination_parameters.get_page(),
        per_page=pagination_parameters.get_per_page(),
        error_out=False,
    )


def managed_assets_db_sighting_summary(assets, scope):
    """Per-asset visible sighting aggregates, in two queries total.

    Keyed by `(client_id, normalized_name, asset_type_id)` — the registry
    identity. One grouped query per observation kind rather than a query
    per asset: a 100-row page would otherwise fire 200 round trips.
    """
    summary = {}
    if not assets:
        return summary

    keys = [(a.client_id, a.normalized_name, a.asset_type_id) for a in assets]
    for key in keys:
        summary[key] = {
            'case_sighting_count': 0,
            'alert_sighting_count': 0,
            'first_seen_at': None,
            'last_seen_at': None,
            'compromised_at': None,
            'seen_compromised': False,
            'seen_not_compromised': False,
        }

    if not scope.has_customer_access():
        return summary

    normalized = _normalized_case_asset_name()
    is_compromised = CaseAssets.asset_compromise_status_id == _COMPROMISED
    compromised_flag = func.bool_or(is_compromised)
    not_compromised_flag = func.bool_or(CaseAssets.asset_compromise_status_id == _NOT_COMPROMISED)

    # No column records *when* a compromise flag was set — `case_assets`
    # only has `date_update`, which any edit to the row moves. So the
    # date reported is the earliest visible sighting that carries the
    # compromised status, i.e. "compromised since", and the aggregate
    # uses the same expression the sightings list shows as `seen_at` so
    # the two always agree on screen. `FILTER` over an empty set yields
    # NULL, which makes the field non-null exactly when
    # `seen_compromised` is true.
    case_compromised_at = func.min(
        func.coalesce(CaseAssets.date_added, Cases.initial_date)
    ).filter(is_compromised)
    alert_compromised_at = func.min(Alert.alert_source_event_time).filter(is_compromised)

    case_stmt = select(
        Cases.client_id.label('client_id'),
        normalized.label('normalized_name'),
        CaseAssets.asset_type_id.label('asset_type_id'),
        func.count(distinct(CaseAssets.case_id)).label('sighting_count'),
        func.min(CaseAssets.date_added).label('first_seen_at'),
        func.max(func.coalesce(CaseAssets.date_update, CaseAssets.date_added)).label('last_seen_at'),
        case_compromised_at.label('compromised_at'),
        compromised_flag.label('seen_compromised'),
        not_compromised_flag.label('seen_not_compromised'),
    ).select_from(CaseAssets).join(
        Cases, Cases.case_id == CaseAssets.case_id
    ).where(
        tuple_(Cases.client_id, normalized, CaseAssets.asset_type_id).in_(keys)
    ).group_by(Cases.client_id, normalized, CaseAssets.asset_type_id)

    case_ids = scope.get_case_ids()
    if case_ids is not None:
        case_stmt = case_stmt.where(CaseAssets.case_id.in_(case_ids))

    alert_stmt = select(
        Alert.alert_customer_id.label('client_id'),
        normalized.label('normalized_name'),
        CaseAssets.asset_type_id.label('asset_type_id'),
        func.count(distinct(Alert.alert_id)).label('sighting_count'),
        func.min(Alert.alert_source_event_time).label('first_seen_at'),
        func.max(Alert.alert_source_event_time).label('last_seen_at'),
        alert_compromised_at.label('compromised_at'),
        compromised_flag.label('seen_compromised'),
        not_compromised_flag.label('seen_not_compromised'),
    ).select_from(CaseAssets).join(
        alert_assets_association, alert_assets_association.c.asset_id == CaseAssets.asset_id
    ).join(Alert, Alert.alert_id == alert_assets_association.c.alert_id).where(
        tuple_(Alert.alert_customer_id, normalized, CaseAssets.asset_type_id).in_(keys)
    ).group_by(Alert.alert_customer_id, normalized, CaseAssets.asset_type_id)

    client_ids = scope.get_client_ids()
    if client_ids is not None:
        alert_stmt = alert_stmt.where(Alert.alert_customer_id.in_(list(client_ids)))

    for stmt, count_field in ((case_stmt, 'case_sighting_count'),
                              (alert_stmt, 'alert_sighting_count')):
        for row in db.session.execute(stmt):
            entry = summary.get((row.client_id, row.normalized_name, row.asset_type_id))
            if entry is None:
                continue
            entry[count_field] = row.sighting_count or 0
            entry['first_seen_at'] = _min_datetime(entry['first_seen_at'], row.first_seen_at)
            entry['last_seen_at'] = _max_datetime(entry['last_seen_at'], row.last_seen_at)
            entry['compromised_at'] = _min_datetime(entry['compromised_at'], row.compromised_at)
            entry['seen_compromised'] = entry['seen_compromised'] or bool(row.seen_compromised)
            entry['seen_not_compromised'] = (
                entry['seen_not_compromised'] or bool(row.seen_not_compromised)
            )

    return summary


def _min_datetime(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_datetime(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def managed_assets_db_sightings(asset, scope, kind, pagination_parameters: PaginationParameters):
    """Paginated list of the individual observations the caller may see.

    A UNION of the case and alert branches, ordered newest first. The
    two branches must project identical column types — `kind` is a
    literal discriminator and every id is cast to a common width so the
    UNION does not fail on a type mismatch under an empty result set.
    """
    selects = []

    if kind in (None, 'case') and scope.has_case_access():
        case_select = select(
            literal('case').label('kind'),
            cast(Cases.case_id, Integer).label('reference_id'),
            Cases.name.label('reference_name'),
            CaseAssets.asset_id.label('observation_id'),
            CaseAssets.asset_compromise_status_id.label('compromise_status_id'),
            CaseAssets.asset_description.label('observation_description'),
            CaseAssets.asset_tags.label('observation_tags'),
            func.coalesce(CaseAssets.date_added, Cases.initial_date).label('seen_at'),
        ).select_from(CaseAssets).join(
            Cases, Cases.case_id == CaseAssets.case_id
        ).where(and_(
            _normalized_case_asset_name() == asset.normalized_name,
            CaseAssets.asset_type_id == asset.asset_type_id,
            Cases.client_id == asset.client_id,
        ))
        case_ids = scope.get_case_ids()
        if case_ids is not None:
            case_select = case_select.where(CaseAssets.case_id.in_(case_ids))
        selects.append(case_select)

    if kind in (None, 'alert'):
        alert_select = select(
            literal('alert').label('kind'),
            cast(Alert.alert_id, Integer).label('reference_id'),
            Alert.alert_title.label('reference_name'),
            CaseAssets.asset_id.label('observation_id'),
            CaseAssets.asset_compromise_status_id.label('compromise_status_id'),
            CaseAssets.asset_description.label('observation_description'),
            CaseAssets.asset_tags.label('observation_tags'),
            Alert.alert_source_event_time.label('seen_at'),
        ).select_from(CaseAssets).join(
            alert_assets_association,
            alert_assets_association.c.asset_id == CaseAssets.asset_id
        ).join(Alert, Alert.alert_id == alert_assets_association.c.alert_id).where(and_(
            _normalized_case_asset_name() == asset.normalized_name,
            CaseAssets.asset_type_id == asset.asset_type_id,
            Alert.alert_customer_id == asset.client_id,
        ))
        client_ids = scope.get_client_ids()
        if client_ids is not None:
            alert_select = alert_select.where(Alert.alert_customer_id.in_(list(client_ids)))
        selects.append(alert_select)

    if not selects:
        return _paginate_rows(
            select(ManagedAsset.managed_asset_id).where(ManagedAsset.managed_asset_id == -1),
            1, 1,
        )

    combined = union_all(*selects).subquery() if len(selects) > 1 else selects[0].subquery()
    stmt = select(combined).order_by(
        combined.c.seen_at.desc().nullslast(),
        combined.c.observation_id.desc(),
    )

    return _paginate_rows(
        stmt,
        pagination_parameters.get_page(),
        pagination_parameters.get_per_page(),
    )


def managed_assets_db_timeline(asset, scope, pagination_parameters: PaginationParameters):
    """Case timeline events that reference any visible sighting.

    Timeline events only ever belong to a case — there is no alert
    branch — so this is scoped purely on case access.
    """
    if not scope.has_case_access():
        return _paginate_rows(
            select(CasesEvent.event_id).where(CasesEvent.event_id == -1),
            1, 1,
        )

    stmt = select(
        CasesEvent.event_id.label('event_id'),
        CasesEvent.event_title.label('event_title'),
        CasesEvent.event_content.label('event_content'),
        CasesEvent.event_date.label('event_date'),
        CasesEvent.event_tz.label('event_tz'),
        CasesEvent.event_tags.label('event_tags'),
        CasesEvent.event_color.label('event_color'),
        CasesEvent.case_id.label('case_id'),
        Cases.name.label('case_name'),
    ).select_from(CaseEventsAssets).join(
        CasesEvent, CasesEvent.event_id == CaseEventsAssets.event_id
    ).join(
        Cases, Cases.case_id == CasesEvent.case_id
    ).where(
        CaseEventsAssets.asset_id.in_(_visible_case_sighting_ids(asset, scope))
    )

    case_ids = scope.get_case_ids()
    if case_ids is not None:
        # Belt and braces: the sighting subquery is already scoped, but
        # an event could in principle reference an asset row from a case
        # other than its own, so the event's own case is checked too.
        stmt = stmt.where(CasesEvent.case_id.in_(case_ids))

    # One event can reference the asset through several `case_assets`
    # rows — the same host recorded twice in a case under different
    # spellings collapses to one registry entry but stays two rows there
    # — so the join can repeat an event. Every selected column comes from
    # the event or its case, so a row-level DISTINCT is exactly a
    # distinct on `event_id`, which is what `..._timeline_count` reports.
    stmt = stmt.distinct().order_by(
        CasesEvent.event_date.desc().nullslast(), CasesEvent.event_id.desc()
    )

    return _paginate_rows(
        stmt,
        pagination_parameters.get_page(),
        pagination_parameters.get_per_page(),
    )


def managed_assets_db_timeline_count(asset, scope):
    if not scope.has_case_access():
        return 0
    stmt = select(func.count(distinct(CasesEvent.event_id))).select_from(CaseEventsAssets).join(
        CasesEvent, CasesEvent.event_id == CaseEventsAssets.event_id
    ).where(
        CaseEventsAssets.asset_id.in_(_visible_case_sighting_ids(asset, scope))
    )
    case_ids = scope.get_case_ids()
    if case_ids is not None:
        stmt = stmt.where(CasesEvent.case_id.in_(case_ids))
    return db.session.execute(stmt).scalar() or 0


_AUDIT_COLUMNS = (
    ManagedAssetAudit.audit_id,
    ManagedAssetAudit.managed_asset_id,
    ManagedAssetAudit.asset_name_snapshot,
    ManagedAssetAudit.action,
    ManagedAssetAudit.changes,
    ManagedAssetAudit.user_id,
    ManagedAssetAudit.user_login_snapshot,
    ManagedAssetAudit.source,
    ManagedAssetAudit.occurred_at,
)

_AUDIT_ORDER = (ManagedAssetAudit.occurred_at.desc(), ManagedAssetAudit.audit_id.desc())


def _paginate_audit(stmt, pagination_parameters: PaginationParameters):
    return _paginate_rows(
        stmt.order_by(*_AUDIT_ORDER),
        pagination_parameters.get_page(),
        pagination_parameters.get_per_page(),
    )


def managed_assets_db_audit(asset_identifier, pagination_parameters: PaginationParameters):
    stmt = select(*_AUDIT_COLUMNS).where(
        ManagedAssetAudit.managed_asset_id == asset_identifier
    )
    return _paginate_audit(stmt, pagination_parameters)


def managed_assets_db_audit_log(scope, client_filter, pagination_parameters: PaginationParameters):
    """The registry-wide change log, access-filtered on the denormalized
    `client_id`.

    That column is the reason a delete entry stays readable: the FK to
    the asset is `ON DELETE SET NULL`, so once the asset is gone there is
    nothing left to join to for the access check. Filtering on the
    customer the entry recorded at write time keeps the tenancy boundary
    intact without resurrecting the row.
    """
    if not scope.has_customer_access():
        return _paginate_audit(
            select(*_AUDIT_COLUMNS).where(ManagedAssetAudit.audit_id == -1),
            pagination_parameters,
        )

    stmt = select(*_AUDIT_COLUMNS)

    client_ids = scope.get_client_ids()
    if client_ids is not None:
        stmt = stmt.where(ManagedAssetAudit.client_id.in_(list(client_ids)))
    if client_filter:
        # Intersection with the scope above, never a replacement.
        stmt = stmt.where(ManagedAssetAudit.client_id.in_(client_filter))

    return _paginate_audit(stmt, pagination_parameters)


def managed_assets_db_export_rows(scope, filters, limit):
    """Rows for CSV/JSON export — registry columns only, no sightings.

    Capped by `limit`; the caller is responsible for treating a
    full-length result as "too many" and refusing rather than silently
    handing back a truncated inventory.
    """
    if not scope.has_customer_access():
        return []
    query = _apply_registry_filters(ManagedAsset.query, scope, filters)
    return query.order_by(
        ManagedAsset.client_id.asc(), ManagedAsset.name.asc()
    ).limit(limit).all()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def managed_assets_db_diff(asset):
    """Field-level `{field: {from, to}}` for the pending changes on `asset`.

    Reads SQLAlchemy's per-attribute change history, which is why this
    cannot live in the business layer. Must be called *before* the
    session is flushed — once flushed, the history is reset and every
    field reports unchanged.
    """
    changes = {}
    state = inspect(asset)
    for attribute in state.attrs:
        if attribute.key in ('created_at', 'updated_at', 'created_by', 'updated_by'):
            continue
        history = attribute.history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if before == after:
            continue
        changes[attribute.key] = {'from': _jsonable(before), 'to': _jsonable(after)}
    return changes


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool, dict, list)):
        return value
    return str(value)


def managed_assets_db_add_audit(asset, action, changes, user, source='api',
                                name_snapshot=None, client_id=None, asset_type_id=None):
    """Append one audit row. Committed by the caller.

    `asset` may be None for entries recorded about an asset that no
    longer exists — the snapshot arguments carry the identity forward.
    """
    entry = ManagedAssetAudit()
    entry.managed_asset_id = asset.managed_asset_id if asset is not None else None
    entry.client_id = client_id if client_id is not None else asset.client_id
    entry.asset_name_snapshot = name_snapshot if name_snapshot is not None else asset.name
    entry.asset_type_id = asset_type_id if asset_type_id is not None else (
        asset.asset_type_id if asset is not None else None
    )
    entry.action = action
    entry.changes = changes or None
    entry.user_id = getattr(user, 'id', None)
    entry.user_login_snapshot = getattr(user, 'user', None)
    entry.source = source
    db.session.add(entry)
    return entry


def managed_assets_db_observe(client_id, asset_type_id, name, normalized_name):
    """Idempotently ensure a registry row exists for an observation.

    `ON CONFLICT DO NOTHING` on the identity unique constraint, so two
    concurrent ingests racing on the same host produce one row and no
    error. Returns True when a row was actually inserted.

    Never updates an existing row: an ingest must not be able to
    overwrite an analyst's curated metadata, and `updated_at` must not
    move on machine activity or it becomes a timing side channel for
    invisible cases.
    """
    stmt = text("""
        INSERT INTO managed_asset (client_id, asset_type_id, name, normalized_name, source)
        VALUES (:client_id, :asset_type_id, :name, :normalized_name, 'observed')
        ON CONFLICT (client_id, normalized_name, asset_type_id) DO NOTHING
        RETURNING managed_asset_id
    """)
    result = db.session.execute(stmt, {
        'client_id': client_id,
        'asset_type_id': asset_type_id,
        'name': name,
        'normalized_name': normalized_name,
    })
    return result.first() is not None


# The normalisation twin of `managed_assets_normalize_name`. Written out
# as SQL so the INSERT ... SELECT variants below can populate
# `normalized_name` without round-tripping every row through Python.
_NORMALIZE_SQL = "lower(btrim(regexp_replace(ca.asset_name, '\\s+', ' ', 'g')))"

# Shared skeleton for every bulk observe. `{source_clause}` supplies the
# customer attribution and the restriction (whole customer, one case, or
# one alert); everything else — the dedup, the empty-name guard, the
# conflict handling — is identical and should stay that way.
#
# `case_assets.asset_name` is an uncapped Text column while the registry
# caps names at MANAGED_ASSET_NAME_MAX_LENGTH, so the two length rules
# here are load-bearing, not cosmetic — without them one oversized asset
# name aborts an entire ingest or reconcile on `ck_managed_asset_name_length`.
# A name that is only over the limit because of whitespace padding is cut
# to fit; `normalized_name` is never cut, because it is what resolves a
# registry row to its sightings and a truncated one would match nothing.
# An observation whose normalised name genuinely exceeds the limit is
# therefore skipped rather than stored under a mangled identity.
_OBSERVE_INSERT_SQL = f"""
    INSERT INTO managed_asset (client_id, asset_type_id, name, normalized_name, source)
    SELECT DISTINCT ON (src.client_id, src.normalized_name, src.asset_type_id)
           src.client_id, src.asset_type_id,
           left(src.name, {MANAGED_ASSET_NAME_MAX_LENGTH}), src.normalized_name, 'observed'
    FROM ({{source_clause}}) AS src
    WHERE length(src.normalized_name) > 0
      AND length(src.normalized_name) <= {MANAGED_ASSET_NAME_MAX_LENGTH}
    ORDER BY src.client_id, src.normalized_name, src.asset_type_id, src.name
    ON CONFLICT (client_id, normalized_name, asset_type_id) DO NOTHING
"""

_CASE_SOURCE_SQL = f"""
    SELECT c.client_id AS client_id,
           ca.asset_type_id AS asset_type_id,
           ca.asset_name AS name,
           {_NORMALIZE_SQL} AS normalized_name
    FROM case_assets ca
    JOIN cases c ON c.case_id = ca.case_id
    WHERE {{predicate}}
      AND ca.asset_name IS NOT NULL
      AND ca.asset_type_id IS NOT NULL
"""

_ALERT_SOURCE_SQL = f"""
    SELECT a.alert_customer_id AS client_id,
           ca.asset_type_id AS asset_type_id,
           ca.asset_name AS name,
           {_NORMALIZE_SQL} AS normalized_name
    FROM case_assets ca
    JOIN alert_assets_association aaa ON aaa.asset_id = ca.asset_id
    JOIN alerts a ON a.alert_id = aaa.alert_id
    WHERE {{predicate}}
      AND ca.asset_name IS NOT NULL
      AND ca.asset_type_id IS NOT NULL
"""


def managed_assets_db_observe_case(case_identifier):
    """Register every asset currently attached to one case.

    Used by the escalate / merge paths, where assets arrive in bulk and
    may be created by `create_case_from_alert` rather than by the normal
    asset-create route. A set-based statement cannot miss one the way an
    ORM-side loop over a stale collection can.
    """
    source = _CASE_SOURCE_SQL.format(predicate='ca.case_id = :case_id')
    result = db.session.execute(
        text(_OBSERVE_INSERT_SQL.format(source_clause=source)),
        {'case_id': int(case_identifier)},
    )
    return result.rowcount or 0


def managed_assets_db_observe_alert(alert_identifier):
    """Register every asset attached to one alert."""
    source = _ALERT_SOURCE_SQL.format(predicate='a.alert_id = :alert_id')
    result = db.session.execute(
        text(_OBSERVE_INSERT_SQL.format(source_clause=source)),
        {'alert_id': int(alert_identifier)},
    )
    return result.rowcount or 0


def managed_assets_db_reconcile(client_id):
    """Backfill any registry rows the observe hooks missed, for one customer.

    Serialised per-customer with a transaction-scoped advisory lock so
    two concurrent reconciles do not both scan the same customer. The
    lock is keyed on a constant namespace plus the customer id, and is
    released automatically at commit/rollback.
    """
    db.session.execute(
        text('SELECT pg_advisory_xact_lock(:namespace, :client_id)'),
        {'namespace': 0x1A55E7, 'client_id': int(client_id)},
    )

    source = '\n UNION ALL \n'.join((
        _CASE_SOURCE_SQL.format(predicate='c.client_id = :client_id'),
        _ALERT_SOURCE_SQL.format(predicate='a.alert_customer_id = :client_id'),
    ))
    result = db.session.execute(
        text(_OBSERVE_INSERT_SQL.format(source_clause=source)),
        {'client_id': int(client_id)},
    )
    return result.rowcount or 0


def managed_assets_db_savepoint():
    """A SAVEPOINT the caller can use as a context manager.

    Observe hooks run in the middle of somebody else's transaction. A
    plain `rollback()` on failure would throw away the caller's pending
    work — an alert half-created because a registry insert tripped a
    constraint. A savepoint contains the damage to the observe itself,
    which is the only part that is allowed to fail silently.
    """
    return db.session.begin_nested()


def managed_assets_db_add(row):
    db.session.add(row)


def managed_assets_db_flush():
    """Assign primary keys to pending rows without ending the transaction.

    The import needs the new asset ids to write audit entries that point
    at them, but must still be able to roll the whole file back as one
    unit if a later row fails.
    """
    db.session.flush()


def managed_assets_db_commit():
    db.session.commit()


def managed_assets_db_rollback():
    db.session.rollback()


def managed_assets_db_delete(asset):
    db.session.delete(asset)


def managed_assets_db_is_integrity_error(exception):
    """True when `exception` is a constraint violation the caller can explain.

    Keeps `sqlalchemy.exc` out of the business layer, which the import
    contracts forbid from importing sqlalchemy at all. In practice this
    is the identity UNIQUE losing a race, or a CHECK rejecting a value
    the schema validator somehow let through.
    """
    return isinstance(exception, IntegrityError)


def managed_assets_db_get_many_by_identity(client_id, identities):
    """Resolve many `(normalized_name, asset_type_id)` pairs in one query.

    An import may carry twenty thousand rows; probing each one
    individually would be twenty thousand round trips before a single
    write happens.
    """
    keys = list(identities)
    if not keys:
        return {}

    found = {}
    for start in range(0, len(keys), _IDENTITY_LOOKUP_CHUNK):
        chunk = keys[start:start + _IDENTITY_LOOKUP_CHUNK]
        rows = ManagedAsset.query.filter(
            ManagedAsset.client_id == client_id,
            tuple_(ManagedAsset.normalized_name, ManagedAsset.asset_type_id).in_(chunk),
        ).all()
        for row in rows:
            found[(row.normalized_name, row.asset_type_id)] = row
    return found


def managed_assets_db_client_names(client_ids):
    """Map customer ids to names for export rendering, in one query."""
    ids = [i for i in client_ids if i is not None]
    if not ids:
        return {}
    rows = Client.query.with_entities(Client.client_id, Client.name).filter(
        Client.client_id.in_(ids)
    ).all()
    return {row[0]: row[1] for row in rows}


def managed_assets_db_resolve_user_logins(user_ids):
    """Map user ids to logins for audit rendering, in one query."""
    ids = [i for i in user_ids if i is not None]
    if not ids:
        return {}
    rows = User.query.with_entities(User.id, User.user).filter(User.id.in_(ids)).all()
    return {row[0]: row[1] for row in rows}


def managed_assets_db_compromise_status(seen_compromised, seen_not_compromised, has_sightings):
    """Collapse the visible compromise flags into a single status id.

    Compromised wins over not-compromised: in a DFIR inventory a single
    confirmed compromise is the fact that matters, and reporting
    "not compromised" because most sightings were clean would be
    actively misleading.
    """
    if seen_compromised:
        return CompromiseStatus.compromised.value
    if seen_not_compromised:
        return CompromiseStatus.not_compromised.value
    if has_sightings:
        return CompromiseStatus.to_be_determined.value
    return None
