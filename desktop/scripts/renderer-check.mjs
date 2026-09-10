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
    let materialVersion = 0
    const importedMaterials = new Map()
    let saveBlocked = true
    let failInitialConnectionCheck = true
    let delayedPurchaseDecision = true
    let showAcceptedProjection = false
    let showCompactionUsage = false
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
    const session = (id, title, businesses) => ({ id, title, created_at: '2026-09-08T08:00:00Z', updated_at: '2026-09-08T09:00:00Z', archived: false, status: 'idle', businesses })
    const business = (id, session_id, title, status = 'idle', goal = '处理订单与发票') => ({ id, session_id, type: id === 'business-b2' ? 'sale_purchase_invoice' : 'sale_invoice', title, goal, status, created_at: '2026-09-08T08:00:00Z', updated_at: '2026-09-08T09:00:00Z', active_run_id: id + '-run' })
    const b1 = business('business-b1', 'session-b', 'Business B1')
    const b2 = { ...business('business-b2', 'session-b', 'Business B2', 'awaiting_approval'), material_ids: ['material-b2'] }
    const b3 = business('business-b3', 'session-b', 'Business B3｜这是一个很长的企业业务标题用于窄面板换行检查', 'needs_reconciliation', '核对中断写入是否已经落库，并保留当前运行与历史单据证据。')
    const b4 = business('business-b4', 'session-b', 'Business B4', 'completed')
    const sessions = [session('session-a', 'Session A', [business('business-a1', 'session-a', 'Business A1')]), session('session-b', 'Session B', [b1, b2, b3, b4])]
    const details = (b) => ({
      business: b.id === 'business-b2' ? { ...b, readback: { latest_run_id: 'business-b2-run', observed_at: '2026-09-08T08:49:00Z', stale: false, checks: [{ name: 'invoice_readback', label: '客户发票回读', status: 'passed', detail: '当前运行后的 Odoo 快照已返回。', source: 'odoo' }] } } : b,
      runs: [{ id: b.id + '-old-run', business_id: b.id, session_id: b.session_id, status: 'completed', started_at: '2026-09-08T08:00:00Z', tool_count: 1, model_rounds: 1, elapsed_seconds: 0.8, verification_status: 'passed', usage: { input: null, cache_read: null, output: null, reasoning: null, total: null } }, { id: b.id + '-run', business_id: b.id, session_id: b.session_id, status: b.id === 'business-b2' ? 'awaiting_approval' : b.id === 'business-b3' ? 'needs_reconciliation' : 'completed', started_at: '2026-09-08T08:30:00Z', tool_count: 2, model_rounds: 2, elapsed_seconds: 1.2, verification_status: b.id === 'business-b2' || b.id === 'business-b3' ? '未知' : 'passed', usage: showCompactionUsage && b.id === 'business-b2' ? { input: 20, cache_read: 0, output: 10, reasoning: 0, total: 130, compaction_total: 100, compaction_calls: 1 } : { input: null, cache_read: null, output: null, reasoning: null, total: null } }],
      live_messages: b.id === 'business-b2' ? [{ id: 'business-live-1', session_id: b.session_id, business_id: b.id, run_id: b.id + '-run', sequence: 1, text: '业务执行公开进度：正在读取 Odoo。', role: 'assistant', status: 'streaming' }] : [],
      artifacts: b.id === 'business-b2' ? [{ id: 'artifact-b2', name: 'Business B2 回执.json', path: 'C:\\runtime\\business-b2-receipt.json', created_at: '2026-09-08T08:50:00Z', kind: 'business_receipt', run_id: 'business-b2-run', available: true }, { id: 'artifact-pdf', name: 'INV-B2.pdf', path: 'C:\\runtime\\INV-B2.pdf', created_at: '2026-09-08T08:51:00Z', kind: 'odoo_pdf', run_id: 'business-b2-run', available: true }, { id: 'artifact-csv', name: 'INV-B2.csv', path: 'C:\\runtime\\INV-B2.csv', created_at: '2026-09-08T08:52:00Z', kind: 'odoo_csv', run_id: 'business-b2-run', available: true }] : [],
      materials: b.id === 'business-b2' ? [{ id: 'material-b2', session_id: b.session_id, name: '订单材料.csv', size: 42, sha256: 'sha-b2', created_at: '2026-09-08T08:44:00Z', row_count: 5, preview: '客户,产品,数量\\nNimbus,服务,2', media_type: 'text/csv' }] : [],
      approvals: b.id === 'business-b2' ? [{ action_id: 'action-b2', run_id: 'business-b2-run', business_id: 'business-b2', status: 'pending_approval', title: 'ERP write approval', model: 'account.move', operation: 'create', record_ids: [42], values: { client_order_ref: 'PI-DYNAMIC-MVP-20260908', commitment_date: '2026-09-16 08:00:00', order_line: [[0, 0, { product_id: 2, product_uom_qty: 1, price_unit: 695.22 }]], partner_id: 10, payment_term_id: 4 }, prestate: { records: [] }, result: { ok: true, preflight: true }, expires_at: Math.floor(Date.now() / 1000) + 60 }, { action_id: 'action-b2-purchase', run_id: 'business-b2-run', business_id: 'business-b2', status: 'pending_approval', title: 'ERP write approval', model: 'purchase.order', operation: 'button_confirm', record_ids: [1], values: { partner_id: 10, order_line: [[0, 0, { product_id: 2, product_qty: 3, price_unit: 12 }] ] }, prestate: { state: 'draft' }, result: { ok: true, preflight: true }, expires_at: Math.floor(Date.now() / 1000) + 60 }, { action_id: 'action-b2-send-file', run_id: 'business-b2-run', business_id: 'business-b2', status: 'pending_approval', title: '生成客户发票正式文件', model: 'account.move.send.wizard', operation: 'action_send_and_print', record_ids: [42], values: { sending_methods: [] }, prestate: { is_move_sent: false }, result: { ok: true, preflight: true }, expires_at: Math.floor(Date.now() / 1000) + 60 }] : b.id === 'business-b3' ? [{ action_id: 'action-b3', run_id: 'business-b3-run', business_id: 'business-b3', status: 'needs_reconciliation', title: '执行客户发票写入', model: 'account.move', operation: 'create', record_ids: [99], values: { state: 'posted' }, prestate: { state: 'draft' }, result: { status: 'unknown', retryable: false, detail: '主机中断，无法确认写入是否落库' }, expires_at: Math.floor(Date.now() / 1000) + 60 }] : b.id === 'business-b4' ? [{ action_id: 'action-b4-verified', run_id: 'business-b4-run', business_id: 'business-b4', status: 'verified', title: '客户发票写入', model: 'account.move', operation: 'create', record_ids: [101], values: { state: 'posted' }, prestate: { state: 'draft' }, result: { ok: true, executed: true }, expires_at: Math.floor(Date.now() / 1000) - 60 }, { action_id: 'action-b4-rejected', run_id: 'business-b4-run', business_id: 'business-b4', status: 'rejected', title: '重复发票写入', model: 'account.move', operation: 'create', record_ids: [102], values: { state: 'posted' }, prestate: { state: 'posted' }, result: { ok: false, reason: 'approval_rejected' }, expires_at: Math.floor(Date.now() / 1000) - 60 }] : [],
      documents: b.id === 'business-b2' ? [
        { id: '1', model: 'sale.order', name: 'SO-B2', state: 'sale', source: 'native_read_receipt', observed_at: '2026-09-08T08:45:00Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', invoice_status: 'invoiced', delivery_status: 'pending' } },
        { id: 'legacy-so', model: 'sale.order', name: 'S00001', state: 'sale', source: 'native_read_receipt', observed_at: '2026-08-08T08:45:00Z', is_reference: true, document_scope: 'reference', fields: { partner_name: 'Legacy Customer', amount_total: 99, currency: 'USD', invoice_status: 'invoiced' } },
        { id: '2', model: 'sale.order', name: 'SO-B2-SECOND', state: 'draft', source: 'native_read_receipt', observed_at: '2026-09-08T08:45:30Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 10, currency: 'USD', invoice_status: 'to invoice' } },
        { id: 'po-1', model: 'purchase.order', name: 'PO-B2', state: 'to approve', source: 'native_read_receipt', observed_at: '2026-09-08T08:45:45Z', fields: { partner_name: 'Nimbus Supplier', amount_total: 120, currency: 'USD' } },
        { id: '1', model: 'purchase.order', name: 'P00001', state: 'to approve', source: 'native_read_receipt', observed_at: '2026-09-08T08:45:46Z', fields: { partner_name: 'Alpine Supplier', amount_total: 36, currency: 'USD' } },
        { id: '1', model: 'account.move', name: 'INV-B2', state: 'posted', source: 'refresh_native_read', observed_at: '2026-09-08T08:46:00Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 695.22, currency: 'USD', payment_state: 'not_paid', amount_residual: 695.22, invoice_pdf_report_id: null } },
        { id: 'line-1', model: 'sale.order.line', name: 'SO-B2 明细 1', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:10Z', fields: { product_name: '服务项目', product_uom_qty: 1, price_unit: 695.22 } },
        { id: 'legacy-line', model: 'sale.order.line', name: 'S00001 明细 1', source: 'native_read_receipt', observed_at: '2026-08-08T08:46:10Z', is_reference: true, document_scope: 'reference', fields: { product_name: '旧服务', product_uom_qty: 1, price_unit: 99 } },
        { id: 'line-2', model: 'account.move.line', name: 'INV-B2 分录 1', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:11Z', fields: { account_name: '应收账款', debit: 695.22, credit: 0 } },
        { id: '4', model: 'account.payment.term', name: '30Days', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:12Z', fields: { name: '30Days' } },
        { id: '10', model: 'res.partner', name: 'Nimbus Bureau', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:12Z', fields: { name: 'Nimbus Bureau' } },
        { id: 'partner-1', model: 'res.partner', name: 'Alpine Supplier', source: 'native_read_receipt', observed_at: '2026-08-08T08:46:12Z', fields: { name: 'Alpine Supplier' } },
        { id: 'supplierinfo-1', model: 'product.supplierinfo', name: 'Alpine 供货信息', source: 'native_read_receipt', observed_at: '2026-08-08T08:46:12Z', fields: { product_name: '服务项目', partner_name: 'Alpine Supplier' } },
        { id: 'tax-1', model: 'account.tax', name: '销售税', source: 'native_read_receipt', observed_at: '2026-08-08T08:46:12Z', fields: { name: '销售税' } },
        { id: 'product-1', model: 'product.template', name: '服务产品', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:13Z', fields: { name: '服务产品' } },
        { id: 'journal-1', model: 'account.journal', name: '销售日记账', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:14Z', fields: { name: '销售日记账' } },
        { id: 'wizard-1', model: 'sale.advance.payment.inv', name: '开票向导', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:15Z', fields: { name: '开票向导' } },
        { id: 'purchase-line-1', model: 'purchase.order.line', name: 'P00001 明细 1', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:16Z', fields: { product_name: '服务项目', product_qty: 3, price_unit: 12 } },
        { id: 'product-variant-1', model: 'product.product', name: '服务项目变体', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:17Z', fields: { name: '服务项目变体' } },
        { id: 'attachment-1', model: 'ir.attachment', name: 'INV-B2 PDF', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:18Z', fields: { name: 'INV-B2 PDF' } },
        { id: 'send-wizard-1', model: 'account.move.send.wizard', name: '发票PDF向导', source: 'native_read_receipt', observed_at: '2026-09-08T08:46:19Z', fields: { name: '发票PDF向导' } },
        ...Array.from({ length: 7 }, (_, index) => ({ id: 'mail-' + (index + 1), model: 'mail.message', name: '业务留言 ' + (index + 1), source: 'refresh_native_read', observed_at: '2026-09-08T08:47:0' + index + 'Z', fields: { subject: '订单沟通', body: '客户留言 ' + (index + 1), author_name: 'Nimbus Bureau' } }))
      ] : b.id === 'business-b3' ? [{ id: '7', model: 'sale.order', name: 'SO-B3-HISTORY', state: 'sale', source: 'odoo', observed_at: '2026-09-08T08:40:00Z', fields: { partner_name: 'Nimbus Bureau', amount_total: 99, currency: 'USD', invoice_status: 'to invoice' } }] : [],
      checks: b.id === 'business-b3' ? [{ name: 'invoice_write', label: '客户发票写入状态', status: 'unknown', detail: '未观察到可确认的 Odoo 写入结果；需要人工核对。', source: 'odoo' }] : [], stale: false, observed_at: '2026-09-08T08:46:00Z', summary: b.id === 'business-b3' ? '写入结果待核对，系统不会自动重试' : '主机已返回业务回执', activity: b.id === 'business-b3' ? { phase: 'reconciliation', label: '写入结果待核对', detail: '主机中断后无法确认写入是否落库；请核对 Odoo 后再决定。', tool_name: 'execute_approved_write', round: 2, tool_count: 2, model_rounds: 2, at: '2026-09-08T08:46:00Z' } : b.id === 'business-b2' ? { phase: 'approval', label: '等待确认', detail: '客户发票写入动作等待人工审批。', tool_name: 'mcp_odoo_validate_write', round: 2, tool_count: 2, model_rounds: 2, at: '2026-09-08T08:46:00Z' } : { phase: 'idle', label: '待执行', detail: '尚未开始。' }, execution: b.id === 'business-b2' ? { run_id: 'business-b2-run', current_stage_id: 'approval', stages: [{ id: 'plan', label: '读取订单', status: 'completed', detail: '已读取 Odoo 销售订单。', evidence: [{ run_id: 'business-b2-old-run', kind: 'readback', label: '查看独立回读快照', observed_at: '2026-09-08T08:45:00Z' }, { run_id: 'business-b2-run', tool_id: 'tool-b2', label: '查看订单读取回执', observed_at: '2026-09-08T08:45:00Z' }] }, { id: 'approval', label: '等待审批', status: 'awaiting_approval', detail: '客户发票写入等待人工确认。', evidence: [{ run_id: 'business-b2-run', tool_id: 'tool-b2', action_id: 'action-b2', label: '查看审批前回执', observed_at: '2026-09-08T08:46:00Z' }] }] } : b.id === 'business-b3' ? { run_id: 'business-b3-run', current_stage_id: 'reconcile', stages: [{ id: 'reconcile', label: '写入结果待核对', status: 'unknown', detail: '没有确认写入是否落库。', evidence: [{ run_id: 'business-b3-run', tool_id: 'tool-b3-unknown-write', action_id: 'action-b3', label: '查看未知写入回执', observed_at: '2026-09-08T08:46:00Z' }] }] } : { run_id: b.id + '-run', current_stage_id: 'start', stages: [{ id: 'start', label: '待执行', status: 'pending', detail: '尚未开始。' }] }, outcome: b.id === 'business-b3' ? { status: 'unknown', label: '结果待核对', detail: '写入结果未知，系统不会自动重试。', scope: '客户发票' } : b.id === 'business-b2' ? { status: 'awaiting_approval', label: '等待审批', detail: '批准只允许恢复本轮流程，不代表写入已经执行。', scope: 'sale_purchase_invoice_posted_checks' } : { status: 'unknown', label: '尚未执行', detail: '没有业务结果。', scope: '销售发票' }
    })
    const sessionDetails = {
      'session-a': { session: sessions[0], messages: [], businesses: sessions[0].businesses, conversation_runs: [], live_messages: [] },
      'session-b': { session: sessions[1], messages: [{ id: 'm1', role: 'assistant', text: '已识别两个业务工作区。', created_at: '2026-09-08T08:01:00Z' }, { id: 'm-user', role: 'user', text: '我想查看订单', created_at: '2026-09-08T08:01:30Z' }, { id: 'proposal-1', role: 'assistant', text: '主机合成的提案正文不应重复显示', created_at: '2026-09-08T08:02:00Z', proposal: { id: 'proposal-1', title: '新业务意图', goal: '处理一笔新的销售业务', type: 'sale_invoice', completion_target: 'posted', status: 'pending' } }], businesses: sessions[1].businesses, conversation_runs: [{ id: 'conversation-run-b', session_id: 'session-b', business_id: null, kind: 'conversation', status: 'running' }], live_messages: [] }
    }
    sessionDetails['session-b'].materials = [{ id: 'material-b2', session_id: 'session-b', name: '订单材料.csv', size: 42, sha256: 'sha-b2', created_at: '2026-09-08T08:44:00Z', row_count: 5, preview: '客户,产品,数量\\nNimbus,服务,2', media_type: 'text/csv' }]
    window.__bridgeCalls = calls
    window.__traceVersion = 0
    window.__persistConversationMessage = (message) => sessionDetails['session-b'].messages.push(message)
    window.__setSessionMessages = (id, messages) => { sessionDetails[id].messages = messages }
    window.__removeConversationMessage = (id) => { sessionDetails['session-b'].messages = sessionDetails['session-b'].messages.filter((message) => message.id !== id) }
    window.__emitWorkbench = (event) => listeners.forEach((listener) => listener(event))
    window.__showAcceptedProjection = (value = true) => { showAcceptedProjection = value }
    window.__showCompactionUsage = () => { showCompactionUsage = true }
    window.workbench = {
      subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
      windowControl() { return Promise.resolve() },
      async call(method, params = {}) {
        calls.push({ method, params })
        if (method === 'health') return { host_ready: true, odoo_status: 'connected', odoo: { status: 'connected', endpoint: 'http://odoo.invalid', database: 'demo', account: 'admin', checked_at: '2026-09-08T09:00:00Z', latency_ms: 12 }, model_configured: true, environment: 'demo', data_dir: 'C:\\runtime\\profile\\data' }
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
          const result = details(sessions.flatMap((item) => item.businesses).find((item) => item.id === id))
          if (id === 'business-b2' && showAcceptedProjection) { result.approvals = []; result.business.status = 'running'; result.runs[1].status = 'running'; result.activity = { phase: 'model', label: '等待模型响应', detail: '审批已完成，模型正在继续处理。' } }
          return result
        }
        if (method === 'get_trace') {
          await wait(params.business_id === 'business-b1' ? 12 : 220)
          if (params.business_id === 'business-b1') return { run: details(b1).runs[0], rounds: [], tools: [] }
          if (params.business_id === 'business-b3') {
            const historical = params.run_id === 'business-b3-old-run'
            const run = details(b3).runs[historical ? 0 : 1]
            const tool = historical ? { id: 'tool-b3-old', name: 'read_business_records', round: 1, status: 'completed', arguments: { model: 'sale.order', ids: [7] }, result: { count: 1, source: 'odoo' }, elapsed_seconds: 0.2 } : { id: 'tool-b3-unknown-write', name: 'execute_approved_write', round: 2, status: 'unknown', arguments: { model: 'account.move', operation: 'create', ids: [99] }, result: null, elapsed_seconds: null, action_id: 'action-b3' }
            return { run, rounds: [{ index: tool.round, status: tool.status, text: historical ? '读取历史业务单据' : '写入结果无法确认', elapsed_seconds: tool.elapsed_seconds, usage: { input: null, cache_read: null, output: null, reasoning: null, total: null }, tool_ids: [tool.id] }], tools: [tool] }
          }
          traceVersion += 1
          window.__traceVersion = traceVersion
          const selectedRun = details(b2).runs.find((run) => run.id === params.run_id) ?? details(b2).runs[1]
          return { run: selectedRun, rounds: [{ index: 1, status: 'completed', text: '公开业务摘要 '.repeat(500), elapsed_seconds: null, usage: { input: null, cache_read: null, output: null, reasoning: null, total: null }, tool_ids: ['tool-b2', 'tool-b2-active', 'tool-b2-executed'] }], tools: [{ id: 'tool-b2', name: 'read_business_records', round: 1, status: 'completed', arguments: { model: 'sale.order' }, result: { count: 2, version: traceVersion }, elapsed_seconds: null, action_id: 'action-b2' }, { id: 'tool-b2-active', name: 'mcp_odoo_validate_write', round: 1, status: 'running', arguments: { model: 'account.move', operation: 'create', ids: [42] }, result: null, elapsed_seconds: null, action_id: 'action-b2' }, { id: 'tool-b2-executed', name: 'execute_approved_write', round: 1, status: 'executed', arguments: { model: 'account.move', operation: 'create', ids: [42] }, result: { ok: true, write_id: 42, state: 'posted' }, elapsed_seconds: 0.4, action_id: 'action-b2' }] }
        }
        if (method === 'import_material') {
          if (params.name === 'cross.csv') await wait(120)
          const importedKey = params.session_id + ':' + params.name + ':sha-fixture'
          if (!importedMaterials.has(importedKey)) { materialVersion += 1; importedMaterials.set(importedKey, { id: 'material-' + materialVersion, session_id: params.session_id, name: params.name, size: 24, sha256: 'sha-fixture', created_at: '2026-09-08T09:03:00Z', row_count: 2, preview: '客户,产品,数量', media_type: 'text/csv' }) }
          return importedMaterials.get(importedKey)
        }
        if (method === 'download_document') {
          if (params.format === 'pdf' && params.model === 'account.move') throw new Error("Error invoking remote method 'workbench:call': Error: [VALUEERROR] 发票已过账，但尚未生成正式 PDF，请先生成发票文件后再下载。")
          if (params.format === 'pdf') return { cancelled: true }
          return { cancelled: false, path: 'C:\\runtime\\SO-B2.csv', artifact: { id: 'artifact-doc-csv', name: 'SO-B2.csv', path: 'C:\\runtime\\SO-B2.csv', kind: 'document_csv', available: true } }
        }
        if (method === 'decide_approval') {
          if (params.action_id === 'action-b2-purchase' && params.decision === 'approve') {
            if (delayedPurchaseDecision) { delayedPurchaseDecision = false; await wait(4300) }
            return { ok: false, status: 'stale' }
          }
          if (params.action_id === 'action-b2-send-file' && params.decision === 'approve') throw new Error('DECISION_NETWORK_DOWN')
          return { ok: true }
        }
        if (method === 'get_settings') return { model: 'deepseek/deepseek-v4-flash/high', base_url: 'http://model.invalid', odoo_url: 'http://odoo.invalid', odoo_db: 'demo', odoo_username: 'admin', has_model_key: false, has_odoo_key: false, environment: 'demo' }
        if (method === 'save_settings') {
          if (saveBlocked) { saveBlocked = false; throw new Error("Error invoking remote method 'workbench:call': Error: CONFIG_BUSY") }
          return { model: 'deepseek/deepseek-v4-flash/high', base_url: 'http://model.invalid', odoo_url: 'http://odoo.invalid', odoo_db: 'demo', odoo_username: 'admin', has_model_key: false, has_odoo_key: false, environment: 'demo' }
        }
        if (method === 'export_business_report') return { cancelled: false, path: 'C:\\runtime\\business-b2-receipt.json', artifact: { id: 'artifact-b2', name: 'Business B2 回执.json', path: 'C:\\runtime\\business-b2-receipt.json', kind: 'business_receipt' } }
        if (method === 'open_business_artifact' || method === 'reveal_business_artifact') return { opened: true }
        if (method === 'open_odoo_record') return { opened: true }
        if (method === 'reconcile_action') return details(b3)
        if (method === 'send_message') {
          if (params.text === 'delayed mutation' || params.text === 'thinking hold') await wait(180)
          if (params.text === 'snapshot public') {
            sessionDetails['session-b'].messages.push({ id: 'snapshot-public', role: 'assistant', text: '快照中的公开回答', run_id: 'conversation-run-b', created_at: '2026-09-08T09:02:00Z' })
            await wait(30)
          }
          if (params.text === 'completed retry') {
            sessionDetails['session-b'].conversation_runs[0].status = 'completed'
            await wait(180)
            sessionDetails['session-b'].conversation_runs[0].status = 'running'
          }
          if (params.text === 'failed conversation') {
            sessionDetails['session-b'].conversation_runs[0].status = 'failed'
            sessionDetails['session-b'].conversation_runs[0].error = 'CONVERSATION_TOOL_FAILED'
            sessionDetails['session-b'].conversation_runs[0].error_detail = '当前回复未完成，检查后可以继续输入。'
          }
          return { ok: true, run_id: 'conversation-run-b' }
        }
        if (method === 'start_run') return { id: params.business_id + '-started-run', business_id: params.business_id, session_id: params.session_id, status: 'running', tool_count: 0, model_rounds: 0, elapsed_seconds: 0 }
        if (method === 'cancel_conversation') {
          sessionDetails['session-b'].conversation_runs[0].status = 'cancel_requested'
          window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', run_id: params.run_id, run_status: 'cancel_requested' } })
          await wait(40)
          sessionDetails['session-b'].conversation_runs[0].status = 'cancelled'
          sessionDetails['session-b'].live_messages = [{ id: 'cancel-live', session_id: 'session-b', business_id: null, run_id: params.run_id, sequence: 0, text: '取消前已经收到的片段', role: 'assistant', status: 'interrupted' }]
          return { ok: true, run_id: params.run_id, status: 'cancelled' }
        }
        if (method === 'cancel_run' || method === 'confirm_business' || method === 'rename_session' || method === 'archive_session') return null
        throw new Error('unexpected bridge method: ' + method)
      }
    }
  })()
`

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
await context.addInitScript({ content: bridgeScript })
const page = await context.newPage()
const pageErrors = []
page.on('pageerror', (error) => { pageErrors.push(error.message); console.error(`renderer page error: ${error.message}`) })
page.on('console', (message) => { if (message.type() === 'error') console.error(`renderer console error: ${message.text()}`) })
await page.goto(`http://127.0.0.1:${serverAddress.port}/`)
await page.getByRole('button', { name: /Session A/ }).waitFor()
await page.getByText('主机已连接').waitFor()

// A new or empty session explains the three supported business entry points;
// choosing one only fills natural language into the composer.
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
await page.getByText('你想先处理哪类业务？').waitFor()
await page.getByRole('button', { name: /^采购 / }).click()
assert.equal(await page.getByRole('textbox', { name: '会话消息' }).inputValue(), '我想整理一笔采购需求，请先告诉我需要补充哪些信息。')

// Materials are imported through the bridge, appear with parsing metadata, and
// travel with the same-session message. A delayed old-session import is ignored.
const materialInput = page.locator('input[type="file"]').first()
await materialInput.setInputFiles({ name: 'orders.csv', mimeType: 'text/csv', buffer: Buffer.from('客户,产品,数量\nNimbus,服务,2') })
await page.getByText('orders.csv', { exact: true }).waitFor()
await page.getByRole('button', { name: '发送' }).click()
await page.waitForTimeout(35)
const materialSend = await page.evaluate(() => window.__bridgeCalls.find(({ method, params }) => method === 'send_message' && params.material_ids?.length))
assert.deepEqual(materialSend?.params.material_ids, ['material-1'])
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
await page.locator('input[type="file"]').first().setInputFiles({ name: 'cross.csv', mimeType: 'text/csv', buffer: Buffer.from('客户,产品\nA,B') })
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
await page.waitForTimeout(150)
assert.equal(await page.getByText('cross.csv', { exact: true }).count(), 0)

// A is deliberately slow. Switching to B while A is in flight must leave B visible.
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
const userMessage = page.locator('.message.user').filter({ hasText: '我想查看订单' })
await userMessage.waitFor()
assert.equal(await userMessage.locator('.message-meta strong').count(), 0)
assert.equal(await userMessage.locator('.avatar').textContent(), '你')
await page.locator('.conversation-pane').waitFor()
await page.getByRole('button', { name: '收起会话', exact: true }).click()
assert.equal(await page.locator('.conversation-pane').count(), 0)
assert.equal(await page.evaluate(() => window.localStorage.getItem('odoo-workbench.conversation-open')), 'false')
await page.getByRole('button', { name: '打开会话', exact: true }).click()
await page.locator('.conversation-pane').waitFor()
const proposalButton = page.getByRole('button', { name: '创建业务工作区' })
await proposalButton.waitFor()
assert.equal(await page.getByText('主机合成的提案正文不应重复显示', { exact: true }).count(), 0)
assert.equal(await page.locator('.message.assistant:not(.thinking-message):not(.live-message)').count(), 1)
await page.getByText('完成目标：发票已过账', { exact: true }).waitFor()
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

// Historical proposal envelopes are hidden from the transcript. They must not
// suppress the welcome entry points when no proposal is pending or streaming.
const sendCallsBeforeProposalOnly = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'send_message').length)
await page.evaluate(() => {
  window.__setSessionMessages('session-a', [
    { id: 'old-proposal-user-1', role: 'user', text: '', created_at: '2026-09-08T07:50:00Z', proposal: { id: 'old-proposal-1', title: '旧提案', goal: '已处理', type: 'sale_invoice', status: 'rejected' } },
    { id: 'old-proposal-user-2', role: 'user', text: '', created_at: '2026-09-08T07:51:00Z', proposal: { id: 'old-proposal-2', title: '旧提案二', goal: '已处理', type: 'purchase', status: 'confirmed' } }
  ])
  window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-a', status: 'idle' } })
})
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
await page.getByText('你想先处理哪类业务？').waitFor()
assert.equal(await page.locator('.welcome-card').count(), 3)
assert.equal(await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'send_message').length), sendCallsBeforeProposalOnly)
await page.evaluate(() => { window.__setSessionMessages('session-a', []); window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-a', status: 'idle' } }) })
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()

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

// Composer target is explicit: current-business followups carry context_business_id; ordinary discussion omits business scope.
const messageTarget = page.getByLabel('讨论范围')
const composer = page.getByRole('textbox', { name: '会话消息' })
await messageTarget.selectOption('business-b2')
await composer.fill('继续处理当前业务')
await page.getByRole('button', { name: '发送' }).click()
await page.waitForTimeout(25)
await messageTarget.selectOption('__conversation__')
await composer.fill('开始一个新的业务意图')
await page.getByRole('button', { name: '发送' }).click()
await page.waitForTimeout(25)
const sentMessages = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'send_message' && params.session_id === 'session-b'))
assert.deepEqual(sentMessages.map(({ params }) => params), [
  { session_id: 'session-b', text: '继续处理当前业务', context_business_id: 'business-b2' },
  { session_id: 'session-b', text: '开始一个新的业务意图' }
])
await messageTarget.selectOption('__conversation__')
await composer.fill('thinking hold')
await page.getByRole('button', { name: '发送' }).click()
await page.locator('.thinking-message').waitFor()
assert.equal(await page.locator('.thinking-message .agent-avatar').count(), 1)
await page.getByRole('button', { name: /Session A/ }).click()
await page.getByRole('heading', { name: 'Session A' }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)
await page.getByRole('button', { name: /Session B/ }).click()
await page.getByRole('heading', { name: 'Session B' }).waitFor()
await page.waitForTimeout(220)
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
await messageTarget.selectOption('business-b2')
await composer.fill('dedupe send')
await page.evaluate(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('发送'))
  for (let index = 0; index < 5; index += 1) button?.click()
})
await page.waitForTimeout(25)
const dedupeSendCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'send_message' && params.text === 'dedupe send'))
assert.equal(dedupeSendCalls.length, 1)

// Stream events are scoped by session, business, run and message. Duplicate
// chunks are ignored, the terminal text is authoritative, and a business stream
// remains visible beside ordinary conversation output.
await page.evaluate(() => {
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-a', business_id: null, run_id: 'conversation-run-b', message_id: 'wrong-session', sequence: 0, text: '不能显示' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b1', run_id: 'business-b2-run', message_id: 'wrong-business', sequence: 0, text: '不能显示' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'conversation-live-1', sequence: 0, text: '临时片段' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'conversation-live-1', sequence: 0, text: '重复片段' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'conversation-live-1', sequence: 1, text: '第二片段' } })
  window.__emitWorkbench({ event: 'message_end', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'conversation-live-1', sequence: 2, text: '最终回答：当前能力与业务范围已确认。\n\n| 项目 | 状态 |\n| --- | --- |\n| 能力 | 已确认 |' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'conversation-live-1', sequence: 3, text: '终态后不应追加' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', message_id: 'business-live-2', sequence: 0, text: '业务流公开进度' } })
  window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'cancel-live', sequence: 0, text: '取消前已经收到的片段' } })
})
await page.getByText('最终回答：当前能力与业务范围已确认。', { exact: true }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)
assert.equal(await page.getByText('重复片段', { exact: true }).count(), 0)
assert.equal(await page.getByText('终态后不应追加', { exact: true }).count(), 0)
await page.getByText('业务流公开进度', { exact: true }).waitFor()
await page.getByText('取消前已经收到的片段', { exact: true }).waitFor()
await page.evaluate(() => {
  window.__persistConversationMessage({ id: 'conversation-live-1', role: 'assistant', text: '最终回答：当前能力与业务范围已确认。\n\n| 项目 | 状态 |\n| --- | --- |\n| 能力 | 已确认 |', created_at: '2026-09-08T09:01:00Z', context_business_id: null })
  window.__persistConversationMessage({ id: 'long-markdown', role: 'assistant', text: '## 长摘要\n\n' + '公开业务内容 '.repeat(200), created_at: '2026-09-08T09:01:10Z', context_business_id: null })
  window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', status: 'completed' } })
})
await page.waitForTimeout(40)
assert.equal(await page.getByText('最终回答：当前能力与业务范围已确认。', { exact: true }).count(), 1)
assert.equal(await page.locator('.conversation-pane .message-markdown table').count(), 1)
assert.equal(await page.locator('.conversation-pane .message-full h2').count(), 1)
assert.equal(await page.getByText('## 长摘要', { exact: true }).count(), 0)
const tableOverflow = await page.locator('.conversation-pane .message-table-wrap').evaluate((element) => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, pageWidth: document.documentElement.scrollWidth, viewport: window.innerWidth }))
assert.ok(tableOverflow.pageWidth <= tableOverflow.viewport + 1, JSON.stringify(tableOverflow))
const visibleStreamText = await page.locator('.conversation-pane').textContent()
assert.ok(!visibleStreamText?.includes('不能显示'))
const streamRows = await page.locator('.conversation-pane .live-message').count()
assert.ok(streamRows >= 2)
await page.getByRole('button', { name: /停止对话/ }).click()
await page.getByRole('button', { name: /正在停止/ }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)
await page.waitForTimeout(25)
const conversationCancelCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'cancel_conversation'))
assert.deepEqual(conversationCancelCalls.map(({ params }) => params), [{ session_id: 'session-b', run_id: 'conversation-run-b' }])
await page.getByText('已停止 · 回复未完成', { exact: true }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)
await composer.fill('failed conversation')
await page.getByRole('button', { name: '发送' }).click()
await page.getByText('对话失败，可继续输入', { exact: true }).waitFor()
await page.getByText('查看错误详情', { exact: true }).click()
await page.getByText('CONVERSATION_TOOL_FAILED', { exact: true }).waitFor()
assert.ok((await page.locator('.conversation-run-status').textContent())?.includes('当前回复未完成'))
await composer.fill('completed retry')
await page.getByRole('button', { name: '发送' }).click()
await page.locator('.thinking-message').waitFor()
await page.waitForTimeout(220)
await page.evaluate(() => window.__emitWorkbench({ event: 'message_delta', data: { session_id: 'session-b', business_id: null, run_id: 'conversation-run-b', message_id: 'retry-live', sequence: 0, text: '重试公开回复' } }))
await page.getByText('重试公开回复', { exact: true }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)

// A persisted assistant message for the active run is public output, so a
// reload snapshot must clear the thinking placeholder instead of duplicating it.
await composer.fill('snapshot public')
await page.getByRole('button', { name: '发送' }).click()
await page.getByText('快照中的公开回答', { exact: true }).waitFor()
assert.equal(await page.locator('.thinking-message').count(), 0)
await page.evaluate(() => window.__removeConversationMessage('snapshot-public'))

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
await page.getByText('范围：销售、采购与开票基础核验', { exact: true }).waitFor()
await page.getByRole('tab', { name: /^单据/ }).click()
const businessReadsBeforeExport = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length)
await page.getByRole('button', { name: '导出业务回执' }).click()
await page.getByRole('status').getByText('C:\\runtime\\business-b2-receipt.json').waitFor()
const businessReadsAfterExport = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length)
assert.ok(businessReadsAfterExport > businessReadsBeforeExport)
const exportCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'export_business_report'))
assert.deepEqual(exportCalls.map(({ params }) => params), [{ session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run' }])
await page.getByText('文件与回执', { exact: true }).waitFor()
await page.getByText('Business B2 回执.json', { exact: true }).waitFor()
await page.locator('.artifact-row').filter({ hasText: 'PDF 单据' }).waitFor()
await page.locator('.artifact-row').filter({ hasText: '明细 CSV' }).waitFor()
const businessArtifact = page.locator('.artifact-row').filter({ hasText: 'Business B2 回执.json' })
await businessArtifact.getByRole('button', { name: '打开文件' }).click()
await businessArtifact.getByRole('button', { name: '显示位置' }).click()
const artifactCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'open_business_artifact' || method === 'reveal_business_artifact'))
assert.deepEqual(artifactCalls.map(({ method, params }) => ({ method, params })), [
  { method: 'open_business_artifact', params: { session_id: 'session-b', business_id: 'business-b2', artifact_id: 'artifact-b2' } },
  { method: 'reveal_business_artifact', params: { session_id: 'session-b', business_id: 'business-b2', artifact_id: 'artifact-b2' } }
])
await page.getByRole('button', { name: '在 Odoo 打开' }).first().click()
const openRecordCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'open_odoo_record'))
assert.deepEqual(openRecordCalls.map(({ params }) => params), [{ session_id: 'session-b', business_id: 'business-b2', model: 'sale.order', record_id: 1 }])
await page.getByRole('button', { name: /^PO-B2 采购订单/ }).click()
await page.getByRole('heading', { name: 'PO-B2' }).waitFor()
const readsBeforeArtifactRefresh = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length)
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: 'business-b2', type: 'artifact_created' } }))
await page.waitForFunction((before) => window.__bridgeCalls.filter(({ method, params }) => method === 'get_business' && params.business_id === 'business-b2').length > before, readsBeforeArtifactRefresh)
await page.getByRole('heading', { name: 'PO-B2' }).waitFor()
assert.equal(await page.locator('.resource-preview h3').textContent(), 'PO-B2')
await page.getByRole('tab', { name: /Business B1/ }).click()
await page.getByRole('heading', { name: 'Business B1' }).waitFor()
assert.equal(await page.getByText('C:\\runtime\\business-b2-receipt.json', { exact: true }).count(), 0)
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
await page.getByText('完成目标', { exact: true }).waitFor()
await page.getByText('发票已过账', { exact: true }).waitFor()
await page.getByRole('tab', { name: /^执行台/ }).click()
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
await page.getByText('业务单据', { exact: true }).waitFor()
await page.getByText('往来单位与明细', { exact: true }).waitFor()
await page.getByText('参考记录', { exact: true }).waitFor()
assert.equal(await page.locator('.resource-preview h3').textContent(), 'SO-B2')
assert.equal(await page.locator('.resource-group').filter({ hasText: '业务单据' }).getByRole('button', { name: /SO-B2/ }).count(), 2)
assert.equal(await page.locator('.resource-group').filter({ hasText: '业务单据' }).getByRole('button', { name: /PO-B2/ }).count(), 1)
assert.ok((await page.getByRole('button', { name: /PO-B2/ }).textContent())?.includes('待二次确认'))
assert.equal(await page.locator('.resource-row').filter({ hasText: '业务留言' }).count(), 7)
assert.equal(await page.locator('.resource-row').filter({ hasText: '销售明细' }).count(), 2)
assert.equal(await page.locator('.resource-group').filter({ hasText: '往来单位与明细' }).getByRole('button', { name: /S00001 明细/ }).count(), 0)
assert.equal(await page.locator('.resource-group').filter({ hasText: '参考记录' }).getByRole('button', { name: /S00001/ }).count(), 2)
await page.getByRole('button', { name: /S00001/ }).first().click()
assert.equal(await page.locator('.resource-preview h3').textContent(), 'S00001')
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: 'business-b2', type: 'artifact_created' } }))
await page.waitForTimeout(80)
assert.equal(await page.locator('.resource-preview h3').textContent(), 'S00001')
assert.ok((await page.getByRole('button', { name: /Alpine Supplier/ }).textContent())?.includes('往来单位 · —（不适用）'))
assert.ok((await page.getByRole('button', { name: /Alpine 供货信息/ }).textContent())?.includes('—（不适用）'))
assert.ok((await page.getByRole('button', { name: /销售税/ }).textContent())?.includes('—（不适用）'))
assert.equal(await page.locator('.resource-row').filter({ hasText: '产品' }).count(), 1)
assert.equal(await page.locator('.resource-row').filter({ hasText: '会计日记账' }).count(), 1)
assert.equal(await page.locator('.resource-row').filter({ hasText: '开票向导' }).count(), 1)
assert.ok((await page.getByRole('button', { name: /P00001 明细 1/ }).textContent())?.includes('采购明细 · —（不适用）'))
assert.ok((await page.getByRole('button', { name: /服务项目变体/ }).textContent())?.includes('商品 · —（不适用）'))
assert.ok((await page.getByRole('button', { name: /INV-B2 PDF/ }).textContent())?.includes('附件 · —（不适用）'))
assert.ok((await page.getByRole('button', { name: /发票PDF向导/ }).textContent())?.includes('发票PDF向导 · —（不适用）'))
assert.ok((await page.locator('.resource-row').filter({ hasText: '业务留言 1' }).textContent())?.includes('独立回读'))
await page.getByRole('button', { name: /服务产品 产品/ }).click()
await page.locator('.resource-preview .state-neutral').waitFor()
assert.equal(await page.getByRole('button', { name: /下载 服务产品 的 PDF/ }).count(), 0)
assert.equal(await page.getByRole('button', { name: /导出 服务产品 的 CSV/ }).count(), 0)
await page.getByRole('button', { name: /SO-B2-SECOND/ }).click()
const secondOrderText = await page.locator('.resource-preview').textContent()
assert.ok(secondOrderText?.includes('开票:待开票'))
assert.ok(!secondOrderText?.includes('付款'))
await page.getByRole('button', { name: /^INV-B2 客户发票/ }).click()
await page.getByRole('heading', { name: 'INV-B2' }).waitFor()
const invoiceText = await page.locator('.resource-preview').textContent()
assert.ok(invoiceText?.includes('付款:未付款'))
assert.ok(invoiceText?.includes('正式 PDF:待生成'))
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')
assert.equal(await page.getByText('正在读取业务状态…').count(), 0)
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-old-run')
await page.getByRole('tab', { name: /^执行台/ }).click()
await page.getByRole('button', { name: '取消运行' }).waitFor()

// An unrelated changed event refreshes the session list, but does not steal the active business.
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-a', business_id: 'business-a1', status: 'completed' } }))
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: 'business-b1', run_status: 'running' } }))
await page.waitForTimeout(30)
assert.equal(await page.locator('.business-header h2').textContent(), 'Business B2')

// Verify both approval decisions carry the currently selected business and its run.
await page.getByRole('tab', { name: /^变更与审批/ }).click()
await page.getByText('创建客户发票', { exact: true }).waitFor()
const invoiceApproval = page.locator('.approval-row').filter({ hasText: '创建客户发票' })
await invoiceApproval.getByText('查看拟提交值与执行前状态', { exact: true }).click()
await invoiceApproval.getByText('拟提交值', { exact: true }).waitFor()
await invoiceApproval.getByText('执行前状态', { exact: true }).waitFor()
await invoiceApproval.getByText('预检 / 动作回执（未执行）').waitFor()
// Production create approvals use direct proposed values and an empty
// prestate records[] envelope. The field diff must keep the three columns
// distinct without inventing a visible `records` business field, while the
// expanded raw view must retain the original prestate shape.
const approvalDiff = invoiceApproval.locator('.field-diff')
await approvalDiff.waitFor()
const diffHeader = approvalDiff.locator('.field-diff-head > span')
assert.deepEqual(await diffHeader.allTextContents(), ['字段', '执行前', '拟提交'])
const diffText = await approvalDiff.textContent()
assert.ok(diffText?.includes('client_order_ref'))
assert.ok(diffText?.includes('新建 / 无前态'))
assert.ok(diffText?.includes('PI-DYNAMIC-MVP-20260908'))
assert.ok(diffText?.includes('Nimbus Bureau（ID 10）'))
assert.ok(diffText?.includes('30Days（ID 4）'))
assert.ok(diffText?.includes('商品 ID 2'))
assert.ok(diffText?.includes('数量 1'))
assert.ok(diffText?.includes('单价 695.22'))
assert.equal(diffText?.includes('记录（records）'), false)
const rawPrestate = await invoiceApproval.locator(':scope > details').first().locator('pre').nth(1).textContent()
assert.ok(rawPrestate?.includes('"records": []'))
const purchaseApproval = page.locator('.approval-row').filter({ hasText: '确认采购订单' })
await purchaseApproval.waitFor()
assert.ok((await purchaseApproval.textContent())?.includes('采购订单'))
assert.ok((await purchaseApproval.textContent())?.includes('P00001（ID 1）'))
assert.ok((await purchaseApproval.textContent())?.includes('供应商（partner_id）'))
assert.ok((await purchaseApproval.textContent())?.includes('数量 3'))
const sendFileApproval = page.locator('.approval-row').filter({ hasText: '生成正式发票文件' })
await sendFileApproval.waitFor()
assert.ok((await sendFileApproval.textContent())?.includes('发票文件向导'))
assert.ok((await sendFileApproval.textContent())?.includes('is_move_sent'))

// The approval and its completed write share action_id. Receipt navigation
// must select the latest matching executed tool, rather than the active
// validation or the earlier read tool.
await invoiceApproval.getByRole('button', { name: '查看关联运行回执' }).click()
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-toolbar select').inputValue(), 'business-b2-run')
await page.getByRole('heading', { name: 'execute_approved_write', exact: true }).waitFor()
const executedReceipt = await page.locator('.trace-detail-panel').textContent()
assert.ok(executedReceipt?.includes('已执行'))
assert.ok(executedReceipt?.includes('"write_id": 42'))
assert.equal(executedReceipt?.includes('回执尚未到达'), false)
// Selecting the Run node, then changing runs, clears the old action target
// and restores the selected run summary.
await page.locator('.trace-tree > .trace-node').first().click()
await page.getByRole('heading', { name: '运行总览', exact: true }).waitFor()
assert.ok((await page.locator('.trace-detail-panel').textContent())?.includes('等待审批'))
await page.locator('.trace-toolbar select').selectOption('business-b2-old-run')
await page.waitForFunction(() => window.__bridgeCalls.some(({ method, params }) => method === 'get_trace' && params.run_id === 'business-b2-old-run'))
await page.waitForTimeout(260)
await page.locator('.trace-detail-panel').getByText('本轮结束', { exact: true }).waitFor()
assert.equal(await page.locator('.trace-toolbar select').inputValue(), 'business-b2-old-run')
await page.getByRole('heading', { name: '运行总览', exact: true }).waitFor()
const historicalRunSummary = await page.locator('.trace-detail-panel').textContent()
assert.ok(historicalRunSummary?.includes('本轮结束'), historicalRunSummary)
assert.equal(historicalRunSummary?.includes('动作回执不可用'), false)
await page.getByRole('tab', { name: /^变更与审批/ }).click()
await page.getByText('创建客户发票', { exact: true }).waitFor()
await invoiceApproval.getByRole('button', { name: '批准这项业务动作' }).waitFor()
await page.waitForFunction(() => {
  const row = [...document.querySelectorAll('.approval-row')].find((item) => item.textContent?.includes('创建客户发票'))
  const button = row?.querySelector('button')
  return Boolean(button && !button.disabled)
})
await page.evaluate(() => {
  const row = [...document.querySelectorAll('.approval-row')].find((item) => item.textContent?.includes('创建客户发票'))
  const approve = [...(row?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('批准这项业务动作'))
  const reject = [...(row?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('拒绝'))
  for (let index = 0; index < 5; index += 1) (index % 2 === 0 ? approve : reject)?.click()
})
await page.waitForTimeout(30)
const repeatedApproveCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'decide_approval' && params.decision === 'approve'))
const repeatedRejectCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'decide_approval' && params.decision === 'reject'))
assert.equal(repeatedApproveCalls.length, 1)
assert.equal(repeatedRejectCalls.length, 0)
assert.equal(await page.evaluate(() => window.__bridgeCalls.some(({ method }) => method === 'execute_approved_write')), false)
await invoiceApproval.getByRole('button', { name: '拒绝' }).click()
await page.waitForTimeout(30)
const approvalCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'decide_approval'))
assert.deepEqual(approvalCalls.map(({ params }) => params), [
  { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', action_id: 'action-b2', decision: 'approve' },
  { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run', action_id: 'action-b2', decision: 'reject' }
])

// A stale decision stays visibly unresolved while the host call is pending,
// then reports failure without ever presenting the action as approved. Other
// pending actions remain available, and a thrown decision clears submitting.
await purchaseApproval.getByRole('button', { name: '批准这项业务动作' }).click()
await page.waitForTimeout(120)
assert.ok((await page.locator('.approval-progress').textContent())?.includes('正在提交审批决定'))
await page.getByText('业务状态已变化，审批未生效。', { exact: true }).first().waitFor({ timeout: 6000 })
assert.equal(await page.getByText('正在提交审批决定…', { exact: true }).count(), 0)
assert.ok((await page.locator('.approval-row').allTextContents()).join('\n').includes('生成正式发票文件'))
await sendFileApproval.getByRole('button', { name: '批准这项业务动作' }).click()
await page.getByText('DECISION_NETWORK_DOWN', { exact: true }).waitFor()
assert.equal(await page.getByText('正在提交审批决定…', { exact: true }).count(), 0)

// A delayed trace must not leak into another business; the final trace displays null usage as unknown.
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-run')
await page.getByRole('tab', { name: /Business B1/ }).click()
await page.getByRole('heading', { name: 'Business B1' }).waitFor()
assert.equal(await page.locator('.trace-page').count(), 0)
await page.waitForTimeout(250)
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-page .trace-toolbar select').inputValue(), 'business-b1-run')
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-run')
await page.getByText('未缓存输入 未知').first().waitFor()
const traceText = await page.locator('.trace-page').textContent()
assert.ok(traceText.includes('未缓存输入 未知'))
assert.ok(traceText.includes('mcp_odoo_validate_write'))
assert.equal(await page.locator('.trace-tool-node').filter({ hasText: 'mcp_odoo_validate_write' }).count(), 1)
await page.getByRole('button', { name: /第 1 轮/ }).click()
assert.ok((await page.locator('.trace-detail-panel').textContent()).length > 1500)
await page.getByRole('button', { name: /mcp_odoo_validate_write/ }).click()
const activeToolDetail = await page.locator('.trace-detail-panel').textContent()
assert.ok(activeToolDetail.includes('进行中'))
assert.ok(activeToolDetail.includes('未知'))
for (const label of ['未缓存输入 未知', '缓存命中 未知', '输出（含推理） 未知', '推理 未知', '总计 未知']) assert.ok(traceText.includes(label), `missing usage label: ${label}`)

// A trace event for the same run refreshes the visible trace without changing tabs or run scope.
const traceCallsBefore = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
const traceVersionBefore = await page.evaluate(() => window.__traceVersion)
await page.evaluate(() => window.__emitWorkbench({ event: 'run_trace', data: { session_id: 'session-b', business_id: 'business-b2', run_id: 'business-b2-run' } }))
await page.waitForFunction((before) => window.__traceVersion > before, traceVersionBefore)
const traceCallsAfter = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').length)
assert.ok(traceCallsAfter > traceCallsBefore)
const currentTraceVersion = await page.evaluate(() => window.__traceVersion)
await page.getByRole('button', { name: /read_business_records/ }).click()
assert.ok((await page.locator('.trace-detail-panel').textContent()).includes(`"version": ${currentTraceVersion}`))

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

// Production-shaped reconciliation case: inspecting an old run must preserve the
// current activity, and an uncertain write must remain unknown without a retry.
await page.getByRole('tab', { name: /Business B3/ }).click()
await page.getByRole('heading', { name: 'Business B3' }).waitFor()
const b3Goal = page.getByLabel('业务目标', { exact: true })
await b3Goal.waitFor()
const fullB3Goal = '核对中断写入是否已经落库，并保留当前运行与历史单据证据。'
assert.ok((await b3Goal.textContent()).includes('核对中断写入是否已经落库'))
await page.getByText('查看原始指令', { exact: true }).click()
await page.locator('.goal-details').getByText(fullB3Goal, { exact: true }).waitFor()
const currentActivity = await page.locator('.activity-card').textContent()
assert.ok(currentActivity?.includes('写入结果待核对'))
assert.equal(await page.getByRole('button', { name: /开始执行|继续执行|重新执行/ }).count(), 0)
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b3-old-run')
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-toolbar select').inputValue(), 'business-b3-old-run')
assert.ok((await page.locator('.trace-page').textContent()).includes('read_business_records'))
await page.getByRole('tab', { name: /^执行台/ }).click()
assert.equal(await page.locator('.activity-card').textContent(), currentActivity)
assert.ok((await page.locator('.business-content').textContent()).includes('系统不会自动重试'))
await page.getByRole('tab', { name: /^变更与审批/ }).click()
const reconcileButton = page.getByRole('button', { name: '核对不确定写入' })
await reconcileButton.waitFor()
await reconcileButton.click()
const reconcileCalls = await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'reconcile_action'))
assert.deepEqual(reconcileCalls.map(({ params }) => params), [{ session_id: 'session-b', business_id: 'business-b3', run_id: 'business-b3-run', action_id: 'action-b3' }])
assert.equal(await page.locator('.outcome-summary').getByText('本轮结束', { exact: true }).count(), 0)
assert.ok((await page.locator('.approval-row').textContent()).includes('需对账'))
assert.equal(await page.locator('.business-content button').filter({ hasText: /重试|重新执行/ }).count(), 0)
await page.getByRole('tab', { name: /^单据/ }).click()
assert.equal(await page.locator('.document-table a').count(), 0)

// Terminal approval records keep their terminal status after expiry; expiry must
// not rewrite verified/rejected history into a pending/expired decision.
await page.getByRole('tab', { name: /Business B4/ }).click()
await page.getByRole('heading', { name: 'Business B4' }).waitFor()
await page.getByRole('tab', { name: /^变更与审批/ }).click()
const terminalApprovals = page.locator('.approval-row')
await terminalApprovals.nth(1).waitFor()
assert.equal(await terminalApprovals.nth(0).locator('.state-badge').textContent(), '已核验')
assert.equal(await terminalApprovals.nth(1).locator('.state-badge').textContent(), '已拒绝')
assert.equal(await terminalApprovals.locator('.state-expired').count(), 0)

// Evidence navigation carries both identifiers: run selection and tool receipt.
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.getByRole('heading', { name: 'Business B2' }).waitFor()
await page.getByRole('tab', { name: /^执行台/ }).click()
await page.getByText('当前业务进度').waitFor()
await page.getByRole('button', { name: /读取订单/ }).click()
const exactReceipt = page.getByRole('button', { name: /查看订单读取回执/ })
await exactReceipt.waitFor()
await exactReceipt.click()
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-toolbar select').inputValue(), 'business-b2-run')
await page.locator('.trace-tool-node.active').filter({ hasText: 'read_business_records' }).waitFor()

// A readback link sourced from the historical run resolves to the current
// independent snapshot and never reports the snapshot as unavailable.
await page.getByRole('tab', { name: /^执行台/ }).click()
await page.getByRole('button', { name: /读取订单/ }).click()
await page.getByRole('button', { name: /查看独立回读快照/ }).click()
await page.locator('.trace-page .loading-line').waitFor({ state: 'hidden' })
assert.equal(await page.locator('.trace-toolbar select').inputValue(), 'business-b2-run')
const readbackDetail = await page.locator('.trace-detail-panel').textContent()
assert.ok(readbackDetail?.includes('独立 Odoo 回读快照'))
assert.equal(readbackDetail?.includes('当前运行没有匹配的独立回读快照'), false)
const readbackTraceCall = await page.evaluate(() => window.__bridgeCalls.filter(({ method, params }) => method === 'get_trace' && params.business_id === 'business-b2').at(-1))
assert.equal(readbackTraceCall?.params.run_id, 'business-b2-run')

// Host lifecycle events are visible and the banner can retry health after a crash/protocol error.
await page.evaluate(() => window.__emitWorkbench({ event: 'host_status', data: { status: 'crashed', code: 9 } }))
await page.getByText('主机已崩溃').waitFor()
await page.evaluate(() => window.__emitWorkbench({ event: 'host_protocol_error', data: { message: 'invalid test frame' } }))
await page.getByText('主机协议错误').waitFor()
await page.getByRole('alert').getByRole('button', { name: '重试连接' }).click({ force: true })
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
const divider = page.getByRole('separator', { name: '调整会话辅助面板宽度' })
await divider.focus()
for (let index = 0; index < 30; index += 1) await page.keyboard.press('ArrowLeft')
const overflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, grid: document.querySelector('.workspace-grid')?.scrollWidth ?? 0, gridClient: document.querySelector('.workspace-grid')?.clientWidth ?? 0 }))
assert.ok(overflow.document <= overflow.viewport + 1, JSON.stringify(overflow))
assert.ok(overflow.grid <= overflow.gridClient + 1, JSON.stringify(overflow))
await page.setViewportSize({ width: 1440, height: 900 })
await page.locator('.conversation-pane').waitFor()
const mediumOverflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, grid: document.querySelector('.workspace-grid')?.scrollWidth ?? 0, gridClient: document.querySelector('.workspace-grid')?.clientWidth ?? 0 }))
assert.ok(mediumOverflow.document <= mediumOverflow.viewport + 1, JSON.stringify(mediumOverflow))
assert.ok(mediumOverflow.grid <= mediumOverflow.gridClient + 1, JSON.stringify(mediumOverflow))
await page.setViewportSize({ width: 1280, height: 800 })
await page.locator('.conversation-pane').waitFor()
await divider.focus()
for (let index = 0; index < 30; index += 1) await page.keyboard.press('ArrowLeft')
const narrowOverflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, grid: document.querySelector('.workspace-grid')?.scrollWidth ?? 0, gridClient: document.querySelector('.workspace-grid')?.clientWidth ?? 0 }))
assert.ok(narrowOverflow.document <= narrowOverflow.viewport + 1, JSON.stringify(narrowOverflow))
assert.ok(narrowOverflow.grid <= narrowOverflow.gridClient + 1, JSON.stringify(narrowOverflow))
// At the compact viewport, the resource list scrolls independently so the
// selected document preview heading remains visible in the active tab.
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.evaluate(() => window.__showAcceptedProjection())
await page.evaluate(() => window.__emitWorkbench({ event: 'changed', data: { session_id: 'session-b', business_id: 'business-b2', run_status: 'running' } }))
await page.getByText('等待模型响应', { exact: true }).waitFor()
await page.waitForTimeout(4600)
await page.locator('.conversation-pane .approval-inbox-card').waitFor()
assert.ok((await page.locator('.conversation-pane .approval-inbox-card').textContent())?.includes('等待模型响应'))
assert.equal(await page.getByText('正在提交审批决定…', { exact: true }).count(), 0)
await page.locator('.material-reuse-tray summary').click()
await page.getByText('沿用历史材料', { exact: true }).waitFor()
assert.ok((await page.locator('.material-reuse-tray').textContent()).includes('订单材料.csv'))
const b2MaterialInput = page.locator('input[type="file"]').first()
await b2MaterialInput.setInputFiles({ name: 'orders.csv', mimeType: 'text/csv', buffer: Buffer.from('客户,产品,数量\nNimbus,服务,2') })
await page.getByText('orders.csv', { exact: true }).waitFor()
await b2MaterialInput.setInputFiles({ name: 'orders.csv', mimeType: 'text/csv', buffer: Buffer.from('客户,产品,数量\nNimbus,服务,2') })
await page.waitForTimeout(40)
assert.equal(await page.locator('.material-chip').filter({ hasText: 'orders.csv' }).count(), 1)
assert.ok((await page.locator('.material-reuse-tray').textContent()).includes('本轮不附'))
await page.locator('.material-chip button').click()
await page.getByRole('tab', { name: /^单据/ }).click()
await page.locator('.material-history').getByText('订单材料.csv', { exact: true }).waitFor()
await page.locator('.material-history-row span').filter({ hasText: '4 条数据' }).waitFor()
await page.getByRole('button', { name: /^INV-B2 客户发票/ }).click()
const previewHeading = page.locator('.resource-preview h3')
await previewHeading.waitFor()
await previewHeading.scrollIntoViewIfNeeded()
assert.equal(await previewHeading.textContent(), 'INV-B2')
const previewVisibility = await page.evaluate(() => {
  const heading = document.querySelector('.resource-preview h3')?.getBoundingClientRect()
  const activeTab = document.querySelector('.business-content > [data-state="active"]')?.getBoundingClientRect()
  if (!heading || !activeTab) return { visible: false }
  return { visible: heading.top >= activeTab.top && heading.bottom <= Math.min(activeTab.bottom, window.innerHeight) }
})
assert.equal(previewVisibility.visible, true, JSON.stringify(previewVisibility))
await page.getByRole('button', { name: /^SO-B2 销售订单/ }).click()
await page.getByRole('button', { name: '下载 SO-B2 的 PDF' }).click()
await page.getByText('已取消，可重试').waitFor()
await page.getByRole('button', { name: '导出 SO-B2 的 CSV' }).click()
await page.getByText('SO-B2.csv', { exact: true }).waitFor()
await page.getByRole('button', { name: '打开', exact: true }).click()
assert.ok((await page.evaluate(() => window.__bridgeCalls.filter(({ method }) => method === 'open_business_artifact'))).length >= 1)
await page.getByRole('button', { name: /^INV-B2 客户发票/ }).click()
await page.getByRole('button', { name: '下载 INV-B2 的 PDF' }).click()
await page.getByRole('alert').getByText('发票已过账，但尚未生成正式 PDF，请先生成发票文件后再下载。', { exact: true }).waitFor()
const actionSpacing = await page.evaluate(() => ({
  documentMargin: getComputedStyle(document.querySelector('.document-download-action .inline-action')).margin,
  documentGap: getComputedStyle(document.querySelector('.document-download-actions')).columnGap,
  artifactGap: getComputedStyle(document.querySelector('.artifact-actions')).columnGap
}))
assert.equal(actionSpacing.documentMargin, '0px')
assert.equal(actionSpacing.documentGap, '14px')
assert.equal(actionSpacing.artifactGap, '12px')
await page.setViewportSize({ width: 1600, height: 1000 })
await page.getByLabel('业务目标', {exact: true}).evaluate(e => { e.textContent = '完整业务目标 '.repeat(1000) })
assert.ok(await page.getByLabel('业务目标', {exact: true}).evaluate(e => e.clientHeight < 100 && e.scrollHeight > e.clientHeight))
await page.getByLabel('业务目标', {exact: true}).evaluate(e => { e.textContent = '处理订单与发票' })
const globalAlert = page.locator('.global-alert')
if (await globalAlert.isVisible().catch(() => false)) {
  await globalAlert.getByRole('button', { name: '关闭', exact: true }).click()
}
await page.setViewportSize({ width: 1100, height: 640 })
await page.getByRole('tab', { name: /Business B3/ }).click()
await page.locator('.business-header h2').waitFor()
const compactLayout = await page.evaluate(() => ({
  document: document.documentElement.scrollWidth,
  viewport: window.innerWidth,
  header: document.querySelector('.business-header h2')?.getBoundingClientRect(),
  composer: document.querySelector('.composer-footer')?.getBoundingClientRect(),
  actions: document.querySelector('.composer-actions')?.getBoundingClientRect(),
  send: document.querySelector('.composer-actions button[type="submit"]')?.getBoundingClientRect(),
  sendScrollWidth: document.querySelector('.composer-actions button[type="submit"]')?.scrollWidth,
  sendClientWidth: document.querySelector('.composer-actions button[type="submit"]')?.clientWidth,
  titleScrollWidth: document.querySelector('.business-header h2')?.scrollWidth,
  titleClientWidth: document.querySelector('.business-header h2')?.clientWidth,
}))
assert.ok(compactLayout.document <= compactLayout.viewport + 1, JSON.stringify(compactLayout))
assert.ok(compactLayout.header && compactLayout.header.width > 0 && compactLayout.header.height > 0, JSON.stringify(compactLayout))
assert.ok(compactLayout.composer && compactLayout.actions && compactLayout.send, JSON.stringify(compactLayout))
assert.ok(compactLayout.actions.right <= compactLayout.composer.right + 1 && compactLayout.actions.left >= compactLayout.composer.left - 1, JSON.stringify(compactLayout))
assert.ok(compactLayout.send.right <= compactLayout.actions.right + 1 && compactLayout.send.left >= compactLayout.actions.left - 1 && compactLayout.send.top >= compactLayout.composer.top - 1 && compactLayout.send.bottom <= compactLayout.composer.bottom + 1, JSON.stringify(compactLayout))
assert.ok(compactLayout.sendScrollWidth <= compactLayout.sendClientWidth, JSON.stringify(compactLayout))
assert.equal(compactLayout.titleScrollWidth, compactLayout.titleClientWidth)
await page.setViewportSize({ width: 1280, height: 800 })
await page.getByRole('tab', { name: /Business B2/ }).click()
await page.evaluate(() => window.__showCompactionUsage())
await page.getByRole('tab', { name: /^运行详情/ }).click()
await page.locator('.trace-toolbar select').selectOption('business-b2-run')
await page.locator('.trace-detail-content').getByText('含上下文压缩 1 次 · 100 token', { exact: true }).waitFor()
assert.equal(await page.getByText('含上下文压缩 0 次', { exact: false }).count(), 0)
await page.evaluate(() => window.__showAcceptedProjection(false))
const businessB3Tab = page.getByRole('tab', { name: /Business B3/ })
await businessB3Tab.evaluate((element) => {
  const list = element.parentElement
  if (list) list.scrollLeft = Math.max(0, element.offsetLeft + element.offsetWidth - list.clientWidth + 12)
})
await businessB3Tab.click()
const businessB2Tab = page.getByRole('tab', { name: /Business B2/ })
await businessB2Tab.evaluate((element) => {
  const list = element.parentElement
  if (list) list.scrollLeft = Math.max(0, element.offsetLeft + element.offsetWidth - list.clientWidth + 12)
})
await businessB2Tab.click()
await page.getByRole('tab', { name: /^变更与审批/ }).click()
const approvalLayout = await page.locator('.approval-actions:visible').evaluateAll((rows) => rows.flatMap((row) => Array.from(row.querySelectorAll('button')).map((button) => {
  const rowBox = row.getBoundingClientRect()
  const buttonBox = button.getBoundingClientRect()
  return { rowBox, buttonBox, disabled: button.disabled }
})))
assert.ok(approvalLayout.length > 0, 'expected visible approval actions')
const approvalBounds = await page.locator('.approval-row').first().evaluate((row) => ({ row: row.getBoundingClientRect(), pane: document.querySelector('.business-workspace')?.getBoundingClientRect() }))
assert.ok(approvalBounds.pane && approvalBounds.row.right <= approvalBounds.pane.right + 1, JSON.stringify(approvalBounds))
for (const { rowBox, buttonBox, disabled } of approvalLayout) {
  assert.ok(buttonBox.width > 0 && buttonBox.height > 0 && buttonBox.left >= rowBox.left - 1 && buttonBox.right <= rowBox.right + 1, JSON.stringify({ rowBox, buttonBox }))
  assert.equal(disabled, false)
}
await page.setViewportSize({ width: 1600, height: 1000 })
assert.equal(pageErrors.length, 0, pageErrors.join('\n'))

const calls = await page.evaluate(() => window.__bridgeCalls.map(({ method }) => method))
assert.ok(calls.includes('get_session') && calls.includes('get_business') && calls.includes('get_trace'))
if (process.env.RENDERER_CHECK_SCREENSHOTS) {
  const screenshotDir = fileURLToPath(new URL('../../.runtime/desktop-refinement/', import.meta.url))
  mkdirSync(screenshotDir, { recursive: true })
  for (const [width, height] of [[1280, 800], [1600, 1000]]) {
    await page.setViewportSize({ width, height })
    for (const [name, label] of [['execution', /^执行台/], ['documents', /^单据/], ['approvals', /^变更与审批/], ['trace', /^运行详情/]]) {
      await page.getByRole('tab', { name: label }).click()
      await page.locator('.business-content .loading-line').waitFor({ state: 'hidden' })
      await page.screenshot({ path: resolve(screenshotDir, `${name}-${width}x${height}.png`), fullPage: false })
    }
  }
}
console.log('renderer-check: PASS')
console.log('checked: session/business/trace stale guards, same-run trace refresh, changed routing, host crash/retry, message scope, session search, approval scope+preflight labels, purchase/file approval labels, invoice PDF availability, business-chain scope labels, document selection across refresh, CONFIG_BUSY mapping, settings save+Escape, splitter overflow, evidence navigation, unknown-write no-retry, historical activity preservation')
console.log(`pressure: session50=${sessionPressureElapsed}ms business50=${businessPressureElapsed}ms business20sequential=${sequentialBusinessElapsed}ms trace300=${traceBurstElapsed}ms trace_rpc_delta=${burstTraceCallsAfter - burstTraceCallsBefore} business_rpc_delta=${burstBusinessCallsAfter - burstBusinessCallsBefore} repeated={proposal:${proposalCalls.length},send:${dedupeSendCalls.length},start:${startCalls.length},approve:${repeatedApproveCalls.length},cancel:${cancelCalls.length}}`)
await browser.close()
server.close()
