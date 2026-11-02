// Opt-in real sandbox runs (spec 2026-10-31-pipeline-console-ui §10): `npm run test:real`. Never in CI.
import { defineConfig } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../..');
const PYTHON = process.env.PYTHON || 'python';

if (!fs.existsSync(path.join(REPO, '.env.sandbox'))) throw new Error('test:real needs .env.sandbox at the repo root');
if (!process.env.PFW_REAL_VIDEO) throw new Error('test:real needs PFW_REAL_VIDEO=<short real .mp4>');

if (!process.env.PFW_REAL_INBOX) {
  const inbox = fs.mkdtempSync(path.join(os.tmpdir(), 'pfw-ui-real-'));
  fs.copyFileSync(path.join(REPO, 'tests/fixtures/transcript_2026-04-14.txt'), path.join(inbox, 'transcript_2026-04-14.txt'));
  fs.copyFileSync(process.env.PFW_REAL_VIDEO, path.join(inbox, path.basename(process.env.PFW_REAL_VIDEO)));
  process.env.PFW_REAL_INBOX = inbox;
  process.env.PFW_UI_PORT = execFileSync(PYTHON, ['-c',
    "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"]).toString().trim();
}

export default defineConfig({
  testDir: 'tests',
  testMatch: ['**/real.spec.js'],
  workers: 1,
  retries: 0,
  timeout: 60 * 60_000,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${process.env.PFW_UI_PORT}`, browserName: 'chromium',
    viewport: { width: 1280, height: 720 }, trace: 'retain-on-failure' },
  webServer: {
    // Spec §10.2 command as written: no PIPELINE_UI_* runner/fixtures, no pre-exported .env.sandbox.
    command: `${PYTHON} -m pipeline.ui --profile sandbox --port ${process.env.PFW_UI_PORT} --inbox ${process.env.PFW_REAL_INBOX}`,
    cwd: REPO,
    url: `http://127.0.0.1:${process.env.PFW_UI_PORT}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
