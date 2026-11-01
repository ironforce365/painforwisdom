import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());

test('renders every node and edge of /api/graph, labels only in aria/tooltip', async ({ page, request }) => {
  const graph = await (await request.get('/api/graph')).json();
  await page.goto('/#/runs/fx-done');
  const dag = page.getByTestId('dag');
  for (const n of graph.nodes) {
    const node = dag.getByTestId(`node-${n.id}`);
    await expect(node).toBeVisible();
    await expect(node).toHaveAttribute('aria-label', new RegExp(`^${n.label}`));
  }
  for (const e of graph.edges) await expect(dag.getByTestId(`edge-${e.from}-${e.to}`)).toHaveCount(1);
  await expect(dag.locator('[data-testid^="node-"]')).toHaveCount(graph.nodes.length);
  await expect(dag.locator('text')).toHaveCount(0);
});

test('columns follow longest-path depth', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const col = (id) => page.getByTestId(`node-${id}`).getAttribute('data-col');
  expect(await col('transcribe')).toBe('0');
  expect(await col('extract')).toBe('1');
  expect(await col('wordpress_draft')).toBe('6');
  expect(await col('summarizer')).toBe('8');
});

const STATES = [
  ['pending', 'fx-running', 'writer'],
  ['running', 'fx-running', 'extract'],
  ['paused', 'fx-hitl', 'kb_curator'],
  ['done', 'fx-done', 'extract'],
  ['error', 'fx-failed', 'research'],
  ['skipped', 'fx-done', 'transcribe'],
];

for (const [state, run, nodeId] of STATES) {
  test(`node state ${state} sets data-state and its static cue`, async ({ page }) => {
    await page.goto(`/#/runs/${run}`);
    const node = page.getByTestId(`node-${nodeId}`);
    await expect(node).toHaveAttribute('data-state', state);
    await expect(node).toHaveAttribute('aria-label', new RegExp(`${state}$`));
    await expect(node.getByTestId(`cue-${state}`)).toBeVisible();
    for (const [other] of STATES.filter(([s]) => s !== state)) {
      await expect(node.getByTestId(`cue-${other}`)).toBeHidden();
    }
  });
}

const animation = (loc) => loc.evaluate((el) => getComputedStyle(el).animationName);

test('running node pulses and its inbound edge flows; reduced motion stops both', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/#/runs/fx-running');
  const ring = page.getByTestId('node-extract').getByTestId('ring');
  const edge = page.getByTestId('edge-transcribe-extract');
  await expect(page.getByTestId('node-extract')).toHaveAttribute('data-state', 'running');
  await expect(edge).toHaveAttribute('data-state', 'running');
  expect(await animation(ring)).not.toBe('none');
  expect(await animation(edge)).not.toBe('none');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await animation(ring)).toBe('none');
  expect(await animation(edge)).toBe('none');
  await expect(page.getByTestId('node-extract').getByTestId('cue-running')).toBeVisible();
});

test('clicking or pressing Enter on a node selects it', async ({ page }) => {
  await page.goto('/#/runs/fx-done');
  const writer = page.getByTestId('node-writer');
  await writer.click();
  await expect(writer).toHaveAttribute('aria-pressed', 'true');
  const research = page.getByTestId('node-research');
  await research.focus();
  await page.keyboard.press('Enter');
  await expect(research).toHaveAttribute('aria-pressed', 'true');
  await expect(writer).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('Tab');
  await page.keyboard.press(' ');
  await expect(research).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('a');
});
