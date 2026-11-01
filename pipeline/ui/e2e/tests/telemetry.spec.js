// Telemetry strip and timeline (spec 2026-10-31-pipeline-console-ui §9).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, freshServerState } from '../fixtures.js';

const FX = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../tests/fixtures/ui/processed');
const TOKENS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'];

/** Expected totals from the fixture files themselves (spec §3.5 telemetry rules). */
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

const value = async (loc) => Number(await loc.getAttribute('data-value'));

test.beforeEach(() => freshServerState());

test('strip totals match the fx-done fixture files, compactly formatted', async ({ page }) => {
  const exp = expectedTotals('fx-done');
  await page.goto('/#/runs/fx-done');
  const strip = page.getByTestId('tele-strip');
  await expect(strip.getByTestId('tele-total')).toHaveAttribute('data-value', String(exp.total));
  expect(await value(strip.getByTestId('tele-work'))).toBeCloseTo(exp.work, 6);
  const sum = Object.values(exp.tokens).reduce((a, b) => a + b, 0);
  expect(await value(strip.getByTestId('tele-tokens'))).toBe(sum);
  for (const [seg, k] of [['input', 'input_tokens'], ['output', 'output_tokens'], ['cache_read', 'cache_read_tokens'],
    ['cache_creation', 'cache_creation_tokens']]) {
    expect(await value(strip.getByTestId(`tele-tok-${seg}`))).toBe(exp.tokens[k]);
  }
  expect(await value(strip.getByTestId('tele-cost'))).toBeCloseTo(exp.cost, 9);
  await expect(strip.getByTestId('tele-cost')).toBeVisible();
  // 298 s wall, 178 s work, 145546 tokens, $0.747
  await expect(strip.getByTestId('tele-total')).toHaveText('4m 58s');
  await expect(strip.getByTestId('tele-work')).toHaveText('2m 58s');
  await expect(strip.getByTestId('tele-tokens')).toHaveText('145.5k');
  await expect(strip.getByTestId('tele-cost')).toHaveText('$0.75');
  await expect(strip.getByTestId('tele-total')).toHaveAttribute('data-live', 'false');
  // stacked bar: segment widths proportional to the split
  const w = async (seg) => (await strip.getByTestId(`tele-tok-${seg}`).boundingBox()).width;
  expect(await w('input')).toBeGreaterThan(await w('output'));
});

test('timeline has one bar per started node, parallel branches overlap, pending nodes have none', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const tl = page.getByTestId('tele-timeline');
  await expect(tl.getByTestId('tl-bar-research')).toHaveAttribute('data-state', 'done');
  await expect(tl.getByTestId('tl-bar-transcribe')).toBeHidden();
  const box = (id) => tl.getByTestId(`tl-bar-${id}`).boundingBox();
  const [writer, research, extract] = [await box('writer'), await box('research'), await box('extract')];
  expect(writer.x).toBeLessThan(research.x + research.width);
  expect(research.x).toBeLessThan(writer.x + writer.width);
  expect(writer.y).not.toBe(research.y);
  expect(extract.x + extract.width).toBeLessThanOrEqual(writer.x);
  await page.getByTestId('run-fx-failed').click();
  await expect(tl.getByTestId('tl-bar-research')).toHaveAttribute('data-state', 'error');
  await expect(tl.getByTestId('tl-bar-summarizer')).toBeHidden();
});

test('legacy run renders from runs.jsonl alone, cost hidden at 0', async ({ page }) => {
  await page.goto('/#/runs/fx-legacy');
  const strip = page.getByTestId('tele-strip');
  await expect(strip.getByTestId('tele-tokens')).toHaveText(/^\d+(\.\d)?M$/);
  await expect(strip.getByTestId('tele-cost')).toBeHidden();
  const tl = page.getByTestId('tele-timeline');
  await expect(tl.getByTestId('tl-bar-transcribe')).toBeVisible();
  await expect(tl.getByTestId('tl-bar-research')).toHaveAttribute('data-state', 'done');
  await expect(tl.getByTestId('tl-bar-bv_youtube')).toBeHidden();
});

test('hovering a bar highlights its DAG node and shows duration, tokens and model', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const tip = page.getByTestId('tele-tip');
  await expect(tip).toBeHidden();
  await page.getByTestId('tl-bar-research').hover();
  await expect(page.getByTestId('node-research')).toHaveAttribute('data-highlight', '');
  await expect(tip).toBeVisible();
  await expect(tip).toContainText('1m 27s');
  await expect(tip).toContainText('104.8k');
  await expect(tip).toContainText('claude-sonnet-5-5');
  // no runs.jsonl row: duration is the bar span, tokens 0, no model
  await page.getByTestId('tl-bar-bv_youtube').hover();
  await expect(page.getByTestId('node-research')).not.toHaveAttribute('data-highlight', '');
  await expect(page.getByTestId('node-bv_youtube')).toHaveAttribute('data-highlight', '');
  await expect(tip).toContainText('0.2s');
  await expect(tip).not.toContainText('claude');
  await page.getByTestId('tele-strip').hover();
  await expect(tip).toBeHidden();
  await expect(page.locator('[data-highlight]')).toHaveCount(0);
  // keyboard focus does the same
  await page.getByTestId('tl-bar-extract').focus();
  await expect(page.getByTestId('node-extract')).toHaveAttribute('data-highlight', '');
  await page.getByTestId('tl-bar-extract').blur();
  await expect(tip).toBeHidden();
});

test('a live paused node is hatched and bars and clock grow on a fake run', async ({ page, request }) => {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'hitl', step_ms: 800 } },
  });
  expect(res.status()).toBe(201);
  const { run_id: id } = await res.json();
  await page.goto(`/#/runs/${id}`);
  const bar = page.getByTestId('tl-bar-kb_curator');
  const total = page.getByTestId('tele-total');
  await expect(bar).toHaveAttribute('data-state', 'paused', { timeout: 20_000 });
  await expect(total).toHaveAttribute('data-live', 'true');
  const [w0, t0] = [Number(await bar.getAttribute('data-span')), await value(total)];
  await expect.poll(async () => Number(await bar.getAttribute('data-span')), { timeout: 10_000 }).toBeGreaterThan(w0);
  expect(await value(total)).toBeGreaterThan(t0);
  await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  await expect(bar).toHaveAttribute('data-state', 'done');
  await expect(total).toHaveAttribute('data-live', 'false');
});

test('a long live wait reads in hours and its paused bar is hatched', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/#/runs/fx-hitl');
  await expect(page.getByTestId('tl-bar-kb_curator')).toHaveAttribute('data-state', 'paused');
  await expect(page.getByTestId('tl-bar-kb_curator')).toHaveCSS('background-image', /repeating-linear-gradient/);
  await expect(page.getByTestId('tele-total')).toHaveText(/^\d+h \d\dm$/);
  await expect(page.getByTestId('tele-total')).toHaveAttribute('data-live', 'true');
});

test('a run with no timings or tokens renders placeholders without NaN', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const run = await (await route.fetch()).json();
    for (const n of Object.values(run.nodes)) Object.assign(n, { state: 'pending', started_at: null, ended_at: null });
    run.telemetry = {
      total_s: null, work_s: null, cost_usd: 0, per_node: {},
      tokens: { input: 0, output: 0, cache_read: 0, cache_creation: 0 },
    };
    await route.fulfill({ json: run });
  });
  await page.goto('/#/runs/fx-done');
  const strip = page.getByTestId('tele-strip');
  await expect(strip.getByTestId('tele-total')).toHaveText('–');
  await expect(strip.getByTestId('tele-work')).toHaveText('–');
  await expect(strip.getByTestId('tele-tokens')).toHaveText('0');
  await expect(strip.getByTestId('tele-cost')).toBeHidden();
  await expect(page.getByTestId('tele-timeline').locator('[data-testid^="tl-bar-"]:visible')).toHaveCount(0);
  await expect(page.getByTestId('tele-strip')).not.toContainText('NaN');
});

// PR screenshots, opt-in: PFW_SHOTS=<dir> npm test -- telemetry
test.describe('screenshots', () => {
  test.skip(!process.env.PFW_SHOTS, 'screenshots only when PFW_SHOTS names an output dir');
  const shot = (page, name) => page.screenshot({ path: path.join(process.env.PFW_SHOTS, `telemetry-${name}.png`) });
  const SHOTS = [
    ['done-hover', 'fx-done', false, { width: 1280, height: 720 }],
    ['live-paused', 'fx-hitl', true, { width: 1280, height: 720 }],
    ['legacy', 'fx-legacy', false, { width: 1280, height: 720 }],
    ['phone', 'fx-done', false, { width: 390, height: 900 }],
  ];
  for (const [name, run, live, viewport] of SHOTS) {
    test(`shot ${name}`, async ({ page }) => {
      freshServerState({ live });
      await page.setViewportSize(viewport);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(`/#/runs/${run}`);
      await expect(page.getByTestId('tl-bar-extract')).toBeVisible();
      if (name === 'done-hover') await page.getByTestId('tl-bar-research').hover();
      await shot(page, name);
    });
  }
});
