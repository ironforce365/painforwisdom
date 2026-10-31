// Run list (spec 2026-10-31-pipeline-console-ui §6.3): status dots, live runs on top, 5 s refresh.
import { svgIcon } from './icons.js';

export const ACTIVE = new Set(['running', 'waiting_hitl', 'waiting_reply']);
const GLYPHS = { done: 'tick', failed: 'cross', crashed: 'bolt', running: 'arc', waiting_hitl: 'hand', waiting_reply: 'chat' };
const REFRESH_MS = 5000;

function setStatus(item, status) {
  item.dataset.status = status;
  item.setAttribute('aria-label', `${item.dataset.runId} ${status}`);
}

function itemEl(run, ctx) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'run';
  item.dataset.testid = `run-${run.run_id}`;
  item.dataset.runId = run.run_id;
  item.dataset.verdict = String(run.verdict);
  item.setAttribute('aria-pressed', String(run.run_id === ctx.state.runId));
  item.title = `${run.input.name} · ${run.status}`;
  setStatus(item, run.status);
  for (const [status, glyph] of Object.entries(GLYPHS)) {
    const g = svgIcon(glyph, { class: `icon glyph glyph-${status}` });
    if (status === 'running') g.dataset.testid = 'spinner';
    item.append(g);
  }
  item.addEventListener('click', () => ctx.bus.emit('run:selected', run.run_id));
  return item;
}

function emptyEl() {
  const empty = document.createElement('div');
  empty.className = 'runs-empty';
  empty.dataset.testid = 'runs-empty';
  empty.setAttribute('role', 'img');
  empty.setAttribute('aria-label', 'no runs yet');
  empty.append(svgIcon('inbox'));
  return empty;
}

export function mount(el, ctx) {
  const render = (runs) => {
    const ordered = [...runs].sort((a, b) => ACTIVE.has(b.status) - ACTIVE.has(a.status));
    el.replaceChildren(...(ordered.length ? ordered.map((r) => itemEl(r, ctx)) : [emptyEl()]));
    if (!ctx.state.runId && ordered.length) ctx.bus.emit('run:selected', ordered[0].run_id);
  };
  // On failure keep the last list; api.js health reporting shows the badge.
  const load = () => ctx.api.listRuns().then((body) => render(body.runs), () => {});

  ctx.bus.on('run:selected', (id) => {
    for (const item of el.querySelectorAll('.run')) item.setAttribute('aria-pressed', String(item.dataset.runId === id));
  });
  ctx.bus.on('run:updated', (run) => {
    el.querySelectorAll(`[data-run-id="${CSS.escape(run.run_id)}"]`).forEach((item) => setStatus(item, run.status));
  });
  load();
  setInterval(load, REFRESH_MS);
}
