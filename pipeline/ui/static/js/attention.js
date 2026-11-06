// Needs you: every run wanting a decision, on the dashboard, with a link back from any page (spec 2026-11-06-console-dashboards).
import { el } from './drawer.js';

// Decisions a live run is blocked on first, then what ended badly, then output waiting to go out.
const PRIORITY = ['approval', 'error', 'failed', 'crashed', 'stopped', 'publish'];
const UNDO_MS = 8000;
const rank = (run) => {
  const at = PRIORITY.indexOf(run.attention[0].kind);
  return at === -1 ? PRIORITY.length : at;
};
const nameOf = (run) => run.input.name ?? run.run_id;
const labels = (items) => items.map((a) => a.label).join(' · ');

export function mount(root, linkRoot, ctx) {
  const count = el('b', { class: 'needs-count', 'data-testid': 'needs-you-count' }, '0');
  const items = el('div', { class: 'needs-items' });
  const dismissedCount = el('span', { 'data-testid': 'needs-dismissed-count' }, '0');
  const dismissedToggle = el('button', { type: 'button', class: 'needs-more', 'data-testid': 'needs-dismissed-toggle', 'aria-expanded': 'false' }, 'Dismissed (', dismissedCount, ')');
  const dismissedList = el('div', { class: 'needs-dismissed', 'data-testid': 'needs-dismissed' });
  dismissedList.hidden = true;
  const failure = el('p', { class: 'needs-error', 'data-testid': 'needs-error', role: 'alert' });
  failure.hidden = true;
  const bar = el('section', { class: 'needs', 'data-testid': 'needs-you', 'aria-label': 'runs that need you', 'data-count': '0' },
    el('h2', { class: 'needs-label' }, 'Needs you (', count, ')'), failure, items, dismissedToggle, dismissedList);
  root.append(bar);

  const linkCount = el('span', { 'data-testid': 'needs-link-count' }, '0');
  const link = el('button', { type: 'button', class: 'needs-link', 'data-testid': 'needs-link', title: 'Back to the dashboard' }, 'Needs you (', linkCount, ')');
  link.addEventListener('click', ctx.goHome);
  linkRoot.append(link);

  const undoName = el('span', {});
  const undo = el('button', { type: 'button', class: 'btn', 'data-testid': 'needs-undo' }, 'Undo');
  const notice = el('div', { class: 'needs-notice', 'data-testid': 'needs-notice', role: 'status' }, 'Dismissed: ', undoName, undo);
  notice.hidden = true;
  document.body.append(notice);

  let undoRun = null;
  let timer;
  let total = 0;

  const syncLink = () => { link.hidden = total === 0 || ctx.state.page === 'home'; };
  const showError = (text) => {
    failure.textContent = text;
    failure.hidden = !text;
  };
  const setDismissed = async (runId, dismissed) => {
    showError('');
    try {
      await ctx.api.dismissRun(runId, dismissed);
    } catch (e) {
      showError(`${runId}: ${e.body?.detail ?? e.message}`);
    }
    ctx.bus.emit('run:refresh');
  };

  undo.addEventListener('click', () => {
    clearTimeout(timer);
    notice.hidden = true;
    setDismissed(undoRun, false);
  });
  ctx.bus.on('run:dismissed', ({ id, name }) => {
    undoRun = id;
    undoName.textContent = name;
    notice.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { notice.hidden = true; }, UNDO_MS);
  });

  dismissedToggle.addEventListener('click', () => {
    dismissedList.hidden = !dismissedList.hidden;
    dismissedToggle.setAttribute('aria-expanded', String(!dismissedList.hidden));
  });

  const open = (run) => {
    ctx.bus.emit('run:selected', run.run_id);
    const { target } = run.attention[0];
    if (target) ctx.bus.emit('output:open', target);
  };

  const itemEl = (run) => {
    const go = el('button', {
      type: 'button', class: 'needs-item', 'data-testid': `needs-you-item-${run.run_id}`, 'data-run-id': run.run_id,
      'data-kind': run.attention[0].kind, title: run.run_id,
    }, el('b', { class: 'needs-what' }, labels(run.attention)), el('span', { class: 'needs-from' }, nameOf(run)), el('span', { class: 'needs-go' }, 'Open →'));
    go.addEventListener('click', () => open(run));
    const dismiss = el('button', { type: 'button', class: 'btn', 'data-testid': `needs-dismiss-${run.run_id}` }, 'Dismiss');
    dismiss.addEventListener('click', async () => {
      await setDismissed(run.run_id, true);
      ctx.bus.emit('run:dismissed', { id: run.run_id, name: nameOf(run) });
    });
    return el('div', { class: 'needs-row', 'data-kind': run.attention[0].kind }, go, dismiss);
  };

  const dismissedEl = (run) => {
    const restore = el('button', { type: 'button', class: 'btn', 'data-testid': `needs-restore-${run.run_id}` }, 'Restore');
    restore.addEventListener('click', () => setDismissed(run.run_id, false));
    return el('div', { class: 'needs-row', 'data-testid': `needs-dismissed-${run.run_id}` },
      el('span', { class: 'needs-from' }, `${labels(run.dismissed_attention)} · ${nameOf(run)}`), restore);
  };

  let sig = '';
  ctx.bus.on('runs:listed', (runs) => {
    const wanted = runs.filter((r) => r.attention.length).sort((a, b) => rank(a) - rank(b));
    const dismissed = runs.filter((r) => r.dismissed_attention.length);
    const next = JSON.stringify([wanted.map((r) => [r.run_id, r.input.name, r.attention]), dismissed.map((r) => [r.run_id, r.dismissed_attention])]);
    if (next === sig) return; // a rebuild would take the focus and the scroll away
    sig = next;
    total = wanted.length;
    count.textContent = String(total);
    linkCount.textContent = String(total);
    bar.dataset.count = String(total);
    syncLink();
    items.replaceChildren(...(total ? wanted.map(itemEl) : [el('p', { class: 'needs-none', 'data-testid': 'needs-none' }, 'Nothing needs you right now.')]));
    dismissedCount.textContent = String(dismissed.length);
    dismissedToggle.hidden = dismissed.length === 0;
    dismissedList.replaceChildren(...dismissed.map(dismissedEl));
  });
  ctx.bus.on('page:changed', syncLink);
  dismissedToggle.hidden = true;
  syncLink();
}
