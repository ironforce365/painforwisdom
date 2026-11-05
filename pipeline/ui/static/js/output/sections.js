// Summary, and the sections made of artifact editors (blog, KB, audit, files).
import { el, icon } from '../drawer.js';
import { base, editable, failingChecks, findings, LABELS, nextSteps, nodeCost, PICKS, runCost, SECTIONS, TARGETS, thumbnail } from './model.js';
import { attach, button, linkEl, sync } from './ui.js';

const empty = (name, text) => el('p', { class: 'section-empty', 'data-testid': `section-empty-${name}` }, text);

function checkEl(f, testid) {
  const level = f.ok ? 'ok' : (f.severity ?? 'unknown');
  return el('li', { class: 'check', 'data-testid': testid, 'data-level': level, title: f.name },
    icon(f.ok ? 'tick' : 'warn'), el('span', { class: 'check-node' }, f.node), el('span', {}, f.name), el('span', { class: 'muted' }, f.detail ?? ''));
}

const card = (title, ...children) => el('section', { class: 'sum-card' }, el('h3', {}, title), ...children);

function goButton(env, name, label, props) {
  const go = button(label, props, 'output');
  go.addEventListener('click', () => env.goTo(name));
  return go;
}

function publishRow(env, target, data) {
  const pkg = data.targets[target];
  const published = pkg?.status === 'published'
    ? [linkEl(pkg.result.url, { 'data-testid': `summary-published-${target}` }, icon('link'), pkg.result.url)]
    : [];
  return el('div', { class: 'sum-row', 'data-testid': `summary-publish-${target}` },
    goButton(env, target, LABELS[target], { 'data-testid': `summary-go-${target}` }), el('span', { class: 'chip-state' }, env.chip(target)), ...published);
}

function summaryEls(env, data) {
  const { run, result } = data;
  const failing = failingChecks(run);
  const thumb = thumbnail(data);
  const steps = nextSteps(data);
  const tele = run.telemetry;
  const verdict = el('div', { class: 'sum-verdict', 'data-testid': 'summary-verdict', 'data-value': run.verdict ?? run.status },
    el('strong', {}, run.verdict ?? 'No verdict'), el('span', { class: 'muted' }, run.status));
  const input = el('div', { class: 'muted', 'data-testid': 'summary-input' }, base(run.input.path || 'unknown input'));
  const stepEls = steps.map((s) => el('li', { class: 'sum-row', 'data-testid': 'summary-next-item' }, el('span', {}, s.text), goButton(env, s.section, `Go to ${LABELS[s.section]}`, {})));
  const others = SECTIONS.map(([name]) => name).filter((name) => !TARGETS.includes(name) && name !== 'summary');
  const links = others.map((name) => goButton(env, name, LABELS[name], { 'data-testid': `summary-go-${name}` }));
  const notion = result?.blog
    ? [linkEl(result.blog.url, { 'data-testid': 'summary-link-notion' }, icon('link'), 'Notion page')]
    : [];
  return [
    el('div', { class: 'sum-col' },
      card('Verdict', verdict, input),
      ...(thumb ? [card('Thumbnail', el('img', { class: 'sum-thumb', src: env.api.artifactUrl(env.runId, thumb.id), alt: 'thumbnail', 'data-testid': 'summary-thumbnail' }))] : [])),
    el('div', { class: 'sum-col' },
      card('What needs doing next', steps.length
        ? el('ul', { class: 'sum-list' }, ...stepEls)
        : el('p', { 'data-testid': 'summary-next-none' }, 'Nothing: everything here is done.')),
      card('Failing checks', failing.length
        ? el('ul', { class: 'sum-list' }, ...failing.map((f) => checkEl(f, 'summary-check')))
        : el('p', { 'data-testid': 'summary-checks-ok' }, 'No failing checks.'))),
    el('div', { class: 'sum-col' },
      card('Publish', ...TARGETS.map((t) => publishRow(env, t, data))),
      card('Everything else', el('div', { class: 'sum-links' }, ...links, ...notion))),
    el('div', { class: 'sum-col' },
      card('Cost', el('div', { class: 'sum-cost', 'data-testid': 'summary-cost', title: 'API-equivalent when marked with ≈' }, runCost(tele)),
        el('table', { class: 'sum-table' }, el('tbody', {}, ...Object.entries(tele.per_node).filter(([, p]) => p.cost_usd !== 0).map(([node, p]) => el('tr', { 'data-testid': `summary-cost-row-${node}` },
          el('td', {}, node), el('td', {}, nodeCost(p)))))))),
  ];
}

export function summarySection(env) {
  const root = el('div', { class: 'out-section summary', 'data-testid': 'output-section-summary' });
  return {
    root,
    dirty: () => false,
    update(data) {
      const { run, result, targets, prepare, artifacts } = data;
      const sig = JSON.stringify([run.status, run.verdict, run.input, run.telemetry, findings(run), thumbnail(data)?.id, targets, prepare, result?.blog, artifacts.length]);
      sync(root, sig, () => summaryEls(env, data));
    },
  };
}

/** A section of editors for the artifacts `PICKS[name]` selects, with secondary links and extras above them. */
function editorSection(env, name, emptyText, linksFor, extraFor) {
  const links = el('div', { class: 'sec-links' });
  const extra = el('div', { class: 'sec-extra' });
  const panes = el('div', { class: 'sec-panes' });
  const none = empty(name, emptyText);
  const root = el('div', { class: `out-section ${name}`, 'data-testid': `output-section-${name}` }, links, extra, panes);
  return {
    root,
    dirty: (data) => data.artifacts.filter(PICKS[name]).some(env.editors.isDirty),
    update(data) {
      const arts = data.artifacts.filter(PICKS[name]);
      sync(links, JSON.stringify([data.result?.blog, data.run.nodes.kb_curator.outputs, arts.length]), () => linksFor(data, arts));
      sync(extra, JSON.stringify(findings(data.run)), () => extraFor(data));
      attach(panes, arts.length ? arts.map(env.editors.node) : [none]);
    },
  };
}

const none = () => [];

export const blogSection = (env) => editorSection(env, 'blog', 'This run has no blog post.', ({ result }) => (result?.blog
  ? [linkEl(result.blog.url, { 'data-testid': 'output-blog-link' }, icon('link'), `Notion: ${result.blog.title}`)]
  : []), none);

export const kbSection = (env) => editorSection(env, 'kb', 'This run has no knowledge base entry.', ({ run }, arts) => {
  const entry = run.nodes.kb_curator.outputs.vault_entry_path;
  // obsidian:// is the one other scheme: built here from the path the run recorded, never taken from content.
  return entry && arts.length
    ? [el('a', { href: `obsidian://open?path=${encodeURIComponent(entry)}`, 'data-testid': 'output-vault-link', title: entry }, icon('link'), 'Open in Obsidian')]
    : [];
}, none);

export const auditSection = (env) => editorSection(env, 'audit', 'This run has no audit report.', none, ({ run }) => {
  const checks = findings(run);
  return checks.length ? [el('ul', { class: 'sum-list audit-checks' }, ...checks.map((f) => checkEl(f, 'audit-check')))] : [];
});

/** Master-detail: a long list of editors would be the page-long scroll this workspace replaces. */
export function filesSection(env) {
  const list = el('nav', { class: 'files-list', 'aria-label': 'files' });
  const detail = el('div', { class: 'files-detail' });
  const nothing = empty('files', 'This run has no files.');
  const root = el('div', { class: 'out-section files', 'data-testid': 'output-section-files' }, list, detail);
  let selected = null;

  const section = {
    root,
    dirty: (data) => data.artifacts.some(env.editors.isDirty),
    update(data) {
      const { artifacts } = data;
      const current = artifacts.find((a) => a.id === selected) ?? artifacts[0];
      const groups = Map.groupBy(artifacts, (a) => a.node ?? 'other');
      const rowEl = (a) => {
        const row = el('button', { type: 'button', class: 'files-row', 'data-testid': `file-${a.name}`, 'aria-current': String(a === current), 'data-dirty': String(env.editors.isDirty(a)), 'data-editable': String(editable(a)) }, a.name);
        row.addEventListener('click', () => {
          selected = a.id;
          section.update(data);
        });
        return row;
      };
      // The node first: its first listing decides whether the row is dirty, so it must exist before the signature.
      attach(detail, current ? [env.editors.node(current)] : [nothing]);
      sync(list, JSON.stringify([artifacts, current?.id, artifacts.map(env.editors.isDirty)]), () => [...groups].map(([node, arts]) => el('div', { class: 'files-group', 'data-testid': `file-group-${node}` },
        el('h3', {}, node), ...arts.map(rowEl))));
    },
  };
  return section;
}
