#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Destination checks for outbound requests the backend makes on behalf
of user-supplied content (VI-013).

The immediate caller is the DOCX report renderer, which hands any
external image URL found inside an uploaded template to the document
generator's remote fetcher. That fetch happens from inside the backend
network with the backend's egress rights, so `http://169.254.169.254/`
in a template body reaches the cloud metadata endpoint.

`egress_destination_error()` answers one question — "is this URL safe to
dereference server-side?" — and is deliberately independent of the HTTP
client used, so the other administrator-configured outbound integrations
can adopt it without a client rewrite.

Limitation worth stating: resolution happens here, the connection happens
in the caller, so this does not close a DNS-rebinding race on its own. It
does close the far more accessible cases — literal private addresses,
`localhost`, metadata IPs, and non-HTTP schemes.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

from app import app


_ALLOWED_SCHEMES = ('http', 'https')

def _address_is_blocked(address) -> bool:
    """Link-local covers 169.254.0.0/16 — the AWS/Azure/GCP metadata
    endpoint — and fe80::/10. `is_global` would be the terser test but it
    also rejects addresses that are legitimate targets on an internal
    deployment, so the categories are enumerated instead.
    """
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve(hostname):
    """Every address the hostname resolves to, or None on failure.

    All of them are checked, not just the first: a host with both a public
    A record and a private one must not pass because the resolver happened
    to order the public one first.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None

    return {info[4][0] for info in infos}


def allow_private_egress() -> bool:
    """Whether private destinations are permitted for content-driven fetches.

    Off by default. On-prem deployments that legitimately host their image
    assets on an internal address can set `IRIS_ALLOW_PRIVATE_EGRESS=True`
    and accept the SSRF exposure knowingly rather than by omission.
    """
    return bool(app.config.get('ALLOW_PRIVATE_EGRESS'))


def egress_destination_error(url):
    """Return why `url` must not be fetched server-side, or None if it may be.

    The message is for the log, not for the requester — it names the
    destination, which the caller already controls.
    """
    if not url or not isinstance(url, str):
        return 'empty URL'

    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"scheme '{parts.scheme}' is not allowed (use http or https)"

    hostname = parts.hostname
    if not hostname:
        return 'URL has no host'

    if allow_private_egress():
        return None

    # A literal IP never reaches the resolver, so test it directly first.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if _address_is_blocked(literal):
            return f'destination {hostname} is a private or reserved address'
        return None

    addresses = _resolve(hostname)
    if addresses is None:
        return f'host {hostname} could not be resolved'

    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return f'host {hostname} resolved to an unusable address'
        if _address_is_blocked(address):
            return f'host {hostname} resolves to a private or reserved address'

    return None
