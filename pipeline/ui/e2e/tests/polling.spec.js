import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

function counter(page, test) {
  const c = { n: 0 };
  page.on('request', (r) => { if (test(new URL(r.url()).pathname)) c.n += 1; });
  return c;
}

test('polls a live run every second until it ends, then stops; list refreshes every 5 s', async ({ page, request }) => {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: 30 } },
  });
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  const detail = counter(page, (p) => p === `/api/runs/${id}`);
  const list = counter(page, (p) => p === '/api/runs');
  await page.clock.install();
  await page.goto(`/#/runs/${id}`);
  await expect(page.getByTestId(`run-${id}`)).toBeVisible();
  await expect.poll(async () => {
    await page.clock.runFor(1000);
    return page.getByTestId(`run-${id}`).getAttribute('data-status');
  }, { timeout: 30_000 }).toBe('done');
  await expect(page.getByTestId('node-summarizer')).toHaveAttribute('data-state', 'done');
  await expect(page.getByTestId(`run-${id}`).getByTestId('spinner')).toBeHidden();
  expect(detail.n).toBeGreaterThan(1);
  const [d, l] = [detail.n, list.n];
  await page.clock.runFor(10_000);
  await expect.poll(() => list.n).toBeGreaterThanOrEqual(l + 2);
  expect(detail.n).toBe(d);
});

test('a slow response for a run that is no longer selected is ignored', async ({ page }) => {
  let release;
  const held = new Promise((r) => { release = r; });
  await page.route('**/api/runs/fx-done', async (route) => {
    await held;
    await route.continue();
  });
  await page.goto('/#/runs/fx-done');
  await page.getByTestId('run-fx-failed').click();
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  const stale = page.waitForResponse('**/api/runs/fx-done');
  release();
  await (await stale).finished();
  await page.evaluate(() => fetch('/health'));
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
});
