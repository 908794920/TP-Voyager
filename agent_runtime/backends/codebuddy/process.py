"""Safe CodeBuddy CLI discovery/probe helpers (no dispatch)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

from agent_runtime.backends.errors import BackendUnavailableError


def resolve_codebuddy_cli() -> str:
    configured = (os.environ.get("CODEBUDDY_CODE_PATH") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise BackendUnavailableError("Configured CodeBuddy CLI was not found")
    for name in ("codebuddy", "codebuddy.cmd", "codebuddy.exe", "cbc", "cbc.cmd", "cbc.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise BackendUnavailableError("CodeBuddy CLI is not installed or not on PATH")


def probe_codebuddy_cli() -> dict[str, object]:
    cli = resolve_codebuddy_cli()
    completed = subprocess.run(
        [cli, "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise BackendUnavailableError("CodeBuddy CLI version probe failed")
    # TP-Voyager's accepted CodeBuddy Crew is the China account route.
    # An explicit operator override is preserved for diagnostics, but an
    # unset environment must agree with the SDK adapter's CN default.
    env = (os.environ.get("CODEBUDDY_INTERNET_ENVIRONMENT") or "internal").strip().lower() or "internal"
    region = "cn" if env == "internal" else ("ioa" if env == "ioa" else "international")
    auth_configured = bool(
        (os.environ.get("CODEBUDDY_AUTH_TOKEN") or "").strip()
        or (os.environ.get("CODEBUDDY_API_KEY") or "").strip()
    )
    sdk_installed = importlib.util.find_spec("codebuddy_agent_sdk") is not None
    return {
        # The accepted Captain route requires both the CLI and the official
        # Python SDK.  Keep the CLI fact separately so health can explain why
        # an otherwise installed vendor tool is not dispatchable.
        "installed": sdk_installed,
        "cli_installed": True,
        "sdk_installed": sdk_installed,
        "version": (completed.stdout or completed.stderr).strip().splitlines()[0] if (completed.stdout or completed.stderr).strip() else None,
        "region": region,
        "environment_auth_configured": auth_configured,
        "auth_probe_performed": False,
        "dispatch_ready": sdk_installed,
    }
