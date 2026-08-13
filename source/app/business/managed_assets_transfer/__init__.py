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

"""Bulk CSV / JSON movement of the asset registry.

Deliberately much smaller in scope than `case_transfer`:

* Only `text/csv` and `application/json` are accepted. No zip, no gzip —
  which removes the archive-bomb class outright instead of mitigating it.
* An import writes to `managed_asset` and nothing else. It never creates
  a case, an alert, or a `case_assets` row.
* One import targets exactly one customer, pinned at inspect and
  re-checked at apply.

`exporter` renders visible registry rows; `importer` stages an upload,
reports row-by-row what applying it would do, then applies it under an
operator-chosen conflict policy. `staging` holds the file between the
two requests.
"""
