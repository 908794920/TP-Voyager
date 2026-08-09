"""Qoder account model discovery using the official ``--list-models`` CLI.

The Windows CLI may render a complete catalog only on an interactive console;
PIPE capture has been observed to return a suspicious one-row subset.  The
adapter therefore marks such snapshots incomplete instead of presenting one
row as authoritative.  No model is auto-selected from this list.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

from agent_runtime.backends.errors import BackendUnavailableError
from agent_runtime.backends.qoder.process import resolve_qoder_cli
from agent_runtime.domain.crew import ModelDescriptor

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_QODER_MODEL_DOC = "https://docs.qoder.com/en/cli/model"
_QODER_TIER_BILLING: dict[str, dict[str, object]] = {
    # Official docs describe these as examples/rates, not deterministic billing
    # formulas.  TP-Voyager exposes them as reference metadata only and never
    # computes task cost from them.
    "auto": {"credit_rate": "~1.0x", "example_credits": 10},
    "ultimate": {"credit_rate": "~1.6x", "example_credits": 20},
    "performance": {"credit_rate": "~1.1x", "example_credits": 11},
    "efficient": {"credit_rate": "~0.3x", "example_credits": 3},
    "lite": {"credit_rate": "free", "example_credits": 0},
}
_QODER_DIRECT_BILLING: dict[str, dict[str, object]] = {
    # Exact-name matches from the official Qoder model page.  These are
    # reference multipliers only; server-side promotions may change actual
    # charging, so Runtime Usage Evidence remains the usage truth source.
    "qwen3.7-max": {"credit_rate": "0.5x"},
    "qwen3.7-plus": {"credit_rate": "0.1x"},
    "deepseek-v4-pro": {"credit_rate": "0.5x"},
    "deepseek-v4-flash": {"credit_rate": "0.1x"},
    "glm-5.2": {"credit_rate": "0.6x"},
    "kimi-k3": {"credit_rate": "0.8x"},
    "kimi-k2.7-code": {"credit_rate": "0.3x"},
    "minimax-m3": {"credit_rate": "0.2x"},
}


_QODER_DIRECT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    # Bounded official-description tags.  They are descriptive metadata, not
    # scores or a routing recommendation.
    "qwen3.7-max": ("agentic_coding", "long_horizon_reasoning"),
    "qwen3.7-plus": ("reasoning", "multimodal"),
    "deepseek-v4-pro": ("reasoning", "coding", "engineering"),
    "deepseek-v4-flash": ("fast_reasoning", "coding"),
    "glm-5.2": ("systems_engineering", "long_horizon_tasks"),
    "kimi-k3": ("software_engineering", "reasoning"),
    "kimi-k2.7-code": ("coding", "long_context"),
    "minimax-m3": ("coding", "multimodal"),
}

_QODER_TIER_INTENT = {
    "auto": "provider_routed_balance",
    "ultimate": "quality_priority",
    "performance": "performance_priority",
    "efficient": "credit_efficiency_priority",
    "lite": "free_basic_tier",
}


def _clean(value: str) -> str:
    return _ANSI.sub("", value).strip()


def _metadata(model_id: str, *, catalog_status: str) -> dict[str, object]:
    metadata: dict[str, object] = {
        "catalog_status": catalog_status,
        "billing": {"status": "unknown"},
        "capabilities": {"status": "unknown"},
    }
    tier_key = model_id.strip().lower()
    tier_intent = _QODER_TIER_INTENT.get(tier_key)
    if tier_intent is not None:
        metadata["capabilities"] = {
            "status": "official_tier_intent",
            "tier_intent": tier_intent,
            "source": _QODER_MODEL_DOC,
            "scored": False,
        }
    direct_tags = _QODER_DIRECT_CAPABILITIES.get(tier_key)
    if direct_tags is not None:
        metadata["capabilities"] = {
            "status": "official_descriptive_tags",
            "tags": list(direct_tags),
            "source": _QODER_MODEL_DOC,
            "scored": False,
        }
    billing = _QODER_TIER_BILLING.get(tier_key) or _QODER_DIRECT_BILLING.get(tier_key)
    if billing is not None:
        metadata["billing"] = {
            "status": "official_reference",
            "scheme": "credits",
            "source": _QODER_MODEL_DOC,
            "calculation_allowed": False,
            "actual_usage_source": "usage_evidence",
            **billing,
        }
    return metadata


def _with_catalog_status(
    models: list[ModelDescriptor], status: str,
) -> list[ModelDescriptor]:
    return [
        ModelDescriptor(
            backend=item.backend,
            model_id=item.model_id,
            display_name=item.display_name,
            available=item.available,
            disabled_reason=item.disabled_reason,
            source=item.source,
            observed_at=item.observed_at,
            metadata={**dict(item.metadata), "catalog_status": status},
        )
        for item in models
    ]


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
        for item in payload[:256]:
            if isinstance(item, str) and item.strip():
                model_id = item.strip()
                result.append(
                    ModelDescriptor(
                        "qoder", model_id,
                        source="official_dynamic", observed_at=stamp,
                        metadata=_metadata(model_id, catalog_status="complete"),
                    )
                )
            elif isinstance(item, dict):
                model_id = str(item.get("value") or item.get("id") or item.get("model") or "").strip()
                if not model_id:
                    continue
                enabled = bool(item.get("isEnabled", True))
                result.append(
                    ModelDescriptor(
                        backend="qoder",
                        model_id=model_id,
                        display_name=str(item.get("displayName") or item.get("name") or model_id),
                        available=enabled,
                        disabled_reason=None if enabled else "disabled_by_account",
                        source="official_dynamic",
                        observed_at=stamp,
                        metadata={
                            **_metadata(model_id, catalog_status="complete"),
                            "raw_entitlement": "enabled" if enabled else "disabled",
                        },
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
        if len(model_id) > 160:
            continue
        seen.add(model_id)
        metadata = _metadata(model_id, catalog_status="complete")
        if len(cols) > 2:
            # Preserve only bounded, non-authoritative CLI declaration columns.
            metadata["cli_columns"] = cols[2:6]
        result.append(
            ModelDescriptor(
                backend="qoder",
                model_id=model_id,
                display_name=cols[1] if len(cols) > 1 else model_id,
                source="official_dynamic",
                observed_at=stamp,
                metadata=metadata,
            )
        )
        if len(result) >= 256:
            break
    return result



def _sdk_metadata(item: dict[str, object], model_id: str) -> dict[str, object]:
    """Project bounded provider-live model facts from Qoder SDK ModelInfo."""
    metadata = _metadata(model_id, catalog_status="complete")
    metadata["catalog_transport"] = "qoder_agent_sdk"
    metadata["entitlement_status"] = "enabled" if bool(item.get("isEnabled", True)) else "disabled"
    for key in ("isNew", "isFree", "context_config", "thinking_config"):
        value = item.get(key)
        if isinstance(value, (bool, dict)):
            metadata[key] = value
    price_factor = item.get("priceFactor")
    if isinstance(price_factor, (int, float)) and not isinstance(price_factor, bool):
        metadata["billing"] = {
            "status": "provider_live_reference",
            "scheme": "credits",
            "price_factor": float(price_factor),
            "source": "qoder_sdk_model_list",
            "calculation_allowed": False,
            "actual_usage_source": "usage_evidence",
        }
    promotion = item.get("promotion")
    if isinstance(promotion, dict):
        safe_promotion: dict[str, object] = {}
        for key in (
            "active", "discount_factor", "before_promotion_price_factor",
            "timezone", "window_start", "window_end",
        ):
            value = promotion.get(key)
            if isinstance(value, (bool, int, float)):
                safe_promotion[key] = value
            elif isinstance(value, str) and len(value) <= 120:
                safe_promotion[key] = value
        if safe_promotion:
            metadata["promotion"] = safe_promotion
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        metadata["official_description"] = description.strip()[:1000]
    return metadata


def parse_sdk_models_output(text: str, *, observed_at: float | None = None) -> list[ModelDescriptor]:
    """Parse the bounded JSON payload emitted by the isolated Qoder SDK probe."""
    marker = "TP_VOYAGER_QODER_MODELS="
    payload_text = ""
    for line in text.splitlines():
        if line.startswith(marker):
            payload_text = line[len(marker):].strip()
    if not payload_text:
        return []
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    stamp = observed_at if observed_at is not None else time.time()
    result: list[ModelDescriptor] = []
    seen: set[str] = set()
    for item in payload[:256]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("value") or "").strip()
        if not model_id or len(model_id) > 160 or model_id in seen:
            continue
        seen.add(model_id)
        enabled = bool(item.get("isEnabled", True))
        display = str(item.get("displayName") or model_id).strip()[:240] or model_id
        result.append(
            ModelDescriptor(
                backend="qoder",
                model_id=model_id,
                display_name=display,
                available=enabled,
                disabled_reason=None if enabled else "disabled_by_account",
                source="official_dynamic_sdk",
                observed_at=stamp,
                metadata=_sdk_metadata(item, model_id),
            )
        )
    return result


def _list_qoder_models_via_sdk(cli: str) -> list[ModelDescriptor]:
    """Read Qoder's account model API via the official Python SDK, without a prompt."""
    script = "\n".join([
        "import asyncio, json, sys",
        "from qoder_agent_sdk import QoderAgentOptions, QoderSDKClient, qodercli_auth",
        "async def main():",
        "    options = QoderAgentOptions(auth=qodercli_auth(), cli_path=sys.argv[1])",
        "    async with QoderSDKClient(options=options) as client:",
        "        models = await client.get_available_models()",
        "    print('TP_VOYAGER_QODER_MODELS=' + json.dumps(models, ensure_ascii=False, separators=(',', ':')))",
        "asyncio.run(main())",
    ])
    completed = subprocess.run(
        [sys.executable, "-c", script, cli],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return []
    return parse_sdk_models_output(completed.stdout)

def list_qoder_models() -> list[ModelDescriptor]:
    cli = resolve_qoder_cli()

    # Preferred path: the official SDK exposes the current account model list
    # and provider-live model metadata. This opens only the CLI control plane;
    # no model prompt is submitted.
    try:
        sdk_models = _list_qoder_models_via_sdk(cli)
    except (OSError, subprocess.SubprocessError):
        sdk_models = []
    if sdk_models:
        return _with_catalog_status(sdk_models, "complete")

    # Compatibility fallback for hosts where the SDK probe cannot initialize.
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
    models = parse_list_models_output(completed.stdout)
    if not models:
        raise BackendUnavailableError("Qoder model discovery returned no models")

    # A plain-table ``MODEL\n<one-row>`` snapshot is a known suspicious capture
    # shape on Windows.  It may be a subset of the interactive catalog.  Keep
    # the observed row, but make incompleteness explicit to the Captain.
    plain = _clean(completed.stdout)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    if len(models) == 1 and lines and lines[0].lower() == "model":
        return _with_catalog_status(models, "incomplete_suspected")
    return _with_catalog_status(models, "complete")
