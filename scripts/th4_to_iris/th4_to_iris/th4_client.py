"""Thin wrapper around the TheHive 4 REST API v0.

Uses `POST /api/<entity>/_search` with query-DSL bodies and `range=start-end`
query parameter for pagination. Auth via `Authorization: Bearer <apikey>` +
per-request `X-Organisation` header for org-scoping.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator
from typing import Any

import requests


log = logging.getLogger("th4-to-iris.th4")

RETRY_STATUSES = {500, 502, 503, 504}
MAX_RETRIES = 6
PAGE_SIZE = 500


class Th4Client:
    def __init__(self, base_url: str, apikey: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {apikey}"
        self.session.headers["Accept"] = "application/json"

    def _request(self, method: str, path: str, org: str | None = None, **kw: Any) -> requests.Response:
        headers = dict(kw.pop("headers", {}))
        if org:
            headers["X-Organisation"] = org
        url = f"{self.base_url}{path}"
        backoff = 1.0
        for attempt in range(MAX_RETRIES):
            r = self.session.request(method, url, headers=headers, **kw)
            if r.status_code in RETRY_STATUSES:
                log.warning("TH4 %s %s → %d (attempt %d)", method, path, r.status_code, attempt + 1)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def list_orgs(self) -> list[dict]:
        # /api/organisation is admin-scoped; caller's key must be admin.
        r = self._request("GET", "/api/organisation")
        return r.json()

    def list_users(self, org: str) -> list[dict]:
        # `GET /api/user` (v0) returns users visible in the current org.
        r = self._request("GET", "/api/user", org=org)
        return r.json()

    def list_custom_field_defs(self, org: str) -> list[dict]:
        r = self._request("GET", "/api/customField", org=org)
        return r.json()

    def _search(
        self,
        entity_path: str,
        org: str,
        query: dict | None = None,
        sort: str = "+_createdAt",
        page_size: int = PAGE_SIZE,
    ) -> Iterator[dict]:
        start = 0
        body = {"query": query} if query else {}
        while True:
            end = start + page_size - 1
            params = {"range": f"{start}-{end}", "sort": sort}
            r = self._request(
                "POST",
                f"/api/{entity_path}/_search",
                org=org,
                params=params,
                json=body,
            )
            batch = r.json()
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected list from {entity_path}/_search, got {type(batch).__name__}")
            yield from batch
            if len(batch) < page_size:
                return
            start += page_size

    def iter_cases(self, org: str, include_deleted: bool) -> Iterator[dict]:
        if include_deleted:
            query = None
        else:
            query = {"_not": {"_field": "status", "_value": "Deleted"}}
        yield from self._search("case", org, query=query)

    def iter_case_tasks(self, org: str, case_id: str) -> Iterator[dict]:
        # /api/case/{id}/task uses same _search pattern via query filter on parent.
        query = {"_parent": {"_type": "case", "_query": {"_id": case_id}}}
        yield from self._search("case/task", org, query=query)

    def iter_task_logs(self, org: str, task_id: str) -> Iterator[dict]:
        query = {"_parent": {"_type": "case_task", "_query": {"_id": task_id}}}
        yield from self._search("case/task/log", org, query=query)

    def iter_case_observables(self, org: str, case_id: str) -> Iterator[dict]:
        query = {"_parent": {"_type": "case", "_query": {"_id": case_id}}}
        yield from self._search("case/artifact", org, query=query)

    def iter_alerts(self, org: str, include_deleted: bool) -> Iterator[dict]:
        # Alerts have no "Deleted" state but keep the filter consistent.
        query = None if include_deleted else {"_not": {"status": "Deleted"}}
        yield from self._search("alert", org, query=query)

    def download_attachment(self, org: str, attachment_id: str, dest_path: str) -> str:
        """Stream `/api/datastore/{id}` to `dest_path`, verify sha256 matches id, return sha256."""
        r = self._request(
            "GET",
            f"/api/datastore/{attachment_id}",
            org=org,
            stream=True,
        )
        h = hashlib.sha256()
        with open(dest_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                h.update(chunk)
        got = h.hexdigest()
        if got.lower() != attachment_id.lower():
            raise RuntimeError(f"Attachment sha256 mismatch: expected {attachment_id}, got {got}")
        return got
