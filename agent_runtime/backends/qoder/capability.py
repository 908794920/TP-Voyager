"""Officially documented Qoder capability declaration."""

from agent_runtime.domain.crew import CrewDescriptor


OFFICIAL_SOURCES = (
    "https://docs.qoder.com/en/cli/model",
    "https://docs.qoder.com/en/cli/acp",
    "https://docs.qoder.com/en/cli/sdk/python/quick-start",
    "https://docs.qoder.com/en/cli/sdk/python/tools",
    "https://docs.qoder.com/en/cli/sdk/permissions",
)


def descriptor() -> CrewDescriptor:
    return CrewDescriptor(
        backend="qoder",
        display_name="Qoder CLI",
        maturity="official",
        official_sources=OFFICIAL_SOURCES,
        capabilities=(
            "read_files",
            "search_code",
            "edit_files",
            "run_commands",
            "mcp",
            "resume",
            "streaming",
            "host_permission_callback",
            "model_selection",
            "model_discovery",
        ),
        controlled_capabilities=("analyze_context", "read_files", "search_code", "edit_files", "run_commands", "verify_commands"),
        documented_routes=("acp", "sdk", "headless"),
        implemented_routes=("acp_read_only", "acp_patch", "acp_verify"),
        dispatch_ready=True,
        model_discovery="qodercli --list-models / SDK get_available_models",
        notes=(
            "TP-Voyager acp_read_only uses official ACP without --yolo; normal workspace mode runs from the real cwd with vendor-visible Read/Grep/Glob only, while explicit read_scope mode runs from the existing bounded snapshot.",
            "The ACP host layer still advertises no write/terminal client capability, rejects permission escalation, and enforces workspace/forbidden-path checks for host filesystem callbacks.",
            "TP-Voyager acp_patch also avoids --yolo; host callbacks enforce allowed paths and exact Captain command argv inside an isolated Git worktree.",
        ),
    )
