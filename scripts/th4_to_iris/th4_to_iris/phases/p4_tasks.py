"""TH4 tasks → IRIS `case_tasks` + `task_assignee`; TH4 task logs → IRIS `notes`.

Per decision (3), task logs land as `notes` under a per-task `note_directory`
named ``Task: {title}``. Attachments on task logs go through the datastore
helper (`_attachments.ingest`, file_is_ioc=False, file_is_evidence=False) and
are referenced inline in the note markdown as ``[name](/datastore/file/<id>)``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tqdm import tqdm

from th4_to_iris.config import Config
from th4_to_iris.iris_writer import IrisWriter
from th4_to_iris.mapping import Mapping
from th4_to_iris.th4_client import Th4Client
from th4_to_iris.transforms import status
from ._attachments import ingest as ingest_attachment
from ._common import lookup_id, resolve_owner, th4_ts_to_datetime


log = logging.getLogger("th4-to-iris.p4")


def _ensure_task_note_dir(iris: IrisWriter, s, case_id: int, task_title: str) -> int:
    name = f"Task: {task_title}"[:255]
    existing = iris.find_one(s, "note_directory", case_id=case_id, name=name, parent_id=None)
    if existing:
        return int(existing["id"])
    return iris.insert_returning_pk(
        s,
        "note_directory",
        {"name": name, "case_id": case_id, "parent_id": None},
        "id",
    )


def _migrate_task_logs(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    s,
    org: str,
    case_id: int,
    task_th4_id: str,
    task_title: str,
    migration_uid: int,
) -> int:
    note_dir_id = _ensure_task_note_dir(iris, s, case_id, task_title)
    n = 0
    for lg in th4.iter_task_logs(org, task_th4_id):
        th4_log_id = lg["_id"]
        if mapping.get("task_logs", th4_log_id) is not None:
            continue

        author = resolve_owner(mapping, org, lg.get("owner"), migration_uid)
        created = th4_ts_to_datetime(lg.get("_createdAt")) or datetime.utcnow()
        content = lg.get("message") or ""

        attachment = lg.get("attachment")
        if attachment:
            file_id = ingest_attachment(
                iris, th4, s, org, case_id, attachment, author, is_ioc=False, is_evidence=False,
            )
            link = f"\n\n![{attachment.get('name', 'attachment')}](/datastore/file/{file_id})"
            content = f"{content}{link}"

        note_id = iris.insert_returning_pk(
            s,
            "notes",
            {
                "note_title": f"Log {created.isoformat(timespec='seconds')}"[:155],
                "note_content": content,
                "note_user": author,
                "note_creationdate": created,
                "note_lastupdate": created,
                "note_case_id": case_id,
                "directory_id": note_dir_id,
            },
            "note_id",
        )
        mapping.put("task_logs", th4_log_id, note_id, org=org)
        n += 1
    return n


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
    migration_uid: int,
) -> None:
    if "tasks" in cfg.force:
        mapping.clear("tasks")
    if "task_logs" in cfg.force:
        mapping.clear("task_logs")

    total_tasks = total_logs = 0
    for org in orgs:
        for th4_case_id, iris_case_id in tqdm(list(mapping.iter_entity("cases")), desc=f"tasks:{org}"):
            for tk in th4.iter_case_tasks(org, th4_case_id):
                th4_tk_id = tk["_id"]
                if mapping.get("tasks", th4_tk_id) is not None:
                    task_iid = mapping.get("tasks", th4_tk_id)
                    with iris.txn() as s:
                        total_logs += _migrate_task_logs(
                            cfg, th4, iris, mapping, s, org, iris_case_id, th4_tk_id,
                            tk.get("title") or f"task-{th4_tk_id}", migration_uid,
                        )
                    continue

                with iris.txn() as s:
                    status_name = status.TH4_TASK_STATUS.get(tk.get("status"), "To do")
                    status_id = lookup_id(iris, s, "task_status", "status_name", status_name, "id")
                    owner = resolve_owner(mapping, org, tk.get("owner"), migration_uid)
                    open_dt = th4_ts_to_datetime(tk.get("startDate")) or th4_ts_to_datetime(tk.get("_createdAt"))
                    close_dt = th4_ts_to_datetime(tk.get("endDate"))
                    last_update = th4_ts_to_datetime(tk.get("_updatedAt")) or open_dt

                    custom = {}
                    for th4_key, iris_key in (
                        ("order", "th4_order"),
                        ("group", "th4_group"),
                        ("dueDate", "th4_due_date"),
                        ("flag", "th4_flag"),
                    ):
                        if tk.get(th4_key) is not None:
                            custom[iris_key] = tk[th4_key]

                    task_iid = iris.insert_returning_pk(
                        s,
                        "case_tasks",
                        {
                            "task_title": (tk.get("title") or "Untitled")[:2000],
                            "task_description": tk.get("description"),
                            "task_open_date": open_dt,
                            "task_close_date": close_dt,
                            "task_last_update": last_update,
                            "task_userid_open": owner,
                            "task_userid_update": owner,
                            "task_userid_close": owner if close_dt else None,
                            "task_status_id": status_id,
                            "task_case_id": iris_case_id,
                            "custom_attributes": custom,
                        },
                        "id",
                    )
                    if tk.get("owner"):
                        assignee_uid = resolve_owner(mapping, org, tk["owner"], migration_uid)
                        iris.upsert_by_key(
                            s,
                            "task_assignee",
                            {"user_id": assignee_uid, "task_id": task_iid},
                            {},
                            "id",
                        )
                    mapping.put("tasks", th4_tk_id, task_iid, org=org)
                    total_tasks += 1

                    total_logs += _migrate_task_logs(
                        cfg, th4, iris, mapping, s, org, iris_case_id, th4_tk_id,
                        tk.get("title") or f"task-{th4_tk_id}", migration_uid,
                    )

    log.info("phase 4 done: %d tasks, %d task-log notes", total_tasks, total_logs)
