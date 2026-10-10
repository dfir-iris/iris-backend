#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Case datastore: business-layer re-exports of the persistence functions,
plus the module hooks a file upload, update or deletion fires.

Keeps `app.blueprints.rest.v2.case_routes.datastore` off `app.datamgmt`
directly so the layering contract passes. The hooks are what webhooks
and AI workflows subscribe to (`on_postload_datastore_file_create`, ...);
a file flagged as evidence also fires `on_postload_evidence_create` for
the evidence it creates.
"""

from app.datamgmt.datastore.datastore_db import datastore_add_child_node
from app.datamgmt.datastore.datastore_db import datastore_add_file_as_evidence as _add_file_as_evidence
from app.datamgmt.datastore.datastore_db import datastore_add_file_as_ioc
from app.datamgmt.datastore.datastore_db import datastore_delete_file as _delete_file
from app.datamgmt.datastore.datastore_db import datastore_delete_node
from app.datamgmt.datastore.datastore_db import datastore_get_file
from app.datamgmt.datastore.datastore_db import datastore_get_interactive_path_node
from app.datamgmt.datastore.datastore_db import datastore_get_local_file_path
from app.datamgmt.datastore.datastore_db import datastore_get_path_node
from app.datamgmt.datastore.datastore_db import datastore_get_standard_path
from app.datamgmt.datastore.datastore_db import datastore_rename_node
from app.datamgmt.datastore.datastore_db import ds_list_tree
from app.iris_engine.module_handler.module_handler import call_modules_hook


__all__ = [
    'datastore_add_child_node',
    'datastore_add_file_as_evidence',
    'datastore_add_file_as_ioc',
    'datastore_delete_file',
    'datastore_delete_node',
    'datastore_get_file',
    'datastore_get_interactive_path_node',
    'datastore_get_local_file_path',
    'datastore_get_path_node',
    'datastore_get_standard_path',
    'datastore_hook_file_saved',
    'datastore_rename_node',
    'ds_list_tree',
]


def datastore_add_file_as_evidence(user_identifier, dsf, caseid):
    """Adds `dsf` to the case evidences unless one with its hash is there."""
    evidence = _add_file_as_evidence(user_identifier, dsf, caseid)
    if evidence is not None:
        call_modules_hook('on_postload_evidence_create', data=evidence, caseid=caseid)
    return evidence


def datastore_hook_file_saved(dsf, caseid, created):
    """Fires the hook of a file uploaded (`created`) or updated in the case datastore."""
    hook_name = 'on_postload_datastore_file_create' if created else 'on_postload_datastore_file_update'
    call_modules_hook(hook_name, data=dsf, caseid=caseid)


def datastore_delete_file(file_id, caseid):
    has_error, logs = _delete_file(file_id, caseid)
    if not has_error:
        call_modules_hook('on_postload_datastore_file_delete',
                          data={'id': file_id, 'file_id': file_id, 'case_id': caseid}, caseid=caseid)
    return has_error, logs
