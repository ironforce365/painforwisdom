// PR screenshots, opt-in: PFW_SHOTS=<dir> npm test -- screenshots
import path from 'node:path';
import { test, expect, freshServerState } from '../fixtures.js';

const DIR = process.env.PFW_SHOTS;
test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');

const shoot = (page, name) => page.screenshot({ path: path.join(DIR, `${name}.png`) });

for (const [width, height] of [[1280, 720], [3440, 1440]]) {
  test.describe(`at ${width}`, () => {
    test.use({ viewport: { width, height } });
    test.beforeEach(({ page }) => page.emulateMedia({ reducedMotion: 'reduce' }));

    test('shot graph with inspector open', async ({ page }) => {
      freshServerState();
      await page.goto('/#/runs/fx-done');
      await page.getByTestId('node-writer').click();
      await expect(page.getByTestId('artifact-blog_post.md')).toHaveAttribute('data-state', 'ready');
      await shoot(page, `graph-inspector-${width}`);
    });

    test('shot error banner', async ({ page }) => {
      freshServerState();
      await page.goto('/#/runs/fx-failed');
      await expect(page.getByTestId('failure-error')).toBeVisible();
      await shoot(page, `error-banner-${width}`);
    });

    test('shot approval banner', async ({ page }) => {
      freshServerState({ live: true });
      await page.goto('/#/runs/fx-error');
      await expect(page.getByTestId('action-option-retry')).toBeVisible();
      await shoot(page, `pending-banner-${width}`);
    });

    test('shot metrics expanded', async ({ page }) => {
      freshServerState();
      await page.goto('/#/runs/fx-unpackaged');
      await page.getByTestId('metrics-toggle').click();
      await expect(page.getByTestId('metrics-table')).toBeVisible();
      await shoot(page, `metrics-expanded-${width}`);
    });

    test('shot logs', async ({ page }) => {
      freshServerState();
      await page.goto('/#/runs/fx-unpackaged/logs');
      await expect(page.getByTestId('logs')).toBeVisible();
      await shoot(page, `logs-${width}`);
    });
  });
}

test('shot phone width', async ({ page }) => {
  freshServerState();
  await page.setViewportSize({ width: 390, height: 800 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/runs/fx-failed');
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  await shoot(page, 'phone');
});
