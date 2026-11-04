// Operability view (spec 2026-11-03-console-operability §8).
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const output = (page) => page.getByTestId('output');
const card = (page, name) => page.getByTestId(`output-${name}`);
const ALL = ['thumbnail', 'blog', 'youtube', 'wordpress', 'kb', 'research', 'audit'];

/**
 * A real load: a goto that only changes the hash would keep the page.
 * V8 coverage survives only for the last document, so a gated test calls this once.
 */
const deep = async (page, run) => {
  await page.goto('about:blank');
  await page.goto(`/#/runs/${run}/output`);
  await expect(output(page)).toBeVisible();
};

const readPkg = (run, dir) => JSON.parse(fs.readFileSync(path.join(PROCESSED, run, 'langgraph', dir, 'package.json'), 'utf8'));

test.beforeEach(() => freshServerState());

test('open button is disabled until a run is selected', async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
  await page.goto('/');
  await expect(page.getByTestId('runs-empty')).toBeVisible();
  await expect(page.getByTestId('output-open')).toBeDisabled();
});

test('opens from the header, replaces the DAG, closes on Esc, button and toggle, keeps the route', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const open = page.getByTestId('output-open');
  await expect(open).toBeEnabled();
  await expect(page.getByTestId('dag')).toBeVisible();
  await open.click();
  await expect(output(page)).toBeVisible();
  await expect(page.getByTestId('dag')).toBeHidden();
  await expect(page.getByTestId('tele-strip')).toBeHidden();
  await expect(page.getByTestId('output-close')).toBeFocused();
  expect(new URL(page.url()).hash).toBe('#/runs/fx-done/output');

  await page.keyboard.press('x');
  await expect(output(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(output(page)).toBeHidden();
  await expect(page.getByTestId('dag')).toBeVisible();
  await expect(open).toBeFocused();
  expect(new URL(page.url()).hash).toBe('#/runs/fx-done');
  await page.keyboard.press('Escape');
  await expect(output(page)).toBeHidden();

  await open.click();
  await page.getByTestId('output-close').click();
  await expect(output(page)).toBeHidden();
  await open.click();
  await open.click();
  await expect(output(page)).toBeHidden();
});

test('deep link opens the view on that run; selecting another run closes it', async ({ page }) => {
  await deep(page, 'fx-done');
  await expect(card(page, 'audit').getByTestId('artifact-audit_report.md')).toHaveAttribute('data-state', 'ready');
  expect(new URL(page.url()).hash).toBe('#/runs/fx-done/output');
  await expect(page.getByTestId('run-fx-done')).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('run-fx-failed').click();
  await expect(output(page)).toBeHidden();
  expect(new URL(page.url()).hash).toBe('#/runs/fx-failed');
});

test('fx-done renders every card but YouTube, in spec order', async ({ page }) => {
  await deep(page, 'fx-done');
  const shown = ['thumbnail', 'blog', 'wordpress', 'kb', 'research', 'audit'];
  for (const name of shown) await expect(card(page, name)).toBeVisible();
  await expect(card(page, 'youtube')).toBeHidden();
  const order = await page.locator('.out-card').evaluateAll((els) => els.map((n) => n.dataset.testid));
  expect(order).toEqual(ALL.map((n) => `output-${n}`));

  await expect(card(page, 'thumbnail').locator('img')).toHaveJSProperty('complete', true);
  const widths = await card(page, 'thumbnail').evaluate((n) => [n.querySelector('img').clientWidth, n.clientWidth]);
  expect(widths[0]).toBeGreaterThan(widths[1] * 0.9);

  const blog = page.getByTestId('output-blog-link');
  await expect(blog).toHaveAttribute('href', 'https://www.notion.so/fx-blog-1');
  await expect(blog).toHaveAttribute('rel', 'noopener');
  await expect(blog).toHaveText('the trail that used to beat me');
  await expect(card(page, 'blog').getByTestId('artifact-blog_post.md')).toHaveAttribute('data-state', 'ready');
  await expect(card(page, 'blog').locator('.md')).toBeVisible();

  const entry = card(page, 'kb').getByTestId('artifact-2026-10-30.md');
  await expect(entry).toHaveAttribute('data-state', 'ready');
  await expect(entry.getByRole('heading', { level: 1 })).toHaveText('2026-10-30');
  await expect(card(page, 'kb').getByTestId('artifact-curator_summary.md')).toContainText('themes: discipline, patience');
  const vaultPath = path.join(path.dirname(PROCESSED), 'vault', 'entries', '2026-10-30.md');
  await expect(page.getByTestId('output-vault-link')).toHaveAttribute('href', `obsidian://open?path=${encodeURIComponent(fs.realpathSync(vaultPath))}`);

  const csv = card(page, 'research').getByTestId('artifact-research_report.csv');
  await expect(csv).toHaveAttribute('data-state', 'ready');
  await expect(csv.locator('th').first()).toHaveText('title');
  const refs = card(page, 'research').getByTestId('research-card');
  await expect(refs).toHaveCount(2);
  await expect(refs.first()).toHaveAttribute('href', 'https://example.org/endure');
  await expect(refs.first()).toHaveAttribute('target', '_blank');
  await expect(refs.first().getByTestId('type-icon')).toHaveAttribute('data-icon', 'book');

  await expect(card(page, 'audit').locator('.md')).toBeVisible();
});

test('every card hides when its data is absent', async ({ page }) => {
  await deep(page, 'fx-crashed');
  await expect(page.getByTestId('output-nothing')).toBeVisible();
  for (const name of ALL) await expect(card(page, name)).toBeHidden();
  await expect(page.locator('[data-testid="output-vault-link"], [data-testid="output-blog-link"]')).toHaveCount(0);
});

test('a kb card without a vault entry has no Obsidian link', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    delete body.nodes.kb_curator.outputs.vault_entry_path;
    await route.fulfill({ response: res, json: body });
  });
  await deep(page, 'fx-done');
  await expect(card(page, 'kb').getByTestId('artifact-curator_summary.md')).toHaveAttribute('data-state', 'ready');
  await expect(page.getByTestId('output-vault-link')).toHaveCount(0);
});

test('a legacy run has a blog card and no kb card', async ({ page }) => {
  await deep(page, 'fx-legacy');
  await expect(card(page, 'blog').getByTestId('artifact-blog_post.md')).toHaveAttribute('data-state', 'ready');
  await expect(card(page, 'kb')).toBeHidden();
  await expect(card(page, 'research').getByTestId('research-card')).toHaveCount(0);
});

test('non-http(s) Notion urls render without an href; an unknown type gets a generic icon', async ({ page }) => {
  await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ json: {
    status: 'ready', reason: null, blog: { title: 't', url: 'javascript:alert(1)', markdown: 'x' },
    research: [{ title: 'r', type: 'Zine', url: 'JavaScript:alert(2)' }],
  } }));
  await deep(page, 'fx-done');
  await expect(page.getByTestId('research-card')).toHaveText('r');
  await expect(page.getByTestId('research-card').getByTestId('type-icon')).toHaveAttribute('data-icon', 'globe');
  await expect(page.getByTestId('output-blog-link')).toHaveText('t');
  await expect(card(page, 'blog').locator('.links [href]')).toHaveCount(0);
  await expect(card(page, 'research').locator('.links [href]')).toHaveCount(0);
});

test('failed reads hide their cards and show the health badge', async ({ page, errors }) => {
  errors.allow(/status of 503/);
  for (const glob of ['artifacts', 'result', 'publish']) {
    await page.route(`**/api/runs/fx-done/${glob}`, (route) => route.fulfill({ status: 503, body: 'nope' }));
  }
  await deep(page, 'fx-done');
  await expect(page.getByTestId('error-badge')).toBeVisible();
  await expect(page.getByTestId('output-nothing')).toBeVisible();
});

test('editor renders every C9 type from field_specs', async ({ page }) => {
  await deep(page, 'fx-done');
  await expect(page.getByTestId('field-wordpress-title')).toHaveValue('The hill does not care');
  await expect(page.getByTestId('field-wordpress-body').evaluate((n) => n.tagName)).resolves.toBe('TEXTAREA');
  await expect(page.getByTestId('field-wordpress-excerpt').evaluate((n) => n.tagName)).resolves.toBe('TEXTAREA');
  await expect(page.getByTestId('field-wordpress-status')).toHaveValue('draft');
  await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveAttribute('type', 'url');
  await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['painforwisdom', 'discipline']);
  await expect(page.getByTestId('readonly-wordpress')).toContainText('fx-blog-1');
  await expect(page.getByTestId('asset-wordpress-featured_image')).toHaveText('featured.png');
  await expect(page.getByTestId('status-wordpress')).toHaveAttribute('aria-label', 'pending');

  const preview = page.getByTestId('preview-wordpress-body');
  await expect(preview.locator('strong')).toHaveText('Keep walking.');
  await page.getByTestId('field-wordpress-body').fill('# New <b>head</b>');
  await expect(preview.getByRole('heading', { level: 1 })).toHaveText('New <b>head</b>');

  await page.getByTestId('field-wordpress-tags').fill('one, two,,');
  await page.getByTestId('field-wordpress-tags').press('Enter');
  await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['painforwisdom', 'discipline', 'one', 'two']);
  await page.getByTestId('chip-remove-wordpress-tags').first().click();
  await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['discipline', 'one', 'two']);
});

test('a package field missing from fields renders empty', async ({ page }) => {
  const pkg = readPkg('fx-done', 'wordpress-draft');
  delete pkg.fields.excerpt;
  delete pkg.fields.tags;
  await page.route('**/api/runs/fx-done/publish', (route) => route.fulfill({ json: { targets: { wordpress: pkg } } }));
  await deep(page, 'fx-done');
  await expect(page.getByTestId('field-wordpress-excerpt')).toHaveValue('');
  await expect(page.getByTestId('chip-wordpress-tags')).toHaveCount(0);
});

test('edit, confirm, publishing, published read-only with link', async ({ page }) => {
  await deep(page, 'fx-done');
  await page.getByTestId('field-wordpress-title').fill('A better title');
  await page.getByTestId('field-wordpress-tags').fill('fresh');
  await page.getByTestId('publish-wordpress').click();
  const link = page.getByTestId('published-wordpress');
  await expect(link).toHaveAttribute('href', 'https://example.invalid/wordpress/fx-done', { timeout: 15_000 });
  await expect(page.getByTestId('status-wordpress')).toHaveAttribute('aria-label', 'published');
  await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
  await expect(page.getByTestId('publish-wordpress')).toBeHidden();
  const written = readPkg('fx-done', 'wordpress-draft');
  expect(written.fields.title).toBe('A better title');
  expect(written.fields.tags).toEqual(['painforwisdom', 'discipline', 'fresh']);
});

test('polls every 2 s only while publishing', async ({ page }) => {
  const t0 = Date.parse('2026-11-03T10:00:00Z');
  await page.clock.install({ time: t0 });
  const pkg = readPkg('fx-done', 'wordpress-draft');
  let state = { ...pkg, status: 'publishing' };
  let gets = 0;
  await page.route('**/api/runs/fx-done/publish', (route) => {
    gets += 1;
    return route.fulfill({ json: { targets: { wordpress: state } } });
  });
  await deep(page, 'fx-done');
  await expect(page.getByTestId('status-wordpress')).toHaveAttribute('aria-label', 'publishing');
  await expect(page.getByTestId('publish-wordpress')).toBeDisabled();
  await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
  // Pausing fires the pending poll once; the page then re-arms it at the paused instant.
  const before = gets;
  await page.clock.pauseAt(t0 + 60_000);
  await expect.poll(() => gets).toBeGreaterThan(before);
  const flush = () => page.evaluate(() => fetch('/health'));
  await flush();
  const armed = gets;
  await page.clock.runFor(1900);
  await flush();
  expect(gets).toBe(armed);
  state = { ...pkg, status: 'published', result: { id: '1', url: 'javascript:alert(1)' } };
  await page.clock.runFor(200);
  await expect.poll(() => gets).toBe(armed + 1);
  await expect(page.getByTestId('published-wordpress')).toBeVisible();
  await expect(page.getByTestId('published-wordpress')).not.toHaveAttribute('href');
  await page.clock.runFor(6000);
  await flush();
  expect(gets).toBe(armed + 1);
});

test('a failed poll keeps polling', async ({ page, errors }) => {
  errors.allow(/status of 503/);
  await page.clock.install();
  const pkg = readPkg('fx-done', 'wordpress-draft');
  let fail = false;
  await page.route('**/api/runs/fx-done/publish', (route) => (fail
    ? route.fulfill({ status: 503, body: 'nope' })
    : route.fulfill({ json: { targets: { wordpress: { ...pkg, status: 'publishing' } } } })));
  await deep(page, 'fx-done');
  await expect(page.getByTestId('status-wordpress')).toHaveAttribute('aria-label', 'publishing');
  fail = true;
  const failed = page.waitForResponse((r) => r.url().endsWith('/publish') && r.status() === 503);
  await page.clock.runFor(2100);
  await failed;
  await expect(page.getByTestId('error-badge')).toBeVisible();
  fail = false;
  const healed = page.waitForResponse((r) => r.url().endsWith('/publish') && r.status() === 200);
  await page.clock.runFor(2100);
  await healed;
});

test('title FAIL shows the error and stays editable', async ({ page }) => {
  await deep(page, 'fx-done');
  await page.getByTestId('field-wordpress-title').fill('FAIL');
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('publish-error-wordpress')).toHaveText('fake publish failure', { timeout: 15_000 });
  await expect(page.getByTestId('status-wordpress')).toHaveAttribute('aria-label', 'failed');
  await expect(page.getByTestId('field-wordpress-title')).toBeEnabled();
  await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
});

test('client checks mirror the server: required and max, per field, no POST', async ({ page }) => {
  let posts = 0;
  page.on('request', (r) => { if (r.method() === 'POST') posts += 1; });
  await deep(page, 'fx-done');
  await page.getByTestId('field-wordpress-title').fill('   ');
  await page.getByTestId('field-wordpress-excerpt').fill('x'.repeat(301));
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('error-wordpress-title')).toHaveText('title: required');
  await expect(page.getByTestId('error-wordpress-excerpt')).toHaveText('excerpt: over max 300');
  expect(posts).toBe(0);
});

test('a server 400 lands per field; an unknown field in the form error', async ({ page, errors }) => {
  errors.allow(/status of 400/);
  await deep(page, 'fx-done');
  await page.getByTestId('field-wordpress-youtube_url').fill('not-a-url');
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('error-wordpress-youtube_url')).toHaveText('youtube_url: not an http(s) url');
  await expect(page.getByTestId('publish-wordpress')).toBeEnabled();

  await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 400, json: { detail: ['zzz: unknown field'] } }));
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('publish-error-wordpress')).toHaveText('zzz: unknown field');
  await expect(page.getByTestId('error-wordpress-youtube_url')).toBeEmpty();
});

test('409 while the lock exists shows the detail', async ({ page, errors }) => {
  errors.allow(/status of 409/);
  await deep(page, 'fx-done');
  await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
  fs.writeFileSync(path.join(PROCESSED, 'fx-done', 'langgraph', 'wordpress-draft', 'publish.lock'), '');
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('publish-error-wordpress')).toHaveText('publishing');
  await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
});

test('a 500 with no JSON body reports the HTTP status', async ({ page, errors }) => {
  errors.allow(/status of 500/);
  await deep(page, 'fx-done');
  await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 500, body: 'boom' }));
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('publish-error-wordpress')).toHaveText('HTTP 500');
});

test('fx-published: WordPress read-only with link, YouTube editable from its own specs', async ({ page }) => {
  await deep(page, 'fx-published');
  const order = await page.locator('.out-card:visible').evaluateAll((els) => els.map((n) => n.dataset.testid));
  expect(order.indexOf('output-youtube')).toBeLessThan(order.indexOf('output-wordpress'));
  await expect(page.getByTestId('published-wordpress')).toHaveAttribute('href', 'https://example.invalid/wordpress/fx-published');
  await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
  await expect(page.getByTestId('publish-wordpress')).toBeHidden();
  await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveValue('');

  await expect(page.getByTestId('field-youtube-privacy_status')).toHaveValue('private');
  await expect(page.getByTestId('asset-youtube-video')).toHaveText('video_2026-10-30.mp4');
  await expect(page.getByTestId('readonly-youtube')).toContainText('42');
  await expect(page.getByTestId('field-youtube-youtube_url')).toHaveCount(0);
});

test('WordPress youtube_url pre-fills from a published YouTube, stays editable, never overwrites', async ({ page }) => {
  const wordpress = readPkg('fx-done', 'wordpress-draft');
  const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
  await page.route('**/api/runs/fx-done/publish', (route) => route.fulfill({ json: { targets: { wordpress, youtube } } }));
  await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 202, json: wordpress }));
  await deep(page, 'fx-done');
  const url = page.getByTestId('field-wordpress-youtube_url');
  await expect(url).toHaveValue('https://youtu.be/abc');
  await expect(page.getByTestId('published-youtube')).toBeVisible();

  // Same package on each confirm, so the refresh keeps this editor: only the pre-fill could change the field.
  const confirm = async () => {
    const refreshed = page.waitForResponse((r) => r.url().endsWith('/publish') && r.request().method() === 'GET');
    await page.getByTestId('publish-wordpress').click();
    await refreshed;
    await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
  };
  await url.fill('https://youtu.be/mine');
  await confirm();
  await expect(url).toHaveValue('https://youtu.be/mine');
  await url.fill('');
  await confirm();
  await expect(url).toHaveValue('');
});

test('a published WordPress package is never pre-filled', async ({ page }) => {
  const wordpress = { ...readPkg('fx-done', 'wordpress-draft'), status: 'published', result: { id: 'w', url: 'https://example.invalid/w' } };
  const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
  await page.route('**/api/runs/fx-done/publish', (route) => route.fulfill({ json: { targets: { wordpress, youtube } } }));
  await deep(page, 'fx-done');
  await expect(page.getByTestId('published-wordpress')).toBeVisible();
  await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveValue('');
});

test('a run update refreshes the open view: a live fake run fills its cards without dropping edits', async ({ page, request }) => {
  test.setTimeout(60_000);
  const res = await request.post('/api/runs', { data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: 300 } } });
  const id = (await res.json()).run_id;
  await deep(page, id);
  await expect(page.getByTestId('field-wordpress-title')).toHaveValue('Fake post', { timeout: 30_000 });
  await page.getByTestId('field-wordpress-title').fill('Kept while running');
  await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  await expect(card(page, 'blog').getByTestId('artifact-blog_post.md')).toHaveAttribute('data-state', 'ready');
  await expect(page.getByTestId('field-wordpress-title')).toHaveValue('Kept while running');
  await page.getByTestId('publish-wordpress').click();
  await expect(page.getByTestId('published-wordpress')).toHaveAttribute('href', `https://example.invalid/wordpress/${id}`, { timeout: 15_000 });
});

// PR screenshots, opt-in like screenshots.spec.js: PFW_SHOTS=<dir> npm test
test.describe('output shots', () => {
  const DIR = process.env.PFW_SHOTS;
  test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');
  test.use({ viewport: { width: 1280, height: 900 } });
  const shoot = (page, name) => page.screenshot({ path: path.join(DIR, `${name}.png`) });
  const shootCard = async (page, name, file) => {
    await card(page, name).scrollIntoViewIfNeeded();
    await card(page, name).screenshot({ path: path.join(DIR, `${file}.png`) });
  };

  test.beforeEach(async ({ page }) => page.emulateMedia({ reducedMotion: 'reduce' }));

  test('shot fx-done cards', async ({ page }) => {
    await deep(page, 'fx-done');
    await expect(card(page, 'audit').locator('.md')).toBeVisible();
    await expect(card(page, 'thumbnail').locator('img')).toHaveJSProperty('complete', true);
    await shoot(page, 'output-top');
    for (const name of ['thumbnail', 'blog', 'wordpress', 'kb', 'research', 'audit']) await shootCard(page, name, `output-${name}`);
  });

  test('shot published, failed, errors, nothing, phone', async ({ page, errors }) => {
    errors.allow(/status of 4/);
    await deep(page, 'fx-published');
    await expect(page.getByTestId('published-wordpress')).toBeVisible();
    await shootCard(page, 'wordpress', 'output-wordpress-published');
    await shootCard(page, 'youtube', 'output-youtube-pending');

    await deep(page, 'fx-done');
    await page.getByTestId('field-wordpress-title').fill('   ');
    await page.getByTestId('field-wordpress-youtube_url').fill('nope');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('error-wordpress-title')).toBeVisible();
    await shootCard(page, 'wordpress', 'output-wordpress-field-error');

    await page.getByTestId('field-wordpress-title').fill('FAIL');
    await page.getByTestId('field-wordpress-youtube_url').fill('');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('fake publish failure', { timeout: 15_000 });
    await shootCard(page, 'wordpress', 'output-wordpress-failed');

    await deep(page, 'fx-crashed');
    await expect(page.getByTestId('output-nothing')).toBeVisible();
    await shoot(page, 'output-nothing');

    await page.setViewportSize({ width: 390, height: 720 });
    await deep(page, 'fx-published');
    await expect(page.getByTestId('field-youtube-title')).toBeVisible();
    await shoot(page, 'output-phone');
  });
});
