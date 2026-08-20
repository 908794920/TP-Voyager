#!/usr/bin/env python3
"""Install/update the TP-Voyager Codex host integration in one pass.

The existing ``tp_voyager`` MCP registration remains the only Runtime server
owner.  The same installer deploys the single skills-only ``tp-voyager`` plugin,
registers it in the user's personal marketplace, and merges a bounded routing
block into global Codex ``AGENTS.md``. Existing legacy standalone Skill/plugin
installations are detected and preserved until explicit post-validation cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

_SCHEMA = "tp-voyager.codex_install/v1"
_BINDINGS_SCHEMA = "tp-voyager.install_bindings/v1"
_BINDINGS_FILE = "tp-voyager.bindings.json"
_SKILL_NAME = "tp-voyager-captain"
_PLUGIN_NAME = "tp-voyager"
_LEGACY_PLUGIN_NAME = "tp-voyager-observability"
_PLUGIN_SOURCE_REL = Path("integrations/codex/local-marketplace/plugins") / _PLUGIN_NAME
_AGENTS_BEGIN = "<!-- >>> TP-Voyager managed guidance >>> -->"
_AGENTS_END = "<!-- <<< TP-Voyager managed guidance <<< -->"
_AGENTS_GUIDANCE = """## TP-Voyager Captain MCP routing

- For bounded repository research, code review, failure analysis, independent verification, or small patch work, proactively evaluate the mounted `tp_voyager` Captain MCP when delegation would add value.
- Simple tasks may be completed directly. Do not auto-dispatch merely because TP-Voyager is available.
- If `tp_voyager` is not mounted or unavailable, say so accurately and continue normally unless the user explicitly requires TP-Voyager.
- Do not auto-retry, silently switch Crew/model, widen task scope, expand permissions, or bypass approvals.
- After a TP-Voyager task is dispatched, use the existing read-only `render_voyager_panel` with the exact `task_id`, or an explicit `presentation_group_id` / exact `task_ids` for an intentional concurrent group; refresh must never re-dispatch, resume, cancel, or mutate.
""".strip()
_EXCLUDED_PARTS = frozenset({"__pycache__"})


class InstallError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_codex_home(
    *, environ: dict[str, str] | None = None, platform: str | None = None, home: str | None = None
) -> str:
    env = environ if environ is not None else dict(os.environ)
    configured = str(env.get("CODEX_HOME") or "").strip()
    if configured:
        return configured
    current_platform = platform or os.name
    if current_platform == "nt":
        profile = str(env.get("USERPROFILE") or home or "").strip()
        if not profile:
            raise InstallError("USERPROFILE is unavailable; set CODEX_HOME explicitly")
        return ntpath.join(profile, ".codex")
    base = str(home or Path.home())
    return str(Path(base).expanduser() / ".codex")


def installed_skill_path(codex_home: str | Path, *, platform: str | None = None) -> str:
    if (platform or os.name) == "nt":
        return ntpath.join(str(codex_home), "skills", _SKILL_NAME)
    return str(Path(codex_home).expanduser() / "skills" / _SKILL_NAME)


def _load_module(path: Path, name: str, *, write_bytecode: bool = True):
    previous = sys.dont_write_bytecode
    if not write_bytecode:
        sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise InstallError(f"unable to load installer dependency: {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


def _managed_files(source_skill: Path) -> list[Path]:
    output: list[Path] = []
    for path in source_skill.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source_skill)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"} or relative.as_posix() == _BINDINGS_FILE:
            continue
        output.append(relative)
    return sorted(output, key=lambda item: item.as_posix())


def _read_binding_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("installed TP-Voyager bindings are unreadable or invalid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != _BINDINGS_SCHEMA or not isinstance(raw.get("values"), dict):
        raise InstallError("installed TP-Voyager bindings schema is invalid")
    values: dict[str, str] = {}
    for key, value in raw["values"].items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise InstallError("installed TP-Voyager bindings contain an invalid value")
        values[key] = value
    return values


def _repository_root_from_source(source_skill: Path) -> Path | None:
    source = source_skill.expanduser().resolve()
    if source.name != _SKILL_NAME or source.parent.name != "skills":
        return None
    root = source.parent.parent.resolve()
    if source != (root / "skills" / _SKILL_NAME).resolve():
        return None
    if not (root / "pyproject.toml").is_file() or not (root / "agent_runtime").is_dir():
        return None
    return root


def _binding_values(
    source_skill: Path,
    sync_module: Any,
    *,
    provided: dict[str, str] | None,
    existing: dict[str, str] | None,
    check_only: bool,
) -> dict[str, str]:
    manifest = source_skill / "tp-voyager.manifest.json"
    names = sync_module.manifest_binding_names(manifest)
    values = dict(existing or {})
    repository_root = _repository_root_from_source(source_skill)
    if repository_root is not None:
        values["repository_root"] = str(repository_root)
    elif provided and provided.get("repository_root"):
        candidate_root = provided["repository_root"]
        if not isinstance(candidate_root, str) or not candidate_root.strip() or "\x00" in candidate_root:
            raise InstallError("install binding is invalid: repository_root")
        values["repository_root"] = candidate_root
    elif not values.get("repository_root"):
        raise InstallError(
            "repository_root binding is unavailable; run installation/update from the "
            "TP-Voyager repository or pass --binding repository_root=<path>"
        )
    for name in names:
        if name == "repository_root":
            continue
        candidate = None
        if provided and name in provided:
            candidate = provided[name]
        elif not check_only:
            candidate = os.environ.get(name)
        if candidate is not None:
            if not isinstance(candidate, str) or not candidate.strip() or "\x00" in candidate:
                raise InstallError(f"install binding is invalid: {name}")
            values[name] = candidate
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise InstallError("required install binding is missing: " + ", ".join(sorted(missing)))
    return values


def _bindings_bytes(values: dict[str, str]) -> bytes:
    payload = {"schema": _BINDINGS_SCHEMA, "values": {key: values[key] for key in sorted(values)}}
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tp-voyager-install-", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _skill_drift(source_skill: Path, target_skill: Path, managed: list[Path]) -> list[str]:
    drift: list[str] = []
    for relative in managed:
        source = source_skill / relative
        target = target_skill / relative
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            drift.append(relative.as_posix())
    return drift



def _resolved_user_home(codex_home: str | Path, user_home: str | Path | None) -> Path:
    if user_home is not None:
        return Path(user_home).expanduser().resolve()
    # Standard Codex home is <user>/.codex.  Using the parent also keeps custom
    # test/install roots self-contained; callers with a nonstandard layout may
    # pass user_home explicitly.
    return Path(codex_home).expanduser().resolve().parent


def _plugin_source(source_skill: Path) -> Path:
    path = source_skill / _PLUGIN_SOURCE_REL
    if not (path / ".codex-plugin" / "plugin.json").is_file():
        raise InstallError("source TP-Voyager plugin is incomplete")
    if (path / ".mcp.json").exists():
        raise InstallError("TP-Voyager plugin must not bundle a duplicate MCP server")
    return path


def _plugin_target(codex_home: str | Path) -> Path:
    return Path(codex_home).expanduser().resolve() / "plugins" / _PLUGIN_NAME


def _managed_block(existing: str) -> tuple[str, bool]:
    block = f"{_AGENTS_BEGIN}\n{_AGENTS_GUIDANCE}\n{_AGENTS_END}"
    starts = [index for index in range(len(existing)) if existing.startswith(_AGENTS_BEGIN, index)]
    ends = [index for index in range(len(existing)) if existing.startswith(_AGENTS_END, index)]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise InstallError("Codex AGENTS.md contains malformed TP-Voyager managed markers")
        end_at = ends[0] + len(_AGENTS_END)
        rendered = existing[: starts[0]] + block + existing[end_at:]
    else:
        if not existing:
            rendered = block + "\n"
        else:
            if existing.endswith("\n\n"):
                separator = ""
            elif existing.endswith("\n"):
                separator = "\n"
            else:
                separator = "\n\n"
            rendered = existing + separator + block + "\n"
    if rendered and not rendered.endswith("\n"):
        rendered += "\n"
    return rendered, rendered != existing


def _agents_status(codex_home: str | Path) -> tuple[bool, bool, bool, str, bytes]:
    home = Path(codex_home).expanduser().resolve()
    path = home / "AGENTS.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered, changed = _managed_block(existing)
    override = home / "AGENTS.override.md"
    override_present = override.is_file() and bool(override.read_text(encoding="utf-8", errors="ignore").strip())
    return (not changed, not override_present, override_present, str(path), rendered.encode("utf-8"))


def _marketplace_path(user_home: Path) -> Path:
    return user_home / ".agents" / "plugins" / "marketplace.json"


def _marketplace_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("personal Codex marketplace is unreadable or invalid JSON") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list):
        raise InstallError("personal Codex marketplace has an unsupported shape")
    if any(not isinstance(item, dict) for item in raw["plugins"]):
        raise InstallError("personal Codex marketplace contains an invalid plugin entry")
    return raw


def _desired_marketplace_entry(plugin_target: Path, user_home: Path) -> dict[str, Any]:
    try:
        relative = plugin_target.relative_to(user_home).as_posix()
    except ValueError as exc:
        raise InstallError("Codex plugin target must be inside the selected user home") from exc
    return {
        "name": _PLUGIN_NAME,
        "source": {"source": "local", "path": "./" + relative},
        "policy": {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
    }


def _render_marketplace(path: Path, plugin_target: Path, user_home: Path) -> tuple[bytes, bool]:
    payload = _marketplace_payload(path)
    desired = _desired_marketplace_entry(plugin_target, user_home)
    plugins = list(payload["plugins"])
    indexes = [i for i, item in enumerate(plugins) if item.get("name") == _PLUGIN_NAME]
    if len(indexes) > 1:
        raise InstallError("personal Codex marketplace contains duplicate TP-Voyager plugin entries")
    if indexes:
        plugins[indexes[0]] = desired
    else:
        plugins.append(desired)
    payload["plugins"] = plugins
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    current = path.read_bytes() if path.exists() else b""
    return data, data != current



def _resolve_codex_cli(codex_cli: str | Path | None) -> str | None:
    if codex_cli is not None:
        explicit = str(codex_cli).strip()
        return explicit or None
    configured = str(os.environ.get("CODEX_CLI") or "").strip()
    if configured:
        return configured
    return shutil.which("codex")


def _codex_cli_env(codex_home: str | Path, user_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
    # Personal marketplaces are rooted at the user home.  Set both conventions
    # for the child process only so Windows and POSIX Codex resolve the same
    # marketplace without modifying the caller's environment.
    env["HOME"] = str(user_home)
    env["USERPROFILE"] = str(user_home)
    return env


def _run_codex_json(
    executable: str,
    args: list[str],
    *,
    codex_home: str | Path,
    user_home: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=str(user_home),
            env=_codex_cli_env(codex_home, user_home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "codex_cli_unavailable"
    if completed.returncode != 0:
        return None, "codex_cli_command_failed"
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, "codex_cli_invalid_json"
    if not isinstance(payload, dict):
        return None, "codex_cli_invalid_json"
    return payload, None


def _plugin_state_from_list(payload: dict[str, Any]) -> tuple[bool, bool]:
    installed = payload.get("installed")
    if not isinstance(installed, list):
        return False, False
    for item in installed:
        if not isinstance(item, dict) or item.get("name") != _PLUGIN_NAME:
            continue
        is_installed = item.get("installed", True) is not False
        is_enabled = item.get("enabled", True) is not False
        return bool(is_installed), bool(is_enabled)
    return False, False


def _codex_plugin_status(
    executable: str,
    *,
    codex_home: str | Path,
    user_home: Path,
) -> tuple[bool, bool, str | None]:
    payload, error = _run_codex_json(
        executable,
        ["plugin", "list", "--json"],
        codex_home=codex_home,
        user_home=user_home,
    )
    if payload is None:
        return False, False, error
    installed, enabled = _plugin_state_from_list(payload)
    return installed, enabled, None


def _ensure_codex_plugin(
    executable: str | None,
    *,
    marketplace_name: str,
    codex_home: str | Path,
    user_home: Path,
    check_only: bool,
    refresh_required: bool = False,
) -> dict[str, Any]:
    base = {
        "plugin_cli_available": executable is not None,
        "plugin_cache_refresh_required": bool(refresh_required),
        "plugin_cache_refreshed": False,
    }
    if executable is None:
        return {
            **base,
            "plugin_installed": False,
            "plugin_enabled": False,
            "plugin_installation_pending": True,
            "plugin_install_method": "marketplace-default",
            "plugin_install_error": None,
            "plugin_cli_changed": False,
        }

    installed, enabled, status_error = _codex_plugin_status(
        executable, codex_home=codex_home, user_home=user_home
    )
    if check_only:
        return {
            **base,
            "plugin_installed": bool(status_error is None and installed),
            "plugin_enabled": bool(status_error is None and installed and enabled),
            "plugin_installation_pending": not bool(status_error is None and installed),
            "plugin_install_method": "codex-cli-check",
            "plugin_install_error": status_error,
            "plugin_cli_changed": False,
        }

    refreshed = False
    if status_error is None and installed and refresh_required:
        _, remove_error = _run_codex_json(
            executable,
            ["plugin", "remove", f"{_PLUGIN_NAME}@{marketplace_name}", "--json"],
            codex_home=codex_home,
            user_home=user_home,
        )
        if remove_error is not None:
            return {
                **base,
                "plugin_installed": True,
                "plugin_enabled": enabled,
                "plugin_installation_pending": True,
                "plugin_install_method": "codex-cli-refresh",
                "plugin_install_error": remove_error,
                "plugin_cli_changed": False,
            }
        installed = False
        refreshed = True

    if status_error is None and installed:
        return {
            **base,
            "plugin_installed": True,
            "plugin_enabled": enabled,
            "plugin_installation_pending": False,
            "plugin_install_method": "codex-cli",
            "plugin_install_error": None,
            "plugin_cli_changed": False,
        }

    _, add_error = _run_codex_json(
        executable,
        ["plugin", "add", f"{_PLUGIN_NAME}@{marketplace_name}", "--json"],
        codex_home=codex_home,
        user_home=user_home,
    )
    if add_error is not None:
        return {
            **base,
            "plugin_installed": False,
            "plugin_enabled": False,
            "plugin_installation_pending": True,
            "plugin_install_method": "marketplace-default",
            "plugin_install_error": add_error,
            "plugin_cli_changed": refreshed,
            "plugin_cache_refreshed": False,
        }
    installed, enabled, verify_error = _codex_plugin_status(
        executable, codex_home=codex_home, user_home=user_home
    )
    if verify_error is not None or not installed:
        return {
            **base,
            "plugin_installed": False,
            "plugin_enabled": False,
            "plugin_installation_pending": True,
            "plugin_install_method": "marketplace-default",
            "plugin_install_error": verify_error or "codex_cli_install_not_visible",
            "plugin_cli_changed": True,
            "plugin_cache_refreshed": False,
        }
    return {
        **base,
        "plugin_installed": True,
        "plugin_enabled": enabled,
        "plugin_installation_pending": False,
        "plugin_install_method": "codex-cli",
        "plugin_install_error": None,
        "plugin_cli_changed": True,
        "plugin_cache_refreshed": refreshed,
    }

def _tree_drift(source_root: Path, target_root: Path) -> tuple[list[Path], list[str]]:
    managed = _managed_files(source_root)
    return managed, _skill_drift(source_root, target_root, managed)


def _deploy_managed_tree(source_root: Path, target_root: Path, managed: list[Path]) -> list[str]:
    changed: list[str] = []
    for relative in managed:
        source_file = source_root / relative
        target_file = target_root / relative
        data = source_file.read_bytes()
        if target_file.is_file() and target_file.read_bytes() == data:
            continue
        _atomic_write(target_file, data)
        changed.append(relative.as_posix())
    return changed


def _legacy_install_state(codex_home: str | Path) -> tuple[Path, Path, bool, bool]:
    home = Path(codex_home).expanduser().resolve()
    legacy_skill = Path(installed_skill_path(home)).expanduser().resolve()
    legacy_plugin = home / "plugins" / _LEGACY_PLUGIN_NAME
    return legacy_skill, legacy_plugin, legacy_skill.exists(), legacy_plugin.exists()


def _legacy_cleanup_steps(legacy_skill: Path, legacy_plugin: Path, marketplace_name: str) -> list[str]:
    return [
        f"After new-plugin validation: codex plugin remove {_LEGACY_PLUGIN_NAME}@{marketplace_name}",
        f"Then manually remove the legacy standalone Skill directory: {legacy_skill}",
        f"Then manually remove the legacy plugin source/cache directory if it still exists: {legacy_plugin}",
        "Remove only the tp-voyager-observability entry from the personal marketplace if it remains; preserve unrelated entries.",
        "Start a new Codex conversation/session and re-verify TP-Voyager before deleting any additional legacy material.",
    ]


def install(
    source_skill: str | Path,
    codex_home: str | Path,
    *,
    bindings: dict[str, str] | None = None,
    check_only: bool = False,
    user_home: str | Path | None = None,
    codex_cli: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(source_skill).expanduser().resolve()
    if not source.is_dir():
        raise InstallError("source Captain bootstrap directory does not exist")
    sync_script = source / "sync_codex_desktop.py"
    manifest = source / "tp-voyager.manifest.json"
    if not sync_script.is_file() or not manifest.is_file():
        raise InstallError("source Captain bootstrap is incomplete")
    source_sync = _load_module(
        sync_script, "tp_voyager_source_sync", write_bytecode=not check_only
    )

    host_user_home = _resolved_user_home(codex_home, user_home)
    source_plugin = _plugin_source(source)
    target_plugin = _plugin_target(codex_home)
    plugin_managed, plugin_drift = _tree_drift(source_plugin, target_plugin)
    agents_current, agents_effective, agents_override_present, agents_path_text, agents_bytes = _agents_status(codex_home)
    marketplace_path = _marketplace_path(host_user_home)
    marketplace_bytes, marketplace_changed = _render_marketplace(
        marketplace_path, target_plugin, host_user_home
    )
    marketplace_payload = json.loads(marketplace_bytes.decode("utf-8"))
    marketplace_name = marketplace_payload.get("name")
    if not isinstance(marketplace_name, str) or not marketplace_name.strip():
        raise InstallError("personal Codex marketplace name is missing or invalid")
    codex_executable = _resolve_codex_cli(codex_cli)

    values = _binding_values(
        source, source_sync, provided=bindings, existing=None, check_only=check_only
    )
    # Validate the portable manifest before any host write.
    source_sync.load_manifest(manifest, bindings=values)
    manifest_bytes = manifest.read_bytes()
    config_path = Path(codex_home).expanduser().resolve() / "config.toml"
    legacy_skill, legacy_plugin, legacy_skill_present, legacy_plugin_present = _legacy_install_state(codex_home)
    cleanup_steps = _legacy_cleanup_steps(legacy_skill, legacy_plugin, marketplace_name)

    if check_only:
        mcp_result = source_sync.sync(
            manifest, config_path, check_only=True, bindings=values
        )
        plugin_files_installed = not plugin_drift
        marketplace_registered = not marketplace_changed
        agents_guidance_installed = agents_current
        plugin_state = _ensure_codex_plugin(
            codex_executable,
            marketplace_name=marketplace_name,
            codex_home=codex_home,
            user_home=host_user_home,
            check_only=True,
            refresh_required=bool(plugin_drift),
        )
        ok = (
            bool(mcp_result.get("ok"))
            and plugin_files_installed
            and marketplace_registered
            and agents_guidance_installed
        )
        return {
            "schema": _SCHEMA,
            "ok": ok,
            "action": "check-ok" if ok else "check-failed",
            "source_bootstrap_path": str(source),
            "config_path": str(config_path),
            "manifest_sha256": _sha256(manifest_bytes),
            "config_sha256_before": mcp_result.get("config_sha256_before"),
            "config_sha256_after": mcp_result.get("config_sha256_after"),
            "mcp_drift": list(mcp_result.get("drift") or []),
            "mcp_action": mcp_result.get("action"),
            "binding_keys": sorted(values),
            "mcp_registered": bool(mcp_result.get("ok")),
            "plugin_name": _PLUGIN_NAME,
            "plugin_files_installed": plugin_files_installed,
            "plugin_drift": list(plugin_drift),
            **plugin_state,
            "marketplace_registered": marketplace_registered,
            "marketplace_path": str(marketplace_path),
            "agents_guidance_installed": agents_guidance_installed,
            "agents_guidance_effective": agents_effective,
            "agents_override_present": agents_override_present,
            "agents_path": agents_path_text,
            "legacy_skill_present": legacy_skill_present,
            "legacy_observability_plugin_present": legacy_plugin_present,
            "legacy_cleanup_steps": cleanup_steps,
            "restart_required": False,
            "new_conversation_required": False,
            "provider_invocation_performed": False,
            "task_dispatch_performed": False,
        }

    plugin_changed_files = _deploy_managed_tree(source_plugin, target_plugin, plugin_managed)
    agents_changed = not agents_current
    if agents_changed:
        _atomic_write(Path(agents_path_text), agents_bytes)
    if marketplace_changed:
        _atomic_write(marketplace_path, marketplace_bytes)

    # MCP configuration is synchronized directly from the repository bootstrap.
    # v1.0.9.2 intentionally does not deploy/update the legacy standalone Skill.
    mcp_result = source_sync.sync(manifest, config_path, bindings=values)
    plugin_state = _ensure_codex_plugin(
        codex_executable,
        marketplace_name=marketplace_name,
        codex_home=codex_home,
        user_home=host_user_home,
        check_only=False,
        refresh_required=bool(plugin_changed_files),
    )
    host_changed = bool(
        plugin_changed_files
        or agents_changed
        or marketplace_changed
        or plugin_state.get("plugin_cli_changed")
        or mcp_result.get("action") in {"added", "updated"}
    )
    action = "changed" if host_changed else "no-op"
    return {
        "schema": _SCHEMA,
        "ok": bool(mcp_result.get("ok")),
        "action": action,
        "source_bootstrap_path": str(source),
        "config_path": str(config_path),
        "manifest_sha256": _sha256(manifest_bytes),
        "config_sha256_before": mcp_result.get("config_sha256_before"),
        "config_sha256_after": mcp_result.get("config_sha256_after"),
        "mcp_action": mcp_result.get("action"),
        "binding_keys": sorted(values),
        "env_keys": list((mcp_result.get("entry") or {}).get("env_keys") or []),
        "mcp_registered": bool(mcp_result.get("ok")),
        "plugin_name": _PLUGIN_NAME,
        "plugin_files_installed": True,
        "plugin_changed_file_count": len(plugin_changed_files),
        **plugin_state,
        "marketplace_registered": True,
        "marketplace_path": str(marketplace_path),
        "agents_guidance_installed": True,
        "agents_guidance_effective": agents_effective,
        "agents_override_present": agents_override_present,
        "agents_path": agents_path_text,
        "legacy_skill_present": legacy_skill_present,
        "legacy_observability_plugin_present": legacy_plugin_present,
        "legacy_cleanup_steps": cleanup_steps,
        "restart_required": bool(action != "no-op" or plugin_state.get("plugin_installation_pending")),
        "new_conversation_required": bool(action != "no-op" or plugin_state.get("plugin_installation_pending")),
        "provider_invocation_performed": False,
        "task_dispatch_performed": False,
    }

def _parse_bindings(values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key.strip() or not value:
            raise InstallError("--binding must use NAME=VALUE")
        key = key.strip()
        if key in output:
            raise InstallError(f"duplicate --binding: {key}")
        output[key] = value
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install/update TP-Voyager plugin, existing MCP registration, "
            "and managed Codex guidance"
        )
    )
    parser.add_argument("--source-skill", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--codex-home", default=resolve_codex_home())
    parser.add_argument("--binding", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument(
        "--codex-cli",
        default=None,
        help="Optional Codex CLI executable used to install/verify the local plugin; auto-detected when omitted",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only validation; do not deploy plugin/guidance or edit Codex config",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = install(
            args.source_skill,
            args.codex_home,
            bindings=_parse_bindings(args.binding),
            check_only=bool(args.check),
            codex_cli=args.codex_cli,
        )
    except (InstallError, OSError, ValueError) as exc:
        print(json.dumps({"schema": _SCHEMA, "ok": False, "error": str(exc), "provider_invocation_performed": False, "task_dispatch_performed": False}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
