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
  return documentFacts(document).map(({ label, value }) => `${label}：${value}`).join(' · ') || '详细信息尚未读取'
}

const hasDocumentValue = (value: unknown) => value !== undefined && value !== null && value !== '' && value !== false && (!Array.isArray(value) || value.length > 0)

export function documentMoney(value: unknown, fields: Record<string, unknown>) {
  if (!hasDocumentValue(value)) return ''
  const currency = fields.currency ?? fields.currency_name ?? fields.currency_id
  // Only an observed currency name is useful here; a numeric relation ID is not a currency.
  const name = Array.isArray(currency) && typeof currency[1] === 'string' ? currency[1] : typeof currency === 'string' ? currency : ''
  const amount = typeof value === 'number' ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 }) : readableValue(value)
  return `${amount}${name ? ` ${name}` : '（币种未读取）'}`
}

export function documentQuantity(fields: Record<string, unknown>, key?: string) {
  const value = key ? fields[key] : fields.product_uom_qty ?? fields.product_qty ?? fields.quantity
  if (!hasDocumentValue(value)) return ''
  const unit = fields.product_uom_id ?? fields.product_uom ?? fields.uom_id
  const name = Array.isArray(unit) && typeof unit[1] === 'string' ? unit[1] : typeof unit === 'string' ? unit : ''
  return `${readableValue(value)}${name ? ` ${name}` : '（单位未读取）'}`
}

export function documentCurrencyFields(document: Document, documents: Document[] = []) {
  const fields = document.fields
  if (fields.currency || fields.currency_name || fields.currency_id) return fields
  const parentModel = ({ 'sale.order.line': 'sale.order', 'purchase.order.line': 'purchase.order', 'account.move.line': 'account.move' } as Record<string, string>)[document.model]
  const relation = document.model === 'account.move.line' ? fields.move_id : fields.order_id
  const parentId = Array.isArray(relation) ? relation[0] : relation
  const parent = parentModel && parentId != null ? documents.find((item) => item.model === parentModel && String(item.id) === String(parentId)) : undefined
  return parent ? { ...fields, currency: parent.fields.currency ?? parent.fields.currency_name ?? parent.fields.currency_id } : fields
}

export function documentAmount(document: Document, documents: Document[] = []) {
  const fields = documentCurrencyFields(document, documents)
  const value = ['sale.order', 'purchase.order', 'account.move'].includes(document.model)
    ? fields.amount_total ?? fields.total
    : ['account.payment', 'account.bank.statement.line'].includes(document.model)
      ? fields.amount
      : ['sale.order.line', 'purchase.order.line', 'account.move.line'].includes(document.model)
        ? fields.price_total ?? fields.price_subtotal : undefined
  return documentMoney(value, fields)
}

export function documentTypeLabel(document: Document) {
  return document.model === 'account.move'
    ? ({ out_invoice: '客户发票', in_invoice: '供应商账单', out_refund: '客户贷项', in_refund: '供应商贷项', entry: '会计凭证' } as Record<string, string>)[String(document.fields.move_type)] || documentModelLabel(document.model)
    : documentModelLabel(document.model)
}

export function documentFacts(document: Document, documents: Document[] = []): { label: string; value: string }[] {
  const f = documentCurrencyFields(document, documents)
  const party = f.partner_name ?? f.customer ?? f.vendor ?? f.partner_id
  const money = (value: unknown) => documentMoney(value, f)
  const count = (value: unknown) => Array.isArray(value) ? `${value.length} 条` : undefined
  const flag = (value: unknown, yes: string, no: string) => typeof value === 'boolean' ? value ? yes : no : undefined
  let entries: [string, unknown][] = []
  if (document.model === 'res.partner') {
    entries = [['邮箱', f.email], ['电话', f.phone ?? f.mobile], ['所属公司', f.company_id], ['所属客户', f.parent_id ?? f.commercial_partner_id], ['联系人用途', ({ invoice: '账单联系人', delivery: '收货联系人', contact: '联系人', other: '其他联系人' } as Record<string, string>)[String(f.type)]], ['职务', f.function], ['地址', [f.street, f.street2, f.city, f.state_id, f.country_id].filter(hasDocumentValue).map(readableValue).join(' ')], ['税号', f.vat]]
  } else if (['sale.order', 'purchase.order'].includes(document.model)) {
    entries = [[document.model === 'sale.order' ? '客户' : '供应商', party], ['含税金额', money(f.amount_total ?? f.total)], ['公司', f.company_id], ['明细记录', count(f.order_line)], ['付款条件', f.payment_term_id], ['开票状态', f.invoice_status ? invoiceStatusLabel(String(f.invoice_status)) : undefined], ['客户参考', f.client_order_ref], ['来源单据', f.origin], ['下单日期', f.date_order], ['交付日期', f.commitment_date ?? f.date_planned]]
  } else if (document.model === 'account.move') {
    entries = [['往来单位', party], ['含税金额', money(f.amount_total ?? f.total)], ['未付余额', money(f.amount_residual ?? f.residual)], ['付款状态', f.payment_state ? paymentStatusLabel(String(f.payment_state)) : undefined], ['公司', f.company_id], ['来源单据', f.invoice_origin], ['原发票', f.reversed_entry_id], ['开票日期', f.invoice_date], ['明细记录', count(f.invoice_line_ids ?? f.line_ids)], ['正式 PDF', Object.hasOwn(f, 'invoice_pdf_report_id') ? invoicePdfStatus(f) : undefined]]
  } else if (document.model.endsWith('.line') && document.model !== 'account.bank.statement.line') {
    entries = [['产品', f.product_name ?? f.product_id], ['所属单据', f.order_id ?? f.move_id], ['数量', documentQuantity(f)], ['单价', money(f.price_unit)], ['未税小计', money(f.price_subtotal)], ['含税小计', money(f.price_total)], ['科目', f.account_name ?? f.account_id], ['借方', documentMoney(f.debit, { currency: f.company_currency_id })], ['贷方', documentMoney(f.credit, { currency: f.company_currency_id })], ['核销状态', flag(f.reconciled, '已核销', '未核销')]]
  } else if (document.model === 'mrp.production') {
    entries = [['产品', f.product_id], ['计划数量', documentQuantity(f, 'product_qty')], ['完工数量', documentQuantity(f, 'qty_produced')], ['公司', f.company_id], ['物料清单', f.bom_id], ['投产日期', f.date_start], ['完工日期', f.date_finished]]
  } else if (document.model === 'stock.picking' || document.model === 'stock.move') {
    entries = [['往来单位', party], ['公司', f.company_id], ['产品', f.product_id], ['需求数量', documentQuantity(f, 'product_uom_qty')], ['完成数量', documentQuantity(f, 'quantity')], ['来源单据', f.origin ?? f.sale_id], ['原退货单', f.return_id ?? f.origin_returned_move_id], ['出库位置', f.location_id], ['入库位置', f.location_dest_id], ['计划日期', f.scheduled_date], ['明细记录', count(f.move_ids)]]
  } else if (document.model === 'account.payment' || document.model === 'account.bank.statement.line') {
    entries = [['往来单位', party], ['金额', money(f.amount)], ['公司', f.company_id], ['摘要', f.payment_ref], ['会计凭证', f.move_id], ['银行匹配', flag(f.is_matched, '已匹配', '未匹配')], ['核销状态', flag(f.is_reconciled, '已核销', '未核销')]]
  } else if (document.model === 'mail.message') {
    entries = [['主题', f.subject], ['作者', f.author_name ?? f.author_id], ['收件邮箱', f.outgoing_email_to], ['发送日期', f.date]]
  } else if (document.model === 'ir.attachment') {
    entries = [['文件名', f.name], ['文件类型', f.mimetype], ['文件大小', typeof f.file_size === 'number' ? `${(f.file_size / 1024).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} KB` : undefined]]
  } else {
    entries = [['公司', f.company_id], ['产品', f.product_name ?? f.product_id], ['供应商', f.partner_name ?? f.partner_id], ['产品型号', f.default_code], ['销售价格', money(f.list_price)], ['条码', f.barcode], ['说明', f.description ?? f.note]]
  }
  return entries.filter(([, value]) => hasDocumentValue(value)).map(([label, value]) => ({ label, value: readableValue(value) }))
}

export function documentLines(document: Document, documents: Document[]) {
  const relation = ({ 'sale.order': ['sale.order.line', 'order_line', 'order_id'], 'purchase.order': ['purchase.order.line', 'order_line', 'order_id'], 'account.move': ['account.move.line', 'invoice_line_ids', 'move_id'], 'stock.picking': ['stock.move', 'move_ids', 'picking_id'] } as Record<string, string[]>)[document.model]
  if (!relation) return []
  const [model, field, parentField] = relation
  const ids = document.fields[field] ?? (document.model === 'account.move' ? document.fields.line_ids : undefined)
  return documents.filter((line) => {
    if (line.model !== model) return false
    const parent = line.fields[parentField]
    const parentId = Array.isArray(parent) ? parent[0] : parent
    if (parentId != null && String(parentId) !== String(document.id)) return false
    return Array.isArray(ids) ? ids.some((id) => String(id) === String(line.id)) : parentId != null
  })
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

export function artifactKindLabel(kind?: string) { return ({ business_receipt: '业务回执', odoo_pdf: 'PDF 单据', odoo_csv: '明细 CSV', document_pdf: 'PDF 单据', document_csv: '明细 CSV' } as Record<string, string>)[kind || ''] || kind || '文件' }

export function documentModelLabel(model: string) { return ({ 'sale.order': '销售订单', 'purchase.order': '采购订单', 'account.move': '发票与贷项', 'stock.picking': '收发货与退货', 'mrp.production': '制造单', 'stock.move': '库存移动', 'account.payment': '收付款', 'account.bank.statement.line': '银行流水', 'account.partial.reconcile': '核销匹配', 'account.payment.register': '付款登记', 'account.move.reversal': '贷项向导', 'stock.return.picking': '退货向导', 'res.partner': '往来单位', 'mail.message': '业务留言', 'sale.order.line': '销售明细', 'purchase.order.line': '采购明细', 'account.move.line': '会计分录', 'account.payment.term': '付款条件', 'product.template': '产品', 'product.product': '商品', 'account.journal': '会计日记账', 'sale.advance.payment.inv': '开票向导', 'account.move.send.wizard': '发票文件向导', 'ir.attachment': '附件', 'product.supplierinfo': '供应商报价', 'account.tax': '税率', 'res.currency': '币种', 'res.company': '公司', 'stock.location': '库位', 'mrp.bom': '物料清单', 'mrp.workcenter': '工作中心' } as Record<string, string>)[model] || model }

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

export function roundStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', pending: '待处理', error: '错误', failed: '失败', interrupted: '已中断', cancelled: '已取消', awaiting_approval: '等待审批' }, status) }

export function toolStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', error: '错误', failed: '失败', executed: '已执行', awaiting_approval: '等待审批', pending: '待处理', interrupted: '已中断', cancelled: '已取消', unknown: '未知' }, status) }

export function activityPhaseLabel(phase?: string) { return ({ idle: '待执行', planning: '准备中', model: '分析业务目标', reading: '读取业务数据', tool: '调用业务工具', approval: '等待确认', executing: '执行中', cancelling: '正在取消', verifying: '回读核验', completed: '本轮结束', failed: '执行失败', interrupted: '已中断', cancelled: '已取消', reconciliation: '等待对账', unknown: '状态未知' } as Record<string, string>)[phase || ''] || '状态未知' }

export function businessTypeMeta(type?: string) { return ({ invoice_delivery: { title: '发票发送', short: '发送发票' }, inventory: { title: '库存收发与退货', short: '库存' }, manufacturing: { title: '制造与补货', short: '制造' }, payment: { title: '客户收款与供应商付款', short: '收付款' }, refund: { title: '退货退款与贷项', short: '退款' }, reconciliation: { title: '银行与账务核销', short: '核销' }, sale_invoice: { title: '销售与开票', short: '销售发票' }, purchase: { title: '采购', short: '采购流程' }, sale_purchase_invoice: { title: '销售 → 采购 → 开票', short: '业务链' } } as Record<string, { title: string; short: string }>)[type || ''] || { title: '业务工作区', short: '业务' } }

export function completionTargetLabel(target?: string, type?: string) { const effective = target || (type === 'purchase' ? 'confirmed' : ['inventory', 'manufacturing'].includes(type || '') ? 'done' : ['payment', 'refund', 'reconciliation'].includes(type || '') ? 'reconciled' : 'posted'); return ({ sent: '交付邮件服务器', read_only: '只读浏览', draft: '保留草稿', confirmed: '完成确认', posted: ['payment', 'refund'].includes(type || '') ? '单据已过账' : '发票已过账', done: '业务已完成', reconciled: '账务与银行已核销' } as Record<string, string>)[effective] || '完成目标未知' }

export function materialRowLabel(material: MaterialRecord) { if (material.row_count == null) return '行数未知'; return material.media_type === 'text/csv' ? `${Math.max(0, material.row_count - 1)} 条数据` : `${material.row_count} 行`; }

export function stageLabel(stage?: string) { return ({ material: '材料', read: '读取', quote: '报价', sales: '销售', confirm: '确认', purchase: '采购', invoice: '开票', inventory: '收发货', manufacturing: '制造', payment: '收付款', refund: '退款', reconciliation: '核销', verify: '核验' } as Record<string, string>)[stage || ''] || '未知阶段' }

export function stageStatusLabel(status?: string) { return ({ pending: '待处理', active: '进行中', awaiting_approval: '等待审批', observed: '已观测', verified: '已核验', failed: '失败', unknown: '未知' } as Record<string, string>)[status || ''] || '未知' }

export function outcomeScopeLabel(scope?: string) { const kind = scope?.split('_')[0]; if (['inventory', 'manufacturing', 'payment', 'refund', 'reconciliation'].includes(kind || '')) return `${businessTypeMeta(kind).title}核验`; return ({
  invoice_delivery_sent_checks: '发票与收件人投递核验',
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
  if (approval.prestate && typeof approval.prestate === 'object' && 'invoice_mail' in approval.prestate) return '发送正式发票邮件'
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
