import { AlertDialog as RadixAlertDialog,Button as RadixButton,IconButton as RadixIconButton,Tooltip as RadixTooltip,Select as RadixSelect } from '@radix-ui/themes'
import { Archive,FilePlus2,FileText,FolderPlus,LoaderCircle,PanelLeftClose,PanelLeftOpen,Search,Send,Settings2,Square,Trash2 } from 'lucide-react'
import { useEffect,useRef,useState,type FormEvent,type ReactNode } from 'react'
import { EmptyState,MessageText } from '../../components/common'
import { businessTypeMeta,completionTargetLabel,materialRowLabel } from '../../presentation'
import {
Approval,
Business,
BusinessDetail,
ConversationRun,
LiveMessage,
Message,
SessionDetail,
SessionSummary,
businessStatusLabel,
formatInstant,
labelFor
} from '../../protocol'
import { ApprovalProgress,MaterialRecord,MessageWithMaterials,ProposalLike } from '../../view-types'
import { ApprovalInboxCard } from '../approvals/ApprovalsPage'

export function SessionRail({
  sessions,
  selectedId,
  loading,
  renamingId,
  onSelect,
  onCreate,
  onArchive,
  onRenameStart,
  onRename,
  query,
  onQueryChange,
  collapsed,
  onToggle,
  footer
}: {
  sessions: SessionSummary[]
  selectedId: string
  loading: boolean
  renamingId: string
  onSelect: (id: string) => void
  onCreate: () => void
  onArchive: (id: string) => void
  onRenameStart: (id: string) => void
  onRename: (id: string, title: string) => void
  query: string
  onQueryChange: (value: string) => void
  collapsed: boolean
  onToggle: () => void
  footer?: ReactNode
}) {
  const visibleSessions = sessions.filter((item) => `${item.title} ${item.id}`.toLowerCase().includes(query.trim().toLowerCase()))
  return (
    <aside className={`session-rail ${collapsed ? 'collapsed' : ''}`}>
      <div className="rail-heading">{!collapsed && <div><h1>会话</h1></div>}<div className="rail-heading-actions"><RadixTooltip content={collapsed ? '展开会话栏' : '收起会话栏'}><RadixIconButton variant="ghost" aria-label={collapsed ? '展开会话栏' : '收起会话栏'} onClick={onToggle}>{collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}</RadixIconButton></RadixTooltip><RadixTooltip content="新建会话">{collapsed ? <RadixIconButton variant="ghost" aria-label="新聊天" onClick={onCreate} disabled={loading}><FolderPlus size={15} /></RadixIconButton> : <RadixButton className="new-button" onClick={onCreate} disabled={loading}><FolderPlus size={15} />新聊天</RadixButton>}</RadixTooltip></div></div>
      {!collapsed && <label className="session-search"><Search size={15} aria-hidden="true" /><span className="sr-only">搜索会话</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>}
      {!collapsed && <div className="rail-summary"><span>{query ? `${visibleSessions.length} / ${sessions.length} 个会话` : `${sessions.length} 个活跃会话`}</span><span className="quiet-rule" /></div>}
      <div className="session-list">
        {sessions.length === 0 && !collapsed && <EmptyState title="还没有会话" detail="点击新聊天，输入要查询或办理的业务。" />}
        {sessions.length > 0 && visibleSessions.length === 0 && !collapsed && <EmptyState title="没有匹配会话" detail="换一个名称或会话 ID 试试。" />}
        {!collapsed && visibleSessions.map((item) => (
          <div key={item.id} className={`session-row ${item.id === selectedId ? 'active' : ''}`} title={collapsed ? item.title : undefined}>
            <button className="session-select" aria-current={item.id === selectedId ? true : undefined} aria-label={item.title || '未命名会话'} onClick={() => onSelect(item.id)}>
              <span className={`session-status-dot status-${item.status}`} />
              {!collapsed && <span className="session-copy"><strong title={item.title || '未命名会话'}>{item.title || '未命名会话'}</strong><small>{formatInstant(item.updated_at)}</small></span>}
            </button>
            {item.id === selectedId && !collapsed && (
              <div className="session-row-actions">
                {renamingId === item.id ? (
                  <input autoFocus defaultValue={item.title} aria-label="会话名称" onKeyDown={(event) => { if (event.key === 'Enter') onRename(item.id, event.currentTarget.value); if (event.key === 'Escape') onRenameStart('') }} />
                ) : <RadixTooltip content="重命名"><RadixIconButton variant="ghost" size="1" aria-label="重命名" onClick={() => onRenameStart(item.id)}><Settings2 size={14} /></RadixIconButton></RadixTooltip>}
                <RadixTooltip content="归档"><RadixIconButton variant="ghost" size="1" aria-label="归档" onClick={() => onArchive(item.id)}><Archive size={14} /></RadixIconButton></RadixTooltip>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="rail-footer">{footer || (!collapsed && <span>本地工作区</span>)}</div>
    </aside>
  )
}

export function ConversationPane({ session, draft, liveMessages, conversationRuns, thinkingRun, selectedBusinessId, loading, inspectionMode = false, pendingProposal, proposalBusy, proposalUnavailable, pendingApprovals, approvalBusinessName, approvalProgress, onOpenApprovals, onOpenExecution, approvalActivity, businesses, messageBusinessId, onMessageBusinessChange, onDraftChange, onSubmit, onProposal, onOpenBusiness, onCancelConversation, pendingMaterials, reusedMaterials, materialsBusy, onFiles, onRemoveMaterial, onStarter }: {
  session: SessionDetail | null
  draft: string
  liveMessages: LiveMessage[]
  conversationRuns: ConversationRun[]
  thinkingRun: { sessionId: string; runId: string } | null
  selectedBusinessId: string
  loading: boolean
  inspectionMode?: boolean
  pendingProposal?: ProposalLike
  proposalBusy: boolean
  proposalUnavailable: string
  pendingApprovals: Approval[]
  approvalBusinessName: string
  approvalProgress: ApprovalProgress | null
  onOpenApprovals: (actionId?: string) => void
  onOpenExecution: () => void
  approvalActivity?: NonNullable<BusinessDetail['activity']>
  businesses: Business[]
  messageBusinessId: string
  onMessageBusinessChange: (id: string) => void
  onDraftChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
  onProposal: (proposal: ProposalLike, confirmed: boolean) => void
  onOpenBusiness: (id: string) => void
  onCancelConversation: (run: ConversationRun) => void
  pendingMaterials: MaterialRecord[]
  reusedMaterials: MaterialRecord[]
  materialsBusy: boolean
  onFiles: (files: File[]) => void
  onRemoveMaterial: (id: string) => void
  onStarter: (goal: string) => void
}) {
  const messages = session?.messages ?? []
  const selectedBusiness = businesses.find((business) => business.id === selectedBusinessId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atLatest, setAtLatest] = useState(true)
  const [hasNew, setHasNew] = useState(false)
  const latestConversation = [...conversationRuns].filter((run) => run.business_id == null).sort((left, right) => String(right.started_at || '').localeCompare(String(left.started_at || '')))[0]
  const activeConversation = latestConversation && ['running', 'cancel_requested'].includes(latestConversation.status) ? latestConversation : undefined
  const terminalConversation = latestConversation && ['failed', 'cancelled', 'interrupted'].includes(latestConversation.status) ? latestConversation : undefined
  const visibleLiveMessages = liveMessages.filter((message) => message.session_id === session?.session.id && (message.business_id == null || message.business_id === selectedBusinessId))
  const persistedMessageKeys = new Set(messages.map((message) => `${message.business_id ?? '__conversation__'}:${message.id}`))
  const orderedMessages = [
    ...messages.filter((message) => message.proposal ? message.proposal.status === 'confirmed' && businesses.some((business) => business.id === message.business_id) : message.text.trim()).map((message) => message.proposal
      ? { kind: 'receipt' as const, message, created_at: message.created_at }
      : { kind: 'message' as const, message, created_at: message.created_at }),
    ...visibleLiveMessages.filter((message) => !persistedMessageKeys.has(`${message.business_id ?? '__conversation__'}:${message.id}`)).map((message) => ({ kind: 'live' as const, message, created_at: message.created_at }))
  ]
  // Legacy history can omit timestamps. Keep its stored order, including proposal receipts.
  if (orderedMessages.every((item) => typeof item.created_at === 'string' && Number.isFinite(Date.parse(item.created_at)))) {
    orderedMessages.sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)))
  }
  const localThinking = Boolean(thinkingRun && thinkingRun.sessionId === session?.session.id && (!thinkingRun.runId || latestConversation?.id === thinkingRun.runId))
  const activePublicText = activeConversation && (
    visibleLiveMessages.some((message) => message.run_id === activeConversation.id && message.text.trim())
    || messages.some((message) => message.role === 'assistant' && message.run_id === activeConversation.id && message.text.trim())
  )
  const localThinkingForRun = Boolean(
    localThinking
    && !(thinkingRun?.runId && latestConversation?.id === thinkingRun.runId && latestConversation.status === 'cancel_requested')
  )
  const showThinking = Boolean(!activePublicText && (localThinkingForRun || activeConversation?.status === 'running'))
  const reusedMaterialIds = new Set(pendingMaterials.map((material) => material.id))
  const inheritedMaterials = reusedMaterials.filter((material) => !reusedMaterialIds.has(material.id))
  const inspectionLabel = (message: { run_id?: string; inspection?: boolean; context_business_id?: string | null }) => {
    const run = conversationRuns.find((row) => row.id === message.run_id)
    if (!message.inspection && !run?.inspection) return undefined
    const businessId = message.context_business_id || run?.context_business_id
    return `运行查询 · ${businesses.find((business) => business.id === businessId)?.title || '历史业务'}`
  }
  const materialSeen = new Set<string>()
  const scrollToLatest = () => {
    const element = scrollRef.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    setAtLatest(true)
    setHasNew(false)
  }
  useEffect(() => {
    const element = scrollRef.current
    if (!element || atLatest) {
      if (element) element.scrollTop = element.scrollHeight
      return
    }
    setHasNew(true)
  }, [atLatest, liveMessages, messages, pendingApprovals.length, approvalProgress?.status, showThinking, proposalBusy, pendingProposal])
  return (
    <section className={`conversation-pane ${terminalConversation ? 'has-run-status' : ''}`}>
      <header className="conversation-header">
        <div><h2 title={session?.session.title}>{session?.session.title || '选择一个会话'}</h2></div>
        <div className="conversation-header-meta">{activeConversation && <RadixButton className="conversation-cancel" variant="soft" disabled={loading || activeConversation.status === 'cancel_requested'} onClick={() => onCancelConversation(activeConversation)}><Square size={13} />{activeConversation.status === 'cancel_requested' ? '正在停止…' : '停止对话'}</RadixButton>}</div>
      </header>
      {terminalConversation && <div className={`conversation-run-status status-${terminalConversation.status}`} role="status"><strong>{terminalConversation.status === 'failed' ? '对话失败，可继续输入' : terminalConversation.status === 'cancelled' ? '对话已停止，可继续输入' : '对话已中断，可继续输入'}</strong>{(terminalConversation.error || terminalConversation.error_detail) && <details><summary>查看错误详情</summary><code>{terminalConversation.error || terminalConversation.error_detail}</code>{terminalConversation.error_detail && terminalConversation.error_detail !== terminalConversation.error && <p>{terminalConversation.error_detail}</p>}</details>}</div>}
      <div ref={scrollRef} className="conversation-scroll" onScroll={(event) => { const element = event.currentTarget; const latest = element.scrollHeight - element.scrollTop - element.clientHeight < 24; setAtLatest(latest); if (latest) setHasNew(false) }}>
        {!session && <EmptyState title="选择一个会话" detail="从左侧继续处理，或新建会话。" />}
        {session && orderedMessages.length === 0 && visibleLiveMessages.length === 0 && !pendingProposal && !showThinking && <ConversationWelcome onStarter={onStarter} />}
        {orderedMessages.map((item) => item.kind === 'receipt' ? <article className="proposal-receipt" key={item.message.id}><strong>{item.message.proposal?.title}</strong><span>业务已确认</span><RadixButton variant="soft" onClick={() => onOpenBusiness(item.message.business_id!)}>打开业务</RadixButton></article> : item.kind === 'message' ? (() => { const ids = (item.message as MessageWithMaterials).material_ids ?? []; const inherited = ids.filter((id) => materialSeen.has(id)); ids.forEach((id) => materialSeen.add(id)); return <MessageRow key={`message:${item.message.id}`} message={item.message} inheritedMaterialIds={inherited} contextLabel={inspectionLabel(item.message)} /> })() : <article className="message assistant live-message" key={`live:${item.message.run_id}:${item.message.id}`}><span className="avatar agent-avatar">A</span><div><div className="message-meta"><strong>Agent</strong><span>{item.message.status === 'ended' ? '回复完成' : item.message.status === 'interrupted' || item.message.status === 'failed' ? '已停止 · 回复未完成' : '实时回复'}</span></div><small>{inspectionLabel(item.message)}</small><MessageText text={item.message.text} collapsible={false} /></div></article>)}
        {showThinking && <article className="message assistant thinking-message" aria-live="polite"><span className="avatar agent-avatar">A</span><div><div className="message-meta"><strong>Agent</strong></div><p className="thinking-copy">正在思考…</p></div></article>}
        {(pendingApprovals.length > 0 || approvalProgress?.businessId === selectedBusinessId || Boolean(selectedBusinessId && approvalActivity && approvalActivity.phase !== 'idle')) && <ApprovalInboxCard approvals={pendingApprovals} businessName={approvalBusinessName} progress={approvalProgress} activity={approvalActivity} onOpenApprovals={onOpenApprovals} onOpenExecution={onOpenExecution} />}
        {pendingProposal && proposalBusy && <p className="proposal-preparing" role="status"><LoaderCircle size={15} className="spin" />正在整理业务方案，回复完成后可确认。</p>}
        {pendingProposal && !proposalBusy && <ProposalCard proposal={pendingProposal} disabled={loading || proposalBusy || Boolean(proposalUnavailable)} preparing={proposalBusy} unavailable={proposalUnavailable} onDecision={onProposal} onOpenBusiness={onOpenBusiness} />}
        {hasNew && <button className="new-message-indicator" type="button" onClick={scrollToLatest}>有新消息 · 回到最新</button>}
      </div>
      <form className="composer" onSubmit={onSubmit} onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add('drop-active') }} onDragLeave={(event) => event.currentTarget.classList.remove('drop-active')} onDrop={(event) => { event.preventDefault(); event.currentTarget.classList.remove('drop-active'); if (!inspectionMode) onFiles(Array.from(event.dataTransfer.files)) }}>
        <MaterialTray materials={pendingMaterials} busy={materialsBusy} onRemove={onRemoveMaterial} onFiles={onFiles} />
        {!inspectionMode && inheritedMaterials.length > 0 && <MaterialReuseTray materials={inheritedMaterials} hasNewMaterials={pendingMaterials.length > 0} />}
        <textarea rows={2} value={draft} onChange={(event) => onDraftChange(event.target.value)} disabled={!session || loading} placeholder={session ? (inspectionMode ? '询问当前进度或故障原因…' : '输入要查询或办理的业务…') : '先选择或创建一个会话'} aria-label="会话消息" />
        <div className="composer-footer">
          {businesses.length > 0 && <div className="composer-context"><label htmlFor="message-business-target" className="sr-only">讨论范围</label><RadixSelect.Root value={messageBusinessId || '__current__'} onValueChange={(value) => onMessageBusinessChange(value === '__current__' ? '' : value)} disabled={!session || loading}><RadixSelect.Trigger id="message-business-target" data-scope={messageBusinessId} aria-label="讨论范围" /><RadixSelect.Content position="popper"><RadixSelect.Item value="__current__">{selectedBusiness ? `当前业务：${selectedBusiness.title || '未命名业务'}` : '新业务讨论'}</RadixSelect.Item><RadixSelect.Item value="__conversation__">整个会话 · 新业务讨论</RadixSelect.Item>{businesses.map((business) => <RadixSelect.Item key={business.id} value={business.id}>{business.title || '未命名业务'} · {labelFor(businessStatusLabel, business.status)}</RadixSelect.Item>)}</RadixSelect.Content></RadixSelect.Root></div>}
        <div className="composer-actions">{inspectionMode && <small>只读询问</small>}<label className="material-picker"><FilePlus2 size={15} />添加材料<input type="file" accept=".csv,.txt,.xlsx,.pdf,.png,.jpg,.jpeg,text/csv,text/plain,application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple disabled={!session || loading || materialsBusy || inspectionMode} onChange={(event) => { onFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} /></label><RadixButton type="submit" disabled={!session || loading || materialsBusy || pendingMaterials.some((material) => material.status === 'parsing' || material.status === 'failed') || (!draft.trim() && !pendingMaterials.length)}>{loading ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}{loading ? '处理中…' : inspectionMode ? '询问' : '发送'}</RadixButton></div>
        </div>
      </form>
    </section>
  )
}

export function MessageRow({ message, inheritedMaterialIds = [], contextLabel }: { message: Message; inheritedMaterialIds?: string[]; contextLabel?: string }) {
  const role = message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant'
  const materialIds = (message as MessageWithMaterials).material_ids ?? []
  const isProposalEnvelope = Boolean(message.proposal)
  return <article className={`message ${role}`}><span className={`avatar ${role === 'user' ? 'user-avatar' : role === 'system' ? 'system-avatar' : 'agent-avatar'}`}>{role === 'user' ? '你' : role === 'system' ? '·' : 'A'}</span><div><div className="message-meta">{role !== 'user' && <strong>{role === 'system' ? '系统' : 'Agent'}</strong>}<span>{formatInstant(message.created_at)}</span></div>{contextLabel && <small>{contextLabel}</small>}{!isProposalEnvelope && <MessageText text={message.text} collapsible={false} />}{materialIds.length > 0 && <span className="message-materials"><FileText size={12} />{inheritedMaterialIds.length === materialIds.length ? `沿用 ${materialIds.length} 个业务材料` : inheritedMaterialIds.length > 0 ? `新附 ${materialIds.length - inheritedMaterialIds.length} 个，沿用 ${inheritedMaterialIds.length} 个材料` : `已附 ${materialIds.length} 个业务材料`}</span>}</div></article>
}

export function ConversationWelcome({ onStarter }: { onStarter: (goal: string) => void }) {
  const starters = [
    { type: 'query', title: '查订单', detail: '查客户、金额和处理状态。', goal: '帮我查一张订单的状态。' },
    { type: 'sale_invoice', title: '确认销售单', detail: '核对订单，确认后暂不发货或开票。', goal: '帮我确认一张销售单，先别发货或开票。' },
    { type: 'purchase', title: '采购', detail: '整理需求，核对供应商和产品。', goal: '帮我整理采购需求，先核对供应商和产品。' }
  ]
  return <section className="conversation-welcome" aria-label="开始一个业务讨论"><h3>处理业务</h3><div className="welcome-actions">{starters.map((starter) => <button key={starter.type} type="button" className="welcome-card" onClick={() => onStarter(starter.goal)}><span className="welcome-card-title">{starter.title}</span><span>{starter.detail}</span></button>)}</div></section>
}

export function MaterialTray({ materials, busy, onRemove, onFiles }: { materials: MaterialRecord[]; busy: boolean; onRemove: (id: string) => void; onFiles: (files: File[]) => void }) {
  if (!materials.length && !busy) return null
  return <div className="material-tray" aria-label="本次消息材料"><div className="material-tray-head"><span><FileText size={14} />本次消息材料</span>{busy && <span className="material-uploading"><LoaderCircle className="spin" size={13} />正在上传与解析…</span>}</div>{materials.map((material) => <div className={`material-chip material-${material.status || 'ready'}`} key={material.id}><div><strong>{material.name}</strong><span>{material.status === 'parsing' ? '正在解析…' : material.status === 'failed' ? `解析失败：${material.error || '请移除后重新上传'}` : `${materialRowLabel(material)} · ${material.preview || '暂无预览'}`}{material.warnings?.length ? ` · ${material.warnings.join('；')}` : ''}</span></div><button type="button" aria-label={`移除 ${material.name}`} onClick={() => onRemove(material.id)}><Trash2 size={14} /></button></div>)}{!busy && materials.length < 3 && <label className="material-inline-drop">继续添加<input type="file" accept=".csv,.txt,.xlsx,.pdf,.png,.jpg,.jpeg,text/csv,text/plain,application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple onChange={(event) => { onFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} /></label>}</div>
}

export function MaterialReuseTray({ materials, hasNewMaterials }: { materials: MaterialRecord[]; hasNewMaterials: boolean }) {
  return <details className="material-reuse-tray"><summary><span><FileText size={14} />沿用历史材料</span><small>{materials.length} 个文件 · {hasNewMaterials ? '本轮不附加' : '发送时按业务上下文沿用'}</small></summary><div className="material-reuse-list">{materials.map((material) => <div className="material-reuse-row" key={material.id}><strong>{material.name}</strong><span>{material.status === 'parsing' ? '正在解析…' : material.status === 'failed' ? `解析失败：${material.error || '请移除后重新上传'}` : `${materialRowLabel(material)} · ${material.preview || '暂无预览'}`}{material.warnings?.length ? ` · ${material.warnings.join('；')}` : ''}</span></div>)}</div></details>
}

export function ProposalCard({ proposal, disabled, preparing, unavailable, onDecision, onOpenBusiness }: { onOpenBusiness?: (id: string) => void; proposal: ProposalLike; disabled: boolean; preparing?: boolean; unavailable?: string; onDecision: (proposal: ProposalLike, confirmed: boolean) => void }) {
  const continuesBusiness = Boolean(proposal.existing_business_id)
  return <section className="proposal-card"><div className="proposal-icon"><FolderPlus size={18} /></div><div className="proposal-kicker">{continuesBusiness ? '延续当前业务' : '业务提案'} · {businessTypeMeta(proposal.type).title}</div><h3>{proposal.title}</h3><p>{proposal.goal}</p>{proposal.source_messages?.length ? <details className="proposal-sources"><summary>执行依据：用户原话</summary>{proposal.source_messages.map((message) => <p key={message.id}>{message.text}</p>)}<small>提案是摘要，执行时保留上述原始要求。</small></details> : <small>历史提案：开始执行前请核对目标。</small>}<div className="proposal-references">{proposal.resolved_references?.map((r) => <small key={`${r.model}:${r.id}`}>{r.quote}<br /></small>)}</div><div className="proposal-target">完成目标：{completionTargetLabel(proposal.completion_target, proposal.type)}</div>{(preparing || unavailable) && <p role="status">{unavailable || "正在完善提案，回复结束后可确认。"}</p>}{unavailable && proposal.existing_business_id && onOpenBusiness && <RadixButton variant="soft" onClick={() => onOpenBusiness(proposal.existing_business_id!)}>打开当前业务核对</RadixButton>}<div className="proposal-actions"><RadixButton className="secondary-button" variant="soft" disabled={disabled} onClick={() => onDecision(proposal, false)}>{continuesBusiness ? '暂不更新' : '暂不创建'}</RadixButton><RadixButton className="primary-button" disabled={disabled} onClick={() => onDecision(proposal, true)}><FolderPlus size={15} />{continuesBusiness ? '更新业务目标' : '创建业务工作区'}</RadixButton></div></section>
}

export function BlockedSendDialog({ message, onClose }: { message: string; onClose: () => void }) {
  return <RadixAlertDialog.Root open={Boolean(message)} onOpenChange={(open) => { if (!open) onClose() }}><RadixAlertDialog.Content maxWidth="420px"><RadixAlertDialog.Title>当前运行尚未结束</RadixAlertDialog.Title><RadixAlertDialog.Description>{message}</RadixAlertDialog.Description><div className="connection-dialog-footer"><RadixAlertDialog.Cancel><RadixButton onClick={onClose}>知道了</RadixButton></RadixAlertDialog.Cancel></div></RadixAlertDialog.Content></RadixAlertDialog.Root>
}

export function ArchiveDialog({ open, onOpenChange, onConfirm }: { open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  return <RadixAlertDialog.Root open={open} onOpenChange={onOpenChange}>
    <RadixAlertDialog.Content maxWidth="420px">
      <RadixAlertDialog.Title>归档会话</RadixAlertDialog.Title>
      <RadixAlertDialog.Description>归档后会话从活跃列表中隐藏，业务记录仍会保留。</RadixAlertDialog.Description>
      <div className="connection-dialog-footer"><RadixAlertDialog.Cancel onClick={() => onOpenChange(false)}><RadixButton variant="soft">取消</RadixButton></RadixAlertDialog.Cancel><RadixAlertDialog.Action onClick={onConfirm}><RadixButton color="red"><Archive size={15} />归档会话</RadixButton></RadixAlertDialog.Action></div>
    </RadixAlertDialog.Content>
  </RadixAlertDialog.Root>
}
