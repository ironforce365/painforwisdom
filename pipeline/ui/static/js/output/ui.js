// Small DOM helpers of the Output workspace.
import { el, icon } from '../drawer.js';
import { isConsoleRoute, isHttp } from '../markdown.js';

const outLink = (href) => ({ href, target: '_blank', rel: 'noopener' });

/** An http(s) link in a new tab; any other target is plain text. */
export const linkEl = (url, props, ...children) => (isHttp(url)
  ? el('a', { ...outLink(url), ...props }, ...children)
  : el('span', props, ...children));

/** http(s) or the console's own routes (a local book): nothing else ever becomes an href. */
export const openable = (url) => isHttp(url) || isConsoleRoute(url);
export const openEl = (url, props, ...children) => el('a', { ...outLink(url), ...props }, ...children);

export const button = (label, props, glyph) => el('button', { type: 'button', class: 'btn', ...props }, icon(glyph), label);

/** Rebuild a slot only when its data changed: a refresh must not drop a selection or a loaded element. */
export function sync(slot, sig, build) {
  if (slot.dataset.sig === sig) return;
  slot.dataset.sig = sig;
  slot.replaceChildren(...build());
}

/** Put these nodes in the slot; touching nothing when they are already there keeps focus and scroll through a poll. */
export function attach(slot, nodes) {
  const same = nodes.length === slot.children.length && nodes.every((n, i) => slot.children[i] === n);
  if (!same) slot.replaceChildren(...nodes);
}
