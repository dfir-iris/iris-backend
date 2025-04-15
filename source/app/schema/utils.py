#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

import hashlib
from pathlib import Path
from marshmallow import ValidationError

from app.logger import logger


def assert_type_mml(input_var: any, field_name: str, type: type, allow_none: bool = False,
                    max_len: int = None, max_val: int = None, min_val: int = None):
    if input_var is None:
        if allow_none is False:
            raise ValidationError('Invalid data - non null expected',
                                  field_name=field_name if field_name else 'type')
        return True

    if isinstance(input_var, type):
        if max_len:
            if len(input_var) > max_len:
                raise ValidationError('Invalid data - max length exceeded',
                                      field_name=field_name if field_name else 'type')

        if max_val:
            if input_var > max_val:
                raise ValidationError('Invalid data - max value exceeded',
                                                  field_name=field_name if field_name else 'type')

        if min_val:
            if input_var < min_val:
                raise ValidationError('Invalid data - min value exceeded',
                                                  field_name=field_name if field_name else 'type')

        return True

    try:

        if isinstance(type(input_var), type):
            return True

    except Exception as e:
        logger.error(e)
        print(e)

    raise ValidationError('Invalid data type', field_name=field_name if field_name else 'type')


def str_to_bool(value):
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return bool(value)

    return value.lower() in ['true', '1', 'yes', 'y', 't']


def stream_sha256sum(stream):

    return hashlib.sha256(stream).hexdigest().upper()


def file_sha256sum(file_path):

    if not Path(file_path).is_file():
        return None

    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

        return sha256_hash.hexdigest().upper()
