"""Compatibility entry point for Agent Runtime MCP.

The implementation lives in :mod:`agent_runtime.api.mcp_server`.
Existing imports of ``agent_runtime.server`` intentionally resolve to the
same module object so monkeypatching/legacy callers keep their semantics.
"""
from __future__ import annotations

import sys
from agent_runtime.api import mcp_server as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
