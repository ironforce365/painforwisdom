// Launch popup (spec 2026-11-05-console-end-to-end S1-S5): input tiles, POST /api/runs, busy lockout with a link to the active run.
import { svgIcon } from './icons.js';
import { ACTIVE } from './runs.js';

const POLL_MS = 1000;
const CLOSE_MS = 160;
const PATHS = {
  play: 'M8 5v14l11-7z',
  transcript: 'M6 3h9l4 4v14H6z M14 3v5h5 M9 12h7 M9 16h7',
  lock: 'M6 11h12v10H6z M8 11V8a4 4 0 0 1 8 0v3',
  upload: 'M12 16V4 M7 9l5-5 5 5 M4 15v5h16v-5',
};
// Mirrors launcher.VIDEO_EXTENSIONS plus transcripts; the server re-checks.
const ACCEPT = 'video/*,.mp4,.mov,.mkv,.webm,.m4v,.avi,.txt';
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
  // S3: it can still start; the tile only warns which runs already used it.
  if (input.previous_runs?.length) {
    const names = input.previous_runs.join(', ');
    tile.dataset.warn = 'true';
    tile.append(el('span', { class: 'tile-previous', 'data-testid': 'tile-previous', title: names }, `Already ran: ${names}`));
  }
  tile.addEventListener('click', () => start(tile, input.name));
  return tile;
}

export function mount(root, ctx) {
  const button = el('button', {
    type: 'button', class: 'launch-button', 'data-testid': 'launch-button', 'data-state': 'idle',
  }, ownIcon('play', 'icon launch-play'), svgIcon('arc', { class: 'icon launch-spin' }), 'Start a new run');
  const activeLink = (testid) => {
    const link = el('button', { type: 'button', class: 'launch-active', 'data-testid': testid });
    link.addEventListener('click', () => {
      if (!modal.hidden) hide();
      ctx.bus.emit('run:selected', busyId);
    });
    return link;
  };
  const headerLink = activeLink('launch-active-link');
  headerLink.hidden = true;
  root.append(headerLink, button);

  const auto = el('button', {
    type: 'button', class: 'launch-auto', role: 'switch', 'aria-checked': 'false', 'data-testid': 'launch-auto',
    title: 'Skip the approval pauses',
  }, svgIcon('bolt'), 'Auto-approve');
  const language = el('input', {
    type: 'text', class: 'launch-language', 'data-testid': 'launch-language', spellcheck: 'false', autocomplete: 'off',
  });
  const languageField = el('label', {
    class: 'launch-language-field', title: 'Language spoken in the video, as a code like en or es. Whisper and the YouTube fields use it.',
  }, 'Language', language);
  const lockLink = activeLink('launch-lock-link');
  const lock = el('span', { class: 'launch-lock', 'data-testid': 'launch-lock' },
    ownIcon('lock', 'icon'), 'Locked: active run ', lockLink);
  const close = el('button', {
    type: 'button', class: 'launch-close', 'data-testid': 'launch-close',
  }, svgIcon('cross'), 'Close');
  const upload = el('button', {
    type: 'button', class: 'launch-upload', 'data-testid': 'launch-upload', 'data-state': 'idle',
    title: 'Upload a video or transcript (or drop files here)',
  }, ownIcon('upload', 'icon launch-up'), svgIcon('arc', { class: 'icon launch-spin' }), 'Upload');
  const failure = el('div', { class: 'launch-error', 'data-testid': 'launch-error', role: 'alert' });
  failure.hidden = true;
  const picker = el('input', { type: 'file', multiple: '', accept: ACCEPT, hidden: '', 'data-testid': 'launch-file' });
  const tiles = el('div', { class: 'launch-tiles', 'data-testid': 'launch-tiles' });
  const panel = el('div', { class: 'launch-panel' },
    el('div', { class: 'launch-head' }, el('strong', { class: 'launch-title' }, 'Start a run'), auto, languageField, upload, picker, lock, close), failure, tiles);
  const modal = el('div', {
    class: 'launch-overlay', 'data-testid': 'launch-modal', role: 'dialog', 'aria-modal': 'true',
    'aria-label': 'choose an input', 'data-locked': 'false',
  }, panel);
  modal.hidden = true;
  document.body.append(modal);

  let busyId = null;
  let timer;
  let closing = modal.animate([], 0);

  const showFailure = (text) => {
    failure.textContent = text;
    failure.hidden = !text;
  };

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
    button.lastChild.textContent = busy ? 'Run active' : 'Start a new run';
    headerLink.hidden = !busy;
    headerLink.textContent = `Go to ${id}`;
    lockLink.textContent = String(id);
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
    showFailure('');
    modal.hidden = false;
    close.focus();
    await load(false);
  }

  /** Render the inbox; `fresh` (after an upload) focuses the newest tile. */
  async function load(fresh) {
    let body;
    try {
      body = await ctx.api.listInputs();
    } catch {
      ctx.bus.emit('ui:error', 'inputs unavailable');
      hide();
      return;
    }
    if (!fresh) language.value = body.default_language; // each opening starts over: the last video's language is not the next one's
    const empty = el('div', { class: 'launch-empty', 'data-testid': 'launch-empty', role: 'img', 'aria-label': 'inbox is empty' },
      svgIcon('inbox'), 'The inbox is empty. Upload a file or drop one here.', el('span', { class: 'launch-path', 'data-testid': 'launch-path' }, body.inbox));
    tiles.replaceChildren(...(body.inputs.length ? body.inputs.map((input) => tileEl(input, start)) : [empty]));
    // Closed (or focus moved) while inputs loaded: do not pull focus back into the popup.
    if (fresh || document.activeElement === close) (tiles.querySelector('.tile') ?? close).focus();
  }

  // Sequential PUTs; a failure (409 exists, 400 bad name) flags the button and the badge, the rest still go.
  async function send(files) {
    upload.disabled = true;
    upload.dataset.state = 'busy';
    let sent = 0;
    const refused = [];
    for (const file of files) {
      try {
        await ctx.api.uploadInput(file);
        sent += 1;
      } catch (e) {
        const why = e.body?.detail ?? e.message;
        refused.push(`${file.name}: ${why}`);
        ctx.bus.emit('ui:error', `upload failed: ${file.name} ${why}`);
      }
    }
    showFailure(refused.join(' · '));
    picker.value = '';
    upload.disabled = false;
    upload.dataset.state = sent === files.length ? 'idle' : 'error';
    if (sent) await load(true);
  }

  async function start(tile, name) {
    tile.removeAttribute('data-state');
    showFailure('');
    try {
      const { run_id: id } = await ctx.api.launchRun({
        input: name, auto_approve: auto.getAttribute('aria-checked') === 'true', language: language.value.trim(),
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
      const why = e.body?.detail ?? e.message;
      showFailure(`${name}: ${why}`);
      ctx.bus.emit('ui:error', `launch failed: ${why}`);
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
  upload.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => send([...picker.files]));
  modal.addEventListener('dragover', (e) => {
    e.preventDefault();
    modal.dataset.drop = 'true';
  });
  modal.addEventListener('dragleave', () => { modal.dataset.drop = 'false'; });
  modal.addEventListener('drop', (e) => {
    e.preventDefault();
    modal.dataset.drop = 'false';
    send([...e.dataTransfer.files]);
  });
  close.addEventListener('click', hide);
  auto.addEventListener('click', () => auto.setAttribute('aria-checked', String(auto.getAttribute('aria-checked') !== 'true')));
  modal.addEventListener('click', (e) => {
    if (e.target === modal) hide();
  });
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hide();
    else if (e.key in STEPS && e.target !== language) {
      e.preventDefault();
      move(STEPS[e.key]);
    } else if (e.key === 'Tab') trap(e);
  });
  ctx.bus.on('run:updated', (run) => {
    if (ACTIVE.has(run.status) && busyId === null) setBusy(run.run_id);
  });
  ctx.bus.on('runs:listed', (runs) => {
    const active = runs.find((r) => ACTIVE.has(r.status));
    if (active && busyId === null) setBusy(active.run_id);
  });
}
