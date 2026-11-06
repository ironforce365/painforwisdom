// Action banner: what the selected run needs, and every answer to it (spec 2026-11-05-console-end-to-end §2, §4).
import { el } from './drawer.js';
import { renderMarkdown } from './markdown.js';
import { ACTIVE } from './runs.js';
import { STATUS_LABELS } from './format.js';
import { LABELS } from './output/model.js';

const VERBS = {
  retry: { label: 'Retry from checkpoint' },
  restart: { label: 'Restart from scratch', question: 'Start over from the input, as a new run?', yes: 'Yes, restart' },
  stop: { label: 'Stop run', question: 'Stop this run now?', yes: 'Yes, stop' },
};
const ORDER = ['retry', 'restart', 'stop'];

function kindOf(run) {
  if (run.pending) return 'pending';
  if (run.failure) return 'failure';
  if (ACTIVE.has(run.status)) return 'running';
  return run.actions.includes('dismiss') && !run.dismissed ? 'attention' : null;
}

function failureTitle(run) {
  if (ACTIVE.has(run.status)) return STATUS_LABELS[run.status];
  return run.failure.node ? `${STATUS_LABELS[run.status]} at ${run.failure.node}` : STATUS_LABELS[run.status];
}

const runningNodes = (run) => Object.entries(run.nodes).filter(([, n]) => n.state === 'running').map(([id]) => id);

export function mount(root, ctx) {
  const body = el('div', { class: 'banner-body' });
  const verbs = el('div', { class: 'banner-verbs' });
  const error = el('div', { class: 'banner-error', 'data-testid': 'action-error', role: 'alert' });
  error.hidden = true;
  const banner = el('section', { class: 'banner', 'data-testid': 'action-banner', 'aria-label': 'what this run needs' }, body, verbs, error);
  banner.hidden = true;
  root.append(banner);

  let run = null;
  let confirming = null; // the verb asking its question
  let answered = null; // the pending id answered from this page, until the run moves on
  let bodySig = '';
  let verbsSig = '';

  const showError = (text) => {
    error.textContent = text;
    error.hidden = !text;
  };
  const setBusy = (busy) => {
    banner.querySelectorAll('button:not([data-blocked])').forEach((b) => { b.disabled = busy; });
  };

  /** Runs one API call with the banner locked; a refusal shows its detail, a late answer for another run lands nowhere. */
  async function act(call, done) {
    const runId = run.run_id;
    setBusy(true);
    showError('');
    try {
      const result = await call();
      if (ctx.state.runId === runId) done(result);
    } catch (e) {
      if (ctx.state.runId === runId) {
        const { detail = e.message, run_id: other } = e.body ?? {};
        showError(other ? `${detail}: ${other}` : String(detail));
      }
    }
    setBusy(false);
    ctx.bus.emit('run:refresh');
  }

  const button = (testid, label, onClick, attrs = {}) => {
    const b = el('button', { type: 'button', 'data-testid': testid, ...attrs }, label);
    b.addEventListener('click', onClick);
    return b;
  };

  const reply = (text) => {
    const id = run.pending.id;
    return act(() => ctx.api.replyRun(run.run_id, id, text), () => {
      answered = id;
      render();
    });
  };

  const pendingEls = (p) => {
    const prompt = el('div', { class: 'banner-prompt md', 'data-testid': 'action-prompt' });
    prompt.innerHTML = renderMarkdown(p.prompt); // safe: renderMarkdown() escapes before adding its own tags
    const out = [el('div', { class: 'banner-title', 'data-testid': 'action-title' }, p.title)];
    if (p.non_recoverable) out.push(el('div', { class: 'banner-reason', 'data-testid': 'action-reason' }, p.non_recoverable));
    if (p.kind === 'error') {
      out.push(failureLine(p.node, p.error));
    }
    out.push(prompt);
    if (answered === p.id) {
      out.push(el('div', { class: 'banner-sent', 'data-testid': 'action-sent' }, 'Answer sent. Waiting for the run to pick it up.'));
      return out;
    }
    const options = p.options.map((o) => button(`action-option-${o.reply}`, o.label, () => reply(o.reply), { 'data-style': o.style }));
    const controls = el('div', { class: 'banner-options' }, ...options);
    if (p.free_text) {
      const text = el('textarea', { class: 'banner-text', 'data-testid': 'action-text', rows: '1', 'aria-label': 'your answer', placeholder: 'Or answer in your own words' });
      controls.append(text, button('action-send', 'Send answer', () => reply(text.value)));
    }
    out.push(controls);
    return out;
  };

  function failureLine(node, message) {
    const known = ctx.state.graph.nodes.some((n) => n.id === node);
    const nodeEl = known ? button('failure-node', node, () => ctx.bus.emit('node:selected', node), { class: 'banner-node', title: 'Inspect this node' }) : '';
    return el('div', { class: 'banner-failure' }, nodeEl, el('span', { class: 'banner-message', 'data-testid': 'failure-error' }, message));
  }

  const failureEls = (f) => {
    const out = [el('div', { class: 'banner-title', 'data-testid': 'action-title' }, failureTitle(run)), failureLine(f.node, f.error)];
    if (f.hint) out.push(el('div', { class: 'banner-hint', 'data-testid': 'action-hint' }, f.hint));
    return out;
  };

  const runningEls = () => {
    const names = runningNodes(run);
    const title = names.length ? `${STATUS_LABELS[run.status]}: ${names.join(', ')}` : STATUS_LABELS[run.status];
    return [el('div', { class: 'banner-title', 'data-testid': 'action-title' }, title)];
  };

  const attentionEls = () => {
    const names = run.waiting_packages.map((p) => (p.status === 'failed' ? `${LABELS[p.target]} (failed)` : LABELS[p.target]));
    const title = `Waiting for you to publish: ${names.join(', ')}.`;
    return [el('div', { class: 'banner-title', 'data-testid': 'action-title' }, title),
      ...run.waiting_packages.map(({ target }) => button(`action-open-${target}`, `Open ${LABELS[target]}`, () => ctx.bus.emit('output:open', target)))];
  };

  const BODY = { pending: () => pendingEls(run.pending), failure: () => failureEls(run.failure), running: runningEls, attention: attentionEls };

  const verbEls = (verb) => {
    const { label, question, yes } = VERBS[verb];
    if (confirming !== verb) {
      return [button(`action-${verb}`, label, () => {
        if (question) {
          confirming = verb;
          render();
        } else {
          act(() => ctx.api.retryRun(run.run_id), (r) => ctx.bus.emit('run:selected', r.run_id));
        }
      }, { 'data-verb': verb })];
    }
    const fire = verb === 'stop'
      ? () => act(() => ctx.api.stopRun(run.run_id), () => {})
      : () => act(() => ctx.api.restartRun(run.run_id), (r) => ctx.bus.emit('run:selected', r.run_id));
    return [
      el('span', { class: 'banner-question', 'data-testid': `action-${verb}-question` }, question),
      button(`action-${verb}-confirm`, yes, () => {
        confirming = null;
        fire();
        render();
      }, { 'data-style': 'danger' }),
      button(`action-${verb}-cancel`, 'Cancel', () => {
        confirming = null;
        render();
      }),
    ];
  };

  const blockerEls = (verb) => [
    el('button', { type: 'button', 'data-testid': `action-${verb}`, 'data-blocked': '', disabled: '' }, VERBS[verb].label),
    el('span', { class: 'banner-blocker', 'data-testid': `action-blocker-${verb}` }, run.action_blockers[verb]),
  ];

  const verbsFor = (kind) => {
    if (kind === 'pending' && run.pending.non_recoverable) return [];
    const offered = ORDER.filter((v) => run.actions.includes(v)).flatMap(verbEls);
    const blocked = kind === 'failure' ? ORDER.filter((v) => run.action_blockers[v]).flatMap(blockerEls) : [];
    const dismiss = run.actions.includes('dismiss') && !run.dismissed
      ? [button('action-dismiss', 'Dismiss from needs-you', () => act(() => ctx.api.dismissRun(run.run_id, true), () => {
        ctx.bus.emit('run:dismissed', { id: run.run_id, name: run.input.path?.split('/').pop() ?? run.run_id });
      }))]
      : [];
    return [...offered, ...blocked, ...dismiss];
  };

  function render() {
    const kind = kindOf(run);
    banner.hidden = kind === null;
    if (kind === null) return;
    banner.dataset.kind = run.pending ? `pending-${run.pending.kind}` : kind;
    // Rebuild only on change: the poll must not drop a half-typed answer or a text selection.
    const nextBody = JSON.stringify([kind, run.run_id, run.pending, run.failure, kind === 'running' && runningNodes(run), run.status, run.dismissed, run.waiting_packages, answered]);
    if (nextBody !== bodySig) {
      bodySig = nextBody;
      body.replaceChildren(...BODY[kind]());
    }
    const nextVerbs = JSON.stringify([kind, run.run_id, run.actions, run.action_blockers, run.pending?.non_recoverable, run.dismissed, confirming]);
    if (nextVerbs !== verbsSig) {
      verbsSig = nextVerbs;
      verbs.replaceChildren(...verbsFor(kind));
    }
  }

  ctx.bus.on('run:selected', () => {
    run = null;
    confirming = null;
    answered = null;
    bodySig = '';
    verbsSig = '';
    banner.hidden = true;
    showError('');
  });
  ctx.bus.on('run:updated', (r) => {
    run = r;
    render();
  });
}
