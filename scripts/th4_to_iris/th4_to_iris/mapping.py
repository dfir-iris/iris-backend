"""SQLite-backed (th4_entity, th4_id) → iris_id map for idempotent re-runs."""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable


log = logging.getLogger("th4-to-iris.map")


SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_map (
    th4_entity TEXT NOT NULL,
    th4_id     TEXT NOT NULL,
    iris_id    INTEGER NOT NULL,
    sha256     TEXT,
    org        TEXT,
    PRIMARY KEY (th4_entity, th4_id)
);
CREATE INDEX IF NOT EXISTS idx_map_iris ON migration_map (th4_entity, iris_id);
"""


class Mapping:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                self.conn.execute(stmt)

    def get(self, entity: str, th4_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT iris_id FROM migration_map WHERE th4_entity=? AND th4_id=?",
            (entity, th4_id),
        ).fetchone()
        return int(row[0]) if row else None

    def put(self, entity: str, th4_id: str, iris_id: int, org: str | None = None, sha256: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO migration_map(th4_entity, th4_id, iris_id, org, sha256) VALUES (?,?,?,?,?)",
            (entity, str(th4_id), int(iris_id), org, sha256),
        )

    def clear(self, entity: str) -> int:
        cur = self.conn.execute("DELETE FROM migration_map WHERE th4_entity=?", (entity,))
        return cur.rowcount

    def count(self, entity: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM migration_map WHERE th4_entity=?", (entity,)
        ).fetchone()
        return int(row[0])

    def iter_entity(self, entity: str) -> Iterable[tuple[str, int]]:
        for row in self.conn.execute(
            "SELECT th4_id, iris_id FROM migration_map WHERE th4_entity=?",
            (entity,),
        ):
            yield row[0], int(row[1])

    def close(self) -> None:
        self.conn.close()
