"""Read a bounded Qoder account snapshot through the official SDK.

This adapter opens Qoder's control plane only.  It never submits an Agent
prompt, imports a task session, or exposes account identifiers/tokens to the
MCP profile projection.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.backends.qoder.process import resolve_qoder_cli


QODER_ACCOUNT_USAGE_MARKER = "TP_VOYAGER_QODER_ACCOUNT_USAGE="


def _unavailable_snapshot() -> dict[str, object]:
    return {
        "status": "unavailable",
        "auth_status": "unknown",
        "user_type": None,
        "is_quota_exceeded": None,
        "user_quota": {},
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None


def _quota_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    projected: dict[str, object] = {}
    for key in ("total", "used", "remaining", "percentage"):
        number = _finite_number(value.get(key))
        if number is not None:
            projected[key] = number
    unit = value.get("unit")
    if isinstance(unit, str) and unit.strip():
        projected["unit"] = unit.strip()[:32]
    return projected


def parse_qoder_account_usage_output(text: str) -> dict[str, object]:
    """Parse the isolated SDK output and discard all account identity fields."""
    payload_text = ""
    for line in text.splitlines():
        if line.startswith(QODER_ACCOUNT_USAGE_MARKER):
            payload_text = line[len(QODER_ACCOUNT_USAGE_MARKER):].strip()
    if not payload_text:
        return _unavailable_snapshot()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return _unavailable_snapshot()
    if not isinstance(payload, dict):
        return _unavailable_snapshot()

    user_type = payload.get("userType")
    safe_user_type = (
        user_type.strip()[:120]
        if isinstance(user_type, str) and user_type.strip()
        else None
    )
    quota = _quota_projection(payload.get("userQuota"))
    quota_exceeded = payload.get("isQuotaExceeded")
    safe_quota_exceeded = quota_exceeded if isinstance(quota_exceeded, bool) else None
    authenticated = safe_user_type is not None or bool(quota) or safe_quota_exceeded is not None
    return {
        "status": "observed",
        "auth_status": "verified" if authenticated else "unknown",
        "user_type": safe_user_type,
        "is_quota_exceeded": safe_quota_exceeded,
        "user_quota": quota,
    }


def collect_qoder_account_snapshot() -> dict[str, object]:
    """Fetch Qoder's current quota with the SDK, without creating an Agent task."""
    try:
        cli = resolve_qoder_cli()
    except BackendUnavailableError:
        return _unavailable_snapshot()

    # Keep the child output account-safe too: never print user IDs, sessions,
    # access tokens, or the provider's unbounded raw response.
    script = "\n".join([
        "import asyncio, json, sys",
        "from qoder_agent_sdk import QoderAgentOptions, QoderSDKClient, qodercli_auth",
        "def get(value, camel, snake):",
        "    if isinstance(value, dict): return value.get(camel, value.get(snake))",
        "    return getattr(value, camel, getattr(value, snake, None))",
        "async def main():",
        "    options = QoderAgentOptions(auth=qodercli_auth(), cli_path=sys.argv[1])",
        "    async with QoderSDKClient(options=options) as client:",
        "        usage = await client.get_usage_info()",
        "    quota = get(usage, 'userQuota', 'user_quota')",
        "    safe_quota = {key: get(quota, key, key) for key in ('total', 'used', 'remaining', 'percentage', 'unit')}",
        "    safe = {'userType': get(usage, 'userType', 'user_type'), 'isQuotaExceeded': get(usage, 'isQuotaExceeded', 'is_quota_exceeded'), 'userQuota': safe_quota}",
        "    print('TP_VOYAGER_QODER_ACCOUNT_USAGE=' + json.dumps(safe, ensure_ascii=False, separators=(',', ':')))",
        "asyncio.run(main())",
    ])
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, cli],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return _unavailable_snapshot()
    if completed.returncode != 0:
        return _unavailable_snapshot()
    return parse_qoder_account_usage_output(completed.stdout)
