// Coverage gate (spec 2026-10-31-pipeline-console-ui §6.5): every static/js file at 100% lines, functions, branches.
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const JS_DIR = path.resolve(HERE, '../static/js');
const METRICS = ['lines', 'functions', 'branches'];

export function belowGate(files) {
  return files.flatMap((f) => METRICS
    .filter((m) => f.summary[m].pct !== '' && f.summary[m].pct < 100)
    .map((m) => `${f.sourcePath}: ${m} ${f.summary[m].pct}%`));
}

export const coverageOptions = {
  name: 'pipeline console',
  outputDir: path.join(HERE, 'coverage'),
  reports: ['v8', 'console-details', 'json-summary'],
  entryFilter: (entry) => entry.url.includes('/static/js/'),
  sourcePath: (p) => p.replace(/^.*?static\/js\//, 'static/js/'),
  all: { dir: [JS_DIR] },
  onEnd: (results) => {
    const failures = belowGate(results.files);
    if (results.files.length === 0) failures.push('no coverage collected');
    if (failures.length) throw new Error(`coverage gate failed:\n${failures.join('\n')}`);
  },
};
