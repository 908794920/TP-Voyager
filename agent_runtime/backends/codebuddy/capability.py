"""Officially documented CodeBuddy capability declaration.

T3b declaration: official SDK context-only read execution is implemented.
Native CodeBuddy tools remain disabled on the Captain route.
"""

from agent_runtime.domain.crew import CrewDescriptor


OFFICIAL_SOURCES = (
    "https://www.workbuddy.ai/docs/cli/",
    "https://www.workbuddy.ai/docs/cli/reference",
    "https://www.workbuddy.ai/docs/cli/iam",
    "https://www.workbuddy.ai/docs/cli/sdk-python",
)


def descriptor() -> CrewDescriptor:
    return CrewDescriptor(
        backend="codebuddy",
        display_name="CodeBuddy CLI",
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
        ),
        controlled_capabilities=("analyze_context", "edit_files", "run_commands"),
        documented_routes=("sdk", "headless", "public_acp_beta", "http_api_beta"),
        implemented_routes=("sdk_context_read_only", "sdk_patch"),
        dispatch_ready=True,
        model_discovery="cli_declared",
        notes=(
            "China accounts use CODEBUDDY_INTERNET_ENVIRONMENT=internal.",
            "Captain read-only dispatch satisfies analyze_context through the official SDK with all native tools denied and a Runtime-rendered Context Manifest snapshot.",
            "Captain patch dispatch uses an isolated Git worktree plus SDK can_use_tool path/command enforcement; bypassPermissions is not used.",
            "Headless permission bypass is not the baseline.",
        ),
    )
