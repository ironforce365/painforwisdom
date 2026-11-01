import fs from 'node:fs';
import MCR from 'monocart-coverage-reports';
import { coverageOptions } from './coverage.js';
import { freshServerState } from './fixtures.js';

// Throws (failing the run) when any static/js file is below the gate; a partial run fails it too.
export default async function globalTeardown() {
  try {
    await MCR(coverageOptions).generate();
  } finally {
    freshServerState(); // kills any fake runner a spec left behind
    fs.rmSync(process.env.PFW_UI_TMP, { recursive: true, force: true });
  }
}
