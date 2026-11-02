// Step inspector drawer and final result view (spec 2026-10-31-pipeline-console-ui §8). Styles: css/drawer.css.
import { svgIcon } from './icons.js';

const POLL_MS = 1000;
const CUES = { pending: 'dot', running: 'arc', paused: 'pause', done: 'tick', error: 'cross', skipped: 'dash' };
const KIND_ICONS = { markdown: 'pen', csv: 'grid', json: 'braces', text: 'lines', image: 'image', other: 'file' };
const TYPE_ICONS = { Book: 'book', Article: 'pen', Paper: 'file', Podcast: 'mic', Video: 'video' };
// Glyphs icons.js lacks; same 24x24 stroke style.
const PATHS = {
  trophy: 'M8 4h8v5a4 4 0 0 1-8 0z M8 6H5a3 3 0 0 0 3 4 M16 6h3a3 3 0 0 1-3 4 M12 13v4 M8 21h8 M10 17h4v4h-4z',
  cloudOff: 'M7 18h10a4 4 0 0 0 1-7.9A6 6 0 0 0 7.2 8 4.5 4.5 0 0 0 7 18z M3 3l18 18',
  hourglass: 'M7 3h10 M7 21h10 M8 3c0 5 8 5 8 9s-8 4-8 9 M16 3c0 5-8 5-8 9s8 4 8 9',
  close: 'M7 7l10 10 M17 7L7 17',
  grid: 'M4 5h16v14H4z M4 10h16 M4 15h16 M10 5v14',
  braces: 'M9 4c-3 0-3 2-3 4s-1 4-3 4c2 0 3 2 3 4s0 4 3 4 M15 4c3 0 3 2 3 4s1 4 3 4c-2 0-3 2-3 4s0 4-3 4',
  lines: 'M5 6h14 M5 10h14 M5 14h14 M5 18h9',
  file: 'M6 3h8l4 4v14H6z M14 3v4h4',
  link: 'M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1 M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1',
};

function icon(name, attrs = {}) {
  const svg = svgIcon('dot', attrs);
  svg.dataset.icon = name;
  svg.firstChild.setAttribute('d', PATHS[name] || svgIcon(name).firstChild.getAttribute('d'));
  return svg;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ESC[c]);

/** Inline markdown on already-escaped text: bold and http(s) links only. */
function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => (/^https?:\/\//i.test(url)
      ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : text));
}

/** Markdown to HTML, escaping first: headings, bold, lists, links; nothing else passes through. */
function markdown(src) {
  const out = [];
  let list = null;
  const endList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const line of escapeHtml(src).split(/\r?\n/)) {
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    const li = /^\s*(?:([-*])|\d+[.)])\s+(.*)$/.exec(line);
    if (h) {
      endList();
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
    } else if (li) {
      const tag = li[1] ? 'ul' : 'ol';
      if (list !== tag) {
        endList();
        out.push(`<${tag}>`);
        list = tag;
      }
      out.push(`<li>${inline(li[2])}</li>`);
    } else {
      endList();
      if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
  }
  endList();
  return out.join('');
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

const RENDER = {
  markdown: (text) => {
    const div = el('div', { class: 'md' });
    div.innerHTML = markdown(text); // safe: markdown() escapes before adding its own tags
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

function artifactEl(a, url) {
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

function fmtDuration(s) {
  return s == null ? '–' : `${Math.round(s * 10) / 10}s`;
}

/** Focusable elements inside a container, in DOM order. */
const focusables = (root) => [...root.querySelectorAll('a[href], button')];

function trapKeys(root, close) {
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      close();
    } else if (e.key === 'Tab') {
      e.preventDefault();
      const items = focusables(root);
      const i = items.indexOf(document.activeElement);
      items[(i + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
    }
  });
}

function resultBody(res) {
  if (res.status === 'pending') {
    return [el('div', { class: 'pending', 'data-testid': 'result-pending', role: 'img', 'aria-label': 'waiting for Notion' },
      icon('hourglass'))];
  }
  if (res.status !== 'ready') {
    return [el('div', { class: 'unavailable', 'data-testid': 'result-unavailable' },
      icon('cloudOff', { role: 'img', 'aria-label': 'result unavailable' }),
      el('small', { 'data-testid': 'result-reason' }, res.reason))];
  }
  const link = el('a', { href: res.blog.url, target: '_blank', rel: 'noopener', 'data-testid': 'result-link', 'aria-label': 'open in Notion' },
    icon('link'));
  const md = RENDER.markdown(res.blog.markdown);
  const cards = (res.research || []).map((r) => el('a', {
    class: 'card', href: r.url, target: '_blank', rel: 'noopener', 'data-testid': 'research-card', 'data-type': r.type, title: r.type,
  }, icon(TYPE_ICONS[r.type] || 'globe', { 'data-testid': 'type-icon', role: 'img', 'aria-label': r.type }), el('span', {}, r.title)));
  return [
    el('header', {}, el('h1', { 'data-testid': 'result-title' }, res.blog.title), link),
    md,
    el('div', { class: 'cards' }, ...cards),
  ];
}

export function mount(root, ctx) {
  root.classList.add('drawer');
  let run = { nodes: {} };

  // ---- step inspector ----
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
  panel.append(head);

  const renderHead = () => {
    const n = Object.assign({ state: 'pending', duration_s: null }, run.nodes[panel.dataset.node]);
    panel.dataset.state = n.state;
    cue.replaceChildren(svgIcon(CUES[n.state]));
    cue.setAttribute('aria-label', n.state);
    duration.textContent = fmtDuration(n.duration_s);
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
    panel.replaceChildren(head, list);
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

  // ---- final result ----
  const open = el('button', { type: 'button', class: 'result-open', 'data-testid': 'result-open', 'aria-label': 'final result', title: 'final result' },
    icon('trophy'));
  open.hidden = true;
  const modal = el('div', { class: 'result', 'data-testid': 'result', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'final result' });
  modal.hidden = true;
  let resultTimer;
  const closeModal = () => {
    clearInterval(resultTimer);
    modal.hidden = true;
    open.focus();
  };
  trapKeys(modal, closeModal);

  open.addEventListener('click', () => {
    const runId = ctx.state.runId;
    const close = el('button', { type: 'button', class: 'result-close', 'data-testid': 'result-close', 'aria-label': 'close' }, icon('close'));
    close.addEventListener('click', closeModal);
    const content = el('div', { class: 'content' });
    modal.replaceChildren(el('div', { class: 'sheet' }, close, content));
    modal.dataset.status = 'loading';
    modal.hidden = false;
    close.focus();
    const load = () => ctx.api.getResult(runId)
      .catch((e) => ({ status: 'unavailable', reason: e.message }))
      .then((res) => {
        if (res.status !== 'pending') clearInterval(resultTimer);
        modal.dataset.status = res.status;
        content.replaceChildren(...resultBody(res));
      });
    load();
    resultTimer = setInterval(load, POLL_MS);
  });

  ctx.bus.on('run:selected', () => {
    open.hidden = false;
    closePanel();
    clearInterval(resultTimer);
    modal.hidden = true;
  });

  root.append(open, panel, modal);
}
