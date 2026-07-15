#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Business-layer re-exports for generic DB helpers used by manage_routes."""

from app.datamgmt.db_operations import db_create
from app.datamgmt.db_operations import db_delete


__all__ = ['db_create', 'db_delete']
