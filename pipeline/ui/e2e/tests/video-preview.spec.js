import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

async function openVideo(page) {
  await page.goto('/#/runs/fx-unpackaged/output');
  await page.getByTestId('output-nav-youtube').click();
  await page.getByTestId('prepare-youtube').click();
  return page.getByTestId('youtube-video');
}

// Chromium cannot decode the HEVC Main 10 the real phone videos use: audio plays over a black box, with no error event.
const loaded = (video, width) => video.evaluate((node, w) => {
  Object.defineProperty(node, 'videoWidth', { value: w, configurable: true });
  node.dispatchEvent(new Event('loadedmetadata'));
}, width);

test('[DT-P3] a video the browser cannot decode says so and says the upload is unaffected', async ({ page, errors }) => {
  errors.allow(/asset\/video/);
  const video = await openVideo(page);
  await loaded(video, 0);
  const notice = page.getByTestId('asset-undecodable-youtube-video');
  await expect(notice).toContainText('cannot decode');
  await expect(notice).toContainText('upload');
});

test('[DT-P3] a video the browser decodes shows no notice', async ({ page, errors }) => {
  errors.allow(/asset\/video/);
  const video = await openVideo(page);
  await loaded(video, 640);
  await expect(page.getByTestId('asset-undecodable-youtube-video')).toBeHidden();
});
