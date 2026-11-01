// Launch popup (spec 2026-10-31-pipeline-console-ui §7.5).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const TXT = 'transcript_2026-10-30.txt';
const MP4 = 'PXL_20261030_070000000.mp4';
const modal = (page) => page.getByTestId('launch-modal');
const button = (page) => page.getByTestId('launch-button');
const tile = (page, name) => page.getByTestId(`input-${name}`);

async function openModal(page) {
  await button(page).click();
  await expect(modal(page)).toBeVisible();
}

/** Capture POST /api/runs bodies; `reply` fulfils instead of hitting the server, else a short fake step is added. */
function capturePosts(page, reply) {
  const bodies = [];
  page.route('**/api/runs', async (route) => {
    const req = route.request();
    if (req.method() !== 'POST') return route.continue();
    bodies.push(req.postDataJSON());
    if (reply) return route.fulfill(reply);
    return route.continue({ postData: JSON.stringify({ ...req.postDataJSON(), fake: { scenario: 'ok', step_ms: 20 } }) });
  });
  return bodies;
}

function setStatus(runId, status) {
  const file = path.join(PROCESSED, runId, 'langgraph', 'run.json');
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, 'utf8')), status }));
}

test('opens on click; closes on the close button, Esc and the backdrop, returning focus', async ({ page }) => {
  freshServerState();
  await page.goto('/#/runs/fx-done');
  await expect(button(page)).toHaveAttribute('data-state', 'idle');
  await openModal(page);
  await expect(modal(page)).toHaveAttribute('role', 'dialog');
  await expect(tile(page, TXT)).toHaveAttribute('data-kind', 'transcript');
  await expect(tile(page, MP4)).toHaveAttribute('data-kind', 'video');
  await expect(page.getByTestId('launch-lock')).toBeHidden();
  await page.getByTestId('launch-close').click();
  await expect(modal(page)).toBeHidden();

  await openModal(page);
  await page.keyboard.press('Escape');
  await expect(modal(page)).toBeHidden();
  await expect(button(page)).toBeFocused();

  await openModal(page);
  await page.getByTestId('launch-auto').click(); // inside the panel: stays open
  await expect(modal(page)).toBeVisible();
  await modal(page).click({ position: { x: 5, y: 5 } });
  await expect(modal(page)).toBeHidden();
});

test('closing before the inputs arrive keeps focus on the play button', async ({ page }) => {
  freshServerState();
  let release;
  const held = new Promise((r) => { release = r; });
  await page.route('**/api/inputs', async (route) => {
    await held;
    await route.continue();
  });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const loaded = page.waitForResponse((r) => r.url().endsWith('/api/inputs'));
  await page.keyboard.press('Escape');
  await expect(modal(page)).toBeHidden();
  release();
  await loaded;
  await expect(tile(page, TXT)).toBeAttached();
  await expect(button(page)).toBeFocused();
});

test('tiles show file name and relative age', async ({ page }) => {
  freshServerState();
  const now = Date.now() / 1000;
  const inputs = [[5, 's'], [120, 'm'], [7200, 'h'], [200_000, 'd'], [-60, 'future']].map(([ago, n]) => (
    { name: `${n}.txt`, kind: 'transcript', bytes: 1, mtime: now - ago }));
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/x/inbox', inputs } }));
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  for (const [name, age] of [['s', /^[56]s$/], ['m', /^2m$/], ['h', /^2h$/], ['d', /^2d$/], ['future', /^1s$/]]) {
    await expect(tile(page, `${name}.txt`).getByTestId('tile-name')).toHaveText(`${name}.txt`);
    await expect(tile(page, `${name}.txt`).getByTestId('tile-age')).toHaveText(age);
  }
});

test('arrows move between tiles, Tab is trapped, Enter starts', async ({ page }) => {
  freshServerState();
  const bodies = capturePosts(page, { status: 201, json: { run_id: 'fx-done' } });
  const inputs = [MP4, TXT].map((name) => ({ name, kind: 'transcript', bytes: 1, mtime: 0 }));
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/x/inbox', inputs } }));
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const [first, second] = [tile(page, MP4), tile(page, TXT)];
  await expect(first).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(second).toBeFocused();
  await page.keyboard.press('ArrowDown'); // wraps
  await expect(first).toBeFocused();
  await page.keyboard.press('ArrowLeft'); // wraps back
  await expect(second).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(first).toBeFocused();

  await page.keyboard.press('Tab'); // not the last: browser default
  await expect(second).toBeFocused();
  await page.keyboard.press('Tab'); // last wraps to first
  await expect(page.getByTestId('launch-auto')).toBeFocused();
  await page.keyboard.press('Shift+Tab'); // first wraps to last
  await expect(second).toBeFocused();

  await page.keyboard.press('Enter');
  await expect(modal(page)).toBeHidden();
  expect(bodies).toEqual([{ input: TXT, auto_approve: false }]);
});

test('starting a fake ok run closes the popup, selects the run and the DAG goes live to done', async ({ page }) => {
  freshServerState();
  const bodies = capturePosts(page);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/runs'));
  await tile(page, TXT).click();
  const res = await posted;
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  expect(bodies).toEqual([{ input: TXT, auto_approve: false }]);
  await expect(modal(page)).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`#/runs/${id}$`));
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  await expect(button(page)).toBeDisabled();
  await expect(page.getByTestId('node-summarizer')).toHaveAttribute('data-state', 'done', { timeout: 30_000 });
  await expect(button(page)).toHaveAttribute('data-state', 'idle', { timeout: 10_000 });
  await expect(button(page)).toBeEnabled();
});

test('a live run at boot shows the button busy', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/');
  await expect(page).toHaveURL(/#\/runs\/fx-running$/);
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  await expect(button(page)).toBeDisabled();
});

test('409 locks the popup and the button until the active run ends', async ({ page, errors }) => {
  freshServerState({ live: true });
  errors.allow(/status of 409/);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const posted = page.waitForResponse((r) => r.request().method() === 'POST');
  await tile(page, TXT).click();
  const res = await posted;
  expect(res.status()).toBe(409);
  const { run_id: busy } = await res.json();
  await expect(page.getByTestId('launch-lock')).toBeVisible();
  await expect(modal(page)).toBeVisible();
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  await expect(button(page)).toBeDisabled();
  await expect(page).toHaveURL(/#\/runs\/fx-done$/); // selection untouched
  await page.waitForResponse((r) => r.url().endsWith(`/api/runs/${busy}`)); // one check while still active
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  setStatus(busy, 'done');
  await expect(page.getByTestId('launch-lock')).toBeHidden({ timeout: 5000 });
  await expect(button(page)).toHaveAttribute('data-state', 'idle');
});

test('busy survives a failed check and clears when the active run is gone', async ({ page, errors }) => {
  freshServerState({ live: true });
  errors.allow(/status of 40[49]|net::ERR_FAILED/);
  const bodies = capturePosts(page, { status: 409, json: { detail: 'run active', run_id: 'gone' } });
  let checks = 0;
  await page.route('**/api/runs/gone', (route) => {
    checks += 1;
    return checks === 1 ? route.abort() : route.fulfill({ status: 404, json: { detail: 'not found' } });
  });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await tile(page, TXT).click();
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  await expect.poll(() => checks, { timeout: 5000 }).toBe(2);
  await expect(button(page)).toHaveAttribute('data-state', 'idle');
  expect(bodies).toHaveLength(1);
});

test('404 shakes the tile and shows the error badge', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 404/);
  capturePosts(page, { status: 404, json: { detail: 'unknown input' } });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await tile(page, MP4).click();
  await expect(tile(page, MP4)).toHaveAttribute('data-state', 'error');
  await expect(page.getByTestId('error-badge')).toBeVisible();
  await expect(modal(page)).toBeVisible();
  await expect(button(page)).toHaveAttribute('data-state', 'idle');
});

test('empty inbox shows the tray icon and the inbox path only', async ({ page }) => {
  freshServerState();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/home/x/inbox', inputs: [] } }));
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await expect(page.getByTestId('launch-empty')).toBeVisible();
  await expect(page.getByTestId('launch-empty')).toHaveText('/home/x/inbox');
  await expect(page.getByTestId('launch-close')).toBeFocused();
  await page.keyboard.press('ArrowRight'); // no tiles: nothing to move to
  await expect(page.getByTestId('launch-close')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(modal(page)).toBeHidden();
});

test('auto-approve toggle is off by default and is sent in the body', async ({ page }) => {
  freshServerState();
  const bodies = capturePosts(page, { status: 201, json: { run_id: 'fx-done' } });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const toggle = page.getByTestId('launch-auto');
  await expect(toggle).toHaveAttribute('role', 'switch');
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await tile(page, MP4).click();
  await expect(modal(page)).toBeHidden();
  expect(bodies).toEqual([{ input: MP4, auto_approve: true }]);
  await expect(button(page)).toHaveAttribute('data-state', 'idle'); // fx-done is not active
});

// PR screenshots, opt-in: PFW_SHOTS=<dir> npm test -- launch
test.describe('shots', () => {
  const DIR = process.env.PFW_SHOTS;
  test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');
  test.use({ viewport: { width: 1280, height: 640 } });
  const shoot = (page, name) => page.screenshot({ path: path.join(DIR, `launch-${name}.png`) });
  test.beforeEach(({ page }) => page.emulateMedia({ reducedMotion: 'reduce' }));

  test('shot tiles, auto-approve on', async ({ page }) => {
    freshServerState();
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await page.getByTestId('launch-auto').click();
    await expect(tile(page, TXT)).toBeVisible();
    await shoot(page, 'tiles');
  });

  test('shot locked', async ({ page, errors }) => {
    freshServerState({ live: true });
    errors.allow(/status of 409/);
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await tile(page, TXT).click();
    await expect(page.getByTestId('launch-lock')).toBeVisible();
    await shoot(page, 'locked');
  });

  test('shot error', async ({ page, errors }) => {
    freshServerState();
    errors.allow(/status of 404/);
    capturePosts(page, { status: 404, json: { detail: 'unknown input' } });
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await tile(page, MP4).click();
    await expect(page.getByTestId('error-badge')).toBeVisible();
    await shoot(page, 'error');
  });

  test('shot empty inbox', async ({ page }) => {
    freshServerState();
    await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/home/x/state/inbox', inputs: [] } }));
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await expect(page.getByTestId('launch-empty')).toBeVisible();
    await shoot(page, 'empty');
  });

  test('shot busy button', async ({ page }) => {
    freshServerState({ live: true });
    await page.goto('/#/runs/fx-running');
    await expect(button(page)).toHaveAttribute('data-state', 'busy');
    await shoot(page, 'busy');
  });

  test('shot phone', async ({ page }) => {
    freshServerState();
    await page.setViewportSize({ width: 390, height: 640 });
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await expect(tile(page, TXT)).toBeVisible();
    await shoot(page, 'phone');
  });
});

test('inputs failing to load shows the error badge and closes the popup', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 500/);
  await page.route('**/api/inputs', (route) => route.fulfill({ status: 500, body: 'down' }));
  await page.goto('/#/runs/fx-done');
  await button(page).click();
  await expect(page.getByTestId('error-badge')).toBeVisible();
  await expect(modal(page)).toBeHidden();
});
