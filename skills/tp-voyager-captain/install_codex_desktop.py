#!/usr/bin/env python3
"""Install/update the TP-Voyager Captain Skill and synchronize Codex Desktop MCP.

This script only writes inside the selected Codex home. Repository files stay
portable: machine-specific bindings are written only to the installed Skill.
Unknown files already present in the installed Skill directory are preserved.
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
import sys
import tempfile
from typing import Any

_SCHEMA = "tp-voyager.codex_install/v1"
_BINDINGS_SCHEMA = "tp-voyager.install_bindings/v1"
_BINDINGS_FILE = "tp-voyager.bindings.json"
_SKILL_NAME = "tp-voyager-captain"
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


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise InstallError(f"unable to load installer dependency: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    values["repository_root"] = str(source_skill.parent.parent.resolve())
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


def install(
    source_skill: str | Path,
    codex_home: str | Path,
    *,
    bindings: dict[str, str] | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    source = Path(source_skill).expanduser().resolve()
    if not source.is_dir():
        raise InstallError("source Captain Skill directory does not exist")
    sync_script = source / "sync_codex_desktop.py"
    manifest = source / "tp-voyager.manifest.json"
    if not sync_script.is_file() or not manifest.is_file():
        raise InstallError("source Captain Skill is incomplete")
    source_sync = _load_module(sync_script, "tp_voyager_source_sync")

    target = Path(installed_skill_path(codex_home)).expanduser().resolve()
    binding_path = target / _BINDINGS_FILE
    existing_bindings = _read_binding_file(binding_path) if target.exists() else {}
    values = _binding_values(
        source, source_sync, provided=bindings, existing=existing_bindings, check_only=check_only
    )
    # Validate the portable manifest with resolved machine bindings before any write.
    source_sync.load_manifest(manifest, bindings=values)
    manifest_bytes = manifest.read_bytes()
    managed = _managed_files(source)
    drift = _skill_drift(source, target, managed)
    desired_binding_bytes = _bindings_bytes(values)
    bindings_changed = not binding_path.is_file() or binding_path.read_bytes() != desired_binding_bytes

    config_path = Path(codex_home).expanduser().resolve() / "config.toml"
    if check_only:
        if not target.exists():
            drift = ["installed_skill_missing"]
            mcp_result = {
                "ok": False,
                "action": "check-failed",
                "drift": ["config_not_checked"],
                "config_sha256_before": _sha256(config_path.read_bytes()) if config_path.exists() else _sha256(b""),
                "config_sha256_after": _sha256(config_path.read_bytes()) if config_path.exists() else _sha256(b""),
            }
        else:
            target_sync_path = target / "sync_codex_desktop.py"
            target_manifest = target / "tp-voyager.manifest.json"
            if not target_sync_path.is_file() or not target_manifest.is_file() or bindings_changed:
                mcp_result = {
                    "ok": False,
                    "action": "check-failed",
                    "drift": ["installed_skill_or_bindings_drift"],
                    "config_sha256_before": _sha256(config_path.read_bytes()) if config_path.exists() else _sha256(b""),
                    "config_sha256_after": _sha256(config_path.read_bytes()) if config_path.exists() else _sha256(b""),
                }
            else:
                target_sync = _load_module(target_sync_path, "tp_voyager_installed_sync_check")
                mcp_result = target_sync.sync(
                    target_manifest,
                    config_path,
                    check_only=True,
                    bindings_path=binding_path,
                )
        ok = not drift and not bindings_changed and bool(mcp_result.get("ok"))
        return {
            "schema": _SCHEMA,
            "ok": ok,
            "action": "check-ok" if ok else "check-failed",
            "source_skill_path": str(source),
            "target_skill_path": str(target),
            "config_path": str(config_path),
            "manifest_sha256": _sha256(manifest_bytes),
            "config_sha256_before": mcp_result.get("config_sha256_before"),
            "config_sha256_after": mcp_result.get("config_sha256_after"),
            "managed_file_count": len(managed),
            "skill_drift": drift + (["tp-voyager.bindings.json"] if bindings_changed else []),
            "mcp_drift": list(mcp_result.get("drift") or []),
            "mcp_action": mcp_result.get("action"),
            "binding_keys": sorted(values),
            "provider_invocation_performed": False,
            "task_dispatch_performed": False,
        }

    changed_files: list[str] = []
    for relative in managed:
        source_file = source / relative
        target_file = target / relative
        data = source_file.read_bytes()
        if target_file.is_file() and target_file.read_bytes() == data:
            continue
        _atomic_write(target_file, data)
        changed_files.append(relative.as_posix())
    if bindings_changed:
        _atomic_write(binding_path, desired_binding_bytes)
        changed_files.append(_BINDINGS_FILE)

    target_sync = _load_module(target / "sync_codex_desktop.py", "tp_voyager_installed_sync")
    mcp_result = target_sync.sync(
        target / "tp-voyager.manifest.json",
        config_path,
        bindings_path=binding_path,
    )
    action = "changed" if changed_files or mcp_result.get("action") in {"added", "updated"} else "no-op"
    return {
        "schema": _SCHEMA,
        "ok": bool(mcp_result.get("ok")),
        "action": action,
        "source_skill_path": str(source),
        "target_skill_path": str(target),
        "config_path": str(config_path),
        "manifest_sha256": _sha256(manifest_bytes),
        "config_sha256_before": mcp_result.get("config_sha256_before"),
        "config_sha256_after": mcp_result.get("config_sha256_after"),
        "managed_file_count": len(managed),
        "changed_file_count": len(changed_files),
        "mcp_action": mcp_result.get("action"),
        "binding_keys": sorted(values),
        "env_keys": list((mcp_result.get("entry") or {}).get("env_keys") or []),
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
    parser = argparse.ArgumentParser(description="Install/update TP-Voyager Captain Skill and Codex Desktop MCP registration")
    parser.add_argument("--source-skill", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--codex-home", default=resolve_codex_home())
    parser.add_argument("--binding", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--check", action="store_true", help="Read-only validation; do not deploy Skill or edit Codex config")
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
        )
    except (InstallError, OSError, ValueError) as exc:
        print(json.dumps({"schema": _SCHEMA, "ok": False, "error": str(exc), "provider_invocation_performed": False, "task_dispatch_performed": False}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
