# Legacy WorkBuddy Data Compatibility

WorkBuddy is not a supported TP-Voyager Crew backend.

The only retained compatibility surfaces are historical persisted identifiers and explicit old-home migration inputs required to read/migrate accepted V1/V2 data, including legacy `workbuddy.* /v1` public schema strings and the old `~/.workbuddy/runtime/workbuddy_runtime.db` location.

No WorkBuddy transport, Gateway, ACP execution, current MCP tool, current Backend adapter, or current acceptance test is supported. Historical non-terminal WorkBuddy tasks encountered during restart reconciliation fail closed to `LOST`; TP-Voyager never substitutes CodeBuddy or Qoder automatically.
