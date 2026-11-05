// View tabs (spec 2026-11-05-console-end-to-end §4). The route `#/runs/<id>/<view>` and `body[data-view]` live here.
export const VIEWS = ['graph', 'output', 'logs'];
const LABELS = { graph: 'Graph', output: 'Output', logs: 'Logs' };

/** The view named by a location hash; `graph` when it names none. */
export function viewFromHash(hash) {
  const view = /^#\/runs\/[^/]+\/([^/]+)/.exec(hash)?.[1];
  return VIEWS.includes(view) ? view : 'graph';
}

/** The run named by a location hash, or null. */
export function runFromHash(hash) {
  const id = /^#\/runs\/([^/]+)/.exec(hash)?.[1];
  return id ? decodeURIComponent(id) : null;
}

/** `graph` keeps the plain run route, so existing links stay valid. */
export function routeFor(runId, view) {
  const run = `#/runs/${encodeURIComponent(runId)}`;
  return view === 'graph' ? run : `${run}/${view}`;
}

/** Mounted after the other modules: the first `view:changed` must reach all of them. */
export function mount(root, ctx) {
  const tabs = new Map(VIEWS.map((view) => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'view-tab';
    tab.dataset.testid = `view-tab-${view}`;
    tab.setAttribute('role', 'tab');
    tab.textContent = LABELS[view];
    tab.addEventListener('click', () => show(view));
    return [view, tab];
  }));
  root.setAttribute('role', 'tablist');
  root.setAttribute('aria-label', 'run views');
  root.append(...tabs.values());

  function show(view) {
    ctx.state.view = view;
    document.body.dataset.view = view;
    for (const [name, tab] of tabs) tab.setAttribute('aria-selected', String(name === view));
    if (ctx.state.runId) history.replaceState(null, '', routeFor(ctx.state.runId, view));
    ctx.bus.emit('view:changed', { view });
  }
  ctx.showView = show;
  show(ctx.state.view);
}
