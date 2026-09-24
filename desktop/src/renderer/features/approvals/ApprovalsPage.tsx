import { Button as RadixButton } from '@radix-ui/themes'
import { Check as CheckIcon,Clock3,RefreshCw,X } from 'lucide-react'
import { useState } from 'react'
import { EmptyState,StatusBadge } from '../../components/common'
import { approvalResultLabel,approvalStatusLabel,isPendingApproval,modelLabel,operationLabel,messageForError,readableValue } from '../../presentation'
import {
Approval,
BusinessDetail,
Document,
formatExpiry,
isExpired,
jsonText
} from '../../protocol'
import { ApprovalProgress } from '../../view-types'
import { approvalActionTitle, approvalActionEffect, approvalBusinessTargets, approvalBusinessFacts, approvalMethodOptions, approvalRelationIds, approvalWrittenValues } from './approval-presentation'

export function ApprovalInboxCard({ approvals, businessName, progress, activity, onOpenApprovals, onOpenExecution }: { approvals: Approval[]; businessName: string; progress: ApprovalProgress | null; activity?: NonNullable<BusinessDetail['activity']>; onOpenApprovals: () => void; onOpenExecution: () => void }) {
  const activeProgress = Boolean(progress && (approvals.length === 0 || approvals.some((approval) => approval.business_id === progress.businessId)))
  const summary = approvals.length ? `${approvals.slice(0, 2).map((approval) => approvalActionTitle(approval)).join('、')}${approvals.length > 2 ? ` 等 ${approvals.length} 项` : ''}` : activity ? `${activity.label || '当前执行状态'}：${activity.detail || '正在读取最新状态。'}` : '正在读取最新执行状态。'
  return <section className="approval-inbox-card" role="status"><div className="approval-inbox-icon"><Clock3 size={18} /></div><div className="approval-inbox-copy"><div className="approval-inbox-kicker">{approvals.length ? `需要人工审批 · ${businessName}` : `业务执行状态 · ${businessName}`}</div><strong>{approvals.length ? `${approvals.length} 项业务动作等待确认` : (activity?.label || '业务执行状态')}</strong><span>{summary}</span>{activeProgress && <small>{progress?.status === 'submitting' ? '正在提交审批决定…' : (progress?.detail || '审批状态未生效，请查看执行详情。')}</small>}</div><RadixButton className="primary-button approval-inbox-button" onClick={approvals.length ? onOpenApprovals : onOpenExecution}>{approvals.length ? <><CheckIcon size={15} />查看并审批</> : '查看执行状态'}</RadixButton></section>
}

export function ApprovalsPage({ approvals, documents, disabled, onDecision, onReconcile, onRequestRevision, onTraceTarget }: { approvals: Approval[]; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onRequestRevision?: (approval: Approval, text: string) => Promise<void>; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const orderedApprovals = [...approvals].sort(compareApprovals)
  return <div className="page-stack"><div className="page-intro"><h3>变更与审批</h3><span>{approvals.filter(isPendingApproval).length} 项待处理</span></div>{approvals.length === 0 ? <EmptyState title="没有审批记录" detail="主机产生需要人工确认的业务动作后，审批卡会保留在这里。" /> : <div className="approval-list">{orderedApprovals.map((approval) => <ApprovalRow key={approval.action_id} approval={approval} documents={documents} disabled={disabled} onDecision={onDecision} onReconcile={onReconcile} onRequestRevision={onRequestRevision} onTraceTarget={onTraceTarget} />)}</div>}</div>
}

export function compareApprovals(left: Approval, right: Approval) {
  const priority = (approval: Approval) => isPendingApproval(approval) ? 0 : approval.status === 'needs_reconciliation' ? 1 : 2
  const priorityDifference = priority(left) - priority(right)
  if (priorityDifference) return priorityDifference
  const createdAt = (approval: Approval) => {
    const value = (approval as Approval & { created_at?: string | number }).created_at
    if (typeof value === 'number') return value
    const timestamp = value ? Date.parse(value) : 0
    return Number.isNaN(timestamp) ? 0 : timestamp
  }
  const createdDifference = createdAt(right) - createdAt(left)
  return createdDifference || right.action_id.localeCompare(left.action_id)
}

export function ApprovalRow({ approval, documents, disabled, onDecision, onReconcile, onRequestRevision, onTraceTarget }: { approval: Approval; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onRequestRevision?: (approval: Approval, text: string) => Promise<void>; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const [editing, setEditing] = useState(false)
  const [revision, setRevision] = useState('')
  const [revisionError, setRevisionError] = useState('')
  const [submittingRevision, setSubmittingRevision] = useState(false)
  const pending = isPendingApproval(approval)
  const expired = pending && isExpired(approval.expires_at)
  const title = approvalActionTitle(approval)
  const targets = approvalBusinessTargets(approval, documents)
  const methodOptions = approvalMethodOptions(approval, documents)
  const submitRevision = async () => {
    if (!onRequestRevision || disabled || submittingRevision || !pending || isExpired(approval.expires_at) || !revision.trim()) return
    setSubmittingRevision(true); setRevisionError('')
    try { await onRequestRevision(approval, revision.trim()); setRevision(''); setEditing(false) }
    catch (error) { setRevisionError(messageForError(error)) }
    finally { setSubmittingRevision(false) }
  }
  return (
    <article className={`approval-row ${pending ? 'pending' : ''}`}>
      <div className="approval-row-head">
        <div><strong>{title}</strong><span>{pending ? formatExpiry(approval.expires_at) : '审批已结束'}</span></div>
        <StatusBadge status={expired ? 'expired' : approval.status} label={expired ? '已过期' : approvalStatusLabel(approval.status)} />
      </div>
      <p className="approval-effect">{approvalActionEffect(approval)}</p>
      <section className="approval-business-context" aria-label="本次业务对象">
        <small className="approval-context-label">关联单据 · 最近读取</small>
        {targets.length ? targets.map(({ model, fields }, index) => <dl className="approval-business-facts" key={`${model}:${String(fields.id ?? index)}`}>{approvalBusinessFacts(model, fields, documents).map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>) : <p className="muted">关联业务单据未读取，请核对原始数据后再批准。</p>}
        {methodOptions.length > 0 && <dl className="approval-business-facts">{methodOptions.map(({ label, value }, index) => <div key={`${label}:${index}`}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
      </section>
      <InvoiceMailPreview approval={approval} />
      <ChatterPreview approval={approval} documents={documents} />
      <ApprovalFieldDiff approval={approval} documents={documents} />
      <details>
        <summary>查看拟提交值与执行前状态</summary>
        <p className="approval-internal-ids">{modelLabel(approval.model)} · {operationLabel(approval.operation)} · {approval.model} · {approval.action_id}<br />{approvalRecordText(approval, documents)}</p><div className="json-columns"><div><small>拟提交值</small><pre>{jsonText(approval.values)}</pre></div><div><small>执行前状态</small><pre>{approvalPrestateText(approval)}</pre></div></div>
      </details>
      {pending && !expired && <div className="approval-actions"><RadixButton className="danger-button" variant="soft" disabled={disabled || submittingRevision} onClick={() => onDecision(approval, 'reject')}><X size={15} />拒绝</RadixButton>{onRequestRevision && <RadixButton className="secondary-button" variant="soft" disabled={disabled || submittingRevision} onClick={() => { setEditing(!editing); setRevisionError('') }}>{editing ? '取消修改' : '提出修改'}</RadixButton>}<RadixButton className="primary-button" disabled={disabled || submittingRevision || editing} onClick={() => onDecision(approval, 'approve')}><CheckIcon size={15} />批准这项业务动作</RadixButton></div>}
      {pending && !expired && editing && onRequestRevision && <form className="approval-revision" onSubmit={(event) => { event.preventDefault(); void submitRevision() }}>
        <label htmlFor={`revision-${approval.action_id}`}>希望怎样修改这项动作？</label>
        <textarea id={`revision-${approval.action_id}`} value={revision} onChange={(event) => setRevision(event.target.value)} disabled={disabled || submittingRevision} rows={3} placeholder="例如：数量改为 5 件，先保留草稿。" />
        <p>提交后当前审批作废，已完成动作不撤销。修改后的方案需重新确认。</p>
        {revisionError && <p className="error-text" role="alert">{revisionError}</p>}
        <RadixButton type="submit" className="primary-button" disabled={disabled || submittingRevision || !revision.trim()}>{submittingRevision ? '提交修改中…' : '提交修改要求'}</RadixButton>
      </form>}
      {approval.status === 'needs_reconciliation'  && <div className="approval-actions"><small>写入结果不确定；核对现有 Odoo 状态后才能继续。</small><RadixButton className="secondary-button" variant="soft" disabled={disabled} onClick={() => onReconcile(approval)}><RefreshCw size={15} />核对不确定写入</RadixButton></div>}
      <button className="receipt-link receipt-button" type="button" onClick={() => onTraceTarget({ run_id: approval.run_id, action_id: approval.action_id })}>查看关联运行回执</button>
      {approval.result != null && <details className={`receipt receipt-details ${pending ? 'receipt-preflight' : ''}`}><summary>{approvalResultLabel(approval.status)}</summary><pre>{jsonText(approval.result)}</pre></details>}
    </article>
  )
}

function InvoiceMailPreview({ approval }: { approval: Approval }) {
  const state = approval.prestate as { invoice_mail?: { invoice: { name: string; amount_total: number }; company: { name: string; email: string }; recipient: { name: string }; email_from: string; email_to: string; attachment: { name: string; checksum: string }; subject: string; body: string } } | null
  const mail = state?.invoice_mail
  if (!mail) return null
  return <section className="field-diff" aria-label="待发送邮件"><div className="field-diff-note"><p><strong>{mail.company.name} · {mail.invoice.name}</strong></p><p>发件人：{mail.company.name} · {mail.company.email}</p><p>收件人：{mail.recipient.name} · {mail.email_to}</p><p>附件：{mail.attachment.name}</p><p>主题：{mail.subject}</p><p>{mail.body}</p><small>仅发送给此登记邮箱，不抄送关注者。附件指纹：{mail.attachment.checksum.slice(0, 12)}（完整值见执行前状态）。</small></div></section>
}

function ChatterPreview({ approval, documents }: { approval: Approval; documents: Document[] }) {
  if (approval.operation !== 'message_post' || (approval.prestate as { invoice_mail?: unknown } | null)?.invoice_mail) return null
  const values = approval.values || {}
  // Native chatter writes literal text. Preserve markup/entities exactly as submitted.
  const text = String(values.body || '')
  const recipients = approvalRelationIds(values.partner_ids).map((id) => approvalReferenceText(['res.partner'], id, documents, '联系人'))
  const attachments = approvalRelationIds(values.attachment_ids).map((id) => approvalReferenceText(['ir.attachment'], id, documents, '附件'))
  return <section className="approval-message-preview" aria-label="待发布留言"><strong>留言内容</strong><p className="approval-message-body">{text || '正文未读取'}</p><small>通知联系人：{recipients.join('、') || '未指定（单据留言）'}</small>{attachments.length > 0 && <small>附件：{attachments.join('、')}</small>}</section>
}

export function ApprovalFieldDiff({ approval, documents }: { approval: Approval; documents: Document[] }) {
  const values = approvalWrittenValues(approval)
  if (Array.isArray(values.values_list)) return <>{values.values_list.map((value, index) => <section key={index}><h4>新建记录 {index + 1}</h4><ApprovalFieldDiff approval={{ ...approval, values: value as Record<string, unknown> }} documents={documents} /></section>)}</>
  const prestate = approvalPrestateView(approval)
  const before = prestate.fields
  const keys = Object.keys(values)
  if (!keys.length) return null
  const beforeText = (key: string) => prestate.kind === 'new' ? '新建 / 无前态' : prestate.kind === 'multiple' ? '多条记录（见下方原始状态）' : prestate.kind === 'unknown' ? '未知' : approvalValueText(approval.model, key, before[key], documents)
  const row = (key: string) => <div className="field-diff-row" key={key}><span>{approvalFieldLabel(key, approval.model)}</span><span className="diff-value">{beforeText(key)}</span><span className="diff-value after">{approvalValueText(approval.model, key, values[key], documents)}</span></div>
  const visible = keys.slice(0, 8)
  const remaining = keys.slice(8)
  return <div className="field-diff"><div className="field-diff-row field-diff-head"><span>字段</span><span>执行前</span><span>拟提交</span></div>{visible.map(row)}{remaining.length > 0 && <details className="field-diff-more"><summary>查看其余字段（共 {keys.length} 项）</summary>{remaining.map(row)}</details>}</div>
}

export function approvalPrestateView(approval: Approval): { kind: 'new' | 'unknown' | 'fields' | 'multiple'; fields: Record<string, unknown> } {
  const raw = approval.prestate
  if (raw == null || raw === '') return { kind: approval.operation === 'create' ? 'new' : 'unknown', fields: {} }
  let records: unknown[] | null = null
  if (Array.isArray(raw)) records = raw
  else if (raw && typeof raw === 'object' && Array.isArray((raw as Record<string, unknown>).records)) records = (raw as Record<string, unknown>).records as unknown[]
  if (records) {
    if (records.length === 0) return { kind: approval.operation === 'create' ? 'new' : 'unknown', fields: {} }
    if (records.length === 1 && records[0] && typeof records[0] === 'object' && !Array.isArray(records[0])) return { kind: 'fields', fields: records[0] as Record<string, unknown> }
    return { kind: 'multiple', fields: {} }
  }
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const fields = Object.fromEntries(Object.entries(raw).filter(([key]) => Object.hasOwn(approvalWrittenValues(approval), key)))
    if (Object.keys(fields).length) return { kind: 'fields', fields }
  }
  return { kind: approval.operation === 'create' ? 'new' : 'unknown', fields: {} }
}

export function approvalPrestateText(approval: Approval) {
  return approval.prestate == null || approval.prestate === '' ? (approval.operation === 'create' ? '新建 / 无前态' : '未知（未返回执行前状态）') : jsonText(approval.prestate)
}

export function approvalRecordText(approval: Approval, documents: Document[]) {
  if (!approval.record_ids.length) return approval.operation === 'create' ? '新建，尚未生成编号' : '记录 ID 未知'
  const records = approval.record_ids.map((id) => {
    const document = documents.find((item) => item.model === approval.model && String(item.id) === String(id))
    return document?.name ? `${document.name}（ID ${String(id)}）` : `ID ${String(id)}`
  })
  return `记录 ${records.join('、')}`
}

export function approvalFieldLabel(key: string, model?: string) {
  const labels: Record<string, string> = { partner_id: model === 'purchase.order' ? '供应商' : '客户', payment_term_id: '付款条件', partner_shipping_id: '收货地址', partner_invoice_id: '开票地址', date_order: '下单日期', invoice_status: '开票状态', invoice_line_ids: '发票明细', order_line: '订单明细', commitment_date: '承诺日期', client_order_ref: '客户参考', advance_payment_method: '开票方式', sale_order_ids: '来源销售订单', move_id: '关联发票 / 凭证', move_ids: '原发票 / 凭证', sending_methods: '发送方式', extra_edis: '附加电子文件', invoice_edi_format: '电子发票格式', company_id: '公司', currency_id: '币种', amount: '金额', amount_total: '含税金额', journal_id: '日记账', product_id: '产品', product_uom_qty: '数量', product_qty: '数量', quantity: '数量', product_uom_id: '计量单位', price_unit: '单价', date: '业务日期', reason: '原因', state: '状态', name: '名称', line_ids: '会计分录', payment_type: '收付款方向', partner_type: '往来类型', group_payment: '合并收付款', payment_difference_handling: '付款差额处理' }
  return labels[key] || key
}

export function approvalReferenceText(models: string[], value: unknown, documents: Document[], fallback: string) {
  const id = Array.isArray(value) ? value[0] : value
  if (id == null || id === '') return '未读取'
  const inlineName = Array.isArray(value) && typeof value[1] === 'string' ? value[1] : ''
  const document = documents.find((item) => models.includes(item.model) && String(item.id) === String(id))
  const name = document?.name || inlineName
  return name || `${fallback}名称未读取（记录 ${String(id)}）`
}

export function approvalLineText(value: unknown, documents: Document[]) {
  if (!Array.isArray(value)) return readableValue(value)
  const rows = value.map((line) => {
    const data = Array.isArray(line) ? line[line.length - 1] : line
    if (!data || typeof data !== 'object' || Array.isArray(data)) return readableValue(line)
    const row = data as Record<string, unknown>
    const product = approvalReferenceText(['product.product'], row.product_id, documents, '商品')
    const quantity = row.product_uom_qty ?? row.product_qty ?? row.quantity ?? row.qty
    const price = row.price_unit ?? row.price
    return [product, quantity == null ? '' : `数量 ${readableValue(quantity)}`, price == null ? '' : `单价 ${readableValue(price)}`].filter(Boolean).join(' · ')
  }).filter(Boolean)
  return rows.length ? rows.join('；') : readableValue(value)
}

export function approvalValueText(model: string, key: string, value: unknown, documents: Document[]) {
  if (value === undefined) return '未读取'
  if (key === 'advance_payment_method') return ({ delivered: '按可开票数量', percentage: '按比例预付款', fixed: '固定金额预付款' } as Record<string, string>)[String(value)] || readableValue(value)
  if (key === 'sending_methods' && (value === false || Array.isArray(value))) return Array.isArray(value) && value.includes('email') ? '邮件' : '不发送邮件'
  if (key === 'extra_edis' && (value === false || Array.isArray(value) && value.length === 0)) return '不生成附加电子文件'
  if (key === 'invoice_edi_format' && value === false) return '不启用'
  if (['sale_order_ids', 'move_ids', 'pick_ids'].includes(key)) {
    const targetModel = key === 'sale_order_ids' ? 'sale.order' : key === 'move_ids' ? 'account.move' : 'stock.picking'
    return approvalRelationIds(value).map((id) => approvalReferenceText([targetModel], id, documents, modelLabel(targetModel))).join('、') || '未指定'
  }
  if (value === null || value === false || Array.isArray(value) && value.length === 0) return '清空 / 未设置'
  if (key === 'company_id') return approvalReferenceText(['res.company'], value, documents, '公司')
  if (key === 'currency_id') return approvalReferenceText(['res.currency'], value, documents, '币种')
  if (key === 'move_id') return approvalReferenceText(['account.move'], value, documents, '发票 / 凭证')
  if (key === 'picking_id') return approvalReferenceText(['stock.picking'], value, documents, '收发货单')
  if (key === 'journal_id') return approvalReferenceText(['account.journal'], value, documents, '日记账')
  if (key === 'partner_id' || key === 'partner_shipping_id' || key === 'partner_invoice_id') return approvalReferenceText(['res.partner'], value, documents, key === 'partner_id' ? (model === 'purchase.order' ? '供应商' : '客户') : '往来单位')
  if (key === 'payment_term_id') return approvalReferenceText(['account.payment.term'], value, documents, '付款条件')
  if (key === 'product_id') return approvalReferenceText(['product.product'], value, documents, '商品')
  if (key === 'product_tmpl_id') return approvalReferenceText(['product.template'], value, documents, '产品模板')
  if (key === 'order_line' || key === 'invoice_line_ids') return approvalLineText(value, documents)
  return readableValue(value)
}
