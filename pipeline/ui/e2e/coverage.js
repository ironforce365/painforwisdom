// Coverage gate (spec 2026-10-31-pipeline-console-ui §6.5): every static/js file at 100% lines, functions, branches.
// PFW_COVERAGE_MERGE=1: only write this run's raw coverage to coverage-shards/<i>-of-<N>; merge-coverage.js enforces the gate.
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const JS_DIR = path.resolve(HERE, '../static/js');
export const SHARDS_DIR = path.join(HERE, 'coverage-shards');
const METRICS = ['lines', 'functions', 'branches'];

export function belowGate(files) {
  return files.flatMap((f) => METRICS
    .filter((m) => f.summary[m].pct !== '' && f.summary[m].pct < 100)
    .map((m) => `${f.sourcePath}: ${m} ${f.summary[m].pct}%`));
}

const common = {
  name: 'pipeline console',
  entryFilter: (entry) => entry.url.includes('/static/js/'),
  sourcePath: (p) => p.replace(/^.*?static\/js\//, 'static/js/'),
};

function shardLabel() {
  // Only the main process sees --shard in argv; workers inherit the env var it sets.
  process.env.PFW_COVERAGE_SHARD ||= (process.argv.join(' ').match(/--shard[= ](\d+)\/(\d+)/)?.slice(1) ?? [1, 1]).join('-of-');
  return process.env.PFW_COVERAGE_SHARD;
}

export const coverageOptions = process.env.PFW_COVERAGE_MERGE
  ? { ...common, outputDir: path.join(SHARDS_DIR, shardLabel()), reports: ['raw'] }
  : {
    ...common,
    outputDir: path.join(HERE, 'coverage'),
    reports: ['v8', 'console-details', 'json-summary'],
    all: { dir: [JS_DIR] },
    onEnd: (results) => {
      const failures = belowGate(results.files);
      if (results.files.length === 0) failures.push('no coverage collected');
      if (failures.length) throw new Error(`coverage gate failed:\n${failures.join('\n')}`);
    },
  };
