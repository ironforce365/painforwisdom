// Shared spec helpers (spec 2026-10-31-pipeline-console-ui §6.5).
import { test as base, expect } from '@playwright/test';
import MCR from 'monocart-coverage-reports';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { coverageOptions } from './coverage.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, '../../../tests/fixtures/ui');
export const PROCESSED = path.join(process.env.PFW_UI_TMP, 'processed');
export const INBOX = path.join(process.env.PFW_UI_TMP, 'inbox');
const LIVE_FIXTURES = ['fx-running', 'fx-hitl', 'fx-error', 'fx-oldwait'];

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function isFakeRunner(pid) {
  try {
    return fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').includes('pipeline.ui.fake_runner');
  } catch {
    return false;
  }
}

/** Launched runs and retried fixtures alike. A live fixture's pid is this process, never signalled. */
function killRunners() {
  if (!fs.existsSync(PROCESSED)) return;
  for (const name of fs.readdirSync(PROCESSED)) {
    let pid;
    try {
      pid = JSON.parse(fs.readFileSync(path.join(PROCESSED, name, 'langgraph', 'run.json'), 'utf8')).pid;
    } catch {
      continue;
    }
    if (!Number.isInteger(pid) || pid <= 0 || !isFakeRunner(pid)) continue;
    try {
      process.kill(-pid, 'SIGKILL'); // fake runner has its own session (start_new_session)
    } catch {
      // already gone
    }
    // The server reaps the child; wait so it cannot write into the root after the wipe.
    const until = Date.now() + 5000;
    while (alive(pid) && Date.now() < until) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
  }
}

function setPid(runId, pid) {
  const file = path.join(PROCESSED, runId, 'langgraph', 'run.json');
  const rec = JSON.parse(fs.readFileSync(file, 'utf8'));
  fs.writeFileSync(file, JSON.stringify({ ...rec, pid }, null, 2));
}

/** Reset the server's processed root to C6 and its inbox to the fixture inbox. `live` makes LIVE_FIXTURES alive (pid of this process). */
export function freshServerState({ live = false } = {}) {
  killRunners();
  fs.rmSync(PROCESSED, { recursive: true, force: true });
  fs.cpSync(path.join(FIXTURES, 'processed'), PROCESSED, { recursive: true });
  fs.cpSync(path.join(FIXTURES, 'vault'), path.join(process.env.PFW_UI_TMP, 'vault'), { recursive: true });
  fs.rmSync(INBOX, { recursive: true, force: true });
  fs.cpSync(path.join(FIXTURES, 'inbox'), INBOX, { recursive: true });
  if (live) LIVE_FIXTURES.forEach((id) => setPid(id, process.pid));
}

/** Answer the fake run's C11 request once it appears; the `hitl` scenario waits for this. */
export async function answerPending(request, runId, reply = 'yes') {
  await expect.poll(async () => (await (await request.get(`/api/runs/${runId}`)).json()).pending?.id, { timeout: 30_000 }).toBeTruthy();
  const { pending } = await (await request.get(`/api/runs/${runId}`)).json();
  const res = await request.post(`/api/runs/${runId}/reply`, { data: { id: pending.id, reply } });
  expect(res.status()).toBe(202);
}

export const test = base.extend({
  // errors.allow(re): a spec-intended failure (route abort/500) whose console/request noise must not fail the test.
  errors: async ({}, use) => {
    const allowed = [];
    const seen = [];
    await use({ allow: (re) => allowed.push(re), seen });
    const unexpected = seen.filter((msg) => !allowed.some((re) => re.test(msg)));
    expect(unexpected, 'unexpected page errors').toEqual([]);
  },
  page: async ({ page, errors }, use) => {
    page.on('pageerror', (e) => errors.seen.push(`pageerror: ${e.message}`));
    page.on('console', (m) => {
      if (m.type() === 'error') errors.seen.push(`console: ${m.text()} @ ${m.location().url}`);
    });
    page.on('requestfailed', (r) => {
      const text = r.failure()?.errorText;
      // Leaving a section detaches its <video>, and the browser cancels the load: correct, and timing-dependent.
      if (text === 'net::ERR_ABORTED' && r.url().includes('/asset/')) return;
      errors.seen.push(`requestfailed: ${r.url()} ${text}`);
    });
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    await use(page);
    const coverage = await page.coverage.stopJSCoverage();
    await MCR(coverageOptions).add(coverage);
  },
});

export { expect };
