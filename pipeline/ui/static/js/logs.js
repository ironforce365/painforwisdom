// Logs view: the tail of stdout.log, following while active (spec 2026-11-05-console-end-to-end §4).
import { el } from './drawer.js';

const NEAR_BOTTOM_PX = 24;

/** How many lines at the end of `old` open `next`: those stay in the DOM, so a selection in them survives. */
function overlap(old, next) {
  for (let k = Math.min(old.length, next.length); k > 0; k -= 1) {
    let same = true;
    for (let i = 0; i < k && same; i += 1) same = old[old.length - k + i] === next[i];
    if (same) return k;
  }
  return 0;
}

export function mount(root, ctx) {
  const count = el('span', { class: 'logs-count', 'data-testid': 'logs-count' }, '0 lines');
  const follow = el('button', { type: 'button', class: 'logs-follow', 'data-testid': 'logs-follow', role: 'switch', 'aria-checked': 'true' }, 'Follow');
  const empty = el('div', { class: 'logs-empty', 'data-testid': 'logs-empty' }, 'No log yet for this run.');
  const lines = el('div', { class: 'logs-lines' });
  const scroll = el('div', { class: 'logs-scroll', 'data-testid': 'logs-scroll', tabindex: '0' }, empty, lines);
  root.append(el('div', { class: 'logs-head' }, count, follow), scroll);

  let shown = [];
  let bytes = -1;

  const toBottom = () => {
    if (follow.getAttribute('aria-checked') === 'true') scroll.scrollTop = scroll.scrollHeight;
  };
  const setFollow = (on) => {
    follow.setAttribute('aria-checked', String(on));
    toBottom();
  };
  follow.addEventListener('click', () => setFollow(follow.getAttribute('aria-checked') !== 'true'));
  scroll.addEventListener('scroll', () => {
    const away = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight;
    if (away > NEAR_BOTTOM_PX) setFollow(false);
  });

  const render = (body) => {
    const keep = overlap(shown, body.lines);
    const stale = shown.length - keep;
    for (let i = 0; i < stale; i += 1) lines.firstChild.remove();
    lines.append(...body.lines.slice(keep).map((line) => el('div', { class: 'log-line', 'data-testid': 'log-line' }, line)));
    shown = body.lines;
    bytes = body.bytes;
    empty.hidden = shown.length > 0;
    count.textContent = `${shown.length} lines · ${Math.ceil(bytes / 1024)} KB`;
  };

  const load = () => {
    const id = ctx.state.runId;
    ctx.api.getLog(id).then((body) => {
      if (id !== ctx.state.runId) return; // the answer belongs to the previous selection
      if (body.bytes !== bytes) render(body);
      toBottom();
    }, () => {}); // keep the last lines; api.js health reporting shows the badge
  };
  const showing = () => ctx.state.view === 'logs' && ctx.state.runId;

  ctx.bus.on('view:changed', () => {
    if (showing()) load();
  });
  ctx.bus.on('run:selected', () => {
    lines.replaceChildren();
    shown = [];
    bytes = -1;
    empty.hidden = false;
    count.textContent = '0 lines';
    follow.setAttribute('aria-checked', 'true');
    if (showing()) load();
  });
  // The run poll drives the tail: it stops when the run ends, after one last answer.
  ctx.bus.on('run:updated', () => {
    if (showing()) load();
  });
}
