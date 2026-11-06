// Logs view (spec 2026-11-05-console-end-to-end §4).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, answerPending, PROCESSED } from '../fixtures.js';

test.beforeEach(() => freshServerState());

const logFile = (run) => path.join(PROCESSED, run, 'langgraph', 'stdout.log');
const lines = (page) => page.getByTestId('log-line');
const scrolled = (page) => page.getByTestId('logs-scroll').evaluate((n) => n.scrollHeight - n.scrollTop - n.clientHeight);

test('[DT-R1] shows the tail of a run, escaped as text', async ({ page }) => {
  fs.writeFileSync(logFile('fx-unpackaged'), 'first line\n<img src=x onerror="window.__xss=1">\nlast line\n');
  await page.goto('/#/runs/fx-unpackaged/logs');
  await expect(page.getByTestId('logs')).toBeVisible();
  await expect(lines(page)).toHaveText(['first line', '<img src=x onerror="window.__xss=1">', 'last line']);
  await expect(page.getByTestId('logs').locator('img')).toHaveCount(0);
  await expect(page.getByTestId('logs-count')).toContainText('3 lines');
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test('a run with no log says so; another run replaces the lines', async ({ page }) => {
  fs.rmSync(logFile('fx-unpackaged'), { force: true });
  fs.writeFileSync(logFile('fx-stopped'), 'stopped log\n');
  await page.goto('/#/runs/fx-unpackaged/logs');
  await expect(page.getByTestId('logs-empty')).toBeVisible();
  await expect(lines(page)).toHaveCount(0);
  await page.evaluate(() => { location.hash = '#/runs/fx-stopped/logs'; });
  await expect(lines(page)).toHaveText(['stopped log']);
  await expect(page.getByTestId('logs-empty')).toBeHidden();
});

test('[DT-R1] follows while active; scrolling up stops following; the toggle resumes', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 500 });
  const res = await request.post('/api/runs', { data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 30 } } });
  const { run_id: id } = await res.json();
  const file = logFile(id);
  fs.writeFileSync(file, `${Array.from({ length: 200 }, (_, i) => `line ${i}`).join('\n')}\n`);
  await page.goto(`/#/runs/${id}/logs`);
  await expect(lines(page).first()).toBeVisible({ timeout: 15_000 });
  const follow = page.getByTestId('logs-follow');
  await expect(follow).toHaveAttribute('aria-checked', 'true');
  await expect(follow).toHaveText(/follow/i);
  await expect.poll(() => scrolled(page), { timeout: 10_000 }).toBeLessThan(5);
  fs.appendFileSync(file, 'more 1\nmore 2\n');
  await expect(lines(page).last()).toHaveText('more 2', { timeout: 10_000 });
  await expect.poll(() => scrolled(page), { timeout: 10_000 }).toBeLessThan(5);

  await page.getByTestId('logs-scroll').evaluate((n) => { n.scrollTop = 0; });
  await expect(follow).toHaveAttribute('aria-checked', 'false');
  fs.appendFileSync(file, 'more 3\n');
  await expect(lines(page).last()).toHaveText('more 3', { timeout: 10_000 });
  expect(await page.getByTestId('logs-scroll').evaluate((n) => n.scrollTop)).toBe(0);
  await follow.click();
  await expect(follow).toHaveAttribute('aria-checked', 'true');
  await expect.poll(() => scrolled(page)).toBeLessThan(5);
  await follow.click();
  await expect(follow).toHaveAttribute('aria-checked', 'false');
  await follow.click();
  await answerPending(request, id);
});

test('new lines append: a selection and an old line survive; a shifted tail trims old lines', async ({ page }) => {
  let body = { lines: ['a', 'b', 'c'], bytes: 6 };
  await page.route('**/api/runs/fx-stopped/log*', (route) => route.fulfill({ json: body }));
  await page.route('**/api/runs/fx-stopped', async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    await route.fulfill({ response: res, json: { ...run, status: 'running', failure: null, actions: ['stop'], action_blockers: {} } });
  });
  await page.clock.install();
  await page.goto('/#/runs/fx-stopped/logs');
  await expect(lines(page)).toHaveText(['a', 'b', 'c']);
  await lines(page).nth(1).evaluate((n) => { n.dataset.keep = 'yes'; });

  body = { lines: ['a', 'b', 'c', 'd'], bytes: 8 };
  await page.clock.runFor(1000);
  await expect(lines(page)).toHaveText(['a', 'b', 'c', 'd']);
  await expect(lines(page).nth(1)).toHaveAttribute('data-keep', 'yes');

  body = { lines: ['b', 'c', 'd', 'e'], bytes: 10 };
  await page.clock.runFor(1000);
  await expect(lines(page)).toHaveText(['b', 'c', 'd', 'e']);
  await expect(lines(page).first()).toHaveAttribute('data-keep', 'yes');

  body = { lines: ['x', 'y'], bytes: 3 }; // rotated: nothing in common
  await page.clock.runFor(1000);
  await expect(lines(page)).toHaveText(['x', 'y']);

  await page.route('**/api/runs/fx-stopped/log*', (route) => route.fulfill({ json: body }));
  const before = await lines(page).first().evaluate((n) => { n.dataset.same = 'yes'; return n.textContent; });
  await page.clock.runFor(1000); // same bytes: no work
  await expect(lines(page).first()).toHaveAttribute('data-same', 'yes');
  expect(before).toBe('x');
});

test('a failed read keeps the lines; a late answer for another run is dropped', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  fs.writeFileSync(logFile('fx-stopped'), 'kept\n');
  await page.goto('/#/runs/fx-stopped/logs');
  await expect(lines(page)).toHaveText(['kept']);

  let release;
  const held = new Promise((r) => { release = r; });
  fs.writeFileSync(logFile('fx-crashed'), 'late\n');
  await page.route('**/api/runs/fx-crashed/log*', async (route) => {
    await held;
    await route.continue();
  });
  await page.evaluate(() => { location.hash = '#/runs/fx-crashed/logs'; });
  const late = page.waitForResponse('**/api/runs/fx-crashed/log*');
  await page.evaluate(() => { location.hash = '#/runs/fx-stopped/logs'; });
  await expect(lines(page)).toHaveText(['kept']);
  release();
  await (await late).finished();
  await page.evaluate(() => fetch('/health'));
  await expect(lines(page)).toHaveText(['kept']);

  await page.route('**/api/runs/fx-stopped/log*', (route) => route.fulfill({ status: 500, body: 'down' }));
  await page.getByTestId('view-tab-graph').click();
  await page.getByTestId('view-tab-logs').click();
  await expect(lines(page)).toHaveText(['kept']);
});

test('nothing fetched while another view is showing', async ({ page }) => {
  let reads = 0;
  page.on('request', (r) => { if (/\/log(\?|$)/.test(r.url())) reads += 1; });
  await page.goto('/#/runs/fx-stopped');
  await expect(page.getByTestId('failure-error')).toBeVisible();
  expect(reads).toBe(0);
  await page.getByTestId('view-tab-logs').click();
  await expect(page.getByTestId('logs')).toBeVisible();
  await expect.poll(() => reads).toBe(1);
});
