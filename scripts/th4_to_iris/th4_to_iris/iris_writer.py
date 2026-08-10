"""SQLAlchemy handle to the IRIS database.

Uses raw SQLAlchemy Core (Table+MetaData reflected once at startup) so this
script can run without importing the whole `app/` Flask stack. Every field
name in `.tables[...]` matches the models under
`iris-backend/source/app/models/`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("th4-to-iris.iris")


IRIS_TABLES = (
    "cases", "case_state", "case_classification", "case_tags", "case_protagonist",
    "severities", "review_status",
    "alerts", "alert_status", "alert_resolution_status", "alert_case_association",
    "alert_iocs_association", "alert_assets_association",
    "case_assets", "assets_type", "analysis_status",
    "ioc", "ioc_type", "tlp", "ioc_asset_link",
    "case_received_file", "evidence_type",
    "notes", "note_directory", "note_revisions",
    "case_tasks", "task_status", "task_assignee",
    "cases_events",
    "comments",
    "client", "contact",
    "user", "user_api_key", "groups", "user_group", "organisations", "user_organisation",
    "user_client", "user_case_access", "user_case_effective_access",
    "group_case_access", "organisation_case_access", "user_followed_case",
    "tags",
    "custom_attribute",
    "data_store_path", "data_store_file",
)


class IrisWriter:
    def __init__(self, db_url: str, dry_run: bool, datastore: str) -> None:
        self.engine: Engine = create_engine(db_url, future=True)
        self.metadata = MetaData()
        self.metadata.reflect(bind=self.engine, only=IRIS_TABLES)
        self.tables: dict[str, Table] = {t.name: t for t in self.metadata.tables.values()}
        missing = set(IRIS_TABLES) - set(self.tables)
        if missing:
            raise RuntimeError(f"Missing IRIS tables in target DB: {sorted(missing)}")
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        self.dry_run = dry_run
        self.datastore = datastore
        os.makedirs(datastore, exist_ok=True)

    @contextlib.contextmanager
    def txn(self):
        s: Session = self.Session()
        try:
            yield s
            if self.dry_run:
                s.rollback()
            else:
                s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def close(self) -> None:
        self.engine.dispose()

    def t(self, name: str) -> Table:
        return self.tables[name]

    def find_one(self, s: Session, table: str, **where: Any) -> dict | None:
        t = self.t(table)
        stmt = select(t).filter_by(**where).limit(1)
        row = s.execute(stmt).mappings().first()
        return dict(row) if row else None

    def insert_returning_pk(self, s: Session, table: str, values: dict, pk: str) -> int:
        t = self.t(table)
        stmt = insert(t).values(**values).returning(t.c[pk])
        return int(s.execute(stmt).scalar_one())

    def update_by_pk(self, s: Session, table: str, pk_col: str, pk_val: Any, values: dict) -> None:
        t = self.t(table)
        s.execute(update(t).where(t.c[pk_col] == pk_val).values(**values))

    def upsert_by_key(self, s: Session, table: str, key: dict, values: dict, pk_col: str) -> int:
        """Insert if no row matches `key`; return the PK value either way."""
        found = self.find_one(s, table, **key)
        if found:
            return int(found[pk_col])
        merged = {**key, **values}
        return self.insert_returning_pk(s, table, merged, pk_col)

    def ensure_migration_user(self, login: str) -> int:
        """Return `user.id` for a service account used as the fallback author."""
        with self.txn() as s:
            row = self.find_one(s, "user", user=login)
            if row:
                return int(row["id"])
            uid = self.insert_returning_pk(
                s,
                "user",
                {
                    "user": login,
                    "name": "IRIS Migration Service",
                    "email": f"{login}@migration.local",
                    "password": secrets.token_urlsafe(32),
                    "active": True,
                    "is_service_account": True,
                    "api_key": secrets.token_urlsafe(64),
                },
                "id",
            )
            log.info("created migration user %r id=%d", login, uid)
            return uid
