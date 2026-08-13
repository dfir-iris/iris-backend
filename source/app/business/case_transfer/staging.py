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

"""Scratch directories for in-flight bundles.

An import is two requests — inspect, then apply — so the uploaded bundle has to
survive between them. It sits here, decrypted, until the operator applies or
discards it, or the TTL sweeps it.

Every workspace is named by a fresh random token, and the token is the only way
back to it. `resolve()` refuses anything that is not a bare hex token, so a
caller-supplied id can never walk out of the staging root.
"""

import json
import re
import secrets
import shutil
import time
from pathlib import Path

from app import app
from app.logger import logger
from app.models.errors import ObjectNotFoundError

_STAGING_DIRNAME = 'case_transfer_staging'
_TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')
_META_FILE = 'staging.json'


def _staging_root() -> Path:
    root = Path(app.config['UPLOADED_PATH']) / _STAGING_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


class TransferWorkspace:
    """A directory that holds one in-flight bundle."""

    def __init__(self, token: str, path: Path):
        self.token = token
        self.path = path

    def __truediv__(self, name: str) -> Path:
        # Only ever joined with literals from our own code. Kept explicit so a
        # future caller cannot quietly pass something from a request body.
        if '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError(f'Invalid workspace entry name {name!r}')
        return self.path / name

    def write_metadata(self, payload: dict):
        (self.path / _META_FILE).write_text(json.dumps(payload, default=str), encoding='utf-8')

    def read_metadata(self) -> dict:
        try:
            return json.loads((self.path / _META_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ObjectNotFoundError()

    def discard(self):
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except OSError:
            logger.warning(f'Case transfer: could not remove workspace {self.token}')


def transfer_workspace() -> TransferWorkspace:
    token = secrets.token_hex(16)
    path = _staging_root() / token
    path.mkdir(parents=True, exist_ok=False)
    return TransferWorkspace(token, path)


def resolve(token: str) -> TransferWorkspace:
    """Return the workspace for `token`, or raise if it is not a live one."""
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        raise ObjectNotFoundError()

    path = _staging_root() / token
    if not path.is_dir():
        raise ObjectNotFoundError()

    return TransferWorkspace(token, path)


def sweep_expired():
    """Drop workspaces older than the configured TTL.

    Called opportunistically at the start of an import request rather than on a
    timer: staging only accumulates when someone is importing, so that is
    exactly when it is worth looking.
    """
    ttl_seconds = int(app.config.get('CASE_TRANSFER_STAGING_TTL_MINUTES', 120)) * 60
    cutoff = time.time() - ttl_seconds

    try:
        candidates = list(_staging_root().iterdir())
    except OSError:
        return

    for entry in candidates:
        if not entry.is_dir() or not _TOKEN_RE.match(entry.name):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
