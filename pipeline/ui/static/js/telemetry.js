// Telemetry strip and step timeline (spec 2026-10-31-pipeline-console-ui §9). Numbers come from C3 run detail as-is.
import { svgIcon } from './icons.js';
import { ACTIVE } from './runs.js';

const PATHS = {
  total: 'M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M12 7v5l3 2',
  work: 'M4 8h16v11H4z M9 8V5h6v3 M4 13h16',
  tokens: 'M12 3a9 4 0 1 1 0 8a9 4 0 0 1 0-8z M3 7v5c0 2 4 4 9 4s9-2 9-4V7 M3 12v5c0 2 4 4 9 4s9-2 9-4v-5',
  cost: 'M16 7c-1-1-2.4-1.5-4-1.5c-2.2 0-3.5 1.1-3.5 2.7c0 3.6 7.5 2 7.5 5.6c0 1.7-1.4 2.7-4 2.7c-1.6 0-3-.5-4-1.5 M12 3v18',
};
const SEGMENTS = ['input', 'output', 'cache_read', 'cache_creation'];
const NODE_TOKENS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'];
const NO_ROW = { duration_s: null, model: null, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0 };

const fmtCount = (n) => {
  if (n < 1e3) return String(n);
  if (n < 1e6) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}k`;
  return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
};

const fmtDuration = (s) => {
  if (s == null) return '–';
  if (s < 60) return `${+s.toFixed(1)}s`;
  const t = Math.round(s);
  const pad = (n) => String(n).padStart(2, '0');
  if (t < 3600) return `${Math.floor(t / 60)}m ${pad(t % 60)}s`;
  return `${Math.floor(t / 3600)}h ${pad(Math.floor(t / 60) % 60)}m`;
};

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

function metric(name, label) {
  const box = el('span', `tele-metric tele-${name}`, { role: 'img', 'aria-label': label, title: label });
  const val = el('span', 'tele-val', { 'data-testid': `tele-${name}` });
  box.append(icon(name), val);
  return { box, val };
}

function buildStrip() {
  const strip = el('div', 'tele-strip', { 'data-testid': 'tele-strip' });
  const total = metric('total', 'wall time');
  const work = metric('work', 'work time (wall minus HITL wait)');
  const tokens = metric('tokens', 'tokens');
  const stack = el('span', 'tele-stack');
  const segs = SEGMENTS.map((s) => {
    const seg = el('span', `tele-seg seg-${s}`, { 'data-testid': `tele-tok-${s}` });
    stack.append(seg);
    return seg;
  });
  tokens.box.append(stack);
  const cost = metric('cost', 'cost');
  strip.append(total.box, work.box, tokens.box, cost.box);
  return { strip, total: total.val, work: work.val, tokens: tokens.val, segs, cost: cost.val, costBox: cost.box };
}

function renderStrip(view, run) {
  const t = run.telemetry;
  view.total.textContent = fmtDuration(t.total_s);
  view.total.dataset.value = t.total_s;
  view.total.dataset.live = String(ACTIVE.has(run.status));
  view.work.textContent = fmtDuration(t.work_s);
  view.work.dataset.value = t.work_s;
  const counts = SEGMENTS.map((s) => t.tokens[s]);
  const sum = counts.reduce((a, b) => a + b, 0);
  view.tokens.textContent = fmtCount(sum);
  view.tokens.dataset.value = sum;
  view.segs.forEach((seg, i) => {
    seg.dataset.value = counts[i];
    seg.style.flexBasis = `${(counts[i] / (sum || 1)) * 100}%`;
    seg.title = `${SEGMENTS[i].replace('_', ' ')} ${fmtCount(counts[i])}`;
  });
  view.cost.textContent = `$${t.cost_usd.toFixed(2)}`;
  view.cost.dataset.value = t.cost_usd;
  view.costBox.hidden = !t.cost_usd; // subscription billing logs 0 everywhere
}

export function mount(root, ctx) {
  const view = buildStrip();
  const timeline = el('div', 'tele-timeline', { 'data-testid': 'tele-timeline', 'aria-label': 'step timeline' });
  const tip = el('div', 'tele-tip', { 'data-testid': 'tele-tip', role: 'tooltip' });
  tip.hidden = true;
  const wrap = el('div', 'telemetry');
  wrap.append(view.strip, timeline, tip);
  root.replaceChildren(wrap);
  const bars = new Map();

  const unhighlight = () => {
    tip.hidden = true;
    document.querySelectorAll('[data-highlight]').forEach((n) => n.removeAttribute('data-highlight'));
  };
  const highlight = (id, bar) => {
    unhighlight();
    document.querySelectorAll(`[data-testid="dag"] [data-testid="node-${CSS.escape(id)}"]`)
      .forEach((n) => n.setAttribute('data-highlight', ''));
    tip.replaceChildren(...bar.tip.map((line) => Object.assign(el('div', 'tele-tip-line'), { textContent: line })));
    tip.style.setProperty('--x', bar.el.style.left);
    bar.el.parentElement.append(tip);
    tip.hidden = false;
  };

  ctx.bus.on('graph:loaded', (graph) => {
    timeline.replaceChildren(...graph.nodes.map((n) => {
      const row = el('div', 'tl-row', { 'data-testid': `tl-row-${n.id}` });
      const track = el('div', 'tl-track');
      const bar = { el: el('div', 'tl-bar', { 'data-testid': `tl-bar-${n.id}`, tabindex: '0' }), label: n.label, tip: [] };
      bar.el.hidden = true;
      bar.el.addEventListener('mouseenter', () => highlight(n.id, bar));
      bar.el.addEventListener('focus', () => highlight(n.id, bar));
      bar.el.addEventListener('mouseleave', unhighlight);
      bar.el.addEventListener('blur', unhighlight);
      track.append(bar.el);
      row.append(svgIcon(n.icon, { role: 'img', 'aria-label': n.label, 'aria-hidden': 'false' }), track);
      bars.set(n.id, bar);
      return row;
    }));
  });

  ctx.bus.on('run:updated', (run) => {
    renderStrip(view, run);
    const now = run.started_at + run.telemetry.total_s;
    const spans = [...bars.keys()].filter((id) => run.nodes[id].started_at != null)
      .map((id) => [id, run.nodes[id].started_at, run.nodes[id].ended_at ?? now]);
    const t0 = Math.min(run.started_at, ...spans.map((s) => s[1]));
    const width = Math.max(now, ...spans.map((s) => s[2])) - t0 || 1;
    for (const bar of bars.values()) bar.el.hidden = true;
    for (const [id, start, end] of spans) {
      const bar = bars.get(id);
      const state = run.nodes[id].state;
      const row = { ...NO_ROW, ...run.telemetry.per_node[id] };
      const tokens = NODE_TOKENS.reduce((a, k) => a + row[k], 0);
      const dur = fmtDuration(row.duration_s ?? end - start);
      Object.assign(bar.el.style, { left: `${((start - t0) / width) * 100}%`, width: `${((end - start) / width) * 100}%` });
      bar.el.dataset.state = state;
      bar.el.dataset.span = end - start;
      bar.el.setAttribute('aria-label', `${bar.label}: ${state}, ${dur}`);
      bar.tip = [bar.label, `${dur} · ${fmtCount(tokens)} tokens`, row.model].filter(Boolean);
      bar.el.hidden = false;
    }
  });
}
