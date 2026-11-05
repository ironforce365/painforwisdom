// Numbers against the fixture files, and live growth (spec 2026-11-05-console-end-to-end §4).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, freshServerState, answerPending } from '../fixtures.js';

const FX = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../tests/fixtures/ui/processed');
const TOKENS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'];

/** Expected totals from the fixture files themselves. */
function expectedTotals(runId) {
  const dir = path.join(FX, runId, 'langgraph');
  const run = JSON.parse(fs.readFileSync(path.join(dir, 'run.json'), 'utf8'));
  const rows = fs.readFileSync(path.join(dir, 'runs.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  const calls = rows.flatMap((r) => (TOKENS.some((k) => k in r) ? [r] : r.per_call || []));
  const tokens = Object.fromEntries(TOKENS.map((k) => [k, calls.reduce((a, c) => a + (c[k] || 0), 0)]));
  const cost = rows.reduce((a, r) => a + (r.cost_usd ?? r.total_cost_usd ?? 0), 0);
  const total = run.ended_at - run.started_at;
  return { total, work: total - run.hitl_wait_s, tokens, cost };
}

const m = (page, name) => page.getByTestId(`metrics-${name}`);
const value = async (loc) => Number(await loc.getAttribute('data-value'));

test.beforeEach(() => freshServerState());

test('[DT-R1] totals match the fx-done fixture files, without the cache counted twice', async ({ page }) => {
  const exp = expectedTotals('fx-done');
  const { input_tokens: input, output_tokens: output, cache_read_tokens: read, cache_creation_tokens: write } = exp.tokens;
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'time')).toHaveAttribute('data-value', String(exp.total));
  expect(await value(m(page, 'work'))).toBeCloseTo(exp.work, 6);
  expect(await value(m(page, 'tokens'))).toBe(input + output);
  expect(await value(m(page, 'tokens'))).not.toBe(input + output + read + write);
  expect(await value(m(page, 'output'))).toBe(output);
  expect(await value(m(page, 'fresh'))).toBe(input - read - write);
  expect(await value(m(page, 'cache'))).toBeCloseTo(read / input, 9);
  expect(await value(m(page, 'cost'))).toBeCloseTo(exp.cost, 9);
  await expect(m(page, 'time')).toHaveText('4m 58s');
  await expect(m(page, 'work')).toHaveText('2m 58s');
  await expect(m(page, 'tokens')).toHaveText('82.2k');
  await expect(m(page, 'cost')).toHaveText('$0.75');
  await expect(m(page, 'time')).toHaveAttribute('data-live', 'false');
});

test('the table has one bar per started node, parallel branches overlap, pending nodes have none', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto('/#/runs/fx-done');
  await m(page, 'toggle').click();
  await expect(m(page, 'span-research')).toHaveAttribute('data-state', 'done');
  await expect(m(page, 'span-transcribe')).toBeHidden();
  const box = (id) => m(page, `span-${id}`).boundingBox();
  const [writer, research, extract] = [await box('writer'), await box('research'), await box('extract')];
  expect(writer.x).toBeLessThan(research.x + research.width);
  expect(research.x).toBeLessThan(writer.x + writer.width);
  expect(writer.y).not.toBe(research.y);
  expect(extract.x + extract.width).toBeLessThanOrEqual(writer.x);
  await page.getByTestId('run-fx-failed').click();
  await expect(m(page, 'span-research')).toHaveAttribute('data-state', 'error');
  await expect(m(page, 'span-summarizer')).toBeHidden();
});

test('a skipped bar is amber and striped', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await m(page, 'toggle').click();
  const bar = m(page, 'span-youtube_upload');
  await expect(bar).toHaveAttribute('data-state', 'skipped');
  await expect(bar).toHaveCSS('background-image', /repeating-linear-gradient/);
  const colours = await bar.evaluate((n) => [getComputedStyle(n).getPropertyValue('--c'), getComputedStyle(document.documentElement).getPropertyValue('--skipped')]);
  expect(colours[0].trim()).toBe(colours[1].trim());
});

test('[DT-D6] a legacy run renders from runs.jsonl alone: tokens, and no fake cost', async ({ page }) => {
  await page.goto('/#/runs/fx-legacy');
  await expect(m(page, 'tokens')).toHaveText('540.9k');
  await expect(m(page, 'cost')).toHaveText('–');
  await expect(m(page, 'cost')).toBeVisible();
  await m(page, 'toggle').click();
  await expect(m(page, 'span-transcribe')).toBeVisible();
  await expect(m(page, 'span-research')).toHaveAttribute('data-state', 'done');
  await expect(m(page, 'span-bv_youtube')).toBeHidden();
});

test('[DT-R1] a live paused node is hatched and bars and clock grow on a fake run', async ({ page, request }) => {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 800 } },
  });
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  await page.goto(`/#/runs/${id}`);
  await m(page, 'toggle').click();
  const bar = m(page, 'span-kb_curator');
  const total = m(page, 'time');
  await expect(bar).toHaveAttribute('data-state', 'paused', { timeout: 20_000 });
  await expect(total).toHaveAttribute('data-live', 'true');
  const [w0, t0] = [Number(await bar.getAttribute('data-span')), await value(total)];
  await expect.poll(async () => Number(await bar.getAttribute('data-span')), { timeout: 10_000 }).toBeGreaterThan(w0);
  expect(await value(total)).toBeGreaterThan(t0);
  await answerPending(request, id);
  await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  await expect(bar).toHaveAttribute('data-state', 'done');
  await expect(total).toHaveAttribute('data-live', 'false');
});

test('a long live wait reads in hours and its paused bar is hatched', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/#/runs/fx-hitl');
  await m(page, 'toggle').click();
  await expect(m(page, 'span-kb_curator')).toHaveAttribute('data-state', 'paused');
  await expect(m(page, 'span-kb_curator')).toHaveCSS('background-image', /repeating-linear-gradient/);
  await expect(m(page, 'time')).toHaveText(/^\d+h \d\dm$/);
  await expect(m(page, 'time')).toHaveAttribute('data-live', 'true');
});

test('a run with no timings or tokens renders placeholders without NaN', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const run = await (await route.fetch()).json();
    for (const n of Object.values(run.nodes)) Object.assign(n, { state: 'pending', started_at: null, ended_at: null });
    run.started_at = null;
    run.telemetry = {
      total_s: null, work_s: null, cost_usd: 0, cost_estimated: false, calls: 0, in_flight: 0, per_node: {},
      tokens: { input: 0, output: 0, cache_read: 0, cache_creation: 0 },
    };
    await route.fulfill({ json: run });
  });
  await page.goto('/#/runs/fx-done');
  await expect(m(page, 'time')).toHaveText('–');
  await expect(m(page, 'work')).toHaveText('–');
  await expect(m(page, 'tokens')).toHaveText('0');
  await expect(m(page, 'cache')).toHaveText('–');
  await expect(m(page, 'cost')).toHaveText('–');
  await m(page, 'toggle').click();
  await expect(m(page, 'table').locator('[data-testid^="metrics-span-"]:visible')).toHaveCount(0);
  await expect(m(page, 'bar')).not.toContainText('NaN');
  await expect(m(page, 'table')).not.toContainText('NaN');
});
