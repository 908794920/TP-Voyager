"""Best-effort Qoder SDK usage adapter for an already executed ACP session.

This module deliberately does not import ``qoder_agent_sdk`` at module-load
time.  The optional SDK is loaded only when usage collection is requested.
Collection copies an existing local Qoder session into an in-memory
``SessionStore``; it never starts, resumes, or prompts an Agent task.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_runtime.backends.base import BackendUsage


@dataclass(frozen=True)
class QoderSdkUsageCollection:
    """Bounded outcome of reading usage facts from an existing Qoder session."""

    status: str
    facts: tuple[BackendUsage, ...] = ()


class _CaptureSessionStore:
    """Minimal external SessionStore used only to capture imported entries."""

    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def append(self, _key: Any, entries: list[Any]) -> None:
        if isinstance(entries, list):
            self.entries.extend(entries)

    async def load(self, _key: Any) -> list[Any] | None:
        return None


class QoderSdkUsageAdapter:
    """Read provider-reported Qoder usage without dispatching a second task.

    ``cli_path`` is supplied by the live ACP client so the adapter follows the
    same TP-Voyager configuration resolution as the task that already ran.  It
    is retained as runtime provenance; Qoder's public session-import helper
    identifies the existing session by ``session_id`` + ``directory`` and does
    not accept a CLI path parameter.
    """

    def __init__(
        self,
        *,
        cli_path: str,
        sdk_module: Any | None = None,
        sdk_loader: Callable[[], Any | None] | None = None,
    ) -> None:
        self.cli_path = str(cli_path or "")
        self._sdk_module = sdk_module
        self._sdk_loader = sdk_loader or self._load_optional_sdk

    @staticmethod
    def _load_optional_sdk() -> Any | None:
        # ``find_spec`` avoids importing the optional dependency in environments
        # where the Qoder SDK is not installed.
        try:
            if importlib.util.find_spec("qoder_agent_sdk") is None:
                return None
            return importlib.import_module("qoder_agent_sdk")
        except (ImportError, ModuleNotFoundError):
            return None

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        try:
            raw = vars(value)
        except TypeError:
            return {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def _message_payload(cls, entry: Any) -> dict[str, Any]:
        raw = cls._mapping(entry)
        # Imported session entries are opaque SDK data.  Accept the direct
        # message shape used by Qoder as well as common serialized wrappers,
        # without interpreting arbitrary content fields.
        for key in ("message", "data", "payload"):
            nested = cls._mapping(raw.get(key))
            if nested and (
                str(nested.get("type") or "").lower() in {"assistant", "result"}
                or "usage" in nested
                or "total_credits" in nested
                or "model_usage" in nested
            ):
                return nested
        return raw

    @staticmethod
    def _number(mapping: dict[str, Any], *names: str) -> int | float | None:
        for name in names:
            if name not in mapping:
                continue
            value = mapping.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value >= 0:
                return value
        return None

    @staticmethod
    def _optional_bool(mapping: dict[str, Any], name: str) -> bool | None:
        value = mapping.get(name)
        return value if isinstance(value, bool) else None

    @staticmethod
    def _first_text(mapping: dict[str, Any], *names: str) -> str:
        for name in names:
            value = mapping.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _run_async(awaitable: Any) -> Any:
        """Run one SDK coroutine from the synchronous backend boundary.

        TP-Voyager's backend path is synchronous.  A small thread fallback
        keeps this helper safe if a host invokes it while an event loop is
        already running on the current thread.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result: dict[str, Any] = {}
        failure: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(awaitable)
            except BaseException as exc:  # pragma: no cover - host-loop guard
                failure["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in failure:
            raise failure["error"]
        return result.get("value")

    @classmethod
    def _assistant_values(cls, message: dict[str, Any]) -> dict[str, Any] | None:
        usage = cls._mapping(message.get("usage"))
        if not usage:
            # TypeScript-shaped serialized Assistant messages may nest usage
            # under ``message`` even when the outer entry is already tagged.
            nested_message = cls._mapping(message.get("message"))
            usage = cls._mapping(nested_message.get("usage"))
        if not usage:
            return None

        request_id = cls._first_text(usage, "request_id", "requestId")
        input_tokens = cls._number(usage, "input_tokens", "inputTokens", "prompt_tokens")
        output_tokens = cls._number(usage, "output_tokens", "outputTokens", "completion_tokens")
        total_tokens = cls._number(usage, "total_tokens", "totalTokens")
        cache_read = cls._number(
            usage,
            "cache_read_tokens", "cacheReadTokens", "cache_read_input_tokens",
            "cached_input_tokens", "cachedInputTokens", "cached_tokens", "cachedTokens",
        )
        cache_write = cls._number(
            usage,
            "cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens",
            "cache_creation_input_tokens", "cacheCreationInputTokens",
        )
        cache_miss = cls._number(usage, "cache_miss_tokens", "cacheMissTokens")
        credits = cls._number(usage, "credits")
        original_credits = cls._number(usage, "original_credits", "originalCredits")
        billable = cls._optional_bool(usage, "billable")

        # Missing provider fields stay missing.  Even when input/output are
        # explicit zeroes, do not synthesize a missing total as zero.
        derived: list[str] = []

        return {
            "request_id": request_id,
            "model": cls._first_text(message, "model"),
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "cache_read_tokens": cache_read,
            "cache_miss_tokens": cache_miss,
            "cache_write_tokens": cache_write,
            "output_tokens": output_tokens,
            "credits": credits,
            "original_credits": original_credits,
            "billable": billable,
            "derived_fields": tuple(derived),
            "provider_usage": usage,
        }

    @staticmethod
    def _fill_missing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            if key == "derived_fields":
                previous = merged.get(key) if isinstance(merged.get(key), tuple) else ()
                current = value if isinstance(value, tuple) else ()
                merged[key] = tuple(sorted({*previous, *current}))
                continue
            if key == "provider_usage":
                previous = merged.get(key) if isinstance(merged.get(key), dict) else {}
                current = value if isinstance(value, dict) else {}
                raw = dict(previous)
                for raw_key, raw_value in current.items():
                    if raw_key not in raw or raw.get(raw_key) is None:
                        raw[raw_key] = raw_value
                merged[key] = raw
                continue
            if (key not in merged or merged.get(key) is None or merged.get(key) == "") and value is not None:
                merged[key] = value
        return merged

    @classmethod
    def _assistant_fact(cls, values: dict[str, Any], *, default_model: str) -> BackendUsage:
        request_id = str(values.get("request_id") or "")
        return BackendUsage(
            provider="qoder",
            scope="turn",
            model=str(values.get("model") or default_model or ""),
            source="qoder_sdk_assistant_usage",
            accounting="delta" if request_id else "snapshot",
            sample_id=request_id,
            request_id=request_id,
            total_tokens=(int(values["total_tokens"]) if values.get("total_tokens") is not None else None),
            input_tokens=(int(values["input_tokens"]) if values.get("input_tokens") is not None else None),
            cache_read_tokens=(int(values["cache_read_tokens"]) if values.get("cache_read_tokens") is not None else None),
            cache_miss_tokens=(int(values["cache_miss_tokens"]) if values.get("cache_miss_tokens") is not None else None),
            cache_write_tokens=(int(values["cache_write_tokens"]) if values.get("cache_write_tokens") is not None else None),
            output_tokens=(int(values["output_tokens"]) if values.get("output_tokens") is not None else None),
            credits=(float(values["credits"]) if values.get("credits") is not None else None),
            original_credits=(float(values["original_credits"]) if values.get("original_credits") is not None else None),
            billable=values.get("billable") if isinstance(values.get("billable"), bool) else None,
            derived_fields=tuple(values.get("derived_fields") or ()),
            provider_usage=(values.get("provider_usage") if isinstance(values.get("provider_usage"), dict) else {}),
        )

    @classmethod
    def _result_fact(
        cls,
        message: dict[str, Any],
        *,
        default_model: str,
    ) -> BackendUsage | None:
        total_credits = cls._number(message, "total_credits", "totalCredits")
        model_usage = message.get("model_usage") if isinstance(message.get("model_usage"), dict) else message.get("modelUsage")
        model_usage = model_usage if isinstance(model_usage, dict) else {}
        if total_credits is None and default_model:
            selected = cls._mapping(model_usage.get(default_model))
            total_credits = cls._number(selected, "credits")
        if total_credits is None:
            return None
        return BackendUsage(
            provider="qoder",
            scope="session",
            model=default_model,
            source="qoder_sdk_result",
            accounting="snapshot",
            session_credits=float(total_credits),
            model_usage=model_usage,
            provider_usage={"total_credits": total_credits},
        )

    def collect_session_usage(
        self,
        *,
        session_id: str,
        cwd: str | Path,
        model: str = "",
    ) -> QoderSdkUsageCollection:
        if not str(session_id or "").strip():
            return QoderSdkUsageCollection(status="provider_omitted")

        sdk = self._sdk_module if self._sdk_module is not None else self._sdk_loader()
        if sdk is None:
            return QoderSdkUsageCollection(status="provider_omitted")
        importer = getattr(sdk, "import_session_to_store", None)
        if not callable(importer):
            return QoderSdkUsageCollection(status="provider_omitted")

        store = _CaptureSessionStore()
        try:
            self._run_async(
                importer(
                    str(session_id),
                    store,
                    directory=str(cwd),
                    include_subagents=False,
                )
            )
        except Exception:
            # Usage is auxiliary.  A missing/incompatible local session or SDK
            # must not convert an already completed ACP task to failed or invent
            # a numeric usage fact.
            return QoderSdkUsageCollection(status="provider_omitted")

        identified: dict[str, dict[str, Any]] = {}
        anonymous: list[dict[str, Any]] = []
        result_fact: BackendUsage | None = None
        recognized_message = False

        for entry in store.entries:
            message = self._message_payload(entry)
            kind = str(message.get("type") or "").strip().lower()
            role = str(message.get("role") or "").strip().lower()
            is_assistant = (
                kind == "assistant"
                or (kind == "message" and role == "assistant")
                or (kind == "" and isinstance(message.get("usage"), dict))
            )
            if is_assistant:
                values = self._assistant_values(message)
                if values is None:
                    continue
                recognized_message = True
                request_id = str(values.get("request_id") or "")
                if request_id:
                    prior = identified.get(request_id)
                    identified[request_id] = values if prior is None else self._fill_missing(prior, values)
                else:
                    anonymous.append(values)
                continue
            if kind == "result" or "total_credits" in message or "model_usage" in message:
                recognized_message = True
                candidate = self._result_fact(message, default_model=model)
                if candidate is not None:
                    # Result credits are cumulative session snapshots.  Keep the
                    # latest provider-reported snapshot; never sum them.
                    result_fact = candidate

        facts: list[BackendUsage] = [
            self._assistant_fact(values, default_model=model)
            for values in identified.values()
        ]
        facts.extend(self._assistant_fact(values, default_model=model) for values in anonymous)
        if result_fact is not None:
            facts.append(result_fact)

        if facts:
            return QoderSdkUsageCollection(status="observed", facts=tuple(facts))
        return QoderSdkUsageCollection(
            status="protocol_unrecognized" if recognized_message or store.entries else "provider_omitted"
        )

    @classmethod
    def normalize_account_usage(cls, raw: Any) -> dict[str, Any]:
        """Normalize account quota separately from per-task/session usage."""
        payload = cls._mapping(raw)
        quota = cls._mapping(payload.get("userQuota"))
        normalized_quota: dict[str, Any] = {}
        for source_key, target_key in (
            ("total", "total"),
            ("used", "used"),
            ("remaining", "remaining"),
            ("percentage", "percentage"),
        ):
            value = cls._number(quota, source_key)
            if value is not None:
                normalized_quota[target_key] = value
        unit = quota.get("unit")
        if isinstance(unit, str) and unit.strip():
            normalized_quota["unit"] = unit.strip()
        exceeded = payload.get("isQuotaExceeded")
        return {
            "user_quota": normalized_quota,
            "is_quota_exceeded": exceeded if isinstance(exceeded, bool) else None,
        }
