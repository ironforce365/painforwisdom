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
  expect(live.map((r) => r.run_id)).toEqual(['fx-running', 'fx-hitl']);
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

test('statuses render as dots with distinct glyphs', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  for (const [id, status] of [['fx-done', 'done'], ['fx-failed', 'failed'], ['fx-crashed', 'crashed'],
    ['fx-hitl', 'waiting_hitl'], ['fx-running', 'running']]) {
    const item = page.getByTestId(`run-${id}`);
    await expect(item).toHaveAttribute('data-status', status);
    await expect(item).toHaveAttribute('aria-label', new RegExp(status));
  }
});

test('empty list shows an icon, not a paragraph', async ({ page }) => {
  freshServerState();
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await expect(page.getByTestId('runs-empty')).toHaveAttribute('aria-label', /.+/);
  expect((await page.getByTestId('runs-empty').innerText()).trim()).toBe('');
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
