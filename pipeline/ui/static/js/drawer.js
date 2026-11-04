// Step inspector drawer (spec 2026-10-31-pipeline-console-ui §8). Styles: css/drawer.css.
import { svgIcon } from './icons.js';
import { renderMarkdown } from './markdown.js';

const POLL_MS = 1000;
const WIDTH_KEY = 'pfw.drawer.width';
const WIDTH_MIN = 320;
const WIDTH_STEP = { ArrowLeft: 40, ArrowRight: -40 };
const AUDIT_KEY = /_findings/;
const FINDING_ICONS = { ok: 'tick', core: 'cross', secondary: 'warn', PASS: 'tick', PARTIAL: 'warn', FAIL: 'cross' };
const CUES = { pending: 'dot', running: 'arc', paused: 'pause', done: 'tick', error: 'cross', skipped: 'dash' };
const KIND_ICONS = { markdown: 'pen', csv: 'grid', json: 'braces', text: 'lines', image: 'image', other: 'file' };
// Glyphs icons.js lacks; same 24x24 stroke style.
const PATHS = {
  cloudOff: 'M7 18h10a4 4 0 0 0 1-7.9A6 6 0 0 0 7.2 8 4.5 4.5 0 0 0 7 18z M3 3l18 18',
  close: 'M7 7l10 10 M17 7L7 17',
  grid: 'M4 5h16v14H4z M4 10h16 M4 15h16 M10 5v14',
  braces: 'M9 4c-3 0-3 2-3 4s-1 4-3 4c2 0 3 2 3 4s0 4 3 4 M15 4c3 0 3 2 3 4s1 4 3 4c-2 0-3 2-3 4s0 4-3 4',
  lines: 'M5 6h14 M5 10h14 M5 14h14 M5 18h9',
  file: 'M6 3h8l4 4v14H6z M14 3v4h4',
  link: 'M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1 M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1',
  warn: 'M12 4l9 16H3z M12 10v4 M12 17h.01',
};

export function icon(name, attrs = {}) {
  const svg = svgIcon('dot', attrs);
  svg.dataset.icon = name;
  svg.firstChild.setAttribute('d', PATHS[name] || svgIcon(name).firstChild.getAttribute('d'));
  return svg;
}

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

/** RFC 4180-ish: quoted fields, "" escapes, CRLF; blank rows dropped. */
function parseCsv(text) {
  const rows = [[]];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted && c === '"' && text[i + 1] === '"') {
      cell += '"';
      i += 1;
    } else if (c === '"') {
      quoted = !quoted;
    } else if (!quoted && (c === ',' || c === '\n')) {
      rows.at(-1).push(cell);
      cell = '';
      if (c === '\n') rows.push([]);
    } else if (quoted || c !== '\r') {
      cell += c;
    }
  }
  rows.at(-1).push(cell);
  return rows.filter((r) => r.join('') !== '');
}

function prettyJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export const RENDER = {
  markdown: (text) => {
    const div = el('div', { class: 'md' });
    div.innerHTML = renderMarkdown(text); // safe: renderMarkdown() escapes before adding its own tags
    return div;
  },
  csv: (text) => {
    const [head, ...body] = parseCsv(text);
    const row = (cells, tag) => el('tr', {}, ...cells.map((c) => el(tag, {}, c)));
    return el('table', {}, el('thead', {}, row(head, 'th')), el('tbody', {}, ...body.map((r) => row(r, 'td'))));
  },
  json: (text) => el('pre', {}, prettyJson(text)),
  text: (text) => el('pre', {}, text),
};

export function artifactEl(a, url) {
  const card = el('section', { class: 'art', 'data-testid': `artifact-${a.name}`, 'data-kind': a.kind, 'data-state': 'loading' },
    el('div', { class: 'art-head', title: `${a.name} · ${a.bytes} B` }, icon(KIND_ICONS[a.kind], { 'aria-label': a.kind, role: 'img' }),
      el('span', {}, a.name)));
  const body = (child) => {
    child.dataset.testid = 'artifact-body';
    card.append(child);
    card.dataset.state = 'ready';
  };
  if (a.kind === 'image') {
    body(el('img', { src: url, alt: a.name }));
  } else if (a.kind === 'other') {
    body(el('a', { href: url, download: a.name, 'aria-label': `download ${a.name}` }, icon('file')));
  } else {
    fetch(url).then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`)))).then(
      (text) => body(RENDER[a.kind](text)),
      () => {
        card.dataset.state = 'error';
        card.append(icon('cross', { role: 'img', 'aria-label': 'could not load' }));
      },
    );
  }
  return card;
}

/** end.result items are JSON cut at 500 chars (C1), so an item may not parse: show it raw. */
function parseFinding(item) {
  try {
    return { name: item, ...JSON.parse(item) };
  } catch {
    return { name: item };
  }
}

function findingEl(item) {
  const f = parseFinding(item);
  const level = f.ok ? 'ok' : (f.severity || f.verdict || 'unknown');
  return el('li', { class: 'finding', 'data-testid': 'finding', 'data-level': level, title: f.detail || f.name },
    icon(FINDING_ICONS[level] || 'dot', { role: 'img', 'aria-label': level }), el('span', {}, f.name));
}

const factText = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v));

/** Facts (C4 row + result scalars) as a key/value list, then findings; each empty when there is nothing. */
function infoEls(n) {
  const entries = Object.entries(n.result);
  const scalars = entries.filter(([k]) => !AUDIT_KEY.test(k));
  const items = entries.filter(([k]) => AUDIT_KEY.test(k)).flatMap(([, v]) => v);
  const pairs = Object.entries({ ...n.facts, ...Object.fromEntries(scalars) });
  const facts = pairs.map(([k, v]) => el('div', { class: 'fact', 'data-testid': `fact-${k}` }, el('dt', {}, k), el('dd', {}, factText(v))));
  return [el('dl', { class: 'facts', 'data-testid': 'drawer-facts' }, ...facts),
    el('ul', { class: 'findings', 'data-testid': 'drawer-findings' }, ...items.map(findingEl))];
}

function loadWidth() {
  try {
    return Number(localStorage.getItem(WIDTH_KEY));
  } catch {
    return 0; // storage blocked: the default width stands
  }
}

function saveWidth(px) {
  try {
    localStorage.setItem(WIDTH_KEY, String(px));
  } catch {
    // storage blocked: the width lasts this page only
  }
}

/** Left-edge drag and ←/→ resize of the panel; the chosen width survives reloads (spec 2026-11-03 §3.5). */
function resizer(panel) {
  const handle = el('div', {
    class: 'resize', 'data-testid': 'drawer-resize', role: 'separator', 'aria-orientation': 'vertical',
    'aria-label': 'resize', tabindex: '0', 'aria-valuemin': WIDTH_MIN,
  });
  const setWidth = (px) => {
    const width = Math.round(Math.min(Math.max(px, WIDTH_MIN), window.innerWidth));
    panel.style.setProperty('--drawer-w', `${width}px`);
    handle.setAttribute('aria-valuenow', width);
    handle.setAttribute('aria-valuemax', window.innerWidth);
    return width;
  };
  handle.addEventListener('pointerdown', (down) => {
    handle.setPointerCapture(down.pointerId);
    const move = (e) => setWidth(window.innerWidth - e.clientX);
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', (up) => {
      handle.removeEventListener('pointermove', move);
      saveWidth(setWidth(window.innerWidth - up.clientX));
    }, { once: true });
  });
  handle.addEventListener('keydown', (e) => {
    const step = WIDTH_STEP[e.key];
    if (step) {
      e.preventDefault();
      saveWidth(setWidth(panel.getBoundingClientRect().width + step));
    }
  });
  const stored = loadWidth();
  if (stored > 0) setWidth(stored);
  return handle;
}

function fmtDuration(s) {
  return s == null ? '–' : `${Math.round(s * 10) / 10}s`;
}

export function mount(root, ctx) {
  root.classList.add('drawer');
  let run = { nodes: {} };

  const panel = el('aside', { class: 'panel', 'data-testid': 'drawer', 'aria-label': 'step inspector' });
  panel.hidden = true;
  let listTimer;
  const closePanel = () => {
    clearInterval(listTimer);
    panel.hidden = true;
  };
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePanel();
  });

  const close = el('button', { type: 'button', 'data-testid': 'drawer-close', 'aria-label': 'close' }, icon('close'));
  close.addEventListener('click', closePanel);
  const nodeIcon = el('span', { class: 'node-icon', role: 'img' });
  const cue = el('span', { class: 'state-cue', role: 'img' });
  const duration = el('small', { 'data-testid': 'drawer-duration' });
  const head = el('header', {}, nodeIcon, cue, duration, close);
  const handle = resizer(panel);
  panel.append(handle, head);
  const info = el('div', { class: 'info' });

  const renderHead = () => {
    const n = Object.assign({ state: 'pending', duration_s: null, facts: {}, result: {} }, run.nodes[panel.dataset.node]);
    panel.dataset.state = n.state;
    cue.replaceChildren(svgIcon(CUES[n.state]));
    cue.setAttribute('aria-label', n.state);
    duration.textContent = fmtDuration(n.duration_s);
    // Rebuild only on change: the poll would otherwise drop a text selection every second.
    const sig = JSON.stringify([panel.dataset.node, n.facts, n.result]);
    if (info.dataset.sig !== sig) {
      info.dataset.sig = sig;
      info.replaceChildren(...infoEls(n));
    }
  };

  ctx.bus.on('node:selected', (id) => {
    const node = ctx.state.graph.nodes.find((n) => n.id === id);
    const runId = ctx.state.runId;
    clearInterval(listTimer);
    panel.dataset.node = id;
    nodeIcon.replaceChildren(svgIcon(node.icon));
    nodeIcon.setAttribute('aria-label', node.label);
    nodeIcon.title = node.label;
    // A fresh list per selection: a late answer for the previous node lands in a detached element.
    const list = el('div', { class: 'arts', 'data-testid': 'artifact-list' });
    // Shown by CSS when the body holds no fact, finding or artifact, so the listing's answer decides no branch.
    const nothing = el('div', { class: 'unavailable', 'data-testid': 'drawer-nothing' },
      icon('cloudOff', { role: 'img', 'aria-label': 'nothing recorded' }));
    panel.replaceChildren(handle, head, el('div', { class: 'body', 'data-testid': 'drawer-body' }, info, list, nothing));
    renderHead();
    panel.hidden = false;
    const shown = new Set();
    const load = () => ctx.api.listArtifacts(runId).then((body) => {
      body.artifacts.filter((a) => a.node === id && !shown.has(a.id)).forEach((a) => {
        shown.add(a.id);
        list.append(artifactEl(a, ctx.api.artifactUrl(runId, a.id)));
      });
    }, () => {}); // keep polling; api.js health reporting shows the badge
    load();
    listTimer = setInterval(load, POLL_MS); // artifacts of a running node appear while open (§8.1)
  });

  ctx.bus.on('run:updated', (r) => {
    run = r;
    renderHead();
  });

  ctx.bus.on('run:selected', closePanel);

  root.append(panel);
}
