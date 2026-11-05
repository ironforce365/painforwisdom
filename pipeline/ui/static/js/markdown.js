// Escape-first markdown renderer (spec 2026-11-03-console-operability §3.6). No library: static/js is under the coverage gate.
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ESC[c]);

// Only http(s) targets become hrefs: Notion rows and LLM output can carry javascript: and other schemes.
export const isHttp = (url) => /^https?:\/\//i.test(url);
// The console's own run routes (a local book) are the only other target an output link may open.
export const isConsoleRoute = (url) => /^\/api\/runs\/[^/]/.test(url);

const INLINE = /`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|\*(?=\S)(.+?)(?<=\S)\*|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)/g;

/** Inline markdown on already-escaped text. */
function inline(s) {
  return s.replace(INLINE, (m, code, text, url, bold, em, under) => {
    if (code) return `<code>${code}</code>`;
    if (bold) return `<strong>${inline(bold)}</strong>`;
    if (url) return isHttp(url) ? `<a href="${url}" target="_blank" rel="noopener">${inline(text)}</a>` : inline(text);
    return `<em>${inline(em || under)}</em>`;
  });
}

const FENCE = /^\s*(`{3,}|~{3,})/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const HR = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s*&gt;\s?/;
const LI = /^\s*(?:([-*+])|\d+[.)])\s+(.*)$/;
const NESTED = /^( {2}|\t)/;
// A delimiter row with at least one pipe; a bare --- is an hr.
const TABLE_DELIM = /^(?=.*\|)\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;

const isTable = (lines, i) => lines[i].includes('|') && TABLE_DELIM.test(lines[i + 1]);
const isBlockStart = (lines, i) => [FENCE, HEADING, HR, QUOTE, LI].some((re) => re.test(lines[i])) || isTable(lines, i);

const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

function fenceBlock(lines, start) {
  const marker = FENCE.exec(lines[start])[1];
  let end = start + 1;
  while (end < lines.length && !lines[end].trim().startsWith(marker)) end += 1;
  return [`<pre><code>${lines.slice(start + 1, end).join('\n')}</code></pre>`, end + 1];
}

function quoteBlock(lines, start) {
  let end = start;
  while (end < lines.length && QUOTE.test(lines[end])) end += 1;
  const inner = lines.slice(start, end).map((l) => l.replace(QUOTE, ''));
  return [`<blockquote>${blocks(inner)}</blockquote>`, end];
}

function tableBlock(lines, start) {
  const head = cells(lines[start]);
  let end = start + 2;
  while (end < lines.length && lines[end].includes('|')) end += 1;
  const row = (values, tag) => `<tr>${head.map((_, k) => `<${tag}>${inline(values[k] ?? '')}</${tag}>`).join('')}</tr>`;
  const body = lines.slice(start + 2, end).map((l) => row(cells(l), 'td')).join('');
  return [`<div class="table-wrap"><table><thead>${row(head, 'th')}</thead><tbody>${body}</tbody></table></div>`, end];
}

const listTag = (m) => (m[1] ? 'ul' : 'ol');

/** One list; indented items nest one level under the item before them, deeper indents flatten into that level. */
function listBlock(lines, start) {
  const tag = listTag(LI.exec(lines[start]));
  const items = [];
  let end = start;
  for (; end < lines.length; end += 1) {
    const m = LI.exec(lines[end]);
    if (!m) break;
    if (NESTED.test(lines[end]) && items.length) {
      const subs = items.at(-1).subs;
      if (subs.at(-1)?.tag !== listTag(m)) subs.push({ tag: listTag(m), items: [] });
      subs.at(-1).items.push(`<li>${inline(m[2])}</li>`);
    } else if (listTag(m) === tag) {
      items.push({ text: inline(m[2]), subs: [] });
    } else {
      break;
    }
  }
  const html = items.map((it) => {
    const subs = it.subs.map((s) => `<${s.tag}>${s.items.join('')}</${s.tag}>`).join('');
    return `<li>${it.text}${subs}</li>`;
  }).join('');
  return [`<${tag}>${html}</${tag}>`, end];
}

function paragraph(lines, start) {
  let end = start + 1;
  while (end < lines.length && lines[end].trim() && !isBlockStart(lines, end)) end += 1;
  return [`<p>${inline(lines.slice(start, end).join('\n'))}</p>`, end];
}

/** Block markdown on already-escaped lines. */
function blocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const h = HEADING.exec(lines[i]);
    let block;
    if (!lines[i].trim()) block = ['', i + 1];
    else if (FENCE.test(lines[i])) block = fenceBlock(lines, i);
    else if (h) block = [`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`, i + 1];
    else if (HR.test(lines[i])) block = ['<hr>', i + 1];
    else if (QUOTE.test(lines[i])) block = quoteBlock(lines, i);
    else if (isTable(lines, i)) block = tableBlock(lines, i);
    else if (LI.test(lines[i])) block = listBlock(lines, i);
    else block = paragraph(lines, i);
    out.push(block[0]);
    i = block[1];
  }
  return out.join('');
}

/** Markdown to HTML, escaping first: nothing from `src` reaches the page as a tag. */
export function renderMarkdown(src) {
  return blocks(escapeHtml(src).split(/\r?\n/));
}
