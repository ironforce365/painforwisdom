// A dry run cannot be taken for a real one (spec 2026-11-06-pipeline-dry-run-design).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const id = (page, testid) => page.getByTestId(testid);

function setRecord(runId, fields) {
  const file = path.join(PROCESSED, runId, 'langgraph', 'run.json');
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, 'utf8')), ...fields }));
}

const fastFake = (page, seen) => page.route('**/api/runs', (route, req) => {
  if (req.method() !== 'POST') return route.continue();
  seen.push(req.postDataJSON());
  return route.continue({ postData: JSON.stringify({ ...req.postDataJSON(), fake: { scenario: 'ok', step_ms: 20 } }) });
});

test.beforeEach(() => freshServerState());

test('[DT-S9] the start popup makes a dry run plain, and starts one', async ({ page }) => {
  const seen = [];
  await fastFake(page, seen);
  await page.goto('/');
  await id(page, 'launch-button').click();
  await expect(id(page, 'launch-title')).toHaveText('Start a run');
  await expect(id(page, 'launch-dry')).toHaveAttribute('aria-checked', 'false');
  await expect(id(page, 'launch-dry-note')).toBeHidden();

  await id(page, 'launch-dry').click();
  await expect(id(page, 'launch-title')).toHaveText('Start a dry run');
  await expect(id(page, 'launch-dry-note')).toContainText('nothing is kept');

  await id(page, 'launch-dry').click(); // and back
  await expect(id(page, 'launch-title')).toHaveText('Start a run');
  await id(page, 'launch-dry').click();

  await page.locator('.tile').first().click();
  await expect(id(page, 'dry-banner')).toBeVisible();
  await expect(id(page, 'dry-banner')).toContainText('Nothing from this run is kept');
  expect(seen[0].dry_run).toBe(true);
});

test('[DT-S9] each opening of the popup starts as a real run', async ({ page }) => {
  await page.goto('/');
  await id(page, 'launch-button').click();
  await id(page, 'launch-dry').click();
  await id(page, 'launch-close').click();
  await id(page, 'launch-button').click();
  await expect(id(page, 'launch-dry')).toHaveAttribute('aria-checked', 'false');
  await expect(id(page, 'launch-title')).toHaveText('Start a run');
});

test('[DT-S9] a normal start says it is not a dry run', async ({ page }) => {
  const seen = [];
  await fastFake(page, seen);
  await page.goto('/');
  await id(page, 'launch-button').click();
  await page.locator('.tile').first().click();
  await expect(id(page, 'dag')).toBeVisible();
  await expect(id(page, 'dry-banner')).toBeHidden();
  expect(seen[0].dry_run).toBeUndefined();
});

test('[DT-D11] the dashboard marks a dry run and keeps it out of the counts', async ({ page }) => {
  await page.goto('/');
  await expect(id(page, 'run-fx-done').getByTestId('rl-dry')).toBeHidden();
  const before = Number(await id(page, 'tile-runs').textContent());
  await expect(id(page, 'tile-runs-note')).not.toContainText('dry');

  setRecord('fx-done', { dry_run: true });
  await page.reload();
  await expect(id(page, 'run-fx-done').getByTestId('rl-dry')).toHaveText('DRY RUN');
  await expect(id(page, 'tile-runs')).toHaveText(String(before - 1));
  await expect(id(page, 'tile-runs-note')).toContainText('1 dry');
});

test('[DT-D11] the run page says what happened instead, and nothing can be published', async ({ page, errors }) => {
  errors.allow(/featured_image/); // the fixture's package lists an image it does not have
  setRecord('fx-done', { dry_run: true });
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'dry-banner')).toBeVisible();
  await expect(id(page, 'run-card-state-kb')).toHaveText('Not saved');
  await expect(id(page, 'run-card-state-blog')).toHaveText('In the sandbox table');
  await expect(id(page, 'run-card-link-blog')).toHaveText('Open in sandbox Notion');
  await expect(id(page, 'run-card-state-wordpress')).toHaveText('Preview only');
  await expect(id(page, 'run-card-wordpress')).toHaveAttribute('data-needs', 'false');
  await expect(id(page, 'node-wordpress_draft')).toHaveAttribute('data-package', 'preview'); // the graph does not say "needs you"
  await expect(id(page, 'node-wordpress_draft')).toContainText('preview only');

  await id(page, 'run-card-open-kb').click();
  await expect(id(page, 'output-section-kb')).toBeVisible();
  await expect(id(page, 'output-vault-link')).toHaveCount(0);

  await page.goto('/#/runs/fx-done/output');
  await id(page, 'output-nav-wordpress').click();
  await expect(id(page, 'publish-dry-wordpress')).toHaveText('Dry run: nothing is published');
  await expect(id(page, 'publish-wordpress')).toBeDisabled();
});

test('[DT-D11] a real run shows none of it', async ({ page, errors }) => {
  errors.allow(/featured_image/);
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'dag')).toBeVisible();
  await expect(id(page, 'dry-banner')).toBeHidden();
  await expect(id(page, 'run-for-real')).toHaveCount(0);
  await expect(id(page, 'run-card-state-kb')).toHaveText('Saved');
  await page.goto('/#/runs/fx-done/output');
  await id(page, 'output-nav-wordpress').click();
  await expect(id(page, 'publish-dry-wordpress')).toBeHidden();
});

test('[DT-D11] Run for real asks first, then starts a real run', async ({ page, request }) => {
  setRecord('fx-done', { dry_run: true });
  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, manage: { ...run.manage, rerun: null } } }));
  await page.goto('/#/runs/fx-done');
  let body;
  await page.route('**/api/runs/fx-done/restart', (route, req) => {
    body = req.postDataJSON();
    return route.fulfill({ status: 201, json: { run_id: 'fx-crashed' } });
  });
  await id(page, 'run-for-real').click();
  await expect(page.getByText('It writes to the knowledge base')).toBeVisible();
  await page.getByRole('button', { name: 'Yes, run for real' }).click();
  await expect(page).toHaveURL(/#\/runs\/fx-crashed/);
  expect(body.dry_run).toBe(false);
});

test('[DT-D11] restarting a dry run asks for a new dry run and sends no choice', async ({ page, request }) => {
  setRecord('fx-done', { dry_run: true });
  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, manage: { ...run.manage, rerun: null } } }));
  await page.goto('/#/runs/fx-done');
  let body;
  await page.route('**/api/runs/fx-done/restart', (route, req) => {
    body = req.postDataJSON();
    return route.fulfill({ status: 201, json: { run_id: 'fx-crashed' } });
  });
  await id(page, 'run-rerun').click();
  await expect(page.getByText('as a new dry run?')).toBeVisible();
  await page.getByRole('button', { name: 'Yes, restart' }).click();
  await expect(page).toHaveURL(/#\/runs\/fx-crashed/);
  expect(body.dry_run).toBeUndefined();
});

test('[DT-D11] Run for real is off when the input is gone, and says why', async ({ page, request }) => {
  setRecord('fx-done', { dry_run: true });
  const run = await (await request.get('/api/runs/fx-done')).json();
  await page.route('**/api/runs/fx-done', (route) => route.fulfill({ json: { ...run, manage: { ...run.manage, rerun: 'the input is gone' } } }));
  await page.goto('/#/runs/fx-done');
  await expect(id(page, 'run-for-real')).toBeDisabled();
  await expect(id(page, 'run-for-real')).toHaveAttribute('title', 'the input is gone');
});

test('[DT-D11] a dry run with nothing yet keeps the plain card', async ({ page, request }) => {
  setRecord('fx-crashed', { dry_run: true });
  await page.route('**/api/runs/fx-crashed/result', (route) => route.fulfill({ json: { status: 'unavailable', reason: 'none', blog: null, research: null } }));
  await page.goto('/#/runs/fx-crashed');
  await expect(id(page, 'dry-banner')).toBeVisible();
  await expect(id(page, 'run-card-state-kb')).toHaveText('Nothing yet');
  await expect(id(page, 'run-card-state-blog')).toHaveText('Nothing yet');
  await expect(id(page, 'run-card-state-youtube')).not.toHaveText('Preview only'); // no package, only a reason why not
  await expect(id(page, 'run-card-state-wordpress')).not.toHaveText('Preview only');
  expect((await (await request.get('/api/runs/fx-crashed')).json()).dry_run).toBe(true);
});
