// A real decode: the fixture is real HEVC Main 10, which Chromium cannot play, and the fake converter returns real H.264.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, freshServerState, PROCESSED } from '../fixtures.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const HEVC = path.join(REPO, 'tests/fixtures/ui/preview/hevc10.mp4');
const RUN = 'fx-unpackaged';
const PREVIEW = `**/api/runs/${RUN}/publish/youtube/asset/video/preview`;
const FILE = `${PREVIEW}/file`;
const pkgFile = path.join(PROCESSED, RUN, 'langgraph', 'youtube-upload', 'package.json');

test.beforeEach(() => freshServerState());

async function openVideo(page, request, source) {
  expect((await request.post(`/api/runs/${RUN}/publish/youtube/prepare`)).status()).toBe(201);
  if (source) {
    const pkg = JSON.parse(fs.readFileSync(pkgFile, 'utf8'));
    fs.writeFileSync(pkgFile, JSON.stringify({ ...pkg, assets: [{ role: 'video', path: source }] }));
  }
  await page.goto(`/#/runs/${RUN}/output`);
  await page.getByTestId('output-nav-youtube').click();
  return page.getByTestId('youtube-video');
}

const width = (video) => video.evaluate((node) => node.videoWidth);
const status = (page) => page.getByTestId('asset-preview-youtube-video');
const spinner = (page) => page.getByTestId('asset-converting-youtube-video');
/** Answers `json` once the page has asked for a conversion; until then the real server (no copy yet) answers. */
const afterPost = (page, json) => {
  let posted = false;
  return page.route(PREVIEW, (route) => {
    if (route.request().method() === 'POST') posted = true;
    return posted ? route.fulfill({ status: route.request().method() === 'POST' ? 202 : 200, json }) : route.continue();
  });
};
const counted = (page, method) => {
  const seen = [];
  page.on('request', (r) => { if (r.method() === method && r.url().endsWith('/asset/video/preview')) seen.push(r.url()); });
  return seen;
};

test('[DT-P12] a video the browser cannot decode is converted and swapped in, and the upload keeps the original', async ({ page, request }) => {
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect(status(page)).toContainText('converted copy');
  await expect(status(page)).toContainText('upload sends the original');
  await expect(video).toHaveAttribute('src', /asset\/video\/preview\/file$/);
  await expect.poll(() => width(video)).toBe(16); // the browser really decodes the copy

  const pkg = JSON.parse(fs.readFileSync(pkgFile, 'utf8'));
  expect(pkg.assets).toEqual([{ role: 'video', path: HEVC }]);
  const original = await request.get(`/api/runs/${RUN}/publish/youtube/asset/video`);
  expect(Buffer.from(await original.body()).equals(fs.readFileSync(HEVC))).toBe(true);
});

test('[DT-P12] while it converts the page says so, and an animation stands in for the video', async ({ page, request }) => {
  await afterPost(page, { status: 'converting', error: null });
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
  await expect(status(page)).toContainText('cannot decode');
  await expect(status(page)).toContainText('Converting');
  await expect(spinner(page)).toBeVisible();
  await expect(video).toBeHidden(); // not a black box with sound
  // A tall video pushes anything under it off screen, and then nothing explains the wait.
  expect((await status(page).boundingBox()).y).toBeLessThan((await spinner(page).boundingBox()).y);
});

test('[DT-P12] a video the browser decodes is left alone', async ({ page, request }) => {
  const posts = counted(page, 'POST');
  const video = await openVideo(page, request);
  await expect.poll(() => width(video)).toBe(16);
  await expect(status(page)).toBeHidden();
  await expect(video).toHaveAttribute('src', /asset\/video$/);
  expect(posts).toEqual([]);
});

test('[DT-P12] the swap keeps the position, and keeps playing what was playing', async ({ page, request }) => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route(PREVIEW, async (route) => {
    if (route.request().method() === 'POST') await held; // the first look finds no copy: the original loads
    await route.continue();
  });
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
  await video.evaluate((node) => {
    node.currentTime = 0.1;
    Object.defineProperty(node, 'paused', { value: false, configurable: true });
  });
  release();
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect.poll(() => video.evaluate((node) => node.autoplay)).toBe(true);
  await expect.poll(() => width(video)).toBe(16);
  expect(await video.evaluate((node) => node.currentTime)).toBeGreaterThanOrEqual(0.1);
});

test('[DT-P12] a failed conversion gives the reason, and retry converts', async ({ page, request }) => {
  let failing = true;
  await page.route(PREVIEW, async (route) => {
    if (failing) return route.fulfill({ status: 202, json: { status: 'failed', error: 'Invalid data found' } });
    return route.continue();
  });
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'failed');
  await expect(status(page)).toContainText('Invalid data found');
  await expect(status(page)).toContainText('upload sends the original');
  failing = false;
  await page.getByTestId('asset-preview-retry-youtube-video').click();
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect.poll(() => width(video)).toBe(16);
});

test('[DT-P12] a console restarted mid-conversion is asked again', async ({ page, request }) => {
  const posts = counted(page, 'POST');
  let answers = [{ status: 200, json: { status: 'none', error: null } }, { status: 202, json: { status: 'converting', error: null } },
    { status: 200, json: { status: 'none', error: null } }];
  await page.route(PREVIEW, async (route) => {
    const [next, ...rest] = answers;
    answers = rest;
    return next ? route.fulfill(next) : route.continue();
  });
  await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  expect(posts.length).toBe(2);
});

test('[DT-P12] leaving the section while it converts still shows the copy on the way back', async ({ page, request }) => {
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
  await page.getByTestId('output-nav-blog').click();
  await expect(page.getByTestId('youtube-video')).toBeHidden();
  await page.waitForTimeout(2500); // the fake conversion finishes while the section is away
  await page.getByTestId('output-nav-youtube').click();
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect.poll(() => width(video)).toBe(16);
});

test('[DT-P12] a file the browser rejects outright also gets a copy', async ({ page, request, errors }) => {
  errors.allow(/asset\/video/);
  await page.route(`**/api/runs/${RUN}/publish/youtube/asset/video`, (route) => route.fulfill({ status: 200, contentType: 'video/mp4', body: 'not a video' }));
  const video = await openVideo(page, request);
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect.poll(() => width(video)).toBe(16);
});

test('[DT-P12] an error on the original while it converts does not start a second conversion', async ({ page, request }) => {
  const posts = counted(page, 'POST');
  await afterPost(page, { status: 'converting', error: null });
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
  await video.evaluate((node) => node.dispatchEvent(new Event('error')));
  await page.evaluate(() => fetch('/health'));
  expect(posts.length).toBe(1);
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
});

test('[DT-P12] with no file to convert it says it cannot be shown', async ({ page, request, errors }) => {
  errors.allow(/404/);
  errors.allow(/asset\/video/);
  await page.route(`**/api/runs/${RUN}/publish/youtube/asset/video`, (route) => route.fulfill({ status: 404, body: '' }));
  await page.route(PREVIEW, (route) => route.fulfill({ status: 404, json: { detail: 'unknown asset' } }));
  await openVideo(page, request);
  await expect(page.getByTestId('asset-missing-youtube-video')).toBeVisible();
  await expect(status(page)).toBeHidden();
});

test('[DT-P12] a copy that will not load says it cannot be shown, without converting again', async ({ page, request, errors }) => {
  errors.allow(/404/);
  errors.allow(/preview\/file/);
  const posts = counted(page, 'POST');
  await page.route(FILE, (route) => route.fulfill({ status: 404, body: '' }));
  await openVideo(page, request, HEVC);
  await expect(page.getByTestId('asset-missing-youtube-video')).toBeVisible({ timeout: 30_000 });
  expect(posts.length).toBe(1);
});

const originalGets = (page) => {
  const seen = [];
  page.on('request', (r) => { if (r.url().endsWith('/asset/video')) seen.push(r.url()); });
  return seen;
};

async function convertOnServer(request) {
  expect((await request.post(`/api/runs/${RUN}/publish/youtube/asset/video/preview`)).status()).toBe(202);
  await expect.poll(async () => (await (await request.get(`/api/runs/${RUN}/publish/youtube/asset/video/preview`)).json()).status,
    { timeout: 30_000 }).toBe('ready');
}

test('[DT-P13] a copy the run already made plays at once, without trying the original', async ({ page, request }) => {
  expect((await request.post(`/api/runs/${RUN}/publish/youtube/prepare`)).status()).toBe(201);
  const pkg = JSON.parse(fs.readFileSync(pkgFile, 'utf8'));
  fs.writeFileSync(pkgFile, JSON.stringify({ ...pkg, assets: [{ role: 'video', path: HEVC }] }));
  await convertOnServer(request);
  const posts = counted(page, 'POST');
  const originals = originalGets(page);
  await page.goto(`/#/runs/${RUN}/output`);
  await page.getByTestId('output-nav-youtube').click();
  const video = page.getByTestId('youtube-video');
  await expect(video).toHaveAttribute('src', /asset\/video\/preview\/file$/);
  await expect(status(page)).toHaveAttribute('data-state', 'ready');
  await expect.poll(() => width(video)).toBe(16);
  await expect(spinner(page)).toBeHidden();
  expect(posts).toEqual([]);
  expect(originals).toEqual([]);
});

test('[DT-P13] while the run still converts, an animation plays until the copy is ready', async ({ page, request }) => {
  let converting = true;
  await page.route(PREVIEW, (route) => (converting
    ? route.fulfill({ json: { status: 'converting', error: null } })
    : route.continue()));
  const posts = counted(page, 'POST');
  const originals = originalGets(page);
  const video = await openVideo(page, request, HEVC);
  await expect(spinner(page)).toBeVisible();
  await expect(video).toBeHidden();
  await expect(status(page)).toHaveAttribute('data-state', 'converting');
  await expect(status(page)).toContainText('Converting a preview of this video');
  await expect(status(page)).not.toContainText('cannot decode');

  await convertOnServer(request); // the run's conversion finishes
  converting = false;
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect(spinner(page)).toBeHidden();
  await expect(video).toBeVisible();
  await expect.poll(() => width(video)).toBe(16);
  expect(posts).toEqual([]); // the page never asked for a second conversion
  expect(originals).toEqual([]);
});

test('[DT-P13] a conversion that failed before the page opened plays the original and converts on demand', async ({ page, request }) => {
  let first = true;
  await page.route(PREVIEW, (route) => {
    if (first && route.request().method() === 'GET') {
      first = false;
      return route.fulfill({ json: { status: 'failed', error: 'the run could not' } });
    }
    return route.continue();
  });
  const video = await openVideo(page, request, HEVC);
  await expect(status(page)).toHaveAttribute('data-state', 'ready', { timeout: 30_000 });
  await expect.poll(() => width(video)).toBe(16);
});

test('[DT-P13] when the copy\'s state cannot be read, the original still plays', async ({ page, request, errors }) => {
  errors.allow(/\/asset\/video\/preview( |$)/); // the request fails on purpose: console and requestfailed both say so
  await page.route(PREVIEW, (route) => (route.request().method() === 'GET' ? route.abort() : route.continue()));
  const video = await openVideo(page, request);
  await expect(video).toHaveAttribute('src', /asset\/video$/);
  await expect.poll(() => width(video)).toBe(16);
});

test('[DT-P13] a run\'s conversion that fails while the page waits says why and plays the original', async ({ page, request }) => {
  let answer = { status: 'converting', error: null };
  await page.route(PREVIEW, (route) => (route.request().method() === 'GET' ? route.fulfill({ json: answer }) : route.continue()));
  const video = await openVideo(page, request, HEVC);
  await expect(spinner(page)).toBeVisible();
  answer = { status: 'failed', error: 'Invalid data found' };
  await expect(status(page)).toHaveAttribute('data-state', 'failed');
  await expect(status(page)).toContainText('Invalid data found');
  await expect(spinner(page)).toBeHidden();
  await expect(video).toHaveAttribute('src', /asset\/video$/);
});

test('[DT-P3] the YouTube form edits and saves recording date, location and languages', async ({ page, request }) => {
  await openVideo(page, request);
  const date = page.getByTestId('field-youtube-recording_date');
  await expect(date).toHaveAttribute('type', 'date');
  await date.fill('2026-09-02');
  await page.getByTestId('field-youtube-location').fill('33.18, -117.19');
  await expect(page.getByTestId('field-youtube-audio_language')).toHaveValue('en');
  await page.getByTestId('field-youtube-text_language').fill('es');
  await page.getByTestId('save-youtube').click();
  await expect(page.getByTestId('publish-notice-youtube')).toHaveText('Saved');
  const { fields } = JSON.parse(fs.readFileSync(pkgFile, 'utf8'));
  expect(fields).toMatchObject({ recording_date: '2026-09-02', location: '33.18, -117.19', audio_language: 'en', text_language: 'es' });
});
