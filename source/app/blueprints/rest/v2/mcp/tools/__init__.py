"""MCP tool wrappers.

One module per domain. Wrappers are thin: they translate the JSON-RPC
`arguments` dict into the shape `app.business.*` expects, call the
business function, and marshal the result via the same Marshmallow
schema the REST endpoint uses.

No direct `app.datamgmt.*` or `sqlalchemy` imports allowed here — the
import-linter enforces the layering.
"""
