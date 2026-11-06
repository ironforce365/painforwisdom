// Dashboard tiles and latest themes (spec 2026-11-06-console-dashboards). Needs you, the run list and Start are their own modules.
import { el } from './drawer.js';
import { fmtCost, fmtDuration } from './format.js';

const LATEST = 3;

export const themeChip = (name) => el('span', { class: 'theme-chip', 'data-testid': 'theme-chip' }, name);

export function entryRow(entry, testid) {
  const row = el('a', { class: 'entry-row', href: `#/knowledge/${encodeURIComponent(entry.slug)}`, 'data-testid': testid },
    el('b', { class: 'entry-title' }, entry.title), el('span', { class: 'entry-date' }, entry.date ?? ''),
    el('span', { class: 'theme-chips' }, ...entry.themes.map(themeChip)));
  return row;
}

const tile = (name, label) => {
  const value = el('b', { class: 'tile-value', 'data-testid': `tile-${name}` }, '–');
  const note = el('span', { class: 'tile-note', 'data-testid': `tile-${name}-note` }, label);
  return { root: el('div', { class: 'stat-tile' }, value, note), value, note };
};

export function mount(root, ctx) {
  const runsTile = tile('runs', 'runs');
  const lastTile = tile('last', 'last good run');
  const costTile = tile('cost', 'API-equivalent, all runs');
  const kbTile = tile('kb', 'knowledge base');
  root.querySelector('#tiles').append(runsTile.root, lastTile.root, costTile.root, kbTile.root);

  const list = el('div', { class: 'entry-list', 'data-testid': 'latest-themes' });
  root.querySelector('#themes').append(list);

  ctx.bus.on('runs:listed', (runs) => {
    const real = runs.filter((r) => !r.dry_run);
    const passed = real.filter((r) => r.status === 'done' && r.verdict === 'PASS');
    const dry = runs.length - real.length;
    runsTile.value.textContent = String(real.length);
    runsTile.note.textContent = `runs · ${passed.length} passed${dry ? ` · ${dry} dry` : ''}`;
    lastTile.value.textContent = passed.length ? fmtDuration(passed[0].total_s) : '–';
    const cost = runs.reduce( // dry runs too: their AI calls are real
(sum, r) => sum + (r.cost_usd ?? 0), 0);
    costTile.value.textContent = fmtCost(cost, runs.some((r) => r.cost_estimated));
  });

  const load = async () => {
    let body;
    try {
      body = await ctx.api.getKnowledge(LATEST);
    } catch {
      return; // the health badge says so; the last answer stays
    }
    kbTile.value.textContent = String(body.entry_count);
    kbTile.note.textContent = `entries · ${body.theme_count} themes`;
    list.replaceChildren(...(body.entries.length
      ? body.entries.map((e) => entryRow(e, `latest-${e.slug}`))
      : [el('p', { class: 'section-empty', 'data-testid': 'latest-empty' }, 'No knowledge base entries yet.')]));
  };
  ctx.bus.on('page:changed', (page) => {
    if (page === 'home') load();
  });
  if (ctx.state.page === 'home') load();
}
