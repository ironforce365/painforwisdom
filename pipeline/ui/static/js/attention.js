// Needs-you bar: every run wanting a decision, one click to it (spec 2026-11-05-console-end-to-end §4, N1).
import { el } from './drawer.js';

// Decisions a live run is blocked on first, then what ended badly, then output waiting to go out.
const PRIORITY = ['approval', 'error', 'failed', 'crashed', 'stopped', 'publish'];
const rank = (run) => {
  const at = PRIORITY.indexOf(run.attention[0].kind);
  return at === -1 ? PRIORITY.length : at;
};

export function mount(root, ctx) {
  const count = el('b', { class: 'needs-count', 'data-testid': 'needs-you-count' }, '0');
  const items = el('div', { class: 'needs-items' });
  const bar = el('div', { class: 'needs', 'data-testid': 'needs-you', 'aria-label': 'runs that need you', 'data-count': '0' },
    el('span', { class: 'needs-label' }, 'Needs you ', count), items);
  root.append(bar);

  const press = () => {
    for (const item of items.querySelectorAll('button')) item.setAttribute('aria-pressed', String(item.dataset.runId === ctx.state.runId));
  };

  const itemEl = (run) => {
    const item = el('button', {
      type: 'button', class: 'needs-item', 'data-testid': `needs-you-item-${run.run_id}`, 'data-run-id': run.run_id,
      'data-kind': run.attention[0].kind, title: run.run_id,
    }, el('b', {}, run.input.name ?? run.run_id), ' ', el('span', {}, run.attention.map((a) => a.label).join(' · ')));
    item.addEventListener('click', () => {
      ctx.bus.emit('run:selected', run.run_id);
      const { target } = run.attention[0];
      if (target) ctx.bus.emit('output:open', target);
    });
    return item;
  };

  let sig = '';
  ctx.bus.on('runs:listed', (runs) => {
    const wanted = runs.filter((r) => r.attention.length).sort((a, b) => rank(a) - rank(b));
    const next = JSON.stringify(wanted.map((r) => [r.run_id, r.input.name, r.attention]));
    if (next === sig) return; // a rebuild would take the focus and the scroll away
    sig = next;
    count.textContent = String(wanted.length);
    bar.dataset.count = String(wanted.length);
    items.replaceChildren(...(wanted.length ? wanted.map(itemEl) : [el('span', { class: 'needs-none' }, 'Nothing right now')]));
    press();
  });
  ctx.bus.on('run:selected', press);
}
