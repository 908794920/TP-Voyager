# Qoder CLI Backend

## Official sources

- https://docs.qoder.com/en/cli/model
- https://docs.qoder.com/en/cli/acp
- https://docs.qoder.com/en/cli/sdk/python/quick-start
- https://docs.qoder.com/en/cli/sdk/python/tools
- https://docs.qoder.com/en/cli/sdk/permissions

## TP-Voyager routes

```text
acp_read_only
acp_patch
```

Both controlled routes use official ACP without `--yolo`. Read-only advertises no write/terminal capability and rejects permission escalation. Patch mode runs in a Runtime-owned Git worktree; host callbacks enforce allowed paths and exact Captain-approved command argv/cwd. `qodercli --list-models` is the current dynamic model catalog source.

Legacy `acp` / `print` routes may remain only as non-Captain compatibility/diagnostic paths and are never an automatic fallback.


Legacy `acp` / `print` execution routes are not part of the current production surface. TP-Voyager exposes only `acp_read_only` and `acp_patch`; neither launches Qoder with `--yolo`.
