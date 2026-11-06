// Dashboard, run page and knowledge base (spec 2026-11-06-console-dashboards).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const id = (page, testid) => page.getByTestId(testid);
const onHome = (page) => expect(page.locator('body')).toHaveAttribute('data-page', 'home');
const onRun = (page) => expect(page.locator('body')).toHaveAttribute('data-page', 'run');

function setRecord(runId, fields) {
  const file = path.join(PROCESSED, runId, 'langgraph', 'run.json');
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, 'utf8')), ...fields }));
}

const packageOf = (runId, dir) => JSON.parse(fs.readFileSync(path.join(PROCESSED, runId, 'langgraph', dir, 'package.json'), 'utf8'));
const mockPackages = (page, runId, targets, prepare = {}) => page.route(`**/api/runs/${runId}/publish`, (route) => route.fulfill({ json: { targets, prepare } }));

test.beforeEach(() => freshServerState());

test('[DT-S7] the console opens on the dashboard, runs open from it, and every page leads back', async ({ page }) => {
  await page.goto('/');
  await onHome(page);
  await expect(id(page, 'dashboard')).toBeVisible();
  await expect(id(page, 'dag')).toBeHidden();
  await expect(id(page, 'launch-button')).toHaveText('Start a new run');
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();

  await id(page, 'run-fx-done').click();
  await onRun(page);
  await expect(page).toHaveURL(/#\/runs\/fx-done$/);
  await expect(id(page, 'dashboard')).toBeHidden();
  await expect(id(page, 'dag')).toBeVisible();
  await expect(id(page, 'run-list')).toBeHidden();

  await page.goBack(); // the browser's Back undoes opening a run
  await onHome(page);

  await id(page, 'run-fx-done').click();
  await id(page, 'run-back').click();
  await onHome(page);
  await expect(page).toHaveURL(/#\/$/);

  await page.goto('/#/runs/fx-done/logs');
  await onRun(page);
  await id(page, 'home-link').click();
  await onHome(page);
  await id(page, 'run-fx-done').click(); // a run always opens on its overview
  await expect(page.locator('body')).toHaveAttribute('data-view', 'graph');
});

test('[DT-S7] leaving a run stops asking about it', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  // The Start button watches one active run by itself: follow a different one.
  const watched = (await id(page, 'launch-active-link').textContent()).replace('Go to ', '');
  const other = ['fx-running', 'fx-hitl'].find((name) => name !== watched);
  const asked = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === `/api/runs/${other}`) asked.push(r.url());
  });
  await page.goto(`/#/runs/${other}`);
  await expect.poll(() => asked.length).toBeGreaterThan(1);
  await id(page, 'run-back').click();
  await onHome(page);
  const after = asked.length;
  await page.waitForTimeout(1500);
  expect(asked.length).toBe(after);
});

test('[DT-S7] the header points back to what needs you, except on the dashboard', async ({ page }) => {
  await page.goto('/');
  await expect(id(page, 'needs-you-count')).not.toHaveText('0');
  await expect(id(page, 'needs-link')).toBeHidden();
  await id(page, 'run-fx-done').click();
  await expect(id(page, 'needs-link')).toBeVisible();
  await expect(id(page, 'needs-link-count')).toHaveText(await id(page, 'needs-you-count').textContent());
  await id(page, 'needs-link').click();
  await onHome(page);
});

test('[DT-S8] with no runs the dashboard says so, and Start is right there', async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(id(page, 'runs-empty')).toBeVisible();
  await expect(id(page, 'needs-none')).toBeVisible();
  await expect(id(page, 'tile-runs')).toHaveText('0');
  await expect(id(page, 'tile-last')).toHaveText('–');
  await expect(id(page, 'needs-link')).toBeHidden();
  await expect(id(page, 'launch-button')).toBeEnabled();
});

test('[DT-S7] tiles sum the runs', async ({ page }) => {
  const run = (over) => ({ run_id: 'a', status: 'done', verdict: 'PASS', input: { kind: 'video', name: 'a.mp4' }, total_s: 90, started_at: 2, cost_usd: 1.5, cost_estimated: false, tokens: 10, attention: [], dismissed: false, dismissed_attention: [], ...over });
  await page.route('**/api/runs', (route) => route.fulfill({
    json: { runs: [run({}), run({ run_id: 'b', status: 'failed', verdict: null, cost_usd: 0.5, cost_estimated: true }), run({ run_id: 'c', cost_usd: null, started_at: 1 })] },
  }));
  await page.goto('/');
  await expect(id(page, 'tile-runs')).toHaveText('3');
  await expect(id(page, 'tile-runs-note')).toHaveText('runs · 2 passed');
  await expect(id(page, 'tile-last')).toHaveText('1m 30s');
  await expect(id(page, 'tile-cost')).toContainText('$2.00');
});

test('[DT-N6] dismiss takes the item away and offers undo; undo brings it back', async ({ page }) => {
  await page.goto('/');
  const item = id(page, 'needs-you-item-fx-failed');
  await expect(item).toBeVisible();
  await id(page, 'needs-dismiss-fx-failed').click();
  await expect(item).toBeHidden();
  await expect(id(page, 'needs-notice')).toBeVisible();
  await expect(id(page, 'needs-notice')).toContainText('transcript_2026-10-30.txt');
  await expect(id(page, 'needs-dismissed-toggle')).toBeVisible();
  await id(page, 'needs-undo').click();
  await expect(item).toBeVisible();
  await expect(id(page, 'needs-notice')).toBeHidden();
});

test('[DT-N6] the undo notice goes away on its own', async ({ page }) => {
  await page.clock.install();
  await page.goto('/');
  await id(page, 'needs-dismiss-fx-failed').click();
  await expect(id(page, 'needs-notice')).toBeVisible();
  await page.clock.runFor(9000);
  await expect(id(page, 'needs-notice')).toBeHidden();
});

test('[DT-N6] a dismiss the server refuses says why and keeps the item', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  await page.route('**/api/runs/fx-failed/dismiss', (route) => route.fulfill({ status: 500, json: { detail: 'disk full' } }));
  await page.goto('/');
  await id(page, 'needs-dismiss-fx-failed').click();
  await expect(id(page, 'needs-error')).toContainText('disk full');
  await expect(id(page, 'needs-you-item-fx-failed')).toBeVisible();
});

test('[DT-N6] a run page dismiss leaves no "back to needs-you" behind', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await id(page, 'action-dismiss').click();
  await expect(id(page, 'needs-notice')).toBeVisible();
  await expect(id(page, 'action-banner')).toBeHidden();
  await expect(page.getByText('Back to needs-you')).toHaveCount(0);
  await id(page, 'needs-undo').click();
  await expect(id(page, 'action-banner')).toBeVisible();
});

test('[DT-N6] a failed run keeps its banner once dismissed, without the dismiss button', async ({ page }) => {
  await page.goto('/#/runs/fx-failed');
  await id(page, 'action-dismiss').click();
  await expect(id(page, 'action-dismiss')).toBeHidden();
  await expect(id(page, 'action-banner')).toBeVisible();
  await expect(id(page, 'failure-error')).toBeVisible();
});

test('[DT-N7] a dismissed item can be restored from the dashboard', async ({ page }) => {
  setRecord('fx-failed', { dismissed: true });
  await page.goto('/');
  await expect(id(page, 'needs-you-item-fx-failed')).toBeHidden();
  const toggle = id(page, 'needs-dismissed-toggle');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(id(page, 'needs-dismissed')).toBeHidden();
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(id(page, 'needs-dismissed-fx-failed')).toContainText('transcript_2026-10-30.txt');
  await id(page, 'needs-restore-fx-failed').click();
  await expect(id(page, 'needs-you-item-fx-failed')).toBeVisible();
  await expect(id(page, 'needs-dismissed-fx-failed')).toHaveCount(0);
});

test('[DT-D8] delete asks first, says what stays, and the run goes', async ({ page }) => {
  await page.goto('/');
  const row = id(page, 'run-row-fx-done');
  await expect(row).toBeVisible();
  await id(page, 'run-delete-fx-done').click();
  await expect(id(page, 'run-delete-fx-done-question')).toContainText('knowledge base entry');
  await id(page, 'run-delete-fx-done-cancel').click();
  await expect(id(page, 'run-delete-fx-done-question')).toBeHidden();
  await expect(row).toBeVisible();
  await id(page, 'run-delete-fx-done').click();
  await id(page, 'run-delete-fx-done-confirm').click();
  await expect(row).toHaveCount(0);
  expect(fs.existsSync(path.join(PROCESSED, 'fx-done'))).toBe(false);
});

test('[DT-D8] deleting from the run page goes back to the dashboard', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await id(page, 'run-delete').click();
  await id(page, 'run-delete-confirm').click();
  await onHome(page);
  await expect(id(page, 'run-row-fx-done')).toHaveCount(0);
});

test('[DT-D8] once its run is deleted, the input stops warning that it already ran', async ({ page, request }) => {
  const name = 'PXL_20261030_070000000.mp4';
  const users = (await (await request.get('/api/inputs')).json()).inputs.find((i) => i.name === name).previous_runs;
  expect(users.length).toBeGreaterThan(0);
  for (const user of users) expect((await request.delete(`/api/runs/${user}`)).status()).toBe(200);
  await page.goto('/');
  await id(page, 'launch-button').click();
  await expect(id(page, `input-${name}`)).toBeVisible();
  await expect(id(page, `input-${name}`).getByTestId('tile-previous')).toHaveCount(0);
});

test('[DT-D9] a delete the server refuses shows the reason and keeps the run', async ({ page, errors }) => {
  errors.allow(/status of 409/);
  await page.route('**/api/runs/fx-done', (route) => (route.request().method() === 'DELETE'
    ? route.fulfill({ status: 409, json: { detail: 'wordpress is publishing: wait for it to finish' } })
    : route.continue()));
  await page.goto('/');
  await id(page, 'run-delete-fx-done').click();
  await id(page, 'run-delete-fx-done-confirm').click();
  await expect(id(page, 'run-delete-fx-done-error')).toContainText('publishing');
  await expect(id(page, 'run-row-fx-done')).toBeVisible();
  await id(page, 'run-delete-fx-done').click(); // asking again clears the old reason
  await expect(id(page, 'run-delete-fx-done-error')).toBeHidden();
});

test('[DT-D9] an active run offers no delete, and its page says why', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  await expect(id(page, 'run-row-fx-running')).toBeVisible();
  await expect(id(page, 'run-delete-fx-running')).toBeHidden();
  await id(page, 'run-fx-running').click();
  await expect(id(page, 'run-delete')).toBeDisabled();
  await expect(id(page, 'run-delete')).toHaveAttribute('title', /active/);
  await expect(id(page, 'run-rerun')).toHaveCount(0);
});

test('[DT-D7] a finished run shows its totals, and a total opens the per-step numbers', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const run = await (await page.request.get('/api/runs/fx-done')).json();
  await expect(id(page, 'run-total-calls')).toHaveText(String(run.telemetry.calls));
  await expect(id(page, 'run-total-tokens-note')).toContainText('output');
  await expect(id(page, 'run-total-time')).not.toHaveText('–');
  await expect(id(page, 'metrics-table')).toBeHidden();
  await id(page, 'run-tile-tokens').click();
  await expect(id(page, 'metrics-table')).toBeVisible();
  await page.getByTestId('view-tab-output').click();
  await expect(id(page, 'run-totals')).toBeHidden();
  await expect(id(page, 'run-cards')).toBeHidden();
});

test('[DT-D7] cards show what the run produced and open its section', async ({ page, errors }) => {
  errors.allow(/asset\/featured_image/); // this fixture has no image
  await mockPackages(page, 'fx-done', {
    youtube: (() => { const pkg = packageOf('fx-published', 'youtube-upload'); return { ...pkg, status: 'published', fields: { ...pkg.fields, title: 'the hill' }, result: { id: 'v', url: 'https://youtu.be/v' } }; })(),
    wordpress: packageOf('fx-done', 'wordpress-draft'),
  });
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'run-card-state-youtube')).toHaveText('Published');
  await expect(id(page, 'run-card-title-youtube')).toHaveText('the hill');
  await expect(id(page, 'run-card-link-youtube')).toHaveAttribute('href', 'https://youtu.be/v');
  await expect(id(page, 'run-card-wordpress')).toHaveAttribute('data-needs', 'true');
  await expect(id(page, 'run-card-state-wordpress')).toHaveText('Needs you');
  await expect(id(page, 'run-card-blog')).toContainText('Notion');
  await expect(id(page, 'run-card-link-blog')).toHaveAttribute('href', /^http/);
  await expect(id(page, 'run-card-state-kb')).toHaveText('Saved');
  await expect(id(page, 'run-card-title-kb')).not.toHaveText('');
  await id(page, 'run-card-open-wordpress').click();
  await expect(page).toHaveURL(/#\/runs\/fx-done\/output$/);
  await expect(id(page, 'output-section-wordpress')).toBeVisible();
});

test('[DT-D7] cards with nothing to show say so, and a failed read costs only the card', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  await page.route('**/api/runs/fx-crashed/publish', (route) => route.fulfill({ status: 500, json: {} }));
  await page.route('**/api/runs/fx-crashed/result', (route) => route.fulfill({ status: 500, json: {} }));
  await page.goto('/#/runs/fx-crashed');
  await expect(id(page, 'run-card-state-youtube')).toHaveText('Nothing yet');
  await expect(id(page, 'run-card-state-blog')).toHaveText('Nothing yet');
  await expect(id(page, 'run-card-state-kb')).toHaveText('Nothing yet');
  await expect(id(page, 'run-total-time')).not.toHaveText('–');
});

test('[DT-D7] a package that cannot be made says why; one that can, offers it', async ({ page }) => {
  await mockPackages(page, 'fx-done', {}, {
    youtube: { can: false, reason: 'youtube publishing is off for profile sandbox', mode: 'off' },
    wordpress: { can: true, reason: null, mode: 'manual' },
  });
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'run-card-state-youtube')).toHaveText('Off');
  await expect(id(page, 'run-card-title-youtube')).toContainText('off for profile');
  await expect(id(page, 'run-card-open-wordpress')).toContainText('Prepare');
});

test('[DT-D7] the graph paints a package waiting for you, and one published by hand', async ({ page, request }) => {
  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({
    json: { ...run, packages: [{ target: 'wordpress', status: 'pending' }, { target: 'youtube', status: 'published' }] },
  }));
  await page.goto('/#/runs/fx-done');
  const wordpress = id(page, 'node-wordpress_draft');
  await expect(wordpress).toHaveAttribute('data-package', 'pending');
  await expect(wordpress.getByTestId('dag-meta')).toHaveText('needs you');
  const youtube = id(page, 'node-youtube_upload');
  await expect(youtube).toHaveAttribute('data-state', 'done');
  await expect(youtube.getByTestId('dag-meta')).toHaveText('published');
  await expect(youtube).toHaveAttribute('aria-label', 'YouTube: published');
});

test('[DT-D7] a finished run can start over from its input, after asking', async ({ page }) => {
  setRecord('fx-done', { input: { kind: 'transcript', path: path.join(process.env.PFW_UI_TMP, 'inbox', 'transcript_2026-10-30.txt') } });
  await page.goto('/#/runs/fx-done');
  await id(page, 'run-rerun').click();
  await expect(id(page, 'run-rerun-question')).toContainText('new run');
  await id(page, 'run-rerun-confirm').click();
  await expect(page).not.toHaveURL(/fx-done/);
  await expect(page).toHaveURL(/_ui/);
});

test('[DT-D7] restart is off when the input is gone, and says so', async ({ page }) => {
  setRecord('fx-done', { input: { kind: 'video', path: '/gone/v.mp4' } });
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'run-rerun')).toBeDisabled();
  await expect(id(page, 'run-rerun')).toHaveAttribute('title', /gone/);
});

test('[DT-D10] the dashboard shows the latest themes, and an entry opens in the knowledge base', async ({ page }) => {
  await page.goto('/');
  const latest = id(page, 'latest-2026-09-02-rehearse-the-quit-urge');
  await expect(latest).toContainText('Rehearse the Quit Urge');
  await expect(latest).toContainText('deliberate-discomfort');
  await expect(id(page, 'tile-kb')).toHaveText('3');
  await expect(id(page, 'tile-kb-note')).toHaveText('entries · 2 themes');
  await latest.click();
  await expect(page.locator('body')).toHaveAttribute('data-page', 'knowledge');
  await expect(id(page, 'knowledge-reader')).toContainText('The urge to stop arrives on a schedule');
  await id(page, 'knowledge-back').click();
  await onHome(page);
});

test('[DT-D10] the knowledge base lists every entry and counts the themes', async ({ page }) => {
  await page.goto('/');
  await id(page, 'knowledge-link').click();
  await expect(id(page, 'entry-2026-05-15-no-themes-yet')).toBeVisible();
  await expect(id(page, 'knowledge-entries').getByRole('link')).toHaveCount(3);
  await expect(id(page, 'knowledge-themes')).toContainText('body-literacy · 2');
  await expect(id(page, 'knowledge-reader')).toBeHidden();
});

test('[DT-D10] an empty vault and an unknown entry each say so', async ({ page, errors }) => {
  errors.allow(/status of 404/);
  const none = { entries: [], themes: [], entry_count: 0, theme_count: 0 };
  await page.route('**/api/knowledge?*', (route) => route.fulfill({ json: none }));
  await page.goto('/');
  await expect(id(page, 'latest-empty')).toBeVisible();
  await page.route('**/api/knowledge', (route) => route.fulfill({ json: none }));
  await page.goto('/#/knowledge');
  await expect(id(page, 'knowledge-empty')).toBeVisible();
  await page.goto('/#/knowledge/nope');
  await expect(id(page, 'knowledge-reader')).toContainText('no entry nope');
});

test('[DT-D10] a knowledge base that cannot be read says so, and the dashboard carries on', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  await page.route('**/api/knowledge*', (route) => route.fulfill({ status: 500, json: {} }));
  await page.goto('/');
  await expect(id(page, 'tile-kb')).toHaveText('–');
  await expect(id(page, 'run-list')).toBeVisible();
  await page.goto('/#/knowledge');
  await expect(id(page, 'knowledge-reader')).toContainText('unavailable');
});

test('[DT-D10] the knowledge page opened directly', async ({ page }) => {
  await page.goto('/#/knowledge/2026-07-27-conditions-before-willpower');
  await expect(page.locator('body')).toHaveAttribute('data-page', 'knowledge');
  await expect(id(page, 'knowledge-reader')).toContainText('A barrier gave way');
});

test('[DT-N6] a dismiss that cannot reach the server says so, and a run with no input path is named by its id', async ({ page, errors, request }) => {
  errors.allow(/requestfailed: .*dismiss/);
  errors.allow(/ERR_FAILED/);
  await page.route('**/api/runs/fx-failed/dismiss', (route) => route.abort());
  await page.goto('/');
  await id(page, 'needs-dismiss-fx-failed').click();
  await expect(id(page, 'needs-error')).toContainText('Failed to fetch');

  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, input: { kind: null, path: null } } }));
  await page.goto('/#/runs/fx-done');
  await id(page, 'action-dismiss').click();
  await expect(id(page, 'needs-notice')).toContainText('fx-done');
});

test('[DT-D9] a delete that cannot reach the server says so', async ({ page, errors }) => {
  errors.allow(/requestfailed: .*fx-done/);
  errors.allow(/ERR_FAILED/);
  await page.route('**/api/runs/fx-done', (route) => (route.request().method() === 'DELETE' ? route.abort() : route.continue()));
  await page.goto('/');
  await id(page, 'run-delete-fx-done').click();
  await id(page, 'run-delete-fx-done-confirm').click();
  await expect(id(page, 'run-delete-fx-done-error')).toContainText('Failed to fetch');
});

test('[DT-D10] an entry with no date still lists', async ({ page }) => {
  await page.route('**/api/knowledge?*', (route) => route.fulfill({
    json: { entries: [{ slug: 'plain', title: 'Plain', date: null, themes: [] }], themes: [], entry_count: 1, theme_count: 0 },
  }));
  await page.goto('/');
  await expect(id(page, 'latest-plain')).toContainText('Plain');
});

for (const [entry, title] of [['2026-07-27-conditions-before-willpower.md', 'Conditions before willpower'], ['2026-10-30.md', '2026-10-30']]) {
  test(`[DT-D7] the entry card calls ${entry} "${title}"`, async ({ page, request }) => {
    const run = await (await request.get('/api/runs/fx-done')).json();
    const kb = { ...run.nodes.kb_curator, outputs: { ...run.nodes.kb_curator.outputs, vault_entry_path: `/vault/entries/${entry}` } };
    await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, nodes: { ...run.nodes, kb_curator: kb } } }));
    await page.goto('/#/runs/fx-done');
    await expect(id(page, 'run-card-title-kb')).toHaveText(title);
  });
}

test('[DT-D7] an answer for a run you already left lands nowhere', async ({ page }) => {
  let release;
  const held = new Promise((r) => { release = r; });
  for (const part of ['result', 'publish']) {
    await page.route(`**/api/runs/fx-done/${part}`, async (route) => {
      await held;
      await route.continue();
    });
  }
  await page.route('**/api/runs/fx-crashed/result', (route) => route.fulfill({ json: { status: 'unavailable', reason: 'none', blog: null, research: null } }));
  const stale = page.waitForResponse('**/api/runs/fx-done/result');
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'run-card-state-kb')).toHaveText('Saved');
  await page.evaluate(() => { location.hash = '#/runs/fx-crashed'; });
  await expect(id(page, 'run-header-id')).toHaveText('fx-crashed');
  await expect(id(page, 'run-card-state-blog')).toHaveText('Nothing yet');
  release();
  await (await stale).finished();
  await page.evaluate(() => fetch('/health'));
  await expect(id(page, 'run-card-state-blog')).toHaveText('Nothing yet');
});

test('[DT-D7] the graph keeps the long edges clear: Pick image and WordPress run above Write blog', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const y = async (name) => (await id(page, `node-${name}`).locator('.disc').boundingBox()).y;
  await expect(id(page, 'node-extract_image')).toHaveAttribute('data-state', 'done');
  expect(await y('extract_image')).toBeLessThan(await y('wordpress_draft'));
  expect(await y('wordpress_draft')).toBeLessThan(await y('notion_blog'));
  expect(await y('notion_blog')).toBeLessThan(await y('writer'));
  expect(await y('writer')).toBeLessThan(await y('kb_curator'));
  expect(await y('kb_curator')).toBeLessThan(await y('research'));
});

test('[DT-D7] a graph with a step nobody laid out still draws', async ({ page, request }) => {
  const graph = await (await request.get('/api/graph')).json();
  const extra = { nodes: [...graph.nodes, { id: 'new_step', label: 'New step', icon: 'dot' }], edges: [...graph.edges, { from: 'summarizer', to: 'new_step' }] };
  await page.route('**/api/graph', (route) => route.fulfill({ json: extra }));
  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, nodes: { ...run.nodes, new_step: { ...run.nodes.summarizer } } } }));
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'node-new_step')).toBeVisible();
});
