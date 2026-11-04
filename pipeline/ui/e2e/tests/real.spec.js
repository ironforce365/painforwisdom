// @real sandbox runs through the console (spec 2026-10-31-pipeline-console-ui §10.3). Opt-in: `npm run test:real`.
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const PYTHON = process.env.PYTHON || 'python';
const SHOTS = process.env.PFW_SHOTS;
const RUN_MS = 50 * 60_000;

const RUNS = [
  { kind: 'transcript', input: 'transcript_2026-04-14.txt', transcribe: 'skipped' },
  { kind: 'video', input: path.basename(process.env.PFW_REAL_VIDEO || 'video.mp4'), transcribe: 'done' },
];

test.describe.configure({ mode: 'serial' });

for (const r of RUNS) {
  test(`@real ${r.kind} run reaches PASS through the console`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
    execFileSync('bash', ['tests/sandbox_reset.sh'], { cwd: REPO, stdio: 'inherit' });

    await page.goto('/');
    await page.getByTestId('launch-button').click();
    const auto = page.getByTestId('launch-auto');
    if ((await auto.getAttribute('aria-checked')) !== 'true') await auto.click();
    await expect(auto).toHaveAttribute('aria-checked', 'true');
    const launched = page.waitForResponse((res) => res.url().endsWith('/api/runs') && res.request().method() === 'POST');
    await page.getByTestId(`input-${r.input}`).click();
    const { run_id: runId } = await (await launched).json();
    console.log(`[real] ${r.kind} run_id=${runId}`);

    const item = page.getByTestId(`run-${runId}`);
    await expect(item).toHaveAttribute('data-status', 'done', { timeout: RUN_MS });
    const runJson = JSON.parse(fs.readFileSync(path.join(REPO, 'processed', runId, 'langgraph', 'run.json'), 'utf8'));
    console.log(`[real] ${r.kind} run.json status=${runJson.status} verdict=${runJson.verdict}`);
    expect({ status: runJson.status, verdict: runJson.verdict }).toEqual({ status: 'done', verdict: 'PASS' });
    await page.reload(); // fresh run list item carries the final verdict
    await expect(page.getByTestId(`run-${runId}`)).toHaveAttribute('data-verdict', 'PASS');
    await expect(page.getByTestId('node-transcribe')).toHaveAttribute('data-state', r.transcribe);
    await expect.poll(async () => Number(await page.getByTestId('tele-tokens').getAttribute('data-value'))).toBeGreaterThan(0);
    if (SHOTS) {
      await page.screenshot({ path: path.join(SHOTS, `real-${r.kind}-done.png`) });
      await page.getByTestId('tele-strip').screenshot({ path: path.join(SHOTS, `real-${r.kind}-telemetry.png`) });
    }

    const { artifacts } = await (await page.request.get(`/api/runs/${runId}/artifacts`)).json();
    const graph = await (await page.request.get('/api/graph')).json();
    for (const node of graph.nodes) {
      const mine = artifacts.filter((a) => a.node === node.id);
      if (!mine.length) continue;
      await page.getByTestId(`node-${node.id}`).click();
      const drawer = page.getByTestId('drawer');
      await expect(drawer).toHaveAttribute('data-node', node.id);
      const cards = drawer.getByTestId('artifact-list').locator('[data-kind]');
      await expect(cards).toHaveCount(mine.length);
      for (let i = 0; i < mine.length; i += 1) await expect(cards.nth(i)).toHaveAttribute('data-state', 'ready');
      await page.getByTestId('drawer-close').click();
      await expect(drawer).toBeHidden();
    }

    const title = execFileSync(PYTHON, ['pipeline/ui/e2e/real_blog_title.py', path.join(REPO, 'processed', runId, 'langgraph')],
      { cwd: REPO, env: { ...process.env, PYTHONPATH: REPO } }).toString().trim();
    expect(title).not.toBe('');
    await page.getByTestId('output-open').click();
    await expect(page.getByTestId('output-blog-link')).toHaveText(title, { timeout: 60_000 });
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `real-${r.kind}-result.png`) });

    expect(errors).toEqual([]);
  });
}
