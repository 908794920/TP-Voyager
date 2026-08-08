"""Execute one unittest target and terminate without waiting for leaked threads.

All unittest teardown hooks run before the forced process exit.  The final
``os._exit`` only skips Python interpreter shutdown waits caused by historical
non-daemon test threads; production code is not affected.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_RESULT_PREFIX = "__AGENT_RUNTIME_TEST_RESULT__="


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m agent_runtime.testing.worker <target>", file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromName(args[0])
    if suite.countTestCases() <= 0:
        print(f"no tests found: {args[0]}", file=sys.stderr)
        return 5
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        "tests_run": int(result.testsRun),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }
    print(_RESULT_PREFIX + json.dumps(summary, sort_keys=True), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
