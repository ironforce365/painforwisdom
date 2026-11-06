// Boot, bus, run polling, the route and the error badge (spec 2026-11-05-console-end-to-end §4; module contract C7).
import * as api from './api.js';
import { svgIcon } from './icons.js';
import * as dag from './dag.js';
import * as runs from './runs.js';
import * as launch from './launch.js';
import * as drawer from './drawer.js';
import * as telemetry from './telemetry.js';
import * as operability from './operability.js';
import * as views from './views.js';
import * as actions from './actions.js';
import * as attention from './attention.js';
import * as logs from './logs.js';
import * as runheader from './runheader.js';

const POLL_MS = 1000;
const $ = (id) => document.getElementById(id);

// The C7 event set; any other name throws. `runs:listed` carries each run-list answer; `run:refresh` asks for one now.
const EVENTS = ['run:selected', 'run:updated', 'node:selected', 'graph:loaded', 'ui:error', 'view:changed', 'runs:listed', 'run:refresh'];
const listeners = new Map(EVENTS.map((e) => [e, []]));
const bus = {
  on: (event, fn) => listeners.get(event).push(fn),
  emit: (event, payload) => listeners.get(event).forEach((fn) => fn(payload)),
};

const state = { graph: null, runId: null, run: null, view: views.viewFromHash(location.hash) };
const ctx = { api, bus, state };

const badge = document.querySelector('[data-testid="error-badge"]');
const badgeText = document.createElement('span');
badge.append(svgIcon('offline'), badgeText);
document.querySelector('[data-testid="logo"]').append(svgIcon('logo'));
// source "health" (network/5xx) clears on the next good response; "ui" (a module's ui:error) on the next selection.
const showError = (source, reason) => {
  badge.dataset.source = source;
  badge.setAttribute('aria-label', `error: ${reason}`);
  badge.title = reason;
  badgeText.textContent = reason;
  badge.hidden = false;
};
const clearError = (source) => {
  if (badge.dataset.source === source) badge.hidden = true;
};
bus.on('ui:error', (reason) => showError('ui', reason));
api.onHealth((ok) => (ok ? clearError('health') : showError('health', 'Backend unreachable')));

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function loadGraph() {
  for (;;) {
    try {
      const [graph, { version }] = await Promise.all([api.getGraph(), api.getVersion()]);
      state.graph = graph;
      document.querySelector('[data-testid="version"]').textContent = version;
      bus.emit('graph:loaded', state.graph);
      return;
    } catch {
      await delay(POLL_MS); // the health listener already shows the badge
    }
  }
}

let timer;
let chain = 0; // one polling chain at a time: an answer from an older one is stale, even for the same run

async function poll(mine, id) {
  const run = await api.getRun(id).catch((e) => e);
  if (mine !== chain) return;
  if (run instanceof Error) {
    if (run.status === 404) bus.emit('ui:error', `run ${id} not found`);
    else timer = setTimeout(poll, POLL_MS, mine, id);
    return;
  }
  state.run = run;
  bus.emit('run:updated', run);
  if (runs.ACTIVE.has(run.status)) timer = setTimeout(poll, POLL_MS, mine, id);
}

const startPolling = () => {
  chain += 1;
  clearTimeout(timer);
  return poll(chain, state.runId);
};

const graphReady = loadGraph();
bus.on('run:selected', (id) => {
  state.runId = id;
  state.run = null;
  clearError('ui');
  history.replaceState(null, '', views.routeFor(id, state.view));
  chain += 1; // an answer in flight belongs to the previous selection
  clearTimeout(timer);
  graphReady.then(startPolling);
});
// An action changed the run: read it now, whether or not it was still being polled.
bus.on('run:refresh', () => graphReady.then(startPolling));

state.runId = views.runFromHash(location.hash);

dag.mount($('dag'), ctx);
launch.mount($('launch'), ctx);
drawer.mount($('drawer'), ctx);
telemetry.mount($('telemetry'), ctx);
actions.mount($('actions'), ctx);
attention.mount($('attention'), ctx);
logs.mount($('logs'), ctx);
runheader.mount($('runinfo'), ctx);
operability.mount($('operability'), ctx);
runs.mount($('runs'), ctx);
views.mount($('viewtabs'), ctx);
if (state.runId) bus.emit('run:selected', state.runId);

window.addEventListener('hashchange', () => {
  const id = views.runFromHash(location.hash);
  const view = views.viewFromHash(location.hash);
  if (!id) return;
  if (view !== state.view) ctx.showView(view);
  if (id !== state.runId) bus.emit('run:selected', id);
});
