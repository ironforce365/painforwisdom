---
name: retry-failed
description: >
  Retry transcripts in to_be_retried/ through the full content pipeline.
  Usage: /retry-failed [filename]
  If no filename is given, lists all pending files and processes them all.
---

Retry one or all files sitting in `to_be_retried/`. Supports transcripts (`.txt`) and videos (`.mp4`, `.mov`, `.m4v`).

## Steps

### 1 — Identify files to process

If a filename argument was provided (e.g. `/retry-failed transcript_2026-02-17.txt`),
use that single file. Otherwise list all supported files in `to_be_retried/`:

```bash
ls ./to_be_retried/*.txt ./to_be_retried/*.mp4 ./to_be_retried/*.mov ./to_be_retried/*.m4v 2>/dev/null || echo "EMPTY"
```

If the folder is empty, report "No files pending retry." and stop.

### 2 — Show the list and confirm

Print the files that will be processed. If there is more than one file and
`--yes` was not passed as an argument, ask the user to confirm before proceeding.

### 3 — Process each file

For each file, run the pipeline using `run-pipeline.sh`:

```bash
./run-pipeline.sh "$FILE"
```

This starts a full new pipeline session for the file. Wait for it to complete
before moving to the next file.

### 4 — Remove from to_be_retried/ on success

After each file completes successfully (exit code 0), remove it from `to_be_retried/`:

```bash
rm ./to_be_retried/$(basename "$FILE")
```

If the pipeline exits with a non-zero code, leave the file in `to_be_retried/`
and report the failure — do not remove it.

### 5 — Report

After all files are processed, print a summary:
```
Retry complete.

✓ transcript_2026-02-17.txt — processed and removed from queue
✗ transcript_2026-02-19.txt — failed (exit code N), left in queue
```
