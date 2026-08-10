"""TH4 observables → IRIS `ioc` (ioc=true) or `case_assets` (ioc=false).

Per decision (2). Attachment observables always ingest to `data_store_file`
with `file_is_ioc=true`; the observable row (asset or ioc) still gets created
so cross-linking (`ioc_asset_link`) works. Within a single case we dedupe on
``(dataType, data)`` — TH4 lets the same value appear multiple times per
case; IRIS UI treats those as one indicator.
"""

from __future__ import annotations

import logging
from datetime import datetime

from tqdm import tqdm

from ..config import Config
from ..iris_writer import IrisWriter
from ..mapping import Mapping
from ..th4_client import Th4Client
from ..transforms import observable_split, tags, tlp as tlp_x
from ._attachments import ingest as ingest_attachment
from ._common import lookup_id, resolve_owner, th4_ts_to_datetime


log = logging.getLogger("th4-to-iris.p5")


def _build_asset(obs: dict, case_id: int, owner: int, asset_type_id: int) -> dict:
    data = obs.get("data") or ""
    dt = obs.get("dataType")
    now = th4_ts_to_datetime(obs.get("_createdAt")) or datetime.utcnow()
    return {
        "asset_name": data[:1000],
        "asset_description": obs.get("message"),
        "asset_domain": data if dt in ("domain", "fqdn") else None,
        "asset_ip": data if dt == "ip" else None,
        "asset_type_id": asset_type_id,
        "asset_tags": ", ".join(tags.clean(obs.get("tags"))) or None,
        "case_id": case_id,
        "date_added": now,
        "date_update": now,
        "user_id": owner,
        "custom_attributes": {
            "th4_tlp": obs.get("tlp"),
            "th4_pap": obs.get("pap"),
            "th4_sighted": obs.get("sighted"),
            "th4_data_type": dt,
        },
    }


def _build_ioc(obs: dict, case_id: int, owner: int, ioc_type_id: int, tlp_id: int | None) -> dict:
    data = obs.get("data") or ""
    return {
        "ioc_value": data,
        "ioc_type_id": ioc_type_id,
        "ioc_description": obs.get("message"),
        "ioc_tags": ", ".join(tags.clean(obs.get("tags")))[:512] or None,
        "user_id": owner,
        "ioc_tlp_id": tlp_id,
        "case_id": case_id,
        "custom_attributes": {
            "th4_pap": obs.get("pap"),
            "th4_sighted": obs.get("sighted"),
            "th4_data_type": obs.get("dataType"),
            "th4_startdate": obs.get("startDate"),
        },
    }


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
    migration_uid: int,
) -> None:
    if "observables" in cfg.force:
        mapping.clear("observables")

    total_assets = total_iocs = total_links = total_files = 0

    for org in orgs:
        for th4_case_id, iris_case_id in tqdm(list(mapping.iter_entity("cases")), desc=f"obs:{org}"):
            per_case: dict[tuple[str, str], dict[str, int]] = {}

            for obs in th4.iter_case_observables(org, th4_case_id):
                th4_obs_id = obs["_id"]
                if mapping.get("observables", th4_obs_id) is not None:
                    continue

                target = observable_split.route(obs)
                dt = obs.get("dataType") or ""
                data = obs.get("data") or ""
                key = (dt, data)
                owner = resolve_owner(mapping, org, obs.get("_createdBy"), migration_uid)

                with iris.txn() as s:
                    if target == observable_split.TARGET_ASSET:
                        atype_name = observable_split.asset_type_for_datatype(dt)
                        atype_id = lookup_id(iris, s, "assets_type", "asset_name", atype_name, "asset_id")
                        if key in per_case and "asset_id" in per_case[key]:
                            asset_id = per_case[key]["asset_id"]
                        else:
                            values = _build_asset(obs, iris_case_id, owner, atype_id)
                            asset_id = iris.insert_returning_pk(s, "case_assets", values, "asset_id")
                            per_case.setdefault(key, {})["asset_id"] = asset_id
                            total_assets += 1
                        mapping.put("observables", th4_obs_id, asset_id, org=org)
                        primary_iid = asset_id
                    else:
                        itype_name = observable_split.ioc_type_for_datatype(dt)
                        itype_id = lookup_id(iris, s, "ioc_type", "type_name", itype_name, "type_id")
                        tlp_name = tlp_x.th4_to_iris(obs.get("tlp"))
                        tlp_id = lookup_id(iris, s, "tlp", "tlp_name", tlp_name, "tlp_id")
                        if key in per_case and "ioc_id" in per_case[key]:
                            ioc_id = per_case[key]["ioc_id"]
                        else:
                            values = _build_ioc(obs, iris_case_id, owner, itype_id, tlp_id)
                            ioc_id = iris.insert_returning_pk(s, "ioc", values, "ioc_id")
                            per_case.setdefault(key, {})["ioc_id"] = ioc_id
                            total_iocs += 1
                        mapping.put("observables", th4_obs_id, ioc_id, org=org)
                        primary_iid = ioc_id

                    seen = per_case.get(key, {})
                    if "asset_id" in seen and "ioc_id" in seen:
                        iris.upsert_by_key(
                            s,
                            "ioc_asset_link",
                            {"ioc_id": seen["ioc_id"], "asset_id": seen["asset_id"]},
                            {},
                            "ioc_asset_link_id",
                        )
                        total_links += 1

                    attachment = obs.get("attachment")
                    if attachment:
                        ingest_attachment(
                            iris, th4, s, org, iris_case_id, attachment, owner,
                            is_ioc=(target == observable_split.TARGET_IOC),
                            is_evidence=False,
                        )
                        total_files += 1

    log.info(
        "phase 5 done: %d assets, %d iocs, %d ioc↔asset links, %d attachments",
        total_assets, total_iocs, total_links, total_files,
    )
