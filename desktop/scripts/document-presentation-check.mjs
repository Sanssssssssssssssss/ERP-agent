import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

// Bundle the real formatter with the existing Vite dependency; no browser, ERP or model calls.
const { outputFiles } = await build({ entryPoints: [fileURLToPath(new URL('../src/renderer/presentation.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' })
const { documentFacts, documentMoney, documentAmount, documentQuantity, documentLines, documentCurrencyFields, documentTypeLabel, isDownloadableDocument } = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`)
const doc = (model, id, fields) => ({ model, id, name: String(id), fields, source: 'refresh_native_read' })
const partner = doc('res.partner', 516, { email: 'customer@example.test', company_id: [1, '澄川工业部件有限公司'] })
assert.deepEqual(documentFacts(partner), [{ label: '邮箱', value: 'customer@example.test' }, { label: '所属公司', value: '澄川工业部件有限公司' }])
assert.equal(isDownloadableDocument(partner), false)
assert.equal(documentMoney(0, { currency_id: [1, 'CNY'] }), '0.00 CNY')
assert.equal(documentMoney(12, { currency_id: 1 }), '12.00（币种未读取）')
assert.equal(documentMoney(false, { currency: 'CNY' }), '')
assert.equal(documentAmount(doc('account.tax', 5, { amount: 13 })), '') // Tax rates are not money.
assert.equal(documentQuantity({ product_uom_qty: 3, product_uom_id: [1, '件'] }), '3 件')
assert.equal(documentQuantity({ quantity: 0 }), '0（单位未读取）')

const order = doc('sale.order', 7, { order_line: [11, 12, 13], currency_id: [1, 'CNY'] })
const first = doc('sale.order.line', 11, { order_id: [7, 'S00007'], product_uom_qty: 3, product_uom_id: [1, '件'], price_unit: 10 })
const second = doc('sale.order.line', 12, { order_id: [7, 'S00007'], product_uom_qty: 4, product_uom_id: [2, '千克'], currency_id: [2, 'USD'], price_unit: 20 })
const wrongParent = doc('sale.order.line', 13, { order_id: [8, 'S00008'], price_unit: 30 })
const unlisted = doc('sale.order.line', 14, { order_id: [7, 'S00007'], price_unit: 40 })
const records = [order, first, second, wrongParent, unlisted]
assert.deepEqual(documentLines(order, records).map((line) => line.id), [11, 12])
assert.equal(documentMoney(first.fields.price_unit, documentCurrencyFields(first, records)), '10.00 CNY')
assert.equal(documentMoney(second.fields.price_unit, documentCurrencyFields(second, records)), '20.00 USD')
assert.equal(documentMoney(wrongParent.fields.price_unit, documentCurrencyFields(wrongParent, records)), '30.00（币种未读取）')
assert.equal(documentFacts(order).some(({ label }) => label === '数量'), false) // Different units stay on separate lines.
assert.deepEqual(documentLines(doc('purchase.order', 7, { order_line: [11] }), records), [])
assert.equal(documentTypeLabel(doc('account.move', 2, { move_type: 'in_refund' })), '供应商贷项')
assert.equal(documentFacts(doc('account.move.line', 3, { currency_id: [2, 'USD'], debit: 10 })).find(({ label }) => label === '借方')?.value, '10.00（币种未读取）')
assert.deepEqual(documentFacts(doc('unknown.model', 4, { secret_internal_code: 'not business copy' })), [])
console.log('Document presentation checks passed: identities, known currency, zero values, mixed units and relation boundaries.')
