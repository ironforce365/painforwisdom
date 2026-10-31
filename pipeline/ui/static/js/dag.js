// Animated DAG (spec 2026-10-31-pipeline-console-ui §6.2). Visuals live in css/app.css keyed on data-state.
import { SVG_NS, svgIcon } from './icons.js';

const COL = 120;
const ROW = 96;
const PAD = 56;
const R = 24;
const CUES = { pending: 'dot', running: 'arc', paused: 'pause', done: 'tick', error: 'cross', skipped: 'dash' };

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/** Columns by longest-path depth; rows ordered by the mean height of each node's parents. */
function layout(graph) {
  const preds = new Map(graph.nodes.map((n) => [n.id, []]));
  graph.edges.forEach((e) => preds.get(e.to).push(e.from));
  const depth = new Map();
  const depthOf = (id) => {
    if (!depth.has(id)) depth.set(id, Math.max(-1, ...preds.get(id).map(depthOf)) + 1);
    return depth.get(id);
  };
  const cols = [];
  graph.nodes.forEach((n) => (cols[depthOf(n.id)] ||= []).push(n.id));
  const rows = Math.max(...cols.map((c) => c.length));
  const pos = new Map();
  cols.forEach((ids, c) => {
    const ps = (id) => preds.get(id).map((p) => pos.get(p).y);
    const key = (id) => ps(id).reduce((a, b) => a + b, 0) / Math.max(1, ps(id).length);
    [...ids].sort((a, b) => key(a) - key(b)).forEach((id, r) => {
      pos.set(id, { col: c, x: PAD + c * COL, y: PAD + ((rows - 1) / 2 + r - (ids.length - 1) / 2) * ROW });
    });
  });
  return { pos, width: PAD * 2 + (cols.length - 1) * COL, height: PAD * 2 + (rows - 1) * ROW };
}

function edgePath(a, b) {
  const mx = (a.x + b.x) / 2;
  return `M${a.x + R} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${b.x - R} ${b.y}`;
}

function nodeEl(n, p, bus) {
  const g = svgEl('g', {
    class: 'node', 'data-testid': `node-${n.id}`, 'data-col': p.col, 'data-state': 'pending', role: 'button',
    tabindex: '0', 'aria-pressed': 'false', 'aria-label': `${n.label}: pending`,
    transform: `translate(${p.x} ${p.y})`,
  });
  const title = svgEl('title', {});
  title.textContent = `${n.label}: pending`;
  g.append(
    title,
    svgEl('circle', { class: 'ring', 'data-testid': 'ring', r: R + 6 }),
    svgEl('circle', { class: 'disc', r: R }),
    svgIcon(n.icon, { x: -11, y: -11, width: 22, height: 22 }),
  );
  // No transform attributes below the node <g>: CSS transforms (shake, pulse) would replace them.
  const [cx, cy] = [R * 0.72, -R * 0.72];
  for (const [state, glyph] of Object.entries(CUES)) {
    const cue = svgEl('g', { class: `cue cue-${state}`, 'data-testid': `cue-${state}` });
    cue.append(svgEl('circle', { cx, cy, r: 9 }), svgIcon(glyph, { x: cx - 7, y: cy - 7, width: 14, height: 14 }));
    g.append(cue);
  }
  const select = () => bus.emit('node:selected', n.id);
  g.addEventListener('click', select);
  g.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      select();
    }
  });
  return { g, title, label: n.label };
}

export function mount(el, ctx) {
  const nodes = new Map();
  const edges = [];

  ctx.bus.on('graph:loaded', (graph) => {
    const { pos, width, height } = layout(graph);
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, class: 'dag-svg' });
    const edgeLayer = svgEl('g', { class: 'edges' });
    for (const e of graph.edges) {
      const path = svgEl('path', {
        class: 'edge', 'data-testid': `edge-${e.from}-${e.to}`, 'data-state': 'pending', d: edgePath(pos.get(e.from), pos.get(e.to)),
      });
      edges.push({ path, to: e.to });
      edgeLayer.append(path);
    }
    svg.append(edgeLayer);
    for (const n of graph.nodes) {
      const view = nodeEl(n, pos.get(n.id), ctx.bus);
      nodes.set(n.id, view);
      svg.append(view.g);
    }
    el.replaceChildren(svg);
  });

  ctx.bus.on('run:updated', (run) => {
    for (const [id, view] of nodes) {
      const state = run.nodes[id].state;
      view.g.setAttribute('data-state', state);
      view.g.setAttribute('aria-label', `${view.label}: ${state}`);
      view.title.textContent = `${view.label}: ${state}`;
    }
    for (const e of edges) e.path.setAttribute('data-state', run.nodes[e.to].state);
  });

  ctx.bus.on('node:selected', (id) => {
    for (const [nid, view] of nodes) view.g.setAttribute('aria-pressed', String(nid === id));
  });
}
