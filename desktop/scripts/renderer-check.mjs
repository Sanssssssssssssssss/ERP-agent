import assert from 'node:assert/strict'
import { createReadStream, existsSync, mkdirSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { extname, resolve } from 'node:path'
import { chromium } from 'playwright'

const renderer = new URL('../out/renderer/index.html', import.meta.url)
assert.ok(existsSync(renderer), `build output is missing: ${renderer.pathname}`)
const rendererRoot = fileURLToPath(new URL('../out/renderer/', import.meta.url))
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }
const server = createServer((request, response) => {
  const requestPath = decodeURIComponent(new URL(request.url || '/', 'http://127.0.0.1').pathname)
  const file = resolve(rendererRoot, `.${requestPath === '/' ? '/index.html' : requestPath}`)
  if (!file.startsWith(resolve(rendererRoot)) || !statSafe(file)) {
    response.writeHead(404).end()
    return
  }
  response.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream' })
  createReadStream(file).pipe(response)
})
const statSafe = (file) => { try { return statSync(file).isFile() } catch { return false } }
server.unref()
await new Promise((resolveServer) => server.listen(0, '127.0.0.1', resolveServer))
const serverAddress = server.address()
assert.ok(serverAddress && typeof serverAddress === 'object')

const bridgeScript = String.raw`
  (() => {
    const listeners = new Set()
    const calls = []
    let traceVersion = 0
    let saveBlocked = true
    let failInitialConnectionCheck = true
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
    const session = (id, title, businesses) => ({ id, title, created_at: '2026-09-08T08:00:00Z', updated_at: '2026-09-08T09:00:00Z', archived: false, status: 'idle', businesses })
    const business = (id, session_id, title, status = 'idle') => ({ id, session_id, type: 'sale_invoice', title, goal: '处理订单与发票', status, created_at: '2026-09-08T08:00:00Z', updated_at: '2026-09-08T09:00:00Z', active_run_id: id + '-run' })
    const b1 = business('business-b1', 'session-b', 'Business B1')
    const b2 = business('business-b2', 'session-b', 'Business B2', 'awaiting_approval')
    const sessions = [session('session-a', 'Session A', [business('business-a1', 'session-a', 'Business A1')]), session('session-b', 'Session B', [b1, b2])]
    const details = (b) => ({
      business: b,
      runs: [{ id: b.id + '-old-run', business_id: b.id, session_id: b.session_id, status: 'completed', started_at: '2026-09-08T08:00:00Z', tool_count: 1, model_rounds: 1, elapsed_seconds: 0.8, verification_status: 'passed', usage: { input: null, cache_read: null, output: null, reasoning: null, total: null } }, { id: b.id + '-run', business_id: b.id, session_id: b.session_id, status: b.id === 'business-b2' ? 'awaiting_approval' : 'completed', started_at: '2026-09-08T08:30:00Z', tool_count: 2, model_rounds: 2, elapsed_seconds: 1.2, verification_status: b.id === 'business-b2' ? '未知' : 'passed', usage: { input: null, cache_read: null, output: null, reasoning: null, total: null } }],
      approvals: b.id === 'business-b2' ? [{ action_id: 'action-b2', run_id: 'business-b2-run', business_id: 'business-b2', status: 'pending_approval', title: 'ERP write approval', model: 'account.move', operation: 'create', record_ids: [42], values: { state: 'posted' }, prestate: { state: 'draft' }, result: { ok: true, preflight: true }, expires_at: Math.floor(Date.now() / 1000) + 60 }] : [],
      documents: b.id === 'business-b2' ? [
        { id: '1', model: 'sale.order', name: 'SO-B2', state: 'sale', source: 'odoo', observed_at: '2026-09-08T08:45:00Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', invoice_status: 'invoiced', delivery_status: 'pending' } },
        { id: '2', model: 'sale.order', name: 'SO-B2-SECOND', state: 'draft', source: 'odoo', observed_at: '2026-09-08T08:45:30Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 10, currency: 'USD', invoice_status: 'to invoice' } },
        { id: '1', model: 'account.move', name: 'INV-B2', state: 'posted', source: 'odoo', observed_at: '2026-09-08T08:46:00Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', payment_state: 'not_paid', amount_residual: 695.22 } }
      ] : [],
      checks: [], stale: false, observed_at: '2026-09-08T08:46:00Z', summary: '主机已返回业务回执'
    })
    const sessionDetails = {
      'session-a': { session: sessions[0], messages: [], businesses: sessions[0].businesses },
      'session-b': { session: sessions[1], messages: [{ id: 'm1', role: 'assistant', text: '已识别两个业务工作区。', created_at: '2026-09-08T08:01:00Z' }, { id: 'proposal-1', role: 'assistant', text: '发现一个新的业务意图。', created_at: '2026-09-08T08:02:00Z', proposal: { id: 'proposal-1', title: '新业务意图', goal: '处理一笔新的销售业务', type: 'sale_invoice', status: 'pending' } }], businesses: sessions[1].businesses }
    }
    window.__bridgeCalls = calls
    window.__traceVersion = 0
    window.__emitWorkbench = (event) => listeners.forEach((listener) => listener(event))
    window.workbench = {
      subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
      windowControl() { return Promise.resolve() },
      async call(method, params = {}) {
        calls.push({ method, params })
        if (method === 'health') return { host_ready: true, odoo_status: 'connected', odoo: { status: 'connected', endpoint: 'http://odoo.invalid', database: 'demo', account: 'admin', checked_at: '2026-09-08T09:00:00Z', latency_ms: 12 }, model_configured: true, environment: 'demo' }
        if (method === 'check_connection') {
          if (failInitialConnectionCheck) { failInitialConnectionCheck = false; throw new Error('CHECK_CONNECTION_OFFLINE') }
          return { host_ready: true, odoo_status: 'connected', odoo: { status: 'connected', endpoint: 'http://odoo.invalid', database: 'demo', account: 'admin', checked_at: '2026-09-08T09:00:00Z', latency_ms: 12 }, model_configured: true, environment: 'demo' }
        }
        if (method === 'list_sessions') return sessions
        if (method === 'get_session') {
          await wait(params.session_id === 'session-a' ? 220 : 12)
          return sessionDetails[params.session_id]
        }
        if (method === 'get_business' || method === 'refresh_business') {
          const id = params.business_id
          await wait(id === 'business-b1' ? 180 : 12)
          return details(sessions.flatMap((item) => item.businesses).find((item) => item.id === id))
        }
        if (method === 'get_trace') {
          await wait(params.business_id === 'business-b1' ? 12 : 220)
          if (params.business_id === 'business-b1') return { run: details(b1).runs[0], rounds: [], tools: [] }
          traceVersion += 1
          window.__traceVersion = traceVersion
          return { run: details(b2).runs[0], rounds: [{ index: 1, status: 'completed', text: '公开业务摘要 '.repeat(500), elapsed_seconds: null, usage: { input: null, cache_read: null, output: null, reasoning: null, total: null }, tool_ids: ['tool-b2'] }], tools: [{ id: 'tool-b2', name: 'read_business_records', status: 'completed', arguments: { model: 'sale.order' }, result: { count: 2, version: traceVersion }, elapsed_seconds: null }] }
        }
        if (method === 'decide_approval') return { ok: true }
        if (method === 'get_settings') return { model: 'deepseek/deepseek-v4-flash/high', base_url: 'http://model.invalid', odoo_url: 'http://odoo.invalid', odoo_db: 'demo', odoo_username: 'admin', has_model_key: false, has_odoo_key: false, environment: 'demo' }
        if (method === 'save_settings') {
          if (saveBlocked) { saveBlocked = false; throw new Error("Error invoking remote method 'workbench:call': Error: CONFIG_BUSY") }
          return { model: 'deepseek/deepseek-v4-flash/high', base_url: 'http://model.invalid', odoo_url: 'http://odoo.invalid', odoo_db: 'demo', odoo_username: 'admin', has_model_key: false, has_odoo_key: false, environment: 'demo' }
        }
        if (method === 'send_message') {
          if (params.text === 'delayed mutation') await wait(180)
          return null
        }
        if (method === 'start_run') return { id: params.business_id + '-started-run', business_id: params.business_id, session_id: params.session_id, status: 'running', tool_count: 0, model_rounds: 0, elapsed_seconds: 0 }
        if (method === 'cancel_run' || method === 'confirm_business' || method === 'rename_session' || method === 'archive_session') return null
        throw new Error('unexpected bridge method: ' + method)
      }
    }
  })()
`

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.addInitScript({ content: bridgeScript })
const page = await context.newPage()
const pageErrors = []
page.on('pageerror', (error) => { pageErrors.push(error.message); console.error(`renderer page error: ${error.message}`) })
page.on('console', (message) => { if (message.type() === 'error') console.error(`renderer console error: ${message.text()}`) })
await page.goto(`http://127.0.0.1:${serverAddress.port}/`)
await page.getByRole('button', { name: /Session A/ }).waitFor()
await page.getByText('主机断开').waitFor()

// A is deliberately slow. Switching to B while A is in flight must leave B visible.
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
const proposalButton = page.getByRole('button', { name: '创建业务工作区' })
await proposalButton.waitFor()
await page.evaluate(() => {
  const create = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('创建业务工作区'))
  const cancel = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('暂不创建'))
  for (let index = 0; index < 5; index += 1) (index % 2 === 0 ? create : cancel)?.click()
})
await page.waitForTimeout(25)
const proposalCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'confirm_business'))
assert.equal(proposalCalls.length, 1)
await page.getByRole('button', { name: /Session B/ }).click()
await page.waitForTimeout(30)
assert.equal(await page.locator('.conversation-header h2').textContent(), 'Session B')
assert.equal(await page.locator('.conversation-header h2').textContent(), 'Session B')

// Pressure: 50 rapid session switches plus an explicit A -> B -> A must settle on the last scope.
const sessionPressureStarted = Date.now()
const sessionBurstDispatches = await page.evaluate(() => {
  const find = (title) => [...document.querySelectorAll('.session-select')].find((button) => button.textContent?.includes(title))
  let dispatched = 0
  for (let index = 0; index < 50; index += 1) { const button = find(index % 2 === 0 ? 'Session A' : 'Session B'); if (!button) throw new Error('missing session switch button'); button.click(); dispatched += 1 }
  return dispatched
})
assert.equal(sessionBurstDispatches, 50)
await page.getByRole('heading', { name: 'Session B' }).waitFor()
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
const sessionPressureElapsed = Date.now() - sessionPressureStarted

// Pressure: 50 business tab switches must leave the last selected business visible.
const businessPressureStarted = Date.now()
const businessBurstDispatches = await page.evaluate(() => {
  const find = (title) => [...document.querySelectorAll('[role="tab"]')].find((button) => button.textContent?.includes(title))
  let dispatched = 0
  for (let index = 0; index < 50; index += 1) { const button = find(index % 2 === 0 ? 'Business B1' : 'Business B2'); if (!button) throw new Error('missing business switch button'); button.click(); dispatched += 1 }
  return dispatched
})
assert.equal(businessBurstDispatches, 50)
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
const businessPressureElapsed = Date.now() - businessPressureStarted
const sequentialBusinessStarted = Date.now()
for (let index = 0; index < 20; index += 1) {
  const title = index % 2 === 0 ? 'Business B1' : 'Business B2'
  await page.getByRole('tab', { name: new RegExp(title) }).click()
  await page.getByRole('heading', { name: title }).waitFor()
}
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
const sequentialBusinessElapsed = Date.now() - sequentialBusinessStarted

// Composer target is explicit: current-business followups carry a business_id; a new intent omits it.
const messageTarget = page.getByLabel('发送到')
const composer = page.getByRole('textbox', { name: '会话消息' })
await messageTarget.selectOption('business-b2')
await composer.fill('继续处理当前业务')
await page.getByRole('button', { name: '发送' }).click()
await page.waitForTimeout(25)
await messageTarget.selectOption('__new__')
await composer.fill('开始一个新的业务意图')
await page.getByRole('button', { name: '发送' }).click()
await page.waitForTimeout(25)
const sentMessages = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'send_message'))
assert.deepEqual(sentMessages.map(({ params }) => params), [
  { session_id: 'session-b', text: '继续处理当前业务', business_id: 'business-b2' },
  { session_id: 'session-b', text: '开始一个新的业务意图' }
])
await messageTarget.selectOption('business-b2')
await composer.fill('dedupe send')
await page.evaluate(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('发送'))
  for (let index = 0; index < 5; index += 1) button?.click()
})
await page.waitForTimeout(25)
const dedupeSendCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'send_message' && params.text === 'dedupe send'))
assert.equal(dedupeSendCalls.length, 1)

// A delayed send response from the old session must not reload that session over a newer selection.
await messageTarget.selectOption('business-b2')
await composer.fill('delayed mutation')
await page.getByRole('button', { name: '发送' }).click()
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
await page.waitForTimeout(220)
assert.equal(await page.locator('.conversation-header h2').textContent(), 'Session A')
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()

// Search stays inside the session rail and does not alter the selected session.
const sessionSearch = page.getByRole('textbox', { name: '搜索会话' })
await sessionSearch.fill('Session A')
assert.equal(await page.getByRole('button', { name: /Session B/ }).isVisible(), false)
await sessionSearch.fill('')
await page.getByRole('button', { name: '归档' }).click()
await page.getByRole('alertdialog', { name: '归档会话' }).waitFor()
await page.getByRole('alertdialog', { name: '归档会话' }).getByRole('button', { name: '取消' }).click()
await page.getByRole('alertdialog', { name: '归档会话' }).waitFor({ state: 'hidden' })

// B1 is deliberately slow. B2 must win the business detail race.
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('tab', { name: /Business B1/ }).click()
await page.getByText('正在读取业务状态…').waitFor()
await page.getByRole('heading', { name: 'Business B1' }).waitFor()
const startButton = page.getByRole('button', { name: /继续执行|开始执行/ })
await startButton.waitFor()
await page.evaluate(() => {
  const button = [...document.querySelectorAll('button')].find((item) => /继续执行|开始执行/.test(item.textContent || ''))
  for (let index = 0; index < 5; index += 1) button?.click()
})
await page.waitForTimeout(30)
const startCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'start_run'))
assert.equal(startCalls.length, 1)
assert.equal(await page.getByRole('button', { name: '取消运行' }).count(), 0)
assert.equal(await page.locator('.tool-row').count(), 0)
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')
assert.ok((await page.locator('.business-facts').textContent()).includes('已确认'))
assert.ok((await page.locator('.business-facts').textContent()).includes('已过账'))
assert.ok((await page.locator('.business-facts').textContent()).includes('2 张'))
assert.ok((await page.locator('.business-facts').textContent()).includes('SO-B2-SECOND'))
await page.getByRole('tab', { name: /^概览/ }).click()
const cancelButton = page.getByRole('button', { name: '取消运行' })
await cancelButton.waitFor()
await page.evaluate(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('取消运行'))
  for (let index = 0; index < 5; index += 1) button?.click()
})
await page.waitForTimeout(30)
const cancelCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'cancel_run'))
assert.equal(cancelCalls.length, 1)
await page.getByRole('tab', { name: /^单据/ }).click()
const documentRows = page.locator('.document-table tbody tr')
const secondOrderText = await documentRows.nth(1).textContent()
const invoiceText = await documentRows.nth(2).textContent()
assert.ok(secondOrderText?.includes('开票:待开票'))
assert.ok(!secondOrderText?.includes('付款'))
assert.ok(invoiceText?.includes('付款:未付款'))
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')
assert.equal(await page.getByText('正在读取业务状态…').count(), 0)
await page.getByRole('tab', { name: /^Trace/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-old-run')
await page.getByRole('tab', { name: /^概览/ }).click()
await page.getByRole('button', { name: '取消运行' }).waitFor()

// An unrelated changed event refreshes the session list, but does not steal the active business.
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-a', business_id: 'business-a1', status: 'completed' } }))
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: 'business-b1', run_status: 'running' } }))
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')

// Verify both approval decisions carry the currently selected business and its run.
await page.getByRole('tab', { name: /^审批/ }).click()
await page.getByText('创建客户发票', { exact: true }).waitFor()
await page.getByText('查看拟提交值与执行前状态').click()
await page.getByText('拟提交值', { exact: true }).waitFor()
await page.getByText('执行前状态', { exact: true }).waitFor()
await page.getByText('预检 / 动作回执（未执行）').waitFor()
const approvalButton = page.getByRole('button', { name: '批准这项业务动作' })
await approvalButton.waitFor()
await page.waitForFunction(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('批准这项业务动作'))
  return Boolean(button && !button.disabled)
})
await page.evaluate(() => {
  const approve = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('批准这项业务动作'))
  const reject = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('拒绝'))
  for (let index = 0; index < 5; index += 1) (index % 2 === 0 ? approve : reject)?.click()
})
await page.waitForTimeout(30)
const repeatedApproveCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'decide_approval' && params.decision === 'approve'))
const repeatedRejectCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'decide_approval' && params.decision === 'reject'))
assert.equal(repeatedApproveCalls.length, 1)
assert.equal(repeatedRejectCalls.length, 0)
await page.getByRole('button', { name: '拒绝' }).click()
await page.waitForTimeout(30)
const approvalCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'decide_approval'))
assert.deepEqual(approvalCalls.map(({ params }) => params), [
  { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', action_id: 'action-b2', decision: 'approve' },
  { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', action_id: 'action-b2', decision: 'reject' }
])

// A delayed trace must not leak into another business; the final trace displays null usage as unknown.
await page.getByRole('tab', { name: /^Trace/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-run')
await page.getByRole('tab', { name: /Business B1/ }).click()
await page.getByRole('heading', { name: 'Business B1' }).waitFor()
assert.equal(await page.locator('.trace-page').count(), 0)
await page.waitForTimeout(250)
await page.getByRole('tab', { name: /^Trace/ }).click()
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-page .trace-toolbar select').inputValue(), 'business-b1-run')
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
await page.getByRole('tab', { name: /^Trace/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-run')
await page.getByText('未知 tokens').first().waitFor()
const traceText = await page.locator('.trace-page').textContent()
assert.ok(traceText.includes('未知 tokens'))
assert.ok((await page.locator('.round-card').first().textContent()).length > 1500)
for (const label of ['未缓存输入 未知', '缓存命中 未知', '输出（含推理） 未知', '推理 未知', '总计 未知']) assert.ok(traceText.includes(label), `missing usage label: ${label}`)

// A trace event for the same run refreshes the visible trace without changing tabs or run scope.
const traceCallsBefore = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
const traceVersionBefore = await page.evaluate(() => window.__traceVersion)
const roundCard = page.locator('.round-card').first()
await roundCard.locator(':scope > summary').click()
await roundCard.locator(':scope .tool-row').first().click()
await page.evaluate(() => window.__emitWorkbench({ event: 'run_trace', data: { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run' } }))
await page.waitForFunction((before) => window.__traceVersion > before, traceVersionBefore)
const traceCallsAfter = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
assert.ok(traceCallsAfter > traceCallsBefore)
const currentTraceVersion = await page.evaluate(() => window.__traceVersion)
assert.ok((await page.locator('.trace-page').textContent()).includes(`"version": ${currentTraceVersion}`))

// 300 trace events in one burst must coalesce to one trace read and one quiet business refresh.
const burstTraceCallsBefore = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
const burstBusinessCallsBefore = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length)
const burstTraceVersionBefore = await page.evaluate(() => window.__traceVersion)
const traceBurstStarted = Date.now()
await page.evaluate(() => {
  for (let index = 0; index < 300; index += 1) window.__emitWorkbench({ event: 'run_trace', data: { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run' } })
})
await page.waitForTimeout(300)
const burstTraceCallsAfter = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
const burstBusinessCallsAfter = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length)
assert.equal(burstTraceCallsAfter - burstTraceCallsBefore, 1)
assert.equal(burstBusinessCallsAfter - burstBusinessCallsBefore, 1)
assert.equal(await page.evaluate(() => window.__traceVersion), burstTraceVersionBefore + 1)
const traceBurstElapsed = Date.now() - traceBurstStarted

// Host lifecycle events are visible and the banner can retry health after a crash/protocol error.
await page.evaluate(() => window.__emitWorkbench({ event: 'host_status', data: { status: 'crashed', code: 9 } }))
await page.getByText('主机已崩溃').waitFor()
await page.evaluate(() => window.__emitWorkbench({ event: 'host_protocol_error', data: { message: 'invalid test frame' } }))
await page.getByText('主机协议错误').waitFor()
await page.getByRole('alert').getByRole('button', { name: '重试连接' }).click()
await page.getByText('主机已连接').waitFor()
const checksBeforeConnectionChanged = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'check_connection').length)
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { type: 'connection_changed', odoo: { status: 'connected', checked_at: '2026-09-08T09:01:00Z' } } }))
await page.waitForTimeout(30)
const checksAfterConnectionChanged = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'check_connection').length)
assert.equal(checksAfterConnectionChanged, checksBeforeConnectionChanged)

// Settings opens with focus on close and Escape closes it.
await page.getByRole('button', { name: '连接设置' }).click()
await page.getByRole('dialog', { name: '连接设置' }).waitFor()
assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '关闭设置')
for (let index = 0; index < 10; index += 1) {
  await page.keyboard.press('Tab')
  assert.equal(await page.evaluate(() => Boolean(document.querySelector('[role="dialog"]')?.contains(document.activeElement))), true)
}
await page.keyboard.press('Shift+Tab')
assert.equal(await page.evaluate(() => Boolean(document.querySelector('[role="dialog"]')?.contains(document.activeElement))), true)
await page.getByRole('button', { name: '保存连接设置' }).click()
await page.getByRole('alert').getByText('当前有业务正在执行或等待审批，请结束后再修改连接设置。').waitFor()
await page.getByRole('button', { name: '保存连接设置' }).click()
await page.getByRole('dialog', { name: '连接设置' }).waitFor({ state: 'hidden' })
await page.getByRole('status').getByText('设置已保存，连接状态已刷新。').waitFor()
await page.getByRole('button', { name: '连接设置' }).click()
await page.getByRole('dialog', { name: '连接设置' }).waitFor()
await page.keyboard.press('Escape')
await page.getByRole('dialog', { name: '连接设置' }).waitFor({ state: 'hidden' })
await page.waitForFunction(() => document.activeElement === document.querySelector('.settings-button'))

// The splitter is keyboard-operable and the four-column layout remains inside the viewport.
const divider = page.getByRole('separator', { name: '调整业务工作区宽度' })
await divider.focus()
for (let index = 0; index < 30; index += 1) await page.keyboard.press('ArrowLeft')
const overflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, grid: document.querySelector('.workspace-grid')?.scrollWidth ?? 0, gridClient: document.querySelector('.workspace-grid')?.clientWidth ?? 0 }))
assert.ok(overflow.document <= overflow.viewport + 1, JSON.stringify(overflow))
assert.ok(overflow.grid <= overflow.gridClient + 1, JSON.stringify(overflow))
await page.setViewportSize({ width: 1280, height: 900 })
await divider.focus()
for (let index = 0; index < 30; index += 1) await page.keyboard.press('ArrowLeft')
const narrowOverflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, grid: document.querySelector('.workspace-grid')?.scrollWidth ?? 0, gridClient: document.querySelector('.workspace-grid')?.clientWidth ?? 0 }))
assert.ok(narrowOverflow.document <= narrowOverflow.viewport + 1, JSON.stringify(narrowOverflow))
assert.ok(narrowOverflow.grid <= narrowOverflow.gridClient + 1, JSON.stringify(narrowOverflow))
await page.setViewportSize({ width: 1440, height: 900 })
await page.getByLabel('业务目标', {exact: true}).evaluate(e => { e.textContent = '完整业务目标 '.repeat(1000) })
assert.ok(await page.getByLabel('业务目标', {exact: true}).evaluate(e => e.clientHeight < 100 && e.scrollHeight > e.clientHeight))
await page.getByLabel('业务目标', {exact: true}).evaluate(e => { e.textContent = '处理订单与发票' })
assert.equal(pageErrors.length, 0, pageErrors.join('\n'))

const calls = await page.evaluate(() => window.__bridgeCalls.map(({ method }) => method))
assert.ok(calls.includes('get_session') && calls.includes('get_business') && calls.includes('get_trace'))
if (process.env.RENDERER_CHECK_SCREENSHOTS) {
  const screenshotDir = fileURLToPath(new URL('../../.runtime/desktop-refinement/', import.meta.url))
  mkdirSync(screenshotDir, { recursive: true })
  for (const [name, label] of [['overview', /^概览/], ['documents', /^单据/], ['approvals', /^审批/], ['trace', /^Trace/]]) {
    await page.getByRole('tab', { name: label }).click()
    await page.screenshot({ path: resolve(screenshotDir, `${name}.png`), fullPage: false })
  }
}
console.log('renderer-check: PASS')
console.log('checked: session/business/trace stale guards, same-run trace refresh, changed routing, host crash/retry, message scope, session search, approval scope+preflight labels, CONFIG_BUSY mapping, settings save+Escape, splitter overflow')
console.log(`pressure: session50=${sessionPressureElapsed}ms business50=${businessPressureElapsed}ms business20sequential=${sequentialBusinessElapsed}ms trace300=${traceBurstElapsed}ms trace_rpc_delta=${burstTraceCallsAfter - burstTraceCallsBefore} business_rpc_delta=${burstBusinessCallsAfter - burstBusinessCallsBefore} repeated={proposal:${proposalCalls.length},send:${dedupeSendCalls.length},start:${startCalls.length},approve:${repeatedApproveCalls.length},cancel:${cancelCalls.length}}`)
await browser.close()
server.close()
