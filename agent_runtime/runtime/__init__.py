"""Process-local runtime mechanics.

Durable domain state lives in :mod:`agent_runtime.domain` and SQLite access
lives in :mod:`agent_runtime.persistence`.  This package is intentionally
limited to process handles, lease/watchdog mechanics, backend callbacks and
runtime diagnostics.
"""
