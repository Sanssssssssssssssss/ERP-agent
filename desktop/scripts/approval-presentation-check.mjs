import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const { outputFiles } = await build({ entryPoints: [fileURLToPath(new URL('../src/renderer/features/approvals/approval-presentation.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' })
const { approvalDisplayDocuments, approvalActionTitle, approvalActionEffect, approvalBusinessTargets, approvalBusinessFacts, approvalMethodOptions, approvalRelationIds, approvalWrittenValues } = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`)
const approval = (overrides = {}) => ({ action_id: 'test', run_id: 'test', business_id: 'test', status: 'pending', model: 'sale.advance.payment.inv', operation: 'create_invoices', record_ids: [1], values: { ids: [1] }, prestate: { wizard: [{ id: 1, sale_order_ids: [3499] }], orders: [{ id: 3499, invoice_ids: [] }], host_task_evidence: { sources: [['user_reference', 'res.partner', { id: 99, name: 'Unrelated customer' }]] } }, ...overrides })
const documents = [
  { model: 'sale.order', id: 1, name: 'Wrong order', fields: { amount_total: 999 }, source: 'read' },
  { model: 'sale.order', id: 3499, name: 'S03499', fields: { partner_id: [516, 'Customer'], company_id: [1, 'Company'], amount_total: 3214.85, currency_id: [7, 'CNY'] }, source: 'read' },
  { model: 'account.move', id: 31, name: 'INV/2026/00004', fields: { partner_id: [516, 'Customer'] }, source: 'read' }
]
const targets = approvalBusinessTargets(approval(), documents)
assert.equal(targets.length, 1)
assert.equal(targets[0].fields.name, 'S03499')
assert.equal(targets[0].fields.partner_id[0], 516)
assert.equal(approvalBusinessFacts(targets[0].model, targets[0].fields, documents).find(({ label }) => label === '单据金额').value, '3,214.85 CNY')
assert.deepEqual(approvalWrittenValues(approval()), {}) // Method kwargs and evidence are not written fields.
assert.deepEqual(approvalWrittenValues(approval({ operation: 'write', values: { partner_id: 516 } })), { partner_id: 516 })
assert.deepEqual(approvalRelationIds([[6, 0, [3499]], [4, 3500, 0]]), [3499, 3500])
const create = approval({ operation: 'create', record_ids: [], values: { advance_payment_method: 'delivered', sale_order_ids: [[6, 0, [3499]]] }, prestate: { records: [] } })
assert.equal(approvalBusinessTargets(create, documents)[0].fields.name, 'S03499')
const pdf = approval({ model: 'account.move.send.wizard', operation: 'action_send_and_print', prestate: { wizard: [{ id: 1, move_id: [31, 'Invoice'] }], invoice: [{ id: 31, state: 'posted' }] } })
assert.equal(approvalBusinessTargets(pdf, documents)[0].fields.name, 'INV/2026/00004')
assert.equal(approvalActionTitle(pdf), '生成正式发票 PDF')
assert.match(approvalActionEffect(pdf), /不发送邮件/)
const chatter = approval({ model: 'account.move', operation: 'message_post', values: { body: 'Saved invoice' }, prestate: { target: [{ id: 31 }] } })
assert.equal(approvalActionTitle(chatter), '添加单据留言')
assert.match(approvalActionEffect(chatter), /不代表.*邮件已送达/)
assert.equal(approvalBusinessFacts('sale.order', { id: 99, partner_id: 42 }, []).find(({ label }) => label === '往来单位').value, '名称未读取（记录 42）')
assert.equal(approvalBusinessFacts('sale.order', { partner_id: [42, 'Bound customer'], partner_name: 'Old cached name', amount_total: 20, currency_id: [7, 'CNY'], currency: 'USD' }, []).find(({ label }) => label === '往来单位').value, 'Bound customer')
assert.equal(approvalBusinessFacts('sale.order', { amount_total: 20, currency_id: [7, 'CNY'], currency: 'USD' }, []).find(({ label }) => label === '单据金额').value, '20.00 CNY')
assert.equal(approvalBusinessTargets(approval(), [])[0].fields.partner_id, undefined) // Unbound user reference is not a target relation.
const payment = approval({ model: 'account.payment.register', operation: 'action_create_payments', prestate: { enterprise: { kind: 'payment', wizard: { amount: 40, currency_id: [7, 'CNY'], payment_type: 'inbound' }, invoices: [{ id: 31, amount_total: 100 }] } } })
assert.equal(approvalMethodOptions(payment, documents)[0].value, '40.00 CNY') // Payment amount is distinct from the source invoice total.
const returns = approval({ model: 'stock.return.picking', operation: 'action_create_returns', prestate: { enterprise: { kind: 'return', lines: [{ product_id: [2, 'Valve'], move_id: [9, 'Move'], quantity: 3 }], moves: [{ id: 9, product_id: [2, 'Valve'], product_uom: [1, '件'] }] } } })
assert.equal(approvalMethodOptions(returns, [])[0].value, '3 件')
console.log('Approval presentation checks passed: real write fields, wizard/source identity, currency, payment/return quantities and chatter boundary.')

// The observed purchase approval held names in bound evidence and nested lines, not top-level entities.
const purchase = approval({ model: 'purchase.order', operation: 'create', values: { company_id: 1, currency_id: 6, order_line: [[0, 0, { product_id: 1, name: 'Submitted description' }]] }, prestate: { host_task_evidence: { sources: [['user_reference', 'purchase.order', { id: 5, name: 'P00001', company_id: [1, '澄川工业'], currency_id: [6, 'CNY'] }]] } } })
const references = approvalDisplayDocuments(purchase, [{ model: 'purchase.order.line', id: 1, name: 'Source line', fields: { product_id: [1, '控制阀'] }, source: 'read' }, { model: 'res.partner', id: 1, name: 'Unrelated partner', fields: {}, source: 'read' }])
assert.equal(references.find((row) => row.model === 'res.company' && row.id === 1)?.name, '澄川工业')
assert.equal(references.find((row) => row.model === 'res.currency' && row.id === 6)?.name, 'CNY')
assert.equal(references.find((row) => row.model === 'product.product' && row.id === 1)?.name, '控制阀')
assert.equal(approvalDisplayDocuments(purchase, []).some((row) => row.model === 'product.product'), false)
assert.equal(approvalDisplayDocuments({ ...purchase, display_references: [{ model: 'res.company', id: 1, name: 'Old secret', status: 'permission_denied' }] }, references).some((row) => row.model === 'res.company'), false)
assert.equal(approvalDisplayDocuments({ ...purchase, display_references: [{ model: 'res.company', id: 1, name: 'Current verified name', status: 'ready' }] }, references).find((row) => row.model === 'res.company').name, 'Current verified name')

// Observed manufacturing replenishment request: quantities are supplied, pricing is left to ERP defaults.
const replenishment = approval({ model: 'purchase.order', operation: 'create', record_ids: [], prestate: {}, values: { company_id: 1, partner_id: 606, order_line: [[0, 0, { product_id: 801, product_qty: 120 }]] } })
const replenishmentFacts = approvalBusinessFacts('purchase.order', replenishment.values, [], 'create')
assert.equal(replenishmentFacts.find(({ label }) => label === '单据金额').value, '建草稿后回读金额与税额')
assert.equal(replenishmentFacts[0].value, '新草稿（编号待生成）')
assert.match(approvalActionEffect(replenishment), /单价、币种和日期按 ERP 默认规则.*建草稿后回读金额与税额.*确认订单另行审批/)
assert.equal(approvalBusinessFacts('purchase.order', { id: 123 }, [], 'button_confirm').find(({ label }) => label === '单据金额').value, '未读取')
assert.equal(approvalBusinessFacts('purchase.order', { amount_total: 0, currency_id: [6, 'CNY'] }, [], 'create').find(({ label }) => label === '单据金额').value, '0.00 CNY')
assert.deepEqual(approvalWrittenValues(replenishment).order_line, [[0, 0, { product_id: 801, product_qty: 120 }]])

// PO3003 confirmation exposed order totals but no lines. Keep relationship edge cases offline.
const orderLine = { id: 9001, order_id: [3003, 'PO3003'], product_id: [801, '补货原料'], product_qty: 120, product_uom: [1, '件'], price_unit: 12, date_planned: '2026-10-01 08:00:00' }
const orderConfirm = approval({ model: 'purchase.order', operation: 'button_confirm', record_ids: [3003], values: {}, prestate: { records: [{ id: 3003, name: 'PO3003', amount_total: 1627.2, currency_id: [6, 'CNY'] }], order_lines: [orderLine, { ...orderLine, id: 9002, order_id: 3004 }, { ...orderLine, id: 9003, order_id: undefined }] } })
const orderOptions = approvalMethodOptions(orderConfirm, [])
assert.equal(orderOptions.length, 1)
assert.equal(orderOptions[0].label, '商品 · 补货原料')
assert.equal(orderOptions[0].value, '数量 120 件 · 单价 12.00 CNY · 交期 2026-10-01 08:00:00')
assert.match(approvalActionEffect(orderConfirm), /二次审批.*关联收货单.*不登记收货或付款/)
const worldLines = [{ model: 'purchase.order', id: 3003, name: 'PO3003', fields: { order_line: [9001], currency_id: [6, 'CNY'] }, source: 'read' }, { model: 'purchase.order.line', id: 9001, name: '原料行', fields: orderLine, source: 'read' }, { model: 'sale.order.line', id: 9001, name: '错误模型', fields: { ...orderLine, product_id: [802, '错误模型产品'] }, source: 'read' }]
assert.deepEqual(approvalBusinessTargets(orderConfirm, worldLines)[0].fields.order_line, [9001])
assert.equal(approvalMethodOptions({ ...orderConfirm, prestate: {} }, worldLines).length, 1)
assert.equal(approvalMethodOptions({ ...orderConfirm, prestate: {} }, [...worldLines, { model: 'purchase.order.line', id: 9004, name: '旧明细', fields: { ...orderLine, id: 9004 }, source: 'read' }]).length, 1)
assert.deepEqual(approvalMethodOptions({ ...orderConfirm, prestate: { order_lines: [] } }, worldLines), [])
assert.equal(approvalMethodOptions({ ...orderConfirm, operation: 'button_cancel', prestate: { enterprise: { kind: 'purchase_cancel', records: orderConfirm.prestate.records, lines: [orderLine] } } }, []).length, 1)
assert.equal(approvalMethodOptions({ ...orderConfirm, prestate: { order_lines: [{ order_id: 3003 }] } }, [])[0].value, '数量 未读取 · 单价 未读取 · 交期 未读取')
const saleConfirm = { ...orderConfirm, model: 'sale.order', operation: 'action_confirm', prestate: { order_lines: [{ ...orderLine, product_uom_qty: 4, product_qty: undefined }] } }
assert.match(approvalMethodOptions(saleConfirm, [])[0].value, /数量 4 件/)
assert.match(approvalActionEffect(saleConfirm), /关联交付或补货单.*不完成发货或开票/)
