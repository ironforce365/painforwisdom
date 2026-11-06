// Needs you on the dashboard (spec 2026-11-06-console-dashboards, N1).
import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

test('[DT-N1] lists every run wanting a decision, with its reason, one click to it', async ({ page, request }) => {
  const { runs } = await (await request.get('/api/runs')).json();
  const wanted = runs.filter((r) => r.attention.length);
  expect(wanted.length).toBeGreaterThan(1);
  await page.goto('/');
  await expect(page.getByTestId('needs-you')).toBeVisible();
  await expect(page.getByTestId('needs-you-count')).toHaveText(String(wanted.length));
  for (const r of wanted) {
    const item = page.getByTestId(`needs-you-item-${r.run_id}`);
    await expect(item).toHaveCount(1);
    await expect(item).toContainText(r.attention[0].label);
    await expect(item).toHaveAttribute('data-kind', r.attention[0].kind);
  }
  await expect(page.getByTestId('needs-you-item-fx-modeoff')).toHaveCount(0);
  await page.getByTestId('needs-you-item-fx-failed').click();
  await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
  await expect(page.getByTestId('failure-node')).toHaveText('research');
});

test('every reason of a run is named; live decisions come first', async ({ page, errors }) => {
  await page.route('**/api/runs', (route) => route.fulfill({
    json: {
      runs: [
        { run_id: 'a', status: 'done', verdict: 'PASS', input: { kind: 'transcript', name: 'a.txt' }, total_s: 1, started_at: 1, cost_usd: 0, cost_estimated: false, tokens: 0, failed_node: null, dismissed_attention: [],
          attention: [{ kind: 'publish', label: 'youtube: package pending' }, { kind: 'publish', label: 'wordpress: package failed' }] },
        { run_id: 'b', status: 'failed', verdict: null, input: { kind: 'transcript', name: null }, total_s: 1, started_at: 2, cost_usd: 0, cost_estimated: false, tokens: 0, failed_node: 'x', dismissed_attention: [],
          attention: [{ kind: 'failed', label: 'Failed at x' }] },
        { run_id: 'c', status: 'waiting_hitl', verdict: null, input: { kind: 'transcript', name: 'c.txt' }, total_s: 1, started_at: 3, cost_usd: 0, cost_estimated: false, tokens: 0, failed_node: null, dismissed_attention: [],
          attention: [{ kind: 'approval', label: 'Approve theme' }] },
        { run_id: 'odd', status: 'failed', verdict: null, input: { kind: 'transcript', name: 'odd.txt' }, total_s: 1, started_at: 0, cost_usd: 0, cost_estimated: false, tokens: 0, failed_node: null, dismissed_attention: [],
          attention: [{ kind: 'unheard-of', label: 'Odd' }] },
      ],
    },
  }));
  await page.route('**/api/runs/*', (route) => route.fulfill({ status: 404, json: { detail: 'unknown run' } }));
  errors.allow(/status of 404/);
  await page.goto('/');
  const a = page.getByTestId('needs-you-item-a');
  await expect(a).toContainText('youtube: package pending');
  await expect(a).toContainText('wordpress: package failed');
  await expect(page.getByTestId('needs-you-item-b')).toContainText('b'); // no input name: the run id
  const order = await page.locator('[data-testid^="needs-you-item-"]').evaluateAll((els) => els.map((e) => e.dataset.testid));
  expect(order).toEqual(['needs-you-item-c', 'needs-you-item-b', 'needs-you-item-a', 'needs-you-item-odd']);
});

test('nothing wanted: count 0 and a plain statement', async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('needs-you-count')).toHaveText('0');
  await expect(page.getByTestId('needs-you')).toContainText('Nothing');
});

test('refreshes with the run list and keeps focus when nothing changed', async ({ page }) => {
  await page.clock.install();
  await page.goto('/');
  const item = page.getByTestId('needs-you-item-fx-stopped');
  await expect(item).toBeVisible();
  await item.focus();
  await page.clock.runFor(5000);
  await page.evaluate(() => fetch('/health'));
  await expect(item).toBeFocused();
  await page.route('**/api/runs', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.runs = body.runs.map((r) => (r.run_id === 'fx-stopped' ? { ...r, attention: [] } : r));
    await route.fulfill({ response: res, json: body });
  });
  await page.clock.runFor(5000);
  await expect(item).toHaveCount(0);
});

test('[DT-S7] needs you heads the dashboard and Start is large and on screen, at both widths', async ({ page }) => {
  for (const width of [1280, 3440]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/');
    await expect(page.getByTestId('needs-you-count')).not.toHaveText('0');
    const start = await page.getByTestId('launch-button').boundingBox();
    expect(start.x + start.width).toBeLessThanOrEqual(width);
    expect(start.height).toBeGreaterThanOrEqual(36);
    const bar = await page.getByTestId('needs-you').boundingBox();
    expect(bar.y + bar.height).toBeLessThanOrEqual((await page.getByTestId('run-list').boundingBox()).y + 1);
    expect(bar.width).toBeGreaterThan(800);
  }
});

test('[DT-N1] a run waiting on a package opens that package; one that failed opens the run', async ({ page, errors }) => {
  errors.allow(/asset\/featured_image/);
  freshServerState();
  await page.goto('/');
  await page.getByTestId('needs-you-item-fx-done').click();
  await expect(page.locator('body')).toHaveAttribute('data-view', 'output');
  await expect(page.getByTestId('output-nav-wordpress')).toHaveAttribute('aria-current', 'true');
  await expect(page).toHaveURL(/#\/runs\/fx-done\/output$/);

  await page.getByTestId('run-back').click();
  await page.getByTestId('needs-you-item-fx-failed').click();
  await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
  await expect(page.locator('body')).toHaveAttribute('data-view', 'graph');
});
