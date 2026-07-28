#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP wire-protocol constants — vendored from the upstream spec.

Kept as plain module-level tuples/ints so the transport can be tested
without pulling the `mcp` PyPI SDK (which is ASGI-first — mismatch with
our WSGI stack). Update the version tuple as new protocol revisions ship.
"""

# Ordered oldest → newest. `LATEST_PROTOCOL_VERSION` is what we advertise
# when a client asks for a version we don't support.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    '2024-11-05',
    '2025-03-26',
    '2025-06-18',
    '2025-11-25',
)
LATEST_PROTOCOL_VERSION: str = SUPPORTED_PROTOCOL_VERSIONS[-1]

# --- JSON-RPC 2.0 error codes -----------------------------------------
# Standard codes (spec: https://www.jsonrpc.org/specification#error_object)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# IRIS-specific application errors. Codes in the -32000..-32099 range are
# reserved by JSON-RPC for implementation-defined server errors.
IRIS_ACCESS_DENIED = -32001
IRIS_RATE_LIMITED = -32002
IRIS_DISABLED = -32003
IRIS_UNAUTHENTICATED = -32004
