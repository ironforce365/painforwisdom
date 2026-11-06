// Knowledge base page: every entry, the theme counts, and one entry read in place (spec 2026-11-06-console-dashboards).
import { el } from './drawer.js';
import { entryRow, themeChip } from './dashboard.js';
import { renderMarkdown } from './markdown.js';
import { knowledgeSlug } from './views.js';

export function mount(root, ctx) {
  const back = el('button', { type: 'button', class: 'btn', 'data-testid': 'knowledge-back' }, '← Dashboard');
  back.addEventListener('click', ctx.goHome);
  const themes = el('div', { class: 'theme-chips', 'data-testid': 'knowledge-themes' });
  const list = el('div', { class: 'entry-list', 'data-testid': 'knowledge-entries' });
  const reader = el('article', { class: 'knowledge-reader md', 'data-testid': 'knowledge-reader' });
  reader.hidden = true;
  root.append(
    el('div', { class: 'page-top' }, back, el('h1', { class: 'page-title' }, 'Knowledge base')),
    el('section', { class: 'dash-card' }, el('h2', {}, 'Themes'), themes),
    el('section', { class: 'dash-card' }, el('h2', {}, 'Entries'), list),
    reader,
  );

  const show = async () => {
    const slug = knowledgeSlug(location.hash);
    reader.hidden = !slug;
    try {
      const body = await ctx.api.getKnowledge();
      themes.replaceChildren(...body.themes.map((t) => themeChip(`${t.name} · ${t.entries}`)));
      list.replaceChildren(...(body.entries.length
        ? body.entries.map((e) => entryRow(e, `entry-${e.slug}`))
        : [el('p', { class: 'section-empty', 'data-testid': 'knowledge-empty' }, 'No entries yet.')]));
      if (slug) {
        const { markdown } = await ctx.api.getKnowledgeEntry(slug);
        reader.innerHTML = renderMarkdown(markdown); // safe: renderMarkdown() escapes before adding its own tags
        reader.scrollIntoView();
      }
    } catch (e) {
      reader.hidden = false;
      reader.textContent = e.status === 404 ? `There is no entry ${slug}.` : 'The knowledge base is unavailable.';
    }
  };
  ctx.bus.on('page:changed', (page) => {
    if (page === 'knowledge') show();
  });
  if (ctx.state.page === 'knowledge') show();
}
