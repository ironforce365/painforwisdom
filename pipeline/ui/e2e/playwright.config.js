// Harness for spec 2026-10-31-pipeline-console-ui §6.5. Workers inherit PFW_UI_TMP/PFW_UI_PORT from the main process.
import { defineConfig } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../..');
const FIXTURES = path.join(REPO, 'tests/fixtures/ui');
const PYTHON = process.env.PYTHON || 'python';

if (!process.env.PFW_UI_TMP) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pfw-ui-e2e-'));
  fs.cpSync(path.join(FIXTURES, 'processed'), path.join(tmp, 'processed'), { recursive: true });
  fs.cpSync(path.join(FIXTURES, 'inbox'), path.join(tmp, 'inbox'), { recursive: true });
  process.env.PFW_UI_TMP = tmp;
  process.env.PFW_UI_PORT = execFileSync(PYTHON, ['-c',
    "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"]).toString().trim();
}
const TMP = process.env.PFW_UI_TMP;
const PORT = process.env.PFW_UI_PORT;

export default defineConfig({
  testDir: 'tests',
  testIgnore: ['**/real.spec.js'],
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  globalSetup: './global-setup.js',
  globalTeardown: './global-teardown.js',
  use: { baseURL: `http://127.0.0.1:${PORT}`, browserName: 'chromium', trace: 'retain-on-failure' },
  webServer: {
    command: `${PYTHON} -m pipeline.ui --port ${PORT}`,
    cwd: REPO,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      PIPELINE_UI_RUNNER: 'fake',
      PIPELINE_UI_PROFILE: 'sandbox',
      PIPELINE_UI_NOTION_FIXTURES: path.join(FIXTURES, 'notion'),
      PIPELINE_UI_PROCESSED_ROOT: path.join(TMP, 'processed'),
      PIPELINE_UI_INBOX: path.join(TMP, 'inbox'),
      CHECKPOINT_DB_PATH: path.join(TMP, 'checkpoints.db'),
    },
  },
});
