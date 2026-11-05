// Publish panel for any target, rendered only from the C9 package and its field_specs (spec 2026-11-03 §1.2, 2026-11-05 §2 P).
import { el, icon, RENDER } from '../drawer.js';
import { base, LABELS, reasonOf } from './model.js';
import { button, linkEl, sync } from './ui.js';

function plainInput(tag, props, value) {
  const input = el(tag, props);
  input.value = value;
  const control = { node: input, input, read: () => input.value, touched: false };
  input.addEventListener('input', () => { control.touched = true; });
  return control;
}

const CONTROLS = {
  string: (id, spec, value) => plainInput('input', { type: 'text', 'data-testid': `field-${id}`, 'aria-label': spec.label }, value),
  url: (id, spec, value) => plainInput('input', { type: 'url', 'data-testid': `field-${id}`, 'aria-label': spec.label }, value),
  text: (id, spec, value) => plainInput('textarea', { rows: 4, 'data-testid': `field-${id}`, 'aria-label': spec.label }, value),
  markdown: (id, spec, value) => {
    const control = plainInput('textarea', { class: 'ed-input', 'data-testid': `field-${id}`, 'aria-label': spec.label }, value);
    const preview = el('div', { class: 'ed-preview', 'data-testid': `preview-${id}` }, RENDER.markdown(value));
    control.input.addEventListener('input', () => preview.replaceChildren(RENDER.markdown(control.input.value)));
    control.node = el('div', { class: 'ed-panes' }, control.input, preview);
    return control;
  },
  enum: (id, spec, value) => {
    const input = el('select', { 'data-testid': `field-${id}`, 'aria-label': spec.label },
      ...spec.options.map((o) => el('option', { value: o }, o)));
    input.value = value;
    return { node: input, read: () => input.value };
  },
  tags: (id, spec, value) => {
    let tags = [value].flat().filter(Boolean);
    const chips = el('div', { class: 'chips', 'data-testid': `chips-${id}` });
    const draw = () => chips.replaceChildren(...tags.map((tag) => {
      const remove = el('button', { type: 'button', 'data-testid': `chip-remove-${id}`, 'aria-label': `remove ${tag}` }, icon('close'));
      remove.addEventListener('click', () => {
        tags = tags.filter((t) => t !== tag);
        draw();
      });
      return el('span', { class: 'chip', 'data-testid': `chip-${id}` }, tag, remove);
    }));
    const entry = el('input', { type: 'text', 'data-testid': `field-${id}`, 'aria-label': spec.label, placeholder: 'Type and press Enter' });
    // change fires on Enter and on blur, so a typed tag lands before a click on publish reads it.
    entry.addEventListener('change', () => {
      tags = [...tags, ...entry.value.split(',').map((t) => t.trim()).filter(Boolean)];
      entry.value = '';
      draw();
    });
    draw();
    return { node: el('div', { class: 'tags' }, chips, entry), read: () => tags };
  },
};

/** The server's required and max checks (pipeline/publish/package.py), as `<field>: <problem>` like its 400. */
function problems(specs, values) {
  return Object.entries(specs).flatMap(([name, spec]) => {
    const text = [values[name]].flat().join('');
    if (spec.required && !text.trim()) return [`${name}: required`];
    return spec.max != null && text.length > spec.max ? [`${name}: over max ${spec.max}`] : [];
  });
}

function assetEl(env, target, asset) {
  const id = `${target}-${asset.role}`;
  const url = env.api.packageAssetUrl(env.runId, target, asset.role);
  const media = asset.role === 'video'
    ? el('video', { src: url, controls: '', preload: 'metadata', 'data-testid': id })
    : el('img', { src: url, alt: asset.role, 'data-testid': id });
  const figure = el('figure', { class: 'asset', 'data-testid': `asset-${id}`, title: asset.path }, media, el('figcaption', {}, base(asset.path)));
  media.addEventListener('error', () => figure.append(el('small', { class: 'error', 'data-testid': `asset-missing-${id}` }, 'This file cannot be shown here.')), { once: true });
  // HEVC from a phone: audio plays over a black box and no error event fires.
  media.addEventListener('loadedmetadata', () => {
    if (media.videoWidth === 0) figure.append(el('small', { class: 'notice', 'data-testid': `asset-undecodable-${id}` }, 'This browser cannot decode this video, so it plays audio only here. The upload sends the original file and is not affected.'));
  }, { once: true });
  return figure;
}

const formSig = (pkg) => JSON.stringify([pkg.fields, pkg.field_specs, pkg.assets, pkg.readonly]);

function buildForm(first, env) {
  const { target, field_specs: specs } = first;
  const fields = Object.fromEntries(Object.entries(specs).map(([name, spec]) => {
    const id = `${target}-${name}`;
    const control = CONTROLS[spec.type](id, spec, first.fields[name] ?? '');
    const error = el('small', { class: 'error', 'data-testid': `error-${id}`, role: 'alert' });
    const root = el('div', { class: 'field', 'data-field': name }, el('span', { class: 'label' }, spec.label), control.node, error);
    return [name, Object.assign(control, { root, error, spec })];
  }));
  const list = Object.values(fields);
  const read = () => Object.fromEntries(Object.entries(fields).map(([name, f]) => [name, f.read()]));
  let sig = formSig(first);
  let baseline = read();
  let busy = false;
  let messages = [];
  const dirty = () => JSON.stringify(read()) !== JSON.stringify(baseline);

  const status = el('span', { class: 'pub-status', 'data-testid': `status-${target}` });
  const mode = el('span', { class: 'pub-mode', 'data-testid': `mode-${target}` }, first.mode);
  const link = el('span', { class: 'pub-link' });
  const notice = el('span', { class: 'notice', 'data-testid': `publish-notice-${target}`, role: 'status' });
  const save = button('Save', { 'data-testid': `save-${target}` }, 'tick');
  const publish = button(`Publish to ${LABELS[target]}`, { class: 'btn primary', 'data-testid': `publish-${target}` }, 'send');
  const general = el('div', { class: 'error', 'data-testid': `publish-error-${target}`, role: 'alert' });
  const side = list.filter((f) => f.spec.type !== 'markdown').map((f) => f.root);
  const prose = list.filter((f) => f.spec.type === 'markdown').map((f) => f.root);
  const readonly = el('dl', { class: 'readonly', 'data-testid': `readonly-${target}` },
    ...Object.entries(first.readonly).map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, String(v ?? '–')))));
  const fieldset = el('fieldset', { class: 'pub-body' },
    el('div', { class: 'pub-side' }, ...side, readonly),
    el('div', { class: 'pub-main' }, ...first.assets.map((a) => assetEl(env, target, a)), ...prose));

  const show = (errors) => {
    list.forEach((f) => f.error.replaceChildren());
    messages = errors.filter((msg) => {
      const field = fields[msg.split(':')[0]];
      if (field) field.error.append(el('div', {}, msg));
      return !field;
    });
    env.changed();
  };
  const act = (request, done) => {
    busy = true;
    notice.textContent = '';
    show([]);
    request.then((pkg) => {
      baseline = read();
      sig = formSig(pkg);
      done();
      return env.setPackage(pkg);
    }, (e) => show(reasonOf(e))).finally(() => {
      busy = false;
      env.changed();
    });
  };
  publish.addEventListener('click', () => {
    const values = read();
    const local = problems(specs, values);
    show(local);
    if (!local.length) act(env.api.publish(env.runId, target, values), () => {});
  });
  save.addEventListener('click', () => {
    act(env.api.savePackage(env.runId, target, read()), () => { notice.textContent = 'Saved'; });
  });
  for (const event of ['input', 'change', 'click']) fieldset.addEventListener(event, () => env.changed());

  const prefill = (url) => {
    const f = fields.youtube_url;
    if (f && !fieldset.disabled && !f.input.value && !f.touched) {
      f.input.value = url;
      baseline = { ...baseline, youtube_url: url }; // the form's own suggestion is not an unsaved edit
    }
  };

  return {
    root: el('div', { class: 'pub-form' },
      el('div', { class: 'pub-head' }, el('h2', {}, LABELS[target]), status, mode, link, notice, el('span', { class: 'pub-actions' }, save, publish)),
      general, fieldset),
    dirty,
    /** Replace this form? Never over an edit, unless the package is published and the edit can no longer be saved. */
    stale: (pkg) => {
      const changed = sig !== formSig(pkg);
      return pkg.status === 'published' ? changed || dirty() : changed && !dirty();
    },
    paint: (pkg, data) => {
      const finished = pkg.status === 'published' || (pkg.mode === 'auto' && pkg.status !== 'failed');
      const blocked = env.locked() || pkg.status === 'publishing';
      fieldset.disabled = blocked || finished;
      status.textContent = pkg.status;
      status.dataset.value = pkg.status;
      mode.textContent = pkg.mode;
      const url = pkg.status === 'published' ? pkg.result.url : '';
      sync(link, url, () => (url ? [linkEl(url, { 'data-testid': `published-${target}` }, icon('link'), url)] : []));
      if (data.targets.youtube?.status === 'published') prefill(data.targets.youtube.result.url);
      if (dirty()) notice.textContent = '';
      save.hidden = finished;
      publish.hidden = finished;
      publish.disabled = busy || blocked;
      save.disabled = busy || blocked || !dirty();
      general.replaceChildren(...(messages.length ? messages.map((m) => el('div', {}, m)) : [pkg.error ?? '']));
    },
  };
}

function prepareEls(env, target, prep) {
  const title = el('h2', {}, LABELS[target]);
  const error = el('div', { class: 'error', 'data-testid': `publish-error-${target}`, role: 'alert' });
  if (!prep?.can) {
    return [title, el('p', { class: 'pub-reason', 'data-testid': `prepare-reason-${target}` }, prep?.reason ?? 'Package state is not available yet.'), error];
  }
  const prepare = button(`Prepare the ${LABELS[target]} package`, { class: 'btn primary', 'data-testid': `prepare-${target}` }, 'pen');
  prepare.addEventListener('click', () => {
    prepare.disabled = true;
    env.api.preparePackage(env.runId, target).then(env.reloadPublish, (e) => {
      error.textContent = reasonOf(e).join('; ');
      prepare.disabled = false;
    });
  });
  return [title, el('p', { class: 'pub-reason' }, 'This run has no package yet. Prepare builds one from the run\'s files; nothing is published.'), prepare, error];
}

export function publishSection(env, target) {
  const root = el('div', { class: 'out-section pub', 'data-testid': `output-section-${target}` });
  let form = null;
  return {
    root,
    dirty: () => form !== null && form.dirty(),
    update(data) {
      const pkg = data.targets[target];
      if (!pkg) {
        const prep = data.prepare?.[target];
        sync(root, String(JSON.stringify(prep)), () => prepareEls(env, target, prep));
        return;
      }
      if (form === null || form.stale(pkg)) {
        form = buildForm(pkg, env);
        root.replaceChildren(form.root);
      }
      form.paint(pkg, data);
    },
  };
}
