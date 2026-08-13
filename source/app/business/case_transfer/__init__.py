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

"""Case transfer: move a whole case between two independent IRIS instances.

The two halves are deliberately separate:

* `exporter` serialises a case into a self-contained zip bundle whose foreign
  keys have been rewritten to bundle-local refs (see `manifest`).
* `importer` stages an uploaded bundle, reports what it cannot resolve on the
  target (`resolvers`), then applies it under an operator-supplied mapping.

`crypto` wraps the finished bundle in an optional AES-256-GCM envelope and
knows nothing about its contents.
"""
