// Run page: back link, commands, totals and output cards around the graph (spec 2026-11-06-console-dashboards D7).
import { el, icon } from './drawer.js';
import { costTip, fmtCost, fmtCount, fmtDuration } from './format.js';
import { askButton, deleteButton } from './manage.js';
import { linkEl } from './output/ui.js';
import { ACTIVE } from './runs.js';

const PACKAGE_STATE = { published: 'Published', pending: 'Needs you', failed: 'Failed: needs you', publishing: 'Publishing' };
const NEEDS = new Set(['pending', 'failed']);

/** 2026-07-27-conditions-before-willpower -> Conditions before willpower */
function entryTitle(path) {
  const name = path.split('/').pop().replace(/\.md$/, '');
  const words = name.replace(/^\d{4}-\d{2}-\d{2}-?/, '').replaceAll('-', ' ');
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

function packageCard(section, label, glyph, pkg, prep) { // prep: null until the first answer
  if (!pkg) {
    return { section, label, glyph, state: prep?.mode === 'off' ? 'Off' : 'Nothing yet', title: prep?.reason ?? '', go: prep?.can ? 'Prepare →' : 'Details →', needs: false };
  }
  const url = pkg.status === 'published' ? pkg.result.url : null;
  return { section, label, glyph, state: PACKAGE_STATE[pkg.status], title: pkg.fields.title, url, urlText: url, needs: NEEDS.has(pkg.status), go: NEEDS.has(pkg.status) ? 'Review and publish →' : 'Details →' };
}

export function cardsFor(run, publish, result) {
  const entry = run.nodes.kb_curator.outputs.vault_entry_path;
  const blog = result?.blog;
  return [
    packageCard('youtube', 'YouTube', 'video', publish?.targets?.youtube, publish?.prepare?.youtube),
    packageCard('wordpress', 'WordPress', 'globe', publish?.targets?.wordpress, publish?.prepare?.wordpress),
    {
      section: 'blog', label: 'Blog draft', glyph: 'pen', state: blog ? 'In Notion' : 'Nothing yet', title: blog?.title ?? '', url: blog?.url, urlText: 'Open in Notion', needs: false, go: 'Read here →',
    },
    {
      section: 'kb', label: 'Knowledge base entry', glyph: 'book', state: entry ? 'Saved' : 'Nothing yet', title: entry ? entryTitle(entry) : '', needs: false, go: 'Read entry →',
    },
  ];
}

export function mount({ back: backRoot, tools, totals, cards }, ctx) {
  const backButton = el('button', { type: 'button', class: 'btn run-back', 'data-testid': 'run-back' }, '← Dashboard');
  backButton.addEventListener('click', ctx.goHome);
  backRoot.append(backButton);

  const tile = (name, note) => {
    const value = el('b', { class: 'tile-value', 'data-testid': `run-total-${name}` }, '–');
    const noteEl = el('span', { class: 'tile-note', 'data-testid': `run-total-${name}-note` }, note);
    const button = el('button', { type: 'button', class: 'stat-tile', 'data-testid': `run-tile-${name}`, title: 'Open the per-step numbers' }, value, noteEl);
    button.addEventListener('click', () => ctx.openMetrics());
    totals.append(button);
    return { value, note: noteEl };
  };
  const time = tile('time', 'total time');
  const tokens = tile('tokens', 'tokens');
  const cost = tile('cost', 'API-equivalent');
  const calls = tile('calls', 'model calls');

  let run = null;
  let publish = null;
  let result = null;
  let loadedSig = '';
  let toolsSig = '';
  let cardsSig = '';

  const renderTools = () => {
    const sig = JSON.stringify([run.run_id, run.status, run.manage]);
    if (sig === toolsSig) return;
    toolsSig = sig;
    const out = [];
    if (run.status === 'done') {
      const rerun = askButton({
        testid: 'run-rerun',
        label: 'Restart with same input',
        question: 'Start over from the input, as a new run?',
        yes: 'Yes, restart',
        action: async () => ctx.bus.emit('run:selected', (await ctx.api.restartRun(run.run_id)).run_id),
      });
      if (run.manage.rerun) {
        rerun.trigger.disabled = true;
        rerun.trigger.title = run.manage.rerun;
      }
      out.push(rerun.root);
    }
    const del = deleteButton(ctx, run.run_id, 'run-delete');
    if (run.manage.delete) {
      del.trigger.disabled = true;
      del.trigger.title = run.manage.delete;
    }
    out.push(del.root);
    tools.replaceChildren(...out);
  };

  const cardEl = (card) => {
    const open = el('button', { type: 'button', class: 'run-card-open', 'data-testid': `run-card-open-${card.section}` },
      el('span', { class: 'run-card-kind' }, icon(card.glyph), `${card.label} · `, el('span', { 'data-testid': `run-card-state-${card.section}` }, card.state)),
      el('b', { class: 'run-card-title', 'data-testid': `run-card-title-${card.section}` }, card.title),
      el('span', { class: 'run-card-go' }, card.go));
    open.addEventListener('click', () => ctx.bus.emit('output:open', card.section));
    const link = card.url ? [linkEl(card.url, { class: 'run-card-link', 'data-testid': `run-card-link-${card.section}` }, icon('link'), card.urlText)] : [];
    return el('section', { class: 'run-card', 'data-testid': `run-card-${card.section}`, 'data-needs': String(card.needs) }, open, ...link);
  };

  const renderCards = () => {
    const list = cardsFor(run, publish, result);
    const sig = JSON.stringify(list);
    if (sig === cardsSig) return;
    cardsSig = sig;
    cards.replaceChildren(...list.map(cardEl));
  };

  const renderTotals = () => {
    const t = run.telemetry;
    const total = t.tokens.input + t.tokens.output;
    time.value.textContent = fmtDuration(t.total_s);
    tokens.value.textContent = fmtCount(total);
    tokens.note.textContent = `tokens · ${fmtCount(t.tokens.output)} output`;
    cost.value.textContent = fmtCost(t.cost_usd, t.cost_estimated);
    cost.value.title = costTip(t.cost_usd, t.cost_estimated);
    calls.value.textContent = String(t.calls);
  };

  /** Asks only on a change: a poll a second must not ask Notion a second. */
  const load = () => {
    const id = run.run_id;
    const sig = JSON.stringify([id, run.status, run.packages, ctx.state.view]);
    if (sig === loadedSig) return;
    loadedSig = sig;
    const current = () => ctx.state.runId === id && run?.run_id === id;
    ctx.api.publishPackages(id).then((body) => {
      if (!current()) return;
      publish = body;
      renderCards();
    }, () => {});
    if (result?.status === 'ready' || ACTIVE.has(run.status)) return;
    ctx.api.getResult(id).then((body) => {
      if (!current()) return;
      result = body;
      renderCards();
    }, () => {});
  };

  const render = () => {
    renderTools();
    renderTotals();
    renderCards();
    load();
  };

  ctx.bus.on('run:selected', () => {
    run = null;
    publish = null;
    result = null;
    loadedSig = '';
    toolsSig = '';
    cardsSig = '';
    tools.replaceChildren();
    cards.replaceChildren();
  });
  ctx.bus.on('run:updated', (r) => {
    run = r;
    render();
  });
  ctx.bus.on('view:changed', () => {
    if (run) load(); // back from the Output tab, where a publish may have happened
  });
}
