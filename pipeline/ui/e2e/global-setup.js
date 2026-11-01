import MCR from 'monocart-coverage-reports';
import { coverageOptions } from './coverage.js';

export default async function globalSetup() {
  MCR(coverageOptions).cleanCache();
}
