# Own blog: static site design

Source: decision do-a6b948a0 (option a). Follow-up orders: wo-87d23f57 (migration + skeleton), wo-42d93cb8 (pipeline `blog` target). Engagement (section 9) is a later order.

## 1. Problem

- Blog has had no post since 2026-04-16: pipeline publish to WordPress never ran. Root cause: WordPress.com free plan blocks REST writes (`pipeline/wordpress_client.py:13`; `OPERATIONS.md:356` "a publish on it ends failed with a 403"); `config/publishing.json` sets `wordpress.mode=manual`.
- Paying ($48–108/yr, do-a6b948a0 f3) fixes publishing only. Likes and comments stay behind a WordPress.com account, the user's main complaint, and the site cannot carry a shared ecosystem nav.
- Audience to lose is small: 56 posts, 184 likes, 0 comments, 11 subscribers (do-a6b948a0 f1).

## 2. Fix

Move the blog off WordPress to a static site we own: markdown in git, built by Astro, served by Cloudflare Pages. The pipeline publishes by git push, so no write API or paid plan gates it. One Cloudflare Worker adds anonymous reactions and moderated comments. Root cause is removed, not worked around: the publish path no longer depends on a third-party plan.

```
pipeline (monorepo, public)            painforwisdom-site (separate repo, public)
  publish target `blog`  --git push-->   src/content/posts/YYYY-MM-DD-slug.md + image
                                              |
                                     Cloudflare Pages build (Astro)  -->  static HTML on CDN
                                              |
reader browser  --fetch-->  Worker `engage` (/api/*) + D1  -->  Telegram moderation alert
```

No server of ours faces the internet. The only dynamic piece is one Worker.

## 3. Generator: Astro

- Astro over Hugo. Content collections give typed front-matter checked at build; islands fit the two JS widgets (reaction button, comments) without shipping JS to the rest of the page.
- Hugo rejected: faster builds, but Go templates and no component model; build speed is irrelevant at 56 posts.
- Pin Astro major in `package.json`; Dependabot on the site repo for updates.

Front-matter (the contract the `blog` target writes, wo-42d93cb8):

```yaml
title: "The obstacle is the way"
date: 2026-04-16          # run date; drives the URL
slug: the-obstacle-is-the-way
excerpt: "..."
themes: [discipline, heat] # kb_curator themes, become theme pages
youtube_url: https://youtube.com/shorts/...   # optional
image: ./the-obstacle-is-the-way.jpg          # featured image, beside the .md
wordpress_url: https://painforwisdom.wordpress.com/2026/04/16/the-obstacle-is-the-way/  # migrated posts only
```

## 4. Hosting: Cloudflare Pages

- Free tier: unlimited static bandwidth and requests, 500 builds/mo, 20 min build timeout (do-a6b948a0 f3). One post per run is far below the build cap.
- Build: `npm ci && npm run build`, output `dist/`. Production branch `main`; preview deploys on other branches.
- Build failure alert: Cloudflare Notifications webhook for Pages deploy failure, into the Worker (section 9), relayed to Telegram.
- Not hosted on the coach box: it has no public ingress (`services/coach/docker-compose.yml:190` binds 127.0.0.1) and must stay that way.

## 5. Domain

Recommendation only; the user picks and buys. This order buys nothing.

- Preferred: `painforwisdom.com`. Fallbacks: `painforwisdom.blog`, `painforwisdom.run`.
- Register at Cloudflare Registrar (at-cost pricing; DNS sits where Pages and the Worker live).
- Until bought, the site runs on `<project>.pages.dev`; every absolute URL comes from the one `site` setting in `astro.config.mjs`.
- Worker routes under `/api/*` on the same host; no `api.` subdomain.

## 6. Site repo and URLs

- Repo `gonandrap/painforwisdom-site`, public, separate from the monorepo. Holds only published content, images and site code: no `.env`, no vault text, nothing unpublished.
- Pipeline pushes with a deploy key scoped to that one repo (write). The key lives in the pipeline `.env`, never in either repo.
- URL scheme keeps WordPress paths so the writer's post-to-post back-references resolve (`pipeline/backfill_wordpress.py:1-30`): `/YYYY/MM/DD/slug/`, trailing slash.

| Path | Page |
|---|---|
| `/` | home |
| `/YYYY/MM/DD/slug/` | post |
| `/themes/<theme>/` | posts by theme |
| `/start/` | ecosystem links hub |
| `/privacy/` | privacy notice |
| `/rss.xml` | feed, full text |

- Old WordPress site stays up with a pinned "we moved" post linking the new domain; each migrated post sets `<link rel="canonical">` to itself on the new site. Paid WP Site Redirect not bought (price unverified, traffic low).

## 7. Ecosystem bar and `/start`

One bar, same on every property that can render HTML; where it cannot (YouTube, Telegram), a `/start` link stands in.

- Bar items, only properties public today: Blog, Videos (`youtube.com/@painforwisdom`), Coach (Telegram bot link), Start. Knowledge and Stats appear when they exist (KB repo private, no Garmin code: do-a6b948a0 f5). The bar reads `src/data/ecosystem.json`, so adding one is a data change.
- Coach bot is allowlisted (`services/coach/access.json`); its `/start` card says "invite only" until that changes.
- `/start` is the single map: one card per property with a one-line purpose. Linked from the YouTube channel description, each Short description, the Telegram bot's `/start` reply and the blog footer.
- Every cross-link carries UTM: `utm_source=<blog|youtube|telegram>&utm_medium=ecosystem&utm_campaign=<bar|start|post>`.

## 8. Wireframes (do-a6b948a0 f7)

HOME

```
+--------------------------------------------------------------+
| PAIN FOR WISDOM   Blog | Videos | Coach | Knowledge | Stats  |  <- shared ecosystem bar
+--------------------------------------------------------------+
| [latest short embedded]   'The obstacle is the way'  04/16/26 |
|                           excerpt...        [Read ->]         |
+--------------------------------------------------------------+
| Themes: [discipline] [heat] [failure] [recovery] ...         |
| [card][card][card]   <- posts grid, image + title + date     |
| [card][card][card]                                            |
+--------------------------------------------------------------+
| Subscribe: [email____] [Join]   RSS   YouTube   Telegram coach |
+--------------------------------------------------------------+
```

POST

```
| ecosystem bar                                                 |
| Title / date / 4 min read / themes                           |
| [featured image]  or  [YouTube short, 9:16, side column]     |
| body...                                                      |
| [heart] 12  'This helped'  (no login)                         |
| Run that day: 21 km, 2:05 h, 31C   <- future Garmin card      |
| Go deeper: KB theme 'heat' | research reading list           |
| Comments (anonymous, moderated)                               |
| Prev / Next  (chronological, matches writer's back-refs)      |
```

LINKS HUB (`/start`): one card per property, so any landing point sends people to the same map.

- Knowledge, Stats, the Garmin card and "Go deeper" render only when their data exists (section 7).
- YouTube embed uses `youtube-nocookie.com` behind a click-to-load facade: no YouTube request before the reader asks.
- Email subscribe box: deferred (section 13); RSS ships first. The 11 WP subscribers get the "we moved" post.

## 9. Comments, reactions, moderation: Worker `engage` + D1

One Worker, one D1 database, in the site repo under `worker/`.

- Remark42 and Isso rejected: both need a public container, and we have no public host (section 4).
- Giscus rejected: needs a GitHub login, the complaint being fixed.

| Method | Path | Does |
|---|---|---|
| GET | `/api/posts/:slug` | reaction count + approved comments |
| POST | `/api/posts/:slug/react` | +1 heart; Turnstile token required |
| POST | `/api/posts/:slug/comments` | new comment, status `pending`; Turnstile token required |
| GET | `/moderate/:id?exp=&sig=` | confirm page: shows the comment and two forms, Approve and Reject; changes nothing |
| POST | `/api/moderate/:id` | form body `action`, `exp`, `sig`; applies the decision once |
| POST | `/api/hooks/pages` | Pages build-failure webhook, relayed to Telegram; shared-secret header required |

```sql
CREATE TABLE reactions (slug TEXT NOT NULL, visitor TEXT NOT NULL, created_at TEXT NOT NULL,
                        PRIMARY KEY (slug, visitor));
CREATE TABLE comments (id TEXT PRIMARY KEY, slug TEXT NOT NULL, name TEXT NOT NULL,
                       body TEXT NOT NULL,
                       status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
                       ip_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                       decided_at TEXT);
```

- **No login, ever.** Name is free text (default "Anonymous"); no email field, so no email PII.
- **Turnstile** (free, no reader account) on react and comment; Worker verifies the token server-side with `siteverify` before any write.
- **One heart per visitor per post per day**: `visitor = sha256(ip + user-agent + daily salt)`; salt rotates daily, so no tracking across days, and a returning reader can heart again the next day. The button also stores a `localStorage` flag per post, so the same browser shows it already hearted. Count is a signal, not an audit.
- **Rate limit**: Cloudflare rate-limiting rule on `/api/*` POSTs, 5/min per IP; Worker also rejects a second comment from the same `ip_hash` within 60 s.
- **Validation**: slug must exist in the build's post list (Worker reads a `slugs.json` the build emits); body 1–2000 chars, name ≤ 40, plain text; rendered escaped, links not auto-linked.
- **Moderation by default**: every comment starts `pending`, invisible until approved.
- **Telegram alert** per new comment: post title, name, body, and one "Review" link to the confirm page. The link carries `exp` (7 days) and `sig = HMAC-SHA256(id + exp, MODERATION_SECRET)`. Worker calls the Bot API directly (`sendMessage`, `disable_web_page_preview: true`) with Worker secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, same chat the pipeline uses (`pipeline/telegram.py`).
- **No GET changes state.** Link-preview fetchers (Telegram and others) issue GETs to URLs in messages; the GET only renders the confirm page. The decision is the form POST, which re-checks `sig` and `exp` and requires `Origin` to be the site host.
- Inline-keyboard buttons with `callback_data` rejected: callbacks arrive through the bot's update stream, and a bot that has a webhook set cannot also be polled. The coach bot polls (`services/coach/telegram_bot/bot.py:476`), so a webhook on a shared token would break it, and a second bot just for moderation adds a token and a chat to manage.
- **Single-use**: the POST runs `UPDATE comments SET status=?, decided_at=? WHERE id=? AND status='pending'`; zero rows changed means already decided, and the page says so without changing anything. A second Review link click shows the recorded decision.
- Approved comments show on next page load (client fetch); no rebuild.
- **Daily Worker cron**: deletes `rejected` comments whose `decided_at` is older than 30 days, and `pending` ones older than 30 days (link expired, never decided). Backs the privacy promise in section 11.
- Backup: weekly Worker cron exports D1 to R2. Posts are in git already.

## 10. Analytics: Cloudflare Web Analytics

- Cloudflare Web Analytics: free, cookieless, no banner; pageviews, referrers, countries, Core Web Vitals.
- Ecosystem funnel: UTM on every cross-link (section 7); YouTube Studio shows external referrals the other way.
- Upgrade path, only if outbound-click events become needed: GoatCounter (free for personal use, cookieless, custom events). Umami rejected: needs a server plus Postgres.
- Hearts and comments come from D1; console dashboards (`docs/superpowers/specs/2026-11-06-console-dashboards-design.md`) can read them later via `GET /api/posts/:slug`.

## 11. Privacy notice (`/privacy/`)

States, in plain words:

- Analytics: Cloudflare Web Analytics, no cookies, no personal profile.
- Reactions: daily-rotating hash of IP and browser, used only to count one heart per visitor per day.
- Comments: the name and text you submit, plus a hash of your IP for spam control; stored in Cloudflare D1; deleted on request (contact link); rejected and never-reviewed comments purged after 30 days (daily job, section 9).
- Turnstile: Cloudflare checks the browser is not a bot; link to its privacy terms.
- YouTube embeds load only on click.
- No ads, no selling, no email collected.

GDPR basis: legitimate interest for spam control; 30-day purge and delete-on-request cover it at this scale.

## 12. Delivery order

1. wo-87d23f57: site skeleton (Astro, layouts, bar, `/start`, `/privacy/`, RSS, Web Analytics) + migration of the 56 posts with images and canonical URLs.
2. wo-42d93cb8: pipeline `blog` target added to `TARGETS` in `pipeline/publish/__init__.py:27`, writing section 3's front-matter and pushing with the deploy key.
3. New order: Worker `engage` + D1 + Turnstile + Telegram moderation (section 9).
4. User: buy the domain, point it at Pages; until then everything runs on `pages.dev`.

## 13. Not decided here

- Email subscribe provider (Buttondown or a Worker list).
- Public KB pages and Garmin card: separate decisions (do-a6b948a0 f5).
- Whether WordPress likes were real readers (experiment from do-a6b948a0).
