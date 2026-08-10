from __future__ import annotations

import logging
import sys

from .config import Config, configure_logging
from .iris_writer import IrisWriter
from .mapping import Mapping
from .phases import p1_reference, p2_identities, p3_cases, p4_tasks, p5_observables, p6_alerts, p7_verify
from .th4_client import Th4Client


log = logging.getLogger("th4-to-iris")


def main(argv: list[str] | None = None) -> int:
    cfg = Config.from_argv(argv)
    configure_logging(cfg.log_level)

    th4 = Th4Client(cfg.th4_url, cfg.th4_apikey)
    iris = IrisWriter(cfg.iris_db, dry_run=cfg.dry_run, datastore=cfg.datastore)
    mapping = Mapping(cfg.state)

    if cfg.th4_all_orgs:
        orgs = [o["name"] for o in th4.list_orgs()]
    else:
        orgs = cfg.th4_orgs
    log.info("orgs to migrate: %s", orgs)

    migration_uid = iris.ensure_migration_user(cfg.migration_user)

    def run(n: int) -> None:
        if cfg.phase is not None and cfg.phase != n:
            return
        log.info("=== phase %d ===", n)

    if cfg.verify_only:
        p7_verify.run(cfg, th4, iris, mapping, orgs)
        return 0

    run(1); p1_reference.run(cfg, th4, iris, mapping)
    run(2); p2_identities.run(cfg, th4, iris, mapping, orgs, migration_uid)
    run(3); p3_cases.run(cfg, th4, iris, mapping, orgs, migration_uid)
    run(4); p4_tasks.run(cfg, th4, iris, mapping, orgs, migration_uid)
    run(5); p5_observables.run(cfg, th4, iris, mapping, orgs, migration_uid)
    if cfg.include_alerts:
        run(6); p6_alerts.run(cfg, th4, iris, mapping, orgs, migration_uid)
    else:
        log.info("--include-alerts not set; skipping phase 6")
    run(7); p7_verify.run(cfg, th4, iris, mapping, orgs)

    iris.close()
    mapping.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
