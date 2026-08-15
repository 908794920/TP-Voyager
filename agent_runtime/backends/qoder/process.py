"""Qoder CLI discovery and cross-platform process-tree control."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Sequence

from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError


def resolve_qoder_cli() -> str:
    """Resolve Qoder as env override -> user config -> PATH."""
    try:
        crew = VoyagerUserConfig.load().crew.qoder
    except VoyagerUserConfigError as exc:
        raise BackendUnavailableError("TP-Voyager user config is invalid") from exc
    if not crew.enabled:
        raise BackendUnavailableError("Qoder Crew is disabled in TP-Voyager config")
    configured = (os.environ.get("QODER_CLI_PATH") or "").strip() or crew.cli_path
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise BackendUnavailableError("Configured Qoder CLI was not found")
    for name in ("qodercli", "qodercli.cmd", "qodercli.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise BackendUnavailableError("Qoder CLI is not installed or not on PATH")


def popen_command(command: Sequence[str], *, cwd: str) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(command), **kwargs)  # type: ignore[arg-type]


def terminate_process_tree(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    """Terminate the complete CLI process tree; safe to call repeatedly."""
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


def probe_qoder_cli() -> dict[str, object]:
    """Probe installed Qoder CLI version without dispatching a task."""
    cli = resolve_qoder_cli()
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
        raise BackendUnavailableError("Qoder CLI version probe failed")
    auth_mode = "pat_env" if (os.environ.get("QODER_PERSONAL_ACCESS_TOKEN") or "").strip() else "local_or_unknown"
    return {
        "installed": True,
        "version": (completed.stdout or completed.stderr).strip().splitlines()[0] if (completed.stdout or completed.stderr).strip() else None,
        "auth_mode": auth_mode,
        "dispatch_ready": True,
    }
