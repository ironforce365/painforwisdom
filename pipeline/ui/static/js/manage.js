// Ask-in-place, and the delete every page offers (spec 2026-11-06-console-dashboards).
import { el } from './drawer.js';

const DELETE_QUESTION = 'Delete this run? Its folder and checkpoint go. The input stays, so it can run again. The knowledge base entry, Notion pages and anything published also stay.';

/** A button that asks before it acts, in place. `action` returns a promise; a refusal shows its reason. */
export function askButton({ testid, label, question, yes, action, danger = false }) {
  const trigger = el('button', { type: 'button', class: 'btn', 'data-testid': testid, ...(danger ? { 'data-style': 'danger' } : {}) }, label);
  const confirm = el('button', { type: 'button', class: 'btn', 'data-testid': `${testid}-confirm`, 'data-style': 'danger' }, yes);
  const cancel = el('button', { type: 'button', class: 'btn', 'data-testid': `${testid}-cancel` }, 'Cancel');
  const ask = el('span', { class: 'ask', 'data-testid': `${testid}-question` }, el('span', { class: 'ask-text' }, question), confirm, cancel);
  const error = el('span', { class: 'ask-error', 'data-testid': `${testid}-error`, role: 'alert' });
  const root = el('span', { class: 'ask-wrap' }, trigger, ask, error);

  const show = (asking) => {
    trigger.hidden = asking;
    ask.hidden = !asking;
  };
  const fail = (text) => {
    error.textContent = text;
    error.hidden = !text;
  };
  show(false);
  fail('');

  trigger.addEventListener('click', () => {
    fail('');
    show(true);
  });
  cancel.addEventListener('click', () => show(false));
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    try {
      await action();
      show(false);
    } catch (e) {
      show(false);
      fail(String(e.body?.detail ?? e.message));
    }
    confirm.disabled = false;
  });
  return { root, trigger };
}

export const deleteButton = (ctx, runId, testid) => askButton({
  testid,
  label: 'Delete',
  question: DELETE_QUESTION,
  yes: 'Yes, delete',
  danger: true,
  action: async () => {
    await ctx.api.deleteRun(runId);
    ctx.bus.emit('run:deleted', runId);
  },
});
