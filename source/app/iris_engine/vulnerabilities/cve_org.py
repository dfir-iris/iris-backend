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

"""Client of the CVE Services record API of the CVE Program (cve.org).

`GET <base>/<CVE-ID>` answers the CVE JSON 5 record of a published or
rejected CVE, and a 404 with `{"error": "CVE_RECORD_DNE"}` otherwise.
Only the identifier is sent. The server-settings proxies apply on top
of the standard `HTTP(S)_PROXY` environment variables, redirects are not
followed and the body is capped, so a misbehaving mirror cannot make the
backend download anything large.
"""

import json
import re

import requests

_CVE_RE = re.compile(r'^CVE-\d{4}-\d{4,}$')
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


class CveOrgError(Exception):
    """The record could not be fetched. `not_found` tells an unknown CVE
    apart from a network or API failure."""

    def __init__(self, message, not_found=False):
        super().__init__(message)
        self.message = message
        self.not_found = not_found


def _read_capped(response):
    chunks = []
    size = 0
    for chunk in response.iter_content(_CHUNK_BYTES):
        size += len(chunk)
        if size > _MAX_RECORD_BYTES:
            raise CveOrgError('The CVE record is too large')
        chunks.append(chunk)
    return b''.join(chunks)


def cve_org_fetch(base_url, cve_id, timeout=10, proxies=None) -> dict:
    """CVE JSON 5 record of `cve_id` (an upper-case `CVE-YYYY-NNNN`)."""
    if not _CVE_RE.fullmatch(cve_id or ''):
        raise CveOrgError(f'{cve_id} is not a CVE identifier')
    url = f'{base_url.rstrip("/")}/{cve_id}'
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    try:
        with session.get(url, timeout=timeout, stream=True, allow_redirects=False,
                         headers={'Accept': 'application/json'}) as response:
            raw = _read_capped(response)
            status = response.status_code
    except requests.Timeout:
        raise CveOrgError('The CVE API did not answer in time')
    except requests.RequestException as e:
        raise CveOrgError(f'The CVE API is unreachable ({type(e).__name__})')
    finally:
        session.close()

    try:
        payload = json.loads(raw.decode('utf-8')) if raw else None
    except (UnicodeDecodeError, ValueError):
        payload = None

    if status == 404 or (isinstance(payload, dict) and payload.get('error') == 'CVE_RECORD_DNE'):
        raise CveOrgError(f'{cve_id} is not a published CVE record', not_found=True)
    if status != 200:
        raise CveOrgError(f'The CVE API answered HTTP {status}')
    if not isinstance(payload, dict) or not isinstance(payload.get('cveMetadata'), dict):
        raise CveOrgError('The CVE API answered something that is not a CVE record')
    return payload
