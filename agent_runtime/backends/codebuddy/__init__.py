"""Official CodeBuddy CLI integration boundary for TP-Voyager."""

from .backend import CodeBuddyBackend
from .capability import descriptor
from .process import probe_codebuddy_cli, resolve_codebuddy_cli
from .sdk_client import CodeBuddySdkClient

__all__ = [
    "CodeBuddyBackend",
    "CodeBuddySdkClient",
    "descriptor",
    "probe_codebuddy_cli",
    "resolve_codebuddy_cli",
]

from .model_catalog import list_codebuddy_models, parse_codebuddy_help_models
