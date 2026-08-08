"""Qoder account model discovery using the official ``--list-models`` CLI."""

from __future__ import annotations

import json
import re
import subprocess
import time

from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.backends.qoder.process import resolve_qoder_cli
from agent_runtime.domain.crew import ModelDescriptor

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _clean(value: str) -> str:
    return _ANSI.sub("", value).strip()


def parse_list_models_output(text: str, *, observed_at: float | None = None) -> list[ModelDescriptor]:
    raw = _clean(text)
    if not raw:
        return []
    stamp = observed_at if observed_at is not None else time.time()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        result: list[ModelDescriptor] = []
        for item in payload:
            if isinstance(item, str) and item.strip():
                result.append(ModelDescriptor("qoder", item.strip(), source="official_dynamic", observed_at=stamp))
            elif isinstance(item, dict):
                model_id = str(item.get("value") or item.get("id") or item.get("model") or "").strip()
                if not model_id:
                    continue
                result.append(
                    ModelDescriptor(
                        backend="qoder",
                        model_id=model_id,
                        display_name=str(item.get("displayName") or item.get("name") or model_id),
                        available=bool(item.get("isEnabled", True)),
                        disabled_reason=None if item.get("isEnabled", True) else "disabled_by_account",
                        source="official_dynamic",
                        observed_at=stamp,
                    )
                )
        return result

    result: list[ModelDescriptor] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = _clean(line)
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(("model ", "models", "available models", "name ", "id ")):
            continue
        if set(line) <= set("-_=+| ─│┌┐└┘├┤┬┴┼"):
            continue
        if "|" in line:
            cols = [part.strip() for part in line.strip("|").split("|") if part.strip()]
        else:
            cols = [part.strip() for part in re.split(r"\t+|\s{2,}", line) if part.strip()]
        if not cols:
            continue
        model_id = cols[0]
        if model_id.lower() in {"model", "name", "id", "value"} or model_id in seen:
            continue
        if len(model_id) > 120:
            continue
        seen.add(model_id)
        result.append(
            ModelDescriptor(
                backend="qoder",
                model_id=model_id,
                display_name=cols[1] if len(cols) > 1 else model_id,
                source="official_dynamic",
                observed_at=stamp,
            )
        )
    return result


def list_qoder_models() -> list[ModelDescriptor]:
    cli = resolve_qoder_cli()
    completed = subprocess.run(
        [cli, "--list-models"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise BackendUnavailableError("Qoder model discovery failed")
    return parse_list_models_output(completed.stdout)
