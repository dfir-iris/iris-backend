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

"""Render visible registry rows as CSV or JSON.

Generated server-side, never in the browser: the browser only ever holds
one access-filtered page, so a client-side export would silently produce
a truncated inventory that looks complete.

Registry columns only. No sighting counts, no first/last seen — those are
per-viewer values, and freezing one caller's view of them into a file
that then gets shared is exactly the leak the counts policy exists to
prevent.
"""

import csv
import io
import json

from app.datamgmt.case.assets_type import get_assets_types
from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_client_names

EXPORT_FORMATS = ('csv', 'json')

# Ordered so the file round-trips: this is exactly the header the importer
# reads back, and `client_name` / `asset_type` are the human-readable
# identities rather than raw ids, so a file exported from one instance can
# be imported into another.
EXPORT_COLUMNS = (
    'client_name',
    'name',
    'asset_type',
    'description',
    'criticality',
    'environment',
    'owner',
    'location',
    'tags',
    'ip',
    'domain',
    'is_active',
    'source',
    'custom_attributes',
)

# A leading one of these turns the cell into a formula when the file is
# opened in a spreadsheet. Tab and CR are in the set because Excel strips
# them and then evaluates whatever followed.
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _sanitize_cell(value):
    """Neutralise spreadsheet formula injection.

    A `'` prefix is the portable escape — Excel, LibreOffice and Sheets
    all render the cell as literal text. Note this is deliberately not
    the frontend's `escapeCSVValue`, which quotes but does not guard
    against formulas at all.
    """
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _row_dict(asset, client_names, type_names):
    return {
        'client_name': client_names.get(asset.client_id, ''),
        'name': asset.name,
        'asset_type': type_names.get(asset.asset_type_id, ''),
        'description': asset.description,
        'criticality': asset.criticality,
        'environment': asset.environment,
        'owner': asset.owner,
        'location': asset.location,
        'tags': asset.tags,
        'ip': asset.ip,
        'domain': asset.domain,
        'is_active': asset.is_active,
        'source': asset.source,
        'custom_attributes': asset.custom_attributes,
    }


def _lookups(assets):
    client_names = managed_assets_db_client_names({asset.client_id for asset in assets})
    type_names = {asset_id: asset_name for asset_id, asset_name in get_assets_types()}
    return client_names, type_names


def export_csv(assets):
    """Render `assets` as RFC 4180 CSV with `\\r\\n` line endings.

    `QUOTE_ALL` so a value containing a delimiter, a quote or a newline
    cannot break out of its cell — and so a reader never has to guess
    whether an empty cell meant empty string or NULL.
    """
    client_names, type_names = _lookups(assets)

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(EXPORT_COLUMNS)

    for asset in assets:
        row = _row_dict(asset, client_names, type_names)
        writer.writerow([
            _sanitize_cell(
                json.dumps(row[column], sort_keys=True)
                if column == 'custom_attributes' and row[column] is not None
                else row[column]
            )
            for column in EXPORT_COLUMNS
        ])

    # BOM so Excel opens UTF-8 hostnames correctly instead of mojibake.
    # The importer strips it back off before parsing the header.
    return '﻿' + buffer.getvalue()


def export_json(assets):
    """Render `assets` as a JSON document the importer accepts verbatim.

    Wrapped in an object rather than returned as a bare array so the
    format has somewhere to grow, and so the response is not a top-level
    array (a JSON hijacking shape, harmless here but not worth shipping).
    """
    client_names, type_names = _lookups(assets)
    rows = []
    for asset in assets:
        row = _row_dict(asset, client_names, type_names)
        rows.append({key: (value if value is not None else None) for key, value in row.items()})

    return json.dumps({'version': 1, 'assets': rows}, indent=2, default=str)


def export_assets(assets, export_format):
    """Return `(payload, content_type, extension)` for the chosen format."""
    if export_format == 'json':
        return export_json(assets), 'application/json', 'json'
    return export_csv(assets), 'text/csv; charset=utf-8', 'csv'
