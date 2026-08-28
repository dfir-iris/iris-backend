"""Seed reference data expected by later phases.

Idempotent: every entry uses ``upsert_by_key``.
"""

from __future__ import annotations

import logging

from th4_to_iris.config import Config
from th4_to_iris.iris_writer import IrisWriter
from th4_to_iris.mapping import Mapping
from th4_to_iris.th4_client import Th4Client


log = logging.getLogger("th4-to-iris.p1")


SEVERITY_NAMES = ("low", "medium", "high", "critical")
TLP_NAMES = ("white", "green", "amber", "red")
CASE_STATES = ("Open", "Closed", "Deleted", "Duplicated")
TASK_STATUSES = ("To do", "In progress", "Done", "Cancelled")
ALERT_STATUSES = ("New", "In progress", "Closed", "Merged")
ALERT_RESOLUTION_STATUSES = ("Not Applicable", "False Positive", "True Positive", "Other")
ASSET_TYPES = (
    "IP Address", "Domain", "Host - Windows", "Host - Linux", "Host - Mac",
    "Account", "Other",
)
IOC_TYPES = (
    "hash", "file", "filename", "url", "registry-key", "email", "email-subject",
    "domain", "ip-any", "user-agent", "other",
)
EVIDENCE_TYPES = ("Digital", "Physical", "Other")


def run(cfg: Config, th4: Th4Client, iris: IrisWriter, mapping: Mapping) -> None:
    with iris.txn() as s:
        for name in SEVERITY_NAMES:
            iris.upsert_by_key(s, "severities", {"severity_name": name}, {}, "severity_id")
        for name in TLP_NAMES:
            iris.upsert_by_key(s, "tlp", {"tlp_name": name}, {"tlp_bscolor": ""}, "tlp_id")
        for name in CASE_STATES:
            iris.upsert_by_key(s, "case_state", {"state_name": name}, {}, "state_id")
        for name in TASK_STATUSES:
            iris.upsert_by_key(s, "task_status", {"status_name": name}, {}, "id")
        for name in ALERT_STATUSES:
            iris.upsert_by_key(s, "alert_status", {"status_name": name}, {}, "status_id")
        for name in ALERT_RESOLUTION_STATUSES:
            iris.upsert_by_key(
                s,
                "alert_resolution_status",
                {"resolution_status_name": name},
                {},
                "resolution_status_id",
            )
        for name in ASSET_TYPES:
            iris.upsert_by_key(
                s,
                "assets_type",
                {"asset_name": name},
                {"asset_description": ""},
                "asset_id",
            )
        for name in IOC_TYPES:
            iris.upsert_by_key(
                s,
                "ioc_type",
                {"type_name": name},
                {"type_description": ""},
                "type_id",
            )
        for name in EVIDENCE_TYPES:
            iris.upsert_by_key(s, "evidence_type", {"name": name}, {}, "id")

    log.info("phase 1 done: reference data seeded")
