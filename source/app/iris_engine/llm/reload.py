#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Hook called from `manage_routes/server.py` when a chatbot_* field flips.

Today `get_llm_provider()` reads settings on every call (no per-request
cache), so a settings edit is picked up on the next turn without an
explicit invalidation step. This module still exists so the PUT-handler
pattern (see `reload_error_reporter`) is consistent — if we add a
cached provider later, only this hook needs to grow the invalidation
logic.
"""
from __future__ import annotations

import logging


logger = logging.getLogger('iris.chatbot.reload')


def reload_llm_client(_app=None) -> None:
    """No-op placeholder for the reload-side-effect pattern.

    Signature matches `reload_error_reporter(app)` so the manage/server
    PUT handler can call it uniformly. The `_app` argument is unused
    today but reserved for future flush-on-app-context work.
    """
    logger.info('llm client cache invalidation requested (no-op in v1)')
