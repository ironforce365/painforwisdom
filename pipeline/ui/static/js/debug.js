// Live debug panel (spec 2026-11-03-console-operability §5.2). Numbers come from C3 telemetry; refresh rides the run poll.
import { svgIcon } from './icons.js';
import { ACTIVE } from './runs.js';
import { fmtCount, fmtDuration } from './telemetry.js';

const PATHS = {
  panel: 'M4 16a8 8 0 1 1 16 0 M12 16l4-5 M4 20h16',
  chevron: 'M6 9l6 6 6-6',
  clear: 'M7 7l10 10 M17 7L7 17',
  input: 'M12 4v12 M7 11l5 5 5-5 M5 20h14',
  output: 'M12 20V8 M7 13l5-5 5 5 M5 4h14',
  cache_read: 'M12 4a8 3 0 1 1 0 6a8 3 0 0 1 0-6z M4 7v10c0 2 4 3 8 3s8-1 8-3V7 M9 15l2 2 4-4',
  cache_creation: 'M12 4a8 3 0 1 1 0 6a8 3 0 0 1 0-6z M4 7v10c0 2 4 3 8 3s8-1 8-3V7 M12 12v6 M9 15h6',
  total: 'M5 5h14 M5 5l7 7-7 7 M5 19h14',
  hit: 'M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M12 8a4 4 0 1 1 0 8a4 4 0 0 1 0-8z M12 12h.01',
  calls: 'M4 5h16v11H9l-5 4z',
  in_flight: 'M12 4a8 8 0 0 1 8 8 M12 20a8 8 0 0 1-8-8 M12 9v3l2 2',
  cost: 'M16 7c-1-1-2.4-1.5-4-1.5c-2.2 0-3.5 1.1-3.5 2.7c0 3.6 7.5 2 7.5 5.6c0 1.7-1.4 2.7-4 2.7c-1.6 0-3-.5-4-1.5 M12 3v18',
  elapsed: 'M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M12 7v5l3 2',
  work: 'M4 8h16v11H4z M9 8V5h6v3 M4 13h16',
  models: 'M7 7h10v10H7z M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4',
};
const fmtRatio = (r) => (r === null ? '–' : `${Math.round(r * 100)}%`);
const fmtCost = (c) => `$${c.toFixed(3)}`;
const fmtModels = (models) => models.join(', ') || '–';
const FIELDS = [
  ['input', 'tokens in', fmtCount],
  ['output', 'tokens out', fmtCount],
  ['cache_read', 'cache read', fmtCount],
  ['cache_creation', 'cache write', fmtCount],
  ['total', 'total tokens', fmtCount],
  ['hit', 'cache hit ratio', fmtRatio],
  ['calls', 'LLM calls', fmtCount],
  ['in_flight', 'calls in flight', fmtCount],
  ['cost', 'cost', fmtCost],
  ['elapsed', 'elapsed', fmtDuration],
  ['work', 'work time', fmtDuration],
  ['models', 'models', fmtModels],
];
// The run the panel holds until a poll answers, so a click in that gap needs no branch.
const BLANK = {
  status: 'pending', started_at: null, nodes: {},
  telemetry: {
    total_s: null, work_s: null, cost_usd: 0, calls: 0, in_flight: 0, per_node: {},
    tokens: { input: 0, output: 0, cache_read: 0, cache_creation: 0 },
  },
};
const NO_NODE = { state: 'pending', started_at: null, ended_at: null, duration_s: null };
const NO_ROW = {
  duration_s: null, model: null, cost_usd: 0, calls: 0, in_flight: 0,
  input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
};

function runNumbers(run) {
  const t = run.telemetry;
  return {
    ...t.tokens, calls: t.calls, in_flight: t.in_flight, cost: t.cost_usd, elapsed: t.total_s, work: t.work_s,
    models: [...new Set(Object.values(t.per_node).map((p) => p.model).filter(Boolean))],
    live: ACTIVE.has(run.status),
  };
}

function nodeNumbers(run, id) {
  const row = { ...NO_ROW, ...run.telemetry.per_node[id] };
  const node = { ...NO_NODE, ...run.nodes[id] };
  const now = run.started_at + run.telemetry.total_s;
  return {
    input: row.input_tokens, output: row.output_tokens, cache_read: row.cache_read_tokens, cache_creation: row.cache_creation_tokens,
    calls: row.calls, in_flight: row.in_flight, cost: row.cost_usd,
    elapsed: node.started_at === null ? null : (node.ended_at ?? now) - node.started_at,
    work: row.duration_s ?? node.duration_s,
    models: [row.model].filter(Boolean),
    live: node.state === 'running',
  };
}

function el(tag, cls, attrs = {}) {
  const node = document.createElement(tag);
  node.className = cls;
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function icon(name) {
  const svg = svgIcon('dot');
  svg.firstChild.setAttribute('d', PATHS[name]);
  return svg;
}

function field([key]) {
  const box = el('span', `dbg-field dbg-${key}`, { role: 'img' });
  const val = el('span', 'dbg-val', { 'data-testid': `debug-${key.replace('_', '-')}` });
  box.append(icon(key), val);
  return { box, val };
}

export function mount(root, ctx) {
  let run = BLANK;
  let scope = null;
  const panel = el('div', 'debug', { 'data-testid': 'debug', 'data-collapsed': 'false', 'aria-label': 'debug panel' });
  const body = el('div', 'dbg-body');
  const fields = FIELDS.map(field);
  body.append(...fields.map((f) => f.box));

  const toggle = el('button', 'dbg-toggle', { type: 'button', 'data-testid': 'debug-toggle', 'aria-expanded': 'true', 'aria-label': 'debug panel' });
  toggle.title = 'debug panel';
  toggle.append(icon('panel'), icon('chevron'));
  toggle.addEventListener('click', () => {
    body.hidden = !body.hidden;
    toggle.setAttribute('aria-expanded', String(!body.hidden));
    panel.dataset.collapsed = String(body.hidden);
  });

  const chipIcon = el('span', 'dbg-chip-icon');
  const chip = el('button', 'dbg-chip', { type: 'button', 'data-testid': 'debug-scope-clear' });
  chip.append(chipIcon, icon('clear'));
  const head = el('div', 'dbg-head');
  head.append(toggle, chip, el('span', 'dbg-live', { 'data-testid': 'debug-live-dot' }));
  panel.append(head, body);
  root.replaceChildren(panel);

  const render = () => {
    const nums = scope === null ? runNumbers(run) : nodeNumbers(run, scope);
    const cached = nums.input + nums.cache_read;
    const values = {
      ...nums,
      total: cached + nums.output + nums.cache_creation,
      hit: cached ? nums.cache_read / cached : null,
    };
    panel.hidden = run === BLANK;
    panel.dataset.scope = scope ?? 'run';
    panel.dataset.live = String(nums.live);
    chip.hidden = scope === null;
    fields.forEach(({ box, val }, i) => {
      const [key, label, fmt] = FIELDS[i];
      val.textContent = fmt(values[key]);
      val.dataset.value = values[key];
      box.title = `${label}: ${val.textContent}`;
      box.setAttribute('aria-label', box.title);
    });
  };

  // Drawer sits fixed over this corner while open; hidden it measures 0, so one write covers both (Neo, Q3329).
  const drawer = document.querySelector('[data-testid="drawer"]');
  new ResizeObserver(() => root.style.setProperty('--drawer-offset', `${drawer.offsetWidth}px`)).observe(drawer);

  chip.addEventListener('click', () => {
    scope = null;
    render();
  });
  ctx.bus.on('node:selected', (id) => {
    const node = ctx.state.graph.nodes.find((n) => n.id === id);
    scope = id === scope ? null : id;
    chipIcon.replaceChildren(svgIcon(node.icon));
    chip.title = `${node.label}: back to whole run`;
    chip.setAttribute('aria-label', chip.title);
    render();
  });
  ctx.bus.on('run:selected', () => {
    run = BLANK;
    scope = null;
    render();
  });
  ctx.bus.on('run:updated', (r) => {
    run = r;
    render();
  });
  render();
}
