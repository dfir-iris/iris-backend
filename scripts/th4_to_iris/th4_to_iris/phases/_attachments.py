"""Ingest a TH4 attachment into IRIS's datastore layout.

TH4 attachment id IS the SHA-256 of the content, so we use it as the on-disk
name and verify post-download. Each case gets a root `DataStorePath` under
which we stash a single sub-directory (``th4-imports``) for everything the
migration writes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from th4_to_iris.iris_writer import IrisWriter
from th4_to_iris.th4_client import Th4Client


log = logging.getLogger("th4-to-iris.attachments")


def _ensure_case_datastore_root(iris: IrisWriter, s, case_id: int) -> int:
    root = iris.find_one(s, "data_store_path", path_case_id=case_id, path_is_root=True)
    if root:
        return int(root["path_id"])
    return iris.insert_returning_pk(
        s,
        "data_store_path",
        {
            "path_name": "/",
            "path_case_id": case_id,
            "path_is_root": True,
            "path_parent_id": None,
        },
        "path_id",
    )


def _ensure_th4_imports_dir(iris: IrisWriter, s, case_id: int) -> int:
    root_id = _ensure_case_datastore_root(iris, s, case_id)
    existing = iris.find_one(
        s,
        "data_store_path",
        path_case_id=case_id,
        path_parent_id=root_id,
        path_name="th4-imports",
    )
    if existing:
        return int(existing["path_id"])
    return iris.insert_returning_pk(
        s,
        "data_store_path",
        {
            "path_name": "th4-imports",
            "path_case_id": case_id,
            "path_is_root": False,
            "path_parent_id": root_id,
        },
        "path_id",
    )


def ingest(
    iris: IrisWriter,
    th4: Th4Client,
    s,
    org: str,
    case_id: int,
    attachment: dict,
    user_id: int,
    is_ioc: bool,
    is_evidence: bool = False,
) -> int:
    """Download TH4 attachment; insert `data_store_file`; return `file_id`.

    Idempotent within one case: if a `data_store_file` with the same sha256
    already exists for this case, we return the existing id and skip download.
    """
    attachment_id = attachment.get("id")
    name = attachment.get("name") or attachment_id
    size = attachment.get("size")
    if not attachment_id:
        raise ValueError("attachment has no id (sha256)")

    existing = iris.find_one(s, "data_store_file", file_case_id=case_id, file_sha256=attachment_id)
    if existing:
        return int(existing["file_id"])

    parent_id = _ensure_th4_imports_dir(iris, s, case_id)
    local_name = attachment_id  # sha256 on disk

    dest = os.path.join(iris.datastore, local_name)
    if not iris.dry_run and not os.path.exists(dest):
        th4.download_attachment(org, attachment_id, dest)

    return iris.insert_returning_pk(
        s,
        "data_store_file",
        {
            "file_original_name": name,
            "file_local_name": local_name,
            "file_description": f"Migrated from TH4 attachment {attachment_id}",
            "file_date_added": datetime.utcnow(),
            "file_size": size,
            "file_is_ioc": is_ioc,
            "file_is_evidence": is_evidence,
            "file_parent_id": parent_id,
            "file_sha256": attachment_id,
            "added_by_user_id": user_id,
            "file_case_id": case_id,
        },
        "file_id",
    )
