#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

"""Database population for demo cases.

This module turns a scenario record from :mod:`app.iris_engine.demo_scenarios`
into rows: IOCs, assets, note directories and notes, timeline events, tasks,
evidence, comments, alerts and a war room, plus the case metadata (severity,
state, classification, tags, description).

Two design constraints shape everything here.

**Actor binding.** Seeding runs from ``post_init`` inside an app context but
*outside* a request context. A large number of the datamgmt helpers reach for
``iris_current_user`` — which is a ``LocalProxy`` that evaluates to ``None``
when there is no request context, so any attribute access on it raises
``AttributeError`` and takes the whole boot down with it. Rather than thread a
``userid`` through every call site, :func:`demo_actor` pushes a throwaway
request context and sets ``g.auth_user``, which makes
``app.blueprints.iris_user._get_current_user`` hand back a real ``TokenUser``.
Flask 3 reuses the already-pushed app context when a request context is pushed
for the same app, so the Flask-SQLAlchemy scoped session is preserved across
the boundary and the seeding transaction is unaffected.

**Idempotency.** ``post_init`` re-runs the seed block on every boot. Every
helper below checks for its own rows before writing, so a restart is a no-op
rather than a duplicate-data event. That also means a partially-seeded case
(the historical failure mode) gets backfilled on the next boot instead of
staying half-empty forever.

All writes go through the ORM directly instead of the business layer. The
business helpers emit activity tracking, websocket notifications and modification
history that are noise at seed time, and several of them raise on the kind of
bulk repetition seeding does.
"""

from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta

from flask import g

from app.db import db
from app.models.alerts import Alert
from app.models.alerts import AlertResolutionStatus
from app.models.alerts import AlertStatus
from app.models.alerts import Severity
from app.models.alerts import SimilarAlertsCache
from app.models.assets import AnalysisStatus
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.assets import CompromiseStatus
from app.models.cases import CaseClassification
from app.models.cases import CaseEventTimeline
from app.models.cases import CaseState
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.comments import AssetComments
from app.models.comments import Comments
from app.models.comments import IocComments
from app.models.comments import TaskComments
from app.models.evidences import CaseReceivedFile
from app.models.evidences import EvidenceTypes
from app.models.iocs import Ioc
from app.models.iocs import Tlp
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import CaseTasks
from app.models.models import EventCategory
from app.models.models import IocType
from app.models.models import NoteDirectory
from app.models.models import Notes
from app.models.models import TaskAssignee
from app.models.models import TaskStatus
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomChatMessage
from app.models.war_rooms import WarRoomMember
from app.models.war_rooms import WarRoomTopic

from app import app

log = app.logger


# --------------------------------------------------------------------------
# Actor binding
# --------------------------------------------------------------------------

@contextmanager
def demo_actor(app, user):
    """Bind ``user`` as the current principal for the enclosed block.

    ``post_init`` runs under an app context only. Pushing a test request
    context and populating ``g.auth_user`` is what makes ``iris_current_user``
    resolve — see the module docstring for why this is preferable to passing
    an explicit ``userid`` into every helper.

    ``user`` may be a ``User`` model instance or anything exposing ``id``,
    ``user``, ``name`` and ``email``.
    """
    with app.test_request_context('/'):
        g.auth_user = {
            'user_id': user.id,
            'user_login': user.user,
            'user_name': user.name,
            'user_email': user.email,
        }
        yield user


# --------------------------------------------------------------------------
# Reference data resolution
#
# Scenario records name reference data by string. Every lookup below falls
# back to something sane rather than raising, so a scenario that names a row
# an operator has since renamed degrades to a less specific case instead of
# breaking the boot.
# --------------------------------------------------------------------------

def _first_id(model, id_column):
    row = model.query.with_entities(id_column).first()
    return row[0] if row else None


def _asset_type_id(name, _cache={}):
    if name not in _cache:
        row = AssetsType.query.filter(AssetsType.asset_name == name).first()
        if row is None:
            row = AssetsType.query.filter(AssetsType.asset_name == 'Unspecified').first()
        _cache[name] = row.asset_id if row else _first_id(AssetsType, AssetsType.asset_id)
    return _cache[name]


def _ioc_type_id(name, _cache={}):
    if name not in _cache:
        row = IocType.query.filter(IocType.type_name == name).first()
        if row is None:
            row = IocType.query.filter(IocType.type_name == 'other').first()
        _cache[name] = row.type_id if row else _first_id(IocType, IocType.type_id)
    return _cache[name]


def _event_category_id(name, _cache={}):
    if name not in _cache:
        row = EventCategory.query.filter(EventCategory.name == name).first()
        if row is None:
            row = EventCategory.query.filter(EventCategory.name == 'Unspecified').first()
        _cache[name] = row.id if row else _first_id(EventCategory, EventCategory.id)
    return _cache[name]


def _severity_id(name, _cache={}):
    if name not in _cache:
        row = Severity.query.filter(Severity.severity_name == name).first()
        _cache[name] = row.severity_id if row else _first_id(Severity, Severity.severity_id)
    return _cache[name]


def _alert_status_id(name, _cache={}):
    if name not in _cache:
        row = AlertStatus.query.filter(AlertStatus.status_name == name).first()
        _cache[name] = row.status_id if row else _first_id(AlertStatus, AlertStatus.status_id)
    return _cache[name]


def _resolution_status_id(name, _cache={}):
    if not name:
        return None
    if name not in _cache:
        row = AlertResolutionStatus.query.filter(
            AlertResolutionStatus.resolution_status_name == name).first()
        _cache[name] = row.resolution_status_id if row else None
    return _cache[name]


def _task_status_id(name, _cache={}):
    if name not in _cache:
        row = TaskStatus.query.filter(TaskStatus.status_name == name).first()
        if row is None:
            row = TaskStatus.query.filter(TaskStatus.status_name == 'To do').first()
        _cache[name] = row.id if row else _first_id(TaskStatus, TaskStatus.id)
    return _cache[name]


def _case_state_id(name, _cache={}):
    if name not in _cache:
        row = CaseState.query.filter(CaseState.state_name == name).first()
        _cache[name] = row.state_id if row else None
    return _cache[name]


def _classification_id(name, _cache={}):
    if name not in _cache:
        row = CaseClassification.query.filter(CaseClassification.name == name).first()
        _cache[name] = row.id if row else None
    return _cache[name]


def _analysis_status_id(name, _cache={}):
    if name not in _cache:
        row = AnalysisStatus.query.filter(AnalysisStatus.name == name).first()
        _cache[name] = row.id if row else _first_id(AnalysisStatus, AnalysisStatus.id)
    return _cache[name]


def _amber_tlp_id(_cache={}):
    if 'id' not in _cache:
        row = Tlp.query.filter(Tlp.tlp_name == 'amber').first()
        _cache['id'] = row.tlp_id if row else _first_id(Tlp, Tlp.tlp_id)
    return _cache['id']


def _evidence_type_id(name, _cache={}):
    """Resolve an evidence type, creating it if the deployment lacks it.

    Unlike asset types, ``evidence_type`` rows carry no icon files, so adding
    one costs nothing and is preferable to collapsing every demo artefact onto
    ``Unspecified``.
    """
    if name not in _cache:
        row = EvidenceTypes.query.filter(EvidenceTypes.name == name).first()
        if row is None:
            row = EvidenceTypes(name=name, description=f'{name} (demo)')
            db.session.add(row)
            db.session.commit()
        _cache[name] = row.id
    return _cache[name]


# --------------------------------------------------------------------------
# Case surfaces
#
# Each helper is idempotent and returns the objects it created *or found*, so
# a re-run on an already-seeded case still yields the lookup maps that the
# later helpers (timeline links, comments, alerts) need.
# --------------------------------------------------------------------------

def _add_demo_iocs(case, scenario, user):
    """Create the scenario's IOCs. Returns ``{ioc_value: Ioc}``."""
    existing = {i.ioc_value: i for i in Ioc.query.filter(Ioc.case_id == case.case_id).all()}
    tlp_id = _amber_tlp_id()
    created = 0

    for ioc_type_name, value, description in scenario['iocs']:
        if value in existing:
            continue

        ioc = Ioc()
        ioc.ioc_value = value
        ioc.ioc_description = description
        ioc.ioc_type_id = _ioc_type_id(ioc_type_name)
        ioc.ioc_tlp_id = tlp_id
        ioc.ioc_tags = 'demo'
        ioc.user_id = user.id
        ioc.case_id = case.case_id

        db.session.add(ioc)
        existing[value] = ioc
        created += 1

    if created:
        db.session.commit()

    return existing


def _add_demo_assets(case, scenario, user):
    """Create the scenario's assets. Returns ``{asset_name: CaseAssets}``."""
    existing = {a.asset_name: a for a in
                CaseAssets.query.filter(CaseAssets.case_id == case.case_id).all()}
    analysis_done = _analysis_status_id('Done')
    created = 0

    for name, asset_type_name, ip, domain, compromised in scenario['assets']:
        if name in existing:
            continue

        asset = CaseAssets()
        asset.asset_name = name
        asset.asset_description = f'{name} — {asset_type_name}'
        asset.asset_ip = ip
        asset.asset_domain = domain
        asset.asset_type_id = _asset_type_id(asset_type_name)
        asset.asset_compromise_status_id = (CompromiseStatus.compromised.value if compromised
                                            else CompromiseStatus.not_compromised.value)
        asset.analysis_status_id = analysis_done
        asset.asset_tags = 'demo'
        asset.case_id = case.case_id
        asset.user_id = user.id
        asset.date_added = datetime.utcnow()

        db.session.add(asset)
        existing[name] = asset
        created += 1

    if created:
        db.session.commit()

    return existing


def _add_demo_notes(case, scenario, user):
    """Create the scenario's note folders and notes.

    Notes are written with an explicit ``directory_id``. The frontend note
    tree drops any note whose ``directory_id`` is not an integer, so a note
    seeded without a folder exists in the database but is invisible in the UI
    — which is what the legacy seeder produced.
    """
    directories = {d.name: d for d in
                   NoteDirectory.query.filter(NoteDirectory.case_id == case.case_id).all()}
    existing = {n.note_title: n for n in
                Notes.query.filter(Notes.note_case_id == case.case_id).all()}
    now = datetime.utcnow()
    created = 0

    for folder_name, notes in scenario['notes']:
        directory = directories.get(folder_name)
        if directory is None:
            directory = NoteDirectory()
            directory.name = folder_name
            directory.case_id = case.case_id
            directory.parent_id = None
            db.session.add(directory)
            db.session.commit()
            directories[folder_name] = directory

        for note_title, content in notes:
            previous = existing.get(note_title)
            if previous is not None:
                # A note left by the legacy seeder has no directory, and the
                # frontend note tree drops those — it exists but can never be
                # opened, and its title would block the replacement below.
                # Adopt it into this folder instead of leaving a hole.
                if previous.directory_id is None:
                    previous.directory_id = directory.id
                    created += 1
                continue

            note = Notes()
            note.note_title = note_title
            note.note_content = content
            note.note_user = user.id
            note.note_case_id = case.case_id
            note.directory_id = directory.id
            note.note_creationdate = now
            note.note_lastupdate = now

            db.session.add(note)
            existing[note_title] = note
            created += 1

    if created:
        db.session.commit()

    return directories


def _ensure_default_timeline(case, user):
    """Return the case's default timeline, creating it if absent.

    Written directly rather than through ``case_ensure_default_timeline`` so
    seeding doesn't emit an activity row per case.
    """
    timeline = CaseTimeline.query.filter(
        CaseTimeline.case_id == case.case_id,
        CaseTimeline.is_default.is_(True)
    ).first()

    if timeline is None:
        timeline = CaseTimeline()
        timeline.case_id = case.case_id
        timeline.name = 'Main'
        timeline.description = 'Default timeline'
        timeline.is_default = True
        timeline.created_by_id = user.id
        db.session.add(timeline)
        db.session.commit()

    return timeline


def _add_demo_timeline(case, scenario, user, assets, iocs, base_time):
    """Create the scenario's timeline events.

    Each event gets a category row (``CaseEventCategory``), a membership row
    on the case's default timeline (``CaseEventTimeline``) and asset/IOC links
    — without those the event renders as an uncategorised orphan that the
    timeline filters can't reach.
    """
    existing = {e.event_title: e for e in
                CasesEvent.query.filter(CasesEvent.case_id == case.case_id).all()}
    timeline = _ensure_default_timeline(case, user)
    now = datetime.utcnow()
    created = 0

    for entry in scenario['timeline']:
        (minutes, title, content, source, color, category,
         in_graph, in_summary, asset_names, ioc_values) = entry

        previous = existing.get(title)
        if previous is not None:
            # The legacy seeder wrote events with neither a category nor a
            # timeline membership, which leaves them unreachable from the
            # timeline filters. Attach them rather than skipping.
            _repair_event_links(previous, timeline, category)
            continue

        event_date = base_time + timedelta(minutes=minutes)

        event = CasesEvent()
        event.case_id = case.case_id
        event.event_title = title
        event.event_content = content
        event.event_source = source
        event.event_raw = ''
        event.event_date = event_date
        event.event_date_wtz = event_date
        event.event_tz = '+00:00'
        event.event_added = now
        event.event_in_graph = in_graph
        event.event_in_summary = in_summary
        event.event_color = color
        event.event_tags = 'demo'
        event.user_id = user.id

        db.session.add(event)
        db.session.commit()

        db.session.add(CaseEventCategory(event_id=event.event_id,
                                         category_id=_event_category_id(category)))
        db.session.add(CaseEventTimeline(event_id=event.event_id,
                                         timeline_id=timeline.timeline_id))

        for asset_name in asset_names:
            asset = assets.get(asset_name)
            if asset is not None:
                db.session.add(CaseEventsAssets(event_id=event.event_id,
                                                asset_id=asset.asset_id,
                                                case_id=case.case_id))

        for ioc_value in ioc_values:
            ioc = iocs.get(ioc_value)
            if ioc is not None:
                db.session.add(CaseEventsIoc(event_id=event.event_id,
                                             ioc_id=ioc.ioc_id,
                                             case_id=case.case_id))

        existing[title] = event
        created += 1

    if created:
        db.session.commit()

    return created


def _repair_event_links(event, timeline, category):
    """Give a pre-existing event its category and timeline membership."""
    repaired = False

    if CaseEventCategory.query.filter(
            CaseEventCategory.event_id == event.event_id).first() is None:
        db.session.add(CaseEventCategory(event_id=event.event_id,
                                         category_id=_event_category_id(category)))
        repaired = True

    if CaseEventTimeline.query.filter(
            CaseEventTimeline.event_id == event.event_id,
            CaseEventTimeline.timeline_id == timeline.timeline_id).first() is None:
        db.session.add(CaseEventTimeline(event_id=event.event_id,
                                         timeline_id=timeline.timeline_id))
        repaired = True

    if repaired:
        db.session.commit()


def _add_demo_tasks(case, scenario, user, analysts, base_time):
    """Create the scenario's tasks, round-robining assignment over analysts.

    Returns ``{task_title: CaseTasks}`` for the comment pass.
    """
    existing = {t.task_title: t for t in
                CaseTasks.query.filter(CaseTasks.task_case_id == case.case_id).all()}
    now = datetime.utcnow()
    created = 0

    for index, (title, description, status_name, tags) in enumerate(scenario['tasks']):
        if title in existing:
            continue

        assignee = analysts[index % len(analysts)] if analysts else user

        task = CaseTasks()
        task.task_title = title
        task.task_description = description
        task.task_tags = tags
        task.task_status_id = _task_status_id(status_name)
        task.task_case_id = case.case_id
        task.task_open_date = base_time + timedelta(minutes=30 * index)
        task.task_last_update = now
        task.task_userid_open = user.id
        task.task_userid_update = user.id

        if status_name == 'Done':
            task.task_close_date = base_time + timedelta(minutes=30 * index + 90)
            task.task_userid_close = assignee.id

        db.session.add(task)
        db.session.commit()

        # CaseTasks has no assignee column — assignment is an association row.
        db.session.add(TaskAssignee(task_id=task.id, user_id=assignee.id))
        db.session.commit()

        existing[title] = task
        created += 1

    if created:
        db.session.commit()

    return existing


def _add_demo_evidence(case, scenario, user, base_time):
    """Create the scenario's evidence entries."""
    existing = {e.filename for e in
                CaseReceivedFile.query.filter(CaseReceivedFile.case_id == case.case_id).all()}
    now = datetime.utcnow()
    created = 0

    for index, (filename, type_name, description, size_bytes, sha256) in enumerate(scenario['evidence']):
        if filename in existing:
            continue

        evidence = CaseReceivedFile()
        evidence.filename = filename
        evidence.file_description = description
        evidence.file_size = size_bytes
        evidence.file_hash = sha256
        evidence.type_id = _evidence_type_id(type_name)
        evidence.case_id = case.case_id
        evidence.user_id = user.id
        evidence.date_added = now
        evidence.acquisition_date = base_time + timedelta(minutes=45 * index)

        db.session.add(evidence)
        existing.add(filename)
        created += 1

    if created:
        db.session.commit()

    return created


def _add_comment(case, author, text, when):
    comment = Comments()
    comment.comment_text = text
    comment.comment_date = when
    comment.comment_update_date = when
    comment.comment_user_id = author.id
    comment.comment_case_id = case.case_id
    db.session.add(comment)
    db.session.commit()
    return comment


def _add_demo_comments(case, scenario, analysts, assets, iocs, tasks, base_time):
    """Attach the scenario's comments to their assets, IOCs and tasks.

    Idempotency is per target: a target that already carries any comment is
    skipped, which keeps re-runs from stacking duplicates without needing to
    compare comment bodies.
    """
    now = datetime.utcnow()
    created = 0

    for index, (kind, target_key, text) in enumerate(scenario['comments']):
        author = analysts[index % len(analysts)] if analysts else None
        if author is None:
            break

        when = base_time + timedelta(minutes=60 * index)
        if when > now:
            when = now

        if kind == 'asset':
            asset = assets.get(target_key)
            if asset is None:
                continue
            if AssetComments.query.filter(AssetComments.comment_asset_id == asset.asset_id).first():
                continue
            comment = _add_comment(case, author, text, when)
            db.session.add(AssetComments(comment_id=comment.comment_id,
                                         comment_asset_id=asset.asset_id))

        elif kind == 'ioc':
            ioc = iocs.get(target_key)
            if ioc is None:
                continue
            if IocComments.query.filter(IocComments.comment_ioc_id == ioc.ioc_id).first():
                continue
            comment = _add_comment(case, author, text, when)
            db.session.add(IocComments(comment_id=comment.comment_id,
                                       comment_ioc_id=ioc.ioc_id))

        elif kind == 'task':
            task = tasks.get(target_key)
            if task is None:
                continue
            if TaskComments.query.filter(TaskComments.comment_task_id == task.id).first():
                continue
            comment = _add_comment(case, author, text, when)
            db.session.add(TaskComments(comment_id=comment.comment_id,
                                        comment_task_id=task.id))

        else:
            continue

        created += 1

    if created:
        db.session.commit()

    return created


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def _add_demo_alerts(case, scenario, user, analysts, assets, iocs, base_time):
    """Create the scenario's alerts for the case's customer.

    Three things the legacy seeder got wrong and this does not:

    * ``alert_source_ref`` is deterministic (``DEMO-<case_id>-<index>``) so a
      re-run recognises its own rows instead of duplicating them.
    * ``alert_creation_time`` is set relative to the case's own base time —
      left to the server default every alert lands at boot time and the alert
      list shows thirty identical timestamps.
    * every alert is written into ``similar_alerts_cache``. That table is the
      sole input to the Related-alerts graph; without it the graph is empty on
      every demo alert.
    """
    existing = {a.alert_source_ref for a in
                Alert.query.filter(Alert.alert_source_ref.like(f'DEMO-{case.case_id}-%')).all()}
    now = datetime.utcnow()
    created = []

    for index, entry in enumerate(scenario['alerts']):
        (title, source, severity_name, status_name, resolution_name,
         minutes, asset_names, ioc_values) = entry

        source_ref = f'DEMO-{case.case_id}-{index}'
        if source_ref in existing:
            continue

        event_time = base_time + timedelta(minutes=minutes)
        if event_time > now:
            event_time = now

        alert = Alert()
        alert.alert_title = title
        alert.alert_description = f'{title}\n\nRaised by {source} during the demo scenario.'
        alert.alert_source = source
        alert.alert_source_ref = source_ref
        alert.alert_source_link = f'https://demo.dfir-iris.org/alerts/{source_ref}'
        alert.alert_severity_id = _severity_id(severity_name)
        alert.alert_status_id = _alert_status_id(status_name)
        alert.alert_customer_id = case.client_id
        alert.alert_classification_id = _classification_id(scenario['classification'])
        alert.alert_source_event_time = event_time
        alert.alert_creation_time = event_time
        alert.alert_tags = f"demo,{scenario['key']}"
        alert.alert_owner_id = (analysts[index % len(analysts)].id if analysts else user.id)
        alert.alert_note = ''
        alert.date_update = event_time

        resolution_id = _resolution_status_id(resolution_name)
        if resolution_id is not None:
            alert.alert_resolution_status_id = resolution_id
            alert.resolved_at = event_time + timedelta(minutes=120)

        alert_assets = [assets[n] for n in asset_names if n in assets]
        alert_iocs = [iocs[v] for v in ioc_values if v in iocs]
        alert.assets = alert_assets
        alert.iocs = alert_iocs

        # 'Escalated' is defined as "alert converted to a new case" and
        # 'Merged' as "merged into an existing case" — the alert card only
        # renders its case chips off this association, so an escalated alert
        # without one contradicts its own status.
        if status_name in ('Escalated', 'Merged'):
            alert.cases.append(case)

        db.session.add(alert)
        db.session.commit()

        # Feeds the Related-alerts graph.
        for asset in alert_assets:
            db.session.add(SimilarAlertsCache(customer_id=case.client_id,
                                              alert_id=alert.alert_id,
                                              asset_name=asset.asset_name,
                                              asset_type_id=asset.asset_type_id,
                                              created_at=event_time))
        for ioc in alert_iocs:
            db.session.add(SimilarAlertsCache(customer_id=case.client_id,
                                              alert_id=alert.alert_id,
                                              ioc_value=ioc.ioc_value,
                                              ioc_type_id=ioc.ioc_type_id,
                                              created_at=event_time))

        existing.add(source_ref)
        created.append(alert)

    if created:
        db.session.commit()

    return created


# --------------------------------------------------------------------------
# War room
# --------------------------------------------------------------------------

def _add_demo_war_room(case, scenario, user, analysts, base_time):
    """Create the scenario's war room, attach the case and seed the chat."""
    name, state, severity_name, description = scenario['war_room']
    room_name = f'{name} — {case.name}'

    room = WarRoom.query.filter(WarRoom.name == room_name).first()
    if room is None:
        room = WarRoom()
        room.name = room_name
        room.description = description
        room.state = state
        room.severity_id = _severity_id(severity_name)
        room.created_by_id = user.id
        room.created_at = base_time
        if state == 'closed':
            room.closed_at = base_time + timedelta(hours=8)
            room.closed_by_id = user.id
        db.session.add(room)
        db.session.commit()

    if WarRoomCase.query.filter(WarRoomCase.war_room_id == room.war_room_id,
                                WarRoomCase.case_id == case.case_id).first() is None:
        db.session.add(WarRoomCase(war_room_id=room.war_room_id,
                                   case_id=case.case_id,
                                   attached_at=base_time,
                                   attached_by_id=user.id,
                                   note='Primary case for this room'))
        db.session.commit()

    members = [user] + list(analysts)
    for index, member in enumerate(members):
        if WarRoomMember.query.filter(WarRoomMember.war_room_id == room.war_room_id,
                                      WarRoomMember.user_id == member.id).first() is not None:
            continue
        db.session.add(WarRoomMember(war_room_id=room.war_room_id,
                                     user_id=member.id,
                                     role='lead' if index == 0 else 'responder',
                                     added_at=base_time,
                                     added_by_id=user.id))
    db.session.commit()

    topic = WarRoomTopic.query.filter(WarRoomTopic.war_room_id == room.war_room_id,
                                      WarRoomTopic.is_main.is_(True)).first()
    if topic is None:
        topic = WarRoomTopic()
        topic.war_room_id = room.war_room_id
        topic.name = 'Main'
        topic.is_main = True
        topic.created_by_id = user.id
        topic.created_at = base_time
        db.session.add(topic)
        db.session.commit()

    _add_demo_chat(room, topic, scenario, members, base_time)

    return room


def _add_demo_chat(room, topic, scenario, members, base_time):
    """Seed the war room's Main topic with the scenario's chat transcript.

    ``author_slot`` indexes into the member roster, so slot 0 is always the
    incident lead and the conversation reads consistently regardless of how
    many analyst accounts the deployment has.
    """
    if WarRoomChatMessage.query.filter(
            WarRoomChatMessage.war_room_id == room.war_room_id).first() is not None:
        return 0

    now = datetime.utcnow()
    created = 0

    for index, (author_slot, body) in enumerate(scenario['chat']):
        author = members[author_slot % len(members)]
        sent_at = base_time + timedelta(minutes=12 * index)
        if sent_at > now:
            sent_at = now

        message = WarRoomChatMessage()
        message.war_room_id = room.war_room_id
        message.author_id = author.id
        message.body = body
        message.kind = 'message'
        message.topic_id = topic.topic_id
        message.created_at = sent_at

        db.session.add(message)
        created += 1

    db.session.commit()
    return created


# --------------------------------------------------------------------------
# Case metadata
# --------------------------------------------------------------------------

def _apply_case_metadata(case, scenario, owner, base_time, first_population):
    """Set severity, state, classification, owner, description and tags.

    Only fills fields that are still empty, so an operator poking at a demo
    case doesn't have their edits reverted on the next restart. The exception
    is the open/initial date on ``first_population``: a case created by an
    earlier boot carries that boot's timestamp, which would put every case in
    the list on the same day while their timelines sit weeks apart.
    """
    changed = False

    if not case.description or len(case.description) < len(scenario['description']) // 2:
        case.description = scenario['description']
        changed = True

    if case.severity_id is None:
        case.severity_id = _severity_id(scenario['severity'])
        changed = True

    if case.state_id is None:
        case.state_id = _case_state_id(scenario['state'])
        changed = True

    if case.classification_id is None:
        case.classification_id = _classification_id(scenario['classification'])
        changed = True

    if case.owner_id is None:
        case.owner_id = owner.id
        changed = True

    if case.open_date is None or first_population:
        case.open_date = base_time.date()
        changed = True

    if case.initial_date is None or first_population:
        case.initial_date = base_time
        changed = True

    if changed:
        db.session.commit()

    if not case.tags:
        # Imported here rather than at module scope: case_db pulls in the
        # business layer, which imports back into iris_engine.
        from app.datamgmt.case.case_db import save_case_tags
        save_case_tags(scenario['tags'], case)

    return changed


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def populate_demo_case(case, scenario, owner, analysts, base_time):
    """Fill ``case`` with every surface described by ``scenario``.

    ``owner`` is the admin the case is attributed to; ``analysts`` is the pool
    of demo users that tasks, comments and chat are spread across. Callers are
    responsible for having bound an actor via :func:`demo_actor` first.

    Safe to call on an already-populated case — each surface no-ops when its
    rows are present, which is what lets a case that failed halfway through a
    previous boot get finished rather than staying empty.
    """
    # "Never populated" is judged on timeline events: they're written in the
    # middle of the run, so their absence means either a fresh case or one
    # whose seeding died early — both want the dates realigned.
    first_population = CasesEvent.query.filter(
        CasesEvent.case_id == case.case_id).first() is None

    _apply_case_metadata(case, scenario, owner, base_time, first_population)

    iocs = _add_demo_iocs(case, scenario, owner)
    assets = _add_demo_assets(case, scenario, owner)

    _add_demo_notes(case, scenario, owner)
    _add_demo_timeline(case, scenario, owner, assets, iocs, base_time)

    tasks = _add_demo_tasks(case, scenario, owner, analysts, base_time)

    _add_demo_evidence(case, scenario, owner, base_time)
    _add_demo_comments(case, scenario, analysts, assets, iocs, tasks, base_time)
    _add_demo_alerts(case, scenario, owner, analysts, assets, iocs, base_time)
    _add_demo_war_room(case, scenario, owner, analysts, base_time)

    log.info(f'Populated demo case {case.case_id} with scenario "{scenario["key"]}"')
