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

"""What one caller is allowed to see of the asset registry.

A plain value object — same role as `PaginationParameters`. It lives in
`models` rather than `business` or `datamgmt` because it crosses the
layer boundary: the business layer builds it from the access-control
helpers, the datamgmt layer turns it into SQL predicates. Neither layer
may import the other, so the shared vocabulary has to sit below both.

`None` means "unrestricted" for either dimension, which is how a server
administrator is represented. An *empty* collection means the opposite —
"nothing is visible" — and must produce a deny, never an absent filter.
Conflating the two is the single easiest way to turn this into a data
leak, hence `has_case_access` / `has_customer_access` rather than
truthiness checks at the call sites.
"""


class ManagedAssetViewerScope:

    def __init__(self, client_ids=None, case_ids=None, is_administrator=False):
        self._client_ids = client_ids
        self._case_ids = case_ids
        self._is_administrator = is_administrator

    def get_client_ids(self):
        """Customer ids the caller may read, or None for "all"."""
        return self._client_ids

    def get_case_ids(self):
        """Case ids the caller may read, or None for "all"."""
        return self._case_ids

    def is_administrator(self):
        return self._is_administrator

    def is_restricted(self):
        """True when derived counts/timestamps are computed over a subset.

        Reported to the caller as a single top-level flag so the UI can
        say "this is partial" without ever revealing *what* is missing.
        A per-asset "hidden sightings" indicator would be a one-bit
        oracle for the existence of investigations the caller is not
        cleared for, enumerable across the registry in one sweep.
        """
        return not self._is_administrator

    def has_customer_access(self):
        return self._client_ids is None or len(self._client_ids) > 0

    def has_case_access(self):
        return self._case_ids is None or len(self._case_ids) > 0
