"""Officially documented CodeBuddy capability declaration.

v1.0.8 implementation supports dual read-only delivery: vendor workspace
exploration and explicit frozen Runtime context. Controlled read/search
capability truth remains gated on real account-live acceptance.
"""

from agent_runtime.domain.crew import CrewDescriptor


OFFICIAL_SOURCES = (
    "https://www.codebuddy.ai/docs/cli/sdk",
    "https://www.codebuddy.ai/docs/cli/sdk-python",
    "https://www.codebuddy.ai/docs/cli/sdk-permissions",
    "https://www.codebuddy.ai/docs/cli/tools-reference",
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
        controlled_capabilities=("analyze_context", "edit_files", "run_commands", "verify_commands"),
        documented_routes=("sdk", "headless", "public_acp_beta", "http_api_beta"),
        implemented_routes=("sdk_context_read_only", "sdk_patch", "sdk_verify"),
        dispatch_ready=True,
        model_discovery="codebuddy_acp_account_live",
        notes=(
            "China accounts use CODEBUDDY_INTERNET_ENVIRONMENT=internal.",
            "Captain read-only dispatch has two modes: normal workspace delivery is configured for plan-mode Read/Glob/Grep behind can_use_tool; explicit context_id keeps the existing Runtime-rendered frozen snapshot with native tools denied.",
            "Workspace read/search remains pending Windows account-live acceptance before controlled_capabilities advertises read_files/search_code.",
            "Captain patch dispatch uses an isolated Git worktree plus SDK can_use_tool path/command enforcement; bypassPermissions is not used.",
            "Headless permission bypass is not the baseline.",
            "Account-live model discovery uses catalog-only ACP initialize/session-new/close without a prompt; CLI declaration is fallback-only.",
        ),
    )
