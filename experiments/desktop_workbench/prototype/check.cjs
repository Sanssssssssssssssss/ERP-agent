const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require(path.resolve(__dirname, '../../../desktop/node_modules/playwright'));

const states = ['ready', 'running', 'approval', 'success', 'failed', 'uncertain'];
const pages = ['execution', 'documents', 'approval', 'trace'];
const sizes = [{ width: 1280, height: 800 }, { width: 1600, height: 1000 }];
const out = path.resolve(__dirname, '../../../.runtime/workbench-prototype');
const file = pathToFileURL(path.join(__dirname, 'index.html')).href;

const results = { combinations: 0, screenshots: [], errors: [], network: [], pageErrors: [], consoleErrors: [], downloads: [] };
const check = async (label, fn) => { try { await fn(); } catch (error) { results.errors.push(`${label}: ${error.message}`); } };
const active = async (page, selector) => page.locator(`${selector}.active, ${selector}[aria-selected="true"]`).count();
const bodyText = page => page.locator('body').innerText();
const assertLayout = async page => {
  const overflow = await page.evaluate(() => [...document.querySelectorAll('#appShell,.business-main,#content,.content-scroll,.resource-layout,.resource-list,.resource-preview,.approval-layout,.approval-card,.history-card,.trace-layout,.run-tree,.trace-detail,.step-layout,.step-list,.step-detail')]
    .filter(el => getComputedStyle(el).display !== 'none' && el.clientWidth)
    .filter(el => el.scrollWidth > el.clientWidth + 2 && !['auto', 'scroll'].includes(getComputedStyle(el).overflowX))
    .map(el => el.id || el.className));
  assert.deepEqual(overflow, []);
};

async function exercise(page, size) {
  await page.goto(file, { waitUntil: 'load' });
  await check(`${size.width}: initial layout`, async () => {
    assert.equal(await page.locator('#content').count(), 1);
    await assertLayout(page);
  });
  for (const state of states) {
    await check(`${size.width}: state ${state}`, async () => {
      await page.locator(`[data-state="${state}"]`).click();
      assert.equal(await page.locator(`[data-state="${state}"][aria-selected="true"]`).count(), 1);
      await page.locator('[data-page="execution"]').click();
      await assertLayout(page);
      await page.screenshot({ path: path.join(out, `${size.width}x${size.height}-${state}.png`), fullPage: false });
      results.screenshots.push(`${size.width}x${size.height}-${state}.png`);
    });
    for (const view of pages) await check(`${size.width}: ${state}/${view}`, async () => {
      await page.locator(`[data-page="${view}"]`).click();
      assert.equal(await active(page, `[data-page="${view}"]`), 1);
      assert.ok((await page.locator('#content').innerText()).trim());
      await assertLayout(page);
      results.combinations += 1;
    });
  }
  await page.locator('[data-state="success"]').click();
  for (const view of pages) {
    await page.locator(`[data-page="${view}"]`).click();
    const name = `${size.width}x${size.height}-success-${view}.png`;
    await page.screenshot({ path: path.join(out, name), fullPage: false });
    results.screenshots.push(name);
  }
  await check(`${size.width}: session and business navigation`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="running"]').click();
    assert.equal(await page.locator('#modeLabel').innerText(), '执行中');
    await page.locator('.business-tab[data-business="readonly"]').click();
    assert.equal(await active(page, '.business-tab[data-business="readonly"]'), 1);
    assert.equal(await page.locator('#modeLabel').innerText(), '只读示例');
    await page.locator('.business-tab[data-business="sales"]').click();
    assert.equal(await active(page, '.business-tab[data-business="sales"]'), 1);
    assert.equal(await page.locator('#modeLabel').innerText(), '执行中');
    const visible = () => page.locator('#assistantPanel').evaluate(el => getComputedStyle(el).display !== 'none');
    if (!await visible()) await page.locator('#assistantToggle').click();
    assert.equal(await visible(), true);
    const before = await page.locator('.business-main').evaluate(el => el.getBoundingClientRect().width);
    await page.locator('#assistantToggle').click();
    assert.equal(await visible(), false);
    const after = await page.locator('.business-main').evaluate(el => el.getBoundingClientRect().width);
    assert.ok(after > before);
    await page.locator('#assistantToggle').click();
    assert.equal(await visible(), true);
    const railInitiallyCollapsed = (await page.locator('#sessionRail').getAttribute('class')).includes('collapsed');
    await page.locator('#railToggle').click();
    assert.equal((await page.locator('#sessionRail').getAttribute('class')).includes('collapsed'), !railInitiallyCollapsed);
    await page.locator('#railToggle').click();
    assert.equal((await page.locator('#sessionRail').getAttribute('class')).includes('collapsed'), railInitiallyCollapsed);
  });
  await check(`${size.width}: keyboard focus`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="ready"]').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.locator(':focus[data-state="running"]').count(), 1);
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('[data-state="running"][aria-selected="true"]').count(), 1);
    await page.locator('[data-state="ready"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator(':focus[data-state="running"]').count(), 1);
    assert.equal(await page.locator('[data-state="running"][aria-selected="true"]').count(), 1);
    await page.locator('#messageInput').focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.locator('[data-page="execution"].active').count(), 1);
  });
  await check(`${size.width}: approval is not business success`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="approval"]').click();
    await page.locator('[data-page="approval"]').click();
    const approve = page.getByRole('button', { name: /批准|同意/ }).first();
    assert.equal(await approve.count(), 1);
    await approve.click();
    const text = await bodyText(page);
    assert.equal(await page.locator('[data-state="success"][aria-selected="true"]').count(), 0);
    assert.equal(await page.getByRole('button', { name: /同意订单确认|批准/ }).count(), 0);
    assert.match(text, /未执行|执行中|审批/);
  });
  await check(`${size.width}: rejection explains result`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="approval"]').click();
    await page.locator('[data-page="approval"]').click();
    const reject = page.getByRole('button', { name: /拒绝/ }).first();
    assert.equal(await reject.count(), 1);
    await reject.click();
    const reason = '先核对付款条件';
    await page.locator('#rejectInput').fill(reason);
    await page.getByRole('button', { name: /确认拒绝/ }).click();
    const text = await bodyText(page);
    assert.equal(await page.locator('[data-state="failed"][aria-selected="true"]').count(), 1);
    assert.match(text, new RegExp(`拒绝|已拒绝|${reason}`));
    assert.match(text, /没有执行订单确认|草稿/);
    assert.doesNotMatch(text, /开票过账未完成/);
  });
  await check(`${size.width}: uncertain write is readback only`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="uncertain"]').click();
    await page.locator('[data-page="execution"]').click();
    assert.match(await bodyText(page), /核对/);
    assert.equal(await page.locator('#content [data-write-action], #content [data-action="write"], #content #approveAction, #content #rejectAction, #content #submitReject, #content #startDemo').count(), 0);
    assert.equal(await page.locator('#reconcile').count(), 1);
    await page.locator('#reconcile').click();
    assert.equal(await page.locator('[data-state="success"][aria-selected="true"]').count(), 1);
    await page.locator('[data-page="documents"]').click();
    await page.locator('[data-resource="invoice"]').click();
    assert.match(await page.locator('.resource-preview').innerText(), /已过账/);
    await page.locator('[data-page="trace"]').click();
    assert.equal(await page.locator('[data-tool="tool-read-invoice"].active').count(), 1);
  });
  await check(`${size.width}: resource and receipt navigation`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="success"]').click();
    await page.locator('[data-page="documents"]').click();
    for (const [resourceId, toolId] of [['so', 'tool-confirm'], ['invoice', 'tool-post'], ['result', 'tool-read-invoice']]) {
      await page.locator(`[data-resource="${resourceId}"]`).click();
      assert.ok((await page.locator('.resource-preview').innerText()).trim());
      const receipt = page.locator('.resource-preview [data-receipt-tool], .resource-preview [data-jump-tool]').first();
      assert.equal(await receipt.count(), 1);
      assert.equal(await receipt.getAttribute('data-receipt-tool') || await receipt.getAttribute('data-jump-tool'), toolId);
      await receipt.click();
      assert.equal(await active(page, '[data-page="trace"]'), 1);
      assert.equal(await page.locator(`[data-tool="${toolId}"].active`).count(), 1);
      await page.locator('[data-page="documents"]').click();
    }
  });
  await check(`${size.width}: sample markdown download`, async () => {
    await page.reload({ waitUntil: 'load' });
    await page.locator('[data-state="success"]').click();
    await page.locator('[data-page="documents"]').click();
    await page.locator('[data-resource="result"]').click();
    const downloadLink = page.getByRole('link', { name: /下载/ }).first();
    assert.equal(await downloadLink.count(), 1);
    const download = await Promise.all([page.waitForEvent('download'), downloadLink.click()]).then(([item]) => item);
    const downloadPath = await download.path();
    const content = await fs.readFile(downloadPath, 'utf8');
    assert.match(content, /示例/);
    results.downloads.push(download.suggestedFilename());
  });
}

async function main() {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const size of sizes) {
      const context = await browser.newContext({ viewport: size });
      await context.route('**/*', async route => {
        const url = route.request().url();
        if (/^(https?|ws|wss):/i.test(url)) { results.network.push(url); await route.abort(); return; }
        await route.continue();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(5000);
      page.on('pageerror', error => results.pageErrors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') results.consoleErrors.push(message.text()); });
      await exercise(page, size);
      await context.close();
    }
  } finally {
    await browser.close();
    results.expected_combinations = 48;
    results.ok = results.combinations === 48 && !results.errors.length && !results.network.length && !results.pageErrors.length && !results.consoleErrors.length;
    await fs.writeFile(path.join(out, 'check.json'), JSON.stringify(results, null, 2));
  }
  if (!results.ok) throw new Error(`prototype checks failed; see ${path.join(out, 'check.json')}`);
  console.log(JSON.stringify(results, null, 2));
}

main().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; });
