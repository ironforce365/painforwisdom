# Console dashboards

Asked for on 2026-11-06, with mockups reviewed in the browser. Extends `2026-11-05-console-end-to-end.md`, whose decision tree gains the leaves named here.

## Why

The console works, but it reads as a debugging tool. The things you need first are small and easy to miss: the "needs you" bar, the Start button, and one row of text about the selected run. The left panel has no title. Nothing shows what a run produced, or what the videos have been about.

## Goals

1. A dashboard that is the home page and where all navigation starts: the last runs, what needs you, the latest themes, and the commands to start a run, open a past run and delete one.
2. Past runs in a titled section, not an unlabelled left panel.
3. "Needs you" large and on the dashboard, each item going straight to the problem.
4. A large, obvious Start button.
5. Dismiss that doesn't turn into a second button. The current "Back to needs-you" is the same toggle relabelled, and reads as a new action.
6. A run page that shows the numbers, the outputs and the color-coded graph, each leading to more detail.

## Non-goals

- **Agent debug view with Jarvis data.** Out of scope by request. The pipeline's stages are API calls, not Claude sessions, so `jarvis wo show` has nothing to read for them. The preferred direction is real sessions, decided separately.
- **Moving Docker to the external disk.** The pipeline doesn't use the coach; that's a separate job.
- Changing what a run does. The pipeline's nodes are untouched.

## Anti-goals

- A delete that quietly destroys something outside the run, or the only copy of an input.
- A dashboard that has to call Notion or YouTube to render.

## Pages and routes

| Route | Page |
|---|---|
| `#/` (or no hash) | Dashboard |
| `#/runs/<id>` | Run page, Overview tab (the `graph` view) |
| `#/runs/<id>/output`, `/logs` | Run page, other tabs |
| `#/knowledge`, `#/knowledge/<slug>` | Knowledge base |

`body[data-page]` holds `home`, `run` or `knowledge`, and CSS shows one page. The run list no longer selects a run by itself: with no run in the route you're on the dashboard. Leaving a run page stops polling that run.

The header is on every page: logo, version, a "Needs you (n)" link back to the dashboard (hidden there and when n is 0), and the large Start button. So Start is reachable everywhere and the launch popup doesn't move.

## Dashboard

From the top:

- **Needs you**, an amber block listing each run wanting something, most urgent first, with **Open** and **Dismiss**. When empty it's a quiet "Nothing needs you". A **Dismissed (n)** line expands to the dismissed items, each with **Restore**.
- **Tiles**: runs and how they ended, time of the last good run, total API-equivalent cost, knowledge base entries and themes.
- **Recent runs** (titled): every run, newest first, active ones on top. Each row has status, input, time, tokens, cost, **Open** and **Delete**.
- **Latest themes in your videos**: the three newest knowledge base entries with their themes, and a link to the knowledge base.

## Dismiss

Dismiss takes the item off the list and shows a short "Dismissed · Undo" notice, on the dashboard and on the run page. The run page no longer offers "Back to needs-you"; a dismissed package banner just goes away. Restoring later is on the dashboard.

## Delete

`DELETE /api/runs/<id>` removes `processed/<id>/` (the run's folder, including its preview copy) and the run's rows in the checkpoint database. The input stays in the inbox, so the same file can run again without the "Already ran" warning.

It doesn't touch the knowledge base entry, Notion pages or anything published. The confirmation says so, in place, with no browser dialog.

**Known limit:** the knowledge base entry stays, so the curator can still see the video as already processed and stop a re-run with a question (leaf A5). Removing the entry too is a separate decision: the vault is a shared git submodule.

Refused with the reason:

- the run is active;
- a publish is in progress;
- a preview is converting;
- the run has a `source/` folder, which can be the only copy of its input. Runs from the console don't; batch runs from the command line do.

## Run page

Top to bottom: a back link and the status, input and date, then the toolbar (**Restart with same input** for a finished run, **Delete run**), the existing action banner, **totals** (time, tokens, cost, model calls), the **graph**, then the **output cards**, then the existing metrics bar.

- Clicking a total opens the per-step metrics table.
- Clicking a graph step opens its inspector, as today.
- Clicking a card opens that section of the Output tab. External links (YouTube, Notion) open directly.

The cards are YouTube, WordPress, Blog draft and Knowledge base entry. Each shows its state and title, or says there's nothing yet. Cards load from `publish` and `result`; a failed Notion read costs only the Notion link.

**Graph:** rows chosen to keep long edges clear of the boxes, as reviewed in the mockup, falling back to the computed layout for a step nobody placed. Boxes in place of circles, the label and time inside, and plain step names ("Write blog", not `writer`). A package waiting for you paints its step amber with "needs you"; a package published by hand reads "published" in green instead of "skipped". The page can now scroll, so a plain wheel scrolls it, and zooming takes Ctrl or ⌘.

## Knowledge base

`GET /api/knowledge` parses `<vault>/gonzalo-book/entries/*.md`: title from the heading, date and themes from the `**Date:**` and `**Themes:**` lines. It returns entries newest first, theme counts, and totals. `GET /api/knowledge/<slug>` returns one entry's markdown. A page lists every entry and shows the theme counts; clicking an entry renders it.

The vault comes from `PIPELINE_UI_VAULT`, else `VAULT_PATH` in the profile's env file, else `runtime.VAULT_PATH`. It's read only. A missing vault gives an empty list, not an error.

## API changes

- `GET /api/inputs`: unchanged.
- `GET /api/runs`: items add `dismissed` and `dismissed_attention`, what the run would ask for if it weren't dismissed.
- `GET /api/runs/<id>`: adds `packages` (`[{target, status}]`, every status), and `manage`: `{delete: reason|null, rerun: reason|null}`.
- `DELETE /api/runs/<id>`, `GET /api/knowledge`, `GET /api/knowledge/<slug>`: new.
- `GET /api/graph`: labels become plain step names.

## Decision tree additions

```
S7 dashboard is home; every page leads back to it
S8 no runs yet ─▶ dashboard says so and offers Start
N6 dismiss ─▶ item leaves, Undo offered
N7 restore a dismissed item from the dashboard
D7 run page: totals, graph, output cards, each leading to details
D8 delete a run ─▶ gone; the input can run again
D9 delete refused ─▶ reason shown
D10 latest themes and the knowledge base
```

## Left to the builder

Exact styling, responsive breakpoints and the tile wording, within the reviewed mockups.
