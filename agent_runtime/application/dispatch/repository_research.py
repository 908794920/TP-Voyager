"""Bounded external GitHub repository acquisition for static Crew research.

This service is a Runtime preparation primitive, not a planner or a network
browser.  The Captain must provide the exact public GitHub URL, size ceiling,
target directory, report path, Crew, model, and read scope.  Only the Runtime
performs the one approved metadata request and shallow clone.  Crew receives a
read-only local snapshot and cannot install/build/run the downloaded source.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_runtime.domain.dispatch import ReadScope, RepositoryResearchSpec

_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]{1,100})/(?P<repo>[A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_MAX_FILES = 20_000


class RepositoryResearchError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryResearchWorkspace:
    root: str
    source_root: str
    report_path: str
    source_url: str
    declared_max_size_bytes: int
    api_size_bytes: int
    checkout_size_bytes: int
    commit: str

    def routing_metadata(self) -> dict[str, Any]:
        return {
            "url": self.source_url,
            "max_size_bytes": self.declared_max_size_bytes,
            "source_subdirectory": "source",
            "report_path": self.report_path,
            "repository_size_bytes": self.checkout_size_bytes,
            "commit": self.commit,
            "acquisition": "github_api_precheck+git_clone_depth_1",
            "network_scope": "github_metadata_and_clone_only",
            "crew_source_network_tools_exposed": False,
            "provider_transport_required": True,
        }


MetadataLoader = Callable[[str, str], dict[str, Any]]
Runner = Callable[..., subprocess.CompletedProcess[str]]


class RepositoryResearchService:
    def __init__(
        self,
        *,
        metadata_loader: MetadataLoader | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._metadata_loader = metadata_loader or self._load_github_metadata
        self._runner = runner or subprocess.run

    @staticmethod
    def prefix_read_scope(scope: ReadScope) -> ReadScope:
        """Translate Captain scope relative to the repo into target/source scope."""
        return ReadScope(
            files=tuple(f"source/{item}" for item in scope.files),
            directories=tuple(f"source/{item}" for item in scope.directories),
            globs=tuple(f"source/{item}" for item in scope.globs),
            max_files=scope.max_files,
            max_bytes=scope.max_bytes,
        )

    def prepare(self, spec: RepositoryResearchSpec) -> RepositoryResearchWorkspace:
        owner, repo = self._parse_github_url(spec.url)
        metadata = self._metadata_loader(owner, repo)
        raw_size = metadata.get("size")
        if not isinstance(raw_size, int) or raw_size < 0:
            raise RepositoryResearchError("GitHub metadata did not provide a usable repository size")
        api_size_bytes = raw_size * 1024
        if api_size_bytes > spec.max_size_bytes:
            raise RepositoryResearchError("repository exceeds Captain max_size_bytes before clone")

        target = Path(spec.target_directory).expanduser()
        if not target.is_absolute():
            raise RepositoryResearchError("repository target_directory must be absolute")
        target = target.resolve()
        if target.exists():
            raise RepositoryResearchError("repository target_directory already exists; overwrite is forbidden")
        parent = target.parent
        if not parent.is_dir():
            raise RepositoryResearchError("repository target parent directory does not exist")

        source = target / "source"
        try:
            target.mkdir(parents=False, exist_ok=False)
            git_env = dict(os.environ)
            # Static research must not trigger interactive credential prompts or
            # Git LFS smudge downloads during checkout. Provider transport for
            # the Crew is separate from this bounded source-acquisition step.
            git_env["GIT_TERMINAL_PROMPT"] = "0"
            git_env["GIT_LFS_SKIP_SMUDGE"] = "1"
            completed = self._runner(
                [
                    "git", "clone", "--depth", "1", "--single-branch", "--no-tags",
                    spec.url, str(source),
                ],
                env=git_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0 or not source.is_dir():
                raise RepositoryResearchError("bounded shallow clone failed")
            checkout_size, file_count = self._checkout_size(source)
            if checkout_size > spec.max_size_bytes:
                raise RepositoryResearchError("checked-out repository exceeds Captain max_size_bytes")
            if file_count > _MAX_FILES:
                raise RepositoryResearchError(f"repository file count exceeds {_MAX_FILES}")
            commit = self._git_text(source, ["rev-parse", "HEAD"])
            if not commit:
                raise RepositoryResearchError("cloned repository has no resolvable HEAD")
            # Prevent later accidental fetch/pull from this static research copy.
            self._runner(
                ["git", "remote", "remove", "origin"], cwd=str(source),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False, text=True, encoding="utf-8", errors="replace",
            )
            (target / "reports").mkdir(parents=False, exist_ok=False)
            return RepositoryResearchWorkspace(
                root=str(target),
                source_root=str(source),
                report_path=spec.report_path,
                source_url=spec.url,
                declared_max_size_bytes=spec.max_size_bytes,
                api_size_bytes=api_size_bytes,
                checkout_size_bytes=checkout_size,
                commit=commit,
            )
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @staticmethod
    def cleanup(workspace: RepositoryResearchWorkspace) -> None:
        shutil.rmtree(Path(workspace.root), ignore_errors=True)

    @staticmethod
    def _parse_github_url(url: str) -> tuple[str, str]:
        match = _GITHUB_RE.fullmatch(str(url or "").strip())
        if match is None:
            raise RepositoryResearchError("only explicit public https://github.com/<owner>/<repo> URLs are accepted")
        return match.group("owner"), match.group("repo")

    @staticmethod
    def _load_github_metadata(owner: str, repo: str) -> dict[str, Any]:
        request = Request(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "TP-Voyager/1.0.3",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - URL is fixed api.github.com
                data = response.read(512 * 1024 + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RepositoryResearchError("GitHub repository metadata precheck failed") from exc
        if len(data) > 512 * 1024:
            raise RepositoryResearchError("GitHub metadata response exceeded size limit")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RepositoryResearchError("GitHub metadata response was invalid") from exc
        if not isinstance(payload, dict):
            raise RepositoryResearchError("GitHub metadata response was not an object")
        if bool(payload.get("private")):
            raise RepositoryResearchError("private repositories are not supported by repository_research")
        return payload

    def _git_text(self, cwd: Path, args: list[str]) -> str:
        completed = self._runner(
            ["git", *args], cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            check=False, text=True, encoding="utf-8", errors="replace",
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    @staticmethod
    def _checkout_size(source: Path) -> tuple[int, int]:
        total = 0
        files = 0
        for path in source.rglob("*"):
            try:
                rel = path.relative_to(source)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == ".git":
                continue
            if path.is_symlink() or not path.is_file():
                continue
            files += 1
            try:
                total += path.stat().st_size
            except OSError as exc:
                raise RepositoryResearchError("repository file could not be inspected") from exc
        return total, files
