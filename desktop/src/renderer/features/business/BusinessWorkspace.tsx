import { Button as RadixButton,Tabs as RadixTabs } from '@radix-ui/themes'
import { Activity,Check as CheckIcon,Clock3,Download,FileText,LoaderCircle,MessageSquare,Play,RefreshCw,Square } from 'lucide-react'
import { useEffect,useRef,useState } from 'react'
import { EmptyState,MessageText,StatusBadge } from '../../components/common'
import { activityPhaseLabel,amountWithCurrency,businessTypeMeta,completionTargetLabel,documentStateLabel,documentFact,documentModelLabel,invoiceStatusLabel,isPendingApproval,outcomeScopeLabel,outcomeStatusLabel,paymentStatusLabel,readableValue,runDisplayLabel,stageLabel,stageStatusLabel,toolLabel } from '../../presentation'
import {
Approval,
Business,
BusinessArtifact,
BusinessDetail,
BusinessDetailProjection,
BusinessEvidence,
BusinessReceipt,
BusinessTab,
Document,
LiveMessage,
Run,
SessionDetail,
TraceBundle,
businessStatusLabel,
formatInstant,
labelFor
} from '../../protocol'
import { ApprovalProgress,BusinessWithType,DetailWithMaterials,DownloadReceipt } from '../../view-types'
import { ApprovalsPage } from '../approvals/ApprovalsPage'
import { DocumentsPage } from '../documents/DocumentsPage'
import { RunRow,TracePage,VerificationPage } from '../traces/TracePage'
import type { LoadTraceDetail } from '../traces/TraceInspectorDetails'

export const tabs: Array<{ id: BusinessTab; label: string }> = [
  { id: 'execution', label: '执行台' },
  { id: 'documents', label: '单据与文件' },
  { id: 'approvals', label: '变更与审批' },
  { id: 'trace', label: '运行详情' }
]

export function BusinessWorkspace({ session, activeBusiness, detail, liveMessages = [], tab, trace, traceTarget, traceLoading, onLoadTraceDetail, businessLoading, loading, selectedRunId, onBusinessSelect, onTabChange, onRunSelect, onRefresh, onStart, onCancel, onApproval, onRequestRevision, onReconcile, onTraceTarget, onToggleConversation, conversationOpen, onExport, exporting, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, approvalProgress, onOpenApprovals }: {
  session: SessionDetail | null
  activeBusiness: Business | null
  detail: BusinessDetailProjection | null
  liveMessages?: LiveMessage[]
  tab: BusinessTab
  trace: TraceBundle | null
  traceTarget: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null
  traceLoading: boolean
  onLoadTraceDetail?: LoadTraceDetail
  businessLoading: boolean
  loading: boolean
  selectedRunId: string
  onBusinessSelect: (id: string) => void
  onTabChange: (tab: BusinessTab) => void
  onRunSelect: (id: string) => void
  onRefresh: () => void
  onStart: () => void
  onCancel: (run: Run) => void
  onApproval: (approval: Approval, decision: 'approve' | 'reject') => void
  onRequestRevision?: (approval: Approval, text: string) => Promise<void>
  onReconcile: (approval: Approval) => void
  onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void
  onToggleConversation: () => void
  conversationOpen: boolean
  onExport: (runId?: string) => void
  exporting: boolean
  exportPath: string
  onOpenDocument: (document: Document) => void
  onDownloadDocument: (document: Document, format: 'pdf' | 'csv') => void
  documentDownloads: Record<string, DownloadReceipt>
  selectedDocumentKey: string
  onSelectedDocumentKey: (key: string) => void
  onOpenArtifact: (artifact: BusinessArtifact) => void
  onRevealArtifact: (artifact: BusinessArtifact) => void
  approvalProgress: ApprovalProgress | null
  onOpenApprovals: () => void
}) {
  const businessList = session?.businesses ?? []
  const businessInfo = activeBusiness as BusinessWithType | null
  const activeRun = detail?.runs?.find((run) => run.id === detail.business.active_run_id)
    ?? detail?.runs?.find((run) => ['running', 'awaiting_approval', 'cancel_requested'].includes(run.status))
    ?? detail?.runs?.[0]
  const pendingApprovals = detail?.approvals?.filter(isPendingApproval) ?? []
  const businessMessages = liveMessages.filter((message) => message.session_id === session?.session.id && message.business_id === activeBusiness?.id && (!message.role || message.role === 'assistant'))
  const acceptedProposal = [...(session?.messages ?? [])].reverse().find((message) => message.business_id === activeBusiness?.id && message.proposal?.status === 'confirmed')?.proposal
  const workspaceRef = useRef<HTMLElement>(null)
  const previousRun = useRef<{ id: string; status: string } | null>(null)
  useEffect(() => {
    if (businessLoading) return
    const previous = previousRun.current
    previousRun.current = activeRun ? { id: activeRun.id, status: activeRun.status } : null
    if (tab === 'execution' && previous?.id === activeRun?.id && ['running', 'awaiting_approval', 'cancel_requested'].includes(previous?.status ?? '') && ['completed', 'failed', 'cancelled', 'interrupted', 'needs_reconciliation'].includes(activeRun?.status ?? '')) {
      workspaceRef.current?.querySelector('.outcome-summary')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    }
  }, [activeRun?.id, activeRun?.status, businessLoading, tab])
  return (
    <main className="business-workspace" ref={workspaceRef}>
      <div className="business-tabs-bar">
        <div className="business-tabs-heading"><div><span className="eyebrow">业务工作区</span><strong>{businessList.length ? `${businessList.length} 个业务` : '业务页'}</strong></div><RadixButton className="conversation-toggle" variant="soft" aria-label={conversationOpen ? '收起会话' : '打开会话'} onClick={onToggleConversation}><MessageSquare size={15} />{conversationOpen ? '收起会话' : '打开会话'}</RadixButton></div>
        <div className="business-tabs" role="tablist" aria-label="业务工作区">
          {businessList.map((business) => <button role="tab" aria-selected={business.id === activeBusiness?.id} key={business.id} className={business.id === activeBusiness?.id ? 'active' : ''} onClick={() => onBusinessSelect(business.id)}>{business.title || businessTypeMeta(business.type).title}<span>{businessTypeMeta(business.type).short} · {labelFor(businessStatusLabel, business.status)}</span></button>)}
        </div>
      </div>
      {!activeBusiness && <EmptyState title="等待业务工作区" detail="在会话中确认一个业务意图后，这里会打开对应工作区。" />}
      {activeBusiness && <>
        <header className="business-header"><div><h2>{activeBusiness.title || businessTypeMeta(activeBusiness.type).title}</h2><div className="business-target-line"><span>完成目标</span><strong>{completionTargetLabel(businessInfo?.completion_target, activeBusiness.type)}</strong></div>{acceptedProposal?.goal && <details className="goal-details"><summary>查看已确认的业务说明</summary><p>{acceptedProposal.goal}</p></details>}<details className="goal-details"><summary>查看原始指令</summary><p>{activeBusiness.goal || '未保存原始指令'}</p></details></div><StatusBadge status={activeBusiness.status} label={labelFor(businessStatusLabel, activeBusiness.status)} /></header>
        {approvalProgress && approvalProgress.businessId === activeBusiness.id && <div className={`approval-progress approval-progress-${approvalProgress.status}`} role="status"><LoaderCircle className={approvalProgress.status === 'submitting' ? 'spin' : ''} size={15} /><span>{approvalProgress.status === 'submitting' ? '正在提交审批决定…' : (approvalProgress.detail || '审批状态未生效，请查看执行详情。')}</span></div>}
        <RadixTabs.Root className="business-tabs-root" value={tab} onValueChange={(value) => onTabChange(value as BusinessTab)}>
          <RadixTabs.List className="business-page-tabs" aria-label="业务页面">
            {tabs.map((item) => <RadixTabs.Trigger key={item.id} value={item.id}>{item.label}{item.id === 'approvals' && detail?.approvals?.filter(isPendingApproval).length ? <b>{detail.approvals.filter(isPendingApproval).length}</b> : null}</RadixTabs.Trigger>)}
          </RadixTabs.List>
          <div className="business-content">
          {businessLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />正在读取业务状态…</div>}
          <RadixTabs.Content value="execution">{!businessLoading && <ExecutionPage detail={detail} activeRun={activeRun} liveMessages={businessMessages.filter((message) => message.run_id === activeRun?.id)} pendingApprovals={pendingApprovals} onOpenApprovals={onOpenApprovals} onRefresh={onRefresh} onStart={onStart} onCancel={onCancel} onEvidence={onTraceTarget} onExport={() => onExport(activeRun?.id)} exporting={exporting} exportPath={exportPath} />}</RadixTabs.Content>
           <RadixTabs.Content value="documents">{!businessLoading && <DocumentsPage documents={detail?.documents ?? []} materials={(detail as DetailWithMaterials | null)?.materials ?? []} artifacts={detail?.artifacts ?? []} goal={activeBusiness.goal} stale={detail?.stale ?? false} onExport={() => onExport()} exporting={exporting} exportPath={exportPath} onOpenDocument={onOpenDocument} onDownloadDocument={onDownloadDocument} documentDownloads={documentDownloads} selectedDocumentKey={selectedDocumentKey} onSelectedDocumentKey={onSelectedDocumentKey} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="approvals">{!businessLoading && <ApprovalsPage approvals={detail?.approvals ?? []} documents={detail?.documents ?? []} disabled={loading || businessLoading} onDecision={onApproval} onRequestRevision={onRequestRevision} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="trace">{!businessLoading && <TracePage trace={trace} liveMessages={businessMessages} runs={detail?.runs ?? []} readback={detail?.business.readback} selectedRunId={selectedRunId} loading={traceLoading} target={traceTarget} onRunSelect={onRunSelect} onLoadDetail={onLoadTraceDetail} />}</RadixTabs.Content>
          </div>
        </RadixTabs.Root>
      </>}
    </main>
  )
}

export function ExecutionPage({ detail, activeRun, liveMessages = [], pendingApprovals, onOpenApprovals, onRefresh, onStart, onCancel, onEvidence, onExport, exporting, exportPath }: { detail: BusinessDetailProjection | null; activeRun?: Run; liveMessages?: LiveMessage[]; pendingApprovals: Approval[]; onOpenApprovals: () => void; onRefresh: () => void; onStart: () => void; onCancel: (run: Run) => void; onEvidence: (evidence: BusinessEvidence) => void; onExport: () => void; exporting: boolean; exportPath: string }) {
  const hasUnknownWrite = Boolean(detail?.approvals?.some((approval) => approval.status === 'needs_reconciliation') || detail?.runs?.some((run) => run.status === 'needs_reconciliation') || detail?.business.status === 'blocked')
  const canStart = !hasUnknownWrite && (!activeRun || !['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))
  const runActionLabel = activeRun?.status === 'completed' ? '继续执行' : activeRun?.status === 'failed' ? '重新执行' : '开始执行'
  const ended = Boolean(activeRun && ['completed', 'failed', 'cancelled', 'interrupted', 'needs_reconciliation'].includes(activeRun.status))
  const oldReadback = Boolean(detail?.stale || (detail?.business.readback?.latest_run_id && detail.business.readback.latest_run_id !== activeRun?.id))
  const unverifiedFailure = ended && activeRun?.status !== 'completed' && activeRun?.verification_status !== 'passed'
  const outcome = (oldReadback || unverifiedFailure) && detail?.outcome?.status === 'passed'
    ? { status: 'unknown', label: '本轮结果待核对', detail: '尚未取得本轮的完整核验结果，请查看核验详情。', scope: '' }
    : detail?.outcome
  const result = <section className="outcome-summary" aria-label="执行结果" aria-live="polite">
    <div className="section-heading"><h3>{outcome?.label || '结果待核对'}</h3><StatusBadge status={outcome?.status} label={outcomeStatusLabel(outcome?.status)} /></div>
    <p>{outcome?.detail || '完成执行后将在这里显示核验结果。'}</p>
    {outcome?.scope && <small>核验范围：{outcomeScopeLabel(outcome.scope)}</small>}
    {ended && <small>{runDisplayLabel(activeRun?.status)}{detail?.observed_at ? ` · 数据更新于 ${formatInstant(detail.observed_at)}` : ''}</small>}
    {ended && (activeRun?.summary?.trim() ? <details className="run-conclusion"><summary>查看 Agent 完整回复</summary><MessageText text={activeRun.summary} collapsible={false} /></details> : <p>本轮未返回总结，可查看核验结果和运行详情。</p>)}
  </section>
  return (
    <div className="page-stack">
      <div className="action-row">
        <RadixButton className="secondary-button" variant="soft" disabled={Boolean(activeRun && ['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))} title="读取 Odoo 最新状态不会重复写入" onClick={onRefresh}><RefreshCw size={15} />读取最新状态</RadixButton>
        {activeRun && ['running', 'awaiting_approval'].includes(activeRun.status)
          ? <RadixButton className="danger-button" variant="soft" onClick={() => onCancel(activeRun)}><Square size={14} />取消运行</RadixButton>
          : <RadixButton className="primary-button" disabled={!canStart} title={hasUnknownWrite ? '存在待核对写入，请先在变更与审批中核对' : undefined} onClick={onStart}><Play size={15} />{hasUnknownWrite ? '先核对写入' : runActionLabel}</RadixButton>}
      </div>
      {pendingApprovals.length > 0 && <section className="approval-execution-cta" role="status"><div><strong>运行已暂停，等待人工审批</strong><span>{pendingApprovals.length} 项动作需要确认后才会继续。</span></div><RadixButton className="primary-button" onClick={onOpenApprovals}><CheckIcon size={15} />查看并审批</RadixButton></section>}
      {ended && result}
      {ended && <ExecutionReceipts receipts={detail?.receipts ?? []} onEvidence={onEvidence} onExport={onExport} exporting={exporting} exportPath={exportPath} />}
      {detail?.activity && activeRun?.status !== 'completed' && <ActivityCard key={activeRun?.id} activity={detail.activity} run={activeRun} liveMessages={liveMessages} />}
      {ended && <BusinessFacts documents={detail?.documents ?? []} />}
      <ExecutionStages execution={detail?.execution} runStatus={activeRun?.status} onEvidence={onEvidence} />
      {!ended && <><BusinessFacts documents={detail?.documents ?? []} />{result}</>}
      <details className="verification-fold"><summary>查看独立回读核验 · {detail?.checks?.length ?? 0} 项</summary><VerificationPage checks={detail?.checks ?? []} observedAt={detail?.observed_at} stale={detail?.stale ?? false} /></details>
      <details className="lower-facts"><summary>查看状态与最近运行</summary><section className="status-table-section">
        <div className="section-heading">
          <div><span className="eyebrow">状态与回执</span><h3>业务状态</h3></div>
          <span>{detail?.stale ? '数据可能已过期' : `观测于 ${formatInstant(detail?.observed_at)}`}</span>
        </div>
        <div className="fact-table">
          <div><span>工作区状态</span><strong>{labelFor(businessStatusLabel, detail?.business.status)}</strong></div>
          <div><span>当前运行</span><strong>{activeRun ? runDisplayLabel(activeRun.status) : '没有运行'}</strong></div>
          <div><span>回读核验</span><strong>{labelFor({passed: '通过', failed: '未通过', unknown: '未知'}, activeRun?.verification_status)}</strong></div>
        </div>
      </section><section className="run-summary">
        <div className="section-heading">
          <div><span className="eyebrow">执行记录</span><h3>最近运行</h3></div>
          <span>{detail?.runs?.length ?? 0} 次</span>
        </div>
        {detail?.runs?.length
          ? detail.runs.slice(0, 4).map((run) => <RunRow key={run.id} run={run} />)
          : <EmptyState title="尚未执行" detail="确认业务要求后，点击开始执行。" />}
      </section></details>
    </div>
  )
}

export function ExecutionStages({ execution, runStatus, onEvidence }: { execution?: BusinessDetailProjection['execution']; runStatus?: string; onEvidence: (evidence: BusinessEvidence) => void }) {
  const stages = execution?.stages ?? []
  const [selectedStageId, setSelectedStageId] = useState('')
  useEffect(() => { if (!selectedStageId || !stages.some((stage) => stage.id === selectedStageId)) setSelectedStageId(execution?.current_stage_id || stages[0]?.id || '') }, [execution?.current_stage_id, selectedStageId, stages])
  const selectedStage = stages.find((stage) => stage.id === selectedStageId) ?? stages[0]
  const stageCaption = runStatus === 'completed' ? '本轮已结束 · 可查看各阶段证据' : execution?.current_stage_id ? `当前：${stageLabel(execution.current_stage_id)}` : '当前阶段未知'
  if (!stages.length) return <section className="stage-panel"><div className="section-heading"><h3>执行进度</h3></div><p className="muted">尚无执行记录。</p></section>
  return <section className="stage-panel"><div className="section-heading"><div><span className="eyebrow">执行阶段</span><h3>当前业务进度</h3></div><span>{stageCaption}</span></div><div className="stage-layout"><nav className="stage-list" aria-label="业务执行阶段">{stages.map((stage) => <button className={`stage-row stage-${stage.status || 'unknown'} ${stage.id === execution?.current_stage_id ? 'current' : ''} ${stage.id === selectedStage?.id ? 'selected' : ''}`} type="button" key={stage.id} onClick={() => setSelectedStageId(stage.id)}><span className="stage-number" aria-hidden="true">{stage.id === execution?.current_stage_id ? '●' : '○'}</span><span><strong>{stage.label || stageLabel(stage.id)}</strong><small>{stageStatusLabel(stage.status)}</small></span></button>)}</nav><article className="stage-detail"><div className="stage-row-head"><strong>{selectedStage?.label || stageLabel(selectedStage?.id)}</strong><span>{stageStatusLabel(selectedStage?.status)}</span></div><p>{selectedStage?.detail || '没有阶段详情。'}</p>{selectedStage?.evidence?.length ? <div className="evidence-list">{selectedStage.evidence.map((evidence, index) => { const kind = (evidence as BusinessEvidence & { kind?: string }).kind; return <button className="evidence-link" type="button" key={`${evidence.run_id || 'run'}:${evidence.tool_id || index}`} onClick={() => onEvidence(evidence)}>{kind === 'readback' ? '查看独立回读快照' : (evidence.label || '查看执行证据')} · {evidence.observed_at ? formatInstant(evidence.observed_at) : '未知时间'}</button> })}</div> : <span className="muted">暂无关联证据</span>}</article></div></section>
}

export function BusinessFacts({ documents }: { documents: Document[] }) {
  const currentDocuments = documents.filter((document) => !document.is_reference && document.document_scope !== 'reference')
  const orders = currentDocuments.filter((document) => document.model === 'sale.order')
  const purchaseOrders = currentDocuments.filter((document) => document.model === 'purchase.order')
  const invoices = currentDocuments.filter((document) => document.model === 'account.move')
  const pickings = currentDocuments.filter((document) => document.model === 'stock.picking')
  const other = currentDocuments.filter((document) => ['mrp.production', 'account.payment', 'account.bank.statement.line', 'account.move.line'].includes(document.model))
  const fact = (document: Document | undefined, keys: string[]) => {
    if (!document) return '未观测'
    const value = keys.map((key) => key === 'state' ? document.state : document.fields[key]).find((candidate) => candidate !== undefined && candidate !== null && candidate !== '')
    return readableValue(value)
  }
  return (
    <section className="business-facts">
      <div className="section-heading"><div><span className="eyebrow">业务记录</span><h3>业务关键事实</h3></div><span>来自已观测单据</span></div>
      {orders.length === 0 && purchaseOrders.length === 0 && invoices.length === 0 && pickings.length === 0 && other.length === 0 && <div className="facts-empty"><FileText size={16} /><span>尚未观察到本业务的目标单据</span></div>}
      {orders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>销售订单</strong><span>{orders.length} 张</span></div>{orders.map((order) => <div className="business-facts-grid" key={`order:${order.id}`}><div><span>订单</span><strong>{order.name || order.id}</strong></div><div><span>客户</span><strong>{fact(order, ['partner_name', 'customer', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)} · 开票 {invoiceStatusLabel(fact(order, ['invoice_status']))}</strong></div></div>)}</div>}
      {purchaseOrders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>采购订单</strong><span>{purchaseOrders.length} 张</span></div>{purchaseOrders.map((order) => <div className="business-facts-grid" key={`purchase:${order.id}`}><div><span>采购单</span><strong>{order.name || order.id}</strong></div><div><span>供应商</span><strong>{fact(order, ['partner_name', 'vendor', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)}</strong></div></div>)}</div>}
      {invoices.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>发票与贷项</strong><span>{invoices.length} 张</span></div>{invoices.map((invoice) => <div className="business-facts-grid" key={`invoice:${invoice.id}`}><div><span>发票</span><strong>{invoice.name || invoice.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(invoice.model, invoice.state)}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(invoice, ['amount_total', 'total']), fact(invoice, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>未付余额</span><strong>{fact(invoice, ['amount_residual', 'residual'])}</strong></div><div><span>付款状态</span><strong>{paymentStatusLabel(fact(invoice, ['payment_state']))}</strong></div></div>)}</div>}
      {pickings.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>收发货与退货</strong><span>{pickings.length} 张</span></div>{pickings.map((picking) => <div className="business-facts-grid" key={`picking:${picking.id}`}><div><span>库存单</span><strong>{picking.name || picking.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(picking.model, picking.state)}</strong></div></div>)}</div>}
      {other.map((document) => <div className="record-fact-block" key={`${document.model}:${document.id}`}><div className="record-fact-heading"><strong>{documentModelLabel(document.model)}</strong><span>{document.name || document.id}</span></div><p>{documentFact(document)}</p></div>)}
    </section>
  )
}

export function ExecutionReceipts({ receipts, onEvidence, onExport, exporting, exportPath }: { receipts: BusinessReceipt[]; onEvidence: (evidence: BusinessEvidence) => void; onExport: () => void; exporting: boolean; exportPath: string }) {
  const actions = receipts.filter((receipt) => receipt.kind === 'action')
  const emails = receipts.filter((receipt) => receipt.kind === 'email')
  const archives = receipts.filter((receipt) => receipt.kind === 'archive')
  const labels: Record<BusinessReceipt['status'], string> = { verified: '有回执', unknown: '待核对', not_observed: '无回执', pending: '待审批', not_executed: '未执行', failed: '未完成' }
  const row = (receipt: BusinessReceipt) => <li className={`execution-receipt receipt-${receipt.status}`} key={receipt.id}>
    <div className="receipt-heading"><strong>{receipt.title}</strong><span className="receipt-status">{labels[receipt.status]}</span></div>
    <p>{receipt.detail}</p>
    <div className="receipt-meta">{receipt.observed_at && <time>{formatInstant(receipt.observed_at)}</time>}{receipt.run_id && <button className="evidence-link" type="button" onClick={() => onEvidence({ run_id: receipt.run_id!, action_id: receipt.action_id, tool_id: receipt.tool_id, kind: receipt.tool_id ? 'tool' : 'action', label: receipt.title })}>查看证据</button>}</div>
  </li>
  return <section className="execution-receipts" aria-label="执行回执与留档">
    <div className="section-heading"><div><h3>执行回执与留档</h3><span className="muted">以落库记录和执行回执为准</span></div><RadixButton variant="soft" disabled={exporting} onClick={onExport}><Download size={15} />{exporting ? '正在导出…' : '导出业务回执'}</RadixButton></div>
    {receipts.length ? <><ul className="receipt-list">{emails.map(row)}{actions.slice(-4).map(row)}{archives.map(row)}</ul>{actions.length > 4 && <details className="receipt-history"><summary>查看前 {actions.length - 4} 项动作回执</summary><ul className="receipt-list">{actions.slice(0, -4).map(row)}</ul></details>}</> : <p className="muted">尚未读取动作与邮件回执，可查看运行详情或导出业务记录。</p>}
    {exportPath && <p className="export-path" role="status">已导出：{exportPath}</p>}
  </section>
}

export function ActivityCard({ activity, run, liveMessages = [] }: { activity: NonNullable<BusinessDetail['activity']>; run?: Run; liveMessages?: LiveMessage[] }) {
  const phase = activity.phase || 'unknown'
  const running = run?.status === 'running' || run?.status === 'cancel_requested'
  const toolRunning = running && phase === 'tool' && (!activity.tool_status || activity.tool_status === 'running')
  const moving = running && (['model', 'model_wait', 'cancelling'].includes(phase) || toolRunning)
  const latestReply = liveMessages.filter((message) => message.text?.trim()).at(-1)
  const streaming = running && ['model', 'model_wait'].includes(phase) && latestReply?.status === 'streaming'
  const publicText = latestReply?.text || activity.intent?.trim()
  const publicTextRef = useRef<HTMLDivElement>(null)
  const followReplyRef = useRef(true)
  useEffect(() => { followReplyRef.current = true }, [latestReply?.id])
  useEffect(() => {
    const element = publicTextRef.current
    if (element && followReplyRef.current) element.scrollTop = element.scrollHeight
  }, [publicText])
  return <section className={`activity-card activity-phase-${phase}`} aria-label="当前动作" data-active={moving ? 'true' : 'false'}>
    <div className="activity-icon">{moving ? <LoaderCircle className="spin" size={17} /> : phase === 'approval' ? <Clock3 size={17} /> : <Activity size={17} />}</div>
    <div className="activity-copy"><span className="eyebrow">当前动作</span><strong role="status">{streaming ? '正在回复' : activity.label || '读取状态中'}</strong><p>{activity.detail || '暂无动作详情'}</p>
      {publicText && <div className="activity-intent"><span>{streaming ? '公开回复 · 更新中' : '最近公开回复'}</span><div ref={publicTextRef} className="activity-public-text" tabIndex={0} aria-label="公开回复" onScroll={(event) => { const element = event.currentTarget; followReplyRef.current = element.scrollHeight - element.clientHeight - element.scrollTop < 32 }}><MessageText text={publicText} collapsible={false} /></div></div>}
      <div className="activity-meta"><span>{activityPhaseLabel(activity.phase)}</span>{activity.tool_name && <span title={activity.tool_name}>{toolRunning ? '正在执行：' : '最近工具：'}{toolLabel(activity.tool_name)}</span>}{activity.round != null && <span>第 {activity.round} 轮</span>}{activity.tool_count != null && <span>{activity.tool_count} 个工具</span>}{activity.model_rounds != null && <span>{activity.model_rounds} 轮模型</span>}{activity.at && <span>{formatInstant(activity.at)}</span>}</div>
    </div>
  </section>
}
