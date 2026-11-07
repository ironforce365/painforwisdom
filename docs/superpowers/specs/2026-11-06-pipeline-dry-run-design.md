# Pipeline dry run

## Why

To trust the pipeline before a real run, the user wants to run the same video again and again and look at what comes out. A real run gets in the way of that: it writes the knowledge base, so the next run of that video stops at the curator or collides with its own entry, and it adds Notion pages to the real tables.

The ask: a dry-run mode, visible, where nothing persists. Logs are fine, no knowledge base, and Notion goes to a separate table if one exists. It does (see below).

## What a dry run does

Everything a real run does, on the real input: Whisper, the same AI calls at the same cost, the validators, the packages. It keeps nothing outside `processed/<run_id>/`.

| What a real run touches | In a dry run |
|---|---|
| Vault entry, themes, frameworks, timeline, book outline | Written to a scratch copy, `processed/<run_id>/dry-run/vault/` |
| Notion blog and research tables | The sandbox tables from `.env.sandbox` |
| Transcript beside the input (`auto-generated/`) | `dry-run/transcripts/` |
| Whisper quarantine, which moves the input | The input stays; the run fails and says why |
| `pipeline/state/themes.db` | `dry-run/themes.db` |
| YouTube and WordPress | The package is built so its fields can be read. Publishing is refused |
| Telegram | Sent, tagged `[DRY RUN]`. The transcription script's own messages are not sent, because it can't tag them |
| Run folder, logs, checkpoint rows | Kept as usual. Deleting the run removes them |

## How

`pipeline/dry_run.py` changes environment variables before the pipeline modules read them at import. The nodes don't know about dry runs, so what is being tested is the real code.

- `python -m pipeline.run --dry-run` activates it. `--dir` is refused, because a batch moves its videos.
- `run.json` records `dry_run: true`. `pipeline.retry` reads it, so a resumed dry run stays one. Retry's discovery mode skips dry runs, since it would resume them with real paths.
- Four separate guards protect the real places: the start refuses when the sandbox tables are missing or are the real ones, `auto` publishing is forced to `manual`, the console refuses to publish, and `pipeline.publish` refuses too.

### The same video again

The scratch vault is copied without the entries dated the same day as the video, and without the lines mentioning them in the timeline, themes and outline. A video that already ran for real then reads as new. Otherwise the curator can answer `ALREADY_PROCESSED`, or hit "entry already exists". Dry runs never see each other's output, because each gets a fresh copy of the real vault.

### The Notion tables

The user recalled a dry-run table. No table has that name, but the sandbox profile has its own two, separate from prod. They are reachable with the sandbox key and their ids differ from prod. They share prod's titles, so ids are what the guard compares. Pages pile up there; `tests/sandbox_reset.sh` clears them, along with the sandbox vault.

The `sandbox` profile itself wasn't reused for the run: it writes to one shared sandbox vault, so a second run of a video would stop just as it does on prod.

## Where it shows

- **Start popup:** an amber **Dry run** switch. Turned on, the title becomes "Start a dry run" and a note says what it means. Each opening starts as a real run, like the language field.
- **Run page:** an amber banner above everything, on every tab. The cards read "Not saved", "In the sandbox table", "Preview only", and the graph's WordPress and YouTube steps read "preview only" instead of "needs you".
- **Publish panel:** the button is off, beside "Dry run: nothing is published". Fields can still be read and edited.
- **Dashboard:** a `DRY RUN` chip in Recent runs. Dry runs stay out of the run count and "last good run", and stay in total cost, because their AI calls are real. A package doesn't reach Needs you; a failure or a question does, labelled "Dry run: ...".
- **Input tile:** a dry run doesn't count as "Already ran".
- **Knowledge base page:** reads the real vault, so dry entries never appear.

Commands: **Restart with same input** stays a dry run. **Run for real** asks first, then starts a real run of the same input.

## Decisions made without the user

- Questions still stop a dry run (a new theme, say). They are part of what is being tested, and Auto-approve is there to skip them.
- The switch is off by default, and resets on each opening.
- Dropping a whole day from the scratch vault also hides any other video from that day. It's only a scratch copy, so nothing is lost.
- A dry run costs what a real one does. The note says so, and the cost tile counts it.

## Limits

- It tests against the sandbox Notion tables. If their schema drifts from prod, a dry run could fail where a real run wouldn't, or the reverse.
- It doesn't test publishing itself.
- The real curator can still decide differently once the real vault has changed.
