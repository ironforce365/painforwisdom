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
const UI_RUN = /_ui(_\d+)?$/;

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function killRunners() {
  if (!fs.existsSync(PROCESSED)) return;
  for (const name of fs.readdirSync(PROCESSED).filter((n) => UI_RUN.test(n))) {
    let pid;
    try {
      pid = JSON.parse(fs.readFileSync(path.join(PROCESSED, name, 'langgraph', 'run.json'), 'utf8')).pid;
    } catch {
      continue;
    }
    if (!Number.isInteger(pid) || pid <= 0) continue;
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

/** Reset the server's processed root to C6. `live` makes fx-running/fx-hitl alive (pid of this process). */
export function freshServerState({ live = false } = {}) {
  killRunners();
  fs.rmSync(PROCESSED, { recursive: true, force: true });
  fs.cpSync(path.join(FIXTURES, 'processed'), PROCESSED, { recursive: true });
  if (live) {
    setPid('fx-running', process.pid);
    setPid('fx-hitl', process.pid);
  }
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
    page.on('requestfailed', (r) => errors.seen.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`));
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    await use(page);
    const coverage = await page.coverage.stopJSCoverage();
    await MCR(coverageOptions).add(coverage);
  },
});

export { expect };
