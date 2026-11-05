// One run's Output workspace: left section nav, one section visible, data loads (spec 2026-11-05-console-end-to-end §4).
import { el, icon } from '../drawer.js';
import { editorStore } from './editors.js';
import { chips, isActive, SECTIONS } from './model.js';
import { publishSection } from './publish.js';
import { researchSection } from './research.js';
import { auditSection, blogSection, filesSection, kbSection, summarySection } from './sections.js';

const POLL_MS = 2000;
const ORDER = SECTIONS.map(([name]) => name);

export function createView(ctx, runId, shared) {
  const { api } = ctx;
  const data = { run: null, artifacts: [], targets: {}, prepare: null, research: [], result: null };
  let shown = false;
  let timer;

  const schedule = () => {
    clearTimeout(timer);
    const publishing = Object.values(data.targets).some((p) => p.status === 'publishing');
    if (shown && publishing) timer = setTimeout(refreshPublish, POLL_MS);
  };
  // A failed GET changes nothing; api.js health reporting shows the badge.
  const load = (promise, apply) => promise.then((body) => {
    apply(body);
    render();
  }, () => {});
  function refreshPublish() {
    return load(api.publishPackages(runId), (body) => {
      data.targets = body.targets;
      data.prepare = body.prepare;
    }).then(schedule);
  }
  const refresh = () => {
    load(api.listArtifacts(runId), (body) => { data.artifacts = body.artifacts; });
    load(api.getResearch(runId), (body) => { data.research = body.items; });
    // A ready result does not change; asking again would hit Notion on every poll.
    if (data.result?.status !== 'ready') load(api.getResult(runId), (body) => { data.result = body; });
    refreshPublish();
  };

  const env = {
    api,
    runId,
    locked: () => isActive(data.run),
    changed: () => render(),
    goTo: (name) => select(name),
    chip: (name) => chips(data)[name],
    reloadPublish: () => refreshPublish(),
    setPackage: (pkg) => {
      data.targets = { ...data.targets, [pkg.target]: pkg };
      return refreshPublish();
    },
  };
  env.editors = editorStore(env);
  const sections = {
    summary: summarySection(env),
    blog: blogSection(env),
    youtube: publishSection(env, 'youtube'),
    wordpress: publishSection(env, 'wordpress'),
    kb: kbSection(env),
    research: researchSection(),
    audit: auditSection(env),
    files: filesSection(env),
  };

  const items = Object.fromEntries(SECTIONS.map(([name, label, glyph]) => {
    const chip = el('span', { class: 'nav-chip', 'data-testid': `output-chip-${name}` }, '…');
    const mark = el('span', { class: 'nav-dirty', 'data-testid': `output-dirty-${name}`, title: 'unsaved edits' }, 'unsaved');
    mark.hidden = true;
    const button = el('button', { type: 'button', class: 'nav-item', 'data-testid': `output-nav-${name}` }, icon(glyph), el('span', { class: 'nav-label' }, label), mark, chip);
    button.addEventListener('click', () => select(name));
    return [name, { button, chip, mark }];
  }));
  const banner = el('div', { class: 'out-banner' });
  const body = el('div', { class: 'out-body' });
  const root = el('div', { class: 'out-view' },
    el('nav', { class: 'out-nav', 'aria-label': 'output sections' }, ...Object.values(items).map((i) => i.button)),
    el('div', { class: 'out-main' }, banner, body));

  function render() {
    if (!data.run) return;
    sections[shared.section].update(data); // first: an update can replace an editor, which the dirty marks read
    Object.entries(chips(data)).forEach(([name, text]) => {
      items[name].chip.textContent = text;
      items[name].chip.dataset.value = text;
      items[name].mark.hidden = !sections[name].dirty(data);
    });
    banner.replaceChildren(...(isActive(data.run)
      ? [el('p', { class: 'out-active', 'data-testid': 'output-active-reason' }, `The run is ${data.run.status}: showing what exists so far. Editing is disabled until it ends.`)]
      : []));
  }

  function select(name) {
    shared.section = name;
    Object.entries(items).forEach(([key, { button }]) => {
      if (key === name) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
    body.replaceChildren(sections[name].root);
    render();
  }

  const update = (run) => {
    data.run = run;
    if (shown) refresh();
    render();
  };

  return {
    root,
    update,
    show(run) {
      shown = true;
      select(shared.section);
      if (run) update(run);
    },
    hide() {
      shown = false;
      clearTimeout(timer);
    },
    move(delta) {
      select(ORDER[Math.min(Math.max(ORDER.indexOf(shared.section) + delta, 0), ORDER.length - 1)]);
    },
    dirtySections: () => ORDER.filter((name) => sections[name].dirty(data)),
  };
}
