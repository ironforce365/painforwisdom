// Merges the raw coverage of every shard in a directory and enforces the coverage.js gate.
// Usage: node merge-coverage.js [dir]  (default coverage-shards/, laid out as <i>-of-<N>/raw/)
import fs from 'node:fs';
import path from 'node:path';
import MCR from 'monocart-coverage-reports';
import { coverageOptions, SHARDS_DIR } from './coverage.js';

const dir = path.resolve(process.argv[2] || SHARDS_DIR);
const found = fs.existsSync(dir) ? fs.readdirSync(dir).filter((n) => /^\d+-of-\d+$/.test(n)) : [];
const total = Number(found[0]?.split('-of-')[1]);
const expected = Array.from({ length: total || 0 }, (_, i) => `${i + 1}-of-${total}`);
// A missing shard must read as missing, and a leftover from another split must not hide a gap.
if (!total || found.slice().sort().join() !== expected.slice().sort().join()) {
  throw new Error(`coverage gate failed: expected one complete split in ${dir}, found [${found.join(', ')}]`);
}

const report = MCR({ ...coverageOptions, inputDir: found.map((n) => path.join(dir, n, 'raw')) });
report.cleanCache(); // a leftover cache from an interrupted run would count as coverage
await report.generate();
