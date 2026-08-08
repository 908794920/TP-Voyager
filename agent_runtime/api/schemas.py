"""Public API schema constants and boundary marker for V1.7.

Concrete request validation remains colocated with adapters until V2 changes
require dedicated DTOs; this module is the stable home for new public schemas.
"""
from __future__ import annotations

PUBLIC_API_VERSION = "workbuddy.api/v1"
