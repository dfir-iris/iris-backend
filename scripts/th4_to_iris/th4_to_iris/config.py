from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass


ENTITY_TYPES = ("cases", "tasks", "task_logs", "observables", "alerts", "users", "orgs")


@dataclass
class Config:
    th4_url: str
    th4_apikey: str
    th4_orgs: list[str]
    th4_all_orgs: bool
    iris_db: str
    datastore: str
    migration_user: str
    state: str
    include_deleted: bool
    include_alerts: bool
    dry_run: bool
    verify_only: bool
    force: list[str]
    phase: int | None
    parallel: int
    log_level: str = "INFO"

    @classmethod
    def from_argv(cls, argv: list[str] | None = None) -> "Config":
        p = argparse.ArgumentParser(prog="th4-to-iris", description="TheHive 4 → IRIS v3 migration")
        p.add_argument("--th4-url", required=True)
        p.add_argument("--th4-apikey", required=True)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--th4-orgs", help="comma-separated TH4 org names")
        g.add_argument("--th4-all-orgs", action="store_true")
        p.add_argument("--iris-db", required=True, help="SQLAlchemy URL, e.g. postgresql+psycopg2://…")
        p.add_argument("--datastore", required=True, help="IRIS DATASTORE_PATH on this host")
        p.add_argument("--migration-user", default="iris_migrator")
        p.add_argument("--state", default="./migration.sqlite")
        p.add_argument("--include-deleted", action="store_true")
        p.add_argument("--include-alerts", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--verify-only", action="store_true")
        p.add_argument("--force", default="", help="comma-separated entity types to re-migrate")
        p.add_argument("--phase", type=int, choices=range(1, 8), default=None)
        p.add_argument("--parallel", type=int, default=4)
        p.add_argument("--log-level", default="INFO")
        ns = p.parse_args(argv)
        orgs = [x.strip() for x in ns.th4_orgs.split(",")] if ns.th4_orgs else []
        force = [x.strip() for x in ns.force.split(",") if x.strip()]
        for f in force:
            if f not in ENTITY_TYPES:
                p.error(f"--force value {f!r} not one of {ENTITY_TYPES}")
        return cls(
            th4_url=ns.th4_url.rstrip("/"),
            th4_apikey=ns.th4_apikey,
            th4_orgs=orgs,
            th4_all_orgs=ns.th4_all_orgs,
            iris_db=ns.iris_db,
            datastore=ns.datastore,
            migration_user=ns.migration_user,
            state=ns.state,
            include_deleted=ns.include_deleted,
            include_alerts=ns.include_alerts,
            dry_run=ns.dry_run,
            verify_only=ns.verify_only,
            force=force,
            phase=ns.phase,
            parallel=ns.parallel,
            log_level=ns.log_level,
        )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
