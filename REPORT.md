# Overnight Build Report — WordPress + YouTube + Image + Cross-Post Context

**Branch:** `main` (uncommitted changes in working tree)
**Date:** 2026-05-11
**Plan file:** `/home/gonzalo/.claude/plans/i-want-you-to-sparkling-truffle.md`

This is the deliverable promised for the morning. The pipeline now has the
new draft-publishing surface fully built — sitting dormant until you flip
a single env var per channel.

---

## What was built

| # | Feature | Status | Activation |
|---|---|---|---|
| F1 | WordPress draft creation | Built, dormant | `WORDPRESS_ENABLED=true` after upgrading to Personal ($4/mo) |
| F2 | YouTube draft short upload | Built, dormant | `YOUTUBE_ENABLED=true` after OAuth setup |
| F3 | Smart-frame featured image | Live | Auto on every run (skips if ffmpeg/cv2 missing) |
| F4 | Cross-post context backend | Live (vault impl) | Auto via writer node; AnythingLLM stub ready for future swap |
| F5 | Backfill CLI | Built | `python -m pipeline.backfill_wordpress` — see Backfill Plan below |
| F6 | Notion schema migration | Built | `python -m pipeline.scripts.migrate_notion_blog_schema --apply` |
| F7 | Writer agent + node enhancements | Live | Auto on every run |
| F8 | Pipeline summary updates | Live | Auto on every run |

### Dormant by design

WordPress and YouTube are wired but switched off via env vars. The pipeline
still produces the artifacts for both stages on every run (bundle on disk +
metadata.json), so once you flip the switches nothing has to be regenerated.

**WordPress reality check (memory rule: no silent feature drops)**: the
WordPress.com **free plan blocks REST API writes** (post creation, media
upload, featured image, tags). The only way to make the pipeline actually
publish is the Personal plan ($4/mo) or higher. Until you upgrade,
`WORDPRESS_ENABLED=false` keeps the integration dormant; the per-run
bundle (`processed/<run_id>/langgraph/wordpress-draft/`) stays usable for
manual paste.

---

## Files changed / added

### New
- `pipeline/wordpress_client.py` — WP REST v2 client (Basic / OAuth2),
  media upload, draft creation, markdown→HTML, excerpt extractor.
- `pipeline/youtube_client.py` — YouTube Data API v3 uploader.
- `pipeline/image_extractor.py` — ffmpeg + OpenCV smart frame picker.
- `pipeline/backfill_wordpress.py` — CLI for chronological Notion → WP backfill.
- `pipeline/blog_context/__init__.py` — backend protocol + selector.
- `pipeline/blog_context/vault_backend.py` — Obsidian-vault impl.
- `pipeline/blog_context/anythingllm_backend.py` — future-proof stub.
- `pipeline/nodes/wordpress_draft.py` — pipeline stage 5c.
- `pipeline/nodes/youtube_upload.py` — pipeline stage 4d.
- `pipeline/nodes/extract_image.py` — pipeline stage 4c.
- `pipeline/scripts/migrate_notion_blog_schema.py` — idempotent schema add.
- `scripts/scrape_youtube_tags.py` — one-shot yt-dlp tag harvester.
- `scripts/youtube_oauth_setup.py` — refresh-token capture helper.
- `config/youtube_metadata.json` — channel defaults (tags, category, privacy).
- `.claude/agents/youtube-upload-agent.md` — metadata-generation agent prompt.
- `tests/test_wordpress_client.py` — pure-helper unit tests (4 cases).
- `tests/test_blog_context_vault.py` — vault backend tests (4 cases).
- `tests/test_image_extractor.py` — pure-helper + e2e (skipped if deps missing).

### Edited
- `pipeline/state.py` — added WP/YT/image/excerpt/notion_blog_page_id keys.
- `pipeline/contracts.py` — added input contracts for the three new nodes.
- `pipeline/graph.py` — new topology with parallel branches + joins.
- `pipeline/notion_client.py` — query_blog_pages, update_blog_page_wordpress_url,
  get_blog_page_markdown, get_blog_page, extended create_blog_page.
- `pipeline/nodes/notion_blog.py` — round-trips `notion_blog_page_id`, sets
  Status="Pending" + Excerpt.
- `pipeline/nodes/writer.py` — injects CROSS-POST CONTEXT block; parses the
  new `**Excerpt:**` line; writes `blog_post_excerpt` into state.
- `pipeline/nodes/validator.py` — summary line items for WP, YouTube, image.
- `.claude/agents/painforwisdom-writer.md` — new Excerpt + YT placeholder +
  cross-post linking guidance.
- `.env.sandbox.template` — WordPress / YouTube / blog_context env vars.
- `pipeline/requirements.txt` — markdown, opencv-python-headless, numpy,
  google-api-python-client, google-auth, google-auth-oauthlib.

---

## Graph topology after change

```
START
  → transcribe
  → extract ─┬─▶ kb_curator ─┬─▶ writer ─▶ notion_blog ─────────┐
             │               └─▶ research ─▶ notion_research ───┐
             ├─▶ extract_image ──────────────────────────────┐  │
             └─▶ youtube_upload ─────────────────────────────┼──┤
                                                              ▼  │
                                                       wordpress_draft ──┐
                                                                          ▼
                                                                     validator → END
```

- `extract_image` and `youtube_upload` fan out from `extract` (in parallel
  with the existing `kb_curator` branch).
- `wordpress_draft` joins `notion_blog` (page id) + `extract_image`
  (featured image path).
- `validator` joins `wordpress_draft`, `notion_research`, `youtube_upload`.

All new nodes self-skip on `validator_verdict="FAIL"` or missing inputs
and catch their own exceptions — they never raise into the graph.

---

## Verification (run these before the first live push)

### 1) Static
```
python -m unittest tests.test_contracts tests.test_blog_context_vault tests.test_wordpress_client
# ran 16 tests in 0.014s — OK on this host
```

### 2) Dependencies
```
pip install -r pipeline/requirements.txt
sudo apt install ffmpeg          # if not already installed
pipx install yt-dlp              # only needed for scrape_youtube_tags.py
```

### 3) Notion schema migration (FIRST — everything else assumes new props)
```
python -m pipeline.scripts.migrate_notion_blog_schema --dry-run
python -m pipeline.scripts.migrate_notion_blog_schema --apply
```

### 4) End-to-end smoke (no live quota)
```
python -m pipeline.run --profile sandbox --video tests/fixtures/<sample>.mp4 --auto-approve --telegram-on-error
```
Expected output in `processed/<run_id>/langgraph/`:
- `wordpress-draft/{post.md, post.html, meta.json, featured.jpg, SKIPPED.md}`
- `youtube-upload/{metadata.json, SKIPPED.md}`
- validator PASS or PARTIAL.

### 5) Image extractor end-to-end (if ffmpeg + cv2 installed)
```
python -m unittest tests.test_image_extractor
```

### 6) Live WP test (after upgrade)
Set `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD`, then flip
`WORDPRESS_ENABLED=true`. Run a single pipeline. Verify the draft in WP
admin. Delete it. **Recommended**: point at a throwaway WP.com test blog
first via `WORDPRESS_SITE`.

### 7) Live YouTube test
After running `scripts/youtube_oauth_setup.py`, set the three YT env vars,
flip `YOUTUBE_ENABLED=true`. Run a single pipeline. Verify the draft in
YouTube Studio. Delete after.

### 8) Backfill dry-run
```
python -m pipeline.backfill_wordpress --profile prod --dry-run --limit 3
```
This walks the oldest 3 unpublished Notion rows, writes bundles to
`processed/_backfill/<page_id>/`, and prints what it would do without
touching WP or Notion.

---

## Backfill plan

The pipeline now has a single CLI command for clearing the long tail of
unpublished Notion blog posts into WordPress drafts:

```
python -m pipeline.backfill_wordpress --profile prod --limit 1
```

### Mechanics

- **Order**: Notion query is sorted `Date` ascending. The oldest
  unpublished post goes first, every time. This is non-negotiable because
  newer posts reference older ones via `[[link:<slug>]]`; reversing the
  order would mint broken links.
- **One per invocation by default** (`--limit 1`). Run it daily; we tag
  every backfilled post with `painforwisdom`, `backfill` so they are easy
  to find in WP.
- **Source video lookup**: backfill scans `processed/<YYYY-MM-DD>_*/source/`
  for a video matching the Notion `Date` property. If found → smart-frame
  featured image. If missing → no featured image (warn, continue).
- **Excerpt**: uses the existing `Excerpt` Notion property if set;
  otherwise generates a fresh 50-word excerpt via `first_50_words`.
- **Notion round-trip**: on success, writes back `WordPress URL` +
  `Status="Draft Created"` (or `Published` if `Published?=true` already)
  + the excerpt that ended up on WP.
- **Strict halt on failure**: any non-network failure aborts the loop
  before incrementing. Resume by re-running — the loop will pick up at
  the first row without a `WordPress URL`.

### Once-per-day cadence (suggested cron)

```
0 8 * * *  cd /home/gonzalo/workspace/painforwisdom/painforwisdom && /usr/bin/python -m pipeline.backfill_wordpress --profile prod --limit 1 >> processed/_backfill/cron.log 2>&1
```

This fires at 08:00, processes one post per day, halts on errors. The
strict-halt behaviour is the safety: a single failure pauses the queue
instead of skipping a row out of order.

### Pre-flight checklist

1. Confirm WP Personal plan is active and you can log into wp-admin.
2. Generate an Application Password (`Account → Security → Application
   Passwords → painforwisdom-pipeline`) and put it in `.env`:
   `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD`.
3. Run `python -m pipeline.scripts.migrate_notion_blog_schema --apply`.
4. Test with `--dry-run --limit 3`.
5. Test the live path on a throwaway WP.com blog by setting
   `WORDPRESS_SITE=<your-test-blog>.wordpress.com` and running
   `--limit 1`. Verify in WP admin. Delete the test post.
6. Point back at `painforwisdom.wordpress.com`. Flip
   `WORDPRESS_ENABLED=true`. Run `--limit 1`. Verify. From here, the cron
   takes over.

### Estimated time per page

- Notion fetch: <1s
- ffprobe + frame extraction + OpenCV scoring (10 candidates): 3–8s on a
  3-minute video
- WP media upload: 2–4s for a 1200x630 JPEG
- WP draft create: 1–2s
- Notion patch: <1s
- **Total: ~10–15s per page**

A 50-page backfill at one per day = 50 days. If you want to drain the
queue faster, raise `--limit` (e.g. `--limit 5`) — chronological order is
preserved within a single invocation.

---

## Activation gates (what you, Gonzalo, still need to do)

1. **(Cheap)** Run the Notion schema migration. Idempotent, free.
2. **($4/mo)** Upgrade `painforwisdom.wordpress.com` to Personal. Create
   an Application Password. Put it in `.env`. Flip `WORDPRESS_ENABLED=true`.
3. **(Free)** Set up a Google Cloud project + YouTube Data API + OAuth
   client (instructions in `scripts/youtube_oauth_setup.py`). Run that
   script to capture a refresh token. Put it in `.env`. Flip
   `YOUTUBE_ENABLED=true`.
4. **(Free)** Run `scripts/scrape_youtube_tags.py` once to seed the
   default tags from your existing shorts.
5. **(Free)** Run the backfill `--dry-run` to confirm the queue order
   matches what you expect.

After step 2, the daily pipeline (and the backfill cron) start producing
real WordPress drafts. After step 3, the daily pipeline also uploads a
draft short to YouTube. Nothing publishes publicly without you clicking
publish manually inside each platform's admin UI.

---

## Open risks / morning review

1. **`[[link:<slug>]]` resolution at render time**: the writer can now
   emit cross-post links via this markup, but the WordPress renderer
   currently leaves them as literal text. Once the first post that
   actually contains a `[[link:foo]]` is drafted, you'll need to decide
   whether the renderer should (a) look up the WP permalink from Notion,
   or (b) leave them as plain text for manual replacement. Adding a
   `Permalink` Notion property is the cleanest path.
2. **YouTube quota**: `videos.insert` costs 1600 units; daily cap 10k.
   You can upload roughly 6 shorts per day in steady state. The pipeline
   currently uploads exactly one per run — well within the cap.
3. **OpenCV install risk on some hosts**: if `opencv-python-headless`
   fails to install (musl/aarch64 issues), the image node falls back to
   writing `IMAGE_EXTRACTION_FAILED.md` and the WP node posts without a
   featured image. Add `ffmpeg`-only fallback if this becomes a problem.
4. **Tag policy**: pipeline currently tags posts as
   `painforwisdom + <themes> + <frameworks>`. Backfill posts also get a
   `backfill` tag so they can be filtered in WP. Adjust the tag rule in
   `pipeline/nodes/wordpress_draft.py` if you'd like a different policy.
5. **Default featured image**: backfill rows without a source video go
   live with no featured image. If you want a fallback (e.g. a blog
   logo), drop `assets/default_cover.jpg` into the repo and I can wire
   it in (~10 lines).
6. **AnythingLLM swap**: the stub backend will raise
   `NotImplementedError` if anyone flips `BLOG_CONTEXT_BACKEND=anythingllm`
   without setting the three AnythingLLM env vars — `get_backend()`
   defensively falls back to vault with a printed warning. Implement the
   real chat call inside `anythingllm_backend.py` once your workspace is
   stable.

---

## Diagnostics summary

- `python -m unittest tests.test_contracts tests.test_blog_context_vault tests.test_wordpress_client` → **16 / 16 passing**.
- All new and modified Python files parse cleanly (`ast.parse`).
- Pyright shows "import could not be resolved" warnings on every
  `pipeline.*` import — this is a long-standing host config issue (the
  project uses runtime imports via `python -m pipeline.*` rather than
  installing the package). Existing code has the same warnings.

---

## How to roll this back if it breaks

Everything is additive except:
- `pipeline/graph.py` topology (replaced `notion_blog → validator` with the
  new chain).
- `pipeline/state.py` (new keys).
- `pipeline/contracts.py` (three new node entries).
- `pipeline/nodes/writer.py` (excerpt parse, cross-post context).
- `pipeline/nodes/notion_blog.py` (now also returns `notion_blog_page_id`
  and writes `Status` + `Excerpt` properties — backwards-compatible if
  the schema migration is **not** applied; Notion silently rejects the
  extra props).

Reverting is `git restore` plus removing the new files. The Notion DB
schema additions (`WordPress URL`, `Status`, `Excerpt`) are also harmless
to leave in place if you revert the code.

---

*Built overnight. Tested. Documented. Awaits Gonzalo's two flips
(`WORDPRESS_ENABLED=true`, `YOUTUBE_ENABLED=true`) and one upgrade.*
