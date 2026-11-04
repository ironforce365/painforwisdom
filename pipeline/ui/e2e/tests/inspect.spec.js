// Step inspector and final result (spec 2026-10-31-pipeline-console-ui §8).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const drawer = (page) => page.getByTestId('drawer');
const art = (page, name) => drawer(page).getByTestId(`artifact-${name}`);

/** Drop a file into a node's agent dir of the server's tmp copy of a fixture run. */
function plant(run, dir, name, content) {
  const d = path.join(PROCESSED, run, 'langgraph', dir);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, name), content);
}

async function openNode(page, run, node) {
  await page.goto(`/#/runs/${run}`);
  await expect(page.getByTestId(`node-${node}`)).not.toHaveAttribute('data-state', 'pending');
  await page.getByTestId(`node-${node}`).click();
  await expect(drawer(page)).toHaveAttribute('data-node', node);
}

/** The open drawer overlays the right of the DAG: close it, then pick another node. */
async function pick(page, node) {
  await drawer(page).getByTestId('drawer-close').click();
  await page.getByTestId(`node-${node}`).click();
  await expect(drawer(page)).toHaveAttribute('data-node', node);
}

async function launch(request, stepMs) {
  const res = await request.post('/api/runs', {
    data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: stepMs } },
  });
  expect(res.status()).toBe(201);
  return (await res.json()).run_id;
}

test.beforeEach(() => freshServerState());

test('every artifact kind renders in the drawer', async ({ page }) => {
  plant('fx-done', 'painforwisdom-writer', 'notes.md',
    '# Top\n\n### Sub\n\nPlain **bold** and [site](https://example.org/x) and [bad](javascript:alert(1))\n'
    + '- one\n- two\n1. first\n2) second\n* star\n\n');
  plant('fx-done', 'painforwisdom-writer', 'table.csv', 'a,"b, c"\r\n"say ""hi""",2\n\n');
  plant('fx-done', 'painforwisdom-writer', 'broken.json', '{not json');
  await openNode(page, 'fx-done', 'writer');
  await expect(drawer(page)).toHaveAttribute('data-state', 'done');
  await expect(drawer(page).getByTestId('drawer-duration')).toHaveText('17.5s');

  const md = art(page, 'notes.md');
  await expect(md).toHaveAttribute('data-state', 'ready');
  await expect(md.getByRole('heading', { level: 1 })).toHaveText('Top');
  await expect(md.getByRole('heading', { level: 3 })).toHaveText('Sub');
  await expect(md.locator('strong')).toHaveText('bold');
  const link = md.getByRole('link', { name: 'site' });
  await expect(link).toHaveAttribute('href', 'https://example.org/x');
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener');
  await expect(md.getByRole('link')).toHaveCount(1);
  await expect(md.getByRole('list')).toHaveCount(3);
  await expect(md.getByRole('listitem')).toHaveCount(5);

  const csv = art(page, 'table.csv');
  await expect(csv).toHaveAttribute('data-state', 'ready');
  await expect(csv.getByRole('columnheader')).toHaveText(['a', 'b, c']);
  await expect(csv.getByRole('cell')).toHaveText(['say "hi"', '2']);
  await expect(art(page, 'broken.json').getByTestId('artifact-body')).toHaveText('{not json');
  await expect(art(page, 'blog_post.md')).toHaveAttribute('data-kind', 'markdown');

  await pick(page, 'youtube_metadata');
  await expect(art(page, 'metadata.json')).toHaveAttribute('data-state', 'ready');
  const json = await art(page, 'metadata.json').getByTestId('artifact-body').textContent();
  expect(json).toContain('\n  ');

  await pick(page, 'extract_image');
  const img = art(page, 'featured.png').getByTestId('artifact-body');
  await expect(img).toHaveJSProperty('complete', true);
  expect(await img.evaluate((el) => el.naturalWidth)).toBeGreaterThan(0);

  await pick(page, 'wordpress_draft');
  await expect(art(page, 'frames.bin').getByTestId('artifact-body')).toHaveAttribute('download', 'frames.bin');
  await expect(art(page, 'featured.png')).toHaveCount(0);

  await pick(page, 'summarizer');
  await expect(art(page, 'telegram.txt').getByTestId('artifact-body')).toHaveText(/\S/);
  await expect(art(page, 'audit_report.md')).toHaveAttribute('data-state', 'ready');

  await pick(page, 'transcribe');
  await expect(drawer(page)).toHaveAttribute('data-state', 'skipped');
  await expect(drawer(page).getByTestId('drawer-duration')).toHaveText('–');
  await expect(drawer(page).getByTestId('artifact-list')).toBeEmpty();
  await expect(drawer(page).getByTestId('drawer-nothing')).toBeVisible();
  await pick(page, 'writer');
  await expect(drawer(page).getByTestId('drawer-nothing')).toBeHidden();
});

const AMBER = 'rgb(232, 207, 79)'; // --skipped

const withResult = (page, node, result) => page.route('**/api/runs/fx-done', async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.nodes[node].result = result;
  await route.fulfill({ response: res, json: body });
});

test('facts and findings show for every node with data; skipped is amber with its reason', async ({ page }) => {
  await openNode(page, 'fx-done', 'youtube_upload');
  const panel = drawer(page);
  await expect(panel).toHaveAttribute('data-state', 'skipped');
  await expect(panel.getByTestId('fact-reason').locator('dd')).toHaveText('off');
  await expect(panel.getByTestId('fact-youtube_skip_reason').locator('dd')).toHaveText('off');
  await expect(panel.getByTestId('fact-stage')).toHaveCount(0);
  await expect(panel.locator('.state-cue')).toHaveCSS('color', AMBER);
  await expect(page.getByTestId('node-wordpress_draft').locator('.disc')).toHaveCSS('stroke', AMBER); // unselected

  await pick(page, 'bv_research');
  await expect(panel.getByTestId('finding')).toHaveText(['research rows logged']);
  await expect(panel.getByTestId('finding')).toHaveAttribute('data-level', 'ok');
  await expect(panel.getByTestId('fact-branch_verdict_research').locator('dd')).toHaveText('PASS');
  await expect(panel.getByTestId('drawer-nothing')).toBeHidden();

  await pick(page, 'notion_research');
  await expect(panel.getByTestId('fact-page_ids').locator('dd')).toHaveText('["fx-res-1","fx-res-2"]');
});

test('every finding level maps to its icon, an unparseable item shows raw', async ({ page }) => {
  await withResult(page, 'pre_validator', { pre_findings: [
    '{"name": "good", "ok": true, "severity": "core", "detail": "why"}',
    '{"name": "bad core", "ok": false, "severity": "core"}',
    '{"name": "bad secondary", "ok": false, "severity": "secondary"}',
    '{"name": "verdict fail", "verdict": "FAIL"}',
    '{"name": "odd", "severity": "other"}',
    '{"name": "bare"}',
    '{"name": "cut off',
  ] });
  await openNode(page, 'fx-done', 'pre_validator');
  const items = drawer(page).getByTestId('finding');
  await expect(items).toHaveCount(7);
  const levels = await items.evaluateAll((els) => els.map((e) => [e.dataset.level, e.querySelector('svg').dataset.icon]));
  expect(levels).toEqual([['ok', 'tick'], ['core', 'cross'], ['secondary', 'warn'], ['FAIL', 'cross'], ['other', 'dot'],
    ['unknown', 'dot'], ['unknown', 'dot']]);
  await expect(items.nth(0)).toHaveAttribute('title', 'why');
  await expect(items.nth(6)).toHaveText('{"name": "cut off');
});

test('drawer default width, drag and keyboard resize persist across reloads', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 800 });
  await openNode(page, 'fx-done', 'writer');
  const width = async () => (await drawer(page).boundingBox()).width;
  expect(await width()).toBeCloseTo(0.38 * 1600, 0);
  const handle = drawer(page).getByTestId('drawer-resize');
  await handle.focus();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  expect(await width()).toBeCloseTo(0.38 * 1600 - 40, 0);
  await page.reload();
  await page.getByTestId('node-writer').click();
  expect(await width()).toBeCloseTo(0.38 * 1600 - 40, 0);
  // After the reload: coverage keeps only the last load of a script.
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + 4, 400);
  await page.mouse.down();
  await page.mouse.move(900, 400);
  await page.mouse.up();
  expect(await width()).toBe(700);
  await handle.focus();
  await page.keyboard.press('x');
  expect(await width()).toBe(700);
  expect(await page.evaluate(() => localStorage.getItem('pfw.drawer.width'))).toBe('700');
});

test('blocked localStorage leaves the default width and resizing still works', async ({ page }) => {
  await page.addInitScript(() => {
    const blocked = () => { throw new Error('blocked'); };
    Storage.prototype.getItem = blocked;
    Storage.prototype.setItem = blocked;
  });
  await page.setViewportSize({ width: 1600, height: 800 });
  await openNode(page, 'fx-done', 'writer');
  const handle = drawer(page).getByTestId('drawer-resize');
  await handle.focus();
  await page.keyboard.press('ArrowLeft');
  expect((await drawer(page).boundingBox()).width).toBeCloseTo(0.38 * 1600 + 40, 0);
});

test('long text wraps without horizontal scroll; only tables and code scroll in their box', async ({ page }) => {
  const long = 'x'.repeat(2000);
  plant('fx-done', 'painforwisdom-writer', 'wide.md',
    `${long}\n\n\`\`\`\n${long}\n\`\`\`\n\n| ${long} | b |\n|---|---|\n| 1 | 2 |\n`);
  await openNode(page, 'fx-done', 'writer');
  await expect(art(page, 'wide.md')).toHaveAttribute('data-state', 'ready');
  const body = drawer(page).getByTestId('drawer-body');
  expect(await body.evaluate((n) => n.scrollWidth <= n.clientWidth)).toBe(true);
  const pre = art(page, 'wide.md').locator('pre');
  expect(await pre.evaluate((n) => n.scrollWidth > n.clientWidth)).toBe(true);
});

const render = (page, src) => page.evaluate(async (s) => (await import('/static/js/markdown.js')).renderMarkdown(s), src);

test('renderMarkdown block and inline forms', async ({ page }) => {
  await page.goto('/');
  const cases = [
    ['', ''],
    ['# H1\n###### H6', '<h1>H1</h1><h6>H6</h6>'],
    ['one\ntwo\n\nthree', '<p>one\ntwo</p><p>three</p>'],
    ['para\n# head', '<p>para</p><h1>head</h1>'],
    ['**b** *i* _u_ `c*d*` snake_case_name 2 * 3 * 4', '<p><strong>b</strong> <em>i</em> <em>u</em> <code>c*d*</code> snake_case_name 2 * 3 * 4</p>'],
    ['**[l](https://e.org/a_b_c)**', '<p><strong><a href="https://e.org/a_b_c" target="_blank" rel="noopener">l</a></strong></p>'],
    ['[bad](javascript:alert(1)) [ok](http://e.org)', '<p>bad) <a href="http://e.org" target="_blank" rel="noopener">ok</a></p>'],
    ['```py\na < b\n\n# no\n```\nafter', '<pre><code>a &lt; b\n\n# no</code></pre><p>after</p>'],
    ['~~~\nopen', '<pre><code>open</code></pre>'],
    ['---\n* * *\n___', '<hr><hr><hr>'],
    ['> q\n> # h\n\nout', '<blockquote><p>q</p><h1>h</h1></blockquote><p>out</p>'],
    ['- a\n  - b\n  - c\n    1. d\n- e\n1. f', '<ul><li>a<ul><li>b</li><li>c</li></ul><ol><li>d</li></ol></li><li>e</li></ul><ol><li>f</li></ol>'],
    ['  - lead\n\t- tab', '<ul><li>lead<ul><li>tab</li></ul></li></ul>'],
    ['| a | **b** |\n|---|:-:|\n| 1 | 2 |\n| 3 |\nend', '<div class="table-wrap"><table><thead><tr><th>a</th><th><strong>b</strong></th></tr></thead><tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td><td></td></tr></tbody></table></div><p>end</p>'],
    ['a|b\nplain', '<p>a|b\nplain</p>'],
    ['a|b\n---', '<p>a|b</p><hr>'],
    ['<b>x</b> & "y"', '<p>&lt;b&gt;x&lt;/b&gt; &amp; &quot;y&quot;</p>'],
  ];
  for (const [src, html] of cases) expect(await render(page, src), src).toBe(html);
});

test('an XSS payload in an artifact is shown as text', async ({ page }) => {
  const payload = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script> [x](https://e.org/" onclick="window.__xss=3)';
  plant('fx-done', 'painforwisdom-writer', 'xss.md', `# ${payload}\n\n${payload}\n`);
  plant('fx-done', 'painforwisdom-writer', 'xss.txt', payload);
  await openNode(page, 'fx-done', 'writer');
  for (const name of ['xss.md', 'xss.txt']) {
    await expect(art(page, name)).toHaveAttribute('data-state', 'ready');
    await expect(art(page, name).getByTestId('artifact-body')).toContainText('<img src=x');
    await expect(art(page, name).locator('img, script, [onclick]')).toHaveCount(0);
  }
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test('drawer closes on its button, on Escape and on another run; keeps other keys', async ({ page }) => {
  await openNode(page, 'fx-done', 'extract');
  await drawer(page).getByTestId('drawer-close').click();
  await expect(drawer(page)).toBeHidden();
  await page.getByTestId('node-extract').click();
  await drawer(page).getByTestId('drawer-close').press('a');
  await expect(drawer(page)).toBeVisible();
  await drawer(page).getByTestId('drawer-close').press('Escape');
  await expect(drawer(page)).toBeHidden();
  await page.getByTestId('node-extract').click();
  await page.getByTestId('run-fx-failed').click();
  await expect(drawer(page)).toBeHidden();
});

test('artifact and listing failures show a cue and the listing retries', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  let listFailures = 1;
  await page.route('**/api/runs/fx-done/artifacts', (route) => (listFailures-- > 0
    ? route.fulfill({ status: 500, body: 'down' }) : route.continue()));
  await page.route('**/api/runs/fx-done/artifacts/*', (route) => route.fulfill({ status: 500, body: 'down' }));
  await openNode(page, 'fx-done', 'extract');
  await expect(art(page, 'extraction_report.md')).toHaveAttribute('data-state', 'error');
});

test('drawer follows a live fake run', async ({ page, request }) => {
  test.setTimeout(60_000);
  const id = await launch(request, 1500); // a step outlasts the 1 s run poll, so `running` is observable
  await page.goto(`/#/runs/${id}`);
  await page.getByTestId('node-writer').click();
  await expect(drawer(page)).toHaveAttribute('data-node', 'writer');
  await expect(drawer(page)).toHaveAttribute('data-state', 'running', { timeout: 30_000 });
  await expect(drawer(page)).toHaveAttribute('data-state', 'done', { timeout: 30_000 });
  await expect(art(page, 'blog_post.md')).toHaveAttribute('data-state', 'ready');
  await expect(art(page, 'blog_post.md').getByRole('heading', { level: 1 })).toHaveText('Fake post');
});

test.describe('final result', () => {
  const result = (page) => page.getByTestId('result');

  test('ready: blog rendered with title and research cards linking out', async ({ page }) => {
    await page.goto('/#/runs/fx-done');
    await page.getByTestId('result-open').click();
    await expect(result(page)).toHaveAttribute('data-status', 'ready');
    await expect(result(page).getByTestId('result-title')).toHaveText('the trail that used to beat me');
    await expect(result(page).getByRole('heading', { level: 2 })).toHaveText(['The climb', 'What changed']);
    const blog = result(page).getByTestId('result-link');
    await expect(blog).toHaveAttribute('href', 'https://www.notion.so/fx-blog-1');
    await expect(blog).toHaveAttribute('rel', 'noopener');
    const cards = result(page).getByTestId('research-card');
    await expect(cards).toHaveCount(2);
    await expect(cards.first()).toHaveAttribute('data-type', 'Book');
    await expect(cards.first()).toHaveAttribute('href', 'https://example.org/endure');
    await expect(cards.first()).toHaveAttribute('target', '_blank');
    await expect(cards.first()).toHaveAttribute('rel', 'noopener');
    await expect(cards.nth(1)).toHaveAttribute('data-type', 'Article');
  });

  test('modal traps focus and closes on Escape or its button', async ({ page }) => {
    await page.goto('/#/runs/fx-done');
    const open = page.getByTestId('result-open');
    await open.click();
    await expect(result(page)).toHaveAttribute('data-status', 'ready');
    const close = result(page).getByTestId('result-close');
    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(result(page).getByTestId('research-card').last()).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(result(page).getByTestId('result-link')).toBeFocused();
    await page.keyboard.press('x');
    await expect(result(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(result(page)).toBeHidden();
    await expect(open).toBeFocused();
    await open.click();
    await close.click();
    await expect(result(page)).toBeHidden();
  });

  test('legacy run: blog shown, no research cards', async ({ page }) => {
    await page.goto('/#/runs/fx-legacy');
    await page.getByTestId('result-open').click();
    await expect(result(page)).toHaveAttribute('data-status', 'ready');
    await expect(result(page).getByTestId('result-title')).toHaveText('the trail that used to beat me');
    await expect(result(page).getByTestId('research-card')).toHaveCount(0);
  });

  test('pending on a live fake run, then ready once Notion rows land', async ({ page, request }) => {
    const id = await launch(request, 400);
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('result-open').click();
    await expect(result(page)).toHaveAttribute('data-status', 'pending');
    await expect(result(page).getByTestId('result-pending')).toBeVisible();
    await expect(result(page)).toHaveAttribute('data-status', 'ready', { timeout: 30_000 });
    await expect(result(page).getByTestId('research-card')).toHaveCount(2);
  });

  test('unavailable shows the reason; an intercepted failure is unavailable too', async ({ page, errors }) => {
    await page.goto('/#/runs/fx-crashed');
    await page.getByTestId('result-open').click();
    await expect(result(page)).toHaveAttribute('data-status', 'unavailable');
    await expect(result(page).getByTestId('result-reason')).toHaveText('no blog page recorded for this run');
    await page.keyboard.press('Escape');

    errors.allow(/status of 503/);
    await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ status: 503, body: 'nope' }));
    await page.getByTestId('run-fx-done').click();
    await page.getByTestId('result-open').click();
    await expect(result(page)).toHaveAttribute('data-status', 'unavailable');
    await expect(result(page).getByTestId('result-reason')).toHaveText('HTTP 503');
  });

  test('unknown research type falls back to a generic icon', async ({ page }) => {
    await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ json: {
      status: 'ready', reason: null, blog: { title: 't', url: 'https://e.org/b', markdown: 'x' },
      research: [{ title: 'r', type: 'Zine', url: 'https://e.org/r' }],
    } }));
    await page.goto('/#/runs/fx-done');
    await page.getByTestId('result-open').click();
    await expect(result(page).getByTestId('research-card')).toHaveAttribute('data-type', 'Zine');
    await expect(result(page).getByTestId('research-card').getByTestId('type-icon')).toHaveAttribute('data-icon', 'globe');
  });

  test('non-http(s) blog and research urls render without an href', async ({ page }) => {
    await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ json: {
      status: 'ready', reason: null, blog: { title: 't', url: 'javascript:alert(1)', markdown: 'x' },
      research: [{ title: 'r', type: 'Book', url: 'JavaScript:alert(2)' }],
    } }));
    await page.goto('/#/runs/fx-done');
    await page.getByTestId('result-open').click();
    await expect(result(page).getByTestId('research-card')).toHaveText('r');
    await expect(result(page).getByTestId('result-link')).toBeVisible();
    await expect(result(page).locator('[href]')).toHaveCount(0);
    expect(await result(page).getByTestId('research-card').evaluate((n) => n.tagName)).toBe('DIV');
  });
});

// PR screenshots, opt-in like screenshots.spec.js: PFW_SHOTS=<dir> npm test
test.describe('inspect shots', () => {
  const DIR = process.env.PFW_SHOTS;
  test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');
  test.use({ viewport: { width: 1280, height: 720 } });
  const shoot = (page, name) => page.screenshot({ path: path.join(DIR, `${name}.png`) });
  const result = (page) => page.getByTestId('result');

  test('shot drawer markdown and csv', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await openNode(page, 'fx-done', 'research');
    await expect(art(page, 'research_report.csv')).toHaveAttribute('data-state', 'ready');
    await shoot(page, 'drawer-csv');
    await pick(page, 'writer');
    await expect(art(page, 'blog_post.md')).toHaveAttribute('data-state', 'ready');
    await shoot(page, 'drawer-markdown');
  });

  test('shot drawer image, json and other', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await openNode(page, 'fx-done', 'extract_image');
    await expect(art(page, 'featured.png').getByTestId('artifact-body')).toHaveJSProperty('complete', true);
    await shoot(page, 'drawer-image');
    await pick(page, 'youtube_metadata');
    await expect(art(page, 'metadata.json')).toHaveAttribute('data-state', 'ready');
    await shoot(page, 'drawer-json');
  });

  for (const [name, run, status, live] of [['result-ready', 'fx-done', 'ready', false],
    ['result-pending', 'fx-hitl', 'pending', true], ['result-unavailable', 'fx-crashed', 'unavailable', false]]) {
    test(`shot ${name}`, async ({ page }) => {
      freshServerState({ live });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(`/#/runs/${run}`);
      await page.getByTestId('result-open').click();
      await expect(result(page)).toHaveAttribute('data-status', status);
      await shoot(page, name);
    });
  }

  test('shot result with non-http(s) urls beside an http(s) card', async ({ page }) => {
    await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ json: {
      status: 'ready', reason: null, blog: { title: 'hostile urls', url: 'javascript:alert(1)', markdown: 'x' },
      research: [{ title: 'http card (link)', type: 'Book', url: 'https://e.org/r' },
        { title: 'javascript: card (no link)', type: 'Article', url: 'javascript:alert(2)' }],
    } }));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/#/runs/fx-done');
    await page.getByTestId('result-open').click();
    await expect(result(page).getByTestId('research-card')).toHaveCount(2);
    await shoot(page, 'result-hostile-urls');
  });

  test('shot drawer facts, findings, skipped, nothing and rich markdown', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    plant('fx-done', 'painforwisdom-writer', 'rich.md',
      '# Heading\n\nSome **bold**, *italic*, `code` and [link](https://example.org).\n\n> A quote\n\n- one\n  - nested\n- two\n\n1. first\n2. second\n\n---\n\n```\ncode block\n```\n\n| col a | col b |\n|---|---|\n| 1 | 2 |\n');
    await openNode(page, 'fx-done', 'bv_youtube');
    await expect(drawer(page).getByTestId('finding')).toHaveCount(1);
    await shoot(page, 'drawer-findings');
    await pick(page, 'youtube_upload');
    await expect(drawer(page)).toHaveAttribute('data-state', 'skipped');
    await shoot(page, 'drawer-skipped');
    await pick(page, 'transcribe');
    await expect(drawer(page).getByTestId('drawer-nothing')).toBeVisible();
    await shoot(page, 'drawer-nothing');
    await pick(page, 'writer');
    await expect(art(page, 'rich.md')).toHaveAttribute('data-state', 'ready');
    await art(page, 'rich.md').scrollIntoViewIfNeeded();
    await shoot(page, 'drawer-rich-markdown');
  });

  test('shot drawer phone width', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 720 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await openNode(page, 'fx-done', 'extract');
    await expect(art(page, 'extraction_report.md')).toHaveAttribute('data-state', 'ready');
    await shoot(page, 'drawer-phone');
  });
});
