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

"""Declarative description of what a case is made of, for transfer purposes.

Every field is listed explicitly rather than introspected off the mapper. That
is deliberate: a column added later should have to be considered and named here
before it travels between instances, instead of silently joining the payload —
which is how credentials, internal counters and instance-local ids end up in
export files.

`ENTITIES` is in dependency order. The importer inserts in that order and
resolves refs as it goes, so a spec may only point at entities declared before
it. Self-references (a folder's parent folder, an event's parent event) are the
one exception and are patched in a second pass once the whole entity is in.
"""

from app.models.alerts import Severity
from app.models.assets import AnalysisStatus
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.cases import CaseClassification
from app.models.cases import CaseEventTimeline
from app.models.cases import CaseState
from app.models.cases import CaseProtagonist
from app.models.cases import CaseTags
from app.models.cases import CaseTimeline
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.comments import AssetComments
from app.models.comments import Comments
from app.models.comments import EventComments
from app.models.comments import EvidencesComments
from app.models.comments import IocComments
from app.models.comments import NotesComments
from app.models.comments import TaskComments
from app.models.customers import Client
from app.models.evidences import CaseReceivedFile
from app.models.evidences import EvidenceTypes
from app.models.iocs import Ioc
from app.models.iocs import Tlp
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import CaseKanban
from app.models.models import CaseTasks
from app.models.models import DataStoreFile
from app.models.models import DataStorePath
from app.models.models import EventCategory
from app.models.models import IocAssetLink
from app.models.models import IocType
from app.models.models import NoteDirectory
from app.models.models import NoteRevisions
from app.models.models import Notes
from app.models.models import NotesGroup
from app.models.models import NotesGroupLink
from app.models.models import ObjectState
from app.models.models import ReviewStatus
from app.models.models import Tags
from app.models.models import TaskAssignee
from app.models.models import TaskStatus

USER_REF_PREFIX = 'user'


class LookupSpec:
    """A reference-data table resolved by name on the target instance.

    `creatable` is False for tables whose rows carry behaviour rather than just
    a label — inventing a `case_state` or a `task_status` on the fly would
    produce a case the target's own workflows cannot reason about.
    """

    def __init__(self, key, model, pk, name_column, extra_columns=(), creatable=True):
        self.key = key
        self.model = model
        self.pk = pk
        self.name_column = name_column
        self.extra_columns = tuple(extra_columns)
        self.creatable = creatable


class EntitySpec:
    """One case-scoped table.

    fields        scalar columns copied verbatim
    user_refs     columns holding a `user.id`, rewritten to `user:<id>`
    entity_refs   {column: entity key} for FKs to another exported entity
    lookup_refs   {column: lookup key} for FKs to reference data
    self_refs     columns pointing at this same entity, patched in a second pass
    case_column   column holding `cases.case_id`, repointed at the new case
    blob_column   column naming this row's blob inside the archive, exported as
                  `_blob` — the row's own id is worthless to the target, so the
                  bundle needs a stable name to look the bytes up by
    """

    def __init__(self, key, model, pk, case_column=None, fields=(), user_refs=(),
                 entity_refs=None, lookup_refs=None, self_refs=(), blob_column=None):
        self.key = key
        self.model = model
        self.pk = pk
        self.case_column = case_column
        self.fields = tuple(fields)
        self.user_refs = tuple(user_refs)
        self.entity_refs = dict(entity_refs or {})
        self.lookup_refs = dict(lookup_refs or {})
        self.self_refs = tuple(self_refs)
        self.blob_column = blob_column


_LOOKUP_SPECS = (
    LookupSpec('customer', Client, 'client_id', 'name', extra_columns=('description', 'sla')),
    LookupSpec('case_state', CaseState, 'state_id', 'state_name', creatable=False),
    LookupSpec('classification', CaseClassification, 'id', 'name',
               extra_columns=('name_expanded', 'description')),
    LookupSpec('severity', Severity, 'severity_id', 'severity_name',
               extra_columns=('severity_description',)),
    LookupSpec('review_status', ReviewStatus, 'id', 'status_name', creatable=False),
    LookupSpec('tag', Tags, 'id', 'tag_title', extra_columns=('tag_namespace',)),
    LookupSpec('ioc_type', IocType, 'type_id', 'type_name',
               extra_columns=('type_description', 'type_taxonomy', 'type_validation_regex',
                              'type_validation_expect')),
    LookupSpec('tlp', Tlp, 'tlp_id', 'tlp_name', extra_columns=('tlp_bscolor',)),
    LookupSpec('asset_type', AssetsType, 'asset_id', 'asset_name',
               extra_columns=('asset_description', 'asset_icon_not_compromised',
                              'asset_icon_compromised')),
    LookupSpec('analysis_status', AnalysisStatus, 'id', 'name', creatable=False),
    LookupSpec('task_status', TaskStatus, 'id', 'status_name',
               extra_columns=('status_description', 'status_bscolor'), creatable=False),
    LookupSpec('evidence_type', EvidenceTypes, 'id', 'name', extra_columns=('description',)),
    LookupSpec('event_category', EventCategory, 'id', 'name'),
)

LOOKUPS = {spec.key: spec for spec in _LOOKUP_SPECS}


# The case row itself. Written by a dedicated code path (owner, ACL and uuid are
# decided by the target, not the bundle) but declared here so its lookup and
# user references are collected by the same walk as everything else.
CASE_SPEC = EntitySpec(
    'case', Cases, 'case_id',
    fields=('soc_id', 'name', 'description', 'open_date', 'close_date', 'initial_date',
            'closing_note', 'status_id', 'custom_attributes', 'modification_history'),
    user_refs=('user_id', 'owner_id', 'reviewer_id'),
    lookup_refs={'client_id': 'customer', 'state_id': 'case_state',
                 'classification_id': 'classification', 'severity_id': 'severity',
                 'review_status_id': 'review_status'},
)


ENTITIES = [
    EntitySpec('case_tag', CaseTags, None, case_column='case_id',
               lookup_refs={'tag_id': 'tag'}),

    EntitySpec('protagonist', CaseProtagonist, 'id', case_column='case_id',
               fields=('name', 'contact', 'role'),
               user_refs=('user_id',)),

    EntitySpec('kanban', CaseKanban, None, case_column='case_id',
               fields=('kanban_data',)),

    EntitySpec('timeline', CaseTimeline, 'timeline_id', case_column='case_id',
               fields=('name', 'description', 'color', 'is_default', 'created_at'),
               user_refs=('created_by_id',)),

    EntitySpec('note_directory', NoteDirectory, 'id', case_column='case_id',
               fields=('name',),
               self_refs=('parent_id',)),

    EntitySpec('note', Notes, 'note_id', case_column='note_case_id',
               fields=('note_title', 'note_content', 'note_creationdate', 'note_lastupdate',
                       'custom_attributes', 'modification_history'),
               user_refs=('note_user',),
               entity_refs={'directory_id': 'note_directory'}),

    EntitySpec('note_revision', NoteRevisions, 'revision_id',
               fields=('revision_number', 'note_title', 'note_content', 'revision_timestamp'),
               user_refs=('note_user',),
               entity_refs={'note_id': 'note'}),

    EntitySpec('notes_group', NotesGroup, 'group_id', case_column='group_case_id',
               fields=('group_title', 'group_creationdate', 'group_lastupdate'),
               user_refs=('group_user',)),

    EntitySpec('notes_group_link', NotesGroupLink, 'link_id', case_column='case_id',
               entity_refs={'group_id': 'notes_group', 'note_id': 'note'}),

    EntitySpec('asset', CaseAssets, 'asset_id', case_column='case_id',
               fields=('asset_name', 'asset_description', 'asset_domain', 'asset_ip', 'asset_info',
                       'asset_compromise_status_id', 'asset_tags', 'date_added', 'date_update',
                       'custom_attributes', 'asset_enrichment', 'modification_history'),
               user_refs=('user_id',),
               lookup_refs={'asset_type_id': 'asset_type', 'analysis_status_id': 'analysis_status'}),

    EntitySpec('ioc', Ioc, 'ioc_id', case_column='case_id',
               fields=('ioc_value', 'ioc_description', 'ioc_tags', 'ioc_misp',
                       'custom_attributes', 'ioc_enrichment', 'modification_history'),
               user_refs=('user_id',),
               lookup_refs={'ioc_type_id': 'ioc_type', 'ioc_tlp_id': 'tlp'}),

    EntitySpec('ioc_asset_link', IocAssetLink, 'ioc_asset_link_id',
               entity_refs={'ioc_id': 'ioc', 'asset_id': 'asset'}),

    EntitySpec('event', CasesEvent, 'event_id', case_column='case_id',
               fields=('event_title', 'event_source', 'event_content', 'event_raw', 'event_date',
                       'event_added', 'event_in_graph', 'event_in_summary', 'modification_history',
                       'event_color', 'event_tags', 'event_tz', 'event_date_wtz',
                       'event_is_flagged', 'custom_attributes'),
               user_refs=('user_id',),
               self_refs=('parent_event_id',)),

    EntitySpec('event_timeline', CaseEventTimeline, None,
               entity_refs={'event_id': 'event', 'timeline_id': 'timeline'}),

    EntitySpec('event_category', CaseEventCategory, 'id',
               entity_refs={'event_id': 'event'},
               lookup_refs={'category_id': 'event_category'}),

    EntitySpec('event_asset', CaseEventsAssets, 'id', case_column='case_id',
               entity_refs={'event_id': 'event', 'asset_id': 'asset'}),

    EntitySpec('event_ioc', CaseEventsIoc, 'id', case_column='case_id',
               entity_refs={'event_id': 'event', 'ioc_id': 'ioc'}),

    EntitySpec('task', CaseTasks, 'id', case_column='task_case_id',
               fields=('task_title', 'task_description', 'task_tags', 'task_open_date',
                       'task_close_date', 'task_last_update', 'custom_attributes',
                       'modification_history'),
               user_refs=('task_userid_open', 'task_userid_close', 'task_userid_update'),
               lookup_refs={'task_status_id': 'task_status'}),

    EntitySpec('task_assignee', TaskAssignee, 'id',
               user_refs=('user_id',),
               entity_refs={'task_id': 'task'}),

    EntitySpec('evidence', CaseReceivedFile, 'id', case_column='case_id',
               fields=('filename', 'date_added', 'acquisition_date', 'file_hash',
                       'file_description', 'file_size', 'start_date', 'end_date',
                       'custom_attributes', 'chain_of_custody', 'modification_history'),
               user_refs=('user_id',),
               lookup_refs={'type_id': 'evidence_type'}),

    EntitySpec('comment', Comments, 'comment_id', case_column='comment_case_id',
               fields=('comment_text', 'comment_date', 'comment_update_date'),
               user_refs=('comment_user_id',)),

    EntitySpec('event_comment', EventComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_event_id': 'event'}),

    EntitySpec('task_comment', TaskComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_task_id': 'task'}),

    EntitySpec('ioc_comment', IocComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_ioc_id': 'ioc'}),

    EntitySpec('asset_comment', AssetComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_asset_id': 'asset'}),

    EntitySpec('evidence_comment', EvidencesComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_evidence_id': 'evidence'}),

    EntitySpec('note_comment', NotesComments, 'id',
               entity_refs={'comment_id': 'comment', 'comment_note_id': 'note'}),

    EntitySpec('dspath', DataStorePath, 'path_id', case_column='path_case_id',
               fields=('path_name', 'path_is_root'),
               self_refs=('path_parent_id',)),

    # `file_local_name` is deliberately absent: it is an absolute path on the
    # source filesystem and the target must compute its own. `file_uuid` is
    # regenerated too, but it names the blob inside the archive, so it travels
    # as `_blob` rather than as a column the importer would write back.
    EntitySpec('dsfile', DataStoreFile, 'file_id', case_column='file_case_id',
               fields=('file_original_name', 'file_description', 'file_date_added', 'file_tags',
                       'file_size', 'file_is_ioc', 'file_is_evidence', 'file_password',
                       'file_sha256', 'modification_history'),
               user_refs=('added_by_user_id',),
               entity_refs={'file_parent_id': 'dspath'},
               blob_column='file_uuid'),

    EntitySpec('object_state', ObjectState, 'object_id', case_column='object_case_id',
               fields=('object_name', 'object_state', 'object_last_update'),
               user_refs=('object_updated_by_id',)),
]

ENTITIES_BY_KEY = {spec.key: spec for spec in ENTITIES}
