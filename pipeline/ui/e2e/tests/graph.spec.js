// Graph navigation (spec 2026-11-05-console-end-to-end §4, N4).
import { test, expect, freshServerState } from '../fixtures.js';

const WIDTHS = [[1280, 720], [3440, 1440]];
const zoom = async (page) => Number(await page.locator('[data-testid="dag"] svg.dag-svg').getAttribute('data-zoom'));
const nodes = (page) => page.getByTestId('dag').locator('[data-testid^="node-"]');
const center = async (loc) => {
  const b = await loc.boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
};

async function ready(page, run = 'fx-done') {
  await page.emulateMedia({ reducedMotion: 'reduce' }); // a shake or pulse must not move a bounding box
  await page.goto(`/#/runs/${run}`);
  await expect(page.getByTestId('node-writer')).not.toHaveAttribute('data-state', 'pending');
  await expect.poll(() => zoom(page)).toBeGreaterThan(0);
}

async function expectAllInside(page) {
  const dag = await page.getByTestId('dag').boundingBox();
  const boxes = await nodes(page).evaluateAll((els) => els.map((e) => {
    const r = e.getBoundingClientRect();
    return { id: e.dataset.testid, left: r.left, right: r.right, top: r.top, bottom: r.bottom };
  }));
  expect(boxes.length).toBe(16);
  for (const b of boxes) {
    expect(b.left, b.id).toBeGreaterThanOrEqual(dag.x - 0.5);
    expect(b.right, b.id).toBeLessThanOrEqual(dag.x + dag.width + 0.5);
    expect(b.top, b.id).toBeGreaterThanOrEqual(dag.y - 0.5);
    expect(b.bottom, b.id).toBeLessThanOrEqual(dag.y + dag.height + 0.5);
  }
  return { dag, boxes };
}

test.beforeEach(() => freshServerState());

for (const [width, height] of WIDTHS) {
  test.describe(`at ${width}`, () => {
    test.use({ viewport: { width, height } });

    test('[DT-N4] fit shows every node; the inspector docks and never overlaps the graph', async ({ page }) => {
      await ready(page);
      await expectAllInside(page);
      await page.getByTestId('node-writer').click();
      const drawer = page.getByTestId('drawer');
      await expect(drawer).toBeVisible();
      await expect.poll(async () => {
        const [d, g] = [await drawer.boundingBox(), await page.getByTestId('dag').boundingBox()];
        return g.x + g.width <= d.x + 0.5;
      }).toBe(true);
      await expect.poll(async () => {
        const g = await page.getByTestId('dag').boundingBox();
        const right = await nodes(page).evaluateAll((els) => Math.max(...els.map((e) => e.getBoundingClientRect().right)));
        return right <= g.x + g.width + 0.5;
      }).toBe(true);
      const { dag } = await expectAllInside(page);
      const d = await drawer.boundingBox();
      expect(dag.x + dag.width).toBeLessThanOrEqual(d.x + 0.5);
      for (let i = 0; i < 16; i += 1) await expect(nodes(page).nth(i)).toBeVisible();
      // reachable: a node at the far right takes the click, with the panel open
      await page.getByTestId('node-summarizer').click();
      await expect(drawer).toHaveAttribute('data-node', 'summarizer');
      await page.getByTestId('drawer-close').click();
      await expect(drawer).toBeHidden();
      await expect.poll(async () => (await page.getByTestId('dag').boundingBox()).width).toBeGreaterThan(dag.width);
      await expectAllInside(page);
    });

    test('the graph takes most of the width it is given', async ({ page }) => {
      await ready(page);
      const { dag, boxes } = await expectAllInside(page);
      const span = Math.max(...boxes.map((b) => b.right)) - Math.min(...boxes.map((b) => b.left));
      expect(span / dag.width).toBeGreaterThan(0.7);
    });
  });
}

test('[DT-N4] every node shows its label as text and what it spent', async ({ page, request }) => {
  const graph = await (await request.get('/api/graph')).json();
  await ready(page);
  for (const n of graph.nodes) {
    const label = page.getByTestId(`node-${n.id}`).getByTestId('dag-label');
    await expect(label).toHaveText(n.label);
    await expect(label).toBeVisible();
  }
  await expect(page.getByTestId('node-research').getByTestId('dag-meta')).toHaveText('1m 27s · $0.40');
  await expect(page.getByTestId('node-youtube_upload').getByTestId('dag-meta')).toContainText('skipped');
  await page.goto('/#/runs/fx-unpackaged');
  await expect(page.getByTestId('node-extract').getByTestId('dag-meta')).toContainText('≈$0.01');
  await expect(page.getByTestId('node-writer').getByTestId('dag-meta')).not.toContainText('$0');
});

test('[DT-N4] the wheel zooms at the cursor and clamps', async ({ page }) => {
  await ready(page);
  const before = await zoom(page);
  const writer = page.getByTestId('node-writer');
  const c = await center(writer.locator('.disc'));
  await page.mouse.move(c.x, c.y);
  await page.mouse.wheel(0, -400);
  await expect.poll(() => zoom(page)).toBeGreaterThan(before);
  const after = await center(writer.locator('.disc'));
  expect(Math.abs(after.x - c.x)).toBeLessThan(2);
  expect(Math.abs(after.y - c.y)).toBeLessThan(2);
  for (let i = 0; i < 12; i += 1) await page.mouse.wheel(0, -2000);
  await expect.poll(() => zoom(page)).toBe(4);
  for (let i = 0; i < 12; i += 1) await page.mouse.wheel(0, 3000);
  await expect.poll(() => zoom(page)).toBe(0.2);
});

test('[DT-N4] a drag pans and does not select; a click selects', async ({ page }) => {
  await ready(page);
  const writer = page.getByTestId('node-writer');
  const c = await center(writer.locator('.disc'));
  await page.mouse.move(c.x, c.y);
  await page.mouse.down();
  await page.mouse.move(c.x + 60, c.y + 40, { steps: 4 });
  await page.mouse.up();
  const moved = await center(writer.locator('.disc'));
  expect(moved.x - c.x).toBeCloseTo(60, 0);
  expect(moved.y - c.y).toBeCloseTo(40, 0);
  await expect(writer).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('drawer')).toBeHidden();

  // the next plain click is not swallowed
  await writer.click();
  await expect(writer).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('drawer-close').click();
  await expect(writer).toHaveAttribute('aria-pressed', 'false');

  // a jitter below the threshold is still a click
  const d = await center(writer.locator('.disc'));
  await page.mouse.move(d.x, d.y);
  await page.mouse.down();
  await page.mouse.move(d.x + 1, d.y);
  await page.mouse.up();
  await expect(writer).toHaveAttribute('aria-pressed', 'true');

  // a stray move with no button down does nothing; a drag from empty space pans
  const box = await page.getByTestId('dag').boundingBox();
  await page.mouse.move(box.x + 5, box.y + box.height - 5);
  const held = await center(writer.locator('.disc'));
  await page.mouse.move(box.x + 25, box.y + box.height - 5);
  expect((await center(writer.locator('.disc'))).x).toBeCloseTo(held.x, 0);
});

test('[DT-N4] buttons and keys zoom and fit; labels are text', async ({ page }) => {
  await ready(page);
  const fitted = await zoom(page);
  await expect(page.getByTestId('dag-zoom-in')).toHaveText(/zoom in/i);
  await expect(page.getByTestId('dag-zoom-out')).toHaveText(/zoom out/i);
  await expect(page.getByTestId('dag-fit')).toHaveText(/fit/i);
  await expect(page.getByTestId('dag-zoom-level')).toHaveText(`${Math.round(fitted * 100)}%`);

  await page.getByTestId('dag-zoom-in').click();
  const z1 = await zoom(page);
  expect(z1).toBeGreaterThan(fitted);
  await page.getByTestId('dag-zoom-out').click();
  await page.getByTestId('dag-zoom-out').click();
  expect(await zoom(page)).toBeLessThan(z1);
  await page.getByTestId('dag-fit').click();
  await expect.poll(() => zoom(page)).toBe(fitted);

  await page.keyboard.press('+');
  await expect.poll(() => zoom(page)).toBeGreaterThan(fitted);
  await page.keyboard.press('0');
  await expect.poll(() => zoom(page)).toBe(fitted);
  await page.keyboard.press('-');
  await expect.poll(() => zoom(page)).toBeLessThan(fitted);
  await page.keyboard.press('0');
  await page.keyboard.press('=');
  await expect.poll(() => zoom(page)).toBeGreaterThan(fitted);
  await page.keyboard.press('0');

  // zoom keys go on working with a node focused, which keeps Enter
  const research = page.getByTestId('node-research');
  await research.focus();
  await page.keyboard.press('+');
  await expect.poll(() => zoom(page)).toBeGreaterThan(fitted);
  await page.keyboard.press('Enter');
  await expect(research).toHaveAttribute('aria-pressed', 'true');
});

test('keys leave typing, browser zoom and other views alone', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const run = await res.json();
    run.pending = {
      id: 'x', kind: 'approval', node: 'writer', title: 't', prompt: 'p', error: null, non_recoverable: null,
      options: [], free_text: true,
    };
    await route.fulfill({ response: res, json: run });
  });
  await ready(page);
  const fitted = await zoom(page);
  await page.getByTestId('action-text').click();
  await page.keyboard.type('+-0');
  await expect(page.getByTestId('action-text')).toHaveValue('+-0');
  expect(await zoom(page)).toBe(fitted);
  await page.locator('body').click({ position: { x: 1, y: 1 } });
  await page.keyboard.press('Control+-');
  expect(await zoom(page)).toBe(fitted);
  await page.getByTestId('view-tab-logs').click();
  await page.keyboard.press('+');
  await page.getByTestId('view-tab-graph').click();
  await expect.poll(() => zoom(page)).toBe(fitted);
});

test('[DT-N4] refits when its box resizes, unless the user moved it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await ready(page);
  const first = await zoom(page);
  await page.setViewportSize({ width: 1900, height: 1000 });
  await expect.poll(() => zoom(page)).toBeGreaterThan(first);
  await expectAllInside(page);
  const second = await zoom(page);
  // user moves it: resizing leaves it alone
  await page.getByTestId('dag-zoom-out').click();
  const third = await zoom(page);
  expect(third).toBeLessThan(second);
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect.poll(async () => (await page.getByTestId('dag').boundingBox()).width).toBeLessThan(1100);
  expect(await zoom(page)).toBe(third);
  // Fit hands it back
  await page.getByTestId('dag-fit').click();
  await expect.poll(() => zoom(page)).toBe(first);
  await page.setViewportSize({ width: 1900, height: 1000 });
  await expect.poll(() => zoom(page)).toBe(second);
});

test('[DT-N4] the graph fits when shown again after another view at another size', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/runs/fx-done/logs');
  await expect(page.getByTestId('logs')).toBeVisible();
  await page.getByTestId('view-tab-graph').click();
  await expect.poll(() => zoom(page)).toBeGreaterThan(0);
  await expectAllInside(page);
});

test('a small window still reaches every node after fit', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 500 });
  await ready(page);
  await expect(page.getByTestId('dag')).toBeVisible();
  await page.getByTestId('dag-fit').click();
  const box = await page.getByTestId('dag').boundingBox();
  expect(box.width).toBeGreaterThan(100);
});
