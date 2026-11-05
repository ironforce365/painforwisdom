// One side-by-side editor per text artifact, kept for the view's life: sections share it, so an edit survives moving between them.
import { artifactEl, el, RENDER } from '../drawer.js';
import { editable, reasonOf } from './model.js';
import { button } from './ui.js';

function artifactEditor(first, env) {
  const { id, name, kind } = first;
  const input = el('textarea', { class: 'ed-input', 'data-testid': `artifact-edit-${name}`, 'aria-label': `edit ${name}`, spellcheck: 'false' });
  const preview = el('div', { class: 'ed-preview', 'data-testid': `artifact-preview-${name}` });
  const save = button('Save', { 'data-testid': `artifact-save-${name}` }, 'tick');
  const notice = el('span', { class: 'notice', 'data-testid': `artifact-notice-${name}`, role: 'status' });
  const error = el('span', { class: 'error', 'data-testid': `artifact-error-${name}`, role: 'alert' });
  const root = el('section', { class: 'ed', 'data-testid': `artifact-${name}`, 'data-kind': kind, 'data-state': 'loading' },
    el('div', { class: 'ed-head' }, el('span', { class: 'ed-name' }, name), notice, error, save),
    el('div', { class: 'ed-panes' }, input, preview));
  let original = '';
  let bytes = null;
  let busy = false;

  const dirty = () => input.value !== original;
  const paint = () => {
    root.dataset.dirty = String(dirty());
    input.readOnly = env.locked();
    input.disabled = root.dataset.state !== 'ready';
    save.disabled = !dirty() || busy || env.locked() || input.disabled;
  };
  const draw = () => preview.replaceChildren(...(input.value.trim() ? [RENDER[kind](input.value)] : []));
  const load = () => fetch(env.api.artifactUrl(env.runId, id))
    .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((text) => {
      if (!dirty()) input.value = text; // typed while this was on its way: the edit wins
      original = text;
      root.dataset.state = 'ready';
      draw();
    }, (e) => {
      root.dataset.state = 'error';
      error.textContent = `Could not load this file: ${e.message}`;
    })
    .then(env.changed);

  input.addEventListener('input', () => {
    notice.textContent = '';
    draw();
    env.changed();
  });
  save.addEventListener('click', () => {
    const text = input.value;
    busy = true;
    notice.textContent = '';
    error.textContent = '';
    paint();
    env.api.saveArtifact(env.runId, id, text).then((res) => {
      original = text;
      bytes = res.bytes;
      notice.textContent = 'Saved';
    }, (e) => {
      error.textContent = reasonOf(e).join('; ');
    }).finally(() => {
      busy = false;
      env.changed();
    });
  });

  return {
    root,
    dirty,
    /** A new listing: follow the disk unless there is an edit to protect. */
    refresh: (a) => {
      if (a.bytes !== bytes && !dirty()) {
        bytes = a.bytes;
        load();
      }
      paint();
    },
  };
}

export function editorStore(env) {
  const editors = new Map();
  const shown = new Map();
  const editor = (a) => {
    if (!editors.has(a.id)) editors.set(a.id, artifactEditor(a, env));
    const e = editors.get(a.id);
    e.refresh(a);
    return e;
  };
  // Not editable: rendered once per version, so a poll does not refetch it.
  const readOnly = (a) => {
    const key = `${a.id}:${a.bytes}`;
    if (!shown.has(key)) shown.set(key, artifactEl(a, env.api.artifactUrl(env.runId, a.id)));
    return shown.get(key);
  };
  return {
    node: (a) => (editable(a) ? editor(a).root : readOnly(a)),
    isDirty: (a) => editors.has(a.id) && editors.get(a.id).dirty(),
  };
}
