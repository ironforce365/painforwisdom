// Launch popup (spec 2026-10-31-pipeline-console-ui §7.5).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED, INBOX } from '../fixtures.js';

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

test('[DT-S1] an empty inbox says so and offers the upload', async ({ page }) => {
  freshServerState();
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/home/x/inbox', default_language: 'en', inputs: [] } }));
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await expect(page.getByTestId('launch-empty')).toContainText('inbox is empty');
  await expect(page.getByTestId('launch-path')).toHaveText('/home/x/inbox');
  await expect(page.getByTestId('launch-upload')).toHaveText('Upload');
  await expect(page.getByTestId('launch-close')).toHaveText('Close');
});

test('[DT-S2] controls read as text: start in the header, auto-approve in the popup', async ({ page }) => {
  freshServerState();
  await page.goto('/#/runs/fx-done');
  await expect(button(page)).toHaveText('Start run');
  await expect(page.locator('header.bar').getByTestId('launch-button')).toBeVisible();
  await openModal(page);
  await expect(page.getByTestId('launch-auto')).toHaveText('Auto-approve');
  await expect(tile(page, TXT)).toBeVisible();
});

test('[DT-S3] a tile warns which runs used the input and can still start', async ({ page, request }) => {
  freshServerState();
  const { inputs } = await (await request.get('/api/inputs')).json();
  const used = inputs.find((i) => i.previous_runs.length);
  const fresh = inputs.find((i) => !i.previous_runs.length);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const warned = tile(page, used.name);
  await expect(warned.getByTestId('tile-previous')).toContainText('Already ran');
  for (const id of used.previous_runs) await expect(warned.getByTestId('tile-previous')).toContainText(id);
  await expect(warned).toHaveAttribute('data-warn', 'true');
  if (fresh) await expect(tile(page, fresh.name).getByTestId('tile-previous')).toHaveCount(0);
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/runs'));
  await page.route('**/api/runs', (route) => (route.request().method() === 'POST'
    ? route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), fake: { scenario: 'ok', step_ms: 20 } }) }) : route.continue()));
  await warned.click();
  expect((await posted).status()).toBe(201);
});

test('[DT-S4] another run active: start is locked with a link to it, from the header and the popup', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/#/runs/fx-done');
  await expect(button(page)).toBeDisabled();
  await expect(button(page)).toHaveText('Run active');
  const link = page.getByTestId('launch-active-link');
  await expect(link).toContainText('fx-running');
  await link.click();
  await expect(page).toHaveURL(/#\/runs\/fx-running$/);
  await expect(page.getByTestId('run-fx-running')).toHaveAttribute('aria-pressed', 'true');
});

test('[DT-S4] a 409 from the popup links to the active run and closes on the way there', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 409/);
  capturePosts(page, { status: 409, json: { detail: 'run active', run_id: 'fx-stopped' } });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await tile(page, TXT).click();
  await expect(page.getByTestId('launch-lock')).toBeVisible();
  const link = page.getByTestId('launch-lock-link');
  await expect(link).toHaveText('fx-stopped');
  await link.click();
  await expect(modal(page)).toBeHidden();
  await expect(page).toHaveURL(/#\/runs\/fx-stopped$/);
});

test('[DT-S5] a refused upload names the file and why; the rest go on', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 409/);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await modal(page).dispatchEvent('drop', { dataTransfer: await transfer(page, [TXT, 'fine.txt']) });
  await expect(tile(page, 'fine.txt')).toBeFocused();
  await expect(page.getByTestId('launch-error')).toContainText(`${TXT}: input exists`);
  await page.getByTestId('launch-close').click();
  await openModal(page);
  await expect(page.getByTestId('launch-error')).toBeHidden();
});

test('a failure without a body shows the bare message', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 500/);
  errors.allow(/requestfailed|ERR_FAILED/);
  await page.route('**/api/inputs/*', (route) => route.abort());
  capturePosts(page, { status: 500, body: 'boom' });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await tile(page, TXT).click();
  await expect(page.getByTestId('launch-error')).toContainText('HTTP 500');
  await modal(page).dispatchEvent('drop', { dataTransfer: await transfer(page, ['x.txt']) });
  await expect(page.getByTestId('launch-error')).toContainText('x.txt:');
});

test('tiles show file name and relative age', async ({ page }) => {
  freshServerState();
  const now = Date.now() / 1000;
  const inputs = [[5, 's'], [120, 'm'], [7200, 'h'], [200_000, 'd'], [-60, 'future']].map(([ago, n]) => (
    { name: `${n}.txt`, kind: 'transcript', bytes: 1, mtime: now - ago }));
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/x/inbox', default_language: 'en', inputs } }));
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
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/x/inbox', default_language: 'en', inputs } }));
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
  expect(bodies).toEqual([{ input: TXT, auto_approve: false, language: 'en' }]);
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
  expect(bodies).toEqual([{ input: TXT, auto_approve: false, language: 'en' }]);
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
  await expect(page.getByTestId('launch-active-link')).toBeVisible();
  await expect(button(page)).toHaveAttribute('data-state', 'busy');
  await expect(button(page)).toBeDisabled();
});

test('409 locks the popup and the button until the active run ends', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 409/);
  const busy = 'fx-stopped';
  setStatus(busy, 'running');
  await page.route(`**/api/runs/${busy}`, async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    // A runner that only the 409 told this page about: the list does not name it as active.
    await route.fulfill({ response: res, json: JSON.parse(fs.readFileSync(path.join(PROCESSED, busy, 'langgraph', 'run.json'), 'utf8')).status === 'running' ? { ...run, status: 'running' } : { ...run, status: 'done' } });
  });
  capturePosts(page, { status: 409, json: { detail: 'run active', run_id: busy } });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const posted = page.waitForResponse((r) => r.request().method() === 'POST');
  await tile(page, TXT).click();
  await posted;
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
  freshServerState();
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

test('empty inbox keeps keys and focus sane', async ({ page }) => {
  freshServerState();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/home/x/inbox', default_language: 'en', inputs: [] } }));
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await expect(page.getByTestId('launch-empty')).toBeVisible();
  await expect(page.getByTestId('launch-path')).toHaveText('/home/x/inbox');
  await expect(page.getByTestId('launch-close')).toBeFocused();
  await page.keyboard.press('ArrowRight'); // no tiles: nothing to move to
  await expect(page.getByTestId('launch-close')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(modal(page)).toBeHidden();
});

test('[DT-S6] the language starts from the catalog, is sent, and goes back to the catalog on the next opening', async ({ page }) => {
  freshServerState();
  const bodies = capturePosts(page, { status: 201, json: { run_id: 'fx-done' } });
  await page.route('**/api/inputs', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), default_language: 'pt' } });
  });
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const language = page.getByTestId('launch-language');
  await expect(language).toHaveValue('pt');
  await language.fill(' es ');
  await tile(page, MP4).click();
  await expect(modal(page)).toBeHidden();
  expect(bodies).toEqual([{ input: MP4, auto_approve: false, language: 'es' }]);
  await openModal(page);
  await expect(language).toHaveValue('pt'); // a Spanish video must not silently set the next run's language
});

test('[DT-S6] a real run records the language, and a code that is not one says why', async ({ page, request, errors }) => {
  freshServerState();
  errors.allow(/status of 400/);
  const posted = () => page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/runs'));
  capturePosts(page);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const language = page.getByTestId('launch-language');
  await language.fill('Spanish');
  await tile(page, TXT).click();
  await expect(page.getByTestId('launch-error')).toContainText('language');
  await expect(modal(page)).toBeVisible();
  await language.fill('es');
  const res = posted();
  await tile(page, TXT).click();
  const { run_id: id } = await (await res).json();
  await expect.poll(async () => (await (await request.get(`/api/runs/${id}`)).json()).language).toBe('es');
  await expect(button(page)).toHaveAttribute('data-state', 'idle', { timeout: 30_000 });
});

test('[DT-S6] arrow keys move the caret inside the language field, and an upload keeps what was typed', async ({ page }) => {
  freshServerState();
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const language = page.getByTestId('launch-language');
  await language.fill('es');
  await language.press('ArrowLeft');
  await expect(language).toBeFocused();
  expect(await language.evaluate((node) => node.selectionStart)).toBe(1);
  await page.route('**/api/inputs/*', (route) => route.fulfill({ status: 201, json: {} }));
  await page.getByTestId('launch-file').setInputFiles({ name: 'new.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
  await expect(tile(page, TXT)).toBeFocused();
  await expect(language).toHaveValue('es');
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
  expect(bodies).toEqual([{ input: MP4, auto_approve: true, language: 'en' }]);
  await expect(button(page)).toHaveAttribute('data-state', 'idle'); // fx-done is not active
});

/** A DataTransfer in the page holding text files, for synthetic drag events. */
const transfer = (page, names) => page.evaluateHandle((list) => {
  const dt = new DataTransfer();
  for (const n of list) dt.items.add(new File([`dropped ${n}`], n, { type: 'text/plain' }));
  return dt;
}, names);

test('the upload button picks a file into the inbox and its tile comes first, focused', async ({ page }) => {
  freshServerState();
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  await expect(page.getByTestId('launch-file')).toBeHidden();
  const chooser = page.waitForEvent('filechooser');
  await page.getByTestId('launch-upload').click();
  await (await chooser).setFiles({ name: 'picked.txt', mimeType: 'text/plain', buffer: Buffer.from('a transcript') });
  await expect(tile(page, 'picked.txt')).toBeFocused();
  await expect(page.locator('.tile').first()).toHaveAttribute('data-testid', 'input-picked.txt');
  await expect(page.getByTestId('launch-upload')).toHaveAttribute('data-state', 'idle');
  await expect(page.getByTestId('launch-upload')).toBeEnabled();
  expect(fs.readFileSync(path.join(INBOX, 'picked.txt'), 'utf8')).toBe('a transcript');
});

test('dropping files uploads them; an existing name is refused and flags the button', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 409/);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const dt = await transfer(page, [TXT, 'dropped.txt']);
  await modal(page).dispatchEvent('dragover', { dataTransfer: dt });
  await expect(modal(page)).toHaveAttribute('data-drop', 'true');
  await modal(page).dispatchEvent('dragleave', { dataTransfer: dt });
  await expect(modal(page)).toHaveAttribute('data-drop', 'false');
  await modal(page).dispatchEvent('drop', { dataTransfer: dt });
  await expect(tile(page, 'dropped.txt')).toBeFocused();
  await expect(page.getByTestId('launch-upload')).toHaveAttribute('data-state', 'error');
  await expect(page.getByTestId('error-badge')).toBeVisible();
  expect(fs.readFileSync(path.join(INBOX, 'dropped.txt'), 'utf8')).toBe('dropped dropped.txt');
  expect(fs.readFileSync(path.join(INBOX, TXT), 'utf8')).not.toBe(`dropped ${TXT}`); // never overwritten
});

test('a drop where every upload fails leaves the tiles alone', async ({ page, errors }) => {
  freshServerState();
  errors.allow(/status of 400/);
  await page.goto('/#/runs/fx-done');
  await openModal(page);
  const listed = [];
  page.on('request', (r) => r.url().endsWith('/api/inputs') && listed.push(r));
  await modal(page).dispatchEvent('drop', { dataTransfer: await transfer(page, ['notes.md']) });
  await expect(page.getByTestId('launch-upload')).toHaveAttribute('data-state', 'error');
  await expect(page.getByTestId('error-badge')).toBeVisible();
  expect(listed).toHaveLength(0);
  expect(fs.existsSync(path.join(INBOX, 'notes.md'))).toBe(false);
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
    freshServerState();
    errors.allow(/status of 409/);
    capturePosts(page, { status: 409, json: { detail: 'run active', run_id: 'fx-stopped' } });
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
    await page.route('**/api/inputs', (route) => route.fulfill({ json: { inbox: '/home/x/state/inbox', default_language: 'en', inputs: [] } }));
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await expect(page.getByTestId('launch-empty')).toBeVisible();
    await shoot(page, 'empty');
  });

  test('shot upload drop target', async ({ page }) => {
    freshServerState();
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await modal(page).dispatchEvent('dragover', { dataTransfer: await transfer(page, ['x.txt']) });
    await expect(modal(page)).toHaveAttribute('data-drop', 'true');
    await shoot(page, 'upload-drop');
  });

  test('shot upload refused', async ({ page, errors }) => {
    freshServerState();
    errors.allow(/status of 409/);
    await page.goto('/#/runs/fx-done');
    await openModal(page);
    await modal(page).dispatchEvent('drop', { dataTransfer: await transfer(page, [TXT, 'new_upload.txt']) });
    await expect(tile(page, 'new_upload.txt')).toBeFocused();
    await expect(page.getByTestId('launch-upload')).toHaveAttribute('data-state', 'error');
    await shoot(page, 'upload-refused');
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
