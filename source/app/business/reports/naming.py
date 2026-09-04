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

"""Filename construction for generated reports (VI-009).

The rendered report's filename comes from `CaseTemplateReport.naming_format`,
a free-text admin field, with `%customer%` / `%case_name%` expanded from case
data that ordinary analysts control. Both were interpolated straight into
`os.path.join(tmp_dir, name)`, so a `../` anywhere in either wrote the
generated report outside the per-render temp directory with the backend's
privileges.

Two layers, because neither alone is sufficient:

* `naming_format_error()` rejects separators at the API boundary, so an
  admin gets a clear error instead of a silently mangled filename.
* `build_report_filename()` scrubs at the sink, which is the layer that
  actually closes the hole — it also covers the substituted case/customer
  names (not admin-controlled) and any row already persisted with a bad
  format before this landed.
"""

import os

from app.models.errors import BusinessProcessingError


# Path separators on either platform plus NUL; control characters are
# handled by the ordinal test alongside them.
_UNSAFE_CHARS = ('/', '\\')


def _sanitise_component(value) -> str:
    """Flatten a single interpolated value into one filename component."""
    return ''.join(
        '_' if (character in _UNSAFE_CHARS or ord(character) < 0x20) else character
        for character in str(value or '')
    )


def naming_format_error(naming_format):
    """Return an error message if `naming_format` is unusable, else None.

    Called by the report-template create/update endpoints. `None` and the
    empty string are accepted — the sink falls back to a default stem.
    """
    if not naming_format:
        return None

    if any(character in naming_format for character in _UNSAFE_CHARS):
        return 'Output filename format cannot contain path separators'

    if any(ord(character) < 0x20 for character in naming_format):
        return 'Output filename format cannot contain control characters'

    return None


def build_report_filename(naming_format, extension: str, substitutions: dict,
                          fallback: str = 'report') -> str:
    """Compose the output filename for a rendered report.

    `substitutions` maps a `%tag%` to its replacement; each replacement is
    scrubbed before it lands in the name so a case named `../../etc/x`
    can't steer the write either. The result is always a single path
    component with `extension` appended.
    """
    stem = str(naming_format or '')
    for tag, value in substitutions.items():
        stem = stem.replace(tag, _sanitise_component(value))

    # basename() is belt-and-braces after the separator scrub. Stripping
    # leading dots and underscores removes what a flattened `../../` leaves
    # behind, along with the `.hidden` form; the trailing strip keeps the
    # name usable on Windows, which rejects a trailing dot or space.
    stem = os.path.basename(_sanitise_component(stem)).strip().lstrip('._ ').rstrip('. ')
    if not stem:
        stem = fallback

    return f'{stem}{extension}'


def resolve_output_path(tmp_dir: str, filename: str) -> str:
    """Join `filename` onto `tmp_dir`, refusing anything that escapes it.

    `build_report_filename` should make this unreachable; it stays as the
    assertion that the invariant held, so a future edit to the naming
    logic fails loudly instead of writing outside the render directory.
    """
    root = os.path.realpath(tmp_dir)
    output_path = os.path.realpath(os.path.join(root, filename))
    if os.path.commonpath([root, output_path]) != root:
        raise BusinessProcessingError('Refusing to write the report outside the render directory')

    return output_path
