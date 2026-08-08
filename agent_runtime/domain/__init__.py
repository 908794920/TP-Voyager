"""Durable runtime domain models (no MCP / gateway / ACP / sqlite imports)."""

# V1.2 workflow types are intentionally not imported eagerly here; callers
# should import from ``runtime.domain.workflow`` to keep the legacy surface
# stable.
