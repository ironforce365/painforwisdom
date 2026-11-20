# Blog engage: comments, hearts, moderation, build-failure alert

Feature order fo-f7e5e389. Builds section 9 and the section 4 build-failure alert of `docs/superpowers/specs/2026-11-18-own-blog-design.md` (the parent spec). This spec is the contract where the two differ; section 2 lists every difference and why.

## 1. Context and scope

- Site repo: `ironforce365/painforwisdom-site` (private, Astro 7, static). Not this monorepo. Every child works in a clone of that repo and opens its PR there. Parent spec section 6 still names `gonandrap/painforwisdom-site`; the real repo is `ironforce365`.
- Live host: `https://painforwisdom-site.pages.dev`, Cloudflare Pages project `painforwisdom-site`, production branch `main`, build `npm ci && npm run build`, output `dist`. No custom domain yet (parent section 5).
- Pre-condition: blog go-live (work order wo-467dd056) has created the Pages project and the site serves. Section 3 needs it; nothing else starts before section 3 merges.
- In scope: hearts, comments, Telegram moderation, Turnstile, D1, purge and backup crons, Pages build-failure alert to Telegram, privacy page text, CI for the site repo.
- Out of scope: custom domain (user purchase), email subscribe, analytics changes, anything in the monorepo `pipeline/` (blog publish preflight and live check is wo-c9ba40cb), WordPress retirement (wo-778f6c18).

## 2. Architecture and deviations from the parent spec

No zone exists (pages.dev only), so a standalone Worker cannot be routed under the site's `/api/*`, and zone WAF rate-limiting rules cannot be created. Therefore:

- **HTTP endpoints are Pages Functions** in the site repo's `functions/` directory, deployed with every Pages build, same origin as the pages. No CORS. Paths exactly as parent section 9's table. Functions bind D1 through the root `wrangler.toml` (`pages_build_output_dir = "dist"`).
- **Crons are one small Worker** `painforwisdom-engage-cron` in `worker/`: Pages Functions have no cron triggers. It binds the same production D1 database (and R2 if the user enables it). Free plan: 10 ms CPU per cron run, 5 cron triggers per account.
- **Preview deploys get their own D1 database** (`[env.preview]` in `wrangler.toml`) and no Telegram secrets, so branch previews never write prod data or send real alerts. `sendTelegram` is a no-op when its secrets are unset.
- **Rate limit**: the parent's "Cloudflare rate-limiting rule, 5/min per IP" needs a zone. Replaced by Turnstile on every write, the `(slug, visitor)` primary key, and the 60 s same-`ip_hash` comment check. No counter table: D1 writes for little gain. Revisit when the domain exists.
- **Slug list**: the build emits `dist/slugs.json`; Functions read it through `env.ASSETS.fetch`, so the list always matches the deployment serving the request.
- **Build-failure alert**: Cloudflare Notifications webhook into a Function if the account plan offers webhook destinations (unverified for Free); otherwise the cron Worker polls the Pages deployments API. Section 3 settles which; section 8 builds only that one.
- **Backup to R2 is optional**: R2 needs a payment method on the account. If the user declines in section 3, D1 Time Travel (point-in-time restore) is the backup and section 8 builds no backup cron.
- Everything else in parent section 9 stands: no login, no email field, moderation by default, signed single-use review links, GET never changes state, Bot API `sendMessage` only (never a webhook or `getUpdates` on the bot token).

Repo layout after this feature:

```
wrangler.toml                       Pages: D1 bindings prod + preview, vars (sections 3, 4)
engage/                             shared helpers, outside functions/ so they are not routes (section 4)
functions/api/posts/[slug]/index.ts     GET   (section 5)
functions/api/posts/[slug]/react.ts     POST  (section 5)
functions/api/posts/[slug]/comments.ts  POST  (section 5)
functions/moderate/[id].ts              GET   (section 6)
functions/api/moderate/[id].ts          POST  (section 6)
functions/api/hooks/pages.ts            POST  (section 8, webhook path only)
migrations/0001_init.sql            D1 schema (section 4)
worker/                             cron Worker, own wrangler.toml (section 8)
src/components/Engage.astro         heart + comments section (section 7)
src/pages/slugs.json.ts             emits dist/slugs.json (section 4)
docs/engage-setup.md                user-step record: ids, choices, secrets list (section 3)
.github/workflows/ci.yml            build + tests (section 4)
```

## 3. Cloudflare setup: user steps before any code

A Pages deploy whose `wrangler.toml` names a D1 `database_id` that does not exist fails, and a failed production deploy stops the site updating. So the databases exist before any binding is committed. This order is user steps plus one small PR; every step is given to the user as an exact command or click-path and confirmed by its visible effect, never by the user's say-so alone where a check is possible.

1. Confirm the Pages project `painforwisdom-site` exists and `https://painforwisdom-site.pages.dev/` returns 200. If not, stop and `jarvis wo ask`: blog go-live is not done.
2. `npx wrangler d1 create painforwisdom-engage` and `npx wrangler d1 create painforwisdom-engage-preview` (user runs; worker reads the two ids from the output).
3. Turnstile widget, managed mode, hostnames `painforwisdom-site.pages.dev` (previews are `*.painforwisdom-site.pages.dev`; add that hostname too if the dashboard accepts it). Record the site key (public) in `docs/engage-setup.md`. The secret goes to step 4.
4. Pages project secrets, Production: `TURNSTILE_SECRET`, `MODERATION_SECRET` and `SALT_SECRET` (each `openssl rand -hex 32`), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (same values as the pipeline prod `.env`), `PAGES_HOOK_SECRET` (`openssl rand -hex 32`). Preview environment: `TURNSTILE_SECRET`, `MODERATION_SECRET`, `SALT_SECRET` only. Pages build variable (both environments): `PUBLIC_TURNSTILE_SITEKEY`.
5. R2: ask the user whether to enable R2 (`painforwisdom-engage-backup`; needs a payment method). Record yes or no.
6. Build alert path: user opens Notifications, Add, Pages "Project updates", and reports whether a webhook destination can be created. Record `webhook` or `poll`. For `poll`, user creates an API token with Account, Cloudflare Pages, Read only, and keeps it for section 9; record the account id (not secret) as `CF_ACCOUNT_ID`.
7. PR to the site repo: root `wrangler.toml` with `name = "painforwisdom-site"`, `pages_build_output_dir = "dist"`, pinned `compatibility_date`, `[[d1_databases]]` binding `DB` (prod id, `migrations_dir = "migrations"`), `[env.preview]` with `DB` bound to the preview id, `[vars] SITE_ORIGIN = "https://painforwisdom-site.pages.dev"`. Plus `docs/engage-setup.md` recording: both D1 ids, Turnstile site key, secret names (never values), R2 yes or no, alert path `webhook` or `poll`, the date. After merge, the production deploy of `main` must succeed (check in the Pages dashboard or `npx wrangler pages deployment list`).
- Done: the PR merged, production deploy green, `docs/engage-setup.md` on `main` answers R2 and alert path.

## 4. Foundation: schema, shared library, slug list, test harness, CI

The base every other code section imports.

- `migrations/0001_init.sql`: the two tables of parent section 9 verbatim, plus `CREATE INDEX comments_slug_status ON comments(slug, status)` and `CREATE INDEX comments_ip_created ON comments(ip_hash, created_at)`. Applied to the preview DB locally by tests; applied to prod in section 9.
- `src/pages/slugs.json.ts`: static endpoint from `getPosts()` in `src/lib/posts.ts`, body `{"<slug>": {"title": "...", "path": "/YYYY/MM/DD/slug/"}}` (path from `postPath`). The build fails on a duplicate slug.
- `engage/` (repo root, TypeScript), each export unit-tested:
  - `type Env = { DB: D1Database; ASSETS: Fetcher; SITE_ORIGIN: string; TURNSTILE_SECRET: string; MODERATION_SECRET: string; SALT_SECRET: string; TELEGRAM_BOT_TOKEN?: string; TELEGRAM_CHAT_ID?: string; PAGES_HOOK_SECRET?: string }`.
  - `nowIso(date = new Date())`: `toISOString()`. Every `created_at` and `decided_at` is this ISO-8601 UTC format, so string `<` comparisons in sections 5 and 8 are time comparisons.
  - `json(data, status = 200)`; `apiError(status, code)` returns `{"error": code}`.
  - `clientIp(request)`: `CF-Connecting-IP`, fallback `"0.0.0.0"`.
  - `visitorHash(ip, ua, now, env)`: hex HMAC-SHA256 keyed by `HMAC(SALT_SECRET, UTC YYYY-MM-DD)` over `ip|ua`. The daily salt is derived, never stored.
  - `ipHash(ip, env)`: hex HMAC-SHA256(`SALT_SECRET`, `ip`). Stable across days so the 60 s check works across midnight.
  - `signReview(id, exp, env)`: hex HMAC-SHA256(`MODERATION_SECRET`, `${id}.${exp}`); `verifyReview(id, exp, sig, now, env)`: constant-time compare, false when `exp` (Unix seconds) is past; `reviewUrl(id, now, env)`: `${SITE_ORIGIN}/moderate/${id}?exp=${now+7d}&sig=...`.
  - `verifyTurnstile(token, ip, env)`: POST `https://challenges.cloudflare.com/turnstile/v0/siteverify`; true only when `success` is true and `hostname` is the `SITE_ORIGIN` host or a subdomain of it. Network failure is false (fail closed). Exception for local dev and tests: when `TURNSTILE_SECRET` is Cloudflare's documented always-pass test secret `1x0000000000000000000000000000000AA`, the hostname check is skipped (pair it with test site key `1x00000000000000000000AA`).
  - `sendTelegram(env: {TELEGRAM_BOT_TOKEN?: string; TELEGRAM_CHAT_ID?: string}, text)` (narrow type, so the cron Worker can call it without the Pages `Env`): no-op returning false when either Telegram secret is unset. Else Bot API `sendMessage`, plain text (no `parse_mode`), `disable_web_page_preview: true`. Returns ok boolean, never throws.
  - `getPost(env, slug)`: `{title, path}` or null, from `/slugs.json` via `env.ASSETS`, cached per isolate.
  - `escapeHtml(text)`.
- Tests: `vitest` with `@cloudflare/vitest-pool-workers`, migrations applied per test file, `fetchMock` for siteverify and Telegram. Test files live in `test/` at the repo root (`test/<area>.test.ts`); `npm test` from the repo root runs all of them, including the cron Worker's. If these devDependencies conflict with Astro's Vite during `npm ci`, the tooling may move into its own `package.json`, but root `npm test` must still run every test (it delegates).
- Test helpers in `test/helpers.ts`: `makeEnv(overrides)` (test D1, an `ASSETS` stub serving a fixture `slugs.json` with two posts, test secrets, Telegram secrets set) and `callFunction(handler, {method, url, headers, body, params}, env)` that builds the Pages `EventContext` and returns the `Response`. Sections 5, 6 and 8 use these and never build their own. `workerd` runs on the dev host (verified 2026-11-19).
- `.github/workflows/ci.yml`: on PR and push to main: `npm ci`, `npm run build`, `npm run check-links`, `npm test`. Node from `.node-version`.
- Done: `npm test` green locally and in CI; `dist/slugs.json` lists all posts; production deploy of `main` after merge is green. No endpoint files in this section.

## 5. Reader API: read, heart, comment

Pages Functions for the three reader endpoints of parent section 9. Frozen contract, built against by section 7:

- `GET /api/posts/:slug`: 404 `{"error":"unknown_slug"}` if `getPost` is null. Else `{"hearts": n, "comments": [{"id","name","body","created_at"}]}`, approved only, oldest first, `Cache-Control: no-store`.
- `POST /api/posts/:slug/react`: JSON `{"token"}`. Order: slug (404), Turnstile (403 `turnstile`), `INSERT OR IGNORE` into `reactions` with `visitor = visitorHash(...)`. Response 200 `{"hearts": n, "already": true|false}`.
- `POST /api/posts/:slug/comments`: JSON `{"name"?, "body", "token"}`. Validation (400 `invalid_name` / `invalid_body`): name trimmed, empty becomes `"Anonymous"`, at most 40 chars; body trimmed, 1-2000 chars; control characters other than newline stripped; stored as plain text. Turnstile (403 `turnstile`). A comment from the same `ip_hash` in the last 60 s: 429 `rate`. Insert with `id = crypto.randomUUID()`, `status='pending'`, `ip_hash = ipHash(...)`. Then `sendTelegram`: `New comment on "<title>"`, name, body (first 500 chars), `Review: <reviewUrl>`. Telegram failure never fails the request. Response 202 `{"status":"pending"}`.
- Body over 8 KB: 413. Wrong method: 405. Non-JSON body: 400 `invalid_json`.
- Done: tests cover every status above, Turnstile mocked success, failure and wrong hostname, the 60 s window, a second heart the same day (`already: true`) and the next day (counted), and the Telegram text carries a link `verifyReview` accepts.

## 6. Moderation: confirm page and decision

- `GET /moderate/:id?exp=&sig=`: `verifyReview` false: 403 HTML "Link invalid or expired". Unknown id: 404 HTML. Else a minimal self-contained HTML page (`Cache-Control: no-store`, `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`): post title linked to its path, name, body (`escapeHtml`), status. If `pending`: two forms, `method=post action=/api/moderate/:id`, hidden `exp`, `sig`, `action=approve|reject`. If decided: the recorded decision and time, no forms. Never writes.
- `POST /api/moderate/:id`: form-encoded. `Origin` must equal `SITE_ORIGIN` exactly (403; missing Origin is 403). `verifyReview` (403). `action` in `approve|reject` (400). `UPDATE comments SET status=?, decided_at=? WHERE id=? AND status='pending'`; zero changed rows renders "Already decided: <status>". Success renders "Approved" or "Rejected" with a link to the post. HTML responses, 200.
- Check that `functions/moderate/` does not shadow an Astro route (`dist/moderate/` must not exist).
- Done: tests cover bad sig, expired, wrong and missing Origin, bad action (400), unknown id (404), success text, double POST (second says already decided), GET on a pending comment leaves it pending, `<script>` in name and body escaped; `test ! -e dist/moderate` after build.

## 7. Post page UI and privacy text

Static Astro plus a small inline client script. No framework, no new runtime dependency. Built against section 5's contract; starts after section 5 merges so the screenshots run against the real endpoints.

- `src/components/Engage.astro`, included in `src/pages/[year]/[month]/[day]/[slug].astro` after the body and before the prev/next nav, prop `slug`.
- Heart: button with count. On load, GET `/api/posts/:slug`, show count and approved comments. `localStorage` key `heart:<slug>` holds the UTC date hearted; if today, the button shows hearted and is disabled. Click runs Turnstile, POSTs react, updates the count, sets the key.
- Comments: list (name, date, body set with `textContent`, never `innerHTML`; no auto-linking), then a form: name (optional, maxlength 40), body (required, maxlength 2000), Turnstile widget, submit. 202: clear the form, show "Thanks, your comment shows after review." 400/403/429: show a message, keep the text. Reset the Turnstile widget after every submit (tokens are single-use).
- Turnstile script `https://challenges.cloudflare.com/turnstile/v0/api.js` loads only when the reader focuses the form or clicks the heart: no third-party request before interaction. Site key from `PUBLIC_TURNSTILE_SITEKEY` at build time; when unset the component renders nothing and makes no request.
- API error or unreachable: the section hides itself; the post still reads.
- Local run for screenshots: `npx wrangler pages dev dist` with `.dev.vars` (never committed) holding the Turnstile test secret and `SITE_ORIGIN=http://localhost:8788`, build with the test site key, local D1 migrated and seeded with one approved comment and a few hearts.
- `src/pages/privacy.astro`: drop "(when they launch)" and "(with comments and reactions)"; keep the existing 30-day purge sentence; state that the IP hash is kept with the comment; add the contact link for delete requests that parent section 11 requires (ask Neo for the address or channel; never invent one).
- Done: `npm run build` and `npm run check-links` green; PR has screenshots of a post with the section rendered against a local API (`npx wrangler pages dev dist`) at desktop and 390 px width, and one with `PUBLIC_TURNSTILE_SITEKEY` unset showing no section.

## 8. Cron Worker and build-failure alert

Reads `docs/engage-setup.md` (section 3) for R2 yes or no and alert path `webhook` or `poll`, and builds only what it says.

- `worker/` with its own `package.json` and `wrangler.toml` (`name = "painforwisdom-engage-cron"`, `main = "src/index.ts"`), `DB` bound to the production D1 id from section 3, `BACKUP` to R2 bucket `painforwisdom-engage-backup` only if R2 is yes. Imports helpers from `../engage/`. `fetch` handler returns 404.
- Daily `"17 3 * * *"`, purge: `DELETE FROM comments WHERE (status='rejected' AND decided_at < ?) OR (status='pending' AND created_at < ?)`, both now minus 30 days.
- Weekly `"23 4 * * 0"`, only if R2: all rows of `comments` and `reactions` as `backup/YYYY-MM-DD.json` `{"comments":[...],"reactions":[...]}`; keep the latest 8 objects. Keep it to a few statements and one put (10 ms CPU limit).
- Alert path `webhook`: `functions/api/hooks/pages.ts`, `POST /api/hooks/pages`. Header `cf-webhook-auth` must equal `PAGES_HOOK_SECRET` (constant-time; 401 otherwise). Send Telegram `Blog build failed`, plus project, environment, deployment id or URL, and the alert `text` from the body; unknown shape still sends the raw `text`. Always 200 after auth (no retry storm).
- Alert path `poll`: third cron `"*/30 * * * *"` calling `GET https://api.cloudflare.com/client/v4/accounts/<CF_ACCOUNT_ID>/pages/projects/painforwisdom-site/deployments` with Worker secret `CF_API_TOKEN` and var `CF_ACCOUNT_ID`; alert once for each deployment whose `latest_stage.status` is `failure` and whose `created_on` is newer than the last alerted one, kept in a one-row table from `migrations/0002_alert_state.sql`. The first run with an empty table records the newest deployment as the baseline and sends nothing, so history never alerts. The Worker needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets in this path.
- Tests in the vitest setup, scheduled handler driven directly: purge boundaries (29 vs 31 days, approved never purged), backup shape and retention if built, the alert path built (good, bad and missing secret for webhook; one alert per failed deployment for poll).
- Deploying the Worker is a user step in section 9, not here.

## 9. Go-live: deploy, wire, verify live

Code is merged; this applies, deploys, wires and proves. Config and docs only. User steps, each as an exact command:

1. `npx wrangler d1 migrations apply painforwisdom-engage --remote` (and the preview DB).
2. `cd worker && npx wrangler deploy`, then `npx wrangler secret put` for what section 8 needs (`TELEGRAM_*`, `CF_API_TOKEN` on the poll path).
3. Alert path `webhook`: Notifications, Pages "Project updates", project `painforwisdom-site`, event deployment failed, destination `https://painforwisdom-site.pages.dev/api/hooks/pages` with secret `PAGES_HOOK_SECRET`.
4. Retry the production deployment of `main` so Functions pick up secrets.

Live checks on `https://painforwisdom-site.pages.dev`, evidence (curl output, screenshots, Telegram screenshots) in the PR:
- `GET /api/posts/<a migrated slug>` returns JSON. A heart from a browser increments and stays hearted after reload.
- A test comment arrives in Telegram with a Review link; after Telegram's preview fetched it, the comment is still `pending` in D1. Approve on the confirm page; the comment shows on the post after reload; a second Approve says already decided. A second test comment rejected. Both deleted from D1 afterwards.
- A deliberately failing preview build (a branch with a broken front-matter post, deleted afterwards) produces the Telegram alert. Production is never broken for this test.
- A manual cron trigger (dashboard "Trigger cron") shows a successful run.
- `OPERATIONS.md` section 9 (monorepo, own PR) and the site `README.md`: secrets list, rotation, reading the queue, deleting a comment on request (`npx wrangler d1 execute painforwisdom-engage --remote --command "DELETE FROM comments WHERE id='...'"`).
- Done only when every check passes live. A pending user step keeps the order open (`jarvis wo ask`); it never completes with steps pending.

## Agent profile

You are a worker building the comments, hearts, moderation and build-alert layer of the Pain for Wisdom blog. The blog is a static Astro 7 site in the separate private repo `ironforce365/painforwisdom-site`, served by Cloudflare Pages at `https://painforwisdom-site.pages.dev`. Your Jarvis worktree is in the monorepo `painforwisdom`; the code you write lives in the site repo. Clone it (`gh repo clone ironforce365/painforwisdom-site`) into your job tmp dir, branch `<your work-order id>-<short-slug>`, push, and open the PR there with `gh pr create --repo ironforce365/painforwisdom-site`. Your spec section is the contract; build exactly that section.

What you must know:
- Architecture (spec section 2): HTTP endpoints are Pages Functions under `functions/`, crons are a separate Worker under `worker/`, data is one D1 database bound as `DB` (a separate preview database for branch previews). No custom domain, so no zone features: no WAF rules, no Worker routes.
- Shared helpers live in `engage/` at the repo root (section 4). Import them; never re-implement hashing, signing, Turnstile, Telegram or escaping in your own file. If a helper is missing or wrong, ask Neo before changing `engage/`.
- `docs/engage-setup.md` in the site repo records the D1 ids, Turnstile site key, secret names, whether R2 is enabled and whether the build alert is `webhook` or `poll`. Read it; never guess those.
- Local build on this host: the Xeon has no AVX2, so Astro's native compiler binding dies with "Illegal instruction". Move `node_modules/@astrojs/compiler-binding-linux-x64-*` aside, `npm i --no-save --force @astrojs/compiler-binding-wasm32-wasi`, build with `NAPI_RS_FORCE_WASI=1` under Node 24 (`npx -y node@24`). `workerd` (wrangler, vitest-pool-workers) does run here. CI and Cloudflare builders need none of this; never commit the workaround.
- Before a PR: `npm test`, `npm run build`, `npm run check-links`, all green. CI runs the same.
- Telegram: Bot API `sendMessage` only, plain text, `disable_web_page_preview: true`. Never set a webhook or call `getUpdates` on the token: the coach bot polls the same token's updates and would break.

Rules:
- No GET request changes state. Every state change is a POST that re-checks its preconditions.
- Reader text (name, body) is untrusted: `escapeHtml` in server HTML, `textContent` in the browser, plain text to Telegram. Never `innerHTML`, never auto-link.
- Fail closed on Turnstile and signatures; fail open on Telegram (a lost alert never loses a comment).
- No secret values in either repo, in PR bodies or in `jarvis` messages. Secrets are Pages or Worker secrets the user sets. `wrangler.toml` holds binding names, D1 ids and public vars only.
- Cloudflare account actions (`wrangler login`, `d1 create`, `migrations apply --remote`, `secret put`, `deploy`, dashboard clicks) are user steps. Give the user the exact command, ask with `jarvis wo ask`, and confirm the effect yourself (curl, deployment list) before calling a step done. Never complete with a user step pending.
- A merge to the site repo's `main` deploys production. Never merge a change whose Pages build would fail; never push to `main` of either repo; merging is gated (`jarvis gate request`).
- No new runtime dependency on the site. Dev dependencies only, and they must not break `npm ci` on Pages.
- A UI change ships with screenshots in the PR (desktop and 390 px). Passing tests are not evidence of how a page looks.
- Stay inside your section. If another section's interface is wrong, ask Neo; never change it in your PR.

Traps the planner already found:
- Parent spec section 6 names `gonandrap/painforwisdom-site`; the real repo is `ironforce365/painforwisdom-site`.
- A `wrangler.toml` naming a D1 id that does not exist fails the Pages deploy. That is why section 3 creates the databases first.
- With `pages_build_output_dir` set, `wrangler.toml` is the source of truth for bindings and vars; dashboard bindings of the same name are ignored. Secrets still come from the dashboard and reach Functions only after a redeploy.
- Files under `functions/` become routes. Shared code goes in `engage/`, never in `functions/`.
- Turnstile tokens are single-use: reset the widget after each submit; the server checks `hostname` in the siteverify response.
- `CF-Connecting-IP` is absent under local test runners; `clientIp` defaults it.
- Free plan cron CPU limit is 10 ms; keep cron work to a few D1 statements and one R2 put.
- Telegram link-preview bots GET every URL in a message. That is why the review link only renders a page.
