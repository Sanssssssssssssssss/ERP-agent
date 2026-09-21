import {
Approval,
Document,
Health,
jsonText,
labelFor,
runStatusLabel
} from './protocol'
import { ConnectionState,MaterialRecord } from './view-types'

export function isReferenceDocument(document: Document) { return Boolean(document.is_reference || document.document_scope === 'reference') }

export function documentFact(document: Document) {
  const fields = document.fields
  const entries = document.model === 'sale.order'
    ? [['客户', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['开票', invoiceStatusLabel(String(fields.invoice_status ?? '未知'))]]
    : document.model === 'mrp.production'
      ? [['产品', fields.product_id], ['计划数量', fields.product_qty], ['完工数量', fields.qty_produced]]
      : document.model === 'account.payment'
        ? [['往来单位', fields.partner_id], ['金额', fields.amount], ['银行匹配', fields.is_matched === true ? '已匹配' : '未核验'], ['原单', fields.invoice_ids]]
        : document.model === 'account.bank.statement.line'
          ? [['摘要', fields.payment_ref], ['金额', fields.amount], ['核销', fields.is_reconciled === true ? '已核销' : '未核验']]
    : document.model === 'account.move'
      ? [['往来单位', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['付款', paymentStatusLabel(String(fields.payment_state ?? '未知'))], ['余额', fields.amount_residual ?? fields.residual], ['正式 PDF', invoicePdfStatus(fields)]]
      : document.model === 'mail.message'
        ? [['主题', fields.subject], ['作者', fields.author_name ?? fields.author_id], ['留言', fields.body ?? fields.message]]
        : document.model === 'sale.order.line'
          ? [['产品', fields.product_name ?? fields.product_id], ['数量', fields.product_uom_qty ?? fields.quantity], ['单价', fields.price_unit]]
          : document.model === 'account.move.line'
            ? [['科目', fields.account_name ?? fields.account_id], ['借方', fields.debit], ['贷方', fields.credit]]
            : document.model === 'account.payment.term'
              ? [['付款条件', fields.name ?? fields.note], ['说明', fields.description]]
      : Object.entries(fields).slice(0, 3).map(([key, value]) => [key, value])
  return entries.filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => `${key}:${readableValue(value)}`).join(' · ') || '没有可显示的关键字段'
}

export function invoicePdfStatus(fields: Record<string, unknown>) {
  if (!Object.prototype.hasOwnProperty.call(fields, 'invoice_pdf_report_id')) return '未观测'
  return fields.invoice_pdf_report_id ? '已生成' : '待生成'
}

export function documentSource(document: Document) {
  const fields = document.fields as Record<string, unknown>
  const run = document.source_run_id ?? fields.source_run_id ?? fields.run_id
  const tool = document.source_tool_id ?? fields.source_tool_id ?? fields.tool_id
  return [document.source || '未知来源', run ? `运行 ${String(run)}` : '', tool ? `工具 ${String(tool)}` : ''].filter(Boolean).join(' · ')
}

export function documentSourceObservedAt(document: Document) {
  const fields = document.fields as Record<string, unknown>
  const value = (document as Document & { source_observed_at?: string }).source_observed_at ?? fields.source_observed_at
  return typeof value === 'string' ? value : undefined
}

export function documentSourceRun(document: Document) { return document.source_run_id || String(document.fields.source_run_id || '') || undefined }

export function documentSourceTool(document: Document) { return document.source_tool_id || String(document.fields.source_tool_id || '') || undefined }

export function resourceText(fields: Record<string, unknown>, key: string) {
  const value = fields[key] ?? fields[`${key}_text`] ?? fields[`${key}_content`]
  return value == null || value === '' ? '' : readableValue(value)
}

export function artifactKindLabel(kind?: string) { return ({ business_receipt: '业务回执', odoo_pdf: 'PDF 单据', odoo_csv: '明细 CSV', document_pdf: 'PDF 单据', document_csv: '明细 CSV' } as Record<string, string>)[kind || ''] || kind || '文件' }

export function documentModelLabel(model: string) { return ({ 'sale.order': '销售订单', 'purchase.order': '采购订单', 'account.move': '发票与贷项', 'stock.picking': '收发货与退货', 'mrp.production': '制造单', 'stock.move': '库存移动', 'account.payment': '收付款', 'account.bank.statement.line': '银行流水', 'account.partial.reconcile': '核销匹配', 'account.payment.register': '付款登记', 'account.move.reversal': '贷项向导', 'stock.return.picking': '退货向导', 'res.partner': '往来单位', 'mail.message': '业务留言', 'sale.order.line': '销售明细', 'purchase.order.line': '采购明细', 'account.move.line': '会计分录', 'account.payment.term': '付款条件', 'product.template': '产品', 'product.product': '商品', 'account.journal': '会计日记账', 'sale.advance.payment.inv': '开票向导', 'account.move.send.wizard': '发票文件向导', 'ir.attachment': '附件' } as Record<string, string>)[model] || model }

export function documentSourceLabel(source?: string) { return ({ odoo: 'Odoo 观测', odoo_rpc: 'Odoo 观测', refresh_native_read: '独立回读', native_read_receipt: '原始读取回执', native_action_readback: '动作回读', agent: 'Agent', host: '本地 host' } as Record<string, string>)[source || ''] || '其他来源' }

export function isDownloadableDocument(document: Document) { return ['sale.order', 'purchase.order', 'account.move'].includes(document.model) }

export function documentKey(document: Document) { return `${document.model}:${String(document.id)}` }

export function documentStateLabel(model: string, state?: string) {
  const labels: Record<string, Record<string, string>> = {
    'sale.order': { draft: '草稿', sent: '已发送', sale: '已确认', cancel: '已取消' },
    'purchase.order': { draft: '询价草稿', sent: '已发询价', 'to approve': '待二次确认', to_approve: '待二次确认', purchase: '已确认', done: '已锁定', cancel: '已取消' },
    'account.move': { draft: '草稿', posted: '已过账', cancel: '已取消' },
    'mrp.production': { draft: '草稿', confirmed: '已确认', progress: '生产中', to_close: '待完工', done: '已完成', cancel: '已取消' },
    'account.payment': { draft: '草稿', in_process: '处理中', paid: '已付款', canceled: '已取消', rejected: '已拒绝' },
    'stock.picking': { draft: '草稿', waiting: '等待', confirmed: '待处理', assigned: '已分配', done: '已完成', cancel: '已取消' }
  }
  const noIndependentState = ['res.partner', 'mail.message', 'sale.order.line', 'purchase.order.line', 'account.move.line', 'account.payment.term', 'product.template', 'product.product', 'account.journal', 'sale.advance.payment.inv', 'account.move.send.wizard', 'ir.attachment', 'product.supplierinfo', 'account.tax'].includes(model)
  return (state && labels[model]?.[state]) || (state ? '状态未知' : noIndependentState ? '—（不适用）' : '未知')
}

export function invoiceStatusLabel(value: string) { return ({ invoiced: '已开票', 'to invoice': '待开票', to_invoice: '待开票', no: '无需开票' } as Record<string, string>)[value] || value }

export function paymentStatusLabel(value: string) { return ({ paid: '已付款', not_paid: '未付款', partial: '部分付款', in_payment: '付款处理中', reversed: '已冲销' } as Record<string, string>)[value] || value }

export function amountWithCurrency(amount: string, currency: string) { return currency === '未知' || currency === '未观测' || currency === '—（不适用）' ? amount : `${amount} ${currency}` }

export function messageForError(reason: unknown) {
  let message = reason instanceof Error ? reason.message : String(reason)
  message = message.replace(/^Error invoking remote method ['"]workbench:call['"]:\s*Error:\s*/i, '')
  const knownCodes = new Set(['VALUEERROR', 'CONFIG_BUSY', 'CONNECTION_CHECK_BUSY', 'EXPORT_CANCELLED', 'ARTIFACT_FILE_MISSING', 'ARTIFACT_NOT_FOUND', 'ARTIFACT_FORMAT_INVALID', 'ARTIFACT_OPEN_FAILED', 'ARTIFACT_INDEX_FAILED', 'DOCUMENT_PDF_UNAVAILABLE', 'PDF_UNAVAILABLE', 'DOCUMENT_DOWNLOAD_FAILED', 'MATERIAL_TOO_LARGE', 'MATERIAL_UNSUPPORTED', 'MATERIAL_PARSE_FAILED', 'ODOO_RECORD_NOT_FOUND', 'ODOO_OPEN_UNAVAILABLE', 'ODOO_ORIGIN_MISMATCH'])
  const codePrefix = message.match(/^\[([A-Z0-9_]+)\]\s*/)
  if (codePrefix && knownCodes.has(codePrefix[1])) message = message.slice(codePrefix[0].length)
  return message.includes('CONFIG_BUSY') ? '当前有业务正在执行或等待审批，请结束后再修改连接设置。' : message.includes('CONNECTION_CHECK_BUSY') ? '执行期间显示最近检查结果，结束后可重新检查。' : message.includes('EXPORT_CANCELLED') ? '已取消导出业务回执。' : message.includes('ARTIFACT_FILE_MISSING') ? '文件已移动或删除，请重新导出。' : message.includes('ARTIFACT_NOT_FOUND') ? '当前业务没有此文件。' : message.includes('ARTIFACT_FORMAT_INVALID') ? '仅支持本业务已登记的 JSON 回执。' : message.includes('ARTIFACT_OPEN_FAILED') ? '系统无法打开文件，可尝试显示位置。' : message.includes('ARTIFACT_INDEX_FAILED') ? message : message.includes('DOCUMENT_PDF_UNAVAILABLE') || message.includes('PDF_UNAVAILABLE') ? '当前单据没有可用 PDF，请改用导出明细 CSV。' : message.includes('DOCUMENT_DOWNLOAD_FAILED') ? '单据下载失败，请稍后重试。' : message.includes('MATERIAL_TOO_LARGE') ? '材料超过 2 MiB 限制。' : message.includes('MATERIAL_UNSUPPORTED') ? '仅支持 CSV 或 TXT 材料。' : message.includes('MATERIAL_PARSE_FAILED') ? '材料解析失败，请检查文件内容。' : message.includes('ODOO_RECORD_NOT_FOUND') ? '该 Odoo 记录已不存在或不属于当前业务。' : message.includes('ODOO_OPEN_UNAVAILABLE') ? '当前无法打开 Odoo 记录，请检查 Odoo 连接。' : message.includes('ODOO_ORIGIN_MISMATCH') ? '该记录不属于当前配置的 Odoo 地址。' : message
}

export function connectionLabel(state: ConnectionState) { return state === 'connected' ? '主机已连接' : state === 'checking' ? '正在连接主机' : state === 'crashed' ? '主机已崩溃' : state === 'protocol_error' ? '主机协议错误' : '主机断开' }

export function odooHealthStatus(health: Health | null) { return health?.odoo?.status || health?.odoo_status || 'unchecked' }

export function healthLabel(status?: string) { return status === 'connected' || status === 'ready' || status === 'ok' ? '已连接' : status === 'configured' ? '已配置' : status === 'unconfigured' ? '未配置' : status === 'unavailable' ? '不可用' : status === 'permission_denied' ? '无权限' : status === 'error' ? '检查失败' : status === 'unchecked' ? '未检查' : status === 'disconnected' ? '断开' : '状态未知' }

export function runDisplayLabel(status?: string) { return status === 'completed' ? '本轮结束' : labelFor(runStatusLabel, status) }

export function roundStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', pending: '待处理', failed: '失败', interrupted: '已中断', cancelled: '已取消', awaiting_approval: '等待审批' }, status) }

export function toolStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', error: '错误', failed: '失败', executed: '已执行', awaiting_approval: '等待审批', pending: '待处理', interrupted: '已中断', cancelled: '已取消', unknown: '未知' }, status) }

export function activityPhaseLabel(phase?: string) { return ({ idle: '待执行', planning: '准备中', model: '分析业务目标', reading: '读取业务数据', tool: '调用业务工具', approval: '等待确认', executing: '执行中', cancelling: '正在取消', verifying: '回读核验', completed: '本轮结束', failed: '执行失败', interrupted: '已中断', cancelled: '已取消', reconciliation: '等待对账', unknown: '状态未知' } as Record<string, string>)[phase || ''] || '状态未知' }

export function businessTypeMeta(type?: string) { return ({ inventory: { title: '库存收发与退货', short: '库存' }, manufacturing: { title: '制造与补货', short: '制造' }, payment: { title: '客户收款与供应商付款', short: '收付款' }, refund: { title: '退货退款与贷项', short: '退款' }, reconciliation: { title: '银行与账务核销', short: '核销' }, sale_invoice: { title: '销售与开票', short: '销售发票' }, purchase: { title: '采购', short: '采购流程' }, sale_purchase_invoice: { title: '销售 → 采购 → 开票', short: '业务链' } } as Record<string, { title: string; short: string }>)[type || ''] || { title: '业务工作区', short: '业务' } }

export function completionTargetLabel(target?: string, type?: string) { const effective = target || (type === 'purchase' ? 'confirmed' : ['inventory', 'manufacturing'].includes(type || '') ? 'done' : ['payment', 'refund', 'reconciliation'].includes(type || '') ? 'reconciled' : 'posted'); return ({ read_only: '只读浏览', draft: '保留草稿', confirmed: '完成确认', posted: ['payment', 'refund'].includes(type || '') ? '单据已过账' : '发票已过账', done: '业务已完成', reconciled: '账务与银行已核销' } as Record<string, string>)[effective] || '完成目标未知' }

export function materialRowLabel(material: MaterialRecord) { if (material.row_count == null) return '行数未知'; return material.media_type === 'text/csv' ? `${Math.max(0, material.row_count - 1)} 条数据` : `${material.row_count} 行`; }

export function stageLabel(stage?: string) { return ({ material: '材料', read: '读取', quote: '报价', sales: '销售', confirm: '确认', purchase: '采购', invoice: '开票', inventory: '收发货', manufacturing: '制造', payment: '收付款', refund: '退款', reconciliation: '核销', verify: '核验' } as Record<string, string>)[stage || ''] || '未知阶段' }

export function stageStatusLabel(status?: string) { return ({ pending: '待处理', active: '进行中', awaiting_approval: '等待审批', observed: '已观测', verified: '已核验', failed: '失败', unknown: '未知' } as Record<string, string>)[status || ''] || '未知' }

export function outcomeScopeLabel(scope?: string) { const kind = scope?.split('_')[0]; if (['inventory', 'manufacturing', 'payment', 'refund', 'reconciliation'].includes(kind || '')) return `${businessTypeMeta(kind).title}核验`; return ({
  sale_invoice_read_only_checks: '销售订单与客户发票只读浏览',
  sale_invoice_draft_checks: '销售订单与客户发票草稿核验',
  sale_invoice_confirmed_checks: '销售订单与客户发票确认核验',
  sale_invoice_basic_checks: '销售订单与客户发票基础核验',
  purchase_read_only_checks: '采购订单只读浏览',
  purchase_draft_checks: '采购订单草稿核验',
  purchase_confirmed_checks: '采购订单确认核验',
  sale_purchase_invoice_read_only_checks: '销售、采购与开票只读浏览',
  sale_purchase_invoice_draft_checks: '销售、采购与开票草稿核验',
  sale_purchase_invoice_confirmed_checks: '销售、采购与开票确认核验',
  sale_purchase_invoice_posted_checks: '销售、采购与开票基础核验'
} as Record<string, string>)[scope || ''] || '业务范围未知' }

export function outcomeStatusLabel(status?: string) { return ({ unknown: '未知', passed: '通过', failed: '失败' } as Record<string, string>)[status || ''] || '状态未知' }

export function toolLabel(tool?: string) { return ({ mcp_odoo_read_record: '读取业务记录', mcp_odoo_read: '读取业务记录', mcp_odoo_validate_write: '预检业务动作', execute_approved_write: '执行已批准动作', refresh_business: '读取最新状态' } as Record<string, string>)[tool || ''] || '业务工具' }

export function isPendingApproval(approval: Approval) { return approval.status === 'pending' || approval.status === 'pending_approval' }

export function approvalStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '待审批', pending_approval: '待审批', approved: '已批准 / 待执行', rejected: '已拒绝',
    verified: '已核验', known_failed: '已知失败', not_executed: '未执行', interrupted: '已中断',
    stale: '已失效', expired: '已过期', needs_reconciliation: '需对账', executing: '执行中', executed: '已执行', failed: '执行失败'
  }
  return labels[status] || '状态未知'
}

export function operationLabel(operation: string) {
  const labels: Record<string, string> = { create: '创建', write: '修改', unlink: '删除', action_confirm: '确认', button_confirm: '确认采购订单', button_approve: '批准采购订单', create_invoices: '创建发票', action_post: '过账', action_send_and_print: '生成正式发票文件', button_validate: '完成收发货', button_mark_done: '生产完工', action_create_payments: '登记收付款', reverse_moves: '生成贷项', reconcile: '核销分录', action_create_returns: '创建退货', process: '处理后续单据' }
  return labels[operation] || operation
}

export function modelLabel(model: string) { return documentModelLabel(model) }

export function readableApprovalTitle(approval: Approval) {
  const title = approval.title?.trim()
  const generic = !title || ['erp write approval', 'write approval', 'approval required', 'business approval', 'action approval'].includes(title.toLowerCase())
  if (!generic) return title
  if (approval.operation === 'button_confirm' || approval.operation === 'button_approve') return operationLabel(approval.operation)
  return `${operationLabel(approval.operation)}${modelLabel(approval.model)}`
}

export function approvalResultLabel(status: string) {
  return status === 'pending' || status === 'pending_approval' ? '预检 / 动作回执（未执行）' : status === 'approved' ? '已批准 / 待执行' : status === 'rejected' || status === 'not_executed' ? '审批结果（未执行）' : ['verified', 'executed', 'completed', 'posted'].includes(status) ? '动作执行回执' : '动作回执'
}

export function environmentLabel(environment?: string) { return environment === 'configured' ? '已配置环境' : environment === 'demo' ? '演示环境' : '未知' }

export function readableValue(value: unknown): string {
  if (value == null || value === '') return '未知'
  if (Array.isArray(value)) {
    if (value.length === 2 && typeof value[0] === 'number' && typeof value[1] === 'string') return value[1]
    return value.map((item) => readableValue(item)).join(', ')
  }
  if (typeof value === 'object') return jsonText(value).replace(/\s+/g, ' ')
  return String(value)
}

export function compactGoal(goal?: string, documents: Document[] = []) {
  const value = goal?.trim()
  if (!value) return '按已确认目标处理销售订单与客户发票'
  if (/you may act autonomously|autonomous|authorization/i.test(value)) {
    const order = documents.find((document) => document.model === 'sale.order')
    return order?.name ? `处理销售订单 ${order.name} 与客户发票` : '按已确认目标处理销售订单与客户发票'
  }
  return value.length > 180 ? `${value.slice(0, 177)}…` : value
}
