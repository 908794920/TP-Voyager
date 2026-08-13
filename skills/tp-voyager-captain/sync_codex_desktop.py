#!/usr/bin/env python3
"""Synchronize TP-Voyager's manifest-owned MCP launch facts into Codex Desktop.

Only ``mcp_servers.tp_voyager`` managed fields are touched.  The surrounding
Codex config is edited as text so unrelated settings and comments survive.
This tool never starts TP-Voyager, CodeBuddy, Qoder, or any model task.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable

_SCHEMA = "tp-voyager.codex_mcp_sync/v1"
_MANIFEST_SCHEMA = "tp-voyager.manifest/v1"
_ALLOWED_SERVER = "tp_voyager"
_ROOT_MANAGED_BEGIN = "# >>> TP-Voyager managed MCP fields >>>"
_ROOT_MANAGED_END = "# <<< TP-Voyager managed MCP fields <<<"
_ENV_MANAGED_BEGIN = "# >>> TP-Voyager managed MCP env >>>"
_ENV_MANAGED_END = "# <<< TP-Voyager managed MCP env <<<"
_TABLE_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*?)\s*$")
_ROOT_KEYS = frozenset({"command", "args", "cwd", "enabled_tools", "env"})


class SyncError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_json_string(value) for value in values) + "]"


def _default_manifest_path() -> Path:
    return Path(__file__).with_name("tp-voyager.manifest.json")


def _default_config_path() -> Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return home / "config.toml"


def _absolute_runtime_cwd(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SyncError("manifest mcp.cwd must be a non-empty path")
    text = value.strip()
    windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"))
    posix_absolute = text.startswith("/")
    if not (windows_absolute or posix_absolute):
        raise SyncError("manifest mcp.cwd must be absolute")
    return text


def load_manifest(path: str | Path) -> tuple[dict[str, Any], bytes]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        data = manifest_path.read_bytes()
        raw = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError("TP-Voyager manifest is unreadable or invalid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != _MANIFEST_SCHEMA:
        raise SyncError("unsupported TP-Voyager manifest schema")
    mcp = raw.get("mcp")
    if not isinstance(mcp, dict):
        raise SyncError("manifest mcp section is required")
    name = str(mcp.get("name") or "").strip()
    if name != _ALLOWED_SERVER:
        raise SyncError("sync tool may only manage the tp_voyager MCP entry")
    if str(mcp.get("transport") or "").strip().lower() != "stdio":
        raise SyncError("Codex Desktop sync currently requires stdio transport")
    command = mcp.get("command")
    if (
        not isinstance(command, list)
        or not command
        or len(command) > 32
        or any(not isinstance(item, str) or not item.strip() or len(item) > 1024 for item in command)
    ):
        raise SyncError("manifest mcp.command must be a bounded argv list")
    env = mcp.get("env")
    if not isinstance(env, dict) or len(env) > 64:
        raise SyncError("manifest mcp.env must be a bounded object")
    clean_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SyncError("manifest mcp.env contains an invalid key")
        if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
            raise SyncError("manifest mcp.env contains an invalid value")
        clean_env[key] = value
    tools = mcp.get("required_captain_tools")
    if (
        not isinstance(tools, list)
        or not tools
        or len(tools) > 64
        or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in tools)
        or len(set(tools)) != len(tools)
    ):
        raise SyncError("manifest required_captain_tools must be a unique bounded list")
    normalized = {
        "name": name,
        "transport": "stdio",
        "command": [item.strip() for item in command],
        "cwd": _absolute_runtime_cwd(mcp.get("cwd")),
        "env": clean_env,
        "required_captain_tools": list(tools),
    }
    return normalized, data


def _table_name(line: str) -> str | None:
    match = _TABLE_RE.match(line)
    return match.group(1).strip() if match else None


def _matches_table(name: str | None, server: str, *, env: bool = False) -> bool:
    if name is None:
        return False
    normalized = re.sub(r"\s+", "", name)
    options = {
        f"mcp_servers.{server}",
        f'mcp_servers."{server}"',
        f"mcp_servers.'{server}'",
    }
    if env:
        options = {value + ".env" for value in options}
    return normalized in options


def _section_indices(lines: list[str], server: str, *, env: bool = False) -> list[tuple[int, int]]:
    starts = [
        index for index, line in enumerate(lines)
        if _matches_table(_table_name(line), server, env=env)
    ]
    output: list[tuple[int, int]] = []
    for start in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if _table_name(lines[index]) is not None:
                end = index
                break
        output.append((start, end))
    return output


def _line_key(line: str) -> str | None:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _KEY_RE.match(line)
    return match.group(1) if match else None


def _replace_marked_or_keys(
    body: list[str], *, begin: str, end: str, desired: list[str], managed_keys: set[str]
) -> list[str]:
    begin_positions = [i for i, line in enumerate(body) if line.strip() == begin]
    end_positions = [i for i, line in enumerate(body) if line.strip() == end]
    if begin_positions or end_positions:
        if len(begin_positions) != 1 or len(end_positions) != 1 or begin_positions[0] >= end_positions[0]:
            raise SyncError("TP-Voyager managed markers are malformed")
        left, right = begin_positions[0], end_positions[0]
        return body[:left] + [begin, *desired, end] + body[right + 1 :]
    retained = [line for line in body if _line_key(line) not in managed_keys]
    return [begin, *desired, end, *retained]


def _desired_root(manifest: dict[str, Any]) -> list[str]:
    argv = manifest["command"]
    return [
        f"command = {_json_string(argv[0])}",
        f"args = {_toml_array(argv[1:])}",
        f"cwd = {_json_string(manifest['cwd'])}",
        f"enabled_tools = {_toml_array(manifest['required_captain_tools'])}",
    ]


def _desired_env(manifest: dict[str, Any]) -> list[str]:
    return [f"{key} = {_json_string(value)}" for key, value in manifest["env"].items()]


def render_sync(existing: str, manifest: dict[str, Any], newline: str) -> tuple[str, str]:
    # splitlines() removes terminators; joining later keeps the caller's newline convention.
    lines = existing.splitlines()
    root_sections = _section_indices(lines, manifest["name"], env=False)
    env_sections = _section_indices(lines, manifest["name"], env=True)
    if len(root_sections) > 1 or len(env_sections) > 1:
        raise SyncError("Codex config contains duplicate tp_voyager sections")
    existed = bool(root_sections)

    if not root_sections:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([
            f"[mcp_servers.{manifest['name']}]",
            _ROOT_MANAGED_BEGIN,
            *_desired_root(manifest),
            _ROOT_MANAGED_END,
            "",
            f"[mcp_servers.{manifest['name']}.env]",
            _ENV_MANAGED_BEGIN,
            *_desired_env(manifest),
            _ENV_MANAGED_END,
        ])
    else:
        start, end = root_sections[0]
        header = lines[start]
        body = lines[start + 1 : end]
        body = _replace_marked_or_keys(
            body,
            begin=_ROOT_MANAGED_BEGIN,
            end=_ROOT_MANAGED_END,
            desired=_desired_root(manifest),
            managed_keys=set(_ROOT_KEYS),
        )
        lines[start:end] = [header, *body]
        # Recompute after changing root length.
        env_sections = _section_indices(lines, manifest["name"], env=True)
        if not env_sections:
            root_start, root_end = _section_indices(lines, manifest["name"], env=False)[0]
            insertion = [
                "",
                f"[mcp_servers.{manifest['name']}.env]",
                _ENV_MANAGED_BEGIN,
                *_desired_env(manifest),
                _ENV_MANAGED_END,
            ]
            lines[root_end:root_end] = insertion
        else:
            env_start, env_end = env_sections[0]
            header = lines[env_start]
            body = lines[env_start + 1 : env_end]
            body = _replace_marked_or_keys(
                body,
                begin=_ENV_MANAGED_BEGIN,
                end=_ENV_MANAGED_END,
                desired=_desired_env(manifest),
                managed_keys=set(manifest["env"]),
            )
            lines[env_start:env_end] = [header, *body]

    rendered = newline.join(lines)
    if existing.endswith(("\n", "\r")) or rendered:
        rendered += newline
    return rendered, "updated" if existed else "added"


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    out: list[str] = []
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            out.append(char)
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            out.append(char)
            continue
        if char == "#" and quote is None:
            break
        out.append(char)
    return "".join(out).strip()


def _parse_literal(value: str) -> Any:
    text = _strip_inline_comment(value)
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise SyncError("tp_voyager config contains an unsupported managed value") from exc


def _section_values(lines: list[str], start: int, end: int) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for line in lines[start + 1 : end]:
        if line.lstrip().startswith("#"):
            continue
        match = _KEY_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in output:
            raise SyncError(f"tp_voyager config contains duplicate key: {key}")
        output[key] = _parse_literal(match.group(2))
    return output


def check_text(text: str, manifest: dict[str, Any]) -> list[str]:
    lines = text.splitlines()
    roots = _section_indices(lines, manifest["name"], env=False)
    envs = _section_indices(lines, manifest["name"], env=True)
    drift: list[str] = []
    if len(roots) != 1:
        return ["mcp_entry_missing_or_duplicate"]
    if len(envs) != 1:
        drift.append("env_section_missing_or_duplicate")
    root = _section_values(lines, *roots[0])
    argv = manifest["command"]
    expected = {
        "command": argv[0],
        "args": argv[1:],
        "cwd": manifest["cwd"],
        "enabled_tools": manifest["required_captain_tools"],
    }
    for key, value in expected.items():
        if root.get(key) != value:
            drift.append(key)
    if envs:
        env_values = _section_values(lines, *envs[0])
        for key, value in manifest["env"].items():
            if env_values.get(key) != value:
                drift.append(f"env.{key}")
    return drift


def _summary(
    *, manifest: dict[str, Any], manifest_bytes: bytes, config_path: Path,
    before: bytes, after: bytes, action: str, ok: bool, drift: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "ok": ok,
        "action": action,
        "server": manifest["name"],
        "config_path": str(config_path),
        "manifest_sha256": _sha256(manifest_bytes),
        "config_sha256_before": _sha256(before),
        "config_sha256_after": _sha256(after),
        "entry": {
            "transport": manifest["transport"],
            "command": list(manifest["command"]),
            "cwd": manifest["cwd"],
            "env_keys": sorted(manifest["env"]),
            "enabled_tools": list(manifest["required_captain_tools"]),
        },
        "drift": list(drift or []),
        "secrets_returned": False,
        "provider_invocation_performed": False,
        "task_dispatch_performed": False,
    }


def sync(manifest_path: str | Path, config_path: str | Path, *, check_only: bool = False) -> dict[str, Any]:
    manifest, manifest_bytes = load_manifest(manifest_path)
    target = Path(config_path).expanduser().resolve()
    before = target.read_bytes() if target.exists() else b""
    try:
        text = before.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SyncError("Codex config must be UTF-8") from exc
    newline = "\r\n" if b"\r\n" in before else "\n"

    if check_only:
        drift = check_text(text, manifest) if target.exists() else ["config_missing"]
        return _summary(
            manifest=manifest, manifest_bytes=manifest_bytes, config_path=target,
            before=before, after=before,
            action="check-ok" if not drift else "check-failed",
            ok=not drift, drift=drift,
        )

    rendered, structural_action = render_sync(text, manifest, newline)
    after = rendered.encode("utf-8")
    drift = check_text(rendered, manifest)
    if drift:
        raise SyncError("generated Codex configuration failed TP-Voyager self-check")
    if after == before:
        return _summary(
            manifest=manifest, manifest_bytes=manifest_bytes, config_path=target,
            before=before, after=before, action="no-op", ok=True,
        )

    # Re-read immediately before replace to avoid silently clobbering a concurrent edit.
    current = target.read_bytes() if target.exists() else b""
    if current != before:
        raise SyncError("Codex config changed during synchronization; retry")
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode if target.exists() else None
    fd, temp_name = tempfile.mkstemp(prefix=".tp-voyager-codex-", suffix=".toml", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return _summary(
        manifest=manifest, manifest_bytes=manifest_bytes, config_path=target,
        before=before, after=after, action=structural_action, ok=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync TP-Voyager MCP registration into Codex Desktop global config")
    parser.add_argument("--manifest", default=str(_default_manifest_path()))
    parser.add_argument("--config", default=str(_default_config_path()))
    parser.add_argument("--check", action="store_true", help="Read-only validation; do not modify Codex config")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = sync(args.manifest, args.config, check_only=bool(args.check))
    except (SyncError, OSError) as exc:
        print(json.dumps({"schema": _SCHEMA, "ok": False, "error": str(exc), "secrets_returned": False}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
