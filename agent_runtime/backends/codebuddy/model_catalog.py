"""CodeBuddy model discovery from the installed CLI declaration.

The accepted CodeBuddy CLI does not currently expose a confirmed machine-readable
model catalog on the TP-Voyager route.  The CLI help text is still an official
observable contract and can safely provide a bounded *declared* model list.
This adapter never starts a Crew session and never treats CLI declaration as an
account-entitlement guarantee.
"""

from __future__ import annotations

import re
import subprocess
import time

from agent_runtime.backends.codebuddy.process import resolve_codebuddy_cli
from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.domain.crew import ModelDescriptor

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SUPPORTED = re.compile(
    r"Currently\s+supported\s*:\s*\((?P<models>[^)]{1,4096})\)",
    re.IGNORECASE | re.DOTALL,
)
_MODEL_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")


def _clean(value: str) -> str:
    return _ANSI.sub("", str(value or "")).strip()


def parse_codebuddy_help_models(
    text: str, *, observed_at: float | None = None,
) -> list[ModelDescriptor]:
    """Parse only the CLI-declared ``Currently supported: (...)`` model list."""
    raw = _clean(text)
    match = _SUPPORTED.search(raw)
    if match is None:
        return []
    stamp = observed_at if observed_at is not None else time.time()
    seen: set[str] = set()
    result: list[ModelDescriptor] = []
    for token in re.split(r"\s*,\s*", match.group("models")):
        model_id = _clean(token)
        if not model_id or model_id in seen or not _MODEL_TOKEN.fullmatch(model_id):
            continue
        seen.add(model_id)
        result.append(
            ModelDescriptor(
                backend="codebuddy",
                model_id=model_id,
                display_name=model_id,
                available=None,
                source="cli_declared",
                observed_at=stamp,
                metadata={
                    "catalog_status": "declared_by_cli",
                    "entitlement_status": "unknown",
                    "billing": {"status": "unknown"},
                    "capabilities": {"status": "unknown", "source": "cli_declared_id_only"},
                },
            )
        )
        if len(result) >= 128:
            break
    return result


def list_codebuddy_models() -> list[ModelDescriptor]:
    """Return the bounded model list declared by the installed CodeBuddy CLI."""
    cli = resolve_codebuddy_cli()
    completed = subprocess.run(
        [cli, "--help"],
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
        raise BackendUnavailableError("CodeBuddy model discovery failed")
    # Some Windows CLIs write help to stderr.  Parse both without exposing it.
    models = parse_codebuddy_help_models(
        "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    )
    if not models:
        raise BackendUnavailableError("CodeBuddy CLI did not declare a model catalog")
    return models
