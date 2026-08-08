"""Isolated subprocess runner for the tiered Runtime test profiles."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_runtime.testing.profiles import profile_targets

_MAX_CAPTURE_CHARS = 64 * 1024
_RESULT_PREFIX = "__AGENT_RUNTIME_TEST_RESULT__="


def _bounded_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= _MAX_CAPTURE_CHARS:
        return value
    return value[-_MAX_CAPTURE_CHARS:]




def _test_summary(stdout: str) -> dict[str, int]:
    for line in reversed(str(stdout or "").splitlines()):
        if line.startswith(_RESULT_PREFIX):
            try:
                value = json.loads(line[len(_RESULT_PREFIX):])
            except (json.JSONDecodeError, TypeError):
                break
            if isinstance(value, dict):
                return {
                    "tests_run": int(value.get("tests_run", 0)),
                    "skipped": int(value.get("skipped", 0)),
                }
    return {"tests_run": 0, "skipped": 0}

@dataclass(frozen=True)
class TargetResult:
    target: str
    status: str
    returncode: int | None
    duration_seconds: float
    timeout_seconds: int
    tests_run: int
    skipped: int
    stdout: str
    stderr: str


def run_target(target, *, python_executable: str) -> TargetResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                python_executable,
                "-m",
                "agent_runtime.testing.worker",
                target.name,
            ],
            text=True,
            capture_output=True,
            timeout=target.timeout_seconds,
            check=False,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        summary = _test_summary(completed.stdout)
        return TargetResult(
            target=target.name,
            status=status,
            returncode=completed.returncode,
            duration_seconds=round(time.monotonic() - started, 3),
            timeout_seconds=target.timeout_seconds,
            tests_run=summary["tests_run"],
            skipped=summary["skipped"],
            stdout=_bounded_output(completed.stdout),
            stderr=_bounded_output(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return TargetResult(
            target=target.name,
            status="timeout",
            returncode=None,
            duration_seconds=round(time.monotonic() - started, 3),
            timeout_seconds=target.timeout_seconds,
            tests_run=0,
            skipped=0,
            stdout=_bounded_output(exc.stdout),
            stderr=_bounded_output(exc.stderr),
        )


def run_profile(
    profile: str,
    *,
    python_executable: str = sys.executable,
    fail_fast: bool = False,
    start_index: int = 0,
    max_targets: int | None = None,
) -> dict:
    all_targets = profile_targets(profile)
    if start_index < 0:
        raise ValueError("start_index must be non-negative")
    end_index = None if max_targets is None else start_index + max_targets
    targets = all_targets[start_index:end_index]
    results: list[TargetResult] = []
    started = time.monotonic()
    for target in targets:
        result = run_target(target, python_executable=python_executable)
        results.append(result)
        print(
            f"[{result.status.upper():7}] {result.target} "
            f"({result.duration_seconds:.3f}s)",
            flush=True,
        )
        if result.status != "passed" and fail_fast:
            break
    counts = {
        status: sum(1 for item in results if item.status == status)
        for status in ("passed", "failed", "timeout")
    }
    return {
        "schema": "tp-voyager.test_profile_report/v1",
        "profile": profile,
        "profile_target_count": len(all_targets),
        "target_count": len(targets),
        "start_index": start_index,
        "executed_count": len(results),
        "test_method_count": sum(item.tests_run for item in results),
        "skipped_test_count": sum(item.skipped for item in results),
        "duration_seconds": round(time.monotonic() - started, 3),
        "counts": counts,
        "ok": counts["failed"] == 0 and counts["timeout"] == 0,
        "results": [asdict(item) for item in results],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "profile",
        choices=("smoke", "current", "regression", "stress", "release"),
    )
    parser.add_argument("--list", action="store_true", help="list targets only")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--json", dest="json_path", help="write JSON report")
    parser.add_argument("--python", default=sys.executable, help="Python executable")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-targets", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = profile_targets(args.profile)
    if args.list:
        for target in targets:
            print(f"{target.name}\t{target.timeout_seconds}\t{target.reason}")
        return 0
    report = run_profile(
        args.profile,
        python_executable=args.python,
        fail_fast=args.fail_fast,
        start_index=args.start_index,
        max_targets=args.max_targets,
    )
    if args.json_path:
        output = Path(args.json_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
