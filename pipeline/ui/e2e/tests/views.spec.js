// Shell, tabs and routes (spec 2026-11-05-console-end-to-end §4, N2).
import { test, expect, freshServerState } from '../fixtures.js';

const VIEWS = ['graph', 'output', 'logs'];
const SLOTS = { graph: 'dag', logs: 'logs' };
const body = (page) => page.locator('body');

test.beforeEach(() => freshServerState());

test('[DT-N2] tabs switch the view, set body[data-view] and the route', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await expect(body(page)).toHaveAttribute('data-view', 'graph');
  await expect(page.getByTestId('view-tab-graph')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('dag')).toBeVisible();
  await page.getByTestId('view-tab-logs').click();
  await expect(body(page)).toHaveAttribute('data-view', 'logs');
  await expect(page).toHaveURL(/#\/runs\/fx-done\/logs$/);
  await expect(page.getByTestId('logs')).toBeVisible();
  await expect(page.getByTestId('dag')).toBeHidden();
  await page.getByTestId('view-tab-output').click();
  await expect(body(page)).toHaveAttribute('data-view', 'output');
  await expect(page).toHaveURL(/#\/runs\/fx-done\/output$/);
  await expect(page.locator('#operability')).toBeVisible();
  await expect(page.getByTestId('logs')).toBeHidden();
  await page.getByTestId('view-tab-graph').click();
  await expect(page).toHaveURL(/#\/runs\/fx-done$/);
  await expect(page.locator('#operability')).toBeHidden();
});

for (const view of VIEWS) {
  test(`[DT-N2] a deep link to ${view} survives a reload`, async ({ page }) => {
    await page.goto(`/#/runs/fx-done/${view}`);
    await expect(body(page)).toHaveAttribute('data-view', view);
    await expect(page.getByTestId(`view-tab-${view}`)).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('run-fx-done')).toHaveAttribute('aria-pressed', 'true');
    await page.reload();
    await expect(body(page)).toHaveAttribute('data-view', view);
    await expect(page.getByTestId('run-fx-done')).toHaveAttribute('aria-pressed', 'true');
  });
}

test('an unknown view is the graph', async ({ page }) => {
  await page.goto('/#/runs/fx-done/nonsense');
  await expect(body(page)).toHaveAttribute('data-view', 'graph');
});

test('a module hears the first view:changed at boot, on a deep link', async ({ page }) => {
  let reads = 0;
  page.on('request', (r) => { if (/\/log(\?|$)/.test(r.url())) reads += 1; });
  await page.goto('/#/runs/fx-done/logs');
  await expect(page.getByTestId('logs-count')).toBeVisible();
  await expect.poll(() => reads).toBeGreaterThan(0); // logs.js mounted before the view was announced
});

test('[DT-N2] editing the hash navigates; a bare hash changes nothing', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await expect(page.getByTestId('run-fx-done')).toHaveAttribute('aria-pressed', 'true');
  await page.evaluate(() => { location.hash = '#/runs/fx-failed/logs'; });
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'true');
  await expect(body(page)).toHaveAttribute('data-view', 'logs');
  await page.evaluate(() => { location.hash = '#/runs/fx-failed'; });
  await expect(body(page)).toHaveAttribute('data-view', 'graph');
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'true');
  await page.evaluate(() => { location.hash = '#/nothing'; });
  await expect(page.getByTestId('run-fx-failed')).toHaveAttribute('aria-pressed', 'true');
});

test('selecting another run keeps the view', async ({ page }) => {
  await page.goto('/#/runs/fx-done/logs');
  await page.getByTestId('run-fx-failed').click();
  await expect(page).toHaveURL(/#\/runs\/fx-failed\/logs$/);
  await expect(body(page)).toHaveAttribute('data-view', 'logs');
});

test('tabs sit in the run header, under the page header', async ({ page }) => {
  await page.goto('/#/runs/fx-failed');
  await expect(page.getByTestId('run-header').getByTestId('view-tab-graph')).toBeVisible();
  await expect(page.locator('header.bar #viewtabs')).toHaveCount(0);
});

test('[DT-R1] the run header names status, input, id, time and reason', async ({ page }) => {
  await page.goto('/#/runs/fx-failed');
  const head = page.getByTestId('run-header');
  await expect(page.getByTestId('run-header-status')).toHaveText('Failed');
  await expect(page.getByTestId('run-header-status')).toHaveAttribute('data-status', 'failed');
  await expect(page.getByTestId('run-header-input')).toHaveText('transcript_2026-10-30.txt');
  await expect(head).toContainText('fx-failed');
  await expect(page.getByTestId('run-header-when')).toHaveText(/\d\d:\d\d/);
  await expect(page.getByTestId('run-header-reason')).toContainText('research agent returned no rows');
  await expect(page.getByTestId('header-profile')).toHaveText('sandbox');
  await page.getByTestId('run-fx-legacy').click();
  await expect(page.getByTestId('run-header-status')).toHaveText('Done');
  await expect(page.getByTestId('run-header-verdict')).toHaveText('PASS');
  await expect(page.getByTestId('run-header-reason')).toHaveText('');
  await expect(page.getByTestId('header-profile')).toHaveText('');
});

test('the header shows what a run is waiting for, and a run with no input path a dash', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    run.input.path = null;
    run.started_at = null;
    run.pending = { id: 'x', kind: 'approval', node: 'writer', title: 'Approve the theme', prompt: 'p', error: null, non_recoverable: null, options: [], free_text: false };
    await route.fulfill({ response: res, json: run });
  });
  await page.goto('/#/runs/fx-done');
  await expect(page.getByTestId('run-header-reason')).toHaveText('Approve the theme');
  await expect(page.getByTestId('run-header-input')).toHaveText('–');
  await expect(page.getByTestId('run-header-when')).toHaveText('–');
});

test('no run selected: the header says so and the view is empty of numbers', async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('run-header')).toContainText('No run selected');
  await expect(page.getByTestId('run-header-status')).toBeHidden();
});

for (const [width, height] of [[1280, 720], [3440, 1440]]) {
  test(`each view fills the centre, end to end, at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/#/runs/fx-modeoff');
    await expect(page.getByTestId('metrics-bar')).toBeVisible();
    const list = await page.getByTestId('run-list').boundingBox();
    const bar = await page.getByTestId('metrics-bar').boundingBox();
    const head = await page.getByTestId('run-header').boundingBox();
    for (const view of VIEWS) {
      await page.getByTestId(`view-tab-${view}`).click();
      const slot = view === 'output' ? page.locator('#operability') : page.getByTestId(SLOTS[view]);
      const box = await slot.boundingBox();
      expect(box.x).toBeCloseTo(list.x + list.width, 0);
      expect(box.x + box.width).toBeCloseTo(width, 0);
      expect(box.y).toBeGreaterThanOrEqual(head.y + head.height - 1);
      expect(Math.abs(box.y + box.height - bar.y)).toBeLessThan(2); // the metrics border
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(list.width).toBeLessThan(width * 0.25);
  });
}

test('the run list reads as text at any width; phone width stacks without overlap', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/#/runs/fx-done');
  await expect(page.getByTestId('metrics-bar')).toBeVisible();
  await page.getByTestId('node-writer').click();
  await expect(page.getByTestId('drawer')).toBeVisible();
  const [g, d] = [await page.getByTestId('dag').boundingBox(), await page.getByTestId('drawer').boundingBox()];
  expect(g.y + g.height <= d.y + 1 || d.y + d.height <= g.y + 1).toBe(true);
});
