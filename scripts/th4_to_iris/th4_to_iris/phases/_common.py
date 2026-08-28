"""Shared helpers used across phases."""

from __future__ import annotations

from datetime import date, datetime

from th4_to_iris.iris_writer import IrisWriter


def th4_ts_to_datetime(ts: int | None) -> datetime | None:
    """TH4 timestamps are ms since epoch (int)."""
    if ts is None:
        return None
    return datetime.utcfromtimestamp(int(ts) / 1000.0)


def th4_ts_to_date(ts: int | None) -> date | None:
    dt = th4_ts_to_datetime(ts)
    return dt.date() if dt else None


def lookup_id(iris: IrisWriter, s, table: str, key_col: str, key_val, pk_col: str) -> int | None:
    row = iris.find_one(s, table, **{key_col: key_val})
    return int(row[pk_col]) if row else None


def resolve_owner(mapping, org: str, th4_login: str | None, fallback: int) -> int:
    if not th4_login:
        return fallback
    return mapping.get("users", th4_login) or fallback


def upsert_tag(iris: IrisWriter, s, tag_title: str) -> int:
    from th4_to_iris.transforms.tags import namespace_of
    return iris.upsert_by_key(
        s,
        "tags",
        {"tag_title": tag_title},
        {"tag_namespace": namespace_of(tag_title), "tag_creation_date": datetime.utcnow()},
        "id",
    )


def link_case_tags(iris: IrisWriter, s, case_id: int, tag_titles: list[str]) -> None:
    from sqlalchemy import insert
    t = iris.t("case_tags")
    for title in tag_titles:
        tag_id = upsert_tag(iris, s, title)
        existing = iris.find_one(s, "case_tags", case_id=case_id, tag_id=tag_id)
        if not existing:
            s.execute(insert(t).values(case_id=case_id, tag_id=tag_id))
