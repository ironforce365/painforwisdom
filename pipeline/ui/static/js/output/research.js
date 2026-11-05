// Research table from GET /research: filter, expand, open (spec 2026-11-05-console-end-to-end §4).
import { el, icon } from '../drawer.js';
import { openable, openEl, sync } from './ui.js';

const SEARCHED = ['title', 'type', 'author', 'location', 'category', 'angle', 'relevance', 'theme'];
const DETAILS = [['Location', 'location'], ['Angle', 'angle'], ['Relevance', 'relevance'], ['Author', 'author'], ['Category', 'category'],
  ['Theme', 'theme'], ['Paywall', 'paywall'], ['Reachable', 'reachable'], ['Reachable reason', 'reason']];
const HEADERS = ['#', 'Title', 'Type', 'Author', 'Category', 'Reachable', 'Open'];

function linkCell(item) {
  const { href, kind } = item.link;
  if (!openable(href)) return el('span', { class: 'muted', 'data-testid': `research-nolink-${item.n}` }, 'No link');
  return openEl(href, { 'data-testid': `research-link-${item.n}` }, icon('link'), kind === 'local' ? 'Local file' : 'Open');
}

export function researchSection() {
  const expanded = new Set();
  let items = [];
  const text = el('input', { type: 'search', 'data-testid': 'research-filter-text', 'aria-label': 'filter research', placeholder: 'Filter by text' });
  const type = el('select', { 'data-testid': 'research-filter-type', 'aria-label': 'filter by type' });
  const body = el('div', { class: 'research-body' });
  const root = el('div', { class: 'out-section research', 'data-testid': 'output-section-research' },
    el('div', { class: 'research-filters' }, text, type), body);

  const matches = (item) => (!type.value || item.type === type.value)
    && SEARCHED.some((k) => item[k].toLowerCase().includes(text.value.trim().toLowerCase()));

  const rowEls = (item) => {
    const open = expanded.has(item.n);
    const toggle = el('button', { type: 'button', class: 'research-toggle', 'data-testid': `research-toggle-${item.n}`, 'aria-expanded': String(open) }, item.title);
    toggle.addEventListener('click', () => {
      if (open) expanded.delete(item.n);
      else expanded.add(item.n);
      draw();
    });
    const row = el('tr', { 'data-testid': `research-row-${item.n}` }, el('td', {}, String(item.n)), el('td', {}, toggle),
      ...[item.type, item.author, item.category, item.reachable].map((v) => el('td', {}, v)), el('td', {}, linkCell(item)));
    if (!open) return [row];
    const facts = DETAILS.map(([label, key]) => el('div', {}, el('dt', {}, label), el('dd', {}, item[key] || '–')));
    const cell = el('td', { colspan: HEADERS.length }, el('dl', { class: 'research-facts' }, ...facts));
    return [row, el('tr', { class: 'research-detail', 'data-testid': `research-detail-${item.n}` }, cell)];
  };

  function draw() {
    const shown = items.filter(matches);
    sync(body, JSON.stringify([items, text.value, type.value, [...expanded]]), () => {
      if (!items.length) return [el('p', { class: 'section-empty', 'data-testid': 'research-empty' }, 'This run has no research report.')];
      if (!shown.length) return [el('p', { class: 'section-empty', 'data-testid': 'research-nomatch' }, 'No item matches the filter.')];
      return [el('table', {}, el('thead', {}, el('tr', {}, ...HEADERS.map((h) => el('th', {}, h)))), el('tbody', {}, ...shown.flatMap(rowEls)))];
    });
  }
  text.addEventListener('input', draw);
  type.addEventListener('change', draw);

  return {
    root,
    dirty: () => false,
    update(data) {
      items = data.research;
      const types = [...new Set(items.map((i) => i.type).filter(Boolean))].sort();
      sync(type, JSON.stringify(types), () => [el('option', { value: '' }, 'All types'), ...types.map((t) => el('option', { value: t }, t))]);
      draw();
    },
  };
}
