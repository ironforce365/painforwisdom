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

  await page.getByTestId('node-youtube_upload').click();
  await expect(drawer(page)).toHaveAttribute('data-node', 'youtube_upload');
  await expect(art(page, 'metadata.json')).toHaveAttribute('data-state', 'ready');
  const json = await art(page, 'metadata.json').getByTestId('artifact-body').textContent();
  expect(json).toContain('\n  ');

  await pick(page, 'wordpress_draft');
  const img = art(page, 'featured.png').getByTestId('artifact-body');
  await expect(img).toHaveJSProperty('complete', true);
  expect(await img.evaluate((el) => el.naturalWidth)).toBeGreaterThan(0);
  await expect(art(page, 'frames.bin').getByTestId('artifact-body')).toHaveAttribute('download', 'frames.bin');

  await pick(page, 'summarizer');
  await expect(art(page, 'telegram.txt').getByTestId('artifact-body')).toHaveText(/\S/);
  await expect(art(page, 'audit_report.md')).toHaveAttribute('data-state', 'ready');

  await drawer(page).getByTestId('drawer-close').click();
  await page.getByTestId('node-transcribe').press('Enter'); // a skipped node's hollow disc takes no clicks
  await expect(drawer(page)).toHaveAttribute('data-node', 'transcribe');
  await expect(drawer(page)).toHaveAttribute('data-state', 'skipped');
  await expect(drawer(page).getByTestId('drawer-duration')).toHaveText('–');
  await expect(drawer(page).getByTestId('artifact-list')).toBeEmpty();
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
    await openNode(page, 'fx-done', 'wordpress_draft');
    await expect(art(page, 'featured.png').getByTestId('artifact-body')).toHaveJSProperty('complete', true);
    await shoot(page, 'drawer-image');
    await pick(page, 'youtube_upload');
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

  test('shot drawer phone width', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 720 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await openNode(page, 'fx-done', 'extract');
    await expect(art(page, 'extraction_report.md')).toHaveAttribute('data-state', 'ready');
    await shoot(page, 'drawer-phone');
  });
});
