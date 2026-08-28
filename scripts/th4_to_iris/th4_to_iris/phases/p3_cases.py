"""TH4 cases → IRIS `cases` + `case_tags`.

TH4 fields carried in `custom_attributes` JSON on the IRIS row (no columns for
these): `th4_tlp`, `th4_pap`, `th4_impact_status`, `th4_flag`, `th4_number`,
`th4_merged_into`, `th4_merged_from`, plus each TH4 customField under
`th4_cf_<name>`.
"""

from __future__ import annotations

import logging

from tqdm import tqdm

from th4_to_iris.config import Config
from th4_to_iris.iris_writer import IrisWriter
from th4_to_iris.mapping import Mapping
from th4_to_iris.th4_client import Th4Client
from th4_to_iris.transforms import customfields, severity, status, tags
from ._common import lookup_id, resolve_owner, th4_ts_to_date, th4_ts_to_datetime, link_case_tags


log = logging.getLogger("th4-to-iris.p3")


def _build_custom_attributes(c: dict) -> dict:
    payload = customfields.flatten(c.get("customFields"))
    for th4_key, iris_key in (
        ("tlp", "th4_tlp"),
        ("pap", "th4_pap"),
        ("impactStatus", "th4_impact_status"),
        ("flag", "th4_flag"),
        ("caseId", "th4_number"),
        ("mergeInto", "th4_merged_into"),
        ("mergeFrom", "th4_merged_from"),
    ):
        if th4_key in c and c[th4_key] is not None:
            payload[iris_key] = c[th4_key]
    return payload


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
    migration_uid: int,
) -> None:
    if "cases" in cfg.force:
        mapping.clear("cases")

    for org_name in orgs:
        client_id = mapping.get("orgs", org_name)
        if client_id is None:
            log.warning("skip org %s: no client mapping (run phase 2 first)", org_name)
            continue

        n = 0
        for c in tqdm(th4.iter_cases(org_name, include_deleted=cfg.include_deleted), desc=f"cases:{org_name}"):
            th4_id = c["_id"]
            if mapping.get("cases", th4_id) is not None:
                continue

            with iris.txn() as s:
                severity_name = severity.th4_to_iris(c.get("severity"))
                severity_id = lookup_id(iris, s, "severities", "severity_name", severity_name, "severity_id")

                state_name = status.TH4_CASE_STATE.get(c.get("status"), "Open")
                state_id = lookup_id(iris, s, "case_state", "state_name", state_name, "state_id")

                owner_id = resolve_owner(mapping, org_name, c.get("owner"), migration_uid)

                open_date = th4_ts_to_date(c.get("startDate"))
                close_date = th4_ts_to_date(c.get("endDate"))
                initial_dt = th4_ts_to_datetime(c.get("_createdAt")) or None

                values = {
                    "name": (c.get("title") or "Untitled")[:200],
                    "soc_id": str(c.get("caseId", "")),
                    "client_id": client_id,
                    "description": c.get("description"),
                    "open_date": open_date,
                    "close_date": close_date,
                    "closing_note": c.get("summary"),
                    "user_id": owner_id,
                    "owner_id": owner_id,
                    "state_id": state_id,
                    "severity_id": severity_id,
                    "custom_attributes": _build_custom_attributes(c),
                }
                if initial_dt is not None:
                    values["initial_date"] = initial_dt

                case_id = iris.insert_returning_pk(s, "cases", values, "case_id")

                clean_tags = tags.clean(c.get("tags"))
                if clean_tags:
                    link_case_tags(iris, s, case_id, clean_tags)

                mapping.put("cases", th4_id, case_id, org=org_name)
                n += 1

        log.info("phase 3 (%s): %d cases migrated", org_name, n)
