"""TH4 alerts → IRIS `alerts` (+ association tables).

Runs only when `--include-alerts` is set. Alert `artifacts` reuse the same
observable-split rule as phase 5 (ioc=true → `ioc`, ioc=false → `case_assets`)
so a followed-up alert lands with its indicators already normalised. If the
TH4 alert already points at a case (`alert.case`), we insert
`alert_case_association` if the case was migrated.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tqdm import tqdm

from th4_to_iris.config import Config
from th4_to_iris.iris_writer import IrisWriter
from th4_to_iris.mapping import Mapping
from th4_to_iris.th4_client import Th4Client
from th4_to_iris.transforms import customfields, observable_split, severity, status, tags, tlp as tlp_x
from ._common import lookup_id, resolve_owner, th4_ts_to_datetime


log = logging.getLogger("th4-to-iris.p6")


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
    migration_uid: int,
) -> None:
    if "alerts" in cfg.force:
        mapping.clear("alerts")

    total = 0
    for org in orgs:
        client_id = mapping.get("orgs", org)
        if client_id is None:
            log.warning("skip alerts for %s: no client mapping", org)
            continue

        for al in tqdm(th4.iter_alerts(org, include_deleted=cfg.include_deleted), desc=f"alerts:{org}"):
            th4_id = al["_id"]
            if mapping.get("alerts", th4_id) is not None:
                continue

            with iris.txn() as s:
                sev_name = severity.th4_to_iris(al.get("severity"))
                sev_id = lookup_id(iris, s, "severities", "severity_name", sev_name, "severity_id")

                st_name = status.TH4_ALERT_STATUS.get(al.get("status"), "New")
                st_id = lookup_id(iris, s, "alert_status", "status_name", st_name, "status_id")

                owner_id = resolve_owner(mapping, org, al.get("_createdBy"), migration_uid)
                event_dt = th4_ts_to_datetime(al.get("date")) or datetime.utcnow()
                created_dt = th4_ts_to_datetime(al.get("_createdAt")) or event_dt

                custom = customfields.flatten(al.get("customFields"))
                for k, target in (("tlp", "th4_tlp"), ("pap", "th4_pap"), ("follow", "th4_follow")):
                    if al.get(k) is not None:
                        custom[target] = al[k]

                alert_iid = iris.insert_returning_pk(
                    s,
                    "alerts",
                    {
                        "alert_title": (al.get("title") or "Untitled")[:2000],
                        "alert_description": al.get("description"),
                        "alert_source": al.get("source"),
                        "alert_source_ref": al.get("sourceRef"),
                        "alert_source_link": al.get("externalLink"),
                        "alert_source_content": al.get("caseTemplate") and {"caseTemplate": al["caseTemplate"]},
                        "alert_severity_id": sev_id,
                        "alert_status_id": st_id,
                        "alert_source_event_time": event_dt,
                        "alert_creation_time": created_dt,
                        "alert_tags": ", ".join(tags.clean(al.get("tags"))) or None,
                        "alert_owner_id": owner_id,
                        "alert_customer_id": client_id,
                    },
                    "alert_id",
                )
                mapping.put("alerts", th4_id, alert_iid, org=org)
                total += 1

                for art in al.get("artifacts") or []:
                    dt = art.get("dataType") or ""
                    data = art.get("data") or ""
                    if not data:
                        continue
                    if observable_split.route(art) == observable_split.TARGET_ASSET:
                        atype_name = observable_split.asset_type_for_datatype(dt)
                        atype_id = lookup_id(iris, s, "assets_type", "asset_name", atype_name, "asset_id")
                        asset_id = iris.insert_returning_pk(
                            s,
                            "case_assets",
                            {
                                "asset_name": data[:1000],
                                "asset_description": art.get("message"),
                                "asset_domain": data if dt in ("domain", "fqdn") else None,
                                "asset_ip": data if dt == "ip" else None,
                                "asset_type_id": atype_id,
                                "asset_tags": ", ".join(tags.clean(art.get("tags"))) or None,
                                "date_added": created_dt,
                                "date_update": created_dt,
                                "user_id": owner_id,
                                "case_id": None,
                            },
                            "asset_id",
                        )
                        iris.upsert_by_key(
                            s,
                            "alert_assets_association",
                            {"alert_id": alert_iid, "asset_id": asset_id},
                            {},
                            "alert_id",
                        )
                    else:
                        itype_name = observable_split.ioc_type_for_datatype(dt)
                        itype_id = lookup_id(iris, s, "ioc_type", "type_name", itype_name, "type_id")
                        tlp_name = tlp_x.th4_to_iris(art.get("tlp"))
                        tlp_id = lookup_id(iris, s, "tlp", "tlp_name", tlp_name, "tlp_id")
                        ioc_id = iris.insert_returning_pk(
                            s,
                            "ioc",
                            {
                                "ioc_value": data,
                                "ioc_type_id": itype_id,
                                "ioc_description": art.get("message"),
                                "ioc_tags": ", ".join(tags.clean(art.get("tags")))[:512] or None,
                                "user_id": owner_id,
                                "ioc_tlp_id": tlp_id,
                                "case_id": None,
                            },
                            "ioc_id",
                        )
                        iris.upsert_by_key(
                            s,
                            "alert_iocs_association",
                            {"alert_id": alert_iid, "ioc_id": ioc_id},
                            {},
                            "alert_id",
                        )

                th4_case_ref = al.get("case")
                if th4_case_ref:
                    iris_case_id = mapping.get("cases", th4_case_ref)
                    if iris_case_id is not None:
                        iris.upsert_by_key(
                            s,
                            "alert_case_association",
                            {"alert_id": alert_iid, "case_id": iris_case_id},
                            {},
                            "alert_id",
                        )

    log.info("phase 6 done: %d alerts", total)
