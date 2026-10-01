import { Button as RadixButton,Tabs as RadixTabs } from '@radix-ui/themes'
import { Activity,Check as CheckIcon,CircleAlert,Clock3,Download,FileText,LoaderCircle,MessageSquare,Play,RefreshCw,Square } from 'lucide-react'
import { useEffect,useRef,useState } from 'react'
import { EmptyState,MessageText,StatusBadge } from '../../components/common'
import { activityPhaseLabel,amountWithCurrency,approvalStatusLabel,modelLabel,operationLabel,businessTypeMeta,completionTargetLabel,documentStateLabel,documentFact,documentModelLabel,invoiceStatusLabel,isPendingApproval,outcomeScopeLabel,outcomeStatusLabel,paymentStatusLabel,readableValue,runDisplayLabel,stageLabel,stageStatusLabel,toolLabel } from '../../presentation'
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
import { approvalActionTitle } from '../approvals/approval-presentation'
import { ApprovalRow,ApprovalsPage,compareApprovals } from '../approvals/ApprovalsPage'
import { DocumentsPage } from '../documents/DocumentsPage'
import { RunRow,TracePage,VerificationPage } from '../traces/TracePage'
import type { LoadTraceDetail } from '../traces/TraceInspectorDetails'

export const tabs: Array<{ id: BusinessTab; label: string }> = [
  { id: 'execution', label: '执行台' },
  { id: 'documents', label: '单据与文件' },
  { id: 'approvals', label: '变更与审批' },
  { id: 'trace', label: '运行详情' }
]

export function BusinessWorkspace({ session, activeBusiness, detail, liveMessages = [], tab, trace, traceTarget, traceLoading, onLoadTraceDetail, businessLoading, loading, starting = false, focusedApprovalId, focusedApprovalRequest, selectedRunId, onBusinessSelect, onTabChange, onRunSelect, onRefresh, onReconcileBusiness, onStart, onCancel, onApproval, onRequestRevision, onReconcile, onTraceTarget, onToggleConversation, conversationOpen, onExport, exporting, onSnapshot, snapshotOpening, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, approvalProgress, onOpenApprovals }: {
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
  starting?: boolean
  focusedApprovalId?: string
  focusedApprovalRequest?: number
  selectedRunId: string
  onBusinessSelect: (id: string) => void
  onTabChange: (tab: BusinessTab) => void
  onRunSelect: (id: string) => void
  onRefresh: () => void
  onReconcileBusiness?: () => void
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
  onSnapshot?: () => void
  snapshotOpening?: boolean
  exportPath: string
  onOpenDocument: (document: Document) => void
  onDownloadDocument: (document: Document, format: 'pdf' | 'csv') => void
  documentDownloads: Record<string, DownloadReceipt>
  selectedDocumentKey: string
  onSelectedDocumentKey: (key: string) => void
  onOpenArtifact: (artifact: BusinessArtifact) => void
  onRevealArtifact: (artifact: BusinessArtifact) => void
  approvalProgress: ApprovalProgress | null
  onOpenApprovals: (actionId?: string) => void
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
  const followingResult = useRef(true)
  useEffect(() => {
    if (businessLoading) return
    const previous = previousRun.current
    previousRun.current = activeRun ? { id: activeRun.id, status: activeRun.status } : null
    if (tab === 'execution' && previous?.id === activeRun?.id && ['running', 'awaiting_approval', 'cancel_requested'].includes(previous?.status ?? '') && ['completed', 'failed', 'cancelled', 'interrupted', 'needs_reconciliation'].includes(activeRun?.status ?? '')) {
      if (!followingResult.current) return
      workspaceRef.current?.querySelector('.outcome-summary')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    }
  }, [activeRun?.id, activeRun?.status, businessLoading, tab])
  return (
    <main className={`business-workspace workspace-${tab}`} ref={workspaceRef}>
      <div className="business-tabs-bar">
        <div className="business-tabs" role="tablist" aria-label="业务工作区">
          {businessList.map((business) => <button role="tab" aria-selected={business.id === activeBusiness?.id} key={business.id} className={business.id === activeBusiness?.id ? 'active' : ''} title={`${business.title || businessTypeMeta(business.type).title} · ${labelFor(businessStatusLabel, business.status)}`} onClick={() => onBusinessSelect(business.id)}><span className="business-tab-title">{business.title || businessTypeMeta(business.type).title}</span><small>{labelFor(businessStatusLabel, business.status)}</small></button>)}
        </div>
        <RadixButton className="conversation-toggle" variant="ghost" aria-label={conversationOpen ? '收起会话' : '打开会话'} title={conversationOpen ? '收起会话' : '打开会话'} onClick={onToggleConversation}><MessageSquare size={16} /></RadixButton>
      </div>
      {!activeBusiness && <EmptyState title="等待业务工作区" detail="在会话中确认一个业务意图后，这里会打开对应工作区。" />}
      {activeBusiness && <>
        <header className="business-header"><div><h2>{activeBusiness.title || businessTypeMeta(activeBusiness.type).title}</h2><div className="business-target-line"><span>完成目标</span><strong title={acceptedProposal?.goal || activeBusiness.goal}>{completionTargetLabel(businessInfo?.completion_target, activeBusiness.type)}</strong></div><details className="goal-details" key={activeBusiness.id}><summary>业务目标</summary><p>{acceptedProposal?.goal || activeBusiness.goal || '未保存业务目标'}</p></details></div><StatusBadge status={activeBusiness.status} label={labelFor(businessStatusLabel, activeBusiness.status)} /></header>
        {approvalProgress && approvalProgress.businessId === activeBusiness.id && <div className={`approval-progress approval-progress-${approvalProgress.status}`} role="status"><LoaderCircle className={approvalProgress.status === 'submitting' ? 'spin' : ''} size={15} /><span>{approvalProgress.status === 'submitting' ? '正在提交审批决定…' : (approvalProgress.detail || '审批状态未生效，请查看执行详情。')}</span></div>}
        <RadixTabs.Root className="business-tabs-root" value={tab} onValueChange={(value) => onTabChange(value as BusinessTab)}>
          <RadixTabs.List className="business-page-tabs" aria-label="业务页面">
            {tabs.map((item) => <RadixTabs.Trigger key={item.id} value={item.id}>{item.label}{item.id === 'approvals' && detail?.approvals?.filter(isPendingApproval).length ? <b>{detail.approvals.filter(isPendingApproval).length}</b> : null}</RadixTabs.Trigger>)}
          </RadixTabs.List>
          <div className="business-content" onScroll={(event) => {
            if (event.target !== event.currentTarget) return
            const content = event.currentTarget
            followingResult.current = content.scrollTop <= 32 || content.scrollHeight - content.scrollTop - content.clientHeight <= 48
          }}>
          {businessLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />正在读取业务状态…</div>}
          <RadixTabs.Content value="execution">{(!businessLoading || detail?.business.id === activeBusiness.id) && <ExecutionPage detail={detail} activeRun={activeRun} liveMessages={businessMessages.filter((message) => message.run_id === activeRun?.id)} pendingApprovals={pendingApprovals} busy={loading || businessLoading} starting={starting} focusedApprovalId={focusedApprovalId} focusedApprovalRequest={focusedApprovalRequest} onApproval={onApproval} onRequestRevision={onRequestRevision} onReconcile={onReconcile} onReconcileBusiness={onReconcileBusiness} onOpenApprovals={onOpenApprovals} onRefresh={onRefresh} onStart={onStart} onCancel={onCancel} onEvidence={onTraceTarget} onExport={() => onExport(activeRun?.id)} exporting={exporting} exportPath={exportPath} />}</RadixTabs.Content>
           <RadixTabs.Content value="documents">{(!businessLoading || detail?.business.id === activeBusiness.id) && <DocumentsPage documents={detail?.documents ?? []} materials={(detail as DetailWithMaterials | null)?.materials ?? []} artifacts={detail?.artifacts ?? []} goal={activeBusiness.goal} stale={detail?.stale ?? false} onExport={() => onExport()} exporting={exporting} exportPath={exportPath} onOpenDocument={onOpenDocument} onDownloadDocument={onDownloadDocument} documentDownloads={documentDownloads} selectedDocumentKey={selectedDocumentKey} onSelectedDocumentKey={onSelectedDocumentKey} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="approvals">{(!businessLoading || detail?.business.id === activeBusiness.id) && <ApprovalsPage onOpenExecution={onOpenApprovals} approvals={detail?.approvals ?? []} documents={detail?.documents ?? []} disabled={loading || businessLoading} onRefresh={onRefresh} focusActionId={focusedApprovalId} focusRequest={focusedApprovalRequest} onDecision={onApproval} onRequestRevision={onRequestRevision} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="trace">{(!businessLoading || detail?.business.id === activeBusiness.id) && <TracePage trace={trace} liveMessages={businessMessages} runs={detail?.runs ?? []} readback={detail?.business.readback} selectedRunId={selectedRunId} loading={traceLoading} target={traceTarget} onRunSelect={onRunSelect} onLoadDetail={onLoadTraceDetail} onSnapshot={onSnapshot} snapshotOpening={snapshotOpening} />}</RadixTabs.Content>
          </div>
        </RadixTabs.Root>
      </>}
    </main>
  )
}

export function ExecutionPage({ detail, activeRun, liveMessages = [], busy = false, starting = false, onReconcileBusiness, pendingApprovals, focusedApprovalId, focusedApprovalRequest, onApproval, onRequestRevision, onReconcile, onOpenApprovals, onRefresh, onStart, onCancel, onEvidence, onExport, exporting, exportPath }: { focusedApprovalId?: string; focusedApprovalRequest?: number; onApproval?: (approval: Approval, decision: 'approve' | 'reject') => void; onRequestRevision?: (approval: Approval, text: string) => Promise<void>; onReconcile?: (approval: Approval) => void; detail: BusinessDetailProjection | null; activeRun?: Run; liveMessages?: LiveMessage[]; busy?: boolean; starting?: boolean; onReconcileBusiness?: () => void; pendingApprovals: Approval[]; onOpenApprovals: (actionId?: string) => void; onRefresh: () => void; onStart: () => void; onCancel: (run: Run) => void; onEvidence: (evidence: BusinessEvidence) => void; onExport: () => void; exporting: boolean; exportPath: string }) {
  const hasUnknownWrite = Boolean(detail?.approvals?.some((approval) => approval.status === 'needs_reconciliation') || detail?.runs?.some((run) => run.status === 'needs_reconciliation') || detail?.business.status === 'blocked')
  const waitingForInput = activeRun?.status === 'awaiting_input'
  const canStart = !busy && !starting && !hasUnknownWrite && !waitingForInput && (!activeRun || !['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))
  const runActionLabel = waitingForInput ? '等待补充条件' : activeRun?.status === 'completed' ? '继续执行' : activeRun && ['failed', 'interrupted', 'cancelled'].includes(activeRun.status) ? '继续剩余步骤' : '开始执行'
  const ended = Boolean(activeRun && ['completed', 'failed', 'cancelled', 'interrupted', 'needs_reconciliation'].includes(activeRun.status))
  const oldReadback = Boolean(detail?.stale || (detail?.business.readback?.latest_run_id && detail.business.readback.latest_run_id !== activeRun?.id))
  const unverifiedFailure = ended && activeRun?.status !== 'completed' && activeRun?.verification_status !== 'passed'
  const outcome = (oldReadback || unverifiedFailure) && detail?.outcome?.status === 'passed'
    ? { status: 'unknown', label: '本轮结果待核对', detail: '尚未取得本轮的完整核验结果，请查看核验详情。', scope: '' }
    : detail?.outcome
  const result = <section className="outcome-summary" aria-label="执行结果" aria-live="polite">
    <div className="execution-result-heading"><span className={`execution-result-symbol result-${outcome?.status || 'unknown'}`} aria-hidden="true">{outcome?.status === 'passed' ? <CheckIcon size={25} /> : outcome?.status === 'failed' ? <CircleAlert size={25} /> : <Clock3 size={25} />}</span><div><span className="activity-caption">{outcome?.status === 'passed' || outcome?.status === 'failed' ? outcome.label : '本轮核验'}</span><h3>{outcome?.status === 'passed' ? '执行结果已核验' : outcome?.status === 'failed' ? '执行结果需要检查' : '本轮结果待核对'}</h3></div><StatusBadge status={outcome?.status} label={outcomeStatusLabel(outcome?.status)} /></div>
    <p>{outcome?.detail || '完成执行后显示核验结果。'}</p>
    {outcome?.scope && <small>核验范围：{outcomeScopeLabel(outcome.scope)}</small>}
    {ended && <small>{runDisplayLabel(activeRun?.status)}{detail?.observed_at ? ` · 数据更新于 ${formatInstant(detail.observed_at)}` : ''}</small>}

  </section>
  const actionable = (detail?.approvals ?? pendingApprovals).filter(approval => isPendingApproval(approval) || approval.status === 'needs_reconciliation').sort(compareApprovals)
  const recentDecisions = (detail?.approvals ?? []).filter(approval => approval.run_id === activeRun?.id && ['approved', 'executing', 'executed', 'verified', 'rejected', 'known_failed', 'expired', 'stale', 'not_executed'].includes(approval.status)).slice(-4)
  const lastFocusRequest = useRef('')
  const focusAvailable = actionable.some(approval => approval.action_id === focusedApprovalId)
  useEffect(() => {
    const request = `${detail?.business.id}:${focusedApprovalRequest}:${focusedApprovalId}`
    if (!focusedApprovalId || lastFocusRequest.current === request) return
    const row = document.getElementById(`approval-${focusedApprovalId}`)
    row?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    row?.focus({ preventScroll: true })
    if (row) lastFocusRequest.current = request
  }, [focusedApprovalId, focusedApprovalRequest, detail?.business.id, focusAvailable])
  return (
    <div className="page-stack execution-v2 execution-v3">
      <div className="execution-controls action-row">
        <span className="execution-run-state">{activeRun ? runDisplayLabel(activeRun.status) : '尚未开始'}</span>
        <RadixButton className="secondary-button" variant="soft" disabled={busy || Boolean(activeRun && ['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))} title="读取 Odoo 最新状态不会重复写入" onClick={onRefresh}><RefreshCw size={15} />读取最新状态</RadixButton>
        {activeRun && ['running', 'awaiting_approval'].includes(activeRun.status)
          ? <RadixButton className="danger-button" variant="soft" onClick={() => onCancel(activeRun)}><Square size={14} />取消运行</RadixButton>
          : <RadixButton className="primary-button" disabled={!canStart} title={hasUnknownWrite ? '存在待核对写入，请先核对' : undefined} onClick={onStart}>{starting ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{starting ? '正在启动…' : hasUnknownWrite ? '执行已暂停' : runActionLabel}</RadixButton>}
      </div>
      {hasUnknownWrite ? <section className="execution-attention" role="status"><CircleAlert size={20} /><div><strong>业务已暂停，先核对已执行的动作</strong><p>写入结果不确定，系统不会自动重试。请先核对现有 Odoo 状态。</p></div><RadixButton disabled={busy} onClick={() => (onReconcileBusiness ?? onOpenApprovals)()}><RefreshCw size={15} />核对当前状态</RadixButton></section>
        : waitingForInput ? <section className="execution-attention" role="status"><Clock3 size={20} /><div><strong>等待补充条件</strong><p>{activeRun?.handoff?.message || '请在会话中补充条件，再确认业务方案。'}</p></div></section>
        : null}
      {!(ended && activeRun?.status === 'completed') && <ActivityCard replyOnly={pendingApprovals.length > 0} compact={hasUnknownWrite || waitingForInput || pendingApprovals.length > 0 || ended} key={`activity:${detail?.business.id}:${activeRun?.id}`} activity={detail?.activity ?? { phase: 'idle', label: activeRun ? runDisplayLabel(activeRun.status) : '准备执行', detail: '' }} run={activeRun} liveMessages={liveMessages} />}
      {actionable.length > 0 && <section className="execution-approvals" aria-label="待处理动作">{actionable.map(approval => <ApprovalRow key={approval.action_id} approval={approval} documents={detail?.documents ?? []} disabled={busy} onDecision={onApproval ?? (() => onOpenApprovals(approval.action_id))} onRequestRevision={onRequestRevision} onReconcile={onReconcile ?? (() => onOpenApprovals(approval.action_id))} onTraceTarget={target => onEvidence({ ...target, kind: target.kind as BusinessEvidence['kind'], run_id: target.run_id || activeRun?.id || '', label: approval.title })} onRefresh={onRefresh} />)}</section>}

      {!hasUnknownWrite && activeRun && ['interrupted', 'failed', 'cancelled'].includes(activeRun.status) && <p className="execution-resume-note" role="status">继续剩余步骤；新的写入仍需审批。</p>}
      {ended && result}
      <ExecutionStages key={`stages:${detail?.business.id}:${activeRun?.id}`} execution={detail?.execution} runStatus={activeRun?.status} onEvidence={onEvidence} />
      {recentDecisions.length > 0 && <div className="decision-receipts" aria-label="最近审批结果">{recentDecisions.map(approval => <button type="button" key={approval.action_id} onClick={() => onEvidence({ run_id: approval.run_id, action_id: approval.action_id, label: approval.title })}><span>{approvalActionTitle(approval)}</span><StatusBadge status={approval.status} label={approval.status === 'approved' ? '决定已提交 · 待执行' : approval.status === 'executed' ? '动作已执行' : approvalStatusLabel(approval.status)} /></button>)}</div>}
      <BusinessFacts documents={detail?.documents ?? []} />
      {ended && <ExecutionReceipts receipts={detail?.receipts ?? []} onEvidence={onEvidence} onExport={onExport} exporting={exporting} exportPath={exportPath} />}
      {ended && <section className="run-conclusion" aria-label="Agent 完整回复"><div className="section-heading"><h3>Agent 回复</h3>{activeRun?.ended_at && <time>{formatInstant(activeRun.ended_at)}</time>}</div>{activeRun?.summary?.trim() ? <MessageText text={activeRun.summary} collapsible={false} /> : <p className="muted">本轮未返回总结，可查看核验结果和运行详情。</p>}</section>}
      {!ended && <details className="verification-fold"><summary>最近核验结果</summary>{result}</details>}
      <details className="verification-fold"><summary>独立回读核验 · {detail?.checks?.length ?? 0} 项{detail?.stale ? ' · 可能过期' : ''}</summary><VerificationPage checks={detail?.checks ?? []} observedAt={detail?.observed_at} stale={detail?.stale ?? false} /></details>
      <details className="lower-facts"><summary>运行记录 · {detail?.runs?.length ?? 0} 次</summary><section className="run-summary">{(detail?.runs ?? []).slice(0, 4).map(run => <RunRow key={run.id} run={run} />)}</section></details>
    </div>
  )
}

export function ExecutionStages({ execution, runStatus, onEvidence }: { execution?: BusinessDetailProjection['execution']; runStatus?: string; onEvidence: (evidence: BusinessEvidence) => void }) {
  const stages = execution?.stages ?? []
  const complete = ['completed', 'failed', 'cancelled', 'interrupted', 'needs_reconciliation'].includes(runStatus || '')
  if (!stages.length) return null
  return <section className="stage-panel" aria-label="业务进度">
    <div className="section-heading"><h3>执行过程</h3><span>{stages.length} 个已记录阶段</span></div>
    <div className="execution-progress" aria-label="已记录阶段状态">{stages.map(stage => <span key={stage.id} className={`progress-segment progress-${stage.status}`} title={`${stage.label || stageLabel(stage.id)}：${stageStatusLabel(stage.status)}`} />)}</div>
    <ol className="execution-timeline">{stages.map(stage => <li key={stage.id} className={`execution-step step-${stage.status} ${!complete && stage.id === execution?.current_stage_id ? 'current' : ''}`} aria-current={!complete && stage.id === execution?.current_stage_id ? 'step' : undefined}>
      <span className="execution-step-marker" aria-hidden="true">{stage.status === 'verified' ? <CheckIcon size={13} /> : stage.status === 'active' && !complete ? <LoaderCircle className="spin" size={13} /> : <span />}</span>
      <div className="execution-step-body"><div className="stage-row-head"><strong>{stage.label || stageLabel(stage.id)}</strong><span>{stageStatusLabel(stage.status)}</span></div>{stage.detail && <p>{stage.detail}</p>}{Boolean(stage.evidence?.length) && <div className="execution-evidence-list">{groupEvidence(stage.evidence).map(group => <button className="execution-evidence-row" type="button" key={group.key} onClick={() => onEvidence(group.target)}><FileText size={14} /><span><strong>{group.title}</strong>{group.objects.length > 0 && <small>{group.objects.slice(0, 3).join('、')}{group.objects.length > 3 ? ` 等 ${group.objects.length} 条记录` : ''}</small>}</span><span className="execution-evidence-kind">{group.target.kind === 'readback' ? '核验快照' : group.target.kind === 'action' ? '动作账本' : '工具回执'}</span></button>)}</div>}</div>
    </li>)}</ol>
  </section>
}

export function groupEvidence(evidence: BusinessEvidence[]) {
  const groups = new Map<string, { key: string; target: BusinessEvidence; title: string; objects: string[] }>()
  evidence.forEach((item, index) => {
    const kind = item.kind === 'action' && !item.action_id ? 'tool' : item.kind ?? (item.tool_id ? 'tool' : item.action_id ? 'action' : 'tool')
    const id = kind === 'readback' ? 'snapshot' : kind === 'action' && item.action_id ? item.action_id : item.tool_id ?? item.action_id ?? String(index)
    const key = `${item.run_id}:${kind}:${id}`
    const model = item.model ? documentModelLabel(item.model) : ''
    const title = kind === 'readback' ? '查看独立回读快照' : item.operation ? `${operationLabel(item.operation)}${model}` : model ? `读取${model}` : item.label || '查看执行证据'
    const objects = item.record_names?.length ? item.record_names : (item.record_ids ?? []).map(id => `${model ? `${model} · ` : ''}记录 ${id}`)
    const existing = groups.get(key)
    if (existing) { existing.objects = [...new Set([...existing.objects, ...objects])]; if (item.operation) existing.title = title }
    else groups.set(key, { key, target: { ...item, kind }, title, objects: [...new Set(objects)] })
  })
  return [...groups.values()]
}

export function BusinessFacts({ documents }: { documents: Document[] }) {
  const currentDocuments = documents.filter(document => !document.is_reference && document.document_scope !== 'reference' && ['sale.order', 'purchase.order', 'account.move', 'stock.picking', 'mrp.production', 'account.payment', 'account.bank.statement.line', 'account.move.line'].includes(document.model))
  const fact = (document: Document, keys: string[]) => readableValue(keys.map(key => document.fields[key]).find(value => value !== undefined && value !== null && value !== ''))
  return <section className="business-facts" aria-label="相关单据">
    <div className="section-heading"><h3>相关单据</h3><span>{currentDocuments.length} 张</span></div>
    {!currentDocuments.length && <p className="facts-empty">尚未观察到本业务的目标单据</p>}
    <ul className="execution-documents">{currentDocuments.map(document => <li key={`${document.model}:${document.id}`}>
      <div className="execution-document-name"><strong>{document.name || document.id}</strong><span>{documentModelLabel(document.model)}</span>{['mrp.production', 'account.bank.statement.line', 'account.move.line'].includes(document.model) && <small>{documentFact(document)}</small>}</div>
      <div className="execution-document-facts">{['sale.order', 'purchase.order', 'account.move', 'account.payment'].includes(document.model) && <><span>{fact(document, ['partner_name', 'customer', 'vendor', 'partner_id'])}</span><strong>{amountWithCurrency(fact(document, ['amount_total', 'total', 'amount']), fact(document, ['currency', 'currency_name', 'currency_id']))}</strong></>}</div>
      <div className="execution-document-state"><span>{documentStateLabel(document.model, document.state)}</span>{document.model === 'sale.order' && <small>开票：{invoiceStatusLabel(fact(document, ['invoice_status']))}</small>}{document.model === 'account.move' && <small>{paymentStatusLabel(fact(document, ['payment_state']))} · 余额 {fact(document, ['amount_residual', 'residual'])}</small>}</div>
    </li>)}</ul>
  </section>
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

export function ActivityCard({ activity, run, liveMessages = [], compact = false, replyOnly = false }: { replyOnly?: boolean; compact?: boolean; activity: NonNullable<BusinessDetail['activity']>; run?: Run; liveMessages?: LiveMessage[] }) {
  const phase = activity.phase || 'unknown'
  const running = run?.status === 'running' || run?.status === 'cancel_requested'
  const toolRunning = running && phase === 'tool' && (!activity.tool_status || activity.tool_status === 'running')
  const moving = running && (['model', 'model_wait', 'cancelling'].includes(phase) || toolRunning)
  const latestReply = liveMessages.filter(message => message.text?.trim()).at(-1)
  const streaming = running && latestReply?.status === 'streaming'
  const publicText = latestReply?.text || activity.intent?.trim()
  const publicTextRef = useRef<HTMLDivElement>(null)
  const followReplyRef = useRef(true)
  const [hasNewReply, setHasNewReply] = useState(false)
  useEffect(() => {
    const viewport = publicTextRef.current?.closest('.business-content')
    if (!viewport) return
    const onScroll = () => {
      followReplyRef.current = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop < 32
      if (followReplyRef.current) setHasNewReply(false)
    }
    viewport.addEventListener('scroll', onScroll, { passive: true })
    return () => viewport.removeEventListener('scroll', onScroll)
  }, [Boolean(publicText)])
  useEffect(() => {
    if (publicText && !followReplyRef.current) setHasNewReply(true)
  }, [publicText])
  const returnToReply = () => {
    followReplyRef.current = true
    setHasNewReply(false)
    publicTextRef.current?.scrollIntoView({ block: 'end', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }
  return <section className={`activity-card activity-phase-${phase} ${compact ? 'activity-compact' : ''} ${replyOnly ? 'activity-reply-only' : ''}`} aria-label={compact ? '最近进展' : '当前动作'} data-active={moving ? 'true' : 'false'}>
    <div className="activity-icon">{moving ? <LoaderCircle className="spin" size={20} /> : phase === 'approval' ? <Clock3 size={20} /> : <Activity size={20} />}</div>
    <div className="activity-copy"><span className="activity-caption">{compact ? '最近进展' : '当前动作'}</span><strong role="status">{streaming ? '正在回复' : activity.label || '读取状态中'}</strong>{activity.detail && <p>{activity.detail}</p>}
      {publicText && <div className="activity-intent"><div className="public-reply-heading"><span>{streaming ? '公开回复 · 更新中' : '最近公开回复'}</span>{hasNewReply && <button type="button" className="evidence-link" onClick={returnToReply}>有新进展 ↓</button>}</div><div ref={publicTextRef} className="activity-public-text" tabIndex={0} aria-label="公开回复" onWheel={event => { if (event.deltaY < 0) followReplyRef.current = false }} onKeyDown={event => { if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) followReplyRef.current = false }} onScroll={event => { const element = event.currentTarget; followReplyRef.current = element.scrollHeight - element.clientHeight - element.scrollTop < 32; if (followReplyRef.current) setHasNewReply(false) }}><MessageText text={publicText} collapsible={false} />{streaming && <span className="execution-stream-caret" aria-hidden="true" />}</div></div>}
      <div className="activity-meta">{activity.tool_name && <span title={activity.tool_name}>{toolRunning ? '正在执行：' : '最近工具：'}{toolLabel(activity.tool_name) === '业务工具' ? activity.tool_name : toolLabel(activity.tool_name)}</span>}{activity.round != null && <span>第 {activity.round} 轮</span>}{activity.at && <span>{formatInstant(activity.at)}</span>}</div>
    </div>
  </section>
}
