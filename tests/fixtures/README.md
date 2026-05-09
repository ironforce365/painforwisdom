# Smoke-test fixtures

Each transcript below exercises a distinct branch of the LangGraph pipeline.
Drive any of them through `tests/smoke_pipeline.sh` by overriding
`SMOKE_FIXTURE`:

```bash
SMOKE_FIXTURE=tests/fixtures/transcript_2026-04-17-strong-existing-themes.txt \
  bash tests/smoke_pipeline.sh
```

| Fixture | Expected `Content Quality` | Expected kb-curator path | What it exercises |
|---------|----------------------------|--------------------------|-------------------|
| `transcript_2026-04-14.txt`                          | Strong   | `NEEDS_APPROVAL_THEME` (new theme) → `PROCEED` after auto-approve | Theme-approval HITL round-trip; `--auto-approve` resume; full graph end-to-end. |
| `transcript_2026-04-15-flagged.txt`                  | Flagged  | `PROCEED` with `pattern-manifestation` auto-attached (no HITL per kb-curator rules) | `Flagged` quality classification; auto-attached pattern-manifestation theme bypasses approval. |
| `transcript_2026-04-16-weak.txt`                     | Weak     | `PROCEED` (entry created, but flagged for thinness) | `Weak` quality classification; pipeline still produces blog post + research. |
| `transcript_2026-04-17-strong-existing-themes.txt`   | Strong   | `PROCEED` directly — only existing themes/frameworks (`deliberate-discomfort`, `cookie-jar-types`, `body-literacy`) | Fastest happy path; no HITL interrupts; validates theme/framework reuse without approval round-trip. |

> Reset the sandbox between runs: `bash tests/sandbox_reset.sh`.
> Reverts the vault worktree and archives all pages in the sandbox Notion DBs.
