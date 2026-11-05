import { test, expect, freshServerState } from '../fixtures.js';

const items = (page) => page.getByTestId('run-list').locator('[data-testid^="run-"]');

test('list is newest first with live runs on top and a spinner on them', async ({ page, request }) => {
  freshServerState({ live: true });
  const api = (await (await request.get('/api/runs')).json()).runs;
  const live = api.filter((r) => r.status === 'running' || r.status.startsWith('waiting_'));
  const rest = api.filter((r) => !live.includes(r));
  await page.goto('/');
  await expect(items(page)).toHaveCount(api.length);
  const ids = await items(page).evaluateAll((els) => els.map((e) => e.dataset.runId));
  expect(ids).toEqual([...live, ...rest].map((r) => r.run_id));
  expect(live.map((r) => r.run_id)).toEqual(['fx-running', 'fx-hitl', 'fx-error', 'fx-oldwait']);
  await expect(page.getByTestId('run-fx-running')).toHaveAttribute('data-status', 'running');
  await expect(page.getByTestId('run-fx-running').getByTestId('spinner')).toBeVisible();
  await expect(page.getByTestId('run-fx-done').getByTestId('spinner')).toBeHidden();
});

test('boot without a hash selects the top run and writes its hash', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  await expect(page.getByTestId('run-fx-running')).toHaveAttribute('aria-pressed', 'true');
  await expect(page).toHaveURL(/#\/runs\/fx-running$/);
});

test('clicking a run selects it, sets the hash, and a reload restores it', async ({ page }) => {
  freshServerState();
  await page.goto('/');
  await page.getByTestId('run-fx-failed').click();
  await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  await page.reload();
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  await page.getByTestId('run-fx-done').click();
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'done');
});

test('statuses read as text with distinct glyphs', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  for (const [id, status] of [['fx-done', 'done'], ['fx-failed', 'failed'], ['fx-crashed', 'crashed'],
    ['fx-hitl', 'waiting_hitl'], ['fx-running', 'running']]) {
    const item = page.getByTestId(`run-${id}`);
    await expect(item).toHaveAttribute('data-status', status);
    await expect(item).toHaveAttribute('aria-label', new RegExp(status));
  }
  await expect(page.getByTestId('run-fx-stopped').locator('.glyph-stopped')).toBeVisible();
  await expect(page.getByTestId('run-fx-stopped').locator('.glyph-done')).toBeHidden();
});

test('[DT-D6] each row names input, status, time and cost', async ({ page }) => {
  freshServerState();
  await page.goto('/#/runs/fx-modeoff');
  const failed = page.getByTestId('run-fx-failed');
  await expect(failed.getByTestId('rl-name')).toHaveText('transcript_2026-10-30.txt');
  await expect(failed.getByTestId('rl-state')).toHaveText('Failed at research');
  await expect(failed.getByTestId('rl-when')).toHaveText(/\d\d:\d\d/);
  await expect(failed.getByTestId('rl-time')).toHaveText('2m 31s');
  await expect(failed.getByTestId('rl-tokens')).toHaveText('17.1k tok');
  await expect(failed.getByTestId('rl-cost')).toHaveText('$0.13');
  await expect(failed.getByTestId('rl-needs')).toBeVisible();

  const estimate = page.getByTestId('run-fx-unpackaged');
  await expect(estimate.getByTestId('rl-state')).toHaveText('Done · PASS');
  await expect(estimate.getByTestId('rl-cost')).toHaveText('≈$0.09');
  await expect(estimate.getByTestId('rl-cost')).toHaveAttribute('title', /API-equivalent/);
  await expect(estimate.getByTestId('rl-needs')).toBeHidden();

  const legacy = page.getByTestId('run-fx-legacy');
  await expect(legacy.getByTestId('rl-cost')).toHaveText('–');
  await expect(page.getByTestId('run-fx-stopped').getByTestId('rl-tokens')).toBeHidden(); // nothing spent
  await expect(page.getByTestId('run-fx-stopped').getByTestId('rl-cost')).toHaveText('–');
  await expect(page.getByTestId('run-list')).not.toContainText(/\$0\.00(?!\d)/);
});

test('a row without an input name or a start time still reads', async ({ page }) => {
  freshServerState();
  await page.route('**/api/runs', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    Object.assign(body.runs.find((r) => r.run_id === 'fx-modeoff'), { input: { kind: null, name: null }, started_at: null });
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/runs/fx-done');
  await expect(page.getByTestId('run-fx-modeoff').getByTestId('rl-name')).toHaveText('fx-modeoff');
  await expect(page.getByTestId('run-fx-modeoff').getByTestId('rl-when')).toHaveText('–');
});

test('the selected row follows its run live; a refresh keeps focus; a run the list missed is fine', async ({ page, request }) => {
  freshServerState();
  await page.clock.install();
  const res = await request.post('/api/runs', { data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: 30 } } });
  const { run_id: id } = await res.json();
  await page.goto(`/#/runs/${id}`);
  await expect.poll(async () => {
    await page.clock.runFor(1000);
    return page.getByTestId(`run-${id}`).getAttribute('data-status');
  }, { timeout: 30_000 }).toBe('done');
  await expect(page.getByTestId(`run-${id}`).getByTestId('rl-state')).toHaveText('Done · PASS');
  await expect(page.getByTestId(`run-${id}`).getByTestId('rl-tokens')).toHaveText(/tok$/);
  await page.getByTestId(`run-${id}`).focus();
  await page.clock.runFor(5000);
  await page.evaluate(() => fetch('/health'));
  await expect(page.getByTestId(`run-${id}`)).toBeFocused();

  // selected before the list has heard of it
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.clock.runFor(5000);
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await page.getByTestId('run-header').hover();
  await page.evaluate(() => { location.hash = '#/runs/fx-done'; });
  await expect(page.getByTestId('run-header-status')).toHaveText('Done');
});

test('the rows use the width they are given and stay readable at both widths', async ({ page }) => {
  freshServerState();
  for (const [width, min] of [[1280, 200], [3440, 300]]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/#/runs/fx-failed');
    await expect(page.getByTestId('run-fx-failed').getByTestId('rl-cost')).toBeVisible();
    const list = await page.getByTestId('run-list').boundingBox();
    expect(list.width).toBeGreaterThanOrEqual(min);
    const needs = await page.getByTestId('run-fx-failed').getByTestId('rl-needs').boundingBox();
    expect(needs.x + needs.width).toBeLessThanOrEqual(list.x + list.width); // not clipped by the column
  }
});

test('empty list says so', async ({ page }) => {
  freshServerState();
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await expect(page.getByTestId('runs-empty')).toContainText('No runs yet');
  await expect(page).toHaveURL(/\/$/);
});

test('a hash naming an unknown run shows the error badge and stops polling it', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 404/);
  let hits = 0;
  page.on('request', (r) => { if (r.url().endsWith('/api/runs/nope')) hits += 1; });
  await page.clock.install();
  await page.goto('/#/runs/nope');
  await expect(page.getByTestId('error-badge')).toBeVisible();
  await page.clock.runFor(3000);
  expect(hits).toBe(1);
});
