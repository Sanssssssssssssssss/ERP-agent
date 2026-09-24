import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const { outputFiles } = await build({ entryPoints: [fileURLToPath(new URL('../src/renderer/features/approvals/approval-presentation.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' })
const { approvalActionTitle, approvalActionEffect, approvalBusinessTargets, approvalBusinessFacts, approvalMethodOptions, approvalRelationIds, approvalWrittenValues } = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`)
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
