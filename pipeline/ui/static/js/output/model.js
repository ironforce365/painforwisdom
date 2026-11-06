// What the Output workspace derives from a run's data; no DOM (spec 2026-11-05-console-end-to-end §4).
export const SECTIONS = [
  ['summary', 'Summary', 'output'], ['blog', 'Blog', 'pen'], ['youtube', 'YouTube', 'video'], ['wordpress', 'WordPress', 'globe'],
  ['kb', 'Knowledge base', 'book'], ['research', 'Research', 'search'], ['audit', 'Audit', 'check'], ['files', 'Files', 'file'],
];
export const TARGETS = ['youtube', 'wordpress'];
export const LABELS = Object.fromEntries(SECTIONS.map(([name, label]) => [name, label]));

const ACTIVE = new Set(['running', 'waiting_hitl', 'waiting_reply']);
const TEXT_KINDS = new Set(['markdown', 'csv', 'json', 'text']);
const PACKAGE_FILES = new Set(['package.json', 'publish.lock']); // the server refuses these: the publish routes own them
const TEXT_CAP = 2 * 1024 * 1024; // the server serves this much of a text file: saving more would truncate it

export const isActive = (run) => ACTIVE.has(run.status);
export const base = (p) => p.split('/').pop();
export const editable = (a) => TEXT_KINDS.has(a.kind) && !PACKAGE_FILES.has(a.name) && a.bytes <= TEXT_CAP;
export const reasonOf = (e) => [e.body?.detail ?? e.message].flat();

/** Which artifacts each artifact section shows. */
export const PICKS = {
  blog: (a) => a.name === 'blog_post.md',
  kb: (a) => a.node === 'kb_curator',
  audit: (a) => a.name === 'audit_report.md',
};
export const thumbnail = (data) => data.artifacts.find((a) => a.kind === 'image' && a.node === 'extract_image');

/** end.result items are JSON cut at 500 chars (C1), so an item may not parse: keep it raw. */
function parseFinding(item) {
  try {
    return { name: item, ...JSON.parse(item) };
  } catch {
    return { name: item };
  }
}

export const findings = (run) => Object.entries(run.nodes).flatMap(([node, n]) => Object.entries(n.result)
  .filter(([key]) => key.includes('_findings'))
  .flatMap(([, items]) => items.map((item) => ({ node, ...parseFinding(item) }))));

export const failingChecks = (run) => findings(run).filter((f) => !f.ok);

const publishChip = (pkg, prep) => {
  if (pkg) return pkg.status;
  if (!prep) return '…';
  return prep.mode === 'off' ? 'off' : 'no package';
};

export function chips(data) {
  const { run, artifacts, targets, prepare, research } = data;
  const failing = failingChecks(run).length;
  const count = (name) => artifacts.filter(PICKS[name]).length;
  const auditChip = failing ? `${failing} failing` : 'ready';
  return {
    summary: run.verdict ?? run.status,
    blog: count('blog') ? 'ready' : 'none',
    youtube: publishChip(targets.youtube, prepare?.youtube),
    wordpress: publishChip(targets.wordpress, prepare?.wordpress),
    kb: String(count('kb')),
    research: String(research.length),
    audit: count('audit') ? auditChip : 'none',
    files: String(artifacts.length),
  };
}

function publishStep(target, pkg, prep) {
  const name = LABELS[target];
  if (!pkg) {
    if (prep?.can) return [{ section: target, text: `Prepare the ${name} package.` }];
    return prep?.reason && prep.mode !== 'off' ? [{ section: target, text: `${name}: ${prep.reason}.` }] : [];
  }
  if (pkg.status === 'failed') return [{ section: target, text: `${name} failed: ${pkg.error}. Fix it and publish again.` }];
  if (pkg.status === 'publishing') return [{ section: target, text: `${name} is still publishing.` }];
  return pkg.status === 'pending' && pkg.mode !== 'auto' ? [{ section: target, text: `Review and publish to ${name}.` }] : [];
}

/** What the user can do next, most urgent first. */
export function nextSteps(data) {
  const { run, targets, prepare } = data;
  if (isActive(run)) return [{ section: 'files', text: `The run is still ${run.status}: its output is not final.` }];
  const failing = failingChecks(run).length;
  const checks = failing ? [{ section: 'audit', text: `Review ${failing} failing check(s).` }] : [];
  return [...checks, ...TARGETS.flatMap((t) => publishStep(t, targets[t], prepare?.[t]))];
}

const money = (cost, estimated) => {
  if (cost == null) return null;
  return `${estimated ? '≈' : ''}$${cost.toFixed(cost < 0.01 ? 4 : 2)}`;
};

// Input already holds both cache counts (pipeline/llm.py).
export const runCost = ({ cost_usd: cost, cost_estimated: estimated, tokens }) => money(cost, estimated)
  ?? `tokens only (${tokens.input + tokens.output})`;

export const nodeCost = (p) => money(p.cost_usd, p.cost_estimated)
  ?? `tokens only (${p.input_tokens + p.output_tokens})`;
