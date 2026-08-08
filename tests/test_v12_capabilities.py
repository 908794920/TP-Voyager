from __future__ import annotations

import unittest

from agent_runtime.backends.base import BackendCapabilities
from agent_runtime.backends.fake import FakeBackend
from agent_runtime.backends.registry import BackendRegistry
from agent_runtime.application.capability_service import (
    BackendCapabilityService,
    CapabilityQueryError,
    CapabilityRequirements,
)


class LimitedBackend(FakeBackend):
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            runtime="limited",
            routes=("print",),
            supports_resume=False,
            supports_streaming=False,
            supports_cancel=True,
            supports_reasoning_effort=False,
            observability="low",
        )


class BrokenDeclarationBackend(FakeBackend):
    def capabilities(self):
        return {"runtime": "broken"}


class BackendCapabilityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = BackendRegistry()
        self.registry.register("fake", FakeBackend)
        self.registry.register("limited", LimitedBackend)
        self.registry.register("broken", BrokenDeclarationBackend)
        self.service = BackendCapabilityService(self.registry)

    def test_catalog_is_read_only_and_deterministic(self) -> None:
        result = self.service.query()
        self.assertEqual([b["registered_name"] for b in result["backends"]], ["broken", "fake", "limited"])
        self.assertEqual(result["matches"], ["fake", "limited"])
        self.assertFalse(result["selection_performed"])
        self.assertFalse(result["dispatch_performed"])

    def test_explicit_requirements_filter_without_selecting(self) -> None:
        result = self.service.query(
            CapabilityRequirements(route="fake", require_resume=True, require_streaming=True)
        )
        self.assertEqual(result["matches"], ["fake"])
        self.assertEqual(result["match_count"], 1)
        limited = next(item for item in result["backends"] if item["registered_name"] == "limited")
        self.assertEqual(limited["mismatch_reasons"], ["route", "resume", "streaming"])

    def test_runtime_filter_limits_declarations(self) -> None:
        result = self.service.query(CapabilityRequirements(runtime="limited"))
        self.assertEqual(len(result["backends"]), 1)
        self.assertEqual(result["matches"], ["limited"])

    def test_invalid_query_fails_closed(self) -> None:
        with self.assertRaises(CapabilityQueryError):
            self.service.query(CapabilityRequirements(route="acp/resume"))

    def test_broken_declaration_is_reported_without_probe(self) -> None:
        result = self.service.query(CapabilityRequirements(runtime="broken"))
        self.assertEqual(result["matches"], [])
        self.assertFalse(result["backends"][0]["declaration_ok"])
        self.assertNotIn("error", result["backends"][0])


if __name__ == "__main__":
    unittest.main()
