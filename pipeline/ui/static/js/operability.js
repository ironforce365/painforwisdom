// Operability view: a run's output cards and C9 package editors (spec 2026-11-03-console-operability §8). Styles: css/operability.css.
import { artifactEl, el, icon, RENDER } from './drawer.js';
import { isHttp } from './markdown.js';

const POLL_MS = 2000;
const CARDS = [['thumbnail', 'image'], ['blog', 'pen'], ['youtube', 'video'], ['wordpress', 'globe'], ['kb', 'book'],
  ['research', 'search'], ['audit', 'check']];
const PACKAGES = ['youtube', 'wordpress'];
const ARTIFACTS = {
  thumbnail: (a) => a.kind === 'image' && a.node === 'extract_image',
  blog: (a) => a.name === 'blog_post.md',
  research: (a) => a.name === 'research_report.csv',
  audit: (a) => a.name === 'audit_report.md',
};
const TYPE_ICONS = { Book: 'book', Article: 'pen', Paper: 'file', Podcast: 'mic', Video: 'video' };
const STATUS_ICONS = { pending: 'dot', publishing: 'arc', published: 'tick', failed: 'cross' };
const ASSET_ICONS = { featured_image: 'image', video: 'video' };

const base = (p) => p.split('/').pop();
// Outbound-link props, or none for a non-http(s) target.
const outLink = (url) => (isHttp(url) ? { href: url, target: '_blank', rel: 'noopener' } : {});
const linkEl = (url, props, ...children) => el(isHttp(url) ? 'a' : 'span', { ...outLink(url), ...props }, ...children);

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
  text: (id, spec, value) => plainInput('textarea', { rows: 3, 'data-testid': `field-${id}`, 'aria-label': spec.label }, value),
  markdown: (id, spec, value) => {
    const control = plainInput('textarea', { rows: 10, 'data-testid': `field-${id}`, 'aria-label': spec.label }, value);
    const preview = el('div', { class: 'preview', 'data-testid': `preview-${id}` }, RENDER.markdown(value));
    control.input.addEventListener('input', () => preview.replaceChildren(RENDER.markdown(control.input.value)));
    control.node = el('div', { class: 'split' }, control.input, preview);
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
    const entry = el('input', { type: 'text', 'data-testid': `field-${id}`, 'aria-label': spec.label });
    // change fires on Enter and on blur, so a typed tag lands before the publish click reads it.
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

function assetEl(target, asset) {
  return el('figure', { class: 'asset', 'data-testid': `asset-${target}-${asset.role}`, title: asset.path },
    icon(ASSET_ICONS[asset.role], { role: 'img', 'aria-label': asset.role }), el('figcaption', {}, base(asset.path)));
}

function packageEditor(pkg, ctx, runId, refresh) {
  const { target } = pkg;
  const fields = Object.fromEntries(Object.entries(pkg.field_specs).map(([name, spec]) => {
    const id = `${target}-${name}`;
    const control = CONTROLS[spec.type](id, spec, pkg.fields[name] ?? '');
    const error = el('small', { class: 'error', 'data-testid': `error-${id}`, role: 'alert' });
    const root = el('div', { class: 'field', 'data-field': name }, el('span', { class: 'label' }, spec.label), control.node, error);
    return [name, Object.assign(control, { root, error })];
  }));
  const general = el('div', { class: 'error', 'data-testid': `publish-error-${target}`, role: 'alert' }, pkg.error ?? '');
  const fieldset = el('fieldset', {}, ...Object.values(fields).map((f) => f.root));
  fieldset.disabled = pkg.status === 'published' || pkg.status === 'publishing';
  const readonly = el('dl', { class: 'readonly', 'data-testid': `readonly-${target}` },
    ...Object.entries(pkg.readonly).map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, String(v)))));
  const assets = el('div', { class: 'assets' }, ...pkg.assets.map((a) => assetEl(target, a)));
  const button = el('button', { type: 'button', class: 'publish', 'data-testid': `publish-${target}`, 'aria-label': `publish to ${target}`, title: `publish to ${target}` },
    icon('send'));
  button.hidden = pkg.status === 'published';
  button.disabled = pkg.status === 'publishing';
  const link = pkg.status === 'published'
    ? [linkEl(pkg.result.url, { class: 'published', 'data-testid': `published-${target}`, 'aria-label': `open on ${target}` }, icon('link'), pkg.result.url)]
    : [];

  const show = (errors) => {
    Object.values(fields).forEach((f) => f.error.replaceChildren());
    general.replaceChildren();
    errors.forEach((msg) => (fields[msg.split(':')[0]]?.error ?? general).append(el('div', {}, msg)));
  };
  button.addEventListener('click', () => {
    const values = Object.fromEntries(Object.entries(fields).map(([name, f]) => [name, f.read()]));
    const local = problems(pkg.field_specs, values);
    show(local);
    if (local.length) return;
    button.disabled = true;
    ctx.api.publish(runId, target, values).then(refresh, (e) => show([e.body?.detail ?? e.message].flat())).finally(() => {
      button.disabled = false; // a refresh that changed the package already replaced this button
    });
  });

  return {
    root: el('div', { class: 'editor', 'data-status': pkg.status },
      el('div', { class: 'editor-head' },
        el('span', { class: 'status', 'data-testid': `status-${target}`, role: 'img', 'aria-label': pkg.status, title: pkg.status }, icon(STATUS_ICONS[pkg.status])),
        ...link, button),
      general, assets, fieldset, readonly),
    prefill: (url) => {
      const f = fields.youtube_url;
      if (f && !fieldset.disabled && !f.input.value && !f.touched) f.input.value = url;
    },
  };
}

/** Rebuild a slot only when its data changed: a refresh must not drop an edit, a selection or a loaded artifact. */
function sync(slot, sig, build) {
  if (slot.dataset.sig === sig) return;
  slot.dataset.sig = sig;
  slot.replaceChildren(...build());
}

/** One open of the view. A late answer after close lands in this detached tree (spec §8.1). */
function buildView(ctx, runId) {
  const slots = {};
  const nothing = el('div', { class: 'nothing', 'data-testid': 'output-nothing' },
    icon('cloudOff', { role: 'img', 'aria-label': 'nothing recorded' }));
  const root = el('div', { class: 'out-cards' }, nothing, ...CARDS.map(([name, glyph]) => {
    slots[name] = { links: el('div', { class: 'out-slot links' }), body: el('div', { class: 'out-slot body' }) };
    return el('section', { class: 'out-card', 'data-testid': `output-${name}` },
      el('header', {}, icon(glyph, { role: 'img', 'aria-label': name }), slots[name].links), slots[name].body);
  }));

  const showArts = (slot, arts) => sync(slot, arts.map((a) => `${a.id}:${a.bytes}`).join(), () => arts.map((a) => artifactEl(a, ctx.api.artifactUrl(runId, a.id))));

  const renderArtifacts = ({ artifacts }) => {
    Object.entries(ARTIFACTS).forEach(([name, pick]) => showArts(slots[name].body, artifacts.filter(pick)));
    const { vault_entry_path: entry, curator_summary_path: summary } = { ...ctx.state.run?.nodes.kb_curator.outputs };
    const names = [entry, summary].filter(Boolean).map(base);
    const mine = artifacts.filter((a) => a.node === 'kb_curator' && names.includes(a.name));
    showArts(slots.kb.body, mine);
    const vault = mine.some((a) => entry && a.name === base(entry));
    sync(slots.kb.links, String(vault), () => (vault
      ? [el('a', { href: `obsidian://open?path=${encodeURIComponent(entry)}`, 'data-testid': 'output-vault-link', 'aria-label': 'open in Obsidian', title: entry }, icon('link'))]
      : []));
  };

  const renderResult = (res) => {
    sync(slots.blog.links, JSON.stringify(res.blog), () => (res.blog
      ? [linkEl(res.blog.url, { 'data-testid': 'output-blog-link', 'aria-label': 'open in Notion' }, icon('link'), res.blog.title)]
      : []));
    const research = res.research ?? [];
    sync(slots.research.links, JSON.stringify(research), () => research.map((r) => linkEl(r.url, { class: 'research', 'data-testid': 'research-card', 'data-type': r.type, title: r.type },
      icon(TYPE_ICONS[r.type] || 'globe', { 'data-testid': 'type-icon', role: 'img', 'aria-label': r.type }), r.title)));
  };

  let targets = {};
  let timer;
  const editors = {};
  const schedule = () => {
    clearTimeout(timer);
    const polling = root.isConnected && Object.values(targets).some((p) => p.status === 'publishing');
    if (polling) timer = setTimeout(refreshPublish, POLL_MS);
  };
  const renderPackages = (body) => {
    targets = body.targets ?? {};
    PACKAGES.filter((t) => targets[t]).forEach((t) => sync(slots[t].body, JSON.stringify(targets[t]), () => {
      editors[t] = packageEditor(targets[t], ctx, runId, refreshPublish);
      return [editors[t].root];
    }));
    if (targets.youtube?.status === 'published') Object.values(editors).forEach((e) => e.prefill(targets.youtube.result.url));
  };
  // A failed GET keeps polling; api.js health reporting shows the badge.
  function refreshPublish() {
    return ctx.api.publishPackages(runId).then(renderPackages, () => {}).then(schedule);
  }

  return {
    root,
    refresh: () => {
      ctx.api.listArtifacts(runId).then(renderArtifacts, () => {});
      ctx.api.getResult(runId).then(renderResult, () => {});
      refreshPublish();
    },
  };
}

export function mount(root, ctx) {
  root.classList.add('operability');
  const open = el('button', { type: 'button', class: 'out-open', 'data-testid': 'output-open', 'aria-label': 'run output', title: 'run output' }, icon('output'));
  open.disabled = true;
  const close = el('button', { type: 'button', class: 'out-close', 'data-testid': 'output-close', 'aria-label': 'close', title: 'close' }, icon('close'));
  const panel = el('section', { class: 'out-panel', 'data-testid': 'output', 'aria-label': 'run output' });
  panel.hidden = true;
  const idle = () => {};
  let refresh = idle;
  let deepLink = /\/output$/.test(location.hash);

  const hide = () => {
    panel.hidden = true;
    panel.replaceChildren();
    refresh = idle;
  };
  const show = () => {
    const runId = ctx.state.runId;
    const view = buildView(ctx, runId);
    panel.replaceChildren(el('div', { class: 'out-head' }, close), view.root);
    panel.hidden = false;
    history.replaceState(null, '', `#/runs/${encodeURIComponent(runId)}/output`);
    ({ refresh } = view);
    refresh();
    close.focus();
  };
  const leave = () => {
    hide();
    history.replaceState(null, '', `#/runs/${encodeURIComponent(ctx.state.runId)}`);
    open.focus();
  };

  open.addEventListener('click', () => (panel.hidden ? show() : leave()));
  close.addEventListener('click', leave);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.hidden) leave();
  });

  // app.js already rewrote the hash to the plain run route; only the boot deep link reopens.
  ctx.bus.on('run:selected', () => {
    open.disabled = false;
    hide();
    if (deepLink) show();
    deepLink = false;
  });
  ctx.bus.on('run:updated', () => refresh());

  root.append(open, panel);
}
