// How numbers and states read, one definition for every module (spec 2026-11-05-console-end-to-end §4).
export const fmtCount = (n) => {
  if (n < 1e3) return String(n);
  if (n < 1e6) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}k`;
  return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
};

export const fmtDuration = (s) => {
  if (s == null) return '–';
  if (s < 60) return `${+s.toFixed(1)}s`;
  const t = Math.round(s);
  const pad = (n) => String(n).padStart(2, '0');
  if (t < 3600) return `${Math.floor(t / 60)}m ${pad(t % 60)}s`;
  return `${Math.floor(t / 3600)}h ${pad(Math.floor(t / 60) % 60)}m`;
};

/** `≈` marks an estimate; no known price (null) and nothing spent (0) are a dash, never `$0.00`. */
export const fmtCost = (cost, estimated) => {
  if (!cost) return '–';
  return `${estimated ? '≈' : ''}$${cost.toFixed(cost < 0.01 ? 3 : 2)}`;
};

export const costTip = (cost, estimated) => {
  if (cost === null) return 'No price is known for this model: tokens only.';
  if (estimated) return 'API-equivalent: what these tokens would cost at API prices. Under subscription billing nothing extra was charged.';
  return cost ? 'Billed cost.' : 'Nothing billed.';
};

export const fmtRatio = (r) => (r === null ? '–' : `${Math.round(r * 100)}%`);

/** Local date and time from epoch seconds. */
export const fmtWhen = (ts) => (ts == null ? '–' : new Date(ts * 1000).toLocaleString('en-GB', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}));

export const STATUS_LABELS = {
  running: 'Running',
  waiting_hitl: 'Waiting for approval',
  waiting_reply: 'Waiting for a reply',
  done: 'Done',
  failed: 'Failed',
  crashed: 'Crashed',
  stopped: 'Stopped',
};

/**
 * input already holds both cache counts (litellm prompt_tokens, pipeline/llm.py; runs.tokens_total).
 * Takes the counts under any names: `tokensOf(a, b, c, d)` = input, output, cache read, cache write.
 */
export function tokensOf(input, output, read, write) {
  return {
    input, output, read, write,
    total: input + output,
    fresh: Math.max(0, input - read - write),
    hit: input ? read / input : null,
  };
}
