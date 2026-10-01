import assert from 'node:assert/strict'
import { readFileSync, mkdirSync, writeFileSync, createReadStream, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { resolve, extname } from 'node:path'
import { chromium } from 'playwright'

const desktop = fileURLToPath(new URL('../', import.meta.url))
const out = resolve(desktop, '../.runtime/execution-check')
mkdirSync(out, { recursive: true })
// Reuse the original complete bridge fixture; this check never calls a provider or Odoo.
const original = readFileSync(resolve(desktop, 'scripts/renderer-check.mjs'), 'utf8')
const bridge = original.match(/const bridgeScript = String.raw`([\s\S]*?)`[\r\n]+const browser/)[1]
const renderer = resolve(desktop, 'out/renderer')
const server = createServer((request, response) => {
  const file = resolve(renderer, '.' + new URL(request.url, 'http://localhost').pathname.replace(/^\/$/, '/index.html'))
  if (!file.startsWith(renderer + '\\')) return response.writeHead(403).end()
  try { if (!statSync(file).isFile()) return response.writeHead(404).end() } catch { return response.writeHead(404).end() }
  response.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[extname(file)] || 'application/octet-stream' })
  createReadStream(file).pipe(response)
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, recordVideo: { dir: out, size: { width: 1600, height: 1000 } } })
await context.addInitScript({ content: bridge })
const page = await context.newPage()
page.setDefaultTimeout(7000)
const errors = []
page.on('pageerror', error => errors.push(error.message))
const changed = async (businessId = 'business-b2') => page.evaluate(id => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: id } }), businessId)
const calls = method => page.evaluate(method => window.__bridgeCalls.filter(call => call.method === method), method)
try {
  await page.goto(`http://127.0.0.1:${server.address().port}/`)
  await page.getByRole('button', { name: /Session B/ }).click()
  await page.getByRole('tab', { name: /Business B2/ }).click()
  await page.evaluate(() => { window.__setRunState(true); window.__showAcceptedProjection(false) })
  await changed()
  const invoice = page.locator('#approval-action-b2')
  await invoice.waitFor({ state: 'attached' })
  assert.equal(await page.getByRole('tab', { name: '执行台', exact: true }).getAttribute('data-state'), 'active')
  assert.equal(await page.locator('.execution-approvals .approval-row').count(), 3)
  assert.equal(await page.getByRole('textbox', { name: '会话消息' }).count(), 1)
  assert.ok(await page.locator('.session-rail').isVisible())
  assert.ok((await invoice.locator('.field-diff').textContent()).includes('数量 1'))
  assert.ok((await invoice.locator('.field-diff').textContent()).includes('Nimbus Bureau'))
  for (const [width, height] of [[1600, 1000], [1280, 900]]) {
    await page.setViewportSize({ width, height })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    const controls = await page.locator('.window-actions').boundingBox()
    assert.ok(controls.x + controls.width >= width - 20, 'Window controls must stay at the upper right')
    assert.ok(controls.y < 20)

    await page.screenshot({ path: resolve(out, `execution-${width}x${height}.png`) })
  }
  await page.setViewportSize({ width: 1600, height: 1000 })
  // Archive is detail-only; pending records route back to the same execution action.
  await page.getByRole('tab', { name: /^变更与审批/ }).click()
  await page.locator('.approval-history button').filter({ hasText: '创建发票与贷项' }).click()
  assert.equal(await invoice.getByRole('button', { name: '批准这项业务动作' }).count(), 0)
  await page.screenshot({ path: resolve(out, 'approvals-1600x1000.png') })
  await page.getByRole('button', { name: '在执行台处理' }).click()
  await invoice.waitFor()
  assert.equal(await invoice.evaluate(element => document.activeElement === element), true)
  await invoice.scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(out, 'inline-approval-1600x1000.png') })
  // Repeated links must focus again without submitting anything.
  await page.locator('.business-content').evaluate(element => { element.scrollTop = element.scrollHeight })
  await page.locator('.approval-inbox-item').filter({ hasText: '创建发票与贷项' }).click()
  assert.equal(await invoice.evaluate(element => document.activeElement === element), true)
  assert.equal((await calls('decide_approval')).length, 0)
  const startBefore = (await calls('start_run')).length
  // Revision error preserves the pending action and the user's editable text.
  await invoice.getByRole('button', { name: '提出修改' }).click()
  await invoice.locator('textarea').fill('测试预检失败')
  await invoice.getByRole('button', { name: '提交修改要求' }).click()
  await invoice.getByText('修改预检失败，原审批仍保留。', { exact: true }).waitFor()
  assert.equal(await invoice.locator('textarea').inputValue(), '测试预检失败')
  await invoice.getByRole('button', { name: '取消修改' }).click()
  // Original approval context and duplicate-click protection.
  await invoice.getByRole('button', { name: '批准这项业务动作' }).evaluate(button => { for (let i = 0; i < 5; i++) button.click() })
  await page.waitForFunction(() => window.__bridgeCalls.some(call => call.method === 'decide_approval'))
  assert.deepEqual((await calls('decide_approval')).map(call => call.params), [{ session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', action_id: 'action-b2', decision: 'approve' }])
  assert.equal((await calls('start_run')).length, startBefore)
  await page.locator('#approval-action-b2-send-file').getByRole('button', { name: '批准这项业务动作' }).click()
  await page.getByRole('alert').filter({ hasText: 'DECISION_NETWORK_DOWN' }).waitFor()
  assert.ok(await page.locator('#approval-action-b2-send-file').isVisible())
  await page.getByRole('alert').getByRole('button', { name: '关闭' }).click()
  // A pending approval can expire while the page stays open.
  await page.evaluate(() => {
    const original = window.workbench.call
    window.workbench.call = async (method, params) => {
      const result = await original(method, params)
      if (method === 'get_business' && params.business_id === 'business-b2') result.approvals.find(a => a.action_id === 'action-b2').expires_at = Math.floor(Date.now() / 1000) + 2
      return result
    }
  })
  await changed()
  await invoice.getByText('已过期', { exact: true }).first().waitFor()
  assert.equal(await invoice.getByRole('button', { name: '批准这项业务动作' }).count(), 0)
  // A successful revision uses the existing breakpoint path without an extra start.
  await page.locator('#approval-action-b2-purchase').getByRole('button', { name: '提出修改' }).click()
  await page.locator('#approval-action-b2-purchase textarea').fill('保留草稿，数量改为 5 件。')
  await page.locator('#approval-action-b2-purchase').getByRole('button', { name: '提交修改要求' }).click()
  await page.waitForFunction(() => window.__revisionAccepted)
  await page.locator('.approval-row.pending').first().waitFor({ state: 'hidden' })
  assert.equal((await calls('start_run')).length, startBefore)
  // Streaming uses original scoped events. It never mixes another business's text.
  await page.getByRole('tab', { name: /Business B4/ }).click()
  await page.evaluate(() => { window.__activityScenario = { phase: 'model', label: '模型处理中', detail: '核对付款状态。' }; window.__setRunState(true) })
  await changed('business-b4')
  await page.locator('.activity-card').getByText('模型处理中', { exact: true }).waitFor()
  assert.equal(await page.locator('.activity-card').count(), 1, 'Business and phase changes must retain exactly one current action')
  await page.evaluate(() => {
    window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b4', run_id: 'business-b4-run', message_id: 'v2-live', sequence: 0, text: '公开业务进展。\n\n' + '核对已有单据。\n\n'.repeat(90) } })
    window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', message_id: 'v2-wrong', sequence: 0, text: '错误业务不得混入。' } })
  })
  const reply = page.locator('.activity-public-text')
  await page.waitForFunction(() => { const e = document.querySelector('.activity-public-text'); return e && e.clientHeight > 1000 && Math.abs(e.scrollHeight - e.clientHeight) < 3 })
  assert.equal((await reply.textContent()).includes('错误业务'), false)
  await page.locator('.business-content').evaluate(e => { e.scrollTop = 0; e.dispatchEvent(new Event('scroll')) })
  await page.evaluate(() => window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b4', run_id: 'business-b4-run', message_id: 'v2-live', sequence: 1, text: '\n新进展一。' } }))
  await page.getByRole('button', { name: '有新进展 ↓', exact: true }).waitFor()
  assert.equal(await reply.evaluate(e => e.scrollTop), 0)
  await page.getByRole('button', { name: '有新进展 ↓', exact: true }).click()
  await page.waitForFunction(() => { const e = document.querySelector('.activity-public-text'); return e.scrollHeight - e.scrollTop - e.clientHeight < 3 })
  await page.evaluate(() => window.__emitWorkbench({ event: 'message_end', data: { session_id: 'session-b', business_id: 'business-b4', run_id: 'business-b4-run', message_id: 'v2-live', sequence: 2, text: '业务进展最终公开回复。' } }))
  await reply.getByText('业务进展最终公开回复。', { exact: true }).waitFor()
  await page.evaluate(() => window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b4', run_id: 'business-b4-run', message_id: 'v2-live', sequence: 3, text: '终态后错误追加。' } }))
  assert.equal((await reply.textContent()).includes('终态后'), false)
  // Unknown writes offer readback and retain the action ledger; no run restart.
  await page.getByRole('tab', { name: /Business B3/ }).click()
  await page.getByRole('button', { name: '执行已暂停', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '执行已暂停', exact: true }).isDisabled(), true)
  const startsAtUnknown = (await calls('start_run')).length
  await page.locator('#approval-action-b3').getByRole('button', { name: '核对不确定写入' }).click()
  await page.waitForFunction(() => window.__bridgeCalls.some(call => call.method === 'reconcile_action'))
  assert.equal((await calls('start_run')).length, startsAtUnknown)
  // Original documents, settings and conversation shell remain functional.
  await page.getByRole('tab', { name: /Business B2/ }).click()
  await page.getByRole('tab', { name: /^单据/ }).click()
  await page.getByRole('button', { name: /^PO-B2 采购订单/ }).click()
  await page.getByRole('heading', { name: 'PO-B2', exact: true }).waitFor()
  await page.getByRole('tab', { name: /^运行详情/ }).click()
  await page.locator('.trace-page').waitFor()
  await page.screenshot({ path: resolve(out, 'trace-1600x1000.png') })
  await page.getByRole('button', { name: '连接设置', exact: true }).click()
  await page.getByRole('checkbox', { name: '深色外观', exact: true }).check()
  assert.equal(await page.evaluate(() => document.documentElement.dataset.appearance), 'dark')
  await page.keyboard.press('Escape')
  await page.screenshot({ path: resolve(out, 'trace-dark-1600x1000.png') })
  // Check the known low-contrast approval surface in dark mode, not only the theme flag.
  await page.evaluate(() => { window.__revisionAccepted = false; window.__setRunState(true) })
  await changed()
  await page.getByRole('tab', { name: '执行台', exact: true }).click()
  await invoice.waitFor()
  await invoice.scrollIntoViewIfNeeded()
  const diffColors = await invoice.locator('.diff-value.after').first().evaluate(element => ({ foreground: getComputedStyle(element).color, background: getComputedStyle(element).backgroundColor }))
  assert.notEqual(diffColors.background, 'rgb(245, 251, 250)')
  await page.screenshot({ path: resolve(out, 'execution-dark-1600x1000.png') })
  // Mail approval must show the actual recipient and literal content before any decision.
  await page.evaluate(() => {
    const original = window.workbench.call
    window.workbench.call = async (method, params) => {
      const result = await original(method, params)
      if (method === 'get_business' && params.business_id === 'business-b2') {
        const action = result.approvals[0]
        action.operation = 'message_post'
        action.model = 'account.move'
        action.values = { subject: '发票 INV/2026/0042', body: '您好，随信附上本次发票。\n请核对金额和附件。' }
        action.prestate = { invoice_mail: { invoice: { name: 'INV/2026/0042', amount_total: 2061.12 }, company: { name: '澄川工业', email: 'billing@example.com' }, recipient: { name: 'Nimbus Bureau' }, email_from: 'billing@example.com', email_to: 'customer@example.com', attachment: { name: 'INV-2026-0042.pdf', checksum: '0123456789abcdef' }, ...action.values } }
        action.expires_at = Math.floor(Date.now() / 1000) + 600
        result.approvals = [action]
      }
      return result
    }
  })
  await changed()
  const mail = page.getByRole('region', { name: '待发送邮件', exact: true })
  await mail.waitFor()
  assert.ok((await mail.textContent()).includes('customer@example.com'))
  assert.ok((await mail.textContent()).includes('请核对金额和附件。'))
  await invoice.scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(out, 'mail-approval-dark-1600x1000.png') })
  await page.getByRole('button', { name: '连接设置', exact: true }).click()
  await page.getByRole('checkbox', { name: '深色外观', exact: true }).uncheck()
  await page.keyboard.press('Escape')
  await invoice.scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(out, 'mail-approval-light-1600x1000.png') })
  await page.getByRole('button', { name: '连接设置', exact: true }).click()
  await page.getByRole('checkbox', { name: '深色外观', exact: true }).check()
  await page.keyboard.press('Escape')
  await page.reload()
  await page.getByRole('button', { name: /Session B/ }).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.dataset.appearance), 'dark')
  await page.getByRole('button', { name: /Session B/ }).click()
  await page.getByRole('tab', { name: /Business B4/ }).click()
  await page.locator('.business-header h2').getByText('Business B4', { exact: true }).waitFor()
  await page.evaluate(() => {
    const start = document.startViewTransition.bind(document)
    window.__viewTransitions = []
    document.startViewTransition = update => {
      const transition = start(update)
      const record = { ready: false, finished: false }
      window.__viewTransitions.push(record)
      transition.ready.then(() => { record.ready = true; record.groups = [...new Set(document.getAnimations().map(animation => animation.effect?.pseudoElement).filter(name => name?.startsWith('::view-transition-group(')))] }).catch(() => { record.skipped = true })
      transition.finished.then(() => { record.finished = true }).catch(() => { record.failed = true })
      return transition
    }
  })
  let headerBounds
  for (const label of ['单据与文件', '变更与审批', '运行详情', '执行台']) {
    await page.getByRole('tab', { name: new RegExp(`^${label}`) }).click()
    await page.waitForFunction(() => window.__viewTransitions.at(-1)?.finished)
    assert.ok(await page.evaluate(() => window.__viewTransitions.at(-1).ready), 'Native transition must actually render')
    assert.deepEqual(await page.evaluate(() => window.__viewTransitions.at(-1).groups), ['::view-transition-group(workspace)'], 'Navigation must animate one workspace snapshot, without child snapshots')
    const bounds = await page.locator('.business-header').boundingBox()
    if (headerBounds) assert.deepEqual(bounds, headerBounds, 'All four pages must keep the same shared header')
    headerBounds = bounds
    assert.equal(await page.locator('.business-header .goal-details p').count(), 1)
  }
  await page.locator('.business-header .goal-details > summary').click()
  await page.locator('.business-header .goal-details p').waitFor()
  assert.equal(await page.locator('.business-header .goal-details p').textContent(), '确认订单，暂不发货。')
  assert.equal(await page.getByText('查看原始指令', { exact: true }).count(), 0)
  await page.locator('.business-header .goal-details > summary').click()
  await page.getByRole('button', { name: '收起会话', exact: true }).click()
  await page.locator('.conversation-pane').waitFor({ state: 'detached' })
  await page.waitForFunction(() => window.__viewTransitions.at(-1)?.finished)
  await page.getByRole('button', { name: '打开会话', exact: true }).click()
  await page.locator('.conversation-pane').waitFor()
  await page.waitForFunction(() => window.__viewTransitions.at(-1)?.finished)
  await page.getByRole('button', { name: '收起会话栏', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.workspace-grid').classList.contains('rail-collapsed') && window.__viewTransitions.at(-1)?.finished)
  await page.getByRole('button', { name: '展开会话栏', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.workspace-grid').classList.contains('rail-collapsed') && window.__viewTransitions.at(-1)?.finished)
  assert.ok(await page.locator('.composer').evaluate(element => element.getBoundingClientRect().height < 130), 'Composer should leave space for the conversation')
  const searchColors = await page.locator('.session-search').evaluate(element => ({ parent: getComputedStyle(element).backgroundColor, input: getComputedStyle(element.querySelector('input')).backgroundColor }))
  assert.equal(searchColors.input, 'rgba(0, 0, 0, 0)')
  await page.screenshot({ path: resolve(out, 'compact-dark-1600x1000.png') })
  await page.getByRole('button', { name: '连接设置', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '连接设置' })
  await dialog.waitFor()
  assert.ok((await dialog.evaluate(element => getComputedStyle(element).animationName)).includes('rt-dialog-content-show'))
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
  for (const label of ['单据与文件', '变更与审批', '执行台']) await page.getByRole('tab', { name: new RegExp(`^${label}`) }).click()
  await page.waitForFunction(() => document.querySelector('.business-page-tabs [role="tab"][aria-selected="true"]')?.textContent?.startsWith('执行台') && window.__viewTransitions.every(record => record.finished))
  assert.equal(await page.locator('.business-header h2').textContent(), 'Business B4')
  const transitionCount = await page.evaluate(() => window.__viewTransitions.length)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.getByRole('button', { name: '收起会话', exact: true }).click()
  await page.locator('.conversation-pane').waitFor({ state: 'detached' })
  await page.getByRole('button', { name: '打开会话', exact: true }).click()
  await page.locator('.conversation-pane').waitFor()
  assert.equal(await page.evaluate(() => window.__viewTransitions.length), transitionCount, 'Reduced motion must bypass snapshots')
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.evaluate(() => { document.startViewTransition = undefined })
  await page.getByRole('tab', { name: /^运行详情/ }).click()
  await page.locator('.trace-page').waitFor()
  assert.equal(await page.evaluate(() => window.__viewTransitions.length), transitionCount, 'Unsupported browsers must retain navigation')
  assert.deepEqual(errors, [])
  const result = { passed: true, page_errors: 0, fixture_only: true, provider_calls: 0, odoo_writes: 0, checks: ['original session/chat/documents', 'inline approvals and archive routing', 'duplicate focus/decision', 'revision success/failure', 'decision failure', 'expiry tick', 'unknown-write no restart', 'SSE scope/end dedup/follow', '1280/1600 layout', 'mail recipient and literal content', 'persisted dark setting', 'shared header on four pages', 'native page/panel transitions', 'Radix dialog presence', 'compact composer', 'reduced-motion and unsupported fallback'] }
  writeFileSync(resolve(out, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
} catch (error) {
  await page.screenshot({ path: resolve(out, 'failure.png') }).catch(() => {})
  throw error
} finally { await context.close(); await page.video().saveAs(resolve(out, 'execution-interactions.webm')); await browser.close(); await new Promise(resolve => server.close(resolve)) }
