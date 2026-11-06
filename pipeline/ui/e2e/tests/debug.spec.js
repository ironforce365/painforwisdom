// Node scope, ex debug panel (spec 2026-11-05-console-end-to-end §4): selecting a node scopes the metrics bar.
import { test, expect, freshServerState, answerPending } from '../fixtures.js';

test.beforeEach(() => freshServerState());

const m = (page, name) => page.getByTestId(`metrics-${name}`);
const numberOf = async (page, id) => Number(await m(page, id).getAttribute('data-value'));

async function expectFields(page, expected) {
  for (const [id, text] of Object.entries(expected)) await expect(m(page, id)).toHaveText(text);
}

test('[DT-R1] run scope shows every field of a finished run', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 800 });
  await page.goto('/#/runs/fx-done');
  const bar = m(page, 'bar');
  await expect(bar).toHaveAttribute('data-scope', 'run');
  await expect(bar).toHaveAttribute('data-live', 'false');
  await expect(m(page, 'scope-clear')).toBeHidden();
  await expect(m(page, 'live')).toBeHidden();
  await expectFields(page, {
    tokens: '82.2k', calls: '6', inflight: '0', cost: '$0.75', time: '4m 58s', work: '2m 58s', models: /claude-sonnet-5-5/,
  });
  const models = await m(page, 'models').textContent();
  expect(models.split(', ')).toEqual([...new Set(models.split(', '))]);
});

test('[DT-R2] selecting a node scopes the bar; the chip or closing the inspector returns to the run', async ({ page }) => {
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto('/#/runs/fx-done');
  const bar = m(page, 'bar');
  await expect(bar).toHaveAttribute('data-scope', 'run');
  await page.getByTestId('node-research').click();
  await expect(bar).toHaveAttribute('data-scope', 'research');
  await expect(bar).toHaveAttribute('data-live', 'false');
  await expect(m(page, 'scope-clear')).toContainText('Research');
  await expectFields(page, {
    tokens: '54.3k', fresh: '500', output: '3.3k', cache: '75%', calls: '1', inflight: '0', cost: '$0.40', work: '1m 27s', models: 'claude-sonnet-5-5',
  });
  await m(page, 'scope-clear').click();
  await expect(bar).toHaveAttribute('data-scope', 'run');
  await expect(page.getByTestId('drawer')).toBeHidden();
  await expect(m(page, 'scope-clear')).toBeHidden();
  await page.getByTestId('node-extract').click();
  await expect(bar).toHaveAttribute('data-scope', 'extract');
  await expectFields(page, { cache: '0%', time: '12s', work: '12s' });
  await page.getByTestId('drawer-close').click();
  await expect(bar).toHaveAttribute('data-scope', 'run');
  // no C4 row: work is the node's own duration, no model; never started: dashes
  await page.getByTestId('node-bv_youtube').click();
  await expectFields(page, { tokens: '0', cache: '–', work: '0.2s', time: '0.2s', models: '–', cost: '–' });
  await page.getByTestId('node-transcribe').click();
  await expectFields(page, { tokens: '0', time: '–', work: '–' });
  // another run starts at run scope
  await page.evaluate(() => { location.hash = '#/runs/fx-failed'; });
  await expect(bar).toHaveAttribute('data-scope', 'run');
});

test('a running node is live with one call in flight and elapsed from server time', async ({ page }) => {
  freshServerState({ live: true });
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.emulateMedia({ reducedMotion: 'reduce' }); // the running ring's pulse never reads as stable to click
  await page.goto('/#/runs/fx-running');
  const bar = m(page, 'bar');
  await expect(bar).toHaveAttribute('data-live', 'true');
  await expect(m(page, 'live')).toBeVisible();
  await page.getByTestId('node-extract').click();
  await expect(bar).toHaveAttribute('data-scope', 'extract');
  await expect(bar).toHaveAttribute('data-live', 'true');
  await expectFields(page, { calls: '1', inflight: '1', cost: '$0.006', work: '–', models: 'claude-sonnet-5-5' });
  expect(await numberOf(page, 'time')).toBeGreaterThan(0);
  await page.getByTestId('node-transcribe').click();
  await expect(bar).toHaveAttribute('data-live', 'false');
});

test('[DT-R1] numbers grow with the run poll on a fake run, then settle', async ({ page, request }) => {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 400 } },
  });
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto(`/#/runs/${id}`);
  await expect.poll(() => numberOf(page, 'tokens'), { timeout: 20_000 }).toBeGreaterThan(0);
  const first = await numberOf(page, 'tokens');
  // The approval holds the run: a first reading taken at the pause can only grow once it is answered.
  await answerPending(request, id);
  await expect.poll(() => numberOf(page, 'tokens'), { timeout: 20_000 }).toBeGreaterThan(first);
  await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  await expect(m(page, 'bar')).toHaveAttribute('data-live', 'false');
  await expect(m(page, 'inflight')).toHaveText('0');
});

test('a node clicked before the first poll answers holds its scope', async ({ page }) => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route('**/api/runs/fx-done', async (route) => {
    await held;
    await route.continue();
  });
  await page.goto('/#/runs/fx-done');
  const bar = m(page, 'bar');
  await expect(page.getByTestId('node-research')).toBeVisible();
  await expect(bar).toBeHidden();
  await page.getByTestId('node-research').click();
  await expect(bar).toHaveAttribute('data-scope', 'research');
  await expect(bar).toBeHidden();
  release();
  await expect(bar).toBeVisible();
  await expect(m(page, 'tokens')).toHaveText('54.3k');
});
