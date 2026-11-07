// Animated DAG with zoom, pan and fit (spec 2026-11-05-console-end-to-end §4). Visuals live in css/app.css keyed on data-state.
import { SVG_NS, svgIcon } from './icons.js';
import { fmtCost, fmtDuration } from './format.js';

const BOX_W = 132;
const BOX_H = 54;
const COL = 156; // minimum column gap; a wide box spreads the columns up to SPREAD times
const SPREAD = 1.5;
const ROW = 84;
const PAD_X = BOX_W / 2 + 10;
const PAD_Y = BOX_H / 2 + 10;
const PAD_BOTTOM = BOX_H + 20;
// Rows that keep the long edges clear of the boxes. A graph with a node not listed falls back to the computed layout.
const LANES = {
  extract_image: -1.95, wordpress_draft: -1.45, bv_wordpress: -1.45, notion_blog: -0.95, writer: -0.5,
  transcribe: 0, extract: 0, kb_curator: 0, pre_validator: 0, youtube_metadata: 0, youtube_upload: 0, bv_youtube: 0, summarizer: 0,
  research: 1.5, notion_research: 1.5, bv_research: 1.5,
};
const PACKAGE_NODE = { wordpress: 'wordpress_draft', youtube: 'youtube_upload' };
const PACKAGE_META = { preview: 'preview only', pending: 'needs you', failed: 'publish failed', publishing: 'publishing', published: 'published' };
const CUES = { pending: 'dot', running: 'arc', paused: 'pause', done: 'tick', error: 'cross', skipped: 'dash' };

const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const FIT_MAX = 2.5;
const STEP = 1.25;
const WHEEL_RATE = 0.0015;
const DRAG_PX = 4;
const TOOLBAR_H = 44; // fit keeps nodes clear of the zoom buttons

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

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
  if (graph.nodes.every((n) => n.id in LANES)) {
    const top = Math.min(...graph.nodes.map((n) => LANES[n.id]));
    const bottom = Math.max(...graph.nodes.map((n) => LANES[n.id]));
    graph.nodes.forEach((n) => pos.set(n.id, { col: depth.get(n.id), y: PAD_Y + (LANES[n.id] - top) * ROW }));
    return { pos, cols: cols.length, height: PAD_Y + PAD_BOTTOM + (bottom - top) * ROW };
  }
  cols.forEach((ids, c) => {
    const ps = (id) => preds.get(id).map((p) => pos.get(p).y);
    const key = (id) => ps(id).reduce((a, b) => a + b, 0) / Math.max(1, ps(id).length);
    [...ids].sort((a, b) => key(a) - key(b)).forEach((id, r) => {
      pos.set(id, { col: c, y: PAD_Y + ((rows - 1) / 2 + r - (ids.length - 1) / 2) * ROW });
    });
  });
  return { pos, cols: cols.length, height: PAD_Y + PAD_BOTTOM + (rows - 1) * ROW };
}

function edgePath(a, b) {
  const mx = (a.x + b.x) / 2;
  return `M${a.x + BOX_W / 2} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${b.x - BOX_W / 2} ${b.y}`;
}

function nodeEl(n, p, bus) {
  const g = svgEl('g', {
    class: 'node', 'data-testid': `node-${n.id}`, 'data-col': p.col, 'data-state': 'pending', role: 'button',
    tabindex: '0', 'aria-pressed': 'false', 'aria-label': `${n.label}: pending`,
  });
  const title = svgEl('title', {});
  title.textContent = `${n.label}: pending`;
  const label = svgEl('text', { class: 'node-label', 'data-testid': 'dag-label', y: -3, 'text-anchor': 'middle' });
  label.textContent = n.label;
  const meta = svgEl('text', { class: 'node-meta', 'data-testid': 'dag-meta', y: 15, 'text-anchor': 'middle' });
  const box = (cls, grow, extra = {}) => svgEl('rect', {
    class: cls, x: -BOX_W / 2 - grow, y: -BOX_H / 2 - grow, width: BOX_W + grow * 2, height: BOX_H + grow * 2, rx: 10 + grow, ...extra,
  });
  g.append(
    title,
    box('ring', 5, { 'data-testid': 'ring' }),
    box('disc', 0),
    label,
    meta,
  );
  // No transform attributes below the node <g>: CSS transforms (shake, pulse) would replace them.
  const [cx, cy] = [BOX_W / 2 - 3, -BOX_H / 2 + 3];
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
  return { g, title, label: n.label, meta };
}

function metaText(node, row, pkg) {
  if (pkg) return PACKAGE_META[pkg];
  const parts = [];
  if (node.state !== 'done') parts.push(node.state);
  const seconds = row?.duration_s ?? node.duration_s;
  if (seconds != null) parts.push(fmtDuration(seconds));
  const cost = fmtCost(row?.cost_usd, row?.cost_estimated);
  if (cost !== '–') parts.push(cost);
  return parts.join(' · ');
}

function toolbar(actions) {
  const button = (testid, text, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.testid = testid;
    b.textContent = text;
    b.addEventListener('click', fn);
    return b;
  };
  const level = document.createElement('span');
  level.className = 'dag-level';
  level.dataset.testid = 'dag-zoom-level';
  const bar = document.createElement('div');
  bar.className = 'dag-tools';
  bar.append(button('dag-zoom-out', 'Zoom out', actions.out), level, button('dag-zoom-in', 'Zoom in', actions.in), button('dag-fit', 'Fit', actions.fit));
  return { bar, level };
}

export function mount(el, ctx) {
  const nodes = new Map();
  const edges = [];
  const view = { k: 1, x: 0, y: 0 };
  let moved = false; // the user took over: a resize no longer refits
  let world = null;
  let svg = null;
  let stage = null;

  const apply = (tools) => {
    stage.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`);
    svg.dataset.zoom = String(Math.round(view.k * 1000) / 1000);
    tools.level.textContent = `${Math.round(view.k * 100)}%`;
  };

  /** A wide box spreads the columns, so an ultra-wide monitor is used, not letterboxed. */
  const place = (colGap) => {
    const at = new Map();
    for (const [id, p] of world.pos) {
      const point = { x: PAD_X + p.col * colGap, y: p.y };
      at.set(id, point);
      nodes.get(id).g.setAttribute('transform', `translate(${point.x} ${point.y})`);
    }
    for (const e of edges) e.path.setAttribute('d', edgePath(at.get(e.from), at.get(e.to)));
    return PAD_X * 2 + (world.cols - 1) * colGap;
  };

  let tools;
  const fit = () => {
    const { width, height } = el.getBoundingClientRect();
    if (!width || !height) return; // hidden view: the observer fits it when it shows again
    moved = false;
    const free = height - TOOLBAR_H;
    const base = PAD_X * 2 + (world.cols - 1) * COL;
    const wanted = clamp((world.height * width) / free, base, base * SPREAD);
    const w = place((wanted - PAD_X * 2) / (world.cols - 1));
    view.k = clamp(Math.min(width / w, free / world.height), ZOOM_MIN, FIT_MAX);
    view.x = (width - w * view.k) / 2;
    view.y = TOOLBAR_H + (free - world.height * view.k) / 2;
    apply(tools);
  };

  const zoomAt = (px, py, factor) => {
    const k = clamp(view.k * factor, ZOOM_MIN, ZOOM_MAX);
    view.x = px - ((px - view.x) * k) / view.k;
    view.y = py - ((py - view.y) * k) / view.k;
    view.k = k;
    moved = true;
    apply(tools);
  };
  const zoomCentre = (factor) => {
    const { width, height } = el.getBoundingClientRect();
    zoomAt(width / 2, height / 2, factor);
  };

  tools = toolbar({ in: () => zoomCentre(STEP), out: () => zoomCentre(1 / STEP), fit: () => fit() });

  const drag = { on: false, dragged: false, x: 0, y: 0 };
  const wire = () => {
    svg.addEventListener('pointerdown', (e) => {
      Object.assign(drag, { on: true, dragged: false, x: e.clientX, y: e.clientY });
    });
    svg.addEventListener('pointermove', (e) => {
      if (!drag.on) return;
      const [dx, dy] = [e.clientX - drag.x, e.clientY - drag.y];
      if (!drag.dragged) {
        if (Math.hypot(dx, dy) < DRAG_PX) return;
        drag.dragged = true;
        svg.setPointerCapture(e.pointerId);
      }
      view.x += dx;
      view.y += dy;
      Object.assign(drag, { x: e.clientX, y: e.clientY });
      moved = true;
      apply(tools);
    });
    const end = () => { drag.on = false; };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    // Capture phase: the node's own click listener must not hear the end of a drag.
    svg.addEventListener('click', (e) => {
      if (drag.dragged) e.stopPropagation();
      drag.dragged = false;
    }, true);
    svg.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return; // a plain wheel scrolls the page, which holds more than the graph
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * WHEEL_RATE));
    }, { passive: false });
  };

  document.addEventListener('keydown', (e) => {
    if (ctx.state.view !== 'graph' || !svg || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest('input, textarea, select')) return;
    if (e.key === '+' || e.key === '=') zoomCentre(STEP);
    else if (e.key === '-') zoomCentre(1 / STEP);
    else if (e.key === '0') fit();
  });

  ctx.bus.on('graph:loaded', (graph) => {
    world = layout(graph);
    svg = svgEl('svg', { class: 'dag-svg', 'data-zoom': '1' });
    stage = svgEl('g', { class: 'dag-stage' });
    const edgeLayer = svgEl('g', { class: 'edges' });
    for (const e of graph.edges) {
      const path = svgEl('path', { class: 'edge', 'data-testid': `edge-${e.from}-${e.to}`, 'data-state': 'pending' });
      edges.push({ path, from: e.from, to: e.to });
      edgeLayer.append(path);
    }
    stage.append(edgeLayer);
    for (const n of graph.nodes) {
      const view_ = nodeEl(n, world.pos.get(n.id), ctx.bus);
      nodes.set(n.id, view_);
      stage.append(view_.g);
    }
    svg.append(stage);
    el.replaceChildren(svg, tools.bar);
    wire();
    fit();
    new ResizeObserver(() => {
      if (!moved) fit();
    }).observe(el);
  });

  ctx.bus.on('run:updated', (run) => {
    const packages = new Map(run.packages.map((p) => [PACKAGE_NODE[p.target], run.dry_run ? 'preview' : p.status]));
    for (const [id, node] of nodes) {
      const n = run.nodes[id];
      const pkg = packages.get(id);
      const state = pkg === 'published' ? 'done' : n.state; // the node skipped a manual package; the work got done later
      const shown = pkg ? PACKAGE_META[pkg] : n.state;
      node.g.setAttribute('data-state', state);
      node.g.setAttribute('data-package', pkg ?? '');
      node.g.setAttribute('aria-label', `${node.label}: ${shown}`);
      node.title.textContent = `${node.label}: ${shown}`;
      node.meta.textContent = metaText(n, run.telemetry.per_node[id], pkg);
    }
    for (const e of edges) e.path.setAttribute('data-state', run.nodes[e.to].state);
  });

  ctx.bus.on('node:selected', (id) => {
    for (const [nid, node] of nodes) node.g.setAttribute('aria-pressed', String(nid === id));
  });
}
