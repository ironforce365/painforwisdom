// Output workspace (spec 2026-11-05-console-end-to-end, section 2 D and P branches, N2, N3, section 4 Output).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const JS_OUTPUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js/output');
const SECTIONS = ['summary', 'blog', 'youtube', 'wordpress', 'kb', 'research', 'audit', 'files'];

const output = (page) => page.getByTestId('output');
const nav = (page, name) => page.getByTestId(`output-nav-${name}`);
const section = (page, name) => page.getByTestId(`output-section-${name}`);
const chip = (page, name) => page.getByTestId(`output-chip-${name}`);
const dirty = (page, name) => page.getByTestId(`output-dirty-${name}`);
const editor = (page, name) => page.getByTestId(`artifact-edit-${name}`);
const saveBtn = (page, name) => page.getByTestId(`artifact-save-${name}`);

/** A real load: a goto that only changes the hash would keep the page. */
const deep = async (page, run) => {
  await page.goto('about:blank');
  await page.goto(`/#/runs/${run}/output`);
  await expect(output(page)).toBeVisible();
};
const go = async (page, run, name) => {
  await deep(page, run);
  await nav(page, name).click();
  await expect(section(page, name)).toBeVisible();
};
const langgraph = (run, ...rest) => path.join(PROCESSED, run, 'langgraph', ...rest);
const readPkg = (run, dir) => JSON.parse(fs.readFileSync(langgraph(run, dir, 'package.json'), 'utf8'));
const writePkg = (run, dir, pkg) => {
  fs.mkdirSync(langgraph(run, dir), { recursive: true });
  fs.writeFileSync(langgraph(run, dir, 'package.json'), JSON.stringify(pkg));
};
const setRecord = (run, fields) => {
  const file = langgraph(run, 'run.json');
  fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, 'utf8')), ...fields }));
};
/** Patch the run detail the server answers. */
const patchRun = (page, run, patch) => page.route(`**/api/runs/${run}`, async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  patch(body);
  await route.fulfill({ response: res, json: body });
});
const mockPublish = (page, run, targets, prepare = {}) => page.route(`**/api/runs/${run}/publish`,
  (route) => route.fulfill({ json: { targets, prepare } }));

test.beforeEach(({ errors }) => {
  freshServerState();
  // Fixture packages name assets that do not exist; the panel handles that, Chromium still logs it.
  errors.allow(/status of 404.*\/asset\//);
});

test.describe('navigation', () => {
  test('[DT-N3] section nav shows one section at a time, j/k move, typing and modifiers do not', async ({ page }) => {
    await deep(page, 'fx-done');
    await expect(nav(page, 'summary')).toHaveAttribute('aria-current', 'true');
    for (const name of SECTIONS) await expect(nav(page, name)).toBeVisible();
    const visible = async () => {
      const shown = [];
      for (const name of SECTIONS) if (await section(page, name).isVisible()) shown.push(name);
      return shown;
    };
    await expect.poll(visible).toEqual(['summary']);

    await page.keyboard.press('k');
    await expect.poll(visible).toEqual(['summary']);
    await page.keyboard.press('j');
    await expect.poll(visible).toEqual(['blog']);
    await expect(nav(page, 'blog')).toHaveAttribute('aria-current', 'true');
    await expect(nav(page, 'summary')).not.toHaveAttribute('aria-current', 'true');
    await page.keyboard.press('Control+j');
    await expect.poll(visible).toEqual(['blog']);

    await editor(page, 'blog_post.md').click();
    await page.keyboard.press('j');
    await expect.poll(visible).toEqual(['blog']);
    await expect(editor(page, 'blog_post.md')).toHaveValue(/j/);
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.activeElement.blur());
    for (let i = 0; i < 8; i += 1) await page.keyboard.press('j');
    await expect.poll(visible).toEqual(['files']);
    await page.keyboard.press('k');
    await expect.poll(visible).toEqual(['audit']);
    await nav(page, 'research').click();
    await expect.poll(visible).toEqual(['research']);
  });

  test('[DT-N3] the panel uses the full width of its container, with no centring', async ({ page }) => {
    await page.setViewportSize({ width: 3440, height: 1300 });
    await deep(page, 'fx-done');
    await nav(page, 'blog').click();
    await expect(editor(page, 'blog_post.md')).toBeVisible();
    const box = await page.locator('#operability').evaluate((root) => {
      const panel = root.querySelector('[data-testid="output"]');
      const edge = (n) => n.getBoundingClientRect();
      return {
        container: edge(root).width,
        panel: edge(panel).width,
        right: edge(root).right,
        sectionRight: edge(panel.querySelector('[data-testid="output-section-blog"]')).right,
        editorWidth: edge(panel.querySelector('[data-testid="artifact-edit-blog_post.md"]')).width,
        maxWidth: getComputedStyle(panel).maxWidth,
      };
    });
    expect(box.container).toBeGreaterThan(3000);
    expect(Math.abs(box.panel - box.container)).toBeLessThan(2);
    expect(Math.abs(box.sectionRight - box.right)).toBeLessThan(2);
    expect(box.maxWidth).toBe('none');
    expect(box.editorWidth).toBeGreaterThan(1200);
  });

  test('[DT-N3] a long section scrolls inside its pane, the page does not', async ({ page }) => {
    fs.writeFileSync(langgraph('fx-done', 'painforwisdom-writer', 'blog_post.md'), `# Long\n\n${'A line.\n\n'.repeat(600)}`);
    await page.setViewportSize({ width: 1280, height: 720 });
    await deep(page, 'fx-done');
    await nav(page, 'blog').click();
    await expect(page.getByTestId('artifact-preview-blog_post.md').locator('h1')).toHaveText('Long');
    const metrics = await page.evaluate(() => {
      const pane = document.querySelector('[data-testid="artifact-preview-blog_post.md"]');
      return { doc: document.documentElement.scrollHeight, view: innerHeight, pane: pane.scrollHeight, box: pane.clientHeight };
    });
    expect(metrics.doc).toBeLessThanOrEqual(metrics.view);
    expect(metrics.pane).toBeGreaterThan(metrics.box);
  });

  test('[DT-N3] nav chips read the state at a glance', async ({ page }) => {
    await deep(page, 'fx-published');
    await expect(chip(page, 'summary')).toHaveText('PASS');
    await expect(chip(page, 'youtube')).toHaveText('pending');
    await expect(chip(page, 'wordpress')).toHaveText('published');
    await expect(chip(page, 'research')).toHaveText('0');
    await expect(chip(page, 'blog')).toHaveText('none');
    await expect(chip(page, 'audit')).toHaveText('none');
    await deep(page, 'fx-unpackaged');
    await expect(chip(page, 'research')).toHaveText('6');
    await expect(chip(page, 'youtube')).toHaveText('no package');
    await expect(chip(page, 'blog')).toHaveText('ready');
    await expect(chip(page, 'kb')).toHaveText('1');
    await expect(chip(page, 'files')).not.toHaveText('0');
    await deep(page, 'fx-modeoff');
    await expect(chip(page, 'youtube')).toHaveText('off');
    await expect(chip(page, 'wordpress')).toHaveText('off');
    await deep(page, 'fx-failed');
    await expect(chip(page, 'summary')).toHaveText('failed');
  });

  test('[DT-N2] a deep link to output keeps its view after a reload, tabs hide and show it, section remembered', async ({ page }) => {
    await deep(page, 'fx-done');
    await nav(page, 'research').click();
    await page.reload();
    await expect(output(page)).toBeVisible();
    expect(new URL(page.url()).hash).toBe('#/runs/fx-done/output');
    await expect(page.getByTestId('view-tab-output')).toHaveAttribute('aria-selected', 'true');
    await nav(page, 'research').click();

    await page.getByTestId('view-tab-graph').click();
    await expect(output(page)).toBeHidden();
    await page.keyboard.press('j');
    await page.getByTestId('view-tab-output').click();
    await expect(output(page)).toBeVisible();
    await expect(section(page, 'research')).toBeVisible();

    await page.getByTestId('run-fx-unpackaged').click();
    await expect(output(page)).toBeVisible();
    await expect(section(page, 'research')).toBeVisible();
    await expect(page.getByTestId('research-link-1')).toBeVisible();
    expect(new URL(page.url()).hash).toBe('#/runs/fx-unpackaged/output');
  });

  test('[DT-N2] a run opened on the graph loads its output only when the tab opens', async ({ page }) => {
    let lists = 0;
    page.on('request', (r) => { if (r.url().endsWith('/fx-done/artifacts')) lists += 1; });
    const detail = page.waitForResponse((r) => r.url().endsWith('/api/runs/fx-done'));
    await page.goto('/#/runs/fx-done');
    await detail;
    await expect(page.getByTestId('run-fx-done')).toHaveAttribute('aria-pressed', 'true');
    await expect(output(page)).toBeHidden();
    expect(lists).toBe(0);
    await page.getByTestId('view-tab-output').click();
    await expect(page.getByTestId('summary-verdict')).toBeVisible();
    await expect.poll(() => lists).toBeGreaterThan(0);
  });

  test('[DT-N2] output without a selected run says so', async ({ page }) => {
    await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [] } }));
    await page.goto('/');
    await expect(page.getByTestId('runs-empty')).toBeVisible();
    await expect(output(page)).toBeHidden();
    await page.getByTestId('view-tab-output').click();
    await expect(page.getByTestId('output-norun')).toBeVisible();
  });

  test('failed reads keep the panel and show the health badge', async ({ page, errors }) => {
    errors.allow(/status of 503/);
    for (const glob of ['artifacts', 'result', 'publish', 'research']) {
      await page.route(`**/api/runs/fx-done/${glob}`, (route) => route.fulfill({ status: 503, body: 'nope' }));
    }
    await deep(page, 'fx-done');
    await expect(page.getByTestId('error-badge')).toBeVisible();
    await expect(chip(page, 'summary')).toHaveText('PASS');
    await expect(chip(page, 'youtube')).toHaveText('…');
    await nav(page, 'youtube').click();
    await expect(page.getByTestId('prepare-reason-youtube')).toHaveText('Package state is not available yet.');
  });

  test('no module under js/output uses innerHTML', () => {
    for (const file of fs.readdirSync(JS_OUTPUT)) {
      expect(fs.readFileSync(path.join(JS_OUTPUT, file), 'utf8'), file).not.toContain('innerHTML');
    }
  });
});

test.describe('summary', () => {
  test('[DT-D1] verdict PASS: thumbnail, no failing checks, publish state, next steps and links', async ({ page }) => {
    await deep(page, 'fx-done');
    await expect(page.getByTestId('summary-verdict')).toContainText('PASS');
    await expect(page.getByTestId('summary-input')).toHaveText('transcript_2026-10-30.txt');
    await expect(page.getByTestId('summary-thumbnail')).toHaveJSProperty('complete', true);
    await expect(page.getByTestId('summary-checks-ok')).toBeVisible();
    await expect(page.getByTestId('summary-check')).toHaveCount(0);
    await expect(page.getByTestId('summary-publish-wordpress')).toContainText('pending');
    await expect(page.getByTestId('summary-publish-youtube')).toContainText('no package');
    const next = page.getByTestId('summary-next-item');
    await expect(next).toHaveCount(2);
    await expect(next.first()).toContainText('YouTube');
    await expect(next.first()).toContainText('this run has no video');
    await expect(next.last()).toContainText('Review and publish');
    await next.last().getByRole('button').click();
    await expect(section(page, 'wordpress')).toBeVisible();
    await nav(page, 'summary').click();
    await page.getByTestId('summary-go-research').click();
    await expect(section(page, 'research')).toBeVisible();
    await nav(page, 'summary').click();
    await expect(page.getByTestId('summary-link-notion')).toHaveAttribute('href', 'https://www.notion.so/fx-blog-1');
    await expect(page.getByTestId('summary-link-notion')).toHaveAttribute('rel', 'noopener');
  });

  test('[DT-P6] the summary links what is published and has nothing to do once all is', async ({ page }) => {
    const wordpress = { ...readPkg('fx-published', 'wordpress-draft') };
    const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
    await mockPublish(page, 'fx-published', { wordpress, youtube });
    await deep(page, 'fx-published');
    await expect(page.getByTestId('summary-published-wordpress')).toHaveAttribute('href', 'https://example.invalid/wordpress/fx-published');
    await expect(page.getByTestId('summary-published-youtube')).toHaveAttribute('href', 'https://youtu.be/abc');
    await expect(page.getByTestId('summary-next-none')).toBeVisible();
    await expect(page.getByTestId('summary-link-notion')).toHaveCount(1);
  });

  test('[DT-D1] next steps for a run that can prepare, a failed publish and a run still publishing', async ({ page }) => {
    const wordpress = { ...readPkg('fx-done', 'wordpress-draft'), status: 'failed', error: 'boom' };
    const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'publishing' };
    await mockPublish(page, 'fx-done', { wordpress, youtube });
    await deep(page, 'fx-done');
    const next = page.getByTestId('summary-next-item');
    await expect(next).toHaveCount(2);
    await expect(next.first()).toContainText('still publishing');
    await expect(next.last()).toContainText('failed');

    await deep(page, 'fx-unpackaged');
    await expect(page.getByTestId('summary-next-item').first()).toContainText('Prepare');
  });

  test('[DT-D1] an active run has one next step: wait', async ({ page }) => {
    freshServerState({ live: true });
    await deep(page, 'fx-running');
    await expect(page.getByTestId('summary-next-item')).toHaveCount(1);
    await expect(page.getByTestId('summary-next-item')).toContainText('still running');
    await expect(page.getByTestId('summary-verdict')).toContainText('running');
    await expect(chip(page, 'summary')).toHaveText('running');
    await expect(page.getByTestId('summary-input')).toBeVisible();
  });

  test('[DT-D2] verdict PARTIAL lists failing checks, the output stays usable', async ({ page }) => {
    setRecord('fx-done', { verdict: 'PARTIAL' });
    await patchRun(page, 'fx-done', (run) => {
      run.nodes.bv_youtube.result = {
        branch_findings_youtube: [
          JSON.stringify({ name: 'upload url present', ok: false, detail: 'no url', severity: 'core' }),
          JSON.stringify({ name: 'passes', ok: true, severity: 'core' }),
          '{"name": "cut off at 500 char',
        ],
        branch_verdict_youtube: 'PARTIAL',
      };
    });
    await deep(page, 'fx-done');
    await expect(page.getByTestId('summary-verdict')).toContainText('PARTIAL');
    await expect(chip(page, 'summary')).toHaveText('PARTIAL');
    const checks = page.getByTestId('summary-check');
    await expect(checks).toHaveCount(2);
    await expect(checks.first()).toContainText('upload url present');
    await expect(checks.first()).toContainText('no url');
    await expect(checks.first()).toHaveAttribute('data-level', 'core');
    await expect(checks.last()).toContainText('cut off at 500');
    await expect(page.getByTestId('summary-next-item').first()).toContainText('failing check');

    await nav(page, 'audit').click();
    await expect(page.getByTestId('audit-check')).toHaveCount(7);
    await nav(page, 'blog').click();
    await editor(page, 'blog_post.md').fill('still usable');
    await saveBtn(page, 'blog_post.md').click();
    await expect(page.getByTestId('artifact-notice-blog_post.md')).toHaveText('Saved');
  });

  test('[DT-D6] cost per node and per run, marked as estimated', async ({ page }) => {
    await deep(page, 'fx-unpackaged');
    await expect(page.getByTestId('summary-cost')).toContainText('≈$');
    await expect(page.getByTestId('summary-cost-row-kb_curator')).toContainText('≈$');
    await expect(page.getByTestId('summary-cost-row-writer')).toContainText('tokens only');
    await expect(page.getByTestId('summary-cost-row-wordpress_draft')).toHaveCount(0);
  });

  test('[DT-D6] a billed cost has no estimate mark, an unpriced run shows tokens only', async ({ page }) => {
    await patchRun(page, 'fx-done', (run) => {
      run.telemetry = { cost_usd: 1.5, cost_estimated: false, tokens: { input: 1, output: 1, cache_read: 0, cache_creation: 0 },
        per_node: {
          writer: { cost_usd: 1.5, cost_estimated: false, input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_creation_tokens: 0 },
          extract: { cost_usd: 0.0042, cost_estimated: false, input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_creation_tokens: 0 },
        } };
    });
    await patchRun(page, 'fx-published', (run) => {
      run.input.path = null;
      run.telemetry = { cost_usd: null, cost_estimated: false, tokens: { input: 1, output: 1, cache_read: 0, cache_creation: 0 }, per_node: {} };
    });
    await deep(page, 'fx-done');
    await expect(page.getByTestId('summary-cost')).toHaveText('$1.50');
    await expect(page.getByTestId('summary-cost-row-extract')).toContainText('$0.0042');
    await deep(page, 'fx-published');
    await expect(page.getByTestId('summary-input')).toHaveText('unknown input');
    await expect(page.getByTestId('summary-cost')).toHaveText('tokens only (2)');
  });
});

test.describe('reading and editing in place', () => {
  test('[DT-D3] every output reads in place: blog, KB, audit, files', async ({ page }) => {
    await go(page, 'fx-done', 'blog');
    await expect(page.getByTestId('artifact-preview-blog_post.md').locator('.md')).toBeVisible();
    const blog = page.getByTestId('output-blog-link');
    await expect(blog).toHaveAttribute('href', 'https://www.notion.so/fx-blog-1');
    await expect(blog).toHaveAttribute('rel', 'noopener');
    await expect(blog).toHaveText('Notion: the trail that used to beat me');

    await nav(page, 'kb').click();
    await expect(page.getByTestId('artifact-preview-2026-10-30.md').getByRole('heading', { level: 1 })).toHaveText('2026-10-30');
    await expect(page.getByTestId('artifact-preview-curator_summary.md')).toContainText('themes: discipline, patience');
    const vaultPath = path.join(path.dirname(PROCESSED), 'vault', 'entries', '2026-10-30.md');
    await expect(page.getByTestId('output-vault-link')).toHaveAttribute('href', `obsidian://open?path=${encodeURIComponent(fs.realpathSync(vaultPath))}`);

    await nav(page, 'audit').click();
    await expect(page.getByTestId('artifact-preview-audit_report.md').locator('.md')).toContainText('PASS 20/20');

    await nav(page, 'files').click();
    for (const node of ['writer', 'summarizer', 'kb_curator']) await expect(page.getByTestId(`file-group-${node}`)).toBeVisible();
    await page.getByTestId('file-curator_summary.md').click();
    await expect(editor(page, 'curator_summary.md')).toBeVisible();
    await page.getByTestId('file-research_report.csv').click();
    await expect(page.getByTestId('artifact-preview-research_report.csv').locator('th').first()).toHaveText('title');
    await page.getByTestId('file-metadata.json').click();
    await expect(page.getByTestId('artifact-preview-metadata.json').locator('pre')).toBeVisible();
    await page.getByTestId('file-telegram.txt').click();
    await expect(page.getByTestId('artifact-preview-telegram.txt').locator('pre')).toBeVisible();
    // Not text: shown, never editable.
    await page.getByTestId('file-featured.png').click();
    await expect(page.getByTestId('artifact-featured.png').locator('img')).toBeVisible();
    await expect(editor(page, 'featured.png')).toHaveCount(0);
    await page.getByTestId('file-frames.bin').click();
    await expect(page.getByTestId('artifact-frames.bin').locator('a')).toHaveAttribute('download', 'frames.bin');
    await expect(editor(page, 'frames.bin')).toHaveCount(0);
    // A package has its own state machine: read here, edited in its section.
    await page.getByTestId('file-package.json').click();
    await expect(page.getByTestId('artifact-package.json')).toHaveAttribute('data-state', 'ready');
    await expect(editor(page, 'package.json')).toHaveCount(0);
  });

  test('[DT-D3] a run without a blog, KB, audit or files says so', async ({ page }) => {
    await deep(page, 'fx-modeoff');
    await expect(page.getByTestId('summary-thumbnail')).toHaveCount(0);
    for (const name of ['blog', 'kb', 'audit', 'files']) {
      await nav(page, name).click();
      await expect(page.getByTestId(`section-empty-${name}`)).toBeVisible();
    }
    await expect(chip(page, 'files')).toHaveText('0');
    await expect(page.getByTestId('output-vault-link')).toHaveCount(0);
  });

  test('[DT-D3] a KB artifact without a vault entry has no Obsidian link', async ({ page }) => {
    await patchRun(page, 'fx-done', (run) => { delete run.nodes.kb_curator.outputs.vault_entry_path; });
    await go(page, 'fx-done', 'kb');
    await expect(page.getByTestId('artifact-preview-curator_summary.md')).toContainText('themes');
    await expect(page.getByTestId('output-vault-link')).toHaveCount(0);
  });

  test('[DT-D3] non-http(s) Notion urls render without an href', async ({ page }) => {
    await page.route('**/api/runs/fx-done/result', (route) => route.fulfill({ json: {
      status: 'ready', reason: null, blog: { title: 't', url: 'javascript:alert(1)', markdown: 'x' }, research: null,
    } }));
    await go(page, 'fx-done', 'blog');
    await expect(page.getByTestId('output-blog-link')).toHaveText('Notion: t');
    await expect(page.getByTestId('output-blog-link')).not.toHaveAttribute('href');
    await nav(page, 'summary').click();
    await expect(page.getByTestId('summary-link-notion')).not.toHaveAttribute('href');
  });

  test('[DT-D3] an artifact that cannot load says so and offers no editing', async ({ page, errors }) => {
    errors.allow(/status of 500/);
    const list = await (await page.request.get('/api/runs/fx-done/artifacts')).json();
    const blog = list.artifacts.find((a) => a.name === 'blog_post.md');
    await page.route(`**/api/runs/fx-done/artifacts/${blog.id}`, (route) => route.fulfill({ status: 500, body: 'x' }));
    await go(page, 'fx-done', 'blog');
    await expect(page.getByTestId('artifact-blog_post.md')).toHaveAttribute('data-state', 'error');
    await expect(page.getByTestId('artifact-error-blog_post.md')).toHaveText('Could not load this file: HTTP 500');
    await expect(editor(page, 'blog_post.md')).toBeDisabled();
    await expect(saveBtn(page, 'blog_post.md')).toBeDisabled();
  });

  test('[DT-D4] a file over the read cap reads only: saving would truncate it', async ({ page }) => {
    await page.route('**/api/runs/fx-done/artifacts', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      Object.assign(body.artifacts.find((a) => a.name === 'telegram.txt'), { bytes: 3 * 1024 * 1024, node: null });
      await route.fulfill({ response: res, json: body });
    });
    await go(page, 'fx-done', 'files');
    await expect(page.getByTestId('file-group-other')).toContainText('telegram.txt');
    await page.getByTestId('file-telegram.txt').click();
    await expect(page.getByTestId('artifact-telegram.txt')).toHaveAttribute('data-state', 'ready');
    await expect(editor(page, 'telegram.txt')).toHaveCount(0);
    await nav(page, 'summary').click();
    await nav(page, 'files').click();
    await expect(page.getByTestId('artifact-telegram.txt')).toHaveAttribute('data-state', 'ready');
  });

  test('[DT-D4] edit the blog in place beside its preview, save writes the file', async ({ page }) => {
    await go(page, 'fx-done', 'blog');
    const edit = editor(page, 'blog_post.md');
    const preview = page.getByTestId('artifact-preview-blog_post.md');
    await expect(edit).toHaveValue(/./);
    await expect(saveBtn(page, 'blog_post.md')).toBeDisabled();
    const [a, b] = [await edit.boundingBox(), await preview.boundingBox()];
    expect(b.x).toBeGreaterThanOrEqual(a.x + a.width - 1);
    expect(Math.abs(a.y - b.y)).toBeLessThan(2);

    await edit.fill('# A new <b>head</b>');
    await expect(preview.getByRole('heading', { level: 1 })).toHaveText('A new <b>head</b>');
    await expect(dirty(page, 'blog')).toBeVisible();
    await expect(dirty(page, 'files')).toBeVisible();
    await saveBtn(page, 'blog_post.md').click();
    await expect(page.getByTestId('artifact-notice-blog_post.md')).toHaveText('Saved');
    await expect(dirty(page, 'blog')).toBeHidden();
    expect(fs.readFileSync(langgraph('fx-done', 'painforwisdom-writer', 'blog_post.md'), 'utf8')).toBe('# A new <b>head</b>');
    await edit.fill('# Again');
    await expect(page.getByTestId('artifact-notice-blog_post.md')).toBeEmpty();
    await edit.fill('# A new <b>head</b>');
    await expect(dirty(page, 'blog')).toBeHidden();
  });

  test('[DT-D4] KB and any text file edit the same way; one editor per file across sections', async ({ page }) => {
    await go(page, 'fx-done', 'kb');
    await editor(page, 'curator_summary.md').fill('edited summary');
    await expect(dirty(page, 'kb')).toBeVisible();
    await nav(page, 'files').click();
    await expect(page.getByTestId('file-curator_summary.md')).toHaveAttribute('data-dirty', 'true');
    await page.getByTestId('file-curator_summary.md').click();
    await expect(editor(page, 'curator_summary.md')).toHaveValue('edited summary');
    await saveBtn(page, 'curator_summary.md').click();
    await expect(page.getByTestId('artifact-notice-curator_summary.md')).toHaveText('Saved');
    expect(fs.readFileSync(langgraph('fx-done', 'kb-curator', 'curator_summary.md'), 'utf8')).toBe('edited summary');

    await page.getByTestId('file-research_report.csv').click();
    await editor(page, 'research_report.csv').fill('');
    await expect(page.getByTestId('artifact-preview-research_report.csv')).toBeEmpty();
    await editor(page, 'research_report.csv').fill('a,b\n1,2\n');
    await expect(page.getByTestId('artifact-preview-research_report.csv').locator('td').first()).toHaveText('1');
    await saveBtn(page, 'research_report.csv').click();
    await expect(page.getByTestId('artifact-notice-research_report.csv')).toHaveText('Saved');
  });

  test('[DT-D4] polling never overwrites an edit; switching section keeps it; a clean editor follows the disk', async ({ page }) => {
    const blog = langgraph('fx-done', 'painforwisdom-writer', 'blog_post.md');
    await go(page, 'fx-done', 'blog');
    const edit = editor(page, 'blog_post.md');
    await expect(edit).toHaveValue(/./);
    fs.writeFileSync(blog, '# From disk');
    const again = async () => {
      await page.getByTestId('view-tab-graph').click();
      const listed = page.waitForResponse((r) => r.url().endsWith('/artifacts'));
      await page.getByTestId('view-tab-output').click();
      await listed;
    };
    await again();
    await expect(edit).toHaveValue('# From disk');

    await edit.fill('# Mine');
    fs.writeFileSync(blog, '# From disk, longer');
    await again();
    await expect(edit).toHaveValue('# Mine');
    await page.evaluate(() => document.activeElement.blur());
    await page.keyboard.press('j');
    await page.keyboard.press('k');
    await expect(edit).toHaveValue('# Mine');
    await nav(page, 'summary').click();
    await expect(dirty(page, 'blog')).toBeVisible();
    await nav(page, 'blog').click();
    await expect(edit).toHaveValue('# Mine');
  });

  test('[DT-D4] text typed while the disk version is on its way wins', async ({ page }) => {
    const list = await (await page.request.get('/api/runs/fx-done/artifacts')).json();
    const { id } = list.artifacts.find((a) => a.name === 'blog_post.md');
    await go(page, 'fx-done', 'blog');
    const edit = editor(page, 'blog_post.md');
    await expect(edit).toHaveValue(/./);
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route(`**/api/runs/fx-done/artifacts/${id}`, async (route) => {
      await held;
      await route.continue();
    });
    fs.writeFileSync(langgraph('fx-done', 'painforwisdom-writer', 'blog_post.md'), '# From disk');
    await page.getByTestId('view-tab-graph').click();
    const asked = page.waitForRequest((r) => r.url().endsWith(id));
    await page.getByTestId('view-tab-output').click();
    await asked;
    await edit.fill('# Typed first');
    const answered = page.waitForResponse((r) => r.url().endsWith(id));
    release();
    await answered;
    await expect(saveBtn(page, 'blog_post.md')).toBeEnabled();
    await expect(edit).toHaveValue('# Typed first');
    await expect(dirty(page, 'blog')).toBeVisible();
  });

  test('[DT-D4] a failed save keeps the text and shows why; 409 while the run is active', async ({ page, errors }) => {
    errors.allow(/status of 409/);
    errors.allow(/ERR_FAILED/);
    errors.allow(/Failed to load resource/);
    await go(page, 'fx-done', 'blog');
    const edit = editor(page, 'blog_post.md');
    await expect(edit).toHaveValue(/./);
    await edit.fill('# Not lost');
    setRecord('fx-done', { status: 'running', pid: process.pid });
    await saveBtn(page, 'blog_post.md').click();
    await expect(page.getByTestId('artifact-error-blog_post.md')).toHaveText('run active');
    await expect(edit).toHaveValue('# Not lost');
    await expect(dirty(page, 'blog')).toBeVisible();
    setRecord('fx-done', { status: 'done', pid: 0 });

    await page.route('**/api/runs/fx-done/artifacts/*', (route) => (route.request().method() === 'PUT' ? route.abort() : route.continue()));
    await saveBtn(page, 'blog_post.md').click();
    await expect(page.getByTestId('artifact-error-blog_post.md')).not.toBeEmpty();
    await expect(page.getByTestId('artifact-error-blog_post.md')).not.toHaveText('run active');
    await expect(edit).toHaveValue('# Not lost');
    await page.unroute('**/api/runs/fx-done/artifacts/*');
    await saveBtn(page, 'blog_post.md').click();
    await expect(page.getByTestId('artifact-notice-blog_post.md')).toHaveText('Saved');
    await expect(page.getByTestId('artifact-error-blog_post.md')).toBeEmpty();
  });

  test('[DT-D4] switching run with unsaved edits warns inline; back restores them, discard drops them', async ({ page }) => {
    let dialogs = 0;
    page.on('dialog', (d) => { dialogs += 1; return d.dismiss(); });
    await go(page, 'fx-done', 'blog');
    const edit = editor(page, 'blog_post.md');
    await expect(edit).toHaveValue(/./);
    await edit.fill('# Unsaved');

    await page.getByTestId('run-fx-published').click();
    const warn = page.getByTestId('output-unsaved-fx-done');
    await expect(warn).toBeVisible();
    await expect(warn).toContainText('blog');
    await page.getByTestId('output-unsaved-back-fx-done').click();
    await expect(edit).toHaveValue('# Unsaved');
    await expect(warn).toHaveCount(0);

    await page.getByTestId('run-fx-published').click();
    await expect(warn).toBeVisible();
    await page.getByTestId('output-unsaved-discard-fx-done').click();
    await expect(warn).toHaveCount(0);
    await page.getByTestId('run-fx-done').click();
    await expect(section(page, 'blog')).toBeVisible();
    await expect(editor(page, 'blog_post.md')).not.toHaveValue('# Unsaved');
    await expect(editor(page, 'blog_post.md')).toHaveValue(/./);
    expect(dialogs).toBe(0);
  });

  test('[DT-P3] unsaved package edits warn on a run switch too', async ({ page }) => {
    await go(page, 'fx-done', 'wordpress');
    await page.getByTestId('field-wordpress-title').fill('Unsaved title');
    await expect(dirty(page, 'wordpress')).toBeVisible();
    await page.getByTestId('run-fx-published').click();
    await expect(page.getByTestId('output-unsaved-fx-done')).toContainText('wordpress');
    await page.getByTestId('output-unsaved-back-fx-done').click();
    await expect(page.getByTestId('field-wordpress-title')).toHaveValue('Unsaved title');
  });

  test('[DT-R1] an active run shows what exists so far with editing disabled and the reason', async ({ page }) => {
    freshServerState({ live: true });
    fs.mkdirSync(langgraph('fx-running', 'painforwisdom-writer'), { recursive: true });
    fs.writeFileSync(langgraph('fx-running', 'painforwisdom-writer', 'blog_post.md'), '# So far');
    writePkg('fx-running', 'wordpress-draft', readPkg('fx-done', 'wordpress-draft'));
    await deep(page, 'fx-running');
    await expect(page.getByTestId('output-active-reason')).toContainText('running');
    await nav(page, 'blog').click();
    await expect(editor(page, 'blog_post.md')).toHaveValue('# So far');
    await expect(editor(page, 'blog_post.md')).toHaveAttribute('readonly');
    await expect(saveBtn(page, 'blog_post.md')).toBeDisabled();
    await nav(page, 'wordpress').click();
    await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
    await expect(page.getByTestId('save-wordpress')).toBeDisabled();
    await expect(page.getByTestId('publish-wordpress')).toBeDisabled();
    await nav(page, 'youtube').click();
    await expect(page.getByTestId('prepare-reason-youtube')).toHaveText('the run is still active');
  });

  test('[DT-R1] a live fake run fills its sections and unlocks when it ends', async ({ page, request }) => {
    test.setTimeout(60_000);
    const res = await request.post('/api/runs', { data: { input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: 300 } } });
    const id = (await res.json()).run_id;
    await deep(page, id);
    await nav(page, 'wordpress').click();
    await expect(page.getByTestId('field-wordpress-title')).toHaveValue('Fake post', { timeout: 30_000 });
    await expect(page.getByTestId(`run-${id}`)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
    await expect(page.getByTestId('output-active-reason')).toHaveCount(0);
    await expect(page.getByTestId('field-wordpress-title')).toBeEnabled();
    await page.getByTestId('field-wordpress-title').fill('Kept after the run');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('published-wordpress')).toHaveAttribute('href', `https://example.invalid/wordpress/${id}`, { timeout: 15_000 });
  });
});

test.describe('research', () => {
  test('[DT-D5] http opens a new tab, a local book the console route, and nothing else ever links', async ({ page, request }) => {
    await go(page, 'fx-unpackaged', 'research');
    const http = page.getByTestId('research-link-1');
    await expect(http).toHaveAttribute('href', 'https://example.org/endure');
    await expect(http).toHaveAttribute('target', '_blank');
    await expect(http).toHaveAttribute('rel', 'noopener');
    const local = page.getByTestId('research-link-2');
    await expect(local).toHaveAttribute('href', '/api/runs/fx-unpackaged/research/2/file');
    await expect(local).toHaveAttribute('rel', 'noopener');
    expect((await request.get('/api/runs/fx-unpackaged/research/2/file')).status()).toBe(200);
    for (const n of [3, 4, 5, 6]) {
      await expect(page.getByTestId(`research-nolink-${n}`)).toBeVisible();
      await expect(page.getByTestId(`research-link-${n}`)).toHaveCount(0);
    }
    await expect(section(page, 'research').locator('[href^="javascript"], [href^="file"]')).toHaveCount(0);
  });

  test('[DT-D5] a link the server calls http but is not http(s) never becomes an href', async ({ page }) => {
    await page.route('**/api/runs/fx-done/research', (route) => route.fulfill({ json: { items: [
      { n: 1, title: 'bad', type: 'Book', author: '', location: '', category: '', angle: '', relevance: '', paywall: '', theme: '', reachable: '', reason: '', link: { kind: 'http', href: 'javascript:alert(1)' } },
    ] } }));
    await go(page, 'fx-done', 'research');
    await expect(page.getByTestId('research-nolink-1')).toBeVisible();
  });

  test('[DT-D5] filter by type and text, every row expands and stays open across filtering', async ({ page }) => {
    await go(page, 'fx-unpackaged', 'research');
    const rows = section(page, 'research').locator('[data-testid^="research-row-"]');
    await expect(rows).toHaveCount(6);
    await expect(page.getByTestId('research-filter-type').locator('option')).toHaveText(['All types', 'Article', 'Book', 'Paper']);

    await page.getByTestId('research-toggle-1').click();
    const detail = page.getByTestId('research-detail-1');
    await expect(detail).toContainText('Chapter 4');
    await expect(detail).toContainText('limits');
    await expect(detail).toContainText('Why the mind quits first.');
    await expect(detail).toContainText('Alex Hutchinson');
    await expect(detail).toContainText('true');
    await expect(page.getByTestId('research-toggle-1')).toHaveAttribute('aria-expanded', 'true');
    await page.getByTestId('research-toggle-3').click();
    await expect(page.getByTestId('research-detail-3')).toContainText('no-http-url');

    await page.getByTestId('research-filter-type').selectOption('Paper');
    await expect(rows).toHaveCount(1);
    await page.getByTestId('research-filter-type').selectOption('');
    await page.getByTestId('research-filter-text').fill('chapter 4');
    await expect(rows).toHaveCount(1);
    await expect(detail).toBeVisible();
    await page.getByTestId('research-filter-text').fill('no such thing');
    await expect(page.getByTestId('research-nomatch')).toBeVisible();
    await page.getByTestId('research-filter-text').fill('');
    await expect(rows).toHaveCount(6);

    await page.getByTestId('research-toggle-1').click();
    await expect(detail).toHaveCount(0);
    await page.keyboard.press('j');
    await expect(section(page, 'audit')).toBeVisible();
    await nav(page, 'research').click();
    await expect(page.getByTestId('research-detail-3')).toBeVisible();
  });

  test('[DT-D5] a run without research says so', async ({ page }) => {
    await go(page, 'fx-modeoff', 'research');
    await expect(page.getByTestId('research-empty')).toBeVisible();
  });
});

test.describe('publish', () => {
  test('[DT-P1] mode off: the reason, no action', async ({ page }) => {
    await go(page, 'fx-modeoff', 'youtube');
    await expect(page.getByTestId('prepare-reason-youtube')).toHaveText('youtube publishing is off for profile prod');
    await expect(page.getByTestId('prepare-youtube')).toHaveCount(0);
    await expect(page.getByTestId('publish-youtube')).toHaveCount(0);
    await nav(page, 'wordpress').click();
    await expect(page.getByTestId('prepare-reason-wordpress')).toHaveText('wordpress publishing is off for profile prod');
    await expect(page.getByTestId('save-wordpress')).toHaveCount(0);
  });

  test('[DT-P2] an older run prepares both packages, then edits and plays them', async ({ page, request }) => {
    await go(page, 'fx-unpackaged', 'youtube');
    await page.getByTestId('prepare-youtube').click();
    await expect(page.getByTestId('field-youtube-title')).toHaveValue('When the road stops arguing');
    await expect(page.getByTestId('prepare-youtube')).toHaveCount(0);
    await expect(page.getByTestId('status-youtube')).toHaveText('pending');
    await expect(chip(page, 'youtube')).toHaveText('pending');
    const video = page.getByTestId('youtube-video');
    await expect(video).toHaveAttribute('src', '/api/runs/fx-unpackaged/publish/youtube/asset/video');
    await expect(video).toHaveAttribute('controls');
    await expect(page.getByTestId('readonly-youtube')).toContainText('duration_s–');
    expect((await request.get('/api/runs/fx-unpackaged/publish/youtube/asset/video')).status()).toBe(200);

    await nav(page, 'wordpress').click();
    await page.getByTestId('prepare-wordpress').click();
    await expect(page.getByTestId('field-wordpress-title')).toHaveValue('The road stopped arguing');
    const image = page.getByTestId('wordpress-featured_image');
    await expect(image).toHaveJSProperty('complete', true);
    expect(await image.evaluate((n) => n.naturalWidth)).toBeGreaterThan(0);
  });

  test('[DT-P7] cannot prepare: the reason; a prepare the server refuses shows why', async ({ page, errors }) => {
    errors.allow(/status of 422/);
    await go(page, 'fx-done', 'youtube');
    await expect(page.getByTestId('prepare-reason-youtube')).toHaveText('this run has no video');
    await expect(page.getByTestId('prepare-youtube')).toHaveCount(0);

    await page.route('**/api/runs/fx-unpackaged/publish/youtube/prepare', (route) => route.fulfill({ status: 422, json: { detail: 'this run has no video' } }));
    await go(page, 'fx-unpackaged', 'youtube');
    await page.getByTestId('prepare-youtube').click();
    await expect(page.getByTestId('publish-error-youtube')).toHaveText('this run has no video');
    await expect(page.getByTestId('prepare-youtube')).toBeEnabled();
  });

  test('[DT-P3] edit and save without publishing', async ({ page }) => {
    await go(page, 'fx-published', 'youtube');
    await expect(page.getByTestId('save-youtube')).toBeDisabled();
    await page.getByTestId('field-youtube-title').fill('A saved title');
    await page.getByTestId('field-youtube-tags').fill('fresh');
    await page.getByTestId('field-youtube-tags').press('Enter');
    await expect(dirty(page, 'youtube')).toBeVisible();
    await page.getByTestId('save-youtube').click();
    await expect(page.getByTestId('publish-notice-youtube')).toHaveText('Saved');
    await expect(dirty(page, 'youtube')).toBeHidden();
    await expect(page.getByTestId('save-youtube')).toBeDisabled();
    const pkg = readPkg('fx-published', 'youtube-upload');
    expect(pkg.status).toBe('pending');
    expect(pkg.fields.title).toBe('A saved title');
    expect(pkg.fields.tags).toEqual(['painforwisdom', 'ultra running', 'fresh']);
    await expect(page.getByTestId('status-youtube')).toHaveText('pending');
    await expect(page.getByTestId('field-youtube-title')).toHaveValue('A saved title');
    await page.getByTestId('field-youtube-title').fill('Next edit');
    await expect(page.getByTestId('publish-notice-youtube')).toBeEmpty();
  });

  test('[DT-P3] the editor renders every C9 type from field_specs, side by side', async ({ page }) => {
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('field-wordpress-title')).toHaveValue('The hill does not care');
    await expect(page.getByTestId('field-wordpress-body').evaluate((n) => n.tagName)).resolves.toBe('TEXTAREA');
    await expect(page.getByTestId('field-wordpress-excerpt').evaluate((n) => n.tagName)).resolves.toBe('TEXTAREA');
    await expect(page.getByTestId('field-wordpress-status')).toHaveValue('draft');
    await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveAttribute('type', 'url');
    await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['painforwisdom', 'discipline']);
    await expect(page.getByTestId('readonly-wordpress')).toContainText('fx-blog-1');
    await expect(page.getByTestId('asset-wordpress-featured_image')).toContainText('featured.png');
    await expect(page.getByTestId('mode-wordpress')).toHaveText('manual');

    const preview = page.getByTestId('preview-wordpress-body');
    await expect(preview.locator('strong')).toHaveText('Keep walking.');
    const [a, b] = [await page.getByTestId('field-wordpress-body').boundingBox(), await preview.boundingBox()];
    expect(b.x).toBeGreaterThanOrEqual(a.x + a.width - 1);
    await page.getByTestId('field-wordpress-body').fill('# New <b>head</b>');
    await expect(preview.getByRole('heading', { level: 1 })).toHaveText('New <b>head</b>');

    await page.getByTestId('field-wordpress-tags').fill('one, two,,');
    await page.getByTestId('field-wordpress-tags').press('Enter');
    await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['painforwisdom', 'discipline', 'one', 'two']);
    await page.getByTestId('chip-remove-wordpress-tags').first().click();
    await expect(page.getByTestId('chip-wordpress-tags')).toHaveText(['discipline', 'one', 'two']);
    await expect(page.getByTestId('asset-missing-wordpress-featured_image')).toBeVisible();
  });

  test('[DT-P3] a field missing from the package renders empty and is not dirty', async ({ page }) => {
    const pkg = readPkg('fx-done', 'wordpress-draft');
    delete pkg.fields.excerpt;
    delete pkg.fields.tags;
    pkg.readonly = {};
    await mockPublish(page, 'fx-done', { wordpress: pkg });
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('field-wordpress-excerpt')).toHaveValue('');
    await expect(page.getByTestId('chip-wordpress-tags')).toHaveCount(0);
    await expect(dirty(page, 'wordpress')).toBeHidden();
  });

  test('[DT-P3] polling and run updates do not overwrite unsaved package edits; a clean form follows the server', async ({ page }) => {
    await go(page, 'fx-published', 'youtube');
    const title = page.getByTestId('field-youtube-title');
    const pkg = readPkg('fx-published', 'youtube-upload');
    writePkg('fx-published', 'youtube-upload', { ...pkg, fields: { ...pkg.fields, title: 'From elsewhere' } });
    const refresh = async () => {
      await page.getByTestId('view-tab-graph').click();
      const got = page.waitForResponse((r) => r.url().endsWith('/publish'));
      await page.getByTestId('view-tab-output').click();
      await got;
    };
    await refresh();
    await expect(title).toHaveValue('From elsewhere');
    await title.fill('My draft');
    writePkg('fx-published', 'youtube-upload', { ...pkg, fields: { ...pkg.fields, title: 'Elsewhere again' } });
    await refresh();
    await expect(title).toHaveValue('My draft');
    await expect(dirty(page, 'youtube')).toBeVisible();
    await nav(page, 'summary').click();
    await nav(page, 'youtube').click();
    await expect(title).toHaveValue('My draft');
  });

  test('[DT-P4] publish saves the current fields; publishing; published with link, read-only', async ({ page }) => {
    await go(page, 'fx-done', 'wordpress');
    await page.getByTestId('field-wordpress-title').fill('A better title');
    await page.getByTestId('field-wordpress-tags').fill('fresh');
    await page.getByTestId('publish-wordpress').click();
    const link = page.getByTestId('published-wordpress');
    await expect(link).toHaveAttribute('href', 'https://example.invalid/wordpress/fx-done', { timeout: 15_000 });
    await expect(link).toHaveAttribute('rel', 'noopener');
    await expect(page.getByTestId('status-wordpress')).toHaveText('published');
    await expect(chip(page, 'wordpress')).toHaveText('published');
    await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
    await expect(page.getByTestId('publish-wordpress')).toBeHidden();
    await expect(page.getByTestId('save-wordpress')).toBeHidden();
    await expect(dirty(page, 'wordpress')).toBeHidden();
    const written = readPkg('fx-done', 'wordpress-draft');
    expect(written.fields.title).toBe('A better title');
    expect(written.fields.tags).toEqual(['painforwisdom', 'discipline', 'fresh']);
  });

  test('[DT-P4] polls every 2 s only while publishing', async ({ page }) => {
    const t0 = Date.parse('2026-11-03T10:00:00Z');
    await page.clock.install({ time: t0 });
    const pkg = readPkg('fx-done', 'wordpress-draft');
    let state = { ...pkg, status: 'publishing' };
    let gets = 0;
    await page.route('**/api/runs/fx-done/publish', (route) => {
      gets += 1;
      return route.fulfill({ json: { targets: { wordpress: state }, prepare: {} } });
    });
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('status-wordpress')).toHaveText('publishing');
    await expect(page.getByTestId('publish-wordpress')).toBeDisabled();
    await expect(page.getByTestId('save-wordpress')).toBeDisabled();
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

  test('[DT-P4] polling stops while the view is hidden and resumes when it returns', async ({ page }) => {
    await page.clock.install();
    const pkg = readPkg('fx-done', 'wordpress-draft');
    let gets = 0;
    await page.route('**/api/runs/fx-done/publish', (route) => {
      gets += 1;
      return route.fulfill({ json: { targets: { wordpress: { ...pkg, status: 'publishing' } }, prepare: {} } });
    });
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('status-wordpress')).toHaveText('publishing');
    await page.getByTestId('view-tab-graph').click();
    await expect(output(page)).toBeHidden();
    const hidden = gets;
    await page.clock.runFor(5000);
    await page.evaluate(() => fetch('/health'));
    expect(gets).toBe(hidden);
    await page.getByTestId('view-tab-output').click();
    await expect.poll(() => gets).toBeGreaterThan(hidden);
  });

  test('[DT-P4] a failed poll keeps polling', async ({ page, errors }) => {
    errors.allow(/status of 503/);
    await page.clock.install();
    const pkg = readPkg('fx-done', 'wordpress-draft');
    let fail = false;
    await page.route('**/api/runs/fx-done/publish', (route) => (fail
      ? route.fulfill({ status: 503, body: 'nope' })
      : route.fulfill({ json: { targets: { wordpress: { ...pkg, status: 'publishing' } }, prepare: {} } })));
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('status-wordpress')).toHaveText('publishing');
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

  test('[DT-P5] publish failed: the error shows, edit, publish again', async ({ page }) => {
    await go(page, 'fx-done', 'wordpress');
    await page.getByTestId('field-wordpress-title').fill('FAIL');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('fake publish failure', { timeout: 15_000 });
    await expect(page.getByTestId('status-wordpress')).toHaveText('failed');
    await expect(chip(page, 'wordpress')).toHaveText('failed');
    await expect(page.getByTestId('field-wordpress-title')).toBeEnabled();
    await page.getByTestId('field-wordpress-title').fill('Fixed');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('published-wordpress')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('publish-error-wordpress')).toBeEmpty();
  });

  test('[DT-P6] published: read-only with link; another target stays editable from its own specs', async ({ page }) => {
    await go(page, 'fx-published', 'wordpress');
    await expect(page.getByTestId('published-wordpress')).toHaveAttribute('href', 'https://example.invalid/wordpress/fx-published');
    await expect(page.getByTestId('field-wordpress-title')).toBeDisabled();
    await expect(page.getByTestId('publish-wordpress')).toBeHidden();
    await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveValue('');
    await nav(page, 'youtube').click();
    await expect(page.getByTestId('field-youtube-privacy_status')).toHaveValue('private');
    await expect(page.getByTestId('asset-youtube-video')).toContainText('video_2026-10-30.mp4');
    await expect(page.getByTestId('readonly-youtube')).toContainText('42');
    await expect(page.getByTestId('field-youtube-youtube_url')).toHaveCount(0);
  });

  test('[DT-P6] a package that becomes published replaces an unsaved draft: it cannot be saved any more', async ({ page }) => {
    await go(page, 'fx-published', 'youtube');
    await page.getByTestId('field-youtube-title').fill('Stale draft');
    const pkg = readPkg('fx-published', 'youtube-upload');
    writePkg('fx-published', 'youtube-upload', { ...pkg, status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } });
    await page.getByTestId('view-tab-graph').click();
    await page.getByTestId('view-tab-output').click();
    await expect(page.getByTestId('published-youtube')).toBeVisible();
    await expect(page.getByTestId('field-youtube-title')).toHaveValue('The hill does not care');
    await expect(dirty(page, 'youtube')).toBeHidden();
  });

  test('[DT-P8] client checks mirror the server on publish: required and max, per field, no POST', async ({ page }) => {
    let posts = 0;
    page.on('request', (r) => { if (r.method() === 'POST') posts += 1; });
    await go(page, 'fx-done', 'wordpress');
    await page.getByTestId('field-wordpress-title').fill('   ');
    await page.getByTestId('field-wordpress-excerpt').fill('x'.repeat(301));
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('error-wordpress-title')).toHaveText('title: required');
    await expect(page.getByTestId('error-wordpress-excerpt')).toHaveText('excerpt: over max 300');
    expect(posts).toBe(0);
  });

  test('[DT-P8] a server 400 lands per field on publish and on save; an unknown field in the form error', async ({ page, errors }) => {
    errors.allow(/status of 400/);
    await go(page, 'fx-done', 'wordpress');
    await page.getByTestId('field-wordpress-youtube_url').fill('not-a-url');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('error-wordpress-youtube_url')).toHaveText('youtube_url: not an http(s) url');
    await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
    await page.getByTestId('field-wordpress-excerpt').fill('x'.repeat(301));
    await page.getByTestId('save-wordpress').click();
    await expect(page.getByTestId('error-wordpress-excerpt')).toHaveText('excerpt: over max 300');
    await expect(page.getByTestId('field-wordpress-excerpt')).toHaveValue('x'.repeat(301));
    await page.getByTestId('field-wordpress-excerpt').fill('short');

    await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 400, json: { detail: ['zzz: unknown field'] } }));
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('zzz: unknown field');
    await expect(page.getByTestId('error-wordpress-youtube_url')).toBeEmpty();
  });

  test('[DT-P9] another publish holds the lock: the detail shows on save and publish', async ({ page, errors }) => {
    errors.allow(/status of 409/);
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
    fs.writeFileSync(langgraph('fx-done', 'wordpress-draft', 'publish.lock'), '');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('publishing');
    await expect(page.getByTestId('publish-wordpress')).toBeEnabled();
    await page.getByTestId('field-wordpress-title').fill('Blocked');
    await page.getByTestId('save-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('publishing');
    await expect(page.getByTestId('field-wordpress-title')).toHaveValue('Blocked');
    await expect(dirty(page, 'wordpress')).toBeVisible();
  });

  test('[DT-P8] a 500 with no JSON body reports the HTTP status', async ({ page, errors }) => {
    errors.allow(/status of 500/);
    await go(page, 'fx-done', 'wordpress');
    await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 500, body: 'boom' }));
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('HTTP 500');
  });

  test('[DT-P10] auto mode: published or pending offers no action, failed allows publishing again', async ({ page }) => {
    const base = { ...readPkg('fx-published', 'youtube-upload'), mode: 'auto' };
    let state = { ...base, status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
    await page.route('**/api/runs/fx-published/publish', (route) => route.fulfill({ json: { targets: { youtube: state }, prepare: {} } }));
    await go(page, 'fx-published', 'youtube');
    await expect(page.getByTestId('mode-youtube')).toHaveText('auto');
    await expect(page.getByTestId('published-youtube')).toBeVisible();
    await expect(page.getByTestId('publish-youtube')).toBeHidden();
    const toggle = async () => {
      await page.getByTestId('view-tab-graph').click();
      await page.getByTestId('view-tab-output').click();
    };
    state = { ...base, status: 'pending' };
    await toggle();
    await expect(page.getByTestId('status-youtube')).toHaveText('pending');
    await expect(page.getByTestId('publish-youtube')).toBeHidden();
    await expect(page.getByTestId('save-youtube')).toBeHidden();
    await expect(page.getByTestId('field-youtube-title')).toBeDisabled();
    await nav(page, 'summary').click();
    await expect(page.getByTestId('summary-next-none')).toBeVisible();
    await nav(page, 'youtube').click();
    state = { ...base, status: 'failed', error: 'quota exceeded' };
    await toggle();
    await expect(page.getByTestId('publish-error-youtube')).toHaveText('quota exceeded');
    await expect(page.getByTestId('publish-youtube')).toBeEnabled();
    await expect(page.getByTestId('field-youtube-title')).toBeEnabled();
  });

  test('[DT-P11] a publisher that died reads as failed and can publish again', async ({ page }) => {
    const pkg = readPkg('fx-done', 'wordpress-draft');
    writePkg('fx-done', 'wordpress-draft', { ...pkg, status: 'publishing' });
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('status-wordpress')).toHaveText('failed');
    await expect(page.getByTestId('publish-error-wordpress')).toHaveText('publisher died before recording a result');
    await page.getByTestId('publish-wordpress').click();
    await expect(page.getByTestId('published-wordpress')).toBeVisible({ timeout: 15_000 });
  });

  test('[DT-P4] WordPress youtube_url pre-fills from a published YouTube, stays editable, never overwrites', async ({ page }) => {
    const wordpress = readPkg('fx-done', 'wordpress-draft');
    const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
    await mockPublish(page, 'fx-done', { wordpress, youtube });
    await page.route('**/api/runs/fx-done/publish/wordpress', (route) => route.fulfill({ status: 202, json: wordpress }));
    await go(page, 'fx-done', 'wordpress');
    const url = page.getByTestId('field-wordpress-youtube_url');
    await expect(url).toHaveValue('https://youtu.be/abc');
    await expect(dirty(page, 'wordpress')).toBeHidden();
    await nav(page, 'youtube').click();
    await expect(page.getByTestId('published-youtube')).toBeVisible();
    await nav(page, 'wordpress').click();

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

  test('[DT-P6] a published WordPress package is never pre-filled', async ({ page }) => {
    const wordpress = { ...readPkg('fx-done', 'wordpress-draft'), status: 'published', result: { id: 'w', url: 'https://example.invalid/w' } };
    const youtube = { ...readPkg('fx-published', 'youtube-upload'), status: 'published', result: { id: 'y', url: 'https://youtu.be/abc' } };
    await mockPublish(page, 'fx-done', { wordpress, youtube });
    await go(page, 'fx-done', 'wordpress');
    await expect(page.getByTestId('published-wordpress')).toBeVisible();
    await expect(page.getByTestId('field-wordpress-youtube_url')).toHaveValue('');
  });
});

// PR screenshots, opt-in like screenshots.spec.js: PFW_SHOTS=<dir> npx playwright test tests/operability.spec.js
test.describe('output shots', () => {
  const DIR = process.env.PFW_SHOTS;
  test.skip(!DIR, 'screenshots only when PFW_SHOTS names an output dir');
  test.beforeEach(async ({ page }) => page.emulateMedia({ reducedMotion: 'reduce' }));

  for (const width of [1280, 3440]) {
    test(`shot sections at ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 1280 ? 800 : 1300 });
      const shoot = (name) => page.screenshot({ path: path.join(DIR, `output-${name}-${width}.png`) });
      await deep(page, 'fx-unpackaged');
      await shoot('summary');
      await nav(page, 'blog').click();
      await expect(page.getByTestId('artifact-preview-blog_post.md').locator('.md')).toBeVisible();
      await editor(page, 'blog_post.md').fill(`${await editor(page, 'blog_post.md').inputValue()}\n\nAn edit in place.\n`);
      await shoot('blog-editing');
      await nav(page, 'youtube').click();
      await page.getByTestId('prepare-youtube').click();
      await expect(page.getByTestId('youtube-video')).toBeVisible();
      await shoot('youtube-publish');
      await nav(page, 'wordpress').click();
      await page.getByTestId('prepare-wordpress').click();
      await expect(page.getByTestId('wordpress-featured_image')).toBeVisible();
      await shoot('wordpress-publish');
      await nav(page, 'research').click();
      await page.getByTestId('research-toggle-1').click();
      await expect(page.getByTestId('research-detail-1')).toBeVisible();
      await shoot('research');
    });
  }
});
