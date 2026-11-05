// Run list as text: input, status, time, cost (spec 2026-11-05-console-end-to-end §4). Live runs on top, 5 s refresh.
import { svgIcon } from './icons.js';
import { costTip, fmtCost, fmtCount, fmtDuration, fmtWhen, STATUS_LABELS } from './format.js';

export const ACTIVE = new Set(['running', 'waiting_hitl', 'waiting_reply']);
const GLYPHS = { done: 'tick', failed: 'cross', crashed: 'bolt', stopped: 'pause', running: 'arc', waiting_hitl: 'hand', waiting_reply: 'chat' };
const REFRESH_MS = 5000;

function span(cls, testid, text) {
  const node = document.createElement('span');
  node.className = cls;
  node.dataset.testid = testid;
  node.textContent = text;
  return node;
}

function stateText(run) {
  const label = STATUS_LABELS[run.status];
  if (run.failed_node) return `${label} at ${run.failed_node}`;
  return run.verdict ? `${label} · ${run.verdict}` : label;
}

function itemEl(run, ctx) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'run';
  item.dataset.testid = `run-${run.run_id}`;
  item.dataset.runId = run.run_id;
  item.setAttribute('aria-pressed', String(run.run_id === ctx.state.runId));
  const glyphs = document.createElement('span');
  glyphs.className = 'run-glyphs';
  for (const [status, glyph] of Object.entries(GLYPHS)) {
    const g = svgIcon(glyph, { class: `icon glyph glyph-${status}` });
    if (status === 'running') g.dataset.testid = 'spinner';
    glyphs.append(g);
  }
  item.append(glyphs);
  const line = (cls, ...children) => {
    const div = document.createElement('span');
    div.className = cls;
    div.append(...children);
    return div;
  };
  item.append(
    line('rl-top', span('rl-name', 'rl-name', '')),
    line('rl-mid', span('rl-state', 'rl-state', ''), span('rl-when', 'rl-when', '')),
    line('rl-bot', span('rl-time', 'rl-time', ''), span('rl-tokens', 'rl-tokens', ''), span('rl-cost', 'rl-cost', ''), span('rl-needs', 'rl-needs', 'Needs you')),
  );
  item.addEventListener('click', () => ctx.bus.emit('run:selected', run.run_id));
  return item;
}

function setText(item, testid, text) {
  const node = item.querySelector(`[data-testid="${testid}"]`);
  if (node.textContent !== text) node.textContent = text;
}

function fillItem(item, run) {
  item.dataset.status = run.status;
  item.dataset.verdict = String(run.verdict);
  item.dataset.needs = String(run.attention.length > 0);
  item.setAttribute('aria-label', `${run.run_id} ${run.status}`);
  item.title = `${run.run_id} · ${run.status}`;
  setText(item, 'rl-name', run.input.name ?? run.run_id);
  setText(item, 'rl-state', stateText(run));
  setText(item, 'rl-when', fmtWhen(run.started_at));
  setText(item, 'rl-time', fmtDuration(run.total_s));
  setText(item, 'rl-tokens', run.tokens ? `${fmtCount(run.tokens)} tokens` : '');
  setText(item, 'rl-cost', fmtCost(run.cost_usd, run.cost_estimated));
  item.querySelector('[data-testid="rl-cost"]').title = costTip(run.cost_usd, run.cost_estimated);
}

function emptyEl() {
  const empty = document.createElement('div');
  empty.className = 'runs-empty';
  empty.dataset.testid = 'runs-empty';
  empty.append(svgIcon('inbox'), 'No runs yet. Start one.');
  return empty;
}

export function mount(el, ctx) {
  const items = new Map();
  const rows = new Map();
  const emptied = emptyEl();

  const render = (runs) => {
    const ordered = [...runs].sort((a, b) => ACTIVE.has(b.status) - ACTIVE.has(a.status));
    rows.clear();
    for (const run of ordered) {
      if (!items.has(run.run_id)) items.set(run.run_id, itemEl(run, ctx));
      fillItem(items.get(run.run_id), run);
      rows.set(run.run_id, run);
    }
    const next = ordered.length ? ordered.map((r) => items.get(r.run_id)) : [emptied];
    // Moving nodes drops the focus: touch the DOM only when the order changed.
    if (next.some((node, i) => el.children[i] !== node) || el.children.length !== next.length) el.replaceChildren(...next);
    ctx.bus.emit('runs:listed', runs);
    if (!ctx.state.runId && ordered.length) ctx.bus.emit('run:selected', ordered[0].run_id);
  };
  // On failure keep the last list; api.js health reporting shows the badge.
  const load = () => ctx.api.listRuns().then((body) => render(body.runs), () => {});

  ctx.bus.on('run:selected', (id) => {
    for (const [runId, item] of items) item.setAttribute('aria-pressed', String(runId === id));
  });
  ctx.bus.on('run:updated', (run) => {
    const row = rows.get(run.run_id);
    if (!row) return; // the list has not heard of it yet
    const t = run.telemetry;
    Object.assign(row, {
      status: run.status, verdict: run.verdict, total_s: t.total_s, cost_usd: t.cost_usd, cost_estimated: t.cost_estimated,
      tokens: t.tokens.input + t.tokens.output,
    });
    fillItem(items.get(run.run_id), row);
  });
  ctx.bus.on('run:refresh', load);
  load();
  setInterval(load, REFRESH_MS);
}
