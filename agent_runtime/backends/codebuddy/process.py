"""Safe CodeBuddy CLI discovery/probe helpers (no dispatch)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Mapping, Sequence
import shutil
import signal
import subprocess

from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError


def _codebuddy_config():
    try:
        return VoyagerUserConfig.load().crew.codebuddy
    except VoyagerUserConfigError as exc:
        raise BackendUnavailableError("TP-Voyager user config is invalid") from exc


def resolve_codebuddy_internet_environment() -> str:
    crew = _codebuddy_config()
    configured = (os.environ.get("CODEBUDDY_INTERNET_ENVIRONMENT") or "").strip().lower()
    return configured or crew.internet_environment


def resolve_codebuddy_cli() -> str:
    crew = _codebuddy_config()
    if not crew.enabled:
        raise BackendUnavailableError("CodeBuddy Crew is disabled in TP-Voyager config")
    configured = (os.environ.get("CODEBUDDY_CODE_PATH") or "").strip() or crew.cli_path
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



def popen_command(
    command: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Launch CodeBuddy in a separate process group for bounded cleanup."""
    process_env = os.environ.copy()
    if env:
        process_env.update({str(key): str(value) for key, value in env.items()})
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
        "env": process_env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(command), **kwargs)  # type: ignore[arg-type]


def terminate_process_tree(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    """Terminate the complete native ACP CLI process tree; idempotent."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass

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
    env = resolve_codebuddy_internet_environment()
    region = "cn" if env == "internal" else ("ioa" if env == "ioa" else "international")
    auth_configured = bool(
        (os.environ.get("CODEBUDDY_AUTH_TOKEN") or "").strip()
        or (os.environ.get("CODEBUDDY_API_KEY") or "").strip()
    )
    sdk_installed = importlib.util.find_spec("codebuddy_agent_sdk") is not None
    return {
        # Native ACP dispatch requires the configured CLI only.  Keep SDK
        # presence informational for explicit compatibility routes.
        "installed": True,
        "cli_installed": True,
        "sdk_installed": sdk_installed,
        "version": (completed.stdout or completed.stderr).strip().splitlines()[0] if (completed.stdout or completed.stderr).strip() else None,
        "region": region,
        "environment_auth_configured": auth_configured,
        "auth_probe_performed": False,
        "dispatch_ready": True,
    }
