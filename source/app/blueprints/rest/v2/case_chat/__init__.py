#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Case-chat blueprint — the LLM-driven floating assistant.

Structure (mirrors the mcp/ package):
  * transport.py         — REST CRUD (conversations + messages)
  * namespace.py         — SocketIO `/chat` namespace (send + approve + deny)
  * loop.py              — stateless tool-use loop; one iteration per event
  * approve_tool.py      — dispatch the write, append role='tool', re-run loop
  * tool_classification.py — READ_ONLY_TOOLS + WRITE_TOOLS enumerations
  * diff_renderers.py    — human-readable "this will delete IOC #42" text
  * persistence.py       — thin business-layer facade

REST is registered on the v2 blueprint; the socket namespace is bound
inside `app/__init__.py` alongside the other class-based namespaces.
"""

from flask import Blueprint

case_chat_blueprint = Blueprint(
    'case_chat_rest_v2', __name__, url_prefix='/case-chat',
)

# ruff: noqa: F401
from app.blueprints.rest.v2.case_chat import transport as _transport
