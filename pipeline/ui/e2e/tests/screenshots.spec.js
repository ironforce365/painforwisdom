// PR screenshots, opt-in: PFW_SHOTS=<dir> npm test -- screenshots
import path from 'node:path';
import { test, expect, freshServerState } from '../fixtures.js';

const DIR = process.env.PFW_SHOTS;
test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');
test.use({ viewport: { width: 1280, height: 640 } });

const shoot = (page, name) => page.screenshot({ path: path.join(DIR, `${name}.png`) });

const RUNS = [
  ['done-skipped', 'fx-done', 'summarizer', 'done', false],
  ['error', 'fx-failed', 'research', 'error', false],
  ['paused', 'fx-hitl', 'kb_curator', 'paused', true],
  ['running-pending', 'fx-running', 'extract', 'running', true],
];

for (const [name, run, node, state, live] of RUNS) {
  test(`shot ${name}`, async ({ page }) => {
    freshServerState({ live });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`/#/runs/${run}`);
    await expect(page.getByTestId(`node-${node}`)).toHaveAttribute('data-state', state);
    await shoot(page, name);
  });
}

test('shot empty and offline', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 500/);
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.route('**/api/graph', (route) => route.fulfill({ status: 500, body: 'down' }));
  await page.goto('/');
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await expect(page.getByTestId('error-badge')).toBeVisible();
  await shoot(page, 'empty-offline');
});

test('shot phone width', async ({ page }) => {
  freshServerState();
  await page.setViewportSize({ width: 390, height: 640 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/runs/fx-failed');
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'error');
  await shoot(page, 'phone');
});
