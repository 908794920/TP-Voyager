from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "agent_runtime" / "api" / "mcp_server.py"


class RuntimeObservationIntegrationTests(unittest.TestCase):
    def test_runtime_wires_observation_recorder_without_changing_durable_event_store(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn("AgentObservationRecorder", source)
        self.assertIn("AgentObservationStore", source)
        self.assertIn("_AGENT_OBSERVATION_STORE = AgentObservationStore()", source)
        self.assertNotIn('runtime" / "observations', source)
        self.assertIn("_AGENT_OBSERVATIONS.activity(task, activity)", source)
        self.assertIn("_AGENT_OBSERVATIONS.usage(task, usage)", source)
        self.assertIn("_AGENT_OBSERVATIONS.started(task", source)
        self.assertIn("_AGENT_OBSERVATIONS.completed(task, answer=task.answer", source)
        self.assertIn("_AGENT_OBSERVATIONS.cancelled(task", source)
        self.assertIn("_AGENT_OBSERVATIONS.failed(task, reason=type(exc).__name__", source)

    def test_runtime_activity_sink_receives_typed_backend_activity(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn("def on_activity(activity: BackendActivity) -> None:", source)
        self.assertIn("_note_task_activity(task, activity.kind)", source)


if __name__ == "__main__":
    unittest.main()
