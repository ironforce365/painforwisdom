import { test, expect, freshServerState } from '../fixtures.js';

test('backend unreachable at boot: badge shows, graph and list retry, badge clears on recovery', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/requestfailed: .*\/api\//);
  errors.allow(/ERR_FAILED/);
  await page.route('**/api/**', (route) => route.abort());
  await page.clock.install();
  await page.goto('/');
  const badge = page.getByTestId('error-badge');
  await expect(badge).toBeVisible();
  await expect(badge).toHaveAttribute('aria-label', /.+/);
  await page.unroute('**/api/**');
  await page.clock.runFor(1000);
  await expect(page.getByTestId('node-extract')).toBeVisible();
  await page.clock.runFor(5000);
  await expect(page.getByTestId('run-fx-done')).toBeVisible();
  await expect(badge).toBeHidden();
});

test('a 5xx while polling a live run shows the badge and polling continues', async ({ page, errors }) => {
  freshServerState({ live: true });
  errors.allow(/status of 500/);
  await page.clock.install();
  await page.goto('/#/runs/fx-running');
  await expect(page.getByTestId('node-extract')).toHaveAttribute('data-state', 'running');
  let failed = 0;
  await page.route('**/api/runs/fx-running', (route) => {
    failed += 1;
    return route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' });
  });
  await page.clock.runFor(1000);
  const badge = page.getByTestId('error-badge');
  await expect(badge).toBeVisible();
  await page.clock.runFor(1000);
  await expect.poll(() => failed).toBeGreaterThanOrEqual(2);
  await page.unroute('**/api/runs/fx-running');
  await page.clock.runFor(1000);
  await expect(badge).toBeHidden();
  await expect(page.getByTestId('node-extract')).toHaveAttribute('data-state', 'running');
});
