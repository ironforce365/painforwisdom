// Live debug panel (spec 2026-11-03-console-operability §5.2).
import path from 'node:path';
import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

const numberOf = async (page, id) => Number(await page.getByTestId(`debug-${id}`).getAttribute('data-value'));

async function expectFields(page, expected) {
  for (const [id, text] of Object.entries(expected)) await expect(page.getByTestId(`debug-${id}`)).toHaveText(text);
}

test('run scope shows every field of a finished run', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const panel = page.getByTestId('debug');
  await expect(panel).toHaveAttribute('data-scope', 'run');
  await expect(panel).toHaveAttribute('data-live', 'false');
  await expect(page.getByTestId('debug-scope-clear')).toBeHidden();
  await expectFields(page, {
    total: '146.8k', calls: '6', 'in-flight': '0', cost: '$0.752', elapsed: '4m 58s', work: '2m 58s', models: /claude-sonnet-5-5/,
  });
  const [input, read] = [await numberOf(page, 'input'), await numberOf(page, 'cache-read')];
  expect(await numberOf(page, 'hit')).toBeCloseTo(read / (input + read), 9);
  const models = await page.getByTestId('debug-models').textContent();
  expect(models.split(', ')).toEqual([...new Set(models.split(', '))]);
});

test('selecting a node scopes the panel; same node or chip returns to the run', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const panel = page.getByTestId('debug');
  await expect(panel).toHaveAttribute('data-scope', 'run');
  await page.getByTestId('node-research').click();
  await expect(panel).toHaveAttribute('data-scope', 'research');
  await expect(panel).toHaveAttribute('data-live', 'false');
  await expectFields(page, {
    input: '51k', output: '3.3k', 'cache-read': '38k', 'cache-creation': '12.5k', total: '104.8k', hit: '43%',
    calls: '1', 'in-flight': '0', cost: '$0.400', work: '1m 27s', models: 'claude-sonnet-5-5',
  });
  await page.getByTestId('node-research').click();
  await expect(panel).toHaveAttribute('data-scope', 'run');
  await page.getByTestId('node-extract').click();
  await expect(panel).toHaveAttribute('data-scope', 'extract');
  await expectFields(page, { hit: '0%', elapsed: '12s', work: '12s' });
  await page.getByTestId('debug-scope-clear').click();
  await expect(panel).toHaveAttribute('data-scope', 'run');
  await expect(page.getByTestId('debug-scope-clear')).toBeHidden();
  // no C4 row: work is the node's own duration, no model; never started: dashes
  await page.getByTestId('drawer-close').click(); // the drawer covers the graph's right side, where bv_youtube now sits
  await page.getByTestId('node-bv_youtube').click();
  await expectFields(page, { total: '0', hit: '–', work: '0.2s', elapsed: '0.2s', models: '–' });
  await page.getByTestId('node-transcribe').click();
  await expectFields(page, { total: '0', elapsed: '–', work: '–' });
  // another run starts at run scope
  await page.getByTestId('run-fx-failed').click();
  await expect(panel).toHaveAttribute('data-scope', 'run');
});

test('a running node is live with one call in flight and elapsed from server time', async ({ page }) => {
  freshServerState({ live: true });
  await page.emulateMedia({ reducedMotion: 'reduce' }); // the running ring's pulse never reads as stable to click
  await page.goto('/#/runs/fx-running');
  const panel = page.getByTestId('debug');
  await expect(panel).toHaveAttribute('data-live', 'true');
  await expect(page.getByTestId('debug-live-dot')).toBeVisible();
  await page.getByTestId('node-extract').click();
  await expect(panel).toHaveAttribute('data-scope', 'extract');
  await expect(panel).toHaveAttribute('data-live', 'true');
  await expectFields(page, { input: '1.2k', calls: '1', 'in-flight': '1', cost: '$0.006', work: '–', models: 'claude-sonnet-5-5' });
  expect(await numberOf(page, 'elapsed')).toBeGreaterThan(0);
  await page.getByTestId('node-transcribe').click();
  await expect(panel).toHaveAttribute('data-live', 'false');
});

test('numbers grow with the run poll on a fake run, then settle', async ({ page, request }) => {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 400 } },
  });
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  await page.goto(`/#/runs/${id}`);
  await expect.poll(() => numberOf(page, 'total'), { timeout: 20_000 }).toBeGreaterThan(0);
  const first = await numberOf(page, 'total');
  await expect.poll(() => numberOf(page, 'total'), { timeout: 20_000 }).toBeGreaterThan(first);
  await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  await expect(page.getByTestId('debug')).toHaveAttribute('data-live', 'false');
  await expect(page.getByTestId('debug-in-flight')).toHaveText('0');
});

test('a node clicked before the first poll answers holds its scope', async ({ page }) => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route('**/api/runs/fx-done', async (route) => {
    await held;
    await route.continue();
  });
  await page.goto('/#/runs/fx-done');
  const panel = page.getByTestId('debug');
  await expect(page.getByTestId('node-research')).toBeVisible();
  await expect(panel).toBeHidden();
  await page.getByTestId('node-research').click();
  await expect(panel).toHaveAttribute('data-scope', 'research');
  await expect(panel).toBeHidden();
  release();
  await expect(panel).toBeVisible();
  await expect(page.getByTestId('debug-total')).toHaveText('104.8k');
});

test('collapses to its header', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const toggle = page.getByTestId('debug-toggle');
  await expect(page.getByTestId('debug-total')).toBeVisible();
  await toggle.click();
  await expect(page.getByTestId('debug-total')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(page.getByTestId('debug-total')).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
});

test('sits in the DAG corner, and beside the drawer while it is open', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/runs/fx-done');
  const panel = page.getByTestId('debug');
  await expect(panel).toBeVisible();
  const right = async () => {
    const b = await panel.boundingBox();
    return b.x + b.width;
  };
  const dag = await page.getByTestId('dag').boundingBox();
  const box = await panel.boundingBox();
  expect(box.y - dag.y).toBeLessThan(20);
  const corner = dag.x + dag.width - (await right());
  expect(corner).toBeLessThan(20);

  await page.getByTestId('node-research').click();
  const drawer = page.getByTestId('drawer');
  const gap = async () => (await drawer.boundingBox()).x - (await right());
  await expect.poll(async () => Math.round(await gap())).toBe(10);
  const before = (await drawer.boundingBox()).x;
  await page.getByTestId('drawer-resize').focus();
  await page.keyboard.press('ArrowLeft');
  await expect.poll(async () => (await drawer.boundingBox()).x).toBeLessThan(before);
  await expect.poll(async () => Math.round(await gap())).toBe(10);

  await page.getByTestId('drawer-close').click();
  await expect.poll(async () => Math.round(dag.x + dag.width - (await right()))).toBe(Math.round(corner));
});

// PR screenshots, opt-in: PFW_SHOTS=<dir> npm test
test.describe('screenshots', () => {
  test.skip(!process.env.PFW_SHOTS, 'screenshots only when PFW_SHOTS names an output dir');
  const shot = (page, name) => page.screenshot({ path: path.join(process.env.PFW_SHOTS, `debug-${name}.png`) });
  test.use({ viewport: { width: 1440, height: 900 } });

  test('shot live run and node scope', async ({ page, request }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const res = await request.post('/api/runs', {
      data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 4000 } },
    });
    const { run_id: id } = await res.json();
    await page.goto(`/#/runs/${id}`);
    await expect.poll(() => numberOf(page, 'total'), { timeout: 20_000 }).toBeGreaterThan(0);
    await shot(page, 'live-run');
    await page.locator('[data-testid="dag"] .node[data-state="running"]').first().click();
    await expect(page.getByTestId('debug')).toHaveAttribute('data-live', 'true');
    await shot(page, 'live-node');
  });

  test('shot finished run', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/#/runs/fx-done');
    await expect(page.getByTestId('debug-total')).toHaveText('146.8k');
    await shot(page, 'done');
    await page.getByTestId('node-research').click();
    await expect(page.getByTestId('debug-total')).toHaveText('104.8k');
    await shot(page, 'done-node');
  });
});
