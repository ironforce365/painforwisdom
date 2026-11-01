// Boot, bus, run polling and the error badge (spec 2026-10-31-pipeline-console-ui §6.1, §6.4; module contract C7).
import * as api from './api.js';
import { svgIcon } from './icons.js';
import * as dag from './dag.js';
import * as runs from './runs.js';
import * as launch from './launch.js';
import * as drawer from './drawer.js';
import * as telemetry from './telemetry.js';

const POLL_MS = 1000;
const $ = (id) => document.getElementById(id);

// The C7 event set; any other name throws.
const EVENTS = ['run:selected', 'run:updated', 'node:selected', 'graph:loaded', 'ui:error'];
const listeners = new Map(EVENTS.map((e) => [e, []]));
const bus = {
  on: (event, fn) => listeners.get(event).push(fn),
  emit: (event, payload) => listeners.get(event).forEach((fn) => fn(payload)),
};

const state = { graph: null, runId: null, run: null };
const ctx = { api, bus, state };

const badge = document.querySelector('[data-testid="error-badge"]');
badge.append(svgIcon('offline'));
document.querySelector('[data-testid="logo"]').append(svgIcon('logo'));
// source "health" (network/5xx) clears on the next good response; "ui" (a module's ui:error) on the next selection.
const showError = (source, reason) => {
  badge.dataset.source = source;
  badge.setAttribute('aria-label', `error: ${reason}`);
  badge.title = reason;
  badge.hidden = false;
};
const clearError = (source) => {
  if (badge.dataset.source === source) badge.hidden = true;
};
bus.on('ui:error', (reason) => showError('ui', reason));
api.onHealth((ok) => (ok ? clearError('health') : showError('health', 'backend unreachable')));

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function loadGraph() {
  for (;;) {
    try {
      state.graph = await api.getGraph();
      bus.emit('graph:loaded', state.graph);
      return;
    } catch {
      await delay(POLL_MS); // the health listener already shows the badge
    }
  }
}

let timer;
async function poll(id) {
  const run = await api.getRun(id).catch((e) => e);
  if (id !== state.runId) return; // stale answer for a run no longer selected
  if (run instanceof Error) {
    if (run.status === 404) bus.emit('ui:error', `run ${id} not found`);
    else timer = setTimeout(poll, POLL_MS, id);
    return;
  }
  state.run = run;
  bus.emit('run:updated', run);
  if (runs.ACTIVE.has(run.status)) timer = setTimeout(poll, POLL_MS, id);
}

const graphReady = loadGraph();
bus.on('run:selected', (id) => {
  state.runId = id;
  state.run = null;
  clearError('ui');
  history.replaceState(null, '', `#/runs/${encodeURIComponent(id)}`);
  clearTimeout(timer);
  graphReady.then(() => poll(id));
});

const hash = /^#\/runs\/(.+)$/.exec(location.hash);
state.runId = hash && decodeURIComponent(hash[1]);

dag.mount($('dag'), ctx);
launch.mount($('launch'), ctx);
drawer.mount($('drawer'), ctx);
telemetry.mount($('telemetry'), ctx);
runs.mount($('runs'), ctx);
if (state.runId) bus.emit('run:selected', state.runId);
