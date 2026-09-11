import assert from 'node:assert/strict'
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../node_modules/playwright/index.mjs'

const rendererRoot = fileURLToPath(new URL('../out/renderer/', import.meta.url))
assert.ok(existsSync(resolve(rendererRoot, 'index.html')), 'run npm run build first')
const statSafe = (file) => { try { return statSync(file).isFile() } catch { return false } }
const server = createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url || '/', 'http://127.0.0.1').pathname)
  const file = resolve(rendererRoot, `.${path === '/' ? '/index.html' : path}`)
  if (!file.startsWith(rendererRoot) || !statSafe(file)) return response.writeHead(404).end()
  response.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[extname(file)] || 'application/octet-stream' })
  createReadStream(file).pipe(response)
})
server.unref()
await new Promise((done) => server.listen(0, '127.0.0.1', done))

const now = '2026-09-11T09:00:00Z'
const session = { id: 'demo-session', title: 'Nimbus｜九月订单', created_at: now, updated_at: now, archived: false, status: 'idle' }
const businesses = [
  { id: 'invoice', session_id: session.id, type: 'sale_invoice', title: 'Nimbus 九月开票成果', goal: '展示九月订单、发票回读与可下载文件。', status: 'completed', completion_target: 'posted', created_at: now, updated_at: now },
  { id: 'approval', session_id: session.id, type: 'sale_invoice', title: 'Nimbus 十月发票审批', goal: '核对十月发票创建提案，审批前不执行任何 Odoo 写入。', status: 'awaiting_approval', completion_target: 'posted', created_at: now, updated_at: now },
  { id: 'quote', session_id: session.id, type: 'sale_invoice', title: 'Nimbus 报价草稿', goal: '为 Nimbus Bureau 准备九月服务报价并保留草稿。', status: 'ready', completion_target: 'draft', created_at: now, updated_at: now }
]
session.businesses = businesses
const materials = [
  { id: 'material-orders', session_id: session.id, name: 'nimbus-september-orders.csv', size: 1842, sha256: 'synthetic-orders', created_at: now, row_count: 12, preview: 'service,quantity,unit_price\nSupport retainer,1,695.22', media_type: 'text/csv' },
  { id: 'material-brief', session_id: session.id, name: 'nimbus-september-brief.txt', size: 622, sha256: 'synthetic-brief', created_at: now, preview: '九月支持服务：数量 1，预算 USD 695.22。', media_type: 'text/plain' }
]
const messages = [
  { id: 'm1', role: 'user', text: '请根据 Nimbus 九月订单准备报价，并说明数量、预算和下一步。', created_at: now, material_ids: materials.map((item) => item.id) },
  { id: 'm2', role: 'assistant', text: '九月订单已完成核对：数量 1，预算 USD 695.22；发票 INV/2026/00921 已回读为 posted，PDF 与 CSV 均已准备。', created_at: now, business_id: 'invoice' },
  { id: 'm3', role: 'user', text: '另外准备十月发票提案；任何写入请先停在人工审批。', created_at: now, business_id: 'approval' },
  { id: 'm4', role: 'assistant', text: '十月发票创建提案已准备。当前仍是 draft，审批前尚未执行 Odoo 写入；批准后才会继续。', created_at: now, business_id: 'approval' }
]
const usage = (input, cacheRead, output, reasoning) => ({ input, cache_read: cacheRead, output, reasoning, total: input + output })
const nativeCatalog = JSON.parse(readFileSync(resolve(process.cwd(), 'integration/native_tool_catalog.json'), 'utf8')).tools
const catalogByName = new Map(nativeCatalog.map((tool) => [tool.name, tool]))
const assertCatalogTool = (tool) => {
  const spec = catalogByName.get(tool.name)
  assert.ok(spec, `fixture tool missing from native catalog: ${tool.name}`)
  for (const key of spec.parameters.required ?? []) assert.ok(Object.hasOwn(tool.arguments, key), `${tool.name} missing required ${key}`)
  for (const key of Object.keys(tool.arguments)) assert.ok(Object.hasOwn(spec.parameters.properties, key), `${tool.name} has unknown argument ${key}`)
}
const traceData = (business) => {
  const awaiting = business.status === 'awaiting_approval'
  const tools = [
    { id: 'read-order', name: 'mcp_odoo_search_records', round: 1, status: 'completed', arguments: { model: 'sale.order', domain: [['partner_id', '=', 42]], fields: ['name', 'partner_id', 'amount_total', 'state'] }, result: { source: 'synthetic_demo', count: 1 }, elapsed_seconds: 1.1 },
    { id: 'read-lines', name: 'mcp_odoo_search_records', round: 1, status: 'completed', arguments: { model: 'sale.order.line', domain: [['order_id', '=', 9021]], fields: ['name', 'product_uom_qty', 'price_subtotal'] }, result: { source: 'synthetic_demo', quantity: 1, amount_total: 695.22 }, elapsed_seconds: 1.0 },
    { id: awaiting ? 'approval-gate' : 'read-invoice', name: awaiting ? 'mcp_odoo_validate_write' : 'mcp_odoo_read_record', round: 2, status: awaiting ? 'awaiting_approval' : 'completed', arguments: awaiting ? { model: 'account.move', operation: 'create', values: { partner_id: 42, move_type: 'out_invoice' } } : { model: 'account.move', record_id: 9021 }, result: { source: 'synthetic_demo', state: awaiting ? 'draft' : 'posted' }, elapsed_seconds: 1.4 },
    { id: awaiting ? 'approval-preview' : 'read-artifact', name: awaiting ? 'mcp_odoo_preview_write' : 'mcp_odoo_read_attachment', round: 2, status: awaiting ? 'awaiting_approval' : 'completed', arguments: awaiting ? { model: 'account.move', operation: 'create', values: { partner_id: 42, move_type: 'out_invoice' } } : { attachment_id: 9201, include_data: false }, result: { source: 'synthetic_demo', verified: !awaiting }, elapsed_seconds: 1.2 }
  ]
  tools.forEach(assertCatalogTool)
  const rounds = [
    { index: 1, status: 'completed', text: '读取 Nimbus 九月订单并整理服务数量与预算。', elapsed_seconds: 2.1, usage: usage(940, 0, 180, 60), tool_ids: ['read-order', 'read-lines'] },
    { index: 2, status: awaiting ? 'awaiting_approval' : 'completed', text: awaiting ? '等待人工审批，尚未执行写入。' : '回读发票、订单状态与演示文件已完成。', elapsed_seconds: 2.6, usage: usage(900, 0, 230, 60), tool_ids: awaiting ? ['approval-gate', 'approval-preview'] : ['read-invoice', 'read-artifact'] }
  ]
  const totals = rounds.reduce((sum, round) => ({ input: sum.input + round.usage.input, cache_read: sum.cache_read + round.usage.cache_read, output: sum.output + round.usage.output, reasoning: sum.reasoning + round.usage.reasoning }), { input: 0, cache_read: 0, output: 0, reasoning: 0 })
  return { run: { id: `${business.id}-run`, business_id: business.id, session_id: session.id, status: awaiting ? 'awaiting_approval' : business.status === 'completed' ? 'completed' : 'ready', started_at: now, ended_at: awaiting ? undefined : now, tool_count: tools.length, model_rounds: rounds.length, elapsed_seconds: 4.7, verification_status: business.status === 'completed' ? 'passed' : 'unknown', usage: { ...totals, total: totals.input + totals.output } }, rounds, tools }
}
const detail = (business) => {
  const trace = traceData(business)
  const completed = business.status === 'completed'
  const awaiting = business.status === 'awaiting_approval'
  return {
    business, runs: [trace.run],
    approvals: awaiting ? [{ action_id: 'demo-approval', run_id: trace.run.id, business_id: business.id, status: 'pending_approval', title: '创建客户发票', model: 'account.move', operation: 'create', record_ids: [], values: { partner_id: 42, move_type: 'out_invoice', invoice_line_ids: [[0, 0, { name: 'October support retainer', quantity: 1, price_unit: 695.22 }]] }, prestate: { records: [] }, result: { status: 'awaiting_approval', detail: '审批前尚未执行写入' }, expires_at: new Date(Date.now() + 3600000).toISOString(), source: 'synthetic_demo' }] : [],
    documents: completed ? [{ id: 'so-9021', model: 'sale.order', name: 'SO9021', state: 'sale', source: 'synthetic_demo', source_run_id: trace.run.id, source_tool_id: 'read-order', observed_at: now, fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', invoice_status: 'invoiced' } }, { id: 'inv-9021', model: 'account.move', name: 'INV/2026/00921', state: 'posted', source: 'synthetic_demo', source_run_id: trace.run.id, source_tool_id: 'read-invoice', observed_at: now, fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', payment_state: 'not_paid' } }] : [],
    artifacts: completed ? [{ id: 'pdf', name: 'INV-2026-00921.pdf（演示素材）', path: 'synthetic://invoice.pdf', kind: 'odoo_pdf', created_at: now, available: true, source: 'synthetic_demo' }, { id: 'csv', name: 'INV-2026-00921.csv（演示素材）', path: 'synthetic://invoice.csv', kind: 'odoo_csv', created_at: now, available: true, source: 'synthetic_demo' }] : [],
    checks: completed ? [{ name: 'invoice', label: '发票回读', status: 'passed', detail: '已从合成演示回读 INV/2026/00921。', source: 'synthetic_demo' }, { name: 'amount', label: '金额核对', status: 'passed', detail: 'USD 695.22，与订单摘要一致。', source: 'synthetic_demo' }] : [],
    stale: false, observed_at: now, summary: awaiting ? '等待人工审批，尚未写入 Odoo。' : completed ? '订单、发票回读与文件已完成。' : '报价保留为草稿。',
    activity: { phase: awaiting ? 'approval' : completed ? 'completed' : 'quote', label: awaiting ? '等待审批' : completed ? '已完成' : '报价草稿', detail: business.goal, at: now },
    outcome: awaiting ? { status: 'unknown', label: '等待审批', detail: '批准后才允许恢复本轮流程。', scope: 'synthetic_demo' } : completed ? { status: 'passed', label: '演示完成', detail: '报价、开票与文件均为合成数据。', scope: 'synthetic_demo' } : { status: 'unknown', label: '待确认', detail: '报价仍保留为草稿。', scope: 'synthetic_demo' },
    execution: { run_id: trace.run.id, current_stage_id: awaiting ? 'invoice' : completed ? 'verify' : 'quote', stages: [{ id: 'read', label: '读取客户', status: 'observed', detail: 'Nimbus Bureau（演示）', evidence: [{ run_id: trace.run.id, kind: 'tool', tool_id: 'read-order', observed_at: now, label: '读取销售订单' }] }, { id: 'quote', label: '报价草稿', status: completed || awaiting ? 'observed' : 'active', detail: business.goal, evidence: [] }, { id: 'invoice', label: '开票审批', status: awaiting ? 'awaiting_approval' : completed ? 'verified' : 'pending', detail: awaiting ? '写入前等待审批' : completed ? '已回读发票' : '等待报价确认', evidence: completed ? [{ run_id: trace.run.id, kind: 'readback', tool_id: 'read-invoice', observed_at: now, label: '回读发票' }] : [] }, { id: 'verify', label: '独立回读', status: completed ? 'verified' : 'pending', detail: completed ? '订单与文件已核对' : '批准后执行', evidence: completed ? [{ run_id: trace.run.id, kind: 'readback', tool_id: 'read-artifact', observed_at: now, label: '核对业务文件' }] : [] }] }
  }
}
const details = Object.fromEntries(businesses.map((item) => [item.id, detail(item)]))
const traces = Object.fromEntries(businesses.map((item) => [item.id, traceData(item)]))
assert.equal(details.approval.approvals[0].values.partner_id, 42)
const bridge = `(() => { const session = ${JSON.stringify(session)}; const businesses = ${JSON.stringify(businesses)}; const messages = ${JSON.stringify(messages)}; const materials = ${JSON.stringify(materials)}; const details = ${JSON.stringify(details)}; const traces = ${JSON.stringify(traces)}; const listeners = new Set(); window.workbench = { subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) }, async call(method, params = {}) { if (method === 'health' || method === 'check_connection') return { host_ready: true, odoo_status: 'connected', model_configured: true, environment: 'demo', odoo: { status: 'connected', endpoint: 'synthetic://odoo', database: 'demo', account: 'demo' } }; if (method === 'list_sessions') return [session]; if (method === 'get_session') return { session, messages, materials, businesses, conversation_runs: [], live_messages: [] }; if (method === 'get_business' || method === 'refresh_business') return details[params.business_id]; if (method === 'get_trace') return traces[params.business_id]; throw new Error('showcase read-only bridge rejected: ' + method) } } })()`

let browser
const fail = (error) => { console.error(error); server.close(); process.exitCode = 1; void browser?.close() }
process.on('unhandledRejection', fail)
process.on('uncaughtException', fail)
browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
await context.addInitScript({ content: bridge })
const page = await context.newPage()
page.setDefaultTimeout(6000)
const pageErrors = []
page.on('pageerror', (error) => pageErrors.push(error.message))
const returnToLatest = async () => { const button = page.getByRole('button', { name: /有新消息.*回到最新/ }); if (await button.isVisible().catch(() => false)) await button.click() }
await page.goto(`http://127.0.0.1:${server.address().port}/`)
await page.getByRole('button', { name: /Nimbus｜九月订单/ }).waitFor()
assert.equal(await page.locator('.session-row').count(), 1)
assert.equal(await page.locator('.global-alert:visible').count(), 0)
assert.ok((await page.locator('.message').count()) >= 4)
await returnToLatest()
const overviewPng = await page.screenshot({ path: resolve('docs/media/workbench-overview.png'), fullPage: true })

await page.getByRole('tab', { name: /Nimbus 十月发票审批/ }).click()
await page.getByRole('tab', { name: /^变更与审批/ }).click()
await page.getByText(/等待审批/).first().waitFor()
assert.equal(await page.locator('.approval-row').count(), 1)
assert.ok((await page.locator('.approval-row').innerText()).includes('未执行'))
await returnToLatest()
await page.screenshot({ path: resolve('docs/media/workbench-approvals.png'), fullPage: true })

await page.getByRole('tab', { name: /Nimbus 九月开票成果/ }).click()
await page.getByRole('tab', { name: /^单据与文件/ }).click()
await page.getByRole('button', { name: /INV\/2026\/00921/ }).waitFor()
assert.ok((await page.getByText(/演示素材/).count()) >= 2)
assert.ok((await page.getByText(/发票回读/).count()) >= 1)
await returnToLatest()
await page.screenshot({ path: resolve('docs/media/workbench-documents.png'), fullPage: true })

await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-node').filter({ hasText: '第 1 轮' }).click()
await page.getByText('读取 Nimbus 九月订单并整理服务数量与预算。', { exact: true }).waitFor()
assert.ok((await page.locator('.trace-node').count()) >= 4)
await returnToLatest()
await page.screenshot({ path: resolve('docs/media/workbench-trace.png'), fullPage: true })
const socialPage = await context.newPage()
await socialPage.setViewportSize({ width: 1200, height: 630 })
await socialPage.setContent(`<style>html,body{margin:0;background:#fbfaf6;color:#123b3b;font-family:Inter,Arial,sans-serif}main{width:1200px;height:630px;box-sizing:border-box;padding:52px 58px;position:relative;background:linear-gradient(135deg,#fbfaf6 0%,#e5f4f0 100%);overflow:hidden}h1{margin:0;font-size:56px;letter-spacing:-2px;color:#087f78}p{font-size:22px;margin:12px 0;color:#315b5a}figure{position:absolute;right:42px;bottom:30px;width:650px;height:365px;margin:0;border:2px solid #b8ded7;border-radius:18px;overflow:hidden;box-shadow:0 16px 40px #115b5826;background:white}img{width:100%;height:100%;object-fit:cover;object-position:top left}footer{position:absolute;left:58px;bottom:54px;font-size:17px;color:#55706f}</style><main><h1>Odoo Agent</h1><p>Native ERP Agent Harness</p><figure><img src="data:image/png;base64,${overviewPng.toString('base64')}" /></figure><footer>Python Pi · Odoo 19 · ERP-Bench</footer></main>`)
await socialPage.screenshot({ path: resolve('docs/media/social-preview.png'), type: 'png' })
await socialPage.close()
assert.equal(pageErrors.length, 0, pageErrors.join('\n'))
await browser.close()
server.close()
console.log(JSON.stringify({ ok: true, fixture: 'synthetic-nimbus-demo', screenshots: 5, page_errors: pageErrors.length }))
