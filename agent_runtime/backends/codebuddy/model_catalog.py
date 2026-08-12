"""CodeBuddy model discovery from the installed CLI declaration.

The accepted CodeBuddy CLI does not currently expose a confirmed machine-readable
model catalog on the TP-Voyager route.  The CLI help text is still an official
observable contract and can safely provide a bounded *declared* model list.
This adapter never starts a Crew session and never treats CLI declaration as an
account-entitlement guarantee.
"""

from __future__ import annotations

import re
import os
import queue
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime.backends.codebuddy.process import resolve_codebuddy_cli
from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.domain.crew import ModelDescriptor

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SUPPORTED = re.compile(
    r"Currently\s+supported\s*:\s*\((?P<models>[^)]{1,4096})\)",
    re.IGNORECASE | re.DOTALL,
)
_MODEL_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}$")
_MULTIPLIER = re.compile(r"^x(?P<value>0(?:\.\d+)?|[1-9]\d*(?:\.\d+)?)\s*(?:credits?)?$")
_MAX_CONFIG_NOTIFICATION_BYTES = 64 * 1024


class CodeBuddyCatalogError(BackendUnavailableError):
    """The account-live catalog protocol was unavailable or unsafe.

    This error deliberately carries no ACP transcript: catalog probing must not
    leak account data and never creates Runtime durable rows.
    """


@dataclass(frozen=True)
class CodeBuddyCatalogProbeResult:
    models: tuple[ModelDescriptor, ...]
    state_trace: tuple[str, ...]


def parse_credits_multiplier(value: object) -> tuple[str, float | None]:
    """Parse the reference-only ACP credit multiplier without estimating cost."""
    raw = str(value or "").strip()
    match = _MULTIPLIER.fullmatch(raw)
    if match is None:
        return raw, None
    parsed = float(match.group("value"))
    return raw, parsed if parsed >= 0 else None


class CodeBuddyAcpCatalogProbe:
    """Strict catalog-only ACP exchange.

    ``exchange`` is intentionally tiny so tests and production transports share
    one state machine.  It may receive only initialize, session/new, then a
    close/terminate notification.  Prompt, tool, terminal and permission
    callbacks are rejected before any model is projected.
    """

    _FORBIDDEN = frozenset({"session/prompt", "tool", "terminal", "permission"})

    def __init__(self, exchange: Callable[[str, dict[str, Any]], object]) -> None:
        self._exchange = exchange

    def probe(self) -> CodeBuddyCatalogProbeResult:
        trace: list[str] = []
        session_id = ""
        models: list[ModelDescriptor] | None = None
        try:
            trace.append("initialize")
            self._validate(self._exchange("initialize", {}))
            trace.append("session/new")
            response = self._validate(self._exchange("session/new", {}))
            if not isinstance(response, dict):
                raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
            result = response.get("result")
            if not isinstance(result, dict):
                raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
            session_id = str(result.get("sessionId") or result.get("session_id") or "")
            models = self._models(result, response.get("_meta"))
            if not models:
                raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        except (TimeoutError, KeyboardInterrupt) as exc:
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_UNAVAILABLE") from exc
        finally:
            # Closing is mandatory for every terminal outcome and is the only
            # permitted operation after session/new.
            try:
                self._exchange("close/terminate", {"sessionId": session_id} if session_id else {})
            except Exception:
                pass
            trace.append("close/terminate")
        return CodeBuddyCatalogProbeResult(tuple(models or ()), tuple(trace))

    def _validate(self, response: object) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        method = str(response.get("method") or "").strip().lower()
        # A JSON-RPC response to initialize/session-new must never itself be a
        # server request or notification.  Reject every callback, not only the
        # known prompt/tool/terminal/permission families, so a new ACP method
        # cannot silently expand this catalog-only state machine.
        if method:
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        if response.get("error"):
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_UNAVAILABLE")
        return response

    @staticmethod
    def _models(result: dict[str, Any], meta: object) -> list[ModelDescriptor]:
        catalog = result.get("models") if isinstance(result.get("models"), dict) else result
        models = catalog.get("availableModels") or catalog.get("available_models") or []
        if not isinstance(models, list):
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        credits = meta.get("credits") if isinstance(meta, dict) else None
        current_model_id = str(catalog.get("currentModelId") or catalog.get("current_model_id") or "")
        observed_at = time.time()
        output: list[ModelDescriptor] = []
        seen: set[str] = set()
        for item in models[:128]:
            data = item if isinstance(item, dict) else {"id": item}
            model_id = _clean(str(data.get("id") or data.get("modelId") or data.get("value") or ""))
            if not _MODEL_TOKEN.fullmatch(model_id) or model_id in seen:
                continue
            seen.add(model_id)
            item_meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
            item_credits = item_meta.get("credits") or data.get("description") or credits
            raw_multiplier, multiplier = parse_credits_multiplier(item_credits)
            output.append(ModelDescriptor(
                backend="codebuddy", model_id=model_id,
                display_name=_clean(str(data.get("displayName") or data.get("name") or model_id)),
                available=True, source="codebuddy_acp_account_live", observed_at=observed_at,
                metadata={"catalog_status": "complete", "entitlement_status": "account_live",
                          "current": model_id == current_model_id,
                          "billing": {"status": "reference_only", "multiplier_raw": raw_multiplier,
                                      "multiplier": multiplier, "calculation_allowed": False}},
            ))
        return output


def _is_expected_catalog_notification(response: object) -> bool:
    """Recognize the one passive directory notification emitted by CodeBuddy.

    Current CodeBuddy ACP emits ``session/update`` with a bounded
    ``config_option_update`` immediately before the matching ``session/new``
    response.  The catalog transport never consumes those option values and
    never answers the notification; all other callbacks remain invalid.
    """
    if not isinstance(response, dict) or "id" in response:
        return False
    if set(response) - {"jsonrpc", "method", "params"}:
        return False
    if str(response.get("method") or "").strip().lower() != "session/update":
        return False
    params = response.get("params")
    if not isinstance(params, dict) or set(params) - {"sessionId", "update", "_meta"}:
        return False
    if not str(params.get("sessionId") or "").strip():
        return False
    if params.get("_meta") is not None and not isinstance(params.get("_meta"), dict):
        return False
    update = params.get("update")
    if not isinstance(update, dict) or set(update) != {"sessionUpdate", "configOptions"}:
        return False
    if str(update.get("sessionUpdate") or "").strip().lower() != "config_option_update":
        return False
    options = update.get("configOptions")
    if not isinstance(options, list) or len(options) > 32 or not all(isinstance(item, dict) for item in options):
        return False
    try:
        return len(json.dumps(options, ensure_ascii=False).encode("utf-8")) <= _MAX_CONFIG_NOTIFICATION_BYTES
    except (TypeError, ValueError):
        return False


def _list_codebuddy_models_via_acp() -> list[ModelDescriptor]:
    """Perform the only permitted account-live ACP catalog exchange."""
    cli = resolve_codebuddy_cli()
    process = subprocess.Popen(
        [cli, "--acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    responses: queue.Queue[str] = queue.Queue()
    thread = threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True)
    thread.start()
    sequence = 0

    def exchange(method: str, params: dict[str, Any]) -> object:
        nonlocal sequence
        if method == "close/terminate":
            method = "session/close"
        sequence += 1
        request = {"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}
        assert process.stdin is not None
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        while True:
            try:
                raw_response = responses.get(timeout=8)
            except queue.Empty as exc:
                raise TimeoutError("ACP catalog response unavailable") from exc
            try:
                response = json.loads(raw_response)
            except ValueError as exc:
                raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID") from exc
            callback = str(response.get("method") or "").strip().lower()
            if callback:
                if method == "session/new" and _is_expected_catalog_notification(response):
                    continue
                raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
            if response.get("id") == sequence:
                return response

    try:
        # ACP requires these session/new fields but this remains a no-prompt,
        # no-tool, no-MCP-server catalog session.
        initial = exchange("initialize", {"protocolVersion": 1, "clientInfo": {"name": "tp-voyager", "version": "1.0.6"}})
        if not isinstance(initial, dict) or initial.get("error"):
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_UNAVAILABLE")
        session = exchange("session/new", {"cwd": os.getcwd(), "mcpServers": []})
        if not isinstance(session, dict):
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        result = session.get("result") if isinstance(session.get("result"), dict) else {}
        session_id = str(result.get("sessionId") or "")
        models = CodeBuddyAcpCatalogProbe._models(result, session.get("_meta"))
        if not models:
            raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_INVALID")
        if session_id:
            try:
                exchange("close/terminate", {"sessionId": session_id})
            except Exception:
                pass
        return models
    except KeyboardInterrupt as exc:
        raise CodeBuddyCatalogError("CODEBUDDY_MODEL_CATALOG_UNAVAILABLE") from exc
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


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
    """Prefer bounded account-live ACP catalog; retain declaration fallback."""
    try:
        return _list_codebuddy_models_via_acp()
    except (CodeBuddyCatalogError, TimeoutError, OSError, subprocess.SubprocessError):
        pass
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
