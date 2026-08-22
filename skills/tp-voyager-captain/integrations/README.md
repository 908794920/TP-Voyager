# Captain Host Integrations

Host-specific loading, packaging, and usage guidance lives here. Runtime execution remains under `agent_runtime/`; these integrations do not create another control plane.

```text
integrations/
├── codex/          # current Codex Desktop/Codex plugin packaging
└── claude-code/    # reserved naming/placement for a future Claude Code integration when real code is added
```

Do not create an empty host directory merely as a placeholder. A future host gets a sibling directory only when it has an actual installer, package, or guide.
