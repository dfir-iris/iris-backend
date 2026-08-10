"""TH4 orgs → IRIS `client`, TH4 users → IRIS `user` + `user_client` + `user_group`.

Per decision (4): TH4 org members get IRIS access to the mapped customer via
`user_client(access_level=full, allow_alerts=True)`. TH4 `profile` maps to
the nearest IRIS group. Passwords are not migrated — users get a random one
and a fresh `user.api_key`.
"""

from __future__ import annotations

import logging
import secrets

from ..config import Config
from ..iris_writer import IrisWriter
from ..mapping import Mapping
from ..th4_client import Th4Client


log = logging.getLogger("th4-to-iris.p2")


ACCESS_LEVEL_FULL = 4  # matches CaseAccessLevel/UserClient in IRIS (full access)

DEFAULT_GROUPS = {
    "admin": "Administrator",
    "org-admin": "Administrator",
    "analyst": "Analyst",
    "read-only": "Read-Only",
}


def _ensure_group(iris: IrisWriter, s, name: str) -> int:
    return iris.upsert_by_key(
        s,
        "groups",
        {"group_name": name},
        {"group_description": f"Migrated from TH4 profile {name}", "group_permissions": 0},
        "group_id",
    )


def _map_profile_to_group(profile: str | None) -> str:
    if not profile:
        return "Analyst"
    return DEFAULT_GROUPS.get(profile, "Analyst")


def run(
    cfg: Config,
    th4: Th4Client,
    iris: IrisWriter,
    mapping: Mapping,
    orgs: list[str],
    migration_uid: int,
) -> None:
    if "orgs" in cfg.force:
        mapping.clear("orgs")
    if "users" in cfg.force:
        mapping.clear("users")

    with iris.txn() as s:
        for grp in set(DEFAULT_GROUPS.values()):
            _ensure_group(iris, s, grp)

    for org_name in orgs:
        with iris.txn() as s:
            client_id = mapping.get("orgs", org_name)
            if client_id is None:
                client_id = iris.upsert_by_key(
                    s,
                    "client",
                    {"name": org_name},
                    {"description": f"Migrated from TH4 org {org_name}", "created_by": migration_uid},
                    "client_id",
                )
                mapping.put("orgs", org_name, client_id, org=org_name)
                log.info("org %s → client_id=%d", org_name, client_id)

            for u in th4.list_users(org_name):
                login = u.get("login") or u.get("_id")
                if not login:
                    continue
                key = f"{login}"
                iris_uid = mapping.get("users", key)
                if iris_uid is None:
                    existing = iris.find_one(s, "user", user=login) or iris.find_one(s, "user", email=login)
                    if existing:
                        iris_uid = int(existing["id"])
                    else:
                        iris_uid = iris.insert_returning_pk(
                            s,
                            "user",
                            {
                                "user": login,
                                "name": u.get("name") or login,
                                "email": u.get("login") if "@" in login else f"{login}@migrated.local",
                                "password": secrets.token_urlsafe(32),
                                "active": not bool(u.get("locked")),
                                "api_key": secrets.token_urlsafe(64),
                            },
                            "id",
                        )
                    mapping.put("users", key, iris_uid, org=org_name)

                group_name = _map_profile_to_group(u.get("profile"))
                group_id = _ensure_group(iris, s, group_name)
                iris.upsert_by_key(
                    s,
                    "user_group",
                    {"user_id": iris_uid, "group_id": group_id},
                    {},
                    "id",
                )

                iris.upsert_by_key(
                    s,
                    "user_client",
                    {"user_id": iris_uid, "client_id": client_id},
                    {"access_level": ACCESS_LEVEL_FULL, "allow_alerts": True},
                    "id",
                )

    log.info("phase 2 done: %d orgs, %d users", len(orgs), mapping.count("users"))
