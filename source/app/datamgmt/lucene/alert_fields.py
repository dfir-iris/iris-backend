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

"""The vocabulary the alert search bar speaks.

One table, read by three consumers: the compiler (to turn an alias into
a column or an EXISTS), the `search-schema` route (to feed autocomplete)
and the OpenAPI description. Adding a searchable field means adding a
row here and nothing else — which is the point, since an alias the
compiler knows but autocomplete does not is an alias nobody finds.

Aliases are what an analyst would say out loud (`owner`, `severity`,
`asset`) rather than what the column is called (`alert_owner_id`). Raw
column names still resolve as an escape hatch, so nothing is reachable
through the grid that is not reachable from the bar.
"""

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional

from app.models.alerts import Alert
from app.models.alerts import AlertCaseAssociation
from app.models.alerts import AlertResolutionStatus
from app.models.alerts import AlertStatus
from app.models.alerts import Severity
from app.models.alert_clusters import AlertClusterAssociation
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import CaseClassification
from app.models.comments import Comments
from app.models.customers import Client
from app.models.iocs import Ioc

#: How the compiler treats a field's values.
KIND_TEXT = 'text'
KIND_INTEGER = 'integer'
KIND_UUID = 'uuid'
KIND_DATE = 'date'
KIND_ENUM = 'enum'
KIND_USER = 'user'
KIND_MEMBERSHIP = 'membership'
KIND_RELATION = 'relation'
KIND_JSON = 'json'
KIND_MACRO = 'macro'

#: Statuses that mean an alert has left the triage queue. Lives here, in
#: one place, rather than in the frontend constant it replaces: "is this
#: alert still open" is a property of the data, and a second copy of the
#: list in the browser is a second copy to get out of step.
TERMINAL_ALERT_STATUS_NAMES = ('closed', 'merged', 'dismissed', 'escalated')

#: Columns a field-less term searches, OR'd together. Kept short on
#: purpose: every entry is another `ILIKE '%…%'` in the plan, and these
#: six are where an analyst's words actually are.
DEFAULT_SEARCH_COLUMNS = (
    'alert_title',
    'alert_description',
    'alert_source',
    'alert_source_ref',
    'alert_tags',
    'alert_note',
)

#: Values `owner:` accepts besides a login. `me` is resolved against the
#: caller, so a saved filter saying `owner:me` follows whoever opens it.
OWNER_SELF = 'me'
VALUE_NONE = 'none'


@dataclass(frozen=True)
class EnumLookup:
    """How to turn a name an analyst typed into the id a column holds."""

    model: type
    identifier: str
    name: str
    #: Further columns accepted as a name — `owner:jdoe` should work on
    #: the login, the display name or the mail address, because an
    #: analyst does not know which one the row was created with.
    alternates: tuple = ()
    #: Whether a failed match may suggest real values back to the caller.
    #: Off for customers: the suggestion would name clients the caller is
    #: not entitled to see, turning a typo into tenant enumeration.
    suggestable: bool = True


@dataclass(frozen=True)
class AlertField:
    """One searchable alias."""

    alias: str
    kind: str
    description: str
    #: Attribute on `Alert`, for everything that is a plain column.
    column: Optional[str] = None
    lookup: Optional[EnumLookup] = None
    #: Relationship attribute on `Alert`, compiled with `.any()`.
    relation: Optional[str] = None
    #: `(model, column)` pairs the relation's value is matched against.
    relation_columns: tuple = ()
    #: Aliases that mean exactly this field.
    synonyms: tuple = ()
    #: Whether `>=` / `[a TO b]` mean anything on this field. Severity
    #: ids run least→most severe so they order; status ids are insertion
    #: order and do not, and `status:>=Closed` answering by row id would
    #: be a wrong answer delivered confidently.
    ordered: bool = False

    def is_enumerable(self) -> bool:
        """True when the client can offer the full value list up front."""
        return self.kind in (KIND_ENUM, KIND_MACRO)


#: `is:` values. Each is a predicate the compiler owns, because each is a
#: question with a fixed answer rather than a value to match: "still in
#: the queue" is four status names, not a status.
IS_MACROS = {
    'open': 'Alert has not reached a terminal status',
    'closed': 'Alert reached a terminal status (closed, merged, dismissed, escalated)',
    'assigned': 'Alert has an owner',
    'unassigned': 'Alert has no owner',
    'escalated': 'Alert was escalated to a case',
    'merged': 'Alert was merged into an existing case',
    'resolved': 'Alert carries a resolution status',
    'unresolved': 'Alert carries no resolution status',
    'clustered': 'Alert belongs to at least one alert cluster',
    'orphan': 'Alert belongs to no alert cluster',
    'in_case': 'Alert is linked to at least one case',
    'no_case': 'Alert is linked to no case',
}

_STATUS_LOOKUP = EnumLookup(AlertStatus, 'status_id', 'status_name')
_SEVERITY_LOOKUP = EnumLookup(Severity, 'severity_id', 'severity_name')
_RESOLUTION_LOOKUP = EnumLookup(
    AlertResolutionStatus, 'resolution_status_id', 'resolution_status_name'
)
_CLASSIFICATION_LOOKUP = EnumLookup(
    CaseClassification, 'id', 'name', alternates=('name_expanded',)
)
_CUSTOMER_LOOKUP = EnumLookup(Client, 'client_id', 'name', suggestable=False)
_OWNER_LOOKUP = EnumLookup(User, 'id', 'user', alternates=('name', 'email'))

_FIELDS = (
    AlertField('title', KIND_TEXT, 'Alert title', column='alert_title'),
    AlertField('description', KIND_TEXT, 'Alert description',
               column='alert_description', synonyms=('desc',)),
    AlertField('note', KIND_TEXT, 'Analyst note on the alert', column='alert_note'),
    AlertField('source', KIND_TEXT, 'System the alert came from', column='alert_source'),
    AlertField('ref', KIND_TEXT, 'Source reference of the alert',
               column='alert_source_ref', synonyms=('source_ref',)),
    AlertField('link', KIND_TEXT, 'Source link of the alert', column='alert_source_link'),
    AlertField('tag', KIND_TEXT, 'Alert tags', column='alert_tags', synonyms=('tags',)),

    AlertField('id', KIND_INTEGER, 'Alert identifier', column='alert_id', ordered=True),
    AlertField('uuid', KIND_UUID, 'Alert UUID', column='alert_uuid'),

    AlertField('status', KIND_ENUM, 'Alert status', column='alert_status_id',
               lookup=_STATUS_LOOKUP),
    AlertField('severity', KIND_ENUM, 'Alert severity', column='alert_severity_id',
               lookup=_SEVERITY_LOOKUP, ordered=True),
    AlertField('resolution', KIND_ENUM, 'Alert resolution status',
               column='alert_resolution_status_id', lookup=_RESOLUTION_LOOKUP),
    AlertField('classification', KIND_ENUM, 'Alert classification',
               column='alert_classification_id', lookup=_CLASSIFICATION_LOOKUP),
    AlertField('customer', KIND_ENUM, 'Customer the alert belongs to',
               column='alert_customer_id', lookup=_CUSTOMER_LOOKUP, synonyms=('client',)),
    AlertField('owner', KIND_USER, "Alert owner — a login, 'me' or 'none'",
               column='alert_owner_id', lookup=_OWNER_LOOKUP, synonyms=('assignee',)),

    AlertField('created', KIND_DATE, 'When the alert was created in IRIS',
               column='alert_creation_time', ordered=True),
    AlertField('event_time', KIND_DATE, 'When the event behind the alert happened',
               column='alert_source_event_time', synonyms=('seen',), ordered=True),
    AlertField('updated', KIND_DATE, 'When the alert was last modified',
               column='date_update', ordered=True),
    AlertField('resolved', KIND_DATE, 'When the alert was resolved',
               column='resolved_at', ordered=True),

    AlertField('case', KIND_MEMBERSHIP, "Case the alert is linked to, or 'none'",
               relation='cases',
               relation_columns=((AlertCaseAssociation, 'case_id'),)),
    AlertField('cluster', KIND_MEMBERSHIP, "Alert cluster the alert belongs to, or 'none'",
               relation='clusters',
               relation_columns=((AlertClusterAssociation, 'cluster_id'),)),

    AlertField('asset', KIND_RELATION, 'Name of an asset attached to the alert',
               relation='assets', relation_columns=((CaseAssets, 'asset_name'),)),
    AlertField('asset_ip', KIND_RELATION, 'IP of an asset attached to the alert',
               relation='assets', relation_columns=((CaseAssets, 'asset_ip'),)),
    AlertField('asset_domain', KIND_RELATION, 'Domain of an asset attached to the alert',
               relation='assets', relation_columns=((CaseAssets, 'asset_domain'),)),
    AlertField('ioc', KIND_RELATION, 'Value of an IOC attached to the alert',
               relation='iocs', relation_columns=((Ioc, 'ioc_value'),)),
    AlertField('comment', KIND_RELATION, 'Text of a comment on the alert',
               relation='comments', relation_columns=((Comments, 'comment_text'),)),

    AlertField('context', KIND_JSON, 'Path into the alert context document',
               column='alert_context', ordered=True),
    AlertField('raw', KIND_JSON, 'Path into the raw source event',
               column='alert_source_content', ordered=True),

    AlertField('is', KIND_MACRO, 'Alert state shorthand'),
)

_BY_ALIAS = {}
for _field in _FIELDS:
    _BY_ALIAS[_field.alias] = _field
    for _synonym in _field.synonyms:
        _BY_ALIAS[_synonym] = _field


def alert_field_for_alias(alias: str) -> Optional[AlertField]:
    """The field an alias names, or `None` if nothing does.

    A dotted alias resolves on its head (`context.rule_name` →
    `context`), and only for JSON fields: a dot is otherwise not part of
    any alias, and letting `title.foo` fall back to `title` would answer
    a question the user did not ask.
    """
    found = _BY_ALIAS.get(alias)
    if found is not None:
        return found

    head = alias.split('.', 1)[0]
    candidate = _BY_ALIAS.get(head)
    if candidate is not None and candidate.kind == KIND_JSON:
        return candidate

    return None


def alert_field_raw_column(alias: str):
    """The `Alert` column an alias names literally, or `None`.

    The escape hatch: anything the grid can filter on stays reachable
    from the bar even before it earns a friendly alias.
    """
    if not alias.startswith('alert_') and alias not in ('date_update', 'resolved_at'):
        return None
    return getattr(Alert, alias, None)


def suggest_alert_field(alias: str) -> Optional[str]:
    """The alias the user most likely meant, for the error message.

    Only offered on a near miss — a suggestion that is not obviously
    right reads as noise on top of an error.
    """
    matches = get_close_matches(alias.lower(), list(_BY_ALIAS), n=1, cutoff=0.75)
    if matches:
        return matches[0]
    return None


def alert_field_catalogue() -> list:
    """The catalogue as plain data, for `GET /alerts/search-schema`.

    Values for enumerable fields are *not* included: they are rows, they
    are tenant-scoped, and the route already has cheaper ways to read
    them. This is the shape of the vocabulary, not its contents.
    """
    described = []
    seen = set()
    for entry in _FIELDS:
        if entry.alias in seen:
            continue
        seen.add(entry.alias)
        described.append({
            'alias': entry.alias,
            'synonyms': list(entry.synonyms),
            'kind': entry.kind,
            'description': entry.description,
            'enumerable': entry.is_enumerable(),
            'ordered': entry.ordered,
            'values': sorted(IS_MACROS) if entry.kind == KIND_MACRO else [],
        })
    return described
