# Worker Profile Store

Optional operator-owned profiles for `worker_profile_ref`.

Layout:

```text
worker-profiles/
└── <name>/
    └── <version>.md
```

A dispatch must provide the exact SHA-256 of the UTF-8 Markdown file. TP-Voyager refuses missing or mismatched profiles. Profile content is injected only into the transient Crew prompt; Session routing metadata stores only the verified `name`, `version`, and `sha256` reference.

Set `resources.worker_profiles_root` in `~/.tp-voyager/config.json` to use an external profile store instead of this default directory.
