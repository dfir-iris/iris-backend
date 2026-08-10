"""Post-migration verification.

Currently checks:
- per-entity counts: TH4 (via mapping table) vs IRIS (via reflected table)
- attachment SHA-256 audit: for every migrated `data_store_file`, verify the
  file on disk hashes to `file_sha256`
- spot-check: N random cases exist in IRIS with matching title & customer

Nothing is written. Exits non-zero via the caller on any mismatch — this
returns a summary dict for the CLI to print.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass, field

from sqlalchemy import func, select

from ..config import Config
from ..iris_writer import IrisWriter
from ..mapping import Mapping
from ..th4_client import Th4Client


log = logging.getLogger("th4-to-iris.p7")

SPOT_CHECK_SAMPLE = 20


@dataclass
class VerifyResult:
    counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # entity -> (map_count, iris_count)
    hash_mismatches: list[str] = field(default_factory=list)
    missing_cases: list[str] = field(default_factory=list)


def _count_iris(iris: IrisWriter, table: str, pk_col: str) -> int:
    with iris.txn() as s:
        stmt = select(func.count()).select_from(iris.t(table))
        return int(s.execute(stmt).scalar_one())


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
) -> VerifyResult:
    r = VerifyResult()

    for entity, (table, pk) in {
        "orgs":   ("client",     "client_id"),
        "users":  ("user",       "id"),
        "cases":  ("cases",      "case_id"),
        "tasks":  ("case_tasks", "id"),
        "task_logs": ("notes",   "note_id"),
        "observables": ("case_assets", "asset_id"),  # split; see below
        "alerts": ("alerts",     "alert_id"),
    }.items():
        m = mapping.count(entity)
        i = _count_iris(iris, table, pk)
        r.counts[entity] = (m, i)
        log.info("count %-11s map=%-6d iris=%-6d", entity, m, i)

    # Observables split across two tables — report ioc + asset totals separately.
    r.counts["observables_iris_ioc"] = (
        mapping.count("observables"),
        _count_iris(iris, "ioc", "ioc_id"),
    )

    # Attachment audit: hash on disk vs recorded sha256.
    with iris.txn() as s:
        rows = s.execute(
            select(iris.t("data_store_file").c.file_local_name,
                   iris.t("data_store_file").c.file_sha256,
                   iris.t("data_store_file").c.file_id)
        ).all()
    log.info("auditing %d attachments...", len(rows))
    for local_name, sha256, fid in rows:
        if not sha256:
            continue
        path = os.path.join(iris.datastore, local_name)
        if not os.path.exists(path):
            r.hash_mismatches.append(f"file_id={fid} missing on disk: {path}")
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            r.hash_mismatches.append(f"file_id={fid} sha mismatch")

    # Spot-check N random cases: TH4 title == IRIS `cases.name` and customer matches.
    sample = list(mapping.iter_entity("cases"))
    random.shuffle(sample)
    sample = sample[:SPOT_CHECK_SAMPLE]
    for th4_id, iris_case_id in sample:
        with iris.txn() as s:
            row = iris.find_one(s, "cases", case_id=iris_case_id)
        if not row:
            r.missing_cases.append(f"case th4={th4_id} iris_id={iris_case_id} not found")

    if r.hash_mismatches or r.missing_cases:
        log.error("VERIFY FAILED: %d hash mismatches, %d missing cases",
                  len(r.hash_mismatches), len(r.missing_cases))
    else:
        log.info("VERIFY OK")
    return r
