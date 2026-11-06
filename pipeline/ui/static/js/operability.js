// Output workspace (spec 2026-11-05-console-end-to-end §4). Routing lives in views.js; this shows on view:changed.
// A run's view outlives its selection while it holds unsaved edits. Styles: css/operability.css. Parts: js/output/*.
import { el } from './drawer.js';
import { createView } from './output/view.js';

const KEYS = { j: 1, k: -1 };
const typing = (target) => target.closest('input, textarea, select, [contenteditable]');

export function mount(root, ctx) {
  root.classList.add('operability');
  const warnings = el('div', { class: 'out-warnings' });
  const slot = el('div', { class: 'out-slot' });
  const panel = el('section', { class: 'out-panel', 'data-testid': 'output', 'aria-label': 'run output' }, warnings, slot);
  const norun = el('p', { class: 'section-empty', 'data-testid': 'output-norun' }, 'Select a run to see its output.');
  const views = new Map();
  const shared = { section: 'summary' };
  let current = ctx.state.view;
  let runId = ctx.state.runId;

  const viewFor = (id) => {
    if (!views.has(id)) views.set(id, createView(ctx, id, shared));
    return views.get(id);
  };

  const renderWarnings = () => {
    warnings.replaceChildren(...[...views].filter(([id, view]) => id !== runId && view.dirtySections().length).map(([id, view]) => {
      const back = el('button', { type: 'button', class: 'btn', 'data-testid': `output-unsaved-back-${id}` }, 'Go back to it');
      back.addEventListener('click', () => ctx.bus.emit('run:selected', id));
      const discard = el('button', { type: 'button', class: 'btn', 'data-testid': `output-unsaved-discard-${id}` }, 'Discard the edits');
      discard.addEventListener('click', () => {
        views.delete(id);
        renderWarnings();
      });
      return el('div', { class: 'out-warning', role: 'alert', 'data-testid': `output-unsaved-${id}` },
        el('span', {}, `Run ${id} has unsaved edits in ${view.dirtySections().join(', ')}.`), back, discard);
    }));
  };

  const sync = () => {
    views.forEach((view) => view.hide());
    panel.hidden = current !== 'output';
    if (panel.hidden) return;
    if (!runId) {
      slot.replaceChildren(norun);
      return;
    }
    const view = viewFor(runId);
    slot.replaceChildren(view.root);
    view.show(ctx.state.run);
    renderWarnings();
  };

  ctx.bus.on('view:changed', ({ view }) => {
    current = view;
    sync();
  });
  ctx.bus.on('run:selected', (id) => {
    runId = id;
    views.forEach((view, key) => {
      if (key !== id && !view.dirtySections().length) views.delete(key);
    });
    sync();
  });
  ctx.bus.on('run:updated', (run) => viewFor(runId).update(run));
  ctx.bus.on('output:open', (section) => {
    shared.section = section;
    if (current === 'output') sync();
    else ctx.showView('output');
  });
  document.addEventListener('keydown', (e) => {
    const delta = KEYS[e.key];
    if (panel.hidden || !runId || !delta || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
    viewFor(runId).move(delta);
  });

  root.append(panel);
  sync();
}
