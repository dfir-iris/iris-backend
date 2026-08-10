"""Route a TH4 observable to IRIS `Ioc` or `CaseAssets` based on `ioc` flag.

Per the migration plan: ``ioc=true`` → `Ioc`, ``ioc=false`` → `CaseAssets`.
Attachment observables always land as file rows regardless of the flag.

`asset_type_for_datatype` / `ioc_type_for_datatype` map TH4 `dataType` to the
name IRIS uses in `assets_type.asset_name` / `ioc_type.type_name`. Anything
not mapped falls back to ``other`` / ``Other``.
"""

from __future__ import annotations

TARGET_IOC = "ioc"
TARGET_ASSET = "asset"


TH4_DATATYPE_TO_ASSET_TYPE = {
    "ip": "IP Address",
    "domain": "Domain",
    "fqdn": "Domain",
    "hostname": "Host - Windows",
    "mail": "Account",
    "user-agent": "Other",
    "autonomous-system": "Other",
}


TH4_DATATYPE_TO_IOC_TYPE = {
    "hash": "hash",
    "file": "file",
    "filename": "filename",
    "url": "url",
    "uri_path": "url",
    "registry": "registry-key",
    "regexp": "other",
    "mail_subject": "email-subject",
    "domain": "domain",
    "fqdn": "domain",
    "ip": "ip-any",
    "mail": "email",
    "user-agent": "user-agent",
}


def route(obs: dict) -> str:
    return TARGET_IOC if obs.get("ioc") else TARGET_ASSET


def asset_type_for_datatype(dt: str | None) -> str:
    if not dt:
        return "Other"
    return TH4_DATATYPE_TO_ASSET_TYPE.get(dt, "Other")


def ioc_type_for_datatype(dt: str | None) -> str:
    if not dt:
        return "other"
    return TH4_DATATYPE_TO_IOC_TYPE.get(dt, "other")
