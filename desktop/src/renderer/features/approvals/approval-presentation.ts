import type { Approval, Document } from '../../protocol'
import { documentMoney, documentQuantity, modelLabel, readableApprovalTitle, readableValue } from '../../presentation'

type Row = Record<string, unknown>
const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item) => item && typeof item === 'object' && !Array.isArray(item)) as Row[] : Object.keys(object(value)).length ? [object(value)] : []
const idOf = (value: unknown) => Array.isArray(value) ? value[0] : value

// Relations may be readback IDs or explicit ORM link/replace commands. Never treat a wizard ID as its source order.
export function approvalRelationIds(value: unknown): (number | string)[] {
  if (typeof value === 'number' || typeof value === 'string') return [value]
  if (!Array.isArray(value)) return []
  if (value.length === 2 && typeof value[0] === 'number' && typeof value[1] === 'string') return [value[0]]
  return value.flatMap((item) => Array.isArray(item) ? item[0] === 6 && Array.isArray(item[2]) ? item[2] : item[0] === 4 ? [item[1]] : [] : typeof item === 'number' || typeof item === 'string' ? [item] : [])
}

export function approvalWrittenValues(approval: Approval): Row {
  return ['create', 'write'].includes(approval.operation) ? approval.values || {} : {}
}

export function approvalActionTitle(approval: Approval) {
  const titles: Record<string, string> = {
    'sale.advance.payment.inv.create': '准备销售订单开票',
    'sale.advance.payment.inv.create_invoices': '从销售订单生成发票',
    'account.move.send.wizard.create': '准备发票文件处理',
    'account.move.send.wizard.action_send_and_print': '生成正式发票 PDF',
    'account.payment.register.create': '准备收付款登记',
    'account.payment.register.action_create_payments': '登记收付款',
    'account.move.reversal.create': '准备贷项通知',
    'account.move.reversal.reverse_moves': '根据原发票生成贷项',
    'stock.return.picking.action_create_returns': '根据原交付生成退货',
    'stock.backorder.confirmation.process': '保留未交数量并生成补交单',
    'account.move.line.reconcile': '核销选定会计分录',
    'sale.order.action_cancel': '取消销售订单',
    'purchase.order.button_cancel': '取消采购订单',
    'mrp.production.action_cancel': '取消制造单',
    'mrp.production.action_assign': '为制造单分配原料',
    'mrp.production.set_qty_producing': '准备本次生产数量',
    'stock.picking.action_assign': '分配可用库存'
  }
  if (approval.operation === 'message_post') return object(approval.prestate).invoice_mail ? '发送正式发票邮件' : '添加单据留言'
  return titles[`${approval.model}.${approval.operation}`] || readableApprovalTitle(approval)
}

export function approvalActionEffect(approval: Approval) {
  const effects: Record<string, string> = {
    'sale.advance.payment.inv.create': '保存所选销售订单和开票方式。生成发票需后续独立审批。',
    'sale.advance.payment.inv.create_invoices': '按来源订单和已保存的开票方式生成发票；过账与发送需另行审批。',
    'account.move.send.wizard.create': '保存发票处理选项。生成文件或发送邮件需后续独立审批。',
    'account.move.send.wizard.action_send_and_print': '为已过账客户发票生成正式 PDF；本动作不发送邮件。',
    'sale.order.action_confirm': '确认销售订单。Odoo 可生成关联交付或补货单；本动作不完成发货或开票。',
    'purchase.order.button_confirm': '确认采购订单；启用二次审批时进入待批准状态。',
    'purchase.order.button_approve': '批准采购订单，进入后续收货流程。',
    'account.move.action_post': '将所选凭证过账，形成正式账务记录。',
    'stock.picking.action_confirm': '确认选定库存单，建立后续库存移动。',
    'stock.picking.action_assign': '为库存单分配可用库存。',
    'stock.picking.button_validate': '按已登记数量处理收发货；有余量时可能要求后续补交确认。',
    'stock.backorder.confirmation.process': '完成已处理部分，为未交数量保留补交单。',
    'stock.return.picking.action_create_returns': '根据原收发货记录与退货数量创建关联退货单。',
    'mrp.production.action_confirm': '确认制造单并生成材料需求。',
    'mrp.production.action_assign': '为制造单分配可用原料。',
    'mrp.production.set_qty_producing': '根据制造单中已登记的生产数量准备耗料和产出。',
    'mrp.production.button_mark_done': '登记制造完工，记录耗料与产出库存。',
    'account.payment.register.action_create_payments': '按已登记金额创建收付款记录并处理关联应收应付；不代表真实银行转账完成。',
    'account.move.reversal.reverse_moves': '为所选原发票生成关联贷项通知；退款付款需另行审批。',
    'account.move.line.reconcile': '核销已选择且满足核销条件的会计分录。'
  }
  if (['sale.order.action_cancel', 'purchase.order.button_cancel', 'mrp.production.action_cancel'].includes(`${approval.model}.${approval.operation}`)) return '取消所选业务单据；已发生的交付、付款或账务记录不会因此自动撤销。'
  if (approval.operation === 'message_post') return object(approval.prestate).invoice_mail ? '向已核验的登记邮箱投递所选发票 PDF，以邮件服务器回执核验。' : '在单据中发布所提交的留言；留言成功不代表发票邮件已送达。'
  if (effects[`${approval.model}.${approval.operation}`]) return effects[`${approval.model}.${approval.operation}`]
  return approval.operation === 'create' ? '按下方拟提交值创建记录。' : approval.operation === 'write' ? '仅修改下方列出的业务字段。' : approval.operation === 'unlink' ? '删除选定记录。此操作可能不可恢复。' : '执行所选业务动作；实际结果以执行后的回读核验为准。'
}

export function approvalBusinessTargets(approval: Approval, documents: Document[]): { model: string; fields: Row }[] {
  const state = object(approval.prestate), enterprise = object(state.enterprise), values = approval.values || {}
  let model = approval.model
  let targets: Row[] = []
  const fromIds = (value: unknown) => approvalRelationIds(value).map((id) => ({ id }))
  if (model === 'sale.advance.payment.inv') {
    model = 'sale.order'
    targets = rows(state.orders)
    if (!targets.length) targets = fromIds(values.sale_order_ids ?? rows(state.wizard).flatMap((row) => approvalRelationIds(row.sale_order_ids)))
  } else if (model === 'account.move.send.wizard') {
    model = 'account.move'
    targets = rows(state.invoice)
    if (!targets.length) targets = fromIds(values.move_id ?? rows(state.wizard).flatMap((row) => approvalRelationIds(row.move_id)))
  } else if (state.invoice_mail) {
    targets = rows(object(state.invoice_mail).invoice)
  } else if (model === 'account.payment.register' || model === 'account.move.reversal') {
    model = 'account.move'
    targets = rows(enterprise.invoices)
    if (!targets.length) targets = fromIds(values.move_ids)
  } else if (model === 'stock.return.picking' || model === 'stock.backorder.confirmation') {
    model = 'stock.picking'
    targets = rows(enterprise.picking ?? enterprise.records)
    if (!targets.length) targets = fromIds(values.picking_id ?? values.pick_ids)
  } else {
    targets = rows(enterprise.records ?? (enterprise.kind === 'reconcile' ? enterprise.lines : undefined) ?? state.records ?? state.target)
    if (!targets.length) targets = approval.operation === 'create' ? rows(values.values_list ?? values) : fromIds(approval.record_ids)
  }
  // Only use task evidence when the tuple explicitly names this model and target ID.
  const sources = object(state.host_task_evidence).sources
  return targets.map((target) => {
    const observed = target.id != null ? documents.find((document) => document.model === model && String(document.id) === String(target.id)) : undefined
    const bound: Row = {}
    if (Array.isArray(sources)) for (const source of sources) {
      if (!Array.isArray(source) || source[0] !== 'target_relations' || source[1] !== model) continue
      const relation = rows(source[2]).find((row) => target.id != null && String(row.id) === String(target.id))
      if (relation) Object.assign(bound, relation)
    }
    return { model, fields: { ...(observed?.fields || {}), ...(observed?.name ? { name: observed.name } : {}), ...bound, ...target } }
  })
}

export function approvalBusinessFacts(model: string, fields: Row, documents: Document[]) {
  const relationName = (value: unknown, expectedModel: string) => {
    const id = idOf(value)
    const name = Array.isArray(value) && typeof value[1] === 'string' ? value[1] : documents.find((item) => item.model === expectedModel && id != null && String(item.id) === String(id))?.name
    return name || (id != null && id !== false ? `名称未读取（记录 ${String(id)}）` : '未读取')
  }
  const facts = [
    { label: modelLabel(model), value: typeof fields.name === 'string' ? fields.name : fields.id != null ? `名称未读取（记录 ${String(fields.id)}）` : '名称未读取' },
    { label: model === 'purchase.order' ? '供应商' : '往来单位', value: fields.partner_id ? relationName(fields.partner_id, 'res.partner') : typeof fields.partner_name === 'string' ? fields.partner_name : '未读取' },
    { label: '公司', value: relationName(fields.company_id, 'res.company') }
  ]
  if (['sale.order', 'purchase.order', 'account.move', 'account.payment'].includes(model)) facts.push({ label: '单据金额', value: documentMoney(fields.amount_total ?? fields.amount, { ...fields, currency: fields.currency_id ?? fields.currency ?? fields.currency_name }) || '未读取' })
  if (model === 'mrp.production' || model === 'stock.move') facts.push({ label: '产品', value: relationName(fields.product_id, 'product.product') }, { label: '数量', value: documentQuantity(fields) || '未读取' })
  return facts
}

export function approvalMethodOptions(approval: Approval, documents: Document[]) {
  const state = object(approval.prestate), enterprise = object(state.enterprise)
  const wizard = rows(enterprise.wizard ?? state.wizard)[0] || {}
  if (enterprise.kind === 'payment') return [{ label: '本次收付款金额', value: documentMoney(wizard.amount, wizard) || '未读取' }, { label: '收付款方向', value: wizard.payment_type === 'inbound' ? '收款' : wizard.payment_type === 'outbound' ? '付款' : '未读取' }]
  if (enterprise.kind === 'reversal') return [{ label: '贷项原因', value: readableValue(wizard.reason) }, { label: '业务日期', value: readableValue(wizard.date) }]
  if (enterprise.kind === 'return') return rows(enterprise.lines).map((line) => {
    const move = rows(enterprise.moves).find((row) => String(row.id) === String(idOf(line.move_id)) && String(idOf(row.product_id)) === String(idOf(line.product_id)))
    return { label: `退货数量 · ${readableValue(line.product_id)}`, value: documentQuantity({ ...line, product_uom: move?.product_uom }) || '未读取' }
  })
  if (enterprise.kind === 'picking') return rows(enterprise.moves).map((move) => ({ label: `库存数量 · ${readableValue(move.product_id)}`, value: documentQuantity(move, ['button_validate', 'process'].includes(approval.operation) ? 'quantity' : 'product_uom_qty') || '未读取' }))
  if (enterprise.kind === 'production') return rows(enterprise.records).map((row) => ({ label: `本次生产数量 · ${readableValue(row.product_id)}`, value: documentQuantity(row, 'qty_producing') || '未读取' }))
  if (approval.model === 'sale.advance.payment.inv' && approval.operation === 'create_invoices') {
    const id = wizard.id ?? approval.record_ids[0]
    const observed = documents.find((item) => item.model === approval.model && String(item.id) === String(id))
    const method = wizard.advance_payment_method ?? observed?.fields.advance_payment_method
    return [{ label: '开票方式', value: ({ delivered: '按可开票数量', percentage: '按比例预付款', fixed: '固定金额预付款' } as Record<string, string>)[String(method)] || '未读取' }]
  }
  return []
}
