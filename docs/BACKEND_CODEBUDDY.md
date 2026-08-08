# CodeBuddy CLI Backend

## Official sources

- https://www.workbuddy.ai/docs/cli/
- https://www.workbuddy.ai/docs/cli/reference
- https://www.workbuddy.ai/docs/cli/iam
- https://www.workbuddy.ai/docs/cli/sdk-python

## TP-Voyager routes

```text
sdk_context_read_only
sdk_patch
```

China accounts use `CODEBUDDY_INTERNET_ENVIRONMENT=internal`. The Captain path uses the official Python SDK. `sdk_context_read_only` disables native tools and supplies only Runtime-rendered, hash-verified context. `sdk_patch` runs inside a Runtime-owned Git worktree and uses SDK host permission callbacks for path/command enforcement. Permission bypass mode is not used.

Model discovery is not claimed as machine-readable until an official supported catalog is confirmed; unknown catalog fields remain unknown rather than guessed.
