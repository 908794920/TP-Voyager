from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_runtime.backends.qoder.process import terminate_process_tree


class _LiveProcess:
    pid = 4321

    def __init__(self) -> None:
        self.wait_timeouts: list[float] = []

    def poll(self):
        return None

    def wait(self, timeout: float | None = None):
        self.wait_timeouts.append(float(timeout or 0))
        return 0

    def kill(self) -> None:
        raise AssertionError("kill is not expected when taskkill succeeded")


class QoderProcessTests(unittest.TestCase):
    def test_windows_tree_termination_waits_before_snapshot_cleanup_can_run(self) -> None:
        process = _LiveProcess()
        with (
            patch("agent_runtime.backends.qoder.process.os.name", "nt"),
            patch("agent_runtime.backends.qoder.process.subprocess.run"),
        ):
            terminate_process_tree(process, timeout=3.0)  # type: ignore[arg-type]
        self.assertEqual(process.wait_timeouts, [3.0])


if __name__ == "__main__":
    unittest.main()
