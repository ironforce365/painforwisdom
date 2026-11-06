// Metrics bar: one line, expands to a per-node table (spec 2026-11-05-console-end-to-end §4). Numbers come from C3 run detail.
import { el } from './drawer.js';
import { ACTIVE } from './runs.js';
import { costTip, fmtCost, fmtCount, fmtDuration, fmtRatio, tokensOf } from './format.js';

const OPEN_KEY = 'pfw.metrics.open';
const NO_ROW = {
  duration_s: null, model: null, cost_usd: 0, cost_estimated: false, calls: 0, in_flight: 0,
  input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
};
const NO_NODE = { state: 'pending', started_at: null, ended_at: null, duration_s: null };

/** name, label, tier: tier 2 fields give way first when the column is narrow. */
const FIELDS = [
  ['tokens', 'Tokens', 1], ['fresh', 'Fresh input', 2], ['output', 'Output', 2], ['cache', 'Cache', 1], ['cost', 'Cost', 1],
  ['time', 'Time', 1], ['work', 'Work', 2], ['calls', 'Calls', 2], ['inflight', 'In flight', 3], ['models', 'Models', 3],
];
const COLUMNS = [['state', 'State'], ['time', 'Time'], ['span', 'When'], ['model', 'Model'], ['tokens', 'Tokens'], ['fresh', 'Fresh'],
  ['output', 'Output'], ['cache', 'Cache'], ['cost', 'Cost']];
const TIPS = {
  tokens: 'Input plus output. Input already holds both cache counts.',
  fresh: 'Input neither read from nor written to the cache.',
  cache: 'Cache read as a share of input.',
  time: 'Wall time.',
  work: 'Wall time minus the wait for answers.',
};

function loadOpen() {
  try {
    return localStorage.getItem(OPEN_KEY) === 'true';
  } catch {
    return false; // storage blocked: closed stands
  }
}

function saveOpen(open) {
  try {
    localStorage.setItem(OPEN_KEY, String(open));
  } catch {
    // storage blocked: the choice lasts this page only
  }
}

/** Only a real change touches the DOM: the poll must not drop a text selection. */
const setText = (node, text) => {
  if (node.textContent !== text) node.textContent = text;
};

function runNumbers(run) {
  const t = run.telemetry;
  return {
    ...tokensOf(t.tokens.input, t.tokens.output, t.tokens.cache_read, t.tokens.cache_creation),
    calls: t.calls, inflight: t.in_flight, cost: t.cost_usd, estimated: t.cost_estimated, time: t.total_s, work: t.work_s,
    models: [...new Set(Object.values(t.per_node).map((p) => p.model).filter(Boolean))],
    live: ACTIVE.has(run.status),
  };
}

function nodeNumbers(run, id) {
  const row = { ...NO_ROW, ...run.telemetry.per_node[id] };
  const node = { ...NO_NODE, ...run.nodes[id] };
  const now = run.started_at + run.telemetry.total_s;
  const span = node.started_at === null ? null : (node.ended_at ?? now) - node.started_at;
  return {
    ...tokensOf(row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_creation_tokens),
    calls: row.calls, inflight: row.in_flight, cost: row.cost_usd, estimated: row.cost_estimated,
    time: span, work: row.duration_s ?? node.duration_s,
    models: [row.model].filter(Boolean),
    state: node.state, started_at: node.started_at, span,
    live: node.state === 'running',
  };
}

function writeField(view, nums) {
  const values = {
    tokens: nums.total, fresh: nums.fresh, output: nums.output, cache: nums.hit, cost: nums.cost,
    time: nums.time, work: nums.work, calls: nums.calls, inflight: nums.inflight, models: nums.models.join(', '),
  };
  const text = {
    tokens: fmtCount(nums.total), fresh: fmtCount(nums.fresh), output: fmtCount(nums.output), cache: fmtRatio(nums.hit),
    cost: fmtCost(nums.cost, nums.estimated), time: fmtDuration(nums.time), work: fmtDuration(nums.work),
    calls: fmtCount(nums.calls), inflight: fmtCount(nums.inflight), models: values.models || '–',
  };
  for (const [name, node] of Object.entries(view)) {
    setText(node, text[name]);
    node.dataset.value = values[name];
  }
  view.cost.dataset.estimated = String(Boolean(nums.estimated));
  view.cost.title = costTip(nums.cost, nums.estimated);
  view.time.dataset.live = String(nums.live);
}

export function mount(root, ctx) {
  let open = loadOpen();
  let run = null;
  let scope = null;
  const rows = new Map();
  let order = [];

  const fields = {};
  const items = FIELDS.map(([name, label, tier]) => {
    fields[name] = el('b', { class: 'm-val', 'data-testid': `metrics-${name}` });
    return el('span', { class: 'm-field', 'data-tier': tier, title: TIPS[name] ?? label }, el('span', { class: 'm-label' }, label), ' ', fields[name]);
  });

  const toggle = el('button', { type: 'button', class: 'm-toggle', 'data-testid': 'metrics-toggle', 'aria-expanded': String(open) }, 'Details');
  const scopeName = el('span', {});
  const chip = el('button', { type: 'button', class: 'm-scope', 'data-testid': 'metrics-scope-clear', title: 'Back to the whole run' }, scopeName, ' · whole run');
  chip.hidden = true;
  const live = el('span', { class: 'm-live', 'data-testid': 'metrics-live' }, 'live');
  const bar = el('div', { class: 'm-bar', 'data-testid': 'metrics-bar', 'data-scope': 'run', 'data-live': 'false' }, toggle, chip, live, ...items);
  bar.hidden = true;

  const tbody = el('tbody', {});
  const head = el('tr', {}, el('th', {}, 'Node'), ...COLUMNS.map(([, label]) => el('th', {}, label)));
  const table = el('div', { class: 'm-table', 'data-testid': 'metrics-table' }, el('table', {}, el('thead', {}, head), tbody));
  table.hidden = !open;
  root.append(el('div', { class: 'metrics' }, bar, table));

  const setOpen = (next) => {
    open = next;
    saveOpen(open);
    table.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };
  toggle.addEventListener('click', () => setOpen(!open));
  ctx.openMetrics = () => {
    setOpen(true);
    table.scrollIntoView({ block: 'nearest' });
  };
  chip.addEventListener('click', () => ctx.bus.emit('node:selected', null));

  const unhighlight = () => document.querySelectorAll('[data-highlight]').forEach((n) => n.removeAttribute('data-highlight'));
  const highlight = (id) => {
    unhighlight();
    document.querySelector(`[data-testid="dag"] [data-testid="node-${CSS.escape(id)}"]`).setAttribute('data-highlight', '');
  };

  const rowEl = (n) => {
    const cells = Object.fromEntries(COLUMNS.map(([col]) => [col, el('td', { 'data-col': col })]));
    const span = el('div', { class: 'm-span', 'data-testid': `metrics-span-${n.id}` });
    span.hidden = true;
    cells.span.append(el('div', { class: 'm-track' }, span));
    const tr = el('tr', { 'data-testid': `metrics-row-${n.id}`, 'aria-selected': 'false' },
      el('td', { class: 'm-node' }, el('button', { type: 'button' }, n.label)), ...Object.values(cells));
    tr.addEventListener('click', () => ctx.bus.emit('node:selected', n.id));
    tr.addEventListener('mouseenter', () => highlight(n.id));
    tr.addEventListener('mouseleave', unhighlight);
    return { tr, cells, span };
  };

  const renderRow = (row, nums, t0, width) => {
    const { cells, span } = row;
    setText(cells.state, nums.state);
    row.tr.dataset.state = nums.state;
    setText(cells.time, fmtDuration(nums.work ?? nums.span));
    setText(cells.model, nums.models[0] ?? '–');
    setText(cells.tokens, nums.total ? fmtCount(nums.total) : '–');
    cells.tokens.dataset.value = nums.total;
    setText(cells.fresh, nums.total ? fmtCount(nums.fresh) : '–');
    setText(cells.output, nums.output ? fmtCount(nums.output) : '–');
    setText(cells.cache, nums.total ? fmtRatio(nums.hit) : '–');
    setText(cells.cost, fmtCost(nums.cost, nums.estimated));
    cells.cost.title = costTip(nums.cost, nums.estimated);
    span.hidden = nums.started_at === null;
    if (nums.started_at !== null) {
      Object.assign(span.style, { left: `${((nums.started_at - t0) / width) * 100}%`, width: `${(nums.span / width) * 100}%` });
      span.dataset.state = nums.state;
      span.dataset.span = nums.span;
    }
  };

  const renderTable = () => {
    const ids = [...rows.keys()];
    const nums = new Map(ids.map((id) => [id, nodeNumbers(run, id)]));
    const started = ids.map((id) => nums.get(id)).filter((n) => n.started_at !== null);
    const t0 = Math.min(run.started_at ?? Infinity, ...started.map((n) => n.started_at));
    const end = Math.max(run.started_at + run.telemetry.total_s, ...started.map((n) => n.started_at + n.span));
    const width = end - t0 || 1;
    ids.forEach((id) => renderRow(rows.get(id), nums.get(id), t0, width));
    const sorted = [...ids].sort((a, b) => nums.get(b).total - nums.get(a).total);
    if (sorted.join() !== order.join()) { // a reorder moves DOM nodes, so only when it must
      order = sorted;
      tbody.append(...sorted.map((id) => rows.get(id).tr));
    }
  };

  const render = () => {
    bar.hidden = run === null;
    bar.dataset.scope = scope ?? 'run';
    chip.hidden = scope === null;
    for (const [id, row] of rows) row.tr.setAttribute('aria-selected', String(id === scope));
    if (run === null) return;
    const nums = scope === null ? runNumbers(run) : nodeNumbers(run, scope);
    bar.dataset.live = String(nums.live);
    writeField(fields, nums);
    renderTable();
  };

  ctx.bus.on('graph:loaded', (graph) => {
    graph.nodes.forEach((n) => rows.set(n.id, rowEl(n)));
    tbody.replaceChildren(...[...rows.values()].map((r) => r.tr));
    order = [...rows.keys()];
  });
  ctx.bus.on('node:selected', (id) => {
    scope = id;
    if (id !== null) scopeName.textContent = ctx.state.graph.nodes.find((n) => n.id === id).label;
    render();
  });
  ctx.bus.on('run:selected', () => {
    run = null;
    render();
  });
  ctx.bus.on('run:updated', (r) => {
    run = r;
    render();
  });
}
