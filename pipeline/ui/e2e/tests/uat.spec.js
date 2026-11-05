// UAT: one flow per branch family of the decision tree (spec 2026-11-05-console-end-to-end sections 2 and 5).
// Every step is what a user does in the browser. The one setup: the fake runner's scenario is added to the POST the
// Start button sends, since a real run has no scenario. Nothing here calls the API to act.
import { test, expect, freshServerState } from '../fixtures.js';

const TXT = 'transcript_2026-10-30.txt';
const VIDEO = 'PXL_20261030_070000000.mp4';
const STEP_MS = 20;
const LIVE = { timeout: 30_000 }; // a fake process has to start and walk the graph
const PUBLISH = { timeout: 15_000 }; // the Output view polls a publish every 2 s

const id = (page, testid) => page.getByTestId(testid);
const statusOf = (page, run) => id(page, `run-${run}`);

test.describe.configure({ timeout: 90_000 });
test.beforeEach(({ page }) => {
  freshServerState();
  page.on('dialog', (d) => {
    d.dismiss();
    throw new Error(`browser dialog: ${d.message()}`);
  });
});

/** Open the popup and press the input's tile. Returns the new run's id. */
async function start(page, input, fake) {
  await page.route('**/api/runs', (route) => (route.request().method() === 'POST'
    ? route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), fake: { step_ms: STEP_MS, ...fake } }) })
    : route.continue()));
  await page.goto('/#/runs/fx-done');
  await id(page, 'launch-button').click();
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/runs'));
  await id(page, `input-${input}`).click();
  const res = await posted;
  expect(res.status()).toBe(201);
  const { run_id: run } = await res.json();
  await expect(id(page, 'launch-modal')).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`#/runs/${run}$`));
  return run;
}

const finished = (page, run) => expect(statusOf(page, run)).toHaveAttribute('data-status', 'done', LIVE);

/** The error is over: only the finished run's own news is left, the package it wrote. */
async function settled(page) {
  await expect(id(page, 'action-banner')).toHaveAttribute('data-kind', 'attention', LIVE);
  await expect(id(page, 'failure-error')).toHaveCount(0);
}

async function openOutput(page, section) {
  await id(page, 'view-tab-output').click();
  await id(page, `output-nav-${section}`).click();
  await expect(id(page, `output-section-${section}`)).toBeVisible();
}

async function publishes(page, run, target) {
  await id(page, `publish-${target}`).click();
  const link = id(page, `published-${target}`);
  await expect(link).toHaveAttribute('href', `https://example.invalid/${target}/${run}`, PUBLISH);
  await expect(id(page, `status-${target}`)).toHaveText('published');
  await expect(id(page, `publish-${target}`)).toBeHidden();
  await expect(id(page, `save-${target}`)).toBeHidden();
}

test.describe('approval to publish', () => {
  test('[DT-S2] [DT-R1] [DT-A3] [DT-A1] [DT-D1] [DT-P2] [DT-P3] [DT-P4] [DT-P6] start, re-ask, approve, finish, prepare, edit, save, publish, link', async ({ page }) => {
    const run = await start(page, VIDEO, { scenario: 'hitl' });

    await expect(id(page, 'action-title')).toHaveText('New theme: approval needed', LIVE);
    await expect(id(page, 'action-prompt')).toContainText('Proposed: fake-theme');
    await expect(id(page, 'node-kb_curator')).toHaveAttribute('data-state', 'paused', LIVE);
    await id(page, 'action-text').fill('call it quiet strength');
    await id(page, 'action-send').click();
    await expect(id(page, 'action-prompt')).toContainText('Your last answer: call it quiet strength', LIVE);
    await id(page, 'action-option-yes').click();
    await finished(page, run);
    await expect(id(page, 'node-summarizer')).toHaveAttribute('data-state', 'done', LIVE);

    await id(page, 'view-tab-output').click();
    await expect(id(page, 'summary-verdict')).toHaveAttribute('data-value', 'PASS');
    await id(page, 'output-nav-youtube').click();
    await id(page, 'prepare-youtube').click();
    const title = id(page, 'field-youtube-title');
    await expect(id(page, 'status-youtube')).toHaveText('pending');
    await title.fill('Edited in place');
    await id(page, 'save-youtube').click();
    await expect(id(page, 'publish-notice-youtube')).toHaveText('Saved');
    await expect(id(page, 'status-youtube')).toHaveText('pending');
    await page.reload();
    await id(page, 'output-nav-youtube').click();
    await expect(title).toHaveValue('Edited in place');
    await publishes(page, run, 'youtube');

    // The run's own package goes the same way, and still has its edit after the publish.
    await id(page, 'output-nav-wordpress').click();
    await id(page, 'field-wordpress-title').fill('The post, edited');
    await publishes(page, run, 'wordpress');
    await expect(id(page, 'field-wordpress-title')).toHaveValue('The post, edited');
  });

  test('[DT-A2] reject: the run moves on and finishes', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'hitl' });
    await id(page, 'action-option-no').click(LIVE);
    await finished(page, run);
    await expect(id(page, 'action-option-no')).toHaveCount(0);
  });
});

test.describe('node error', () => {
  test('[DT-E1] retry succeeds', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'error' });
    await expect(id(page, 'action-banner')).toHaveAttribute('data-kind', 'pending-error', LIVE);
    await expect(id(page, 'failure-node')).toHaveText('research');
    await expect(id(page, 'failure-error')).toContainText('fake failure at research');
    await id(page, 'action-option-retry').click();
    await finished(page, run);
    await settled(page);
  });

  test('[DT-E2] fails twice: asked again, and the second retry finishes it', async ({ page }) => {
    const replies = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/reply')) replies.push(r.postDataJSON());
    });
    const run = await start(page, TXT, { scenario: 'error_twice' });
    await id(page, 'action-option-retry').click(LIVE);
    await expect.poll(() => replies.length).toBe(1);
    await expect(statusOf(page, run)).not.toHaveAttribute('data-status', 'done');
    await id(page, 'action-option-retry').click(LIVE);
    await finished(page, run);
    expect(new Set(replies.map((r) => r.id)).size).toBe(2);
  });

  test('[DT-E3] [DT-F1] abort: failed with the reason, then retry from checkpoint finishes it', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'error' });
    await id(page, 'action-option-abort').click(LIVE);
    await expect(statusOf(page, run)).toHaveAttribute('data-status', 'failed', LIVE);
    await expect(id(page, 'action-title')).toHaveText('Failed at research');
    await expect(id(page, 'failure-error')).toContainText('fake failure at research');
    await id(page, 'action-retry').click();
    await finished(page, run);
    await settled(page);
    await expect(id(page, 'node-research')).toHaveAttribute('data-state', 'done');
  });

  test('[DT-F4] abort, then restart: a new run from the same input', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'error' });
    await id(page, 'action-option-abort').click(LIVE);
    await expect(id(page, 'action-restart')).toBeVisible(LIVE);
    await id(page, 'action-restart').click();
    await expect(id(page, 'action-restart-question')).toBeVisible();
    await id(page, 'action-restart-confirm').click();
    await expect(page).not.toHaveURL(new RegExp(`#/runs/${run}$`));
    const again = /#\/runs\/([^/]+)$/.exec(page.url())[1];
    await finished(page, again);
    await expect(id(page, 'run-header-input')).toContainText(TXT);
    await expect(statusOf(page, run)).toHaveAttribute('data-status', 'failed');
  });

  test('[DT-E4] non-recoverable: the reason and only Abort', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'non_recoverable' });
    await expect(id(page, 'action-reason')).toContainText('auth credentials rejected', LIVE);
    await expect(id(page, 'action-banner').locator('button:not([data-testid="failure-node"])')).toHaveText(['Abort']);
    await expect(id(page, 'action-text')).toHaveCount(0);
    await id(page, 'action-option-abort').click();
    await expect(statusOf(page, run)).toHaveAttribute('data-status', 'failed', LIVE);
  });

  test('[DT-A5] unknown curator action: the run asks, then continues', async ({ page }) => {
    const run = await start(page, TXT, { scenario: 'curator' });
    await expect(id(page, 'action-title')).toHaveText('Curator: decision needed', LIVE);
    await expect(id(page, 'action-prompt')).toContainText('ALREADY_PROCESSED');
    await id(page, 'action-text').fill('it is a new angle, keep it');
    await id(page, 'action-send').click();
    await expect(id(page, 'action-prompt')).toContainText('Your last answer: it is a new angle, keep it', LIVE);
    await id(page, 'action-option-yes').click();
    await finished(page, run);
  });
});

test('[DT-R3] [DT-F3] stop a running run: stopped, then retry from checkpoint finishes it', async ({ page }) => {
  // Slow enough to catch running; stopped after a node ended, since with none there is no checkpoint (F6).
  const run = await start(page, TXT, { scenario: 'ok', step_ms: 1500 });
  await expect(id(page, 'node-extract')).toHaveAttribute('data-state', 'done', LIVE);
  await expect(statusOf(page, run)).toHaveAttribute('data-status', 'running', LIVE);
  await id(page, 'action-stop').click();
  await id(page, 'action-stop-confirm').click();
  await expect(statusOf(page, run)).toHaveAttribute('data-status', 'stopped', LIVE);
  await expect(id(page, 'action-title')).toHaveText(/^Stopped/);
  await id(page, 'action-retry').click();
  await finished(page, run);
});

test('[DT-N1] [DT-F7] the needs-you bar reaches a run in one click; dismiss takes it off', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const item = id(page, 'needs-you-item-fx-failed');
  await expect(item).toBeVisible();
  const before = Number(await id(page, 'needs-you-count').textContent());
  await item.click();
  await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
  await expect(id(page, 'action-banner')).toHaveAttribute('data-kind', 'failure');
  await expect(id(page, 'failure-error')).toBeVisible();
  await id(page, 'action-dismiss').click();
  await expect(item).toHaveCount(0);
  await expect(id(page, 'needs-you-count')).toHaveText(String(before - 1));
  await page.reload();
  await expect(id(page, 'needs-you-count')).toHaveText(String(before - 1));
  await expect(item).toHaveCount(0);
});

test('[DT-P2] [DT-P4] an older run with no package reaches YouTube and WordPress publish', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await statusOf(page, 'fx-unpackaged').click();
  await openOutput(page, 'youtube');
  await id(page, 'prepare-youtube').click();
  await expect(id(page, 'field-youtube-title')).toHaveValue('When the road stops arguing');
  await publishes(page, 'fx-unpackaged', 'youtube');
  await id(page, 'output-nav-wordpress').click();
  await id(page, 'prepare-wordpress').click();
  await expect(id(page, 'field-wordpress-title')).toHaveValue('The road stopped arguing');
  await publishes(page, 'fx-unpackaged', 'wordpress');
});

test('[DT-D5] research: an http link, a local book that opens, a row with no link', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  await statusOf(page, 'fx-unpackaged').click();
  await openOutput(page, 'research');
  await expect(id(page, 'research-link-1')).toHaveAttribute('href', 'https://example.org/endure');
  await expect(id(page, 'research-nolink-3')).toHaveText('No link');
  await expect(id(page, 'research-link-3')).toHaveCount(0);

  const served = page.context().waitForEvent('response', (r) => r.url().endsWith('/api/runs/fx-unpackaged/research/2/file'));
  const opened = page.context().waitForEvent('page');
  await id(page, 'research-link-2').click();
  expect((await served).status()).toBe(200);
  await (await opened).close();
});

for (const [width, height] of [[1280, 720], [3440, 1440]]) {
  test.describe(`at ${width}`, () => {
    test.use({ viewport: { width, height } });

    test(`[DT-N4] [DT-R1] zoom, pan and fit, the inspector never over the graph, metrics bar under 40 px`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto('/#/runs/fx-done');
      const svg = id(page, 'dag').locator('svg.dag-svg');
      const zoom = async () => Number(await svg.getAttribute('data-zoom'));
      const writer = id(page, 'node-writer');
      await expect(writer).toHaveAttribute('data-state', 'done');
      await expect.poll(zoom).toBeGreaterThan(0);
      const fitted = await zoom();

      await id(page, 'dag-zoom-in').click();
      await expect.poll(zoom).toBeGreaterThan(fitted);
      const zoomed = await zoom();
      const box = await id(page, 'dag').boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(0, 200);
      await expect.poll(zoom).toBeLessThan(zoomed);

      const a = await writer.boundingBox();
      await page.mouse.move(box.x + 8, box.y + box.height - 8);
      await page.mouse.down();
      await page.mouse.move(box.x + 68, box.y + box.height - 48, { steps: 4 });
      await page.mouse.up();
      await expect.poll(async () => (await writer.boundingBox()).x).toBeCloseTo(a.x + 60, 0);

      await id(page, 'dag-fit').click();
      await expect.poll(zoom).toBe(fitted);

      await writer.click();
      const drawer = id(page, 'drawer');
      await expect(drawer).toBeVisible();
      await expect.poll(async () => {
        const [g, d] = [await id(page, 'dag').boundingBox(), await drawer.boundingBox()];
        return g.x + g.width <= d.x + 0.5;
      }).toBe(true);
      await id(page, 'dag-zoom-in').click();
      const [g, d] = [await id(page, 'dag').boundingBox(), await drawer.boundingBox()];
      expect(g.x + g.width).toBeLessThanOrEqual(d.x + 0.5);

      const bar = await id(page, 'metrics-bar').boundingBox();
      expect(bar.height).toBeLessThan(40);
    });

    test('[DT-N3] the output takes the whole free centre column of the real shell', async ({ page }) => {
      await page.goto('/#/runs/fx-done');
      await id(page, 'view-tab-output').click();
      await expect(id(page, 'output-section-summary')).toBeVisible();
      const g = await page.evaluate(() => {
        const width = (q) => document.querySelector(q).getBoundingClientRect().width;
        return { free: window.innerWidth - width('[data-testid="run-list"]'), operability: width('#operability'), panel: width('[data-testid="output"]') };
      });
      expect(Math.abs(g.operability - g.free)).toBeLessThan(10);
      expect(Math.abs(g.panel - g.operability)).toBeLessThan(2);
      await id(page, 'output-nav-blog').click();
      await expect(id(page, 'artifact-edit-blog_post.md')).toBeVisible();
      // nothing scrolls the page: the section keeps to its pane
      expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    });
  });
}

test('[DT-N2] a reload keeps the view', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  for (const view of ['output', 'logs', 'graph']) {
    await id(page, `view-tab-${view}`).click();
    await page.reload();
    await expect(page.locator('body')).toHaveAttribute('data-view', view);
    await expect(id(page, `view-tab-${view}`)).toHaveAttribute('aria-selected', 'true');
    await expect(statusOf(page, 'fx-done')).toHaveAttribute('aria-pressed', 'true');
  }
  await expect(id(page, 'dag')).toBeVisible();
});

test('[DT-N5] backend down shows the badge; it clears when the backend returns', async ({ page, errors }) => {
  errors.allow(/requestfailed: .*\/api\//);
  errors.allow(/ERR_FAILED/);
  await page.goto('/#/runs/fx-done');
  const badge = id(page, 'error-badge');
  await expect(statusOf(page, 'fx-done')).toBeVisible();
  await expect(badge).toBeHidden();
  await page.route('**/api/**', (route) => route.abort());
  await expect(badge).toBeVisible({ timeout: 10_000 }); // the run list polls every 5 s
  await page.unroute('**/api/**');
  await expect(badge).toBeHidden({ timeout: 10_000 });
});
