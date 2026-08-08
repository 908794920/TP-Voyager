"""Centralized time helpers for durable runtime storage.

All persisted timestamps are Unix epoch seconds (UTC), matching the existing
in-process task timestamps so public projections stay byte-compatible.
"""

from __future__ import annotations

import time


def now_epoch() -> float:
    """Current UTC Unix epoch seconds, the single storage clock."""
    return time.time()
