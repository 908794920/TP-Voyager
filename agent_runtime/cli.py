"""Local operational CLI for the durable Sub-Agent Runtime.

The CLI is intentionally read-only except for writing an explicitly requested
Markdown/JSON export file.  It does not import the MCP server and cannot start,
cancel, retry, resume, or mutate a task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_runtime.persistence.runtime_paths import resolve_runtime_database, runtime_database_path

from agent_runtime.runtime.diagnostics import (
    RuntimeDiagnosticsError,
    RuntimeInspector,
    render_task_markdown,
)


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runtime",
        description="Read-only diagnostics for Agent Runtime.",
    )
    parser.add_argument(
        "--db",
        default="",
        help="Runtime SQLite path (default: AGENT_RUNTIME_DB or runtime home)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Show schema and aggregate Runtime health")

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
    inspector = RuntimeInspector(path)
    try:
        if args.command == "doctor":
            overview = inspector.overview().to_dict()
            path_info = resolution.to_dict()
            if args.db:
                path_info = {**path_info, "database": str(path), "path_source": "--db", "legacy_compat_active": False}
            marker = path.parent / "migration-v2.json"
            overview["path_resolution"] = path_info
            overview["home_migration"] = {
                "marker_file": str(marker),
                "marker_exists": marker.is_file(),
            }
            _json_print(overview)
            if not overview["schema_supported"]:
                return 2
            return 0 if overview["integrity_ok"] else 3
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
