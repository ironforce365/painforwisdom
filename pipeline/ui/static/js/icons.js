// Inline SVG icons, 24x24 stroke paths. Node keys match pipeline/ui/mapping.py NODE_ICON.
export const SVG_NS = 'http://www.w3.org/2000/svg';

export const ICONS = {
  // nodes
  mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z M5 11a7 7 0 0 0 14 0 M12 18v3 M8 21h8',
  spark: 'M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2.5 2.5 M15.5 15.5L18 18 M18 6l-2.5 2.5 M8.5 15.5L6 18',
  book: 'M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z M4 20a2 2 0 0 0 2 1h13v-3 M8 7h7',
  pen: 'M15 4l5 5L9 20H4v-5z M13 6l5 5',
  search: 'M11 4a7 7 0 1 1 0 14a7 7 0 0 1 0-14z M16 16l5 5',
  notion: 'M5 4h11l3 3v13H5z M9 16V9l6 7V9',
  image: 'M4 5h16v14H4z M4 16l5-5 4 4 2-2 5 5 M15 9h.01',
  video: 'M3 6h13v12H3z M16 10l5-3v10l-5-3',
  globe: 'M12 3a9 9 0 1 1 0 18a9 9 0 0 1 0-18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18',
  flag: 'M5 21V4 M5 4h11l-2 4 2 4H5',
  check: 'M12 3l7 3v6c0 4-3 7-7 9c-4-2-7-5-7-9V6z M9 12l2 2 4-4',
  dot: 'M12 9a3 3 0 1 1 0 6a3 3 0 0 1 0-6z',
  // state cues
  tick: 'M6 12l4 4 8-8',
  cross: 'M7 7l10 10 M17 7L7 17',
  pause: 'M9 6v12 M15 6v12',
  dash: 'M7 12h10',
  arc: 'M12 4a8 8 0 0 1 8 8',
  // chrome
  logo: 'M4 7a2 2 0 1 1 0 .01 M12 12a2 2 0 1 1 0 .01 M20 7a2 2 0 1 1 0 .01 M20 17a2 2 0 1 1 0 .01 M6 7l4 4 M14 11l4-3 M14 13l4 3',
  bolt: 'M13 3L5 14h6l-1 7 8-11h-6z',
  hand: 'M8 13V6a1.5 1.5 0 0 1 3 0v5 M11 11V4.5a1.5 1.5 0 0 1 3 0V11 M14 11V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7 7c-3 0-4-1-6-4l-2-3a1.5 1.5 0 0 1 2.5-1.5L8 13',
  chat: 'M4 5h16v11H9l-5 4z',
  inbox: 'M3 13l3-8h12l3 8v6H3z M3 13h5l1 3h6l1-3h5',
  offline: 'M2 8a15 15 0 0 1 20 0 M5 12a10 10 0 0 1 14 0 M8.5 15.5a5 5 0 0 1 7 0 M12 19h.01 M3 3l18 18',
};

/** <svg> with one stroked path; usable in HTML and nested in the DAG <svg>. */
export function svgIcon(name, attrs = {}) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'icon');
  for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, v);
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', ICONS[name]);
  svg.append(path);
  return svg;
}
