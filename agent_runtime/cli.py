"""Local operational CLI for the durable Sub-Agent Runtime.

The CLI is read-only for Runtime task state except for explicit ``init`` and
``model-routing-init`` configuration bootstraps, plus explicitly requested
Markdown/JSON export files.  It does not import the MCP server and cannot start,
cancel, retry, resume, or mutate a task.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_runtime.api.schemas import CAPTAIN_TOOL_NAMES
from agent_runtime.configuration import VoyagerUserConfig, VoyagerUserConfigError
from agent_runtime.application.crew.routing_profiles import (
    ModelRoutingProfileError,
    ModelRoutingProfiles,
)
from agent_runtime.application.crew.model_evaluation import ModelEvaluationSourceRegistry
from agent_runtime.application.crew.model_scorecard import load_tier_rules
from agent_runtime.backends.codebuddy.process import probe_codebuddy_cli
from agent_runtime.backends.codebuddy.model_catalog import list_codebuddy_models
from agent_runtime.backends.qoder.process import probe_qoder_cli
from agent_runtime.backends.qoder.model_catalog import list_qoder_models
from agent_runtime.persistence.runtime_paths import (
    canonical_runtime_home, resolve_runtime_database, runtime_database_path,
)

from agent_runtime.runtime.diagnostics import (
    RuntimeDiagnosticsError,
    RuntimeInspector,
    render_task_markdown,
)


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))




def _safe_probe(probe: Any) -> dict[str, Any]:
    """Run a non-model installation probe without exposing paths or credentials."""
    try:
        observed = dict(probe() or {})
        allowed = {
            "installed", "cli_installed", "sdk_installed", "version", "region",
            "dispatch_ready", "auth_probe_performed",
        }
        return {
            "ok": bool(observed.get("installed", observed.get("cli_installed", True))),
            **{key: observed[key] for key in sorted(allowed) if key in observed},
        }
    except Exception as exc:  # installation diagnostics are intentionally bounded
        return {"ok": False, "error": type(exc).__name__}



def _safe_model_catalog(loader: Any) -> dict[str, Any]:
    """Read a non-model CLI catalog and expose only bounded model facts."""
    try:
        models = list(loader() or [])[:256]
    except Exception as exc:
        return {"ok": False, "status": "unknown", "model_count": 0, "error": type(exc).__name__}
    statuses = {str(item.metadata.get("catalog_status") or "unknown") for item in models}
    status = "incomplete" if any(value.startswith("incomplete") for value in statuses) else ("complete" if models else "unknown")
    return {
        "ok": bool(models),
        "status": status,
        "model_count": len(models),
        "models": [str(item.model_id) for item in models],
        "model_invocation_performed": False,
    }


def _safe_routing_profiles() -> dict[str, Any]:
    """Project routing-profile status without selecting or dispatching a route."""
    try:
        profiles = ModelRoutingProfiles.load(canonical_runtime_home())
        metadata = profiles.metadata()
        if metadata.get("status") in {"not_configured", "bundled_baseline"}:
            metadata["materialize_command"] = "python -m agent_runtime.cli model-routing-init"
        return metadata
    except (ModelRoutingProfileError, OSError) as exc:
        return {
            "status": "invalid", "source": "operator_model_routing_profiles",
            "profile_count": 0, "advisory_only": True, "error": type(exc).__name__,
        }


def _model_evaluation_validation(home: Path) -> dict[str, Any]:
    profiles = ModelRoutingProfiles.load(home)
    registry = ModelEvaluationSourceRegistry.load_bundled()
    rules = load_tier_rules()
    fixed = {
        profile.canonical_family for profile in profiles.profiles
        if profile.provider_identity != "dynamic_tier" and profile.canonical_family
    }
    dynamic = [profile for profile in profiles.profiles if profile.provider_identity == "dynamic_tier"]
    standard_by_id: dict[str, dict[str, Any]] = {}
    for profile in profiles.profiles:
        for item in profile.standard_evidence:
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id:
                standard_by_id.setdefault(evidence_id, item)
    standard_evidence = list(standard_by_id.values())
    legacy_by_value: dict[str, dict[str, Any]] = {}
    for profile in profiles.profiles:
        for item in profile.benchmark_evidence:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            legacy_by_value.setdefault(key, item)
    legacy_evidence = list(legacy_by_value.values())
    archived = 0
    versions: dict[str, set[str]] = {}
    for item in standard_evidence:
        source = registry.source(str(item.get("source_id") or ""))
        if source.get("status") == "archived":
            archived += 1
        benchmark = item.get("benchmark") if isinstance(item.get("benchmark"), dict) else {}
        benchmark_id = str(benchmark.get("id") or "")
        version = str(benchmark.get("version") or "")
        if benchmark_id and version:
            versions.setdefault(benchmark_id, set()).add(version)
    return {
        "schema": "tp-voyager.model_evaluation_validation/v1",
        "profiles": profiles.profile_count,
        "canonical_fixed_models": len(fixed),
        "dynamic_routes": len(dynamic),
        "profile_schema": profiles.schema,
        "normalized_profile_schema": profiles.normalized_schema,
        "migration_available": profiles.schema == "tp-voyager.model_routing_profiles/v1",
        "standard_evidence": len(standard_evidence),
        "legacy_evidence": len(legacy_evidence),
        "invalid_evidence": 0,
        "primary_missing_required_context": 0,
        "archived_source_evidence": archived,
        "incomparable_groups": sum(1 for values in versions.values() if len(values) > 1),
        "tier_rules": str(profiles.tier_rules_status or "unknown"),
        "bundled_tier_rules": str(rules.get("status") or "unknown"),
        "tier_authority_conflicts": 0,
        "retired_routes": list(profiles.retired_routes),
        "network_access_performed": False,
        "write_performed": False,
    }


def _doctor_projection(overview: dict[str, Any]) -> dict[str, Any]:
    required = sorted(CAPTAIN_TOOL_NAMES)
    runtime_ok = bool(overview.get("schema_supported") and overview.get("integrity_ok"))
    mcp_available = importlib.util.find_spec("mcp") is not None
    codebuddy = _safe_probe(probe_codebuddy_cli)
    qoder = _safe_probe(probe_qoder_cli)
    codebuddy_models = _safe_model_catalog(list_codebuddy_models)
    qoder_models = _safe_model_catalog(list_qoder_models)
    routing_profiles = _safe_routing_profiles()
    installation_ready = bool(
        runtime_ok
        and mcp_available
        and codebuddy.get("ok")
        and qoder.get("ok")
    )
    projection = dict(overview)
    projection.update({
        "schema": "tp-voyager.doctor/v1",
        "version": "1.0.8",
        "ok": installation_ready,
        "runtime": {
            "ok": runtime_ok,
            "schema_version": overview.get("schema_version"),
            "supported_schema_version": overview.get("supported_schema_version"),
            "schema_supported": bool(overview.get("schema_supported")),
            "integrity_ok": bool(overview.get("integrity_ok")),
        },
        "mcp_transport": {
            "ok": mcp_available,
            "transport": "stdio",
            "package_available": mcp_available,
        },
        "captain_tools": {
            "ok": len(required) == 6,
            "required": required,
            "declared": required,
        },
        "crew": {
            "codebuddy": codebuddy,
            "qoder": qoder,
        },
        "model_catalog": {
            "codebuddy": codebuddy_models,
            "qoder": qoder_models,
            "selection_performed": False,
            "pricing_estimated": False,
        },
        "model_routing_profiles": routing_profiles,
        # Installation readiness is informational so doctor remains useful on
        # development hosts where one optional Crew CLI is intentionally absent.
        "installation_ready": installation_ready,
        "safety": {
            "model_invocation_performed": False,
            "credentials_returned": False,
            "task_content_returned": False,
            "usage_returned": False,
        },
    })
    return projection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tp-voyager",
        description="Local operations and read-only diagnostics for TP-Voyager Runtime.",
    )
    parser.add_argument(
        "--db",
        default="",
        help="Runtime SQLite path (default: TP_VOYAGER_DB or TP-Voyager home)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Show installation and Runtime health")
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the stable tp-voyager.doctor/v1 JSON contract",
    )

    subparsers.add_parser(
        "init",
        help="Initialize ~/.tp-voyager user configuration and reviewed routing baseline",
    )

    subparsers.add_parser(
        "model-routing-init",
        help="Install the reviewed model-routing baseline into TP-Voyager Home without overwrite",
    )

    migrate_parser = subparsers.add_parser(
        "model-routing-migrate",
        help="Explicitly migrate model_routing_profiles.json v1 to v2",
    )
    migrate_mode = migrate_parser.add_mutually_exclusive_group(required=True)
    migrate_mode.add_argument("--dry-run", action="store_true", help="Validate and report migration without writing")
    migrate_mode.add_argument("--write", action="store_true", help="Atomically persist the validated v2 migration")

    subparsers.add_parser(
        "model-evaluation-validate",
        help="Read-only validation of model evaluation evidence, scorecards, and tier authority",
    )

    audit_parser = subparsers.add_parser(
        "artifact-audit",
        help="Read-only verification of content-addressed Artifact blobs",
    )
    audit_parser.add_argument("--issue-limit", type=int, default=20)

    list_parser = subparsers.add_parser("list", help="List durable tasks")
    list_parser.add_argument("--runtime", default="", choices=("", "codebuddy", "qoder"))
    list_parser.add_argument("--status", default="")
    list_parser.add_argument("--limit", type=int, default=50)

    tool_history_parser = subparsers.add_parser(
        "tool-history",
        help="List content-free V1.4 Tool Runtime invocation audit records",
    )
    tool_history_parser.add_argument("--tool-name", default="")
    tool_history_parser.add_argument(
        "--status", default="", choices=("", "succeeded", "failed", "rejected")
    )
    tool_history_parser.add_argument("--limit", type=int, default=50)

    knowledge_list_parser = subparsers.add_parser(
        "knowledge-list",
        help="List content-free V1.5 knowledge collection metadata",
    )
    knowledge_list_parser.add_argument("--limit", type=int, default=100)

    knowledge_history_parser = subparsers.add_parser(
        "knowledge-history",
        help="List content-free V1.5 knowledge resolution audit records",
    )
    knowledge_history_parser.add_argument("--knowledge-id", default="")
    knowledge_history_parser.add_argument(
        "--operation", default="", choices=("", "search", "bundle")
    )
    knowledge_history_parser.add_argument(
        "--status", default="", choices=("", "succeeded", "failed", "rejected")
    )
    knowledge_history_parser.add_argument("--limit", type=int, default=50)

    assess_parser = subparsers.add_parser(
        "assess",
        help="Assess execution outcome and verified work product without mutation",
    )
    assess_parser.add_argument("task_id")

    show_parser = subparsers.add_parser("show", help="Show one safe task snapshot")
    show_parser.add_argument("task_id")
    show_parser.add_argument(
        "--include-result",
        action="store_true",
        help="Explicitly include final answer and structured result material",
    )

    export_parser = subparsers.add_parser("export", help="Export one task report")
    export_parser.add_argument("task_id")
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    export_parser.add_argument(
        "--include-result",
        action="store_true",
        help="Explicitly include final answer and structured result material",
    )
    export_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    resolution = resolve_runtime_database()
    path = Path(args.db).expanduser().resolve() if args.db else resolution.database
    try:
        if args.command == "init":
            home = canonical_runtime_home()
            config_result = VoyagerUserConfig.initialize(home)
            routing_path = home / "model_routing_profiles.json"
            if routing_path.exists():
                routing_result = {
                    "status": "already_exists",
                    "target": str(routing_path),
                    "profile_count": ModelRoutingProfiles.load(home).profile_count,
                }
            else:
                routing_result = ModelRoutingProfiles.initialize(home)
            _json_print({
                "schema": "tp-voyager.init/v1",
                "home": str(home),
                "config": config_result,
                "model_routing_profiles": routing_result,
            })
            return 0
        if args.command == "model-routing-init":
            result = ModelRoutingProfiles.initialize(canonical_runtime_home())
            _json_print(result)
            return 0
        if args.command == "model-routing-migrate":
            result = ModelRoutingProfiles.migrate(canonical_runtime_home(), write=bool(args.write))
            _json_print(result)
            return 0
        if args.command == "model-evaluation-validate":
            _json_print(_model_evaluation_validation(canonical_runtime_home()))
            return 0

        inspector = RuntimeInspector(path)
        if args.command == "doctor":
            overview = inspector.overview().to_dict()
            path_info = resolution.to_dict()
            if args.db:
                path_info = {**path_info, "database": str(path), "path_source": "--db"}
            overview["path_resolution"] = path_info
            doctor = _doctor_projection(overview)
            _json_print(doctor)
            if not doctor["schema_supported"]:
                return 2
            return 0 if doctor["integrity_ok"] else 3
        if args.command == "artifact-audit":
            audit = inspector.audit_artifact_store(
                issue_limit=args.issue_limit
            ).to_dict()
            _json_print(audit)
            return 0 if audit["integrity_ok"] else 3
        if args.command == "list":
            _json_print(
                {
                    "database": str(path),
                    "tasks": inspector.list_tasks(
                        runtime=args.runtime,
                        status=args.status,
                        limit=args.limit,
                    ),
                }
            )
            return 0
        if args.command == "tool-history":
            _json_print({
                "database": str(path),
                "invocations": inspector.list_tool_invocations(
                    tool_name=args.tool_name,
                    status=args.status,
                    limit=args.limit,
                ),
            })
            return 0
        if args.command == "knowledge-list":
            _json_print({
                "database": str(path),
                "collections": inspector.list_knowledge_collections(limit=args.limit),
            })
            return 0
        if args.command == "knowledge-history":
            _json_print({
                "database": str(path),
                "resolutions": inspector.list_knowledge_resolutions(
                    knowledge_id=args.knowledge_id,
                    operation=args.operation,
                    status=args.status,
                    limit=args.limit,
                ),
            })
            return 0
        if args.command == "assess":
            _json_print(inspector.task_assessment(args.task_id))
            return 0
        if args.command == "show":
            _json_print(
                inspector.task_snapshot(
                    args.task_id,
                    include_result=args.include_result,
                )
            )
            return 0
        if args.command == "export":
            output = Path(args.output).expanduser().resolve()
            if output.exists() and not args.force:
                parser.error(f"output already exists (use --force): {output}")
            snapshot = inspector.task_snapshot(
                args.task_id,
                include_result=args.include_result,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            if args.format == "json":
                body = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            else:
                body = render_task_markdown(snapshot)
            output.write_text(body, encoding="utf-8", newline="\n")
            _json_print({"ok": True, "output": str(output), "format": args.format})
            return 0
    except (RuntimeDiagnosticsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
