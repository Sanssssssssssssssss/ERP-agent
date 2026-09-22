import { Button as RadixButton } from '@radix-ui/themes'
import { Check as CheckIcon,Clock3,RefreshCw,X } from 'lucide-react'
import { EmptyState,StatusBadge } from '../../components/common'
import { approvalResultLabel,approvalStatusLabel,isPendingApproval,modelLabel,operationLabel,readableApprovalTitle,readableValue } from '../../presentation'
import {
Approval,
BusinessDetail,
Document,
formatExpiry,
isExpired,
jsonText
} from '../../protocol'
import { ApprovalProgress } from '../../view-types'

export function ApprovalInboxCard({ approvals, businessName, progress, activity, onOpenApprovals, onOpenExecution }: { approvals: Approval[]; businessName: string; progress: ApprovalProgress | null; activity?: NonNullable<BusinessDetail['activity']>; onOpenApprovals: () => void; onOpenExecution: () => void }) {
  const activeProgress = Boolean(progress && (approvals.length === 0 || approvals.some((approval) => approval.business_id === progress.businessId)))
  const summary = approvals.length ? `${approvals.slice(0, 2).map((approval) => readableApprovalTitle(approval)).join('、')}${approvals.length > 2 ? ` 等 ${approvals.length} 项` : ''}` : activity ? `${activity.label || '当前执行状态'}：${activity.detail || '正在读取最新状态。'}` : '正在读取最新执行状态。'
  return <section className="approval-inbox-card" role="status"><div className="approval-inbox-icon"><Clock3 size={18} /></div><div className="approval-inbox-copy"><div className="approval-inbox-kicker">{approvals.length ? `需要人工审批 · ${businessName}` : `业务执行状态 · ${businessName}`}</div><strong>{approvals.length ? `${approvals.length} 项业务动作等待确认` : (activity?.label || '业务执行状态')}</strong><span>{summary}</span>{activeProgress && <small>{progress?.status === 'submitting' ? '正在提交审批决定…' : (progress?.detail || '审批状态未生效，请查看执行详情。')}</small>}</div><RadixButton className="primary-button approval-inbox-button" onClick={approvals.length ? onOpenApprovals : onOpenExecution}>{approvals.length ? <><CheckIcon size={15} />查看并审批</> : '查看执行状态'}</RadixButton></section>
}

export function ApprovalsPage({ approvals, documents, disabled, onDecision, onReconcile, onTraceTarget }: { approvals: Approval[]; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const orderedApprovals = [...approvals].sort(compareApprovals)
  return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">需要确认</span><h3>变更与审批</h3></div><span>{approvals.filter(isPendingApproval).length} 项待处理</span></div>{approvals.length === 0 ? <EmptyState title="没有审批记录" detail="主机产生需要人工确认的业务动作后，审批卡会保留在这里。" /> : <div className="approval-list">{orderedApprovals.map((approval) => <ApprovalRow key={approval.action_id} approval={approval} documents={documents} disabled={disabled} onDecision={onDecision} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />)}</div>}</div>
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

export function ApprovalRow({ approval, documents, disabled, onDecision, onReconcile, onTraceTarget }: { approval: Approval; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const pending = isPendingApproval(approval)
  const expired = pending && isExpired(approval.expires_at)
  const title = readableApprovalTitle(approval)
  return (
    <article className={`approval-row ${pending ? 'pending' : ''}`}>
      <div className="approval-row-head">
        <div><strong>{title}</strong><span>{modelLabel(approval.model)} · {operationLabel(approval.operation)} · {approval.model} · {approval.action_id}</span></div>
        <StatusBadge status={expired ? 'expired' : approval.status} label={expired ? '已过期' : approvalStatusLabel(approval.status)} />
      </div>
      <div className="approval-facts"><span>{approvalRecordText(approval, documents)}</span><span>{pending ? formatExpiry(approval.expires_at) : '审批已结束'}</span></div>
      <InvoiceMailPreview approval={approval} />
      <ApprovalFieldDiff approval={approval} documents={documents} />
      <details>
        <summary>查看拟提交值与执行前状态</summary>
        <div className="json-columns"><div><small>拟提交值</small><pre>{jsonText(approval.values)}</pre></div><div><small>执行前状态</small><pre>{approvalPrestateText(approval)}</pre></div></div>
      </details>
      {pending && !expired && <div className="approval-actions"><RadixButton className="danger-button" variant="soft" disabled={disabled} onClick={() => onDecision(approval, 'reject')}><X size={15} />拒绝</RadixButton><RadixButton className="primary-button" disabled={disabled} onClick={() => onDecision(approval, 'approve')}><CheckIcon size={15} />批准这项业务动作</RadixButton></div>}
      {approval.status === 'needs_reconciliation' && <div className="approval-actions"><small>写入结果不确定；核对现有 Odoo 状态后才能继续。</small><RadixButton className="secondary-button" variant="soft" disabled={disabled} onClick={() => onReconcile(approval)}><RefreshCw size={15} />核对不确定写入</RadixButton></div>}
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

export function ApprovalFieldDiff({ approval, documents }: { approval: Approval; documents: Document[] }) {
  const values = approval.values || {}
  const prestate = approvalPrestateView(approval)
  const before = prestate.fields
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(values)]))
  if (!keys.length) return prestate.kind === 'multiple' ? <div className="field-diff"><p className="field-diff-note">执行前状态包含多条记录，无法压缩为单条字段对比；原始结构保留在下方。</p></div> : <p className="muted">没有可展示的字段前后值。</p>
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
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? { kind: 'fields', fields: raw as Record<string, unknown> } : { kind: 'unknown', fields: {} }
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
  const labels: Record<string, string> = { partner_id: model === 'purchase.order' ? '供应商' : '客户', payment_term_id: '付款条件', partner_shipping_id: '收货地址', partner_invoice_id: '开票地址', date_order: '下单日期', invoice_status: '开票状态', invoice_line_ids: '发票明细', order_line: '订单明细', commitment_date: '承诺日期', client_order_ref: '客户参考', records: '记录' }
  return labels[key] ? `${labels[key]}（${key}）` : key
}

export function approvalReferenceText(models: string[], value: unknown, documents: Document[], fallback: string) {
  const id = Array.isArray(value) ? value[0] : value
  if (id == null || id === '') return '未知'
  const inlineName = Array.isArray(value) && typeof value[1] === 'string' ? value[1] : ''
  const document = documents.find((item) => models.includes(item.model) && String(item.id) === String(id))
  const name = document?.name || inlineName
  return name ? `${name}（ID ${String(id)}）` : `${fallback} ID ${String(id)}`
}

export function approvalLineText(value: unknown, documents: Document[]) {
  if (!Array.isArray(value)) return readableValue(value)
  const rows = value.map((line) => {
    const data = Array.isArray(line) ? line[line.length - 1] : line
    if (!data || typeof data !== 'object' || Array.isArray(data)) return readableValue(line)
    const row = data as Record<string, unknown>
    const product = approvalReferenceText(['product.product', 'product.template'], row.product_id, documents, '商品')
    const quantity = row.product_uom_qty ?? row.product_qty ?? row.quantity ?? row.qty
    const price = row.price_unit ?? row.price
    return [product, quantity == null ? '' : `数量 ${readableValue(quantity)}`, price == null ? '' : `单价 ${readableValue(price)}`].filter(Boolean).join(' · ')
  }).filter(Boolean)
  return rows.length ? rows.join('；') : readableValue(value)
}

export function approvalValueText(model: string, key: string, value: unknown, documents: Document[]) {
  if (key === 'partner_id' || key === 'partner_shipping_id' || key === 'partner_invoice_id') return approvalReferenceText(['res.partner'], value, documents, key === 'partner_id' ? (model === 'purchase.order' ? '供应商' : '客户') : '往来单位')
  if (key === 'payment_term_id') return approvalReferenceText(['account.payment.term'], value, documents, '付款条件')
  if (key === 'product_id') return approvalReferenceText(['product.product', 'product.template'], value, documents, '商品')
  if (key === 'order_line' || key === 'invoice_line_ids') return approvalLineText(value, documents)
  return readableValue(value)
}
