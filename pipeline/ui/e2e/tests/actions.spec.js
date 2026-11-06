// Action banner (spec 2026-11-05-console-end-to-end §2, §4): every branch of a run has a console action.
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, freshServerState, answerPending, PROCESSED } from '../fixtures.js';

const banner = (page) => page.getByTestId('action-banner');
const TXT = 'transcript_2026-10-30.txt';

async function launch(request, scenario, opts = {}) {
  const res = await request.post('/api/runs', { data: { input: TXT, fake: { scenario, step_ms: 30, ...opts } } });
  expect(res.status()).toBe(201);
  return (await res.json()).run_id;
}

/** No browser dialog may ever open: every confirm is in place. */
function noDialogs(page) {
  page.on('dialog', (d) => {
    d.dismiss();
    throw new Error(`browser dialog: ${d.message()}`);
  });
}

const status = (page, id) => page.getByTestId(`run-${id}`);

test.beforeEach(({ page }) => {
  freshServerState();
  noDialogs(page);
});

test.describe('approval', () => {
  test('[DT-A1] approve: title, rendered prompt, buttons from options, the run finishes', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.goto(`/#/runs/${id}`);
    await expect(banner(page)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('action-title')).toHaveText('New theme: approval needed', { timeout: 30_000 });
    await expect(page.getByTestId('action-prompt')).toContainText('Proposed: fake-theme');
    await expect(page.getByTestId('action-option-yes')).toHaveText('Approve');
    await expect(page.getByTestId('action-option-no')).toHaveText('Reject');
    await expect(page.getByTestId('action-text')).toBeVisible();
    await page.getByTestId('action-option-yes').click();
    await expect(page.getByTestId('node-summarizer')).toHaveAttribute('data-state', 'done', { timeout: 30_000 });
    await expect(page.getByTestId('action-option-yes')).toHaveCount(0);
  });

  test('an answer waiting for the runner shows that it was sent and cannot be sent twice', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.route(`**/api/runs/${id}/reply`, (r) => r.fulfill({ status: 202, json: { accepted: true } }));
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('action-option-yes').click({ timeout: 30_000 });
    await expect(page.getByTestId('action-sent')).toBeVisible();
    await expect(page.getByTestId('action-option-yes')).toHaveCount(0);
    await page.unroute(`**/api/runs/${id}/reply`);
    await answerPending(request, id);
  });

  test('[DT-A2] reject sends no and the run moves on', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.goto(`/#/runs/${id}`);
    const posted = page.waitForRequest((r) => r.url().endsWith(`/api/runs/${id}/reply`));
    await page.getByTestId('action-option-no').click({ timeout: 30_000 });
    expect((await posted).postDataJSON().reply).toBe('no');
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-A3] free text re-plans and asks again', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.goto(`/#/runs/${id}`);
    const text = page.getByTestId('action-text');
    await text.fill('rename it to quiet strength', { timeout: 30_000 });
    await page.getByTestId('action-send').click();
    await expect(page.getByTestId('action-prompt')).toContainText('Your last answer: rename it to quiet strength', { timeout: 30_000 });
    await expect(text).toHaveValue('');
    await page.getByTestId('action-option-yes').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-A4] answered on another channel: the console reflects it', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByTestId('action-option-yes')).toBeVisible({ timeout: 30_000 });
    await answerPending(request, id);
    await expect(page.getByTestId('action-option-yes')).toHaveCount(0, { timeout: 15_000 });
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-A5] an unknown curator action asks instead of crashing', async ({ page, request }) => {
    const id = await launch(request, 'curator');
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByTestId('action-title')).toHaveText('Curator: decision needed', { timeout: 30_000 });
    await expect(page.getByTestId('action-prompt')).toContainText('ALREADY_PROCESSED');
    await page.getByTestId('action-text').fill('skip it');
    await page.getByTestId('action-send').click();
    await expect(page.getByTestId('action-prompt')).toContainText('Your last answer: skip it', { timeout: 30_000 });
    await page.getByTestId('action-option-yes').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('polling does not drop a half-typed answer, its caret or a text selection', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    let polls = 0;
    page.on('response', (r) => { if (r.url().endsWith(`/api/runs/${id}`)) polls += 1; });
    await page.goto(`/#/runs/${id}`);
    const text = page.getByTestId('action-text');
    await text.click({ timeout: 30_000 });
    await page.keyboard.type('half a thou');
    const seen = polls;
    await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(seen + 2);
    await expect(text).toHaveValue('half a thou');
    await expect(text).toBeFocused();
    await page.keyboard.type('ght');
    await expect(text).toHaveValue('half a thought');

    const select = () => page.getByTestId('action-prompt').evaluate((n) => {
      const r = document.createRange();
      r.selectNodeContents(n);
      const s = getSelection();
      s.removeAllRanges();
      s.addRange(r);
      return s.toString().length;
    });
    const length = await select();
    expect(length).toBeGreaterThan(0);
    const before = polls;
    await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(before + 2);
    expect(await page.evaluate(() => getSelection().toString().length)).toBe(length);
  });

  test('arming stop while typing keeps the typed answer', async ({ page, request }) => {
    const id = await launch(request, 'hitl');
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('action-text').fill('still typing', { timeout: 30_000 });
    await page.getByTestId('action-stop').click();
    await expect(page.getByTestId('action-stop-confirm')).toBeVisible();
    await expect(page.getByTestId('action-text')).toHaveValue('still typing');
    await page.getByTestId('action-stop-cancel').click();
    await expect(page.getByTestId('action-text')).toHaveValue('still typing');
  });

  test('a prompt and title are never markup', async ({ page }) => {
    const payload = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>';
    await page.route('**/api/runs/fx-done', async (route) => {
      const res = await route.fetch();
      const run = await res.json();
      run.status = 'waiting_hitl';
      run.failure = null;
      run.actions = ['reply', 'stop'];
      run.pending = {
        id: 'x', kind: 'error', node: 'research', title: payload, prompt: `# Heading\n\n${payload}`, error: payload, non_recoverable: payload,
        options: [{ reply: 'yes', label: payload, style: 'primary' }], free_text: false,
      };
      await route.fulfill({ response: res, json: run });
    });
    await page.goto('/#/runs/fx-done');
    await expect(page.getByTestId('action-title')).toHaveText(payload);
    await expect(page.getByTestId('action-prompt').getByRole('heading')).toHaveText('Heading');
    await expect(page.getByTestId('action-prompt')).toContainText('<img src=x');
    await expect(page.getByTestId('failure-error')).toHaveText(payload);
    await expect(page.getByTestId('action-reason')).toContainText(payload);
    await expect(page.getByTestId('action-option-yes')).toHaveText(payload);
    await expect(banner(page).locator('img, script')).toHaveCount(0);
    expect(await page.evaluate(() => window.__xss)).toBeUndefined();
  });
});

test.describe('node error', () => {
  test('[DT-E1] recoverable: node, error, retry succeeds', async ({ page, request }) => {
    const id = await launch(request, 'error');
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByTestId('failure-node')).toHaveText('research', { timeout: 30_000 });
    await expect(page.getByTestId('failure-error')).toContainText('fake failure at research');
    await expect(page.getByTestId('action-text')).toHaveCount(0); // free_text false
    await expect(page.getByTestId('action-send')).toHaveCount(0);
    await expect(page.getByTestId('action-option-retry')).toHaveText('Retry');
    await page.getByTestId('action-option-retry').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
    await expect(page.getByTestId('failure-node')).toHaveCount(0);
  });

  test('[DT-E2] fails again after a retry: asks again', async ({ page, request }) => {
    const id = await launch(request, 'error_twice');
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('action-option-retry').click({ timeout: 30_000 });
    await expect(page.getByTestId('action-option-retry')).toBeVisible({ timeout: 30_000 });
    await page.getByTestId('action-option-retry').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-E3] abort: run failed, reason in the banner', async ({ page, request }) => {
    const id = await launch(request, 'error');
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('action-option-abort').click({ timeout: 30_000 });
    await expect(status(page, id)).toHaveAttribute('data-status', 'failed', { timeout: 30_000 });
    await expect(page.getByTestId('failure-error')).toContainText('fake failure at research');
    await expect(page.getByTestId('failure-node')).toHaveText('research');
  });

  test('[DT-E4] non-recoverable: the reason and only Abort', async ({ page, request }) => {
    const id = await launch(request, 'non_recoverable');
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByTestId('action-reason')).toHaveText(/\S/, { timeout: 30_000 });
    await expect(page.getByTestId('action-option-abort')).toBeVisible();
    await expect(page.getByTestId('action-option-retry')).toHaveCount(0);
    await expect(page.getByTestId('action-stop')).toHaveCount(0);
    await expect(page.getByTestId('action-retry')).toHaveCount(0);
    await page.getByTestId('action-option-abort').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'failed', { timeout: 30_000 });
  });

  test('[DT-E5] no answer before the reminder budget: failed', async ({ page, request }) => {
    const id = await launch(request, 'error', { wait_s: 1 });
    await page.goto(`/#/runs/${id}`);
    await expect(status(page, id)).toHaveAttribute('data-status', 'failed', { timeout: 30_000 });
    await expect(page.getByTestId('failure-error')).toContainText('no answer within');
  });

  test('[DT-E6] old release wait: reason shown, stop offered', async ({ page }) => {
    freshServerState({ live: true });
    await page.goto('/#/runs/fx-oldwait');
    await expect(page.getByTestId('action-title')).toHaveText('Waiting for a reply');
    await expect(page.getByTestId('failure-error')).toContainText('ALREADY_PROCESSED');
    await expect(page.getByTestId('action-hint')).toContainText('Telegram');
    await expect(page.getByTestId('action-stop')).toBeEnabled();
    await expect(page.getByTestId('action-retry')).toBeDisabled();
    await expect(page.getByTestId('action-blocker-retry')).toContainText('still active');
    await expect(page.getByTestId('action-blocker-restart')).toContainText('still active');
  });

  test('the failure node opens the inspector', async ({ page }) => {
    await page.goto('/#/runs/fx-failed');
    await page.getByTestId('failure-node').click();
    await expect(page.getByTestId('drawer')).toHaveAttribute('data-node', 'research');
    await expect(page.getByTestId('drawer-error')).toContainText('research agent returned no rows');
  });

  test('a failure without a graph node shows no node', async ({ page }) => {
    await page.route('**/api/runs/fx-stopped', async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, json: { ...(await res.json()), failure: { node: null, error: 'no node', hint: null } } });
    });
    await page.goto('/#/runs/fx-stopped');
    await expect(page.getByTestId('failure-error')).toHaveText('no node');
    await expect(page.getByTestId('failure-node')).toHaveCount(0);
    await expect(page.getByTestId('action-hint')).toHaveCount(0);
    await expect(page.getByTestId('action-title')).toHaveText('Stopped');
  });
});

test.describe('running', () => {
  test('[DT-R3] stop asks in place, cancels, then stops', async ({ page, request }) => {
    const id = await launch(request, 'ok', { step_ms: 3000 });
    await page.goto(`/#/runs/${id}`);
    await expect(page.getByTestId('action-title')).toContainText('Running', { timeout: 30_000 });
    await page.getByTestId('action-stop').click();
    await expect(page.getByTestId('action-stop-question')).toBeVisible();
    await page.getByTestId('action-stop-cancel').click();
    await expect(page.getByTestId('action-stop')).toBeVisible();
    await expect(status(page, id)).toHaveAttribute('data-status', 'running');
    await page.getByTestId('action-stop').click();
    await page.getByTestId('action-stop-confirm').click();
    await expect(status(page, id)).toHaveAttribute('data-status', 'stopped', { timeout: 20_000 });
    await expect(page.getByTestId('failure-error')).toHaveText('Stopped from the console.');
    await expect(page.getByTestId('action-retry')).toBeVisible();
  });
});

test('between nodes the banner names the status alone', async ({ page }) => {
  await page.route('**/api/runs/fx-modeoff', async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), status: 'running', actions: ['stop'], action_blockers: {} } });
  });
  await page.goto('/#/runs/fx-modeoff');
  await expect(page.getByTestId('action-title')).toHaveText('Running');
  await expect(page.getByTestId('action-stop')).toBeVisible();
});

test.describe('ended, not ok', () => {
  test('[DT-F1] failed: reason and node shown, retry from checkpoint selects the run', async ({ page }) => {
    await page.goto('/#/runs/fx-failed');
    await expect(page.getByTestId('action-title')).toHaveText('Failed at research');
    await expect(page.getByTestId('failure-node')).toHaveText('research');
    await expect(page.getByTestId('failure-error')).toContainText('research agent returned no rows');
    await expect(page.getByTestId('action-hint')).toContainText('Retry');
    await page.getByTestId('node-writer').click(); // the retry reselects the run, which closes the inspector
    await expect(page.getByTestId('drawer')).toBeVisible();
    await page.getByTestId('action-retry').click();
    await expect(page.getByTestId('drawer')).toBeHidden();
    await expect(status(page, 'fx-failed')).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
    await expect(page.getByTestId('node-research')).toHaveAttribute('data-state', 'done');
    await expect(page).toHaveURL(/#\/runs\/fx-failed$/);
  });

  test('[DT-F2] crashed: reason shown, retry from checkpoint', async ({ page }) => {
    await page.goto('/#/runs/fx-crashed');
    await expect(page.getByTestId('action-title')).toContainText('Crashed');
    await expect(page.getByTestId('failure-error')).toContainText('runner process died');
    await page.getByTestId('action-retry').click();
    await expect(status(page, 'fx-crashed')).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-F3] stopped: retry from checkpoint', async ({ page }) => {
    await page.goto('/#/runs/fx-stopped');
    await expect(page.getByTestId('failure-error')).toContainText('Stopped');
    await page.getByTestId('action-retry').click();
    await expect(status(page, 'fx-stopped')).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-F4] restart asks in place and selects the new run', async ({ page }) => {
    await page.goto('/#/runs/fx-stopped');
    await page.getByTestId('action-restart').click();
    await expect(page.getByTestId('action-restart-question')).toBeVisible();
    await page.getByTestId('action-restart-cancel').click();
    await page.getByTestId('action-restart').click();
    const posted = page.waitForResponse((r) => r.url().endsWith('/api/runs/fx-stopped/restart'));
    await page.getByTestId('action-restart-confirm').click();
    const { run_id: id } = await (await posted).json();
    expect(id).not.toBe('fx-stopped');
    await expect(page).toHaveURL(new RegExp(`#/runs/${id}$`));
    await expect(status(page, id)).toHaveAttribute('aria-pressed', 'true');
    await expect(status(page, id)).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
  });

  test('[DT-F5] input file gone: restart refused with the reason', async ({ page }) => {
    await page.goto('/#/runs/fx-failed');
    await expect(page.getByTestId('action-restart')).toBeDisabled();
    await expect(page.getByTestId('action-blocker-restart')).toContainText('input file is gone');
  });

  test('[DT-F6] no checkpoint: retry refused with the reason', async ({ page }) => {
    await page.goto('/#/runs/fx-nocheckpoint');
    await expect(page.getByTestId('action-retry')).toBeDisabled();
    await expect(page.getByTestId('action-blocker-retry')).toContainText('no checkpoint');
  });

  test('[DT-F7] dismiss removes the run from needs-you and can be undone', async ({ page }) => {
    await page.goto('/#/runs/fx-stopped');
    await expect(page.getByTestId('needs-you-item-fx-stopped')).toBeVisible();
    await expect(page.getByTestId('action-dismiss')).toHaveText('Dismiss from needs-you');
    await page.getByTestId('action-dismiss').click();
    await expect(page.getByTestId('needs-you-item-fx-stopped')).toHaveCount(0);
    await expect(page.getByTestId('action-dismiss')).toHaveText('Back to needs-you');
    await page.getByTestId('action-dismiss').click();
    await expect(page.getByTestId('needs-you-item-fx-stopped')).toBeVisible();
  });

  test('another active run blocks retry and restart with its id', async ({ page }) => {
    freshServerState({ live: true });
    await page.goto('/#/runs/fx-stopped');
    await expect(page.getByTestId('action-retry')).toBeDisabled();
    await expect(page.getByTestId('action-blocker-retry')).toContainText('fx-');
    await expect(page.getByTestId('action-blocker-restart')).toContainText('is active');
  });

  test('[DT-F3] a publish in progress blocks retry and restart with its reason', async ({ page }) => {
    freshServerState();
    const dir = path.join(PROCESSED, 'fx-stopped', 'langgraph', 'youtube-upload');
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({
      schema: 1, target: 'youtube', mode: 'manual', status: 'publishing', fields: { title: 'x' }, field_specs: {},
      readonly: {}, assets: [], result: null, error: null, updated_at: 1,
    }));
    fs.writeFileSync(path.join(dir, 'publish.lock'), String(process.pid));
    await page.goto('/#/runs/fx-stopped');
    await expect(page.getByTestId('action-retry')).toBeDisabled();
    await expect(page.getByTestId('action-blocker-retry')).toContainText('youtube is publishing');
    await expect(page.getByTestId('action-blocker-restart')).toContainText('youtube is publishing');
  });
});

test.describe('ended, ok', () => {
  test('[DT-D1] a clean finished run has no banner', async ({ page }) => {
    await page.goto('/#/runs/fx-modeoff');
    await expect(page.getByTestId('run-header-status')).toHaveText('Done');
    await expect(page.getByTestId('run-header-verdict')).toHaveText('PASS');
    await expect(banner(page)).toBeHidden();
  });

  test('[DT-N1] a finished run with a pending package names it, opens that package, and can be dismissed', async ({ page, errors }) => {
    errors.allow(/asset\/featured_image/);
    await page.goto('/#/runs/fx-done');
    await expect(page.getByTestId('action-title')).toHaveText('Waiting for you to publish: WordPress.');
    await page.getByTestId('action-open-wordpress').click();
    await expect(page.locator('body')).toHaveAttribute('data-view', 'output');
    await expect(page.getByTestId('output-nav-wordpress')).toHaveAttribute('aria-current', 'true');
    await expect(page.getByTestId('field-wordpress-title')).toBeVisible();

    // Already on Output, in another section: the same button still goes there. It used to do nothing.
    await page.getByTestId('output-nav-summary').click();
    await expect(page.getByTestId('field-wordpress-title')).toBeHidden();
    await page.getByTestId('action-open-wordpress').click();
    await expect(page.getByTestId('field-wordpress-title')).toBeVisible();

    await page.getByTestId('action-dismiss').click();
    await expect(page.getByTestId('action-title')).toContainText('Dismissed');
    await expect(page.getByTestId('needs-you-item-fx-done')).toHaveCount(0);
  });
});

test('[DT-N1] a package that failed to publish says so', async ({ page }) => {
  await page.route('**/api/runs/fx-done', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.waiting_packages = [{ target: 'wordpress', status: 'failed' }, { target: 'youtube', status: 'pending' }];
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/runs/fx-done');
  await expect(page.getByTestId('action-title')).toHaveText('Waiting for you to publish: WordPress (failed), YouTube.');
  await expect(page.getByTestId('action-open-youtube')).toHaveText('Open YouTube');
});

test.describe('failed API calls show their detail', () => {
  test('retry refused', async ({ page, errors }) => {
    errors.allow(/status of 409/);
    await page.route('**/api/runs/fx-stopped/retry', (r) => r.fulfill({ status: 409, json: { detail: 'this run has no checkpoint' } }));
    await page.goto('/#/runs/fx-stopped');
    await page.getByTestId('action-retry').click();
    await expect(page.getByTestId('action-error')).toHaveText('this run has no checkpoint');
    await expect(page.getByTestId('action-retry')).toBeEnabled();
  });

  test('retry against an active run names it', async ({ page, errors }) => {
    errors.allow(/status of 409/);
    await page.route('**/api/runs/fx-stopped/retry', (r) => r.fulfill({ status: 409, json: { detail: 'run active', run_id: 'fx-other' } }));
    await page.goto('/#/runs/fx-stopped');
    await page.getByTestId('action-retry').click();
    await expect(page.getByTestId('action-error')).toHaveText('run active: fx-other');
  });

  test('restart refused keeps the banner and clears on the next action', async ({ page, errors }) => {
    errors.allow(/status of 404/);
    let fail = true;
    await page.route('**/api/runs/fx-stopped/restart', (r) => (fail
      ? r.fulfill({ status: 404, json: { detail: 'the input file is gone: x.txt' } })
      : r.fulfill({ status: 201, json: { run_id: 'fx-done' } })));
    await page.goto('/#/runs/fx-stopped');
    await page.getByTestId('action-restart').click();
    await page.getByTestId('action-restart-confirm').click();
    await expect(page.getByTestId('action-error')).toHaveText('the input file is gone: x.txt');
    await expect(page.getByTestId('action-restart')).toBeVisible(); // confirm disarmed
    fail = false;
    await page.getByTestId('action-restart').click();
    await page.getByTestId('action-restart-confirm').click();
    await expect(page).toHaveURL(/#\/runs\/fx-done$/);
    await expect(page.getByTestId('action-error')).toBeHidden();
  });

  test('a rejected reply and a network failure', async ({ page, request, errors }) => {
    errors.allow(/status of 400/);
    errors.allow(/requestfailed: .*\/reply/);
    errors.allow(/ERR_FAILED/);
    const id = await launch(request, 'hitl');
    let mode = 'detail';
    await page.route(`**/api/runs/${id}/reply`, (r) => (mode === 'detail'
      ? r.fulfill({ status: 400, json: { detail: 'empty reply' } })
      : r.abort()));
    await page.goto(`/#/runs/${id}`);
    await page.getByTestId('action-send').click({ timeout: 30_000 });
    await expect(page.getByTestId('action-error')).toHaveText('empty reply');
    await expect(page.getByTestId('action-send')).toBeEnabled();
    await expect(page.getByTestId('action-sent')).toHaveCount(0);
    mode = 'abort';
    await page.getByTestId('action-option-yes').click();
    await expect(page.getByTestId('action-error')).toHaveText(/\S/);
    await expect(page.getByTestId('action-option-yes')).toBeEnabled();
    await page.unroute(`**/api/runs/${id}/reply`);
  });

  test('an answer arriving after switching runs lands nowhere', async ({ page, errors }) => {
    errors.allow(/status of 409/);
    let release;
    const held = new Promise((r) => { release = r; });
    await page.route('**/api/runs/fx-stopped/retry', async (r) => {
      await held;
      await r.fulfill({ status: 409, json: { detail: 'late' } });
    });
    let releaseOk;
    const heldOk = new Promise((r) => { releaseOk = r; });
    await page.route('**/api/runs/fx-crashed/retry', async (r) => {
      await heldOk;
      await r.fulfill({ status: 202, json: { run_id: 'fx-crashed' } });
    });
    await page.goto('/#/runs/fx-stopped');
    await page.getByTestId('action-retry').click();
    await page.getByTestId('run-fx-nocheckpoint').click();
    await expect(page.getByTestId('failure-node')).toHaveText('extract');
    release();
    await page.evaluate(() => fetch('/health'));
    await expect(page.getByTestId('action-error')).toBeHidden();
    await page.getByTestId('run-fx-crashed').click();
    await page.getByTestId('action-retry').click();
    await page.getByTestId('run-fx-nocheckpoint').click();
    await expect(page.getByTestId('failure-node')).toHaveText('extract');
    const answered = page.waitForResponse('**/api/runs/fx-crashed/retry');
    releaseOk();
    await (await answered).finished();
    await page.evaluate(() => fetch('/health'));
    await expect(page).toHaveURL(/fx-nocheckpoint$/);
  });
});

test('the banner stays out of the graph at both widths', async ({ page }) => {
  for (const width of [1280, 3440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/#/runs/fx-failed`);
    await expect(banner(page)).toBeVisible();
    const b = await banner(page).boundingBox();
    const d = await page.getByTestId('dag').boundingBox();
    expect(b.y + b.height).toBeLessThanOrEqual(d.y);
  }
});

test('a live error request reads from its fixture', async ({ page }) => {
  freshServerState({ live: true });
  await page.goto('/#/runs/fx-error');
  await expect(page.getByTestId('action-title')).toHaveText('Error in research');
  await expect(page.getByTestId('action-prompt').locator('code').first()).toHaveText('research');
  await expect(page.getByTestId('action-stop')).toBeEnabled();
});
