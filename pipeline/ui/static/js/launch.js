// Launch popup (spec 2026-10-31-pipeline-console-ui §7): input tiles, POST /api/runs, busy lockout.
import { svgIcon } from './icons.js';
import { ACTIVE } from './runs.js';

const POLL_MS = 1000;
const CLOSE_MS = 160;
const PATHS = {
  play: 'M8 5v14l11-7z',
  transcript: 'M6 3h9l4 4v14H6z M14 3v5h5 M9 12h7 M9 16h7',
  lock: 'M6 11h12v10H6z M8 11V8a4 4 0 0 1 8 0v3',
};
const STEPS = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
const AGE_UNITS = [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]];
const motion = matchMedia('(prefers-reduced-motion: no-preference)');

// Icons this module needs that icons.js lacks; icons.js belongs to §6.
function ownIcon(name, cls) {
  const svg = svgIcon('dot', { class: cls });
  svg.firstChild.setAttribute('d', PATHS[name]);
  return svg;
}

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

function age(mtime) {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - mtime));
  const [unit, n] = AGE_UNITS.find(([, size]) => s >= size);
  return `${Math.floor(s / n)}${unit}`;
}

function tileEl(input, start) {
  const tile = el('button', {
    type: 'button', class: 'tile', 'data-testid': `input-${input.name}`, 'data-kind': input.kind,
    title: input.name, 'aria-label': `start ${input.kind} ${input.name}`,
  },
  input.kind === 'video' ? svgIcon('video', { class: 'icon tile-icon' }) : ownIcon('transcript', 'icon tile-icon'),
  svgIcon('cross', { class: 'icon tile-error' }),
  el('span', { class: 'tile-name', 'data-testid': 'tile-name' }, input.name),
  el('span', { class: 'tile-age', 'data-testid': 'tile-age' }, age(input.mtime)));
  tile.addEventListener('click', () => start(tile, input.name));
  return tile;
}

export function mount(root, ctx) {
  const button = el('button', {
    type: 'button', class: 'launch-button', 'data-testid': 'launch-button', 'data-state': 'idle',
    'aria-label': 'start a run', title: 'start a run',
  }, ownIcon('play', 'icon launch-play'), svgIcon('arc', { class: 'icon launch-spin' }));
  root.append(button);

  const auto = el('button', {
    type: 'button', class: 'launch-auto', role: 'switch', 'aria-checked': 'false', 'data-testid': 'launch-auto',
    'aria-label': 'auto-approve HITL', title: 'auto-approve: skip the HITL pause',
  }, svgIcon('bolt'));
  const lock = el('span', { class: 'launch-lock', 'data-testid': 'launch-lock', role: 'img', 'aria-label': 'a run is active' },
    ownIcon('lock', 'icon'));
  const close = el('button', {
    type: 'button', class: 'launch-close', 'data-testid': 'launch-close', 'aria-label': 'close', title: 'close',
  }, svgIcon('cross'));
  const tiles = el('div', { class: 'launch-tiles', 'data-testid': 'launch-tiles' });
  const panel = el('div', { class: 'launch-panel' }, el('div', { class: 'launch-head' }, auto, lock, close), tiles);
  const modal = el('div', {
    class: 'launch-overlay', 'data-testid': 'launch-modal', role: 'dialog', 'aria-modal': 'true',
    'aria-label': 'choose an input', 'data-locked': 'false',
  }, panel);
  modal.hidden = true;
  document.body.append(modal);

  let busyId = null;
  let timer;
  let closing = modal.animate([], 0);

  // Own 1 s check of the blocking run: app.js only polls the selected one.
  const check = () => ctx.api.getRun(busyId).then(
    (run) => setBusy(ACTIVE.has(run.status) ? busyId : null),
    (e) => setBusy(e.status === 404 ? null : busyId),
  );
  function setBusy(id) {
    busyId = id;
    const busy = id !== null;
    button.disabled = busy;
    button.dataset.state = busy ? 'busy' : 'idle';
    modal.dataset.locked = String(busy);
    clearTimeout(timer);
    if (busy) timer = setTimeout(check, POLL_MS);
  }

  function hide() {
    closing = modal.animate([{ opacity: 1 }, { opacity: 0, transform: 'scale(.96)' }], CLOSE_MS * motion.matches);
    closing.onfinish = () => { modal.hidden = true; };
    button.focus();
  }

  async function show() {
    closing.cancel();
    modal.hidden = false;
    close.focus();
    let body;
    try {
      body = await ctx.api.listInputs();
    } catch {
      ctx.bus.emit('ui:error', 'inputs unavailable');
      hide();
      return;
    }
    const empty = el('div', { class: 'launch-empty', 'data-testid': 'launch-empty', role: 'img', 'aria-label': 'inbox is empty' },
      svgIcon('inbox'), el('span', { class: 'launch-path' }, body.inbox));
    tiles.replaceChildren(...(body.inputs.length ? body.inputs.map((input) => tileEl(input, start)) : [empty]));
    // Closed (or focus moved) while inputs loaded: do not pull focus back into the popup.
    if (document.activeElement === close) (tiles.querySelector('.tile') ?? close).focus();
  }

  async function start(tile, name) {
    tile.removeAttribute('data-state');
    try {
      const { run_id: id } = await ctx.api.launchRun({
        input: name, auto_approve: auto.getAttribute('aria-checked') === 'true',
      });
      setBusy(id);
      hide();
      ctx.bus.emit('run:selected', id);
    } catch (e) {
      if (e.status === 409) {
        setBusy(e.body.run_id);
        return;
      }
      tile.dataset.state = 'error';
      ctx.bus.emit('ui:error', `launch failed: ${e.message}`);
    }
  }

  function move(step) {
    const list = [...tiles.querySelectorAll('.tile')];
    const i = list.indexOf(document.activeElement);
    list[(i + step + list.length) % list.length]?.focus();
  }

  function trap(e) {
    const focusable = [...modal.querySelectorAll('button:not([disabled])')];
    const edge = e.shiftKey ? focusable[0] : focusable.at(-1);
    if (document.activeElement !== edge) return;
    e.preventDefault();
    (e.shiftKey ? focusable.at(-1) : focusable[0]).focus();
  }

  button.addEventListener('click', show);
  close.addEventListener('click', hide);
  auto.addEventListener('click', () => auto.setAttribute('aria-checked', String(auto.getAttribute('aria-checked') !== 'true')));
  modal.addEventListener('click', (e) => {
    if (e.target === modal) hide();
  });
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hide();
    else if (e.key in STEPS) {
      e.preventDefault();
      move(STEPS[e.key]);
    } else if (e.key === 'Tab') trap(e);
  });
  ctx.bus.on('run:updated', (run) => {
    if (ACTIVE.has(run.status) && busyId === null) setBusy(run.run_id);
  });
}
