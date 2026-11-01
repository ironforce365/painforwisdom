import { test, expect, freshServerState } from '../fixtures.js';

test.beforeEach(() => freshServerState());
test.afterEach(() => freshServerState());

test('api.js has one working function per C3 endpoint', async ({ page, errors }) => {
  errors.allow(/status of 404/);
  await page.goto('/#/runs/fx-done');
  const out = await page.evaluate(async () => {
    const api = await import('/static/js/api.js');
    const runs = (await api.listRuns()).runs.length;
    const arts = (await api.listArtifacts('fx-done')).artifacts;
    const file = await fetch(api.artifactUrl('fx-done', arts[0].id));
    let missing;
    try {
      await api.launchRun({ input: 'nope.txt' });
    } catch (e) {
      missing = { status: e.status, detail: e.body.detail, name: e.name };
    }
    const launched = await api.launchRun({ input: 'transcript_2026-10-30.txt', fake: { scenario: 'ok', step_ms: 0 } });
    return {
      nodes: (await api.getGraph()).nodes.length,
      runs,
      run: (await api.getRun('fx-done')).verdict,
      arts: arts.length,
      fileOk: file.ok,
      result: (await api.getResult('fx-done')).status,
      inputs: (await api.listInputs()).inputs.length,
      missing,
      launched: typeof launched.run_id,
    };
  });
  expect(out).toEqual({
    nodes: 15, runs: 6, run: 'PASS', arts: expect.any(Number), fileOk: true, result: 'ready', inputs: 2,
    missing: { status: 404, detail: 'unknown input', name: 'ApiError' }, launched: 'string',
  });
  expect(out.arts).toBeGreaterThan(0);
});
