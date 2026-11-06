// Metrics bar (spec 2026-11-05-console-end-to-end §4): one line, expands to a table.
import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

const m = (page, name) => page.getByTestId(`metrics-${name}`);
const value = async (loc) => Number(await loc.getAttribute('data-value'));
const cell = (page, node, col) => m(page, `row-${node}`).locator(`[data-col="${col}"]`);

for (const [width, height] of [[1280, 720], [3440, 1440]]) {
  test(`[DT-R1] collapsed it is one line, at most 40 px, at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/#/runs/fx-done');
    const bar = m(page, 'bar');
    await expect(bar).toBeVisible();
    expect((await bar.boundingBox()).height).toBeLessThanOrEqual(40);
    await expect(m(page, 'tokens')).toBeVisible();
    await expect(m(page, 'cache')).toBeVisible();
    await expect(m(page, 'cost')).toBeVisible();
    await expect(m(page, 'time')).toBeVisible();
    await expect(m(page, 'toggle')).toBeVisible();
    // with the inspector docked too
    await page.getByTestId('node-writer').click();
    await expect(page.getByTestId('drawer')).toBeVisible();
    expect((await bar.boundingBox()).height).toBeLessThanOrEqual(40);
    await expect(m(page, 'cost')).toBeVisible();
    await expect(m(page, 'toggle')).toBeVisible();
    const b = await bar.boundingBox();
    const c = await m(page, 'cost').boundingBox();
    expect(c.x + c.width).toBeLessThanOrEqual(b.x + b.width);
  });
}

test('[DT-R1] token total is input + output; cache ratio and fresh input follow from it', async ({ page, request }) => {
  const run = await (await request.get('/api/runs/fx-done')).json();
  const { input, output, cache_read: read, cache_creation: write } = run.telemetry.tokens;
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'tokens')).toHaveAttribute('data-value', String(input + output));
  await expect(m(page, 'tokens')).toHaveText('82.2k');
  expect(await value(m(page, 'output'))).toBe(output);
  expect(await value(m(page, 'fresh'))).toBe(input - read - write);
  await expect(m(page, 'fresh')).toHaveText('9.1k');
  expect(await value(m(page, 'cache'))).toBeCloseTo(read / input, 6);
  await expect(m(page, 'cache')).toHaveText('58%');
  await expect(m(page, 'calls')).toHaveText('6');
  await expect(m(page, 'time')).toHaveText('4m 58s');
  await expect(m(page, 'work')).toHaveText('2m 58s');
  await expect(m(page, 'bar')).toHaveAttribute('data-scope', 'run');
});

test('labels are text', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const bar = m(page, 'bar');
  for (const word of ['Tokens', 'Cache', 'Cost', 'Time']) await expect(bar).toContainText(word);
  await expect(m(page, 'toggle')).toHaveText(/details/i);
});

test('[DT-D6] a billed cost has no mark; an estimate is ≈ with a tooltip; a null is a dash', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'cost')).toHaveText('$0.75');
  await expect(m(page, 'cost')).toHaveAttribute('data-estimated', 'false');

  await page.getByTestId('run-fx-unpackaged').click();
  await expect(m(page, 'cost')).toHaveText('≈$0.09');
  await expect(m(page, 'cost')).toHaveAttribute('data-estimated', 'true');
  await expect(m(page, 'cost')).toHaveAttribute('title', /API-equivalent.*subscription/);
  await m(page, 'toggle').click();
  await expect(cell(page, 'extract', 'cost')).toHaveText('≈$0.01');
  await expect(cell(page, 'kb_curator', 'cost')).toHaveText('≈$0.08');
  await expect(cell(page, 'writer', 'cost')).toHaveText('–'); // tokens, no known price
  await expect(cell(page, 'writer', 'tokens')).not.toHaveText('–');
  await expect(cell(page, 'writer', 'cost')).toHaveAttribute('title', /No price/);

  await page.getByTestId('run-fx-legacy').click();
  await expect(m(page, 'cost')).toHaveText('–');
  await expect(m(page, 'cost')).toBeVisible();
  await expect(m(page, 'cost')).toHaveAttribute('title', /No price/);
  await expect(m(page, 'bar')).not.toContainText('$0.00');
});

test('a cost under a cent keeps its digits; a node that spent nothing shows a dash', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    run.telemetry.cost_usd = 0.004;
    await route.fulfill({ response: res, json: run });
  });
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'cost')).toHaveText('$0.004');
  await m(page, 'toggle').click();
  await expect(cell(page, 'notion_blog', 'cost')).toHaveText('–');
  await expect(cell(page, 'notion_blog', 'cost')).toHaveAttribute('title', /Nothing billed/);
  await expect(cell(page, 'research', 'cost')).toHaveAttribute('title', /Billed/);
});

test('expands to a table sorted by tokens, and remembers it', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const toggle = m(page, 'toggle');
  await expect(m(page, 'table')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(m(page, 'table')).toBeVisible();
  const order = await m(page, 'table').locator('tbody tr').evaluateAll((rows) => rows.map((r) => [r.dataset.testid, Number(r.querySelector('[data-col="tokens"]').dataset.value)]));
  expect(order[0][0]).toBe('metrics-row-research');
  for (let i = 1; i < order.length; i += 1) expect(order[i][1]).toBeLessThanOrEqual(order[i - 1][1]);
  expect(order).toHaveLength(16);
  await expect(cell(page, 'research', 'tokens')).toHaveText('54.3k');
  await expect(cell(page, 'research', 'state')).toHaveText('done');
  await expect(cell(page, 'research', 'time')).toHaveText('1m 27s');
  await expect(cell(page, 'research', 'model')).toHaveText('claude-sonnet-5-5');
  await expect(cell(page, 'research', 'cache')).toHaveText('75%');
  await expect(cell(page, 'research', 'cost')).toHaveText('$0.40');
  await expect(cell(page, 'transcribe', 'tokens')).toHaveText('–');
  await expect(cell(page, 'transcribe', 'cache')).toHaveText('–');
  await expect(cell(page, 'bv_youtube', 'time')).toHaveText('0.2s'); // no C4 row: the node's own duration
  expect((await m(page, 'span-research').boundingBox()).width).toBeGreaterThan(0);
  await expect(m(page, 'span-transcribe')).toBeHidden(); // never started

  await page.reload();
  await expect(m(page, 'table')).toBeVisible();
  await toggle.click();
  await expect(m(page, 'table')).toBeHidden();
  await page.reload();
  await expect(m(page, 'bar')).toBeVisible();
  await expect(m(page, 'table')).toBeHidden();
});

test('expanded the table keeps the graph on screen and scrolls itself', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/#/runs/fx-done');
  await m(page, 'toggle').click();
  const [t, d] = [await m(page, 'table').boundingBox(), await page.getByTestId('dag').boundingBox()];
  expect(d.y + d.height).toBeLessThanOrEqual(t.y + 1);
  expect(d.height).toBeGreaterThan(200);
  const viewport = page.viewportSize();
  const b = await page.locator('.metrics').boundingBox();
  expect(b.y + b.height).toBeLessThanOrEqual(viewport.height + 1);
});

test('blocked localStorage: closed by default, the toggle still works', async ({ page }) => {
  await page.addInitScript(() => {
    const blocked = () => { throw new Error('blocked'); };
    Storage.prototype.getItem = blocked;
    Storage.prototype.setItem = blocked;
  });
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'table')).toBeHidden();
  await m(page, 'toggle').click();
  await expect(m(page, 'table')).toBeVisible();
});

test('a row selects its node and highlights it on hover', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await m(page, 'toggle').click();
  const row = m(page, 'row-writer');
  await row.hover();
  await expect(page.getByTestId('node-writer')).toHaveAttribute('data-highlight', '');
  await m(page, 'row-research').hover();
  await expect(page.getByTestId('node-writer')).not.toHaveAttribute('data-highlight', '');
  await m(page, 'bar').hover();
  await expect(page.locator('[data-highlight]')).toHaveCount(0);
  await row.click();
  await expect(page.getByTestId('drawer')).toHaveAttribute('data-node', 'writer');
  await expect(page.getByTestId('node-writer')).toHaveAttribute('aria-pressed', 'true');
  await expect(row).toHaveAttribute('aria-selected', 'true');
  await m(page, 'row-writer').getByRole('button').focus();
  await page.keyboard.press('Enter');
  await expect(row).toHaveAttribute('aria-selected', 'true');
});

test('stays visible, and at the same place, in every view', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const y = (await m(page, 'bar').boundingBox()).y;
  for (const view of ['output', 'logs', 'graph']) {
    await page.getByTestId(`view-tab-${view}`).click();
    await expect(m(page, 'bar')).toBeVisible();
    expect((await m(page, 'bar').boundingBox()).y).toBeCloseTo(y, 0);
  }
});

test('a poll does not drop a selection in the table', async ({ page }) => {
  freshServerState({ live: true });
  let polls = 0;
  page.on('response', (r) => { if (r.url().endsWith('/api/runs/fx-running')) polls += 1; });
  await page.goto('/#/runs/fx-running');
  await m(page, 'toggle').click();
  const model = cell(page, 'extract', 'model');
  await expect(model).toHaveText(/\S/);
  const length = await model.evaluate((n) => {
    const r = document.createRange();
    r.selectNodeContents(n);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
    return getSelection().toString().length;
  });
  const seen = polls;
  await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(seen + 1);
  expect(await page.evaluate(() => getSelection().toString().length)).toBe(length);
});

test('millions read as M; a run with no elapsed span divides by nothing', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    run.telemetry.tokens.input = 2_400_000;
    run.telemetry.total_s = 0;
    for (const n of Object.values(run.nodes)) Object.assign(n, { started_at: null, ended_at: null });
    Object.assign(run.nodes.writer, { started_at: run.started_at, ended_at: run.started_at });
    await route.fulfill({ response: res, json: run });
  });
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'tokens')).toHaveText(/^2\.4M$/);
  await m(page, 'toggle').click();
  await expect(m(page, 'span-writer')).toBeAttached();
  expect(await m(page, 'span-writer').evaluate((n) => n.style.left)).toBe('0%');
});

test('hidden until a run is selected', async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await expect(m(page, 'bar')).toBeHidden();
});
