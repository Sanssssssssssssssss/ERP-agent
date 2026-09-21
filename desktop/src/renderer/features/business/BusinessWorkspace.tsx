import { Button as RadixButton,Tabs as RadixTabs } from '@radix-ui/themes'
import { Activity,Check as CheckIcon,Clock3,FileText,LoaderCircle,MessageSquare,Play,RefreshCw,Square } from 'lucide-react'
import { useEffect,useState } from 'react'
import { EmptyState,MessageText,StatusBadge } from '../../components/common'
import { activityPhaseLabel,amountWithCurrency,businessTypeMeta,compactGoal,completionTargetLabel,documentStateLabel,invoiceStatusLabel,isPendingApproval,outcomeScopeLabel,outcomeStatusLabel,paymentStatusLabel,readableValue,runDisplayLabel,stageLabel,stageStatusLabel,toolLabel } from '../../presentation'
import {
Approval,
Business,
BusinessArtifact,
BusinessDetail,
BusinessDetailProjection,
BusinessEvidence,
BusinessTab,
Document,
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

export const tabs: Array<{ id: BusinessTab; label: string }> = [
  { id: 'execution', label: '执行台' },
  { id: 'documents', label: '单据与文件' },
  { id: 'approvals', label: '变更与审批' },
  { id: 'trace', label: '运行详情' }
]

export function BusinessWorkspace({ session, activeBusiness, detail, tab, trace, traceTarget, traceLoading, businessLoading, loading, selectedRunId, onBusinessSelect, onTabChange, onRunSelect, onRefresh, onStart, onCancel, onApproval, onReconcile, onTraceTarget, onToggleConversation, conversationOpen, onExport, exporting, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, approvalProgress, onOpenApprovals }: {
  session: SessionDetail | null
  activeBusiness: Business | null
  detail: BusinessDetailProjection | null
  tab: BusinessTab
  trace: TraceBundle | null
  traceTarget: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null
  traceLoading: boolean
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
  onReconcile: (approval: Approval) => void
  onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void
  onToggleConversation: () => void
  conversationOpen: boolean
  onExport: () => void
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
  return (
    <main className="business-workspace">
      <div className="business-tabs-bar">
        <div className="business-tabs-heading"><div><span className="eyebrow">业务工作区</span><strong>{businessList.length ? `${businessList.length} 个业务` : '业务页'}</strong></div><RadixButton className="conversation-toggle" variant="soft" aria-label={conversationOpen ? '收起会话' : '打开会话'} onClick={onToggleConversation}><MessageSquare size={15} />{conversationOpen ? '收起会话' : '打开会话'}</RadixButton></div>
        <div className="business-tabs" role="tablist" aria-label="业务工作区">
          {businessList.map((business) => <button role="tab" aria-selected={business.id === activeBusiness?.id} key={business.id} className={business.id === activeBusiness?.id ? 'active' : ''} onClick={() => onBusinessSelect(business.id)}>{business.title || businessTypeMeta(business.type).title}<span>{businessTypeMeta(business.type).short} · {labelFor(businessStatusLabel, business.status)}</span></button>)}
        </div>
      </div>
      {!activeBusiness && <EmptyState title="等待业务工作区" detail="在会话中确认一个业务意图后，这里会打开对应工作区。" />}
      {activeBusiness && <>
        <header className="business-header"><div><span className="eyebrow">{businessTypeMeta(activeBusiness.type).title}</span><h2>{activeBusiness.title}</h2><p tabIndex={0} aria-label="业务目标">{compactGoal(activeBusiness.goal, detail?.documents ?? [])}</p><div className="business-target-line"><span>完成目标</span><strong>{completionTargetLabel(businessInfo?.completion_target, activeBusiness.type)}</strong></div><details className="goal-details"><summary>查看原始指令</summary><p>{activeBusiness.goal || '未知'}</p></details></div><StatusBadge status={activeBusiness.status} label={labelFor(businessStatusLabel, activeBusiness.status)} /></header>
        {approvalProgress && approvalProgress.businessId === activeBusiness.id && <div className={`approval-progress approval-progress-${approvalProgress.status}`} role="status"><LoaderCircle className={approvalProgress.status === 'submitting' ? 'spin' : ''} size={15} /><span>{approvalProgress.status === 'submitting' ? '正在提交审批决定…' : (approvalProgress.detail || '审批状态未生效，请查看执行详情。')}</span></div>}
        <RadixTabs.Root className="business-tabs-root" value={tab} onValueChange={(value) => onTabChange(value as BusinessTab)}>
          <RadixTabs.List className="business-page-tabs" aria-label="业务页面">
            {tabs.map((item) => <RadixTabs.Trigger key={item.id} value={item.id}>{item.label}{item.id === 'approvals' && detail?.approvals?.filter(isPendingApproval).length ? <b>{detail.approvals.filter(isPendingApproval).length}</b> : null}</RadixTabs.Trigger>)}
          </RadixTabs.List>
          <div className="business-content">
          {businessLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />正在读取业务状态…</div>}
          <RadixTabs.Content value="execution">{!businessLoading && <ExecutionPage detail={detail} activeRun={activeRun} pendingApprovals={pendingApprovals} onOpenApprovals={onOpenApprovals} onRefresh={onRefresh} onStart={onStart} onCancel={onCancel} onEvidence={onTraceTarget} />}</RadixTabs.Content>
           <RadixTabs.Content value="documents">{!businessLoading && <DocumentsPage documents={detail?.documents ?? []} materials={(detail as DetailWithMaterials | null)?.materials ?? []} artifacts={detail?.artifacts ?? []} goal={activeBusiness.goal} stale={detail?.stale ?? false} onExport={onExport} exporting={exporting} exportPath={exportPath} onOpenDocument={onOpenDocument} onDownloadDocument={onDownloadDocument} documentDownloads={documentDownloads} selectedDocumentKey={selectedDocumentKey} onSelectedDocumentKey={onSelectedDocumentKey} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="approvals">{!businessLoading && <ApprovalsPage approvals={detail?.approvals ?? []} documents={detail?.documents ?? []} disabled={loading || businessLoading} onDecision={onApproval} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="trace">{!businessLoading && <TracePage trace={trace} runs={detail?.runs ?? []} readback={detail?.business.readback} selectedRunId={selectedRunId} loading={traceLoading} target={traceTarget} onRunSelect={onRunSelect} />}</RadixTabs.Content>
          </div>
        </RadixTabs.Root>
      </>}
    </main>
  )
}

export function ExecutionPage({ detail, activeRun, pendingApprovals, onOpenApprovals, onRefresh, onStart, onCancel, onEvidence }: { detail: BusinessDetailProjection | null; activeRun?: Run; pendingApprovals: Approval[]; onOpenApprovals: () => void; onRefresh: () => void; onStart: () => void; onCancel: (run: Run) => void; onEvidence: (evidence: BusinessEvidence) => void }) {
  const hasUnknownWrite = Boolean(detail?.approvals?.some((approval) => approval.status === 'needs_reconciliation') || detail?.runs?.some((run) => run.status === 'needs_reconciliation') || detail?.business.status === 'blocked')
  const canStart = !hasUnknownWrite && (!activeRun || !['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))
  const runActionLabel = activeRun?.status === 'completed' ? '继续执行' : activeRun?.status === 'failed' ? '重新执行' : '开始执行'
  return (
    <div className="page-stack">
      <div className="action-row">
        <RadixButton className="secondary-button" variant="soft" disabled={Boolean(activeRun && ['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))} title="读取 Odoo 最新状态不会重复写入" onClick={onRefresh}><RefreshCw size={15} />读取最新状态</RadixButton>
        {activeRun && ['running', 'awaiting_approval'].includes(activeRun.status)
          ? <RadixButton className="danger-button" variant="soft" onClick={() => onCancel(activeRun)}><Square size={14} />取消运行</RadixButton>
          : <RadixButton className="primary-button" disabled={!canStart} title={hasUnknownWrite ? '存在待核对写入，请先在变更与审批中核对' : undefined} onClick={onStart}><Play size={15} />{hasUnknownWrite ? '先核对写入' : runActionLabel}</RadixButton>}
      </div>
      {pendingApprovals.length > 0 && <section className="approval-execution-cta" role="status"><div><strong>运行已暂停，等待人工审批</strong><span>{pendingApprovals.length} 项动作需要确认后才会继续。</span></div><RadixButton className="primary-button" onClick={onOpenApprovals}><CheckIcon size={15} />查看并审批</RadixButton></section>}
      {detail?.activity && <ActivityCard activity={detail.activity} />}
      <ExecutionStages execution={detail?.execution} runStatus={activeRun?.status} onEvidence={onEvidence} />
      <BusinessFacts documents={detail?.documents ?? []} />
      <section className="outcome-summary">
        <div className="section-heading"><div><span className="eyebrow">业务结果</span><h3>{detail?.outcome?.label || '结果状态未知'}</h3></div><span>{outcomeStatusLabel(detail?.outcome?.status)}</span></div>
        <p>{detail?.outcome?.detail || '主机尚未提供业务结果说明。'}</p>
        {detail?.outcome?.scope && <small>范围：{outcomeScopeLabel(detail.outcome.scope)}</small>}
      </section>
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
          <div><span>最近摘要</span><strong>{detail?.summary || '暂无主机摘要'}</strong></div>
        </div>
      </section><section className="run-summary">
        <div className="section-heading">
          <div><span className="eyebrow">执行记录</span><h3>最近运行</h3></div>
          <span>{detail?.runs?.length ?? 0} 次</span>
        </div>
        {detail?.runs?.length
          ? detail.runs.slice(0, 4).map((run) => <RunRow key={run.id} run={run} />)
          : <EmptyState title="还没有运行" detail="读取状态不会触发模型；点击开始执行才会创建运行。" />}
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
  if (!stages.length) return <section className="stage-panel"><div className="section-heading"><div><span className="eyebrow">执行阶段</span><h3>阶段状态未知</h3></div><span>主机尚未提供阶段计划</span></div><p className="muted">等待业务执行投影。</p></section>
  return <section className="stage-panel"><div className="section-heading"><div><span className="eyebrow">执行阶段</span><h3>当前业务进度</h3></div><span>{stageCaption}</span></div><div className="stage-layout"><nav className="stage-list" aria-label="业务执行阶段">{stages.map((stage) => <button className={`stage-row stage-${stage.status || 'unknown'} ${stage.id === execution?.current_stage_id ? 'current' : ''} ${stage.id === selectedStage?.id ? 'selected' : ''}`} type="button" key={stage.id} onClick={() => setSelectedStageId(stage.id)}><span className="stage-number" aria-hidden="true">{stage.id === execution?.current_stage_id ? '●' : '○'}</span><span><strong>{stage.label || stageLabel(stage.id)}</strong><small>{stageStatusLabel(stage.status)}</small></span></button>)}</nav><article className="stage-detail"><div className="stage-row-head"><strong>{selectedStage?.label || stageLabel(selectedStage?.id)}</strong><span>{stageStatusLabel(selectedStage?.status)}</span></div><p>{selectedStage?.detail || '没有阶段详情。'}</p>{selectedStage?.evidence?.length ? <div className="evidence-list">{selectedStage.evidence.map((evidence, index) => { const kind = (evidence as BusinessEvidence & { kind?: string }).kind; return <button className="evidence-link" type="button" key={`${evidence.run_id || 'run'}:${evidence.tool_id || index}`} onClick={() => onEvidence(evidence)}>{kind === 'readback' ? '查看独立回读快照' : (evidence.label || '查看执行证据')} · {evidence.observed_at ? formatInstant(evidence.observed_at) : '未知时间'}</button> })}</div> : <span className="muted">暂无关联证据</span>}</article></div></section>
}

export function BusinessFacts({ documents }: { documents: Document[] }) {
  const currentDocuments = documents.filter((document) => !document.is_reference && document.document_scope !== 'reference')
  const orders = currentDocuments.filter((document) => document.model === 'sale.order')
  const purchaseOrders = currentDocuments.filter((document) => document.model === 'purchase.order')
  const invoices = currentDocuments.filter((document) => document.model === 'account.move')
  const pickings = currentDocuments.filter((document) => document.model === 'stock.picking')
  const fact = (document: Document | undefined, keys: string[]) => {
    if (!document) return '未观测'
    const value = keys.map((key) => key === 'state' ? document.state : document.fields[key]).find((candidate) => candidate !== undefined && candidate !== null && candidate !== '')
    return readableValue(value)
  }
  return (
    <section className="business-facts">
      <div className="section-heading"><div><span className="eyebrow">业务记录</span><h3>业务关键事实</h3></div><span>来自已观测单据</span></div>
      {orders.length === 0 && purchaseOrders.length === 0 && invoices.length === 0 && <div className="facts-empty"><FileText size={16} /><span>尚未观察到销售订单、采购订单或发票</span></div>}
      {orders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>销售订单</strong><span>{orders.length} 张</span></div>{orders.map((order) => <div className="business-facts-grid" key={`order:${order.id}`}><div><span>订单</span><strong>{order.name || order.id}</strong></div><div><span>客户</span><strong>{fact(order, ['partner_name', 'customer', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)} · 开票 {invoiceStatusLabel(fact(order, ['invoice_status']))}</strong></div></div>)}</div>}
      {purchaseOrders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>采购订单</strong><span>{purchaseOrders.length} 张</span></div>{purchaseOrders.map((order) => <div className="business-facts-grid" key={`purchase:${order.id}`}><div><span>采购单</span><strong>{order.name || order.id}</strong></div><div><span>供应商</span><strong>{fact(order, ['partner_name', 'vendor', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)}</strong></div></div>)}</div>}
      {invoices.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>客户发票</strong><span>{invoices.length} 张</span></div>{invoices.map((invoice) => <div className="business-facts-grid" key={`invoice:${invoice.id}`}><div><span>发票</span><strong>{invoice.name || invoice.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(invoice.model, invoice.state)}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(invoice, ['amount_total', 'total']), fact(invoice, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>未付余额</span><strong>{fact(invoice, ['amount_residual', 'residual'])}</strong></div><div><span>付款状态</span><strong>{paymentStatusLabel(fact(invoice, ['payment_state']))}</strong></div></div>)}</div>}
      {pickings.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>出库状态</strong><span>{pickings.length} 张</span></div>{pickings.map((picking) => <div className="business-facts-grid" key={`picking:${picking.id}`}><div><span>出库单</span><strong>{picking.name || picking.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(picking.model, picking.state)}</strong></div></div>)}</div>}
    </section>
  )
}

export function ActivityCard({ activity }: { activity: NonNullable<BusinessDetail['activity']> }) {
  const phase = activity.phase || 'unknown'
  const moving = ['model', 'tool', 'cancelling'].includes(phase)
  const intent = (activity as NonNullable<BusinessDetail['activity']> & { intent?: string }).intent?.trim()
  const intentPreview = intent && intent.length > 220 ? `${intent.slice(0, 217)}…` : intent
  return <section key={phase} className={`activity-card activity-phase-${phase}`} aria-label="当前动作">
    <div className="activity-icon">{moving ? <LoaderCircle className="spin" size={17} /> : phase === 'approval' ? <Clock3 size={17} /> : <Activity size={17} />}</div>
    <div className="activity-copy"><span className="eyebrow">当前动作</span><strong>{activity.label || '读取状态中'}</strong><p>{activity.detail || '暂无动作详情'}</p>{intent && <div className="activity-intent"><span>Agent 当前说明</span><MessageText text={intentPreview || ''} collapsible={false} />{intent.length > 220 && <details><summary>查看完整说明</summary><MessageText text={intent} collapsible={false} /></details>}</div>}<div className="activity-meta"><span>{activityPhaseLabel(activity.phase)}</span>{activity.tool_name && <span title={activity.tool_name}>{toolLabel(activity.tool_name)}</span>}{activity.round != null && <span>第 {activity.round} 轮</span>}{activity.tool_count != null && <span>{activity.tool_count} 个工具</span>}{activity.model_rounds != null && <span>{activity.model_rounds} 轮模型</span>}{activity.at && <span>{formatInstant(activity.at)}</span>}</div></div>
  </section>
}
