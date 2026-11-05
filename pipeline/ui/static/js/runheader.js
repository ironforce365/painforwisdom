// Run header line: status, input, id, time and the reason it needs you (spec 2026-11-05-console-end-to-end §4).
import { el } from './drawer.js';
import { fmtDuration, fmtWhen, STATUS_LABELS } from './format.js';

const base = (p) => (p ? p.split('/').pop() : '–');
/** Only a real change touches the DOM: the poll must not drop a text selection. */
const set = (node, text) => {
  if (node.textContent !== text) node.textContent = text;
};

export function mount(root, ctx) {
  const field = (name, cls = 'rh-field') => el('span', { class: cls, 'data-testid': `run-header-${name}` });
  const empty = el('span', { class: 'rh-empty' }, 'No run selected');
  const status = field('status', 'rh-status');
  const verdict = field('verdict', 'rh-verdict');
  const input = field('input', 'rh-input');
  const id = field('id', 'rh-id');
  const when = field('when');
  const total = field('total');
  const reason = field('reason', 'rh-reason');
  const parts = [status, verdict, input, id, when, total, reason];
  const profile = document.querySelector('[data-testid="header-profile"]');
  root.append(empty, ...parts);

  const showParts = (shown) => {
    empty.hidden = shown;
    parts.forEach((p) => { p.hidden = !shown; });
  };
  showParts(false);

  ctx.bus.on('run:selected', (runId) => {
    showParts(true);
    set(id, runId);
    [status, verdict, input, when, total, reason].forEach((p) => set(p, ''));
  });

  ctx.bus.on('run:updated', (run) => {
    status.dataset.status = run.status;
    set(status, STATUS_LABELS[run.status]);
    verdict.dataset.verdict = String(run.verdict);
    set(verdict, run.verdict ?? '');
    set(input, base(run.input.path));
    input.title = String(run.input.path);
    set(when, fmtWhen(run.started_at));
    set(total, fmtDuration(run.telemetry.total_s));
    const why = run.pending?.title ?? run.failure?.error ?? '';
    set(reason, why);
    reason.title = why;
    set(profile, run.profile ?? '');
  });
}
