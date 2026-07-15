#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Business-layer re-exports for custom-attributes persistence."""

from app.datamgmt.manage.manage_attribute_db import update_all_attributes
from app.datamgmt.manage.manage_attribute_db import validate_attribute


__all__ = ['update_all_attributes', 'validate_attribute']
